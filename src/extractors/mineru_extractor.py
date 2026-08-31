"""
FinScope MinerU PDF 提取器

使用 MinerU 提取 PDF 文本内容，降级方案使用 PyMuPDF。
MinerU 对扫描件和复杂排版支持更好。
"""

import json
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


def extract_pdf_text(pdf_path: str) -> Dict[str, Any]:
    """
    提取 PDF 文本内容（优先 MinerU，降级 PyMuPDF）

    Args:
        pdf_path: PDF 文件路径

    Returns:
        {"error": False, "full_text": "...", "total_pages": N, "source": "mineru"|"pymupdf"}
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

        # 读取 content_list.json 提取文本
        content_list_path = Path(output_dir) / "content_list.json"
        if content_list_path.is_file():
            content_list = json.loads(content_list_path.read_text(encoding="utf-8"))
            full_text = _content_list_to_text(content_list)
            if full_text.strip():
                return {"error": False, "full_text": full_text, "total_pages": len(content_list)}

        return {"error": True, "message": "MinerU 输出为空"}

    except subprocess.TimeoutExpired:
        return {"error": True, "message": "MinerU 提取超时"}
    except FileNotFoundError:
        return {"error": True, "message": "MinerU 未安装"}
    except Exception as e:
        return {"error": True, "message": f"MinerU 异常: {str(e)[:200]}"}


def _content_list_to_text(content_list: list) -> str:
    """将 MinerU content_list 转为纯文本"""
    texts = []
    for item in content_list:
        if item.get("type") == "text":
            t = str(item.get("text", "")).strip()
            if t:
                texts.append(t)
        elif item.get("type") == "table":
            caption = str(item.get("caption", "")).strip()
            if caption:
                texts.append(f"[表格] {caption}")
    return "\n".join(texts)


def _extract_with_pymupdf(pdf_path: str) -> Dict[str, Any]:
    """使用 PyMuPDF 提取 PDF 文本"""
    try:
        import fitz

        doc = fitz.open(pdf_path)
        full_text = ""
        for page_num, page in enumerate(doc):
            page_text = page.get_text() if page else ""
            if page_text and page_text.strip():
                full_text += f"\n--- 第{page_num+1}页 ---\n{page_text}"
        total_pages = len(doc)
        doc.close()

        if not full_text.strip():
            return {"error": True, "message": "PDF 内容为空"}

        return {"error": False, "full_text": full_text, "total_pages": total_pages}

    except ImportError:
        return {"error": True, "message": "PyMuPDF 未安装，请执行: pip install pymupdf"}
    except Exception as e:
        return {"error": True, "message": f"PyMuPDF 异常: {str(e)[:200]}"}
