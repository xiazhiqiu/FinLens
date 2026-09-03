"""
FinScope 表格序列化器（P1 地基，纯规则零 LLM）

职责:
1. parse_table_html: HTML 表格 -> 二维网格（lxml，展开 rowspan/colspan）
2. serialize_table: Table -> Markdown 文本，超长按行边界分块且**每块重复表头**
   —— 任意切分点列语义可辨（分块安全性），且比「逐行前置表头」省约一半 token

设计依据（2026-09-03 真实年报产物核实，joinn 2024）:
- 115 个表格中 29 个含 rowspan、44 个含 colspan（38%），必须展开合并单元格
- 最大表格 166 行，超过 rows_per_chunk(30) 的宽表不少，分块重复表头是刚需
- 原始 HTML 另行保留在 Table["html"]，供前端渲染与溯源（双视图）
"""

import logging
import re
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# 常见货币单位（从 caption/footnote 识别）
_UNIT_RE = re.compile(r"(人民幣|人民币)?\s*(千元|万元|萬元|亿元|億元|百万|百萬|元)")


def _clean_cell_text(text: str) -> str:
    """单元格文本清洗: 压空白、竖线换全角（防破坏 Markdown 列分隔）"""
    text = re.sub(r"\s+", " ", text).strip()
    return text.replace("|", "｜").replace("\n", " ")


def parse_table_html(html: str) -> List[List[str]]:
    """
    HTML 表格 -> 二维文本网格（展开 rowspan/colspan）

    Returns:
        rows[row][col] = str；解析失败返回 []（never-throw，调用方降级处理）
    """
    if not html or "<table" not in html.lower():
        return []

    try:
        from lxml import html as lxml_html

        doc = lxml_html.fromstring(html)
        tables = doc.xpath("//table")
        if not tables:
            return []
        table = tables[0]

        # MinerU 产物是 <table><tr>... 平铺结构；兼容 tbody/thead 包裹
        trs = table.xpath("./tr") or table.xpath(".//tr")
        if not trs:
            return []

        grid: Dict[tuple, str] = {}
        occupied = set()
        r = 0
        for tr in trs:
            c = 0
            cells = tr.xpath("./td | ./th")
            for cell in cells:
                while (r, c) in occupied:
                    c += 1
                text = _clean_cell_text(cell.text_content() or "")
                try:
                    rowspan = max(1, int(cell.get("rowspan", 1) or 1))
                    colspan = max(1, int(cell.get("colspan", 1) or 1))
                except (TypeError, ValueError):
                    rowspan = colspan = 1
                for dr in range(rowspan):
                    for dc in range(colspan):
                        occupied.add((r + dr, c + dc))
                        grid[(r + dr, c + dc)] = text
                c += colspan
            r += 1

        if not grid:
            return []

        n_cols = max(c for (_, c) in grid) + 1
        n_rows = max(rr for (rr, _) in grid) + 1
        rows = [[grid.get((rr, cc), "") for cc in range(n_cols)] for rr in range(n_rows)]
        return rows

    except Exception as e:
        # 坏表格不阻断主链路，返回空由调用方降级（保留原始 HTML 兜底）
        logger.warning("[TableSerializer] 表格解析失败（降级保留原始 HTML）: %s", str(e)[:100])
        return []


# B8: 邻接文本单位解析（严格模式——必须带明确标记，宁缺毋滥）
# 匹配 "單位：千元" / "单位:人民币元" / "（人民幣千元）" / "(百万元)" 等
_ADJ_UNIT_RE = re.compile(
    r"(?:單位|单位)\s*[:：]?\s*(人民幣|人民币)?\s*(千元|万元|萬元|亿元|億元|百萬元|百万元|元)"
    r"|[（(]\s*(人民幣|人民币)?\s*(千元|万元|萬元|亿元|億元|百萬元|百万元|元)\s*[)）]"
)

