"""
FinScope 上下文工具工厂（P2，Agent 工具化）

产出 langchain @tool（可 bind_tools），闭包绑定本次分析的 L1（sections/tables/facts）。

三个工具（Analyst/Writer 是**真工具持有者**，可按需取数——不再被动接收全量注入）:
- fetch_context(scope): 按 section_id / "p12-14" 调取章节无损原文（含表格）
- query_fact(company, metric, period): 精确查 Fact（带溯源），三参数均可省略做过滤
- search_section(query, top_k): 章节关键词检索，返回命中标题 + 页码 + 摘要

设计约束:
- 确定性实现（查 L1 结构化数据，零 LLM）
- 输出截断防止污染上下文（fetch_context 上限 ~8000 字符）
- 任何异常返回可读错误串，绝不 throw（工具执行必须安全）
"""

import logging
import re
from typing import Any, Callable, Dict, List, Optional

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

_MAX_FETCH_CHARS = 8000
_MAX_SEARCH_CHARS = 3000


def _render_section_with_tables(
    section: Dict[str, Any],
    tables_by_id: Dict[str, Dict[str, Any]],
) -> str:
    """章节无损渲染（标题行 + 正文 + 表格分块）——与装配器 render 一致"""
    lines: List[str] = []
    pr = section.get("page_range") or []
    lines.append(
        f"## [{section.get('section_id')} | {section.get('title')} | "
        f"{section.get('tier')} | p{pr[0] if pr else '?'}-{pr[-1] if pr else '?'}]"
    )
    text = (section.get("text") or "").strip()
    if text:
        lines.append(text)
    for tid in section.get("table_ids") or []:
        tb = tables_by_id.get(tid)
        if tb is None:
            continue
        if not tb.get("headers"):
            lines.append(f"[表 {tid} | 解析失败，保留原始 HTML]")
            continue
        from extractors.table_serializer import serialize_table
        for chunk in serialize_table(tb):
            lines.append(chunk)
    return "\n".join(lines)


def _fact_line(fact: Dict[str, Any]) -> str:
    src = fact.get("source") or {}
    unit = fact.get("unit") or ""
    return (
        f"{fact.get('company') or '(未绑定)'} | {fact.get('metric')} | {fact.get('period')} "
        f"= {fact.get('raw')}{unit}"
        f" (src p{src.get('page_idx', '?')} {src.get('table_id') or src.get('section_id') or ''})"
    )


