"""
FinScope 页面压缩器

对 MinerU 提取的结构化页面进行压缩:
- 主路径: LLM 逐页压缩，提取 key_points + financial_data
- 降级路径: 规则压缩（正则提取关键信息）

设计原则:
1. LLM 压缩为主，规则兜底
2. 每页独立压缩，保留 page_idx 用于来源标注
3. 表格内容完整保留（不压缩），文本内容压缩提取
"""

import re
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


def compress_pages(
    structured_pages: List[Dict[str, Any]],
    use_llm: bool = True,
) -> Dict[str, Any]:
    """
    压缩结构化页面列表

    Args:
        structured_pages: MinerU 输出的结构化页面列表
        use_llm: 是否使用 LLM 压缩（False 则使用规则压缩）

    Returns:
        {
            "error": False,
            "compressed_pages": [
                {
                    "page_idx": 0,
                    "key_points": ["...", "..."],
                    "financial_data": {"revenue": "...", ...},
                    "tables": ["<table>...</table>"],
                    "has_llm_compression": True/False,
                },
                ...
            ],
            "summary": "全文摘要",
            "total_pages": N,
            "llm_compressed_count": N,
            "rule_compressed_count": N,
        }
    """
    if not structured_pages:
        return {"error": True, "message": "无结构化页面数据"}

    compressed_pages = []
    llm_count = 0
    rule_count = 0

    for page in structured_pages:
        page_idx = page.get("page_idx", 0)
        items = page.get("items", [])

        if not items:
            continue

        # LLM 压缩
        if use_llm:
            result = _compress_page_with_llm(page_idx, items)
            if not result.get("error"):
                compressed_pages.append(result)
                llm_count += 1
                continue

        # 规则压缩（降级）
        result = _compress_page_with_rules(page_idx, items)
        compressed_pages.append(result)
        rule_count += 1

    # 生成全文摘要
    summary = _generate_summary(compressed_pages)

    return {
        "error": False,
        "compressed_pages": compressed_pages,
        "summary": summary,
        "total_pages": len(compressed_pages),
        "llm_compressed_count": llm_count,
        "rule_compressed_count": rule_count,
    }


