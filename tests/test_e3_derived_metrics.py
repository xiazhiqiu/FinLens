"""P5-E3 测试: 确定性算子层（增长率/利润率/偿债比率，纯 Python 零 LLM）"""

from extractors.derived_metrics import compute_derived_metrics, render_derived_metrics


def _f(metric, period, value, unit="元", page=8, table="t_001"):
    return {"company": "富瑞特装", "metric": metric, "metric_std": metric, "period": period,
            "value": value, "raw": f"{value:,}", "is_pct": False, "is_subtotal": False,
            "unit": unit, "confidence": 0.9,
            "source": {"page_idx": page, "table_id": table}}


def _get(metrics, label, period="FY2024"):
    return next(m for m in metrics if m["label"] == label and m.get("period") == period)


def test_growth_same_unit():
    ms = compute_derived_metrics([
        _f("营业收入", "FY2024", 110.0), _f("营业收入", "FY2023", 100.0),
    ])
    m = _get(ms, "营业收入增长率")
    assert m["status"] == "ok"
    assert m["value"] == 0.1 and m["display"] == "+10.00%"
    assert len(m["sources"]) == 2 and m["sources"][0]["page_idx"] == 8


def test_growth_cross_unit_folded_to_yuan():
    """万元 vs 亿元 自动折元后可比"""
    ms = compute_derived_metrics([
        _f("营业收入", "FY2024", 110.0, unit="万元"), _f("营业收入", "FY2023", 0.01, unit="亿元"),
    ])
    m = _get(ms, "营业收入增长率")
    assert m["status"] == "ok" and abs(m["value"] - 0.1) < 1e-9


def test_growth_missing_prev_and_zero_denominator():
    ms = compute_derived_metrics([
        _f("营业收入", "FY2024", 110.0),                        # 无 FY2023
        _f("净利润", "FY2024", 5.0), _f("净利润", "FY2023", 0.0),  # 上年为零
    ])
    assert _get(ms, "营业收入增长率")["status"] == "skipped_missing_inputs"
    assert _get(ms, "净利润增长率")["status"] == "skipped_zero_denominator"


def test_growth_unit_incomparable_skipped():
    """单位未知（空）vs 元 → 不可比，跳过不硬算"""
    ms = compute_derived_metrics([
        _f("营业收入", "FY2024", 110.0, unit=""), _f("营业收入", "FY2023", 100.0, unit="元"),
    ])
    assert _get(ms, "营业收入增长率")["status"] == "skipped_unit_unknown"


def test_growth_direct_exists_not_recomputed():
    """年报已直接披露增长率（pct fact）→ 不重复计算（防双源数字打架）"""
    direct = _f("营业收入增长率", "FY2024", 4.75)
    direct["is_pct"] = True
    ms = compute_derived_metrics([
        _f("营业收入", "FY2024", 110.0), _f("营业收入", "FY2023", 100.0), direct,
    ])
    assert _get(ms, "营业收入增长率")["status"] == "skipped_direct_exists"


def test_ratios_profitability_and_solvency():
    ms = compute_derived_metrics([
        _f("营业收入", "FY2024", 110.0),
        _f("归母净利润", "FY2024", 22.0),
        _f("负债合计", "FY2024", 60.0), _f("资产总计", "FY2024", 100.0),
        _f("流动资产合计", "FY2024", 70.0), _f("流动负债合计", "FY2024", 50.0),
        _f("存货", "FY2024", 20.0),
    ])
    assert _get(ms, "净利率")["display"] == "20.00%"      # 22/110
    assert _get(ms, "资产负债率")["display"] == "60.00%"  # 60/100
    assert _get(ms, "流动比率")["display"] == "1.40"      # 70/50
    assert _get(ms, "速动比率")["display"] == "1.00"      # (70-20)/50
    assert all(m["status"] == "ok" for m in ms if m["label"] in ("净利率", "资产负债率", "流动比率", "速动比率"))


def test_pct_and_low_confidence_facts_excluded_as_inputs():
    """is_pct / confidence<0.9 事实不进算术索引（防止拿比率当绝对值算）"""
    pct = _f("营业收入", "FY2024", 110.0)
    pct["is_pct"] = True
    low = _f("营业收入", "FY2023", 100.0)
    low["confidence"] = 0.6
    ms = compute_derived_metrics([pct, low])
    assert _get(ms, "营业收入增长率")["status"] == "skipped_missing_inputs"


def test_render_only_ok_items_as_table():
    ms = compute_derived_metrics([
        _f("营业收入", "FY2024", 110.0), _f("营业收入", "FY2023", 100.0),
    ])
    text = render_derived_metrics(ms)
    assert "营业收入增长率" in text and "+10.00%" in text and "p8" in text
    assert render_derived_metrics([m for m in ms if m["status"] != "ok"]) == ""