def build_context_tools(
    l1: Dict[str, Any],
    on_tool_call: Optional[Callable[[str, Dict[str, Any], str], None]] = None,
) -> List[Any]:
    """
    构造三个上下文工具（闭包绑定本次 L1）。

    Args:
        l1: {"sections", "tables", "facts"}
        on_tool_call: 审计回调 (tool_name, args, result)，由 Agent 层接 tool_call_history
    """
    sections = list(l1.get("sections") or [])
    tables_by_id = {t.get("table_id"): t for t in (l1.get("tables") or [])}
    facts = list(l1.get("facts") or [])

    def _audit(name: str, args: Dict[str, Any], result: str) -> None:
        if on_tool_call:
            try:
                on_tool_call(name, args, result)
            except Exception as e:
                logger.warning("[ContextTools] 审计回调异常: %s", str(e)[:100])

    @tool
    def fetch_context(scope: str) -> str:
        """
        按需调取 L1 无损原文。
        scope 支持: 章节ID（如 "s_012"）或页范围（如 "p12-14"）。
        返回该章节/页的正文与表格（Markdown，每块带表头）；找不到返回明确提示。
        """
        try:
            scope = (scope or "").strip()
            out: List[str] = []

            # 页范围: p12 或 p12-14
            m = re.fullmatch(r"p(\d+)(?:-(\d+))?", scope, re.IGNORECASE)
            if m:
                p_start, p_end = int(m.group(1)), int(m.group(2) or m.group(1))
                hit = [s for s in sections
                       if (s.get("page_range") or [0, 0])[0] <= p_end
                       and (s.get("page_range") or [0, 0])[-1] >= p_start]
                for sec in hit:
                    out.append(_render_section_with_tables(sec, tables_by_id))
                if not out:
                    return f"未找到页 {scope} 对应的章节"
            else:
                hit = [s for s in sections if s.get("section_id") == scope]
                if not hit:
                    return (f"未找到章节 {scope}。可用章节: "
                            + ", ".join(s.get("section_id") for s in sections[:20]))
                out.append(_render_section_with_tables(hit[0], tables_by_id))

            result_text = "\n\n".join(out)
            _audit("fetch_context", {"scope": scope}, result_text)
            return result_text[: _MAX_FETCH_CHARS]
        except Exception as e:
            return f"fetch_context 异常: {str(e)[:200]}"

    @tool
    def query_fact(company: str = "", metric: str = "", period: str = "") -> str:
        """
        精确查询事实表（结构化数字，带溯源）。
        参数均可省略作过滤: company 公司名 / metric 科目 / period 期间（如 FY2024）。
        命中返回若干条 "公司 | 科目 | 期间 = 数值(单位) (src p页码 表格/章节)"；
        未命中返回明确提示，并建议改用 search_section 查原文。
        """
        try:
            from extractors.fact_extractor import normalize_metric  # B6: 查询侧别名归一

            metric_std = normalize_metric(metric) if metric else ""
            hits = []
            for f in facts:
                if company and company not in (f.get("company") or ""):
                    continue
                # B6: 同时匹配原始科目名与标准名（查询"营业收入"可命中"總收益"）
                if metric_std and metric_std not in (
                    f.get("metric_std") or "", f.get("metric") or ""
                ):
                    continue
                if period and period not in (f.get("period") or ""):
                    continue
                hits.append(f)
                if len(hits) >= 10:
                    break
            if not hits:
                _audit("query_fact", {"company": company, "metric": metric, "period": period}, "")
                cond = " 且 ".join(x for x in (company, metric, period) if x) or "全部"
                return f"事实表未命中: {cond}（可尝试省略条件，或用 search_section 查原文）"
            result_text = "\n".join(_fact_line(f) for f in hits)
            _audit("query_fact", {"company": company, "metric": metric, "period": period}, result_text)
            return result_text
        except Exception as e:
            return f"query_fact 异常: {str(e)[:200]}"

    @tool
    def search_section(query: str, top_k: int = 5) -> str:
        """
        章节关键词检索。
        按 query 分词（支持中文连续串 + 空格/顿号分隔）对章节标题与正文打分，
        返回命中章节的标题/页码/摘要片段。
        """
        try:
            terms = [t for t in re.split(r"[\s、，,]+", (query or "").strip()) if len(t) >= 1]
            if not terms:
                return "query 为空"

            scored = []
            for sec in sections:
                title = sec.get("title") or ""
                text = (sec.get("text") or "")[:2000]
                score = 0
                for term in terms:
                    score += title.count(term) * 3 + text.count(term)
                if score > 0:
                    scored.append((score, sec))

            scored.sort(key=lambda x: -x[0])
            pr = lambda s: (s.get("page_range") or [0, 0])[0]
            lines = []
            for score, sec in scored[: max(1, min(top_k, 20))]:
                text_snip = (sec.get("text") or "").strip().replace("\n", " ")[:120]
                lines.append(
                    f"[{score}分] {sec.get('section_id')} {sec.get('title')} "
                    f"(p{pr(sec)}, {sec.get('tier')})"
                    + (f": {text_snip}" if text_snip else "")
                )
            result_text = "\n".join(lines) if lines else "无命中章节"
            _audit("search_section", {"query": query, "top_k": top_k}, result_text)
            return result_text[: _MAX_SEARCH_CHARS]
        except Exception as e:
            return f"search_section 异常: {str(e)[:200]}"

    return [fetch_context, query_fact, search_section]