# B8: 网格单位行——港股报表单位常在表格第 2 行单元格（如 <td>人民幣千元</td> 列标签行）
_UNIT_TOKEN_RE = re.compile(r"(人民幣|人民币)?(千元|萬元|百萬元|億元|万元|亿元|百万元|元)")


def build_table_record(
    table_id: str,
    page_idx: int,
    html: str,
    caption: List[str],
    footnote: List[str],
    adjacent_text: str = "",
) -> Dict[str, Any]:
    """
    原始表格 item -> Table 记录（l1_builder 调用）

    表头取首行；单位从 caption/footnote 识别，缺省时从表格前邻接文本严格解析（B8）；
    解析失败时 headers/rows 置空、html 原文保留兜底。
    """
    rows = parse_table_html(html)
    cap_text = " ".join(str(c) for c in caption) if caption else ""
    note_text = " ".join(str(f) for f in footnote) if footnote else ""

    unit = ""
    m = _UNIT_RE.search(cap_text + " " + note_text)
    if m:
        unit = (m.group(1) or "") + m.group(2)

    if not unit and adjacent_text:
        # B8: caption/footnote 无单位 → 表格前同页邻接文本严格解析（须带單位/（）标记）
        m2 = _ADJ_UNIT_RE.search(adjacent_text[-160:])
        if m2:
            cur = m2.group(1) or m2.group(3) or ""
            u = m2.group(2) or m2.group(4) or ""
            unit = cur + u

    if not unit and len(rows) > 1:
        # B8: 网格单位行——港股报表前两数据行的单元格恰为单位词（'人民幣千元'）
        for r in rows[1:3]:
            for cell in r[1:]:  # 跳过行标签列
                cs = re.sub(r"\s+", "", str(cell or ""))
                if cs and _UNIT_TOKEN_RE.fullmatch(cs):
                    unit = cs
                    break
            if unit:
                break

    return {
        "table_id": table_id,
        "page_idx": page_idx,
        "caption": cap_text.strip(),
        "unit": unit,
        "headers": rows[0] if rows else [],
        "rows": rows[1:] if rows else [],
        "n_rows": len(rows),
        "html": html,  # 双视图: 原始 HTML 保留供前端渲染/溯源
    }


def _md_row(cells: List[str]) -> str:
    return "| " + " | ".join(c if c else " " for c in cells) + " |"


def serialize_table(table: Dict[str, Any], rows_per_chunk: int = 30) -> List[str]:
    """
    Table -> Markdown 分块文本（每块重复表头，任意切分点列语义可辨）

    Returns:
        List[str] —— 每个元素是一个自包含的 Markdown 块（含元信息行 + 表头 + 数据行）
    """
    headers = table.get("headers") or []
    rows = table.get("rows") or []
    if not headers:
        # 解析失败: 降级为原始 HTML 提示（下游至少知道这里有表）
        return [f"[表 {table.get('table_id', '?')} | 解析失败，原始 HTML 保留于 L1.tables]"]

    meta = f"[表 {table.get('table_id', '?')}"
    if table.get("caption"):
        meta += f" | {table['caption']}"
    if table.get("unit"):
        meta += f" | 单位: {table['unit']}"
    meta += f" | 来源: p{table.get('page_idx', '?')}]"

    header_md = _md_row(headers)
    sep_md = "|" + "---|" * len(headers)

    chunks: List[str] = []
    if not rows:
        chunks.append(f"{meta}\n{header_md}\n{sep_md}")
        return chunks

    for start in range(0, len(rows), rows_per_chunk):
        part = rows[start : start + rows_per_chunk]
        lines = [meta]
        if start > 0:
            lines.append(f"（接上表，第 {start + 1}-{start + len(part)} 行）")
        lines.append(header_md)
        lines.append(sep_md)
        lines.extend(_md_row(r) for r in part)
        chunks.append("\n".join(lines))

    return chunks
