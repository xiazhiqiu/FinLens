"""P5-E7 测试: Reviewer defect_domain 判定 + 跨源告警消费"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from graphs.state import create_initial_state

_CROSS_MISMATCH = [{
    "metric": "营业收入", "status": "mismatch", "n_prose_hits": 1, "n_consistent": 0,
    "n_table_facts": 1,
    "mismatches": [{"prose_value": "30.50亿元", "prose_src": "p45（s_100）",
                    "table_values": ["3,322,399,262.33"], "rel_diff": 0.08}],
}]


def test_e7_schema_has_defect_domain():
    from agents.reviewer import ReviewVerdict
    fields = ReviewVerdict.model_fields
    assert "defect_domain" in fields
    assert fields["defect_domain"].default == ""


def test_e7_review_input_includes_cross_warnings():
    from agents.reviewer import _build_review_input
    st = create_initial_state("q")
    st["final_report"] = "# 报告"
    st["cross_source_checks"] = _CROSS_MISMATCH
    text = _build_review_input(st)
    assert "跨源核对告警" in text and "营业收入" in text and "30.50亿元" in text


def test_e7_reviewer_returns_defect_domain(monkeypatch):
    import agents.reviewer as rev

    monkeypatch.setattr(rev, "is_llm_ready", lambda: True)
    verdict_json = ('{"verdict": "revise", "defect_locus": "analysis", "defect_domain": "operating", '
                    '"issues_found": ["经营分析缺少毛利率"], "feedback": "补充毛利率"}')
    monkeypatch.setattr(rev, "safe_invoke",
                        lambda sp, um: {"error": False, "content": verdict_json, "tool_calls": []})

    st = create_initial_state("q")
    st["final_report"] = "# 富瑞特装投资分析报告\n营业收入增长 [P 8]"
    out = rev.reviewer_node(st)
    assert out["review_result"] == "revise"
    assert out["defect_domain"] == "operating", "缺陷领域必须随判定透传"


def test_e7_defect_domain_empty_on_degrade(monkeypatch):
    """输出不可解析且 schema 重试失败 → 降级 pass，defect_domain 必须为空"""
    import agents.reviewer as rev

    monkeypatch.setattr(rev, "is_llm_ready", lambda: True)
    monkeypatch.setattr(rev, "safe_invoke",
                        lambda sp, um: {"error": False, "content": "不是 JSON", "tool_calls": []})
    monkeypatch.setattr(rev, "_schema_retry", lambda sp, um: None)

    st = create_initial_state("q")
    st["final_report"] = "# 报告\n结论 [P 8]"
    out = rev.reviewer_node(st)
    assert out["review_result"] == "pass"
    assert out["defect_domain"] == ""