"""
FinScope 跨源交叉核对（P5-E5，确定性对账 job，零 LLM）

facts 表非「确定正确」（三类不准确: 实现 bug / 覆盖缺口 / 上游 MinerU 噪声）。
B7 只校 facts 内部勾稽；本模块补最后一道防线: MD&A 散文数字 ↔ facts 表数字
（两个独立来源）的一致性核对——领域架构下近乎免费。

输出（聚合到指标粒度）:
{metric, status: consistent|mismatch|no_prose, n_prose_hits, n_consistent,
 n_table_facts, mismatches: [{prose_value, prose_src, table_values, rel_diff}]}
mismatches 仅作告警（散文常见整数亿舍入），Reviewer 消费后裁决，不硬失败。
"""

import re
from typing import Any, Dict, List

# 散文科目短语（只查头部指标；「净利润」不单列——避免「归母净利润」子串误命中）
_PROSE_METRICS = {
    "营业收入": "营业收入|营业总收入|营收|總收益",
    "归母净利润": "归母净利润|归属于上市公司股东的净利润|归属于母公司股东的净利润",
    "资产总计": "资产总计|总资产|资产总额|總資產",
}

_NUM_UNIT_RE = r"([0-9][0-9,，.]*\.?[0-9]*)\s*(亿元|億元|百万元|百萬元|万元|萬元|千元|元)"
_PROSE_UNIT_SCALE = {"元": 1.0, "千元": 1e3, "万元": 1e4, "萬元": 1e4,
                     "百万元": 1e6, "百萬元": 1e6, "亿元": 1e8, "億元": 1e8}

_MDNA_CHAPTER = 3  # 第三节 管理层讨论与分析


def _parse_prose_number(num: str, unit: str) -> float:
    return float(num.replace(",", "").replace("，", "")) * _PROSE_UNIT_SCALE[unit]


def _fact_to_yuan(f: Dict[str, Any]):
    from extractors.identity_checker import _UNIT_SCALE
    u = (f.get("unit") or "").strip()
    if u not in _UNIT_SCALE or f.get("value") is None:
        return None
    return float(f["value"]) * _UNIT_SCALE[u]


def cross_check_prose_vs_facts(
    l1: Dict[str, Any], chapter_map: Dict[str, int], tol_rel: float = 0.02
) -> List[Dict[str, Any]]:
    """MD&A 散文数字 vs 表格 facts（confidence≥0.9、非 pct、表格来源）。

    匹配口径: 散文数字与该科目**任一期间**的表格值相对偏差 ≤ tol_rel 即 consistent
    （散文常提及上年对比值）。散文四舍五入常见，tol=2% 是告警线不是硬错。
    """
    sections = l1.get("sections") or []
    mdna = [(s.get("section_id", "?"), (s.get("page_range") or [0])[0], s.get("text") or "")
            for s in sections if chapter_map.get(s.get("section_id", "?"), 0) == _MDNA_CHAPTER]

    table_facts = [f for f in (l1.get("facts") or [])
                   if f.get("confidence", 0) >= 0.9 and not f.get("is_pct")
                   and (f.get("source") or {}).get("table_id")]

    checks: List[Dict[str, Any]] = []
    for metric_std, phrase in _PROSE_METRICS.items():
        facts_m = [f for f in table_facts if (f.get("metric_std") or f.get("metric")) == metric_std]
        if not facts_m:
            continue
        known = [(y, f) for y, f in ((_fact_to_yuan(f), f) for f in facts_m) if y is not None]
        lead_re = re.compile(rf"(?:{phrase})")
        num_re = re.compile(_NUM_UNIT_RE)

        # 一旦出现科目短语，本段内此后的带单位数字都视为该科目散文值
        # （散文常省略重复科目词，只写「A 亿元，增长至 B 亿元」；后续再与任一期间对账）
        hits: List[Any] = []
        for sid, page, text in mdna:
            leads = [m.start() for m in lead_re.finditer(text)]
            if not leads:
                continue
            anchor = min(leads)
            for m in num_re.finditer(text):
                if m.start() < anchor:
                    continue
                try:
                    y = _parse_prose_number(m.group(1), m.group(2))
                except ValueError:
                    continue
                hits.append((y, f"{m.group(1)}{m.group(2)}", f"p{page}（{sid}）"))

        if not hits:
            checks.append({"metric": metric_std, "status": "no_prose", "n_prose_hits": 0,
                           "n_consistent": 0, "n_table_facts": len(facts_m), "mismatches": []})
            continue

        mismatches, n_consistent = [], 0
        for y, raw, src in hits:
            best = min((abs(y - ty) / max(abs(ty), 1e-9) for ty, _ in known), default=None)
            if best is None:
                mismatches.append({"prose_value": raw, "prose_src": src,
                                   "table_values": [f.get("raw") for f in facts_m],
                                   "rel_diff": None, "reason": "table_unit_unknown"})
            elif best > tol_rel:
                mismatches.append({"prose_value": raw, "prose_src": src,
                                   "table_values": [f.get("raw") for f in facts_m],
                                   "rel_diff": round(best, 4)})
            else:
                n_consistent += 1
        checks.append({"metric": metric_std,
                       "status": "mismatch" if mismatches else "consistent",
                       "n_prose_hits": len(hits), "n_consistent": n_consistent,
                       "n_table_facts": len(facts_m), "mismatches": mismatches})
    return checks


def render_cross_warnings(checks: List[Dict[str, Any]]) -> str:
    """跨源告警渲染（Writer/Reviewer 注入用）；无 mismatch 返回空串"""
    mismatches = [c for c in checks or [] if c.get("status") == "mismatch"]
    if not mismatches:
        return ""
    lines = ["## ⚠ 跨源核对告警（facts 表 ↔ MD&A 散文，确定性对账发现不一致）"]
    for c in mismatches:
        for m in c.get("mismatches") or []:
            diff = f"，相对偏差 {m['rel_diff']:.1%}" if m.get("rel_diff") is not None else ""
            lines.append(f"- {c['metric']}: 散文 {m['prose_value']}（{m['prose_src']}）"
                         f" vs 表格 {m.get('table_values')}{diff}")
    lines.append("引用该科目数字以 facts 表（query_fact 可溯源）为准；散文值需 fetch_context 核对原文后取舍。")
    return "\n".join(lines)