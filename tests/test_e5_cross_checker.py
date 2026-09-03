"""P5-E5 测试: facts 表 ↔ MD&A 散文数字跨源对账（确定性，零 LLM）"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from extractors.cross_checker import cross_check_prose_vs_facts, render_cross_warnings


def _tf(metric, period, value, unit="元", page=8):
    return {"company": "富瑞特装", "metric": metric, "metric_std": metric, "period": period,
            "value": value, "raw": f"{value:,}", "is_pct": False, "is_subtotal": False,
            "unit": unit, "confidence": 0.9,
            "source": {"page_idx": page, "table_id": "t_001"}}


def _l1(prose, facts):
    return {
        "sections": [{"section_id": "s_100", "title": "管理层讨论与分析",
                      "tier": "T1", "text": prose, "page_range": [45], "table_ids": []}],
        "tables": [], "facts": facts,
    }


CMAP = {"s_100": 3}  # 第三节 MD&A


def test_consistent_prose_vs_table():
    l1 = _l1("报告期内，公司实现营业收入 33.22 亿元，同比增长 4.76%。",
             [_tf("营业收入", "FY2024", 3.322e9)])
    checks = cross_check_prose_vs_facts(l1, CMAP)
    c = next(x for x in checks if x["metric"] == "营业收入")
    assert c["status"] == "consistent" and c["n_prose_hits"] == 1


def test_match_any_period_avoids_false_mismatch():
    """散文提到上年数字（FY2023）→ 与任一期间命中即 consistent"""
    l1 = _l1("2023年公司营业收入 31.71 亿元，2024年增长至 33.22 亿元。",
             [_tf("营业收入", "FY2024", 3.322e9), _tf("营业收入", "FY2023", 3.171e9)])
    c = next(x for x in cross_check_prose_vs_facts(l1, CMAP) if x["metric"] == "营业收入")
    assert c["status"] == "consistent" and c["n_prose_hits"] == 2


def test_mismatch_reported_with_detail():
    l1 = _l1("报告期内，公司实现营业收入 30.50 亿元。",
             [_tf("营业收入", "FY2024", 3.322e9)])
    c = next(x for x in cross_check_prose_vs_facts(l1, CMAP) if x["metric"] == "营业收入")
    assert c["status"] == "mismatch"
    mm = c["mismatches"][0]
    assert "30.50亿元" in mm["prose_value"] and mm["prose_src"].startswith("p45")
    assert mm["rel_diff"] > 0.02


def test_no_prose_and_non_mdna_sections_ignored():
    l1 = {
        "sections": [
            {"section_id": "s_100", "title": "管理层讨论与分析", "tier": "T1",
             "text": "经营平稳。", "page_range": [45], "table_ids": []},
            {"section_id": "s_200", "title": "公司简介", "tier": "T1",
             "text": "营业收入 999 亿元。", "page_range": [5], "table_ids": []},  # 非第三节，不扫
        ],
        "tables": [], "facts": [_tf("营业收入", "FY2024", 3.322e9), _tf("资产总计", "FY2024", 1e10)],
    }
    checks = cross_check_prose_vs_facts(l1, {"s_100": 3, "s_200": 1})
    rev = {c["metric"]: c for c in checks}
    assert rev["营业收入"]["status"] == "no_prose"      # MD&A 内无散文数字
    assert rev["资产总计"]["status"] == "no_prose"


def test_pct_and_text_facts_not_compared():
    """pct 事实 / 非表格来源事实（文本级 0.6）不参与表格侧对账"""
    pct = _tf("营业收入", "FY2024", 33.22)
    pct["is_pct"] = True
    txt = _tf("营业收入", "FY2024", 3.322e9)
    txt["confidence"] = 0.6
    txt["source"] = {"page_idx": 8}
    l1 = _l1("公司实现营业收入 33.22 亿元。", [pct, txt])
    assert cross_check_prose_vs_facts(l1, CMAP) == []  # 无合格表格事实 → 不产出该科目检查


def test_render_cross_warnings_only_mismatch():
    checks = [
        {"metric": "营业收入", "status": "consistent", "mismatches": []},
        {"metric": "归母净利润", "status": "mismatch", "mismatches": [
            {"prose_value": "2.5亿元", "prose_src": "p45（s_100）",
             "table_values": ["219,215,799.89"], "rel_diff": 0.14}]},
    ]
    text = render_cross_warnings(checks)
    assert "跨源核对告警" in text and "归母净利润" in text and "2.5亿元" in text
    assert render_cross_warnings(checks[:1]) == ""