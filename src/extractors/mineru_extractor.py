"""
FinScope PDF 提取器（MinerU 专用）

MinerU API 服务优先（MINERU_API_URL），本地 CLI 降级。
PyMuPDF 降级路径已按产品决策移除——MinerU 对扫描件、复杂排版、
跨页表格的支持是 PyMuPDF 无法替代的，低质量降级解析不如明确报错。

输出格式:
- full_text: 纯文本（向后兼容）
- structured_pages: 结构化页面列表（用于 LLM 压缩）
"""

import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)


def extract_pdf_text(pdf_path: str) -> Dict[str, Any]:
    """
    提取 PDF 文本内容（MinerU API 服务优先，本地 CLI 降级）

    生产部署推荐 mineru-api 服务（GPU 集中管理），本地 CLI 用于开发调试。
    PyMuPDF 降级路径已按产品决策移除——PDF 解析质量优先，无 MinerU 时明确报错。

    Args:
        pdf_path: PDF 文件路径

    Returns:
        {
            "error": False,
            "full_text": "...",           # 纯文本（向后兼容）
            "structured_pages": [...],     # 结构化页面列表
            "total_pages": N,
            "source": "mineru-api"|"mineru"
        }
        或
        {"error": True, "message": "..."}
    """
    if not pdf_path or not isinstance(pdf_path, str):
        return {"error": True, "message": "PDF 路径为空或非法"}

    pdf_path = pdf_path.strip()
    if not Path(pdf_path).is_file():
        return {"error": True, "message": f"PDF 文件不存在: {pdf_path}"}

    from utils.config import get_settings
    settings = get_settings()

    # 1) MinerU API 服务模式（MINERU_API_URL 配置时启用；生产部署推荐）
    if settings.MINERU_API_URL:
        result = _extract_with_mineru_service(
            pdf_path, settings.MINERU_API_URL, settings.MINERU_TIMEOUT_SECONDS,
        )
        if not result.get("error"):
            result["source"] = "mineru-api"
            return result
        logger.warning("[MinerU] API 服务不可用，降级本地 CLI: %s", result.get("message", ""))

    # 2) 本地 MinerU CLI
    result = _extract_with_mineru(pdf_path)
    if not result.get("error"):
        result["source"] = "mineru"
        return result

    # 两条 MinerU 路径均失败: 明确报错（不静默降级到低质量解析）
    if settings.MINERU_API_URL:
        result.setdefault("message", "")
        result["message"] += "（提示: 检查 MINERU_API_URL 服务是否可达，或本地安装 mineru CLI）"
    return result


def _find_content_list(output_dir: str) -> Optional[Path]:
    """
    在 MinerU 输出目录中递归查找 content_list.json

    MinerU CLI 实际输出在 <output_dir>/<pdf名>/<mode>/ 子目录下，
    直接读 output_dir/content_list.json 必然失败（历史 bug，导致 MinerU 从未生效）。
    """
    root = Path(output_dir)
    if not root.is_dir():
        return None
    for candidate in root.rglob("content_list.json"):
        if candidate.is_file():
            return candidate
    return None


