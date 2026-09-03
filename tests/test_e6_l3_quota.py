"""P5-E6 测试: L3 亮点按指标类型分桶配额（修「资产总计挤掉 EPS/ROE」大数霸屏）"""

from extractors.section_compressor import _bucket_of, build_global_summary_lite


def _f(metric, value, raw, is_pct=False, period="FY2024"):
    return {"company": "富瑞特装", "metric": metric, "metric_std": metric, "period": period,
            "value": value, "raw": raw, "is_pct": is_pct, "is_subtotal": False,
            "unit": "", "confidence": 0.9, "source": {"page_idx": 8, "table_id": "t_001"}}


def test_e6_bucket_of_classification():
    assert _bucket_of(_f("基本每股收益", 0.38, "0.3804")) == "ratio"
    assert _bucket_of(_f("净资产收益率", 10.77, "10.77%", is_pct=True)) == "ratio"
    assert _bucket_of(_f("资产负债率", 60.0, "60%")) == "ratio"      # 以「率」结尾
    assert _bucket_of(_f("归母净利润", 2.2e8, "219,215,799.89")) == "profit"
    assert _bucket_of(_f("营业收入", 3.3e9, "3,322,399,262.33")) == "income"
    assert _bucket_of(_f("资产总计", 2.4e9, "2,400,000,000")) == "balance"


def test_e6_quota_keeps_small_high_signal_metrics():
    """旧逻辑 |value| 前 6 会把 EPS/ROE 挤掉；配额后各桶均衡、EPS/ROE 必入选"""
    l1 = {"facts": [
        _f("资产总计", 2.4e9, "2,400,000,000"),        # balance
        _f("负债合计", 1.4e9, "1,400,000,000"),        # balance
        _f("流动资产合计", 1.2e9, "1,200,000,000"),     # balance（第 3 大数）
        _f("流动负债合计", 8e8, "800,000,000"),         # balance（第 4 大数）
        _f("营业收入", 3.3e9, "3,322,399,262.33"),     # income
        _f("归母净利润", 2.2e8, "219,215,799.89"),     # profit
        _f("基本每股收益", 0.38, "0.3804"),             # ratio（小数，旧逻辑必被挤掉）
        _f("净资产收益率", 10.77, "10.77%", is_pct=True),  # ratio
    ]}
    text = build_global_summary_lite(l1, company="富瑞特装")
    # ratio 桶 2 席: EPS 与 ROE 都在
    assert "0.3804" in text and "10.77" in text, "高信号小指标必须入选"
    # balance 桶 2 席: 第 3/4 大数被配额截掉
    assert "流动资产合计" not in text and "流动负债合计" not in text
    # 确定性
    assert text == build_global_summary_lite(l1, company="富瑞特装")