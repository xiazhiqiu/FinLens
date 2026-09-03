"""
FinScope ContextPreparator（P5-E1，确定性编排 + LLM 受限叶子）

一次构建、全链复用（Analyst/Writer/Reviewer 零装配零重复构建）:
1. 十节标签 + 覆盖率（E2 门控输入，纯规则）
2. L2 急切构建（一次性 max_new=L2_EAGER_MAX_NEW，替代 Analyst 惰性 8/次；LLM 失败规则兜底）
3. L3 全局亮点（E6 配额版，构建一次跨轮复用）
4. E3 派生指标（确定性算子）
5. E5 跨源对账（facts ↔ MD&A 散文）
6. 装配: 全局 pdf_context（Writer/Reviewer/回退路径）+ 领域 domain_contexts（E2）

图接入: 确定性边 report_extractor → context_preparator → supervisor，
不经 Supervisor LLM 路由（确定性节点不该消耗 LLM 配额，也不该被误路由跳过）。
"""

import logging
from datetime import datetime
from typing import Any, Dict

from graphs.state import FinancialAnalysisState
from utils.config import get_settings

logger = logging.getLogger(__name__)

_L3_MIN_SECTIONS = 30  # 与旧 Analyst 惰性构建同门槛（小文档无 L3 必要）


def context_preparator_node(state: FinancialAnalysisState) -> Dict[str, Any]:
    agent_status = dict(state.get("agent_status", {}))
    error_log = list(state.get("error_log", []))
    l1 = state.get("pdf_l1") or {}
    sections = list(l1.get("sections") or [])
    settings = get_settings()

    if not sections:
        agent_status["context_preparator"] = "done"
        return {"agent_status": agent_status, "error_log": error_log}

    # 1) 十节标签 + 覆盖率（纯规则，E2 门控输入）
    from extractors.chapter_tagger import DOMAINS, sections_for_domain, tag_chapters, chapter_token_coverage
    chapter_map = tag_chapters(sections)
    coverage = chapter_token_coverage(sections, chapter_map)

    # 2) L2 急切构建（一次性 max_new=L2_EAGER_MAX_NEW；已有缓存全命中跳过）
    from extractors.section_compressor import build_global_summary_lite, compress_document_l2
    pdf_l2 = dict(state.get("pdf_l2") or {})
    try:
        compress_document_l2(
            sections, pdf_l2,
            min_text_tokens=settings.L2_MIN_TEXT_TOKENS,
            max_new=settings.L2_EAGER_MAX_NEW,
            use_llm=True,  # LLM 不可用时内部规则兜底（确定性）
        )
    except Exception as e:
        logger.warning("[ContextPreparator] L2 构建失败（不阻断主流程）: %s", str(e)[:120])

    # 3) L3 全局亮点（E6 配额版，构建一次跨轮复用）
    pdf_l3 = dict(state.get("pdf_l3") or {})
    if not pdf_l3.get("text") and len(sections) >= _L3_MIN_SECTIONS:
        company = next(
            (str(e.get("entity_name", "")) for e in state.get("extracted_entities", [])
             if e.get("entity_type") == "company"),
            "",
        )
        try:
            pdf_l3 = {
                "text": build_global_summary_lite(l1, company=company),
                "source": "l3_lite_rules",
                "built_at": datetime.now().isoformat(timespec="seconds"),
            }
        except Exception as e:
            logger.warning("[ContextPreparator] L3 构建失败（跳过）: %s", str(e)[:120])

    # 4) E3 派生指标（确定性算子，零 LLM）
    from extractors.derived_metrics import compute_derived_metrics
    derived_metrics = compute_derived_metrics(l1.get("facts") or [])

    # 5) E5 跨源对账（facts ↔ MD&A 散文，零 LLM）
    from extractors.cross_checker import cross_check_prose_vs_facts
    cross_source_checks = cross_check_prose_vs_facts(l1, chapter_map)

    # 6) 装配: 全局 pdf_context + 领域 domain_contexts（十节覆盖达标且 flag 开才做领域装配）
    from extractors.context_assembler import assemble
    pdf_context = assemble(
        state.get("user_query", ""), settings.CONTEXT_BUDGET_TOKENS, l1,
        l2=pdf_l2 or None, l3=pdf_l3.get("text") or None,
    )["context"]

    domain_contexts: Dict[str, str] = {}
    if settings.USE_DOMAIN_AGENTS and coverage >= settings.DOMAIN_CHAPTER_COVERAGE_MIN:
        for d in DOMAINS:
            sub = sections_for_domain(sections, chapter_map, d["key"])
            if not sub:
                continue
            domain_contexts[d["key"]] = assemble(
                state.get("user_query", ""), settings.CONTEXT_BUDGET_TOKENS,
                {"sections": sub, "tables": l1.get("tables"), "facts": l1.get("facts")},
                l2=pdf_l2 or None, l3=pdf_l3.get("text") or None,
            )["context"]

    agent_status["context_preparator"] = "done"
    logger.info(
        "[ContextPreparator] 完成: 十节覆盖 %.1f%%, L2 %d 条, L3 %s, 派生指标 %d, 领域 %d/%d",
        coverage * 100, len(pdf_l2), "有" if pdf_l3.get("text") else "无",
        len(derived_metrics), len(domain_contexts), len(DOMAINS),
    )
    return {
        "chapter_map": chapter_map,
        "pdf_l2": pdf_l2,
        "pdf_l3": pdf_l3,
        "derived_metrics": derived_metrics,
        "cross_source_checks": cross_source_checks,
        "pdf_context": pdf_context,
        "domain_contexts": domain_contexts,
        "agent_status": agent_status,
        "error_log": error_log,
    }