def _extract_with_mineru_service(pdf_path: str, api_url: str, timeout_seconds: int) -> Dict[str, Any]:
    """
    MinerU API 服务模式（MINERU_API_URL 配置时启用）

    调用 mineru-api 同步解析端点 POST /file_parse（multipart 上传）。
    200 页全文解析在此端点耗时数分钟属正常——超时应配置 MINERU_TIMEOUT_SECONDS
    而非缩短（拆页解析会破坏跨页表格合并与阅读顺序，违背官方全文解析推荐）。

    官方请求/响应结构（MinerU 2.1.x RESTful API）:
    - multipart 字段名固定为 files（复数），参数 return_content_list=true 必带
    - 响应: {"backend":..., "version":...,
             "results": {"<文档名>": {"content_list": [...], "md_content": "...", ...}}}

    解析优先级: content_list（结构化全路径）> md_content（降级单页）。
    任何失败返回 error dict，由上层降级本地 CLI。
    """
    try:
        import requests
    except ImportError:
        return {"error": True, "message": "requests 未安装，无法调用 MinerU API 服务"}

    try:
        with open(pdf_path, "rb") as f:
            resp = requests.post(
                f"{api_url.rstrip('/')}/file_parse",
                files={"files": (Path(pdf_path).name, f, "application/pdf")},  # 字段名固定复数
                data={
                    "lang_list": "ch",
                    "backend": "pipeline",
                    "return_md": "true",
                    "return_content_list": "true",
                },
                timeout=timeout_seconds,
            )

        if resp.status_code != 200:
            return {"error": True, "message": f"MinerU API 返回 HTTP {resp.status_code}: {resp.text[:150]}"}

        content_list, md_content = _parse_service_response(resp.json())
        if content_list is not None:
            full_text = _content_list_to_text(content_list)
            structured_pages = _content_list_to_structured_pages(content_list)
            if full_text.strip() or structured_pages:
                return {
                    "error": False,
                    "full_text": full_text,
                    "structured_pages": structured_pages,
                    "total_pages": len(set(p.get("page_idx", 0) for p in structured_pages)) if structured_pages else 0,
                }
            return {"error": True, "message": "MinerU API 返回 content_list 为空"}

        if md_content and md_content.strip():
            # markdown 降级: 单"页"承载全文，下游压缩器仍可用
            return {
                "error": False,
                "full_text": md_content,
                "structured_pages": [{
                    "page_idx": 0,
                    "items": [{"type": "text", "content": md_content, "bbox": [0, 0, 0, 0]}],
                }],
                "total_pages": 1,
            }

        return {"error": True, "message": "MinerU API 响应中无 content_list / md_content 字段"}

    except requests.Timeout:
        return {"error": True, "message": f"MinerU API 请求超时（>{timeout_seconds}s）"}
    except Exception as e:
        return {"error": True, "message": f"MinerU API 异常: {str(e)[:200]}"}


def _parse_service_response(data: Any, depth: int = 0):
    """
    从 /file_parse 响应提取 (content_list, md_content)

    官方结构: {"results": {"<文档名>": {"content_list": [...], "md_content": "..."}}}
    兼容处理: content_list / md_content 出现在任意两层嵌套字典中均可找到；
    results 为 {文档名: 结果} 映射时逐个文档查找（当前链路单文件上传，取第一个命中）。
    """
    if depth > 2 or not isinstance(data, dict):
        return None, None

    content_list = data.get("content_list") or data.get("contentList")
    if isinstance(content_list, list):
        return content_list, data.get("md_content")

    md = data.get("md_content")
    if isinstance(md, str) and md.strip():
        return None, md

    for key in ("results", "result", "data"):
        child = data.get(key)
        if not isinstance(child, dict):
            continue
        # results 是 {文档名: 结果} 映射，逐个找；result/data 直接下钻
        candidates = child.values() if key == "results" else [child]
        for value in candidates:
            cl, md2 = _parse_service_response(value, depth + 1)
            if cl is not None or md2:
                return cl, md2
    return None, None


def _extract_with_mineru(pdf_path: str) -> Dict[str, Any]:
    """使用 MinerU 本地 CLI 提取 PDF"""
    output_dir = None
    try:
        from utils.config import get_settings
        timeout_seconds = get_settings().MINERU_TIMEOUT_SECONDS

        output_dir = tempfile.mkdtemp(prefix="finscope_mineru_")

        cmd = ["mineru", "-p", pdf_path, "-o", output_dir, "-m", "auto"]
        logger.info("[MinerU] 执行: %s (timeout=%ss)", " ".join(cmd), timeout_seconds)

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_seconds,
        )

        if result.returncode != 0:
            return {"error": True, "message": f"MinerU 执行失败: {result.stderr[:200]}"}

        # 读取 content_list.json（递归查找，兼容新版本子目录输出）
        content_list_path = _find_content_list(output_dir)
        if content_list_path is None:
            return {"error": True, "message": "MinerU 未产出 content_list.json"}

        content_list = json.loads(content_list_path.read_text(encoding="utf-8"))
        full_text = _content_list_to_text(content_list)
        structured_pages = _content_list_to_structured_pages(content_list)

        if full_text.strip() or structured_pages:
            return {
                "error": False,
                "full_text": full_text,
                "structured_pages": structured_pages,
                "total_pages": len(set(p.get("page_idx", 0) for p in structured_pages)) if structured_pages else 0,
            }

        return {"error": True, "message": "MinerU 输出为空"}

    except subprocess.TimeoutExpired:
        return {"error": True, "message": "MinerU 提取超时"}
    except FileNotFoundError:
        return {"error": True, "message": "MinerU CLI 未安装（pip install mineru），或配置 MINERU_API_URL 使用 API 服务"}
    except Exception as e:
        return {"error": True, "message": f"MinerU 异常: {str(e)[:200]}"}
    finally:
        # [P2-13 修复] 清理临时目录，不再泄漏
        if output_dir:
            shutil.rmtree(output_dir, ignore_errors=True)


