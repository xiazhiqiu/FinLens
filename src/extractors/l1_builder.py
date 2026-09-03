"""
FinScope L1 构建器（P1 地基，纯规则零 LLM）

职责: 编排 section_segmenter + table_serializer + fact_extractor，
产出 L1 结构化无损层 {sections, tables, facts}。

L1 是 structured_pages 的确定性函数 → 由调用方（financial_tools 解析链路）
在解析层构建并与解析结果一同落 parse_cache，命中缓存零重建。
"""

import logging
from typing import Any, Dict, List, Optional

from extractors.fact_extractor import extract_facts
from extractors.identity_checker import run_identity_checks
from extractors.section_segmenter import segment_sections
from extractors.table_serializer import build_table_record

logger = logging.getLogger(__name__)

# 注: 计量单位当前仅信任表格自带的 caption/footnote（build_table_record 内解析）。
# MinerU 多数表 caption 为空（表题是表格前的独立 text item）→ 单位缺省。
# P1 曾用「整章正文正则探测单位」补齐，实测误命中率高（如把千元表误标为百萬元），
# 宁缺毋滥回退——正确解法是「表格前邻接文本（同页、紧邻段落）解析」，列入 P3 待办。


def build_l1(
    structured_pages: List[Dict[str, Any]],
    companies: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    构建完整 L1（零 LLM，确定性）

    Args:
        structured_pages: mineru_extractor 归一化输出（item.type ∈ {heading, text, table}）
        companies: 报告主体公司名（来自实体抽取，用于 Fact 主体绑定）

    Returns:
        {
            "sections": [...],   # 章节（含 tier 分档与 tier_reason）
            "tables":   [...],   # Table 记录（结构化网格 + 原始 HTML 双视图）
            "facts":    [...],   # Fact 记录（company/metric/period/source 四件套）
            "stats":    {...},   # 构建统计
        }
    """
    sections = segment_sections(structured_pages)

    # 章节内挂载的原始表格 -> Table 记录
    tables: List[Dict[str, Any]] = []
    n_table_parse_fail = 0
    for sec in sections:
        for raw in sec.pop("_tables", []):
            record = build_table_record(
                table_id=raw["table_id"],
                page_idx=raw["page_idx"],
                html=raw["html"],
                caption=raw.get("caption", []),
                footnote=raw.get("footnote", []),
                adjacent_text=raw.get("adjacent_text", ""),  # B8: 表格前邻接文本
            )
            if not record["headers"]:
                n_table_parse_fail += 1
            tables.append(record)

    facts = extract_facts(sections, tables, companies)
    identity_checks = run_identity_checks(facts)  # B7: 勾稽校验（就地打标/降置信）

    tier_counts: Dict[str, int] = {}
    for sec in sections:
        tier_counts[sec["tier"]] = tier_counts.get(sec["tier"], 0) + 1

    stats = {
        "n_pages": len(structured_pages),
        "n_sections": len(sections),
        "tier_counts": tier_counts,
        "n_tables": len(tables),
        "n_table_parse_fail": n_table_parse_fail,
        "n_facts": len(facts),
        "identity_checks": identity_checks,  # B7: 勾稽校验记录（审计）
        "n_text_chars": sum(len(s.get("text", "")) for s in sections),
    }
    log_stats = {
        k: v for k, v in stats.items() if k not in ("tier_counts", "identity_checks")
    } | {
        "tiers": stats["tier_counts"],
        "n_identity_checks": len(identity_checks),
    }
    logger.info("[L1Builder] L1 构建完成: %s", log_stats)

    return {"sections": sections, "tables": tables, "facts": facts, "stats": stats}
