"""
FinScope MinerU PDF 提取器

使用 MinerU 提取 PDF 文本内容，降级方案使用 PyMuPDF。
MinerU 对扫描件和复杂排版支持更好。

输出格式:
- full_text: 纯文本（向后兼容）
- structured_pages: 结构化页面列表（新功能，用于 LLM 压缩）
"""

import json
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)


def extract_pdf_text(pdf_path: str) -> Dict[str, Any]:
    """
    提取 PDF 文本内容（优先 MinerU，降级 PyMuPDF）

    Args:
        pdf_path: PDF 文件路径

    Returns:
        {
            "error": False,
            "full_text": "...",           # 纯文本（向后兼容）
            "structured_pages": [...],     # 结构化页面列表
            "total_pages": N,
            "source": "mineru"|"pymupdf"
        }
        或
        {"error": True, "message": "..."}
    """
    if not pdf_path or not isinstance(pdf_path, str):
        return {"error": True, "message": "PDF 路径为空或非法"}

    pdf_path = pdf_path.strip()
    if not Path(pdf_path).is_file():
        return {"error": True, "message": f"PDF 文件不存在: {pdf_path}"}

    # 优先尝试 MinerU
    result = _extract_with_mineru(pdf_path)
    if not result.get("error"):
        result["source"] = "mineru"
        return result

    # 降级到 PyMuPDF
    logger.info("[MinerU] 不可用，降级使用 PyMuPDF")
    result = _extract_with_pymupdf(pdf_path)
    if not result.get("error"):
        result["source"] = "pymupdf"
    return result


def _extract_with_mineru(pdf_path: str) -> Dict[str, Any]:
    """使用 MinerU 提取 PDF"""
    try:
        output_dir = tempfile.mkdtemp(prefix="finscope_mineru_")

        cmd = ["mineru", "-p", pdf_path, "-o", output_dir, "-m", "auto"]
        logger.info("[MinerU] 执行: %s", " ".join(cmd))

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300,
        )

        if result.returncode != 0:
            return {"error": True, "message": f"MinerU 执行失败: {result.stderr[:200]}"}

        # 读取 content_list.json
        content_list_path = Path(output_dir) / "content_list.json"
        if content_list_path.is_file():
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
        return {"error": True, "message": "MinerU 未安装"}
    except Exception as e:
        return {"error": True, "message": f"MinerU 异常: {str(e)[:200]}"}


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

    输出格式:
    [
        {
            "page_idx": 0,
            "items": [
                {"type": "text", "content": "...", "bbox": [x1,y1,x2,y2]},
                {"type": "table", "content": "<table>...</table>", "bbox": [x1,y1,x2,y2]},
                {"type": "header", "content": "...", "level": 2, "bbox": [x1,y1,x2,y2]},
            ]
        },
        ...
    ]
    """
    pages_dict: Dict[int, List[Dict[str, Any]]] = {}

    for item in content_list:
        page_idx = item.get("page_idx", 0)
        item_type = item.get("type", "text")
        bbox = item.get("bbox", [0, 0, 0, 0])

        if page_idx not in pages_dict:
            pages_dict[page_idx] = []

        if item_type == "text":
            text_content = str(item.get("text", "")).strip()
            if text_content:
                pages_dict[page_idx].append({
                    "type": "text",
                    "content": text_content,
                    "bbox": bbox,
                })
        elif item_type == "header":
            text_content = str(item.get("text", "")).strip()
            if text_content:
                pages_dict[page_idx].append({
                    "type": "header",
                    "content": text_content,
                    "level": item.get("text_level", 1),
                    "bbox": bbox,
                })
        elif item_type == "table":
            table_body = str(item.get("table_body", "")).strip()
            if table_body:
                pages_dict[page_idx].append({
                    "type": "table",
                    "content": table_body,
                    "bbox": bbox,
                    "caption": item.get("table_caption", []),
                    "footnote": item.get("table_footnote", []),
                })

    # 按页码排序
    result = []
    for page_idx in sorted(pages_dict.keys()):
        result.append({
            "page_idx": page_idx,
            "items": pages_dict[page_idx],
        })

    return result


def _extract_with_pymupdf(pdf_path: str) -> Dict[str, Any]:
    """使用 PyMuPDF 提取 PDF 文本（降级路径）"""
    try:
        import fitz

        doc = fitz.open(pdf_path)
        full_text = ""
        structured_pages = []

        for page_num, page in enumerate(doc):
            page_text = page.get_text() if page else ""
            if page_text and page_text.strip():
                full_text += f"\n--- 第{page_num+1}页 ---\n{page_text}"
                structured_pages.append({
                    "page_idx": page_num,
                    "items": [{
                        "type": "text",
                        "content": page_text.strip(),
                        "bbox": [0, 0, 0, 0],
                    }],
                })

        total_pages = len(doc)
        doc.close()

        if not full_text.strip():
            return {"error": True, "message": "PDF 内容为空"}

        return {
            "error": False,
            "full_text": full_text,
            "structured_pages": structured_pages,
            "total_pages": total_pages,
        }

    except ImportError:
        return {"error": True, "message": "PyMuPDF 未安装，请执行: pip install pymupdf"}
    except Exception as e:
        return {"error": True, "message": f"PyMuPDF 异常: {str(e)[:200]}"}
