"""
FinScope 勾稽校验器（B7，纯规则零 LLM）

职责: 抽取后对事实表跑会计恒等式校验（如 资产总计 = 负债合计 + 股东权益合计），
命中打 identity_checked=True；不符**告警降置信**（不静默、不删除）；无匹配跳过。

设计依据:
- 容差口径抄 Agentic FinSearch: max(0.01% × 期望值, 0.005)
- 必要不充分: 恒等式只能拦「两边都抽到且对不上」的错，一边没抽到无从校验（允许无匹配）
- 单位已知时先折算到元再比较；单位口径不明（部分已知部分缺失）时跳过
"""

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# 恒等式定义: 总账科目 = 分项之和（科目均为 metric_std 标准名，依赖 B6 别名归一）
_IDENTITIES = [
    ("资产总计", ["负债合计", "股东权益合计"]),
]

# 单位 → 元 的折算系数（与 table_serializer._UNIT_RE 的口径对齐）
_UNIT_SCALE = {
    "元": 1.0, "人民币元": 1.0, "人民幣元": 1.0,
    "千元": 1e3, "人民幣千元": 1e3, "人民币千元": 1e3,
    "万元": 1e4, "萬元": 1e4, "人民币万元": 1e4, "人民幣萬元": 1e4,
    "百万元": 1e6, "百萬元": 1e6, "人民币百万元": 1e6, "人民幣百萬元": 1e6,
    "亿元": 1e8, "億元": 1e8, "人民币亿元": 1e8, "人民幣億元": 1e8,
}


def _to_yuan(value: float, unit: str) -> float:
    scale = _UNIT_SCALE.get((unit or "").strip())
    return value * scale if scale is not None else value


def run_identity_checks(facts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    对 facts 就地跑勾稽校验。

    副作用:
    - 命中恒等式且通过: 相关 fact 打 identity_checked=True
    - 命中但不符: 相关 fact confidence 降为原值 × 0.6、打 identity_mismatch=True 并告警

    Returns:
        校验记录列表（入 L1 stats 供审计）: {identity, period, expected, actual, diff, ok, reason}
    """
    # 按 (metric_std, period) 建索引（同键取首条，与 dedup 语义一致）
    by_key: Dict[str, Dict[str, Any]] = {}
    for f in facts:
        key = (f.get("metric_std") or f.get("metric") or "", f.get("period") or "")
        by_key.setdefault(key, f)

    checks: List[Dict[str, Any]] = []
    periods = sorted({p for (_, p) in by_key.keys() if p})

    for total_name, part_names in _IDENTITIES:
        for period in periods:
            total = by_key.get((total_name, period))
            parts = [by_key.get((n, period)) for n in part_names]
            if total is None or any(p is None for p in parts):
                continue  # 允许无匹配（一边没抽到无从校验）

            involved = [total] + list(parts)
            units = {(f.get("unit") or "").strip() for f in involved}
            known = {u for u in units if u in _UNIT_SCALE}
            if 0 < len(known) < len(units):
                # 部分已知单位、部分缺失 → 口径不明，跳过（宁缺毋滥）
                checks.append({
                    "identity": f"{total_name} = {' + '.join(part_names)}",
                    "period": period, "ok": None, "reason": "unit_mixed",
                })
                continue

            expected = sum(_to_yuan(p["value"], p.get("unit") or "") for p in parts)
            actual = _to_yuan(total["value"], total.get("unit") or "")
            tol = max(0.0001 * abs(expected), 0.005)

            if abs(actual - expected) <= tol:
                for f in involved:
                    f["identity_checked"] = True
                checks.append({
                    "identity": f"{total_name} = {' + '.join(part_names)}",
                    "period": period, "expected": expected, "actual": actual,
                    "diff": actual - expected, "ok": True, "reason": "pass",
                })
            else:
                # 不符: 告警降置信，不删除不静默
                for f in involved:
                    f["confidence"] = round(f.get("confidence", 0.9) * 0.6, 3)
                    f["identity_mismatch"] = True
                checks.append({
                    "identity": f"{total_name} = {' + '.join(part_names)}",
                    "period": period, "expected": expected, "actual": actual,
                    "diff": actual - expected, "ok": False, "reason": "mismatch",
                })
                logger.warning(
                    "[IdentityChecker] 勾稽不符: %s @%s 期望 %.4g 实际 %.4g（差 %.4g，已降置信）",
                    total_name, period, expected, actual, actual - expected,
                )

    n_pass = sum(1 for c in checks if c.get("ok") is True)
    n_fail = sum(1 for c in checks if c.get("ok") is False)
    if checks:
        logger.info("[IdentityChecker] 勾稽校验: %d 通过 / %d 不符 / %d 跳过",
                    n_pass, n_fail, len(checks) - n_pass - n_fail)
    return checks
