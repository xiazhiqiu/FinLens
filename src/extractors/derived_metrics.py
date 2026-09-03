"""
FinScope 确定性算子层（P5-E3，纯 Python 零 LLM）

LLM 心算是数字错误的结构性来源（FinRobot 教训）——增长率/利润率/偿债比率
全部由本模块确定性计算，LLM 只消费结果（display 直接引用，禁止改写）。

设计:
- 输入: L1 facts 表；索引 (company, metric_std, period) -> fact
- 量纲: 单位折算复用 B7 identity_checker._UNIT_SCALE 口径（单一事实源）；
  多输入单位一致（含同为空）或均可折元才可比，否则 skip 不硬算
- 产物: {label, period, value, display, formula, sources, status}
  status ∈ ok / skipped_missing_inputs / skipped_unit_unknown /
           skipped_zero_denominator / skipped_direct_exists
- 增长率 skipped 项保留（相邻年度缺口是数据质量信号）；比率输入不齐静默跳过（防噪声）
- render_derived_metrics 只渲染 ok 项（Writer/Analyst/Synthesizer 注入用）
"""

import re
from typing import Any, Dict, List, Optional, Tuple

from extractors.identity_checker import _UNIT_SCALE

_FY_RE = re.compile(r"^FY(\d{4})$")

# 增长率: (展示名, 源科目)
_GROWTH_DEFS = (
    ("营业收入增长率", "营业收入"),
    ("归母净利润增长率", "归母净利润"),
    ("净利润增长率", "净利润"),
    ("总资产增长率", "资产总计"),
)

# 比率: (展示名, 分子候选, 分母候选, 格式 pct|x, 额外扣减科目候选或 None)
#   候选列表吸收 A股 CAS 与港股 IFRS 口径差异，不动 B6 别名表
_RATIO_DEFS = (
    ("净利率", ("归母净利润", "净利润"), ("营业收入", "营业总收入"), "pct", None),
    ("资产负债率", ("负债合计", "总负债", "總負債"), ("资产总计", "总资产", "總資產"), "pct", None),
    ("流动比率", ("流动资产合计", "流動資產合計"), ("流动负债合计", "流動負債合計"), "x", None),
    ("速动比率", ("流动资产合计", "流動資產合計"), ("流动负债合计", "流動負債合計"), "x", ("存货", "存貨")),
)


def _key(company: str, metric: str, period: str) -> Tuple[str, str, str]:
    return (company or "", metric, period or "")


def _pick(idx, company: str, names, period: str) -> Optional[Dict[str, Any]]:
    for n in names:
        f = idx.get(_key(company, n, period))
        if f is not None:
            return f
    return None


def _same_scale(*fs):
    """多条事实折算到同一量纲（优先元）。全部单位一致（含同为空）或全部可折元 → 数值元组；否则 None。"""
    if any(f.get("value") is None for f in fs):
        return None
    us = {(f.get("unit") or "").strip() for f in fs}
    if len(us) == 1:
        u = next(iter(us))
        scale = _UNIT_SCALE.get(u, 1.0)  # 同一未知单位: 内部自洽，按原值比较
        return tuple(float(f["value"]) * scale for f in fs)
    if all(u in _UNIT_SCALE for u in us):
        return tuple(float(f["value"]) * _UNIT_SCALE[(f.get("unit") or "").strip()] for f in fs)
    return None


def _src_ref(f: Dict[str, Any]) -> Dict[str, Any]:
    src = f.get("source") or {}
    return {"metric": f.get("metric_std") or f.get("metric"), "period": f.get("period"),
            "raw": f.get("raw"), "page_idx": src.get("page_idx"), "table_id": src.get("table_id")}


def _mk(label, period, value, display, formula, sources, status="ok"):
    return {"label": label, "period": period, "value": value, "display": display,
            "formula": formula, "sources": sources, "status": status}