def _compress_page_with_llm(page_idx: int, items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """使用 LLM 压缩单页内容"""
    try:
        from utils.llm_client import safe_invoke, is_llm_ready

        if not is_llm_ready():
            return {"error": True, "message": "LLM 不可用"}

        # 构建页面内容
        page_content = _items_to_text(items)

        if len(page_content.strip()) < 50:
            # 内容太短，不需要压缩
            return {
                "error": False,
                "page_idx": page_idx,
                "key_points": [page_content] if page_content else [],
                "financial_data": {},
                "tables": _extract_tables_from_items(items),
                "has_llm_compression": False,
            }

        system_prompt = """你是一位金融研报分析助手。请对以下 PDF 页面内容进行压缩提取。

## 任务
从页面内容中提取关键信息，用于后续金融分析。

## 输出格式（严格 JSON）
{
    "key_points": ["要点1", "要点2", ...],
    "financial_data": {
        "指标名": "数值",
        ...
    }
}

## 要求
1. key_points: 提取 3-8 个关键要点，每个要点不超过 100 字
2. financial_data: 提取所有可见的财务数据（营收、利润、增长率、比率等）
3. 保留原始数据的准确性，不要编造
4. 如果是表格内容，提取表格中的关键数据
5. 忽略页眉、页脚、页码等无关内容

## 页面内容
{content}
"""

        result = safe_invoke(
            system_prompt.format(content=page_content[:3000]),
            "请压缩提取关键信息。",
        )

        if result.get("error"):
            return {"error": True, "message": result.get("message", "LLM 调用失败")}

        # 解析 JSON
        content = result.get("content", "")
        parsed = _parse_json_from_llm(content)

        if parsed:
            return {
                "error": False,
                "page_idx": page_idx,
                "key_points": parsed.get("key_points", []),
                "financial_data": parsed.get("financial_data", {}),
                "tables": _extract_tables_from_items(items),
                "has_llm_compression": True,
            }

        return {"error": True, "message": "LLM 返回格式解析失败"}

    except Exception as e:
        logger.warning("[PageCompressor] LLM 压缩失败 page %d: %s", page_idx, str(e)[:100])
        return {"error": True, "message": str(e)}


def _compress_page_with_rules(page_idx: int, items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """使用规则压缩单页内容（降级方案）"""
    key_points = []
    financial_data = {}
    tables = []

    for item in items:
        item_type = item.get("type", "text")
        content = item.get("content", "")

        if item_type == "table":
            tables.append(content)
            # 从表格提取财务数据
            table_data = _extract_financial_data_from_table(content)
            financial_data.update(table_data)
        elif item_type in ("text", "header"):
            # 提取关键要点
            points = _extract_key_points_from_text(content)
            key_points.extend(points)

            # 提取财务数据
            text_data = _extract_financial_data_from_text(content)
            financial_data.update(text_data)

    return {
        "error": False,
        "page_idx": page_idx,
        "key_points": key_points[:8],  # 最多 8 个要点
        "financial_data": financial_data,
        "tables": tables,
        "has_llm_compression": False,
    }


def _items_to_text(items: List[Dict[str, Any]]) -> str:
    """将页面 items 转为纯文本"""
    texts = []
    for item in items:
        content = item.get("content", "")
        item_type = item.get("type", "text")

        if item_type == "table":
            # 从 HTML 表格提取文本
            clean = re.sub(r"<[^>]+>", " ", content)
            clean = re.sub(r"\s+", " ", clean).strip()
            if clean:
                texts.append(f"[表格] {clean}")
        elif item_type == "header":
            level = item.get("level", 2)
            texts.append(f"{'#' * level} {content}")
        else:
            texts.append(content)

    return "\n".join(texts)


def _extract_tables_from_items(items: List[Dict[str, Any]]) -> List[str]:
    """从 items 中提取表格 HTML"""
    tables = []
    for item in items:
        if item.get("type") == "table":
            tables.append(item.get("content", ""))
    return tables


def _extract_financial_data_from_table(table_html: str) -> Dict[str, str]:
    """从 HTML 表格提取财务数据"""
    data = {}

    # 提取数字和百分比
    numbers = re.findall(r"[\d,]+\.?\d*%?", table_html)
    # 提取表头
    headers = re.findall(r"<td[^>]*>(.*?)</td>", table_html)
    headers = [re.sub(r"<[^>]+>", "", h).strip() for h in headers if h.strip()]

    # 简单提取：将数字与最近的表头关联
    for i, num in enumerate(numbers[:10]):
        if i < len(headers):
            data[headers[i]] = num

    return data


def _extract_financial_data_from_text(text: str) -> Dict[str, str]:
    """从文本提取财务数据"""
    data = {}

    patterns = {
        "营业收入": r"营业(?:总)?收入[：:：为\s]*([\d,.]+[亿万]?(?:元)?)",
        "归母净利润": r"归(?:属于)?母(?:公司)?(?:股东)?(?:的)?净利润[：:：为\s]*([\d,.]+[亿万]?(?:元)?)",
        "毛利率": r"毛利[率][：:：为\s]*([\d.]+%)",
        "净利率": r"净利[率][：:：为\s]*([\d.]+%)",
        "ROE": r"ROE[：:：为\s]*([\d.]+%)",
        "EPS": r"(?:基本)?每股收益[：:：为\s]*([\d.]+)元?",
        "同比增长": r"(?:同比)?增长[：:：为\s]*([\d.]+%)",
    }

    for name, pattern in patterns.items():
        matches = re.findall(pattern, text)
        if matches:
            data[name] = matches[0]

    return data


def _extract_key_points_from_text(text: str) -> List[str]:
    """从文本提取关键要点"""
    points = []

    # 按句子分割
    sentences = re.split(r"[。！？\n]", text)

    for sentence in sentences:
        sentence = sentence.strip()
        if len(sentence) < 10:
            continue

        # 包含关键信息的句子
        keywords = ["收入", "利润", "增长", "下降", "风险", "机遇", "战略", "投资", "市场", "竞争"]
        if any(kw in sentence for kw in keywords):
            points.append(sentence[:100])

        if len(points) >= 3:
            break

    return points


def _parse_json_from_llm(content: str) -> Optional[Dict[str, Any]]:
    """从 LLM 输出解析 JSON"""
    # 尝试直接解析
    try:
        return __import__("json").loads(content)
    except Exception:
        pass

    # 尝试提取 JSON 块
    json_match = re.search(r"```json\s*(.*?)\s*```", content, re.DOTALL)
    if json_match:
        try:
            return __import__("json").loads(json_match.group(1))
        except Exception:
            pass

    # 尝试提取 { } 块
    brace_match = re.search(r"\{.*\}", content, re.DOTALL)
    if brace_match:
        try:
            return __import__("json").loads(brace_match.group(0))
        except Exception:
            pass

    return None


def _generate_summary(compressed_pages: List[Dict[str, Any]]) -> str:
    """生成全文摘要"""
    all_points = []
    all_financial = {}

    for page in compressed_pages:
        points = page.get("key_points", [])
        all_points.extend(points)

        fin_data = page.get("financial_data", {})
        all_financial.update(fin_data)

    summary_parts = []

    if all_points:
        summary_parts.append("关键要点:")
        for i, point in enumerate(all_points[:10], 1):
            summary_parts.append(f"{i}. {point}")

    if all_financial:
        summary_parts.append("\n核心财务数据:")
        for key, value in list(all_financial.items())[:10]:
            summary_parts.append(f"- {key}: {value}")

    return "\n".join(summary_parts) if summary_parts else "无可用摘要"