def _content_list_to_text(content_list: list) -> str:
    """将 MinerU content_list 转为纯文本（向后兼容）"""
    texts = []
    for item in content_list:
        if item.get("type") == "text":
            t = str(item.get("text", "")).strip()
            if t:
                texts.append(t)
        elif item.get("type") == "table":
            table_body = str(item.get("table_body", "")).strip()
            if table_body:
                # 从 HTML 表格提取纯文本
                import re
                clean_text = re.sub(r"<[^>]+>", " ", table_body)
                clean_text = re.sub(r"\s+", " ", clean_text).strip()
                if clean_text:
                    texts.append(f"[表格] {clean_text[:500]}")
    return "\n".join(texts)


def _content_list_to_structured_pages(content_list: list) -> List[Dict[str, Any]]:
    """
    将 MinerU content_list 转为结构化页面列表

    [2026-09-03 重构] 字段语义依据真实年报产物核实（joinn 2024 年报, 189 页, 2256 items）:
    - type="header" 是**页眉**（跨页重复的 running header，如「釋義」×5、「企業管治報告」×9），
      不是章节标题——旧实现把它当标题属于双重错误（把噪声当标题 + 丢掉真标题）
    - 真正的章节标题 = type="text" 且 text_level>0（该样本 561 个，level 2 为主）
    - type="footer"/"page_number" 是页脚/页码噪声（合计 ~390 个，占 17%）

    归一化输出（下游只认三种 type，兼容各 MinerU 版本的拼写差异）:
        heading: {"type": "heading", "content": ..., "level": N}     # 章节标题
        text:    {"type": "text", "content": ...}
        table:   {"type": "table", "content": <html>, "caption": [...], "footnote": [...]}

    页眉/页脚/页码/图片在入口丢弃，不进下游（版面噪声，污染正文且浪费 token）。
    """
    pages_dict: Dict[int, List[Dict[str, Any]]] = {}

    for item in content_list:
        item_type = item.get("type", "text")

        # 版面噪声: 页眉 / 页脚 / 页码 —— 入口丢弃
        if item_type in ("header", "footer", "page_number"):
            continue

        try:
            page_idx = int(item.get("page_idx", 0) or 0)
        except (TypeError, ValueError):
            page_idx = 0
        bbox = item.get("bbox", [0, 0, 0, 0])

        if page_idx not in pages_dict:
            pages_dict[page_idx] = []

        if item_type == "table":
            table_body = str(item.get("table_body", "")).strip()
            if table_body:
                pages_dict[page_idx].append({
                    "type": "table",
                    "content": table_body,
                    "bbox": bbox,
                    "caption": item.get("table_caption", []) or [],
                    "footnote": item.get("table_footnote", []) or [],
                })
            continue

        # text 与 heading: text_level > 0 即章节标题（兼容 title/header/heading 拼写变体，
        # 各版本 heading 载体可能是独立 type 也可能是 text + text_level）
        text_content = str(item.get("text", "")).strip()
        if not text_content:
            continue

        text_level = item.get("text_level")
        try:
            is_heading = text_level is not None and int(text_level) > 0
        except (TypeError, ValueError):
            is_heading = False

        if is_heading:
            pages_dict[page_idx].append({
                "type": "heading",
                "content": text_content,
                "level": int(text_level),
                "bbox": bbox,
            })
        else:
            pages_dict[page_idx].append({
                "type": "text",
                "content": text_content,
                "bbox": bbox,
            })

    # 按页码排序
    result = []
    for page_idx in sorted(pages_dict.keys()):
        result.append({
            "page_idx": page_idx,
            "items": pages_dict[page_idx],
        })

    return result