def compute_derived_metrics(facts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """facts 表 -> 派生指标列表（含 skipped 项，render 只取 ok 项）"""
    # 算术索引: 高置信非 pct 事实；直接名集合: 全量（含 pct，直存检测用）
    idx: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    direct_names: set = set()
    for f in facts or []:
        k = _key(f.get("company") or "", f.get("metric_std") or f.get("metric") or "", f.get("period") or "")
        direct_names.add(k)
        if f.get("confidence", 0) < 0.9 or f.get("is_pct") or f.get("value") is None:
            continue
        idx.setdefault(k, f)

    out: List[Dict[str, Any]] = []

    # 1) 增长率（同科目相邻 FY）
    # 候选键取自原始 facts（含被排除的 pct/低置信项），当前年算术事实仍从 idx 取——
    # 这样“科目存在但无可算输入”也会被记录为数据质量信号（skipped），而非凭空消失。
    _growth_label = {src: label for label, src in _GROWTH_DEFS}
    grow_keys = set()
    for f in facts or []:
        if not _FY_RE.match((f.get("period") or "")):
            continue
        if (f.get("metric_std") or f.get("metric") or "") in _growth_label:
            grow_keys.add(_key(f.get("company") or "", f.get("metric_std") or f.get("metric") or "",
                               f.get("period") or ""))
    for company, metric, period in sorted(grow_keys):
        label = _growth_label[metric]
        year = int(_FY_RE.match(period).group(1))
        f = idx.get(_key(company, metric, period))  # 可能因 pct/低置信被排除 -> None
        if _key(company, label, period) in direct_names:
            out.append(_mk(label, period, None, "", "（报告已直接披露，不重复计算）",
                           [_src_ref(f)] if f else [], status="skipped_direct_exists"))
            continue
        if f is None:
            out.append(_mk(label, period, None, "", f"{metric} 无可算算术事实",
                           [], status="skipped_missing_inputs"))
            continue
        prev_f = idx.get(_key(company, metric, f"FY{year - 1}"))
        pair = _same_scale(f, prev_f) if prev_f is not None else None
        if prev_f is None:
            out.append(_mk(label, period, None, "", f"{metric} 相邻年度缺失",
                           [_src_ref(f)], status="skipped_missing_inputs"))
        elif pair is None:
            out.append(_mk(label, period, None, "",
                           f"单位不可比（{f.get('unit')!r} vs {prev_f.get('unit')!r}）",
                           [_src_ref(f), _src_ref(prev_f)], status="skipped_unit_unknown"))
        elif abs(pair[1]) < 1e-9:
            out.append(_mk(label, period, None, "", f"{metric} FY{year - 1} 为零",
                           [_src_ref(f), _src_ref(prev_f)], status="skipped_zero_denominator"))
        else:
            g = (pair[0] - pair[1]) / abs(pair[1])
            out.append(_mk(label, period, g, f"{g * 100:+.2f}%",
                           f"{metric} {period} / {metric} FY{year - 1} - 1",
                           [_src_ref(f), _src_ref(prev_f)]))

    # 2) 比率（净利率/资产负债率/流动·速动比率）
    for company, period in sorted({(c, p) for (c, _m, p) in idx.keys()}):
        for label, num_names, den_names, fmt, sub_names in _RATIO_DEFS:
            if _key(company, label, period) in direct_names:
                continue  # 报告已直接披露，不重复计算
            den = _pick(idx, company, den_names, period)
            num = _pick(idx, company, num_names, period)
            sub = _pick(idx, company, sub_names, period) if sub_names else None
            if num is None or den is None or (sub_names and sub is None):
                continue  # 输入不齐静默跳过（防 skipped 噪声刷屏）
            vals = _same_scale(*( [num, sub, den] if sub is not None else [num, den] ))
            if vals is None:
                continue
            n, d = (vals[0] - vals[1], vals[2]) if sub is not None else (vals[0], vals[1])
            if abs(d) < 1e-9:
                continue
            v = n / d
            formula = (f"({num.get('metric_std') or num.get('metric')} - "
                       f"{sub.get('metric_std') or sub.get('metric')}) / "
                       f"{den.get('metric_std') or den.get('metric')}" if sub is not None else
                       f"{num.get('metric_std') or num.get('metric')} / "
                       f"{den.get('metric_std') or den.get('metric')}")
            srcs = [_src_ref(x) for x in ([num, sub, den] if sub is not None else [num, den])]
            out.append(_mk(label, period, v, f"{v * 100:.2f}%" if fmt == "pct" else f"{v:.2f}",
                           formula, srcs))

    return out


def render_derived_metrics(metrics: List[Dict[str, Any]]) -> str:
    """派生指标表（Markdown，只渲染 ok 项；无 ok 项返回空串）"""
    ok = [m for m in metrics or [] if m.get("status") == "ok"]
    if not ok:
        return ""
    lines = ["| 指标 | 期间 | 数值 | 公式 | 来源 |", "|---|---|---|---|---|"]
    for m in ok:
        src = "; ".join(
            f"p{s.get('page_idx', '?')}" + (f" {s.get('table_id')}" if s.get("table_id") else "")
            for s in m.get("sources") or []
        )
        lines.append(f"| {m['label']} | {m['period']} | {m['display']} | {m['formula']} | {src} |")
    return "\n".join(lines)
