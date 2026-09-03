"""P5-E4 测试: Writer 卖方模板 + 派生指标表/跨源告警注入"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from graphs.state import create_initial_state


def _fact(metric, period, value):
    return {"company": "富瑞特装", "metric": metric, "metric_std": metric, "period": period,
            "value": value, "raw": f"{value:,}", "is_pct": False, "is_subtotal": False,
            "unit": "元", "confidence": 0.9,
            "source": {"page_idx": 8, "table_id": "t_001"}}


def _state_for_writer(with_derived=True, with_cross=True):
    st = create_initial_state("撰写富瑞特装年报分析", pdf_path="")
    st["pdf_l1"] = {"sections": [{"section_id": "s_001", "title": "第三节 管理层讨论与分析",
                                  "tier": "T1", "text": "经营分析。", "page_range": [10],
                                  "table_ids": []}],
                    "tables": [], "facts": []}
    st["extracted_entities"] = [{"entity_type": "company", "entity_name": "富瑞特装"}]
    st["analysis_result"] = "## 综合分析\n结论 [P 10]"
    if with_derived:
        from extractors.derived_metrics import compute_derived_metrics
        st["derived_metrics"] = compute_derived_metrics([
            _fact("营业收入", "FY2024", 110.0), _fact("营业收入", "FY2023", 100.0),
        ])
    if with_cross:
        st["cross_source_checks"] = [{
            "metric": "营业收入", "status": "mismatch",
            "mismatches": [{"prose_value": "30.50亿元", "prose_src": "p45（s_100）",
                            "table_values": ["110"], "rel_diff": 0.7}],
        }]
    return st


def _capture_prompt(monkeypatch):
    import agents.report_writer as wr
    captured = {}

    def fake_with_tools(system_prompt, user_msg, tools, max_rounds=5, on_tool_call=None):
        captured["prompt"] = system_prompt
        return {"error": False, "content": "# 富瑞特装投资分析报告\n结论 [P 8]",
                "tool_calls": [], "rounds": 1}

    monkeypatch.setattr(wr, "is_llm_ready", lambda: True)
    monkeypatch.setattr(wr, "safe_invoke_with_tools", fake_with_tools)
    return captured


def test_e4_sellside_structure_and_injections(monkeypatch):
    captured = _capture_prompt(monkeypatch)
    import agents.report_writer as wr
    from utils.config import get_settings

    monkeypatch.setattr(get_settings(), "USE_MULTILEVEL_COMPRESSION", True)
    out = wr.report_writer_node(_state_for_writer())
    prompt = captured["prompt"]
    assert "投资要点" in prompt and "论点前置" in prompt, "卖方模板结构"
    assert "派生指标铁律" in prompt
    assert "营业收入增长率" in prompt and "+10.00%" in prompt, "派生指标表注入"
    assert "跨源核对告警" in prompt and "30.50亿元" in prompt, "跨源告警注入"
    assert out["final_report"], "报告正常产出"


def test_e4_no_injection_when_empty(monkeypatch):
    """无派生指标/无告警 → prompt 不含对应段（空段不注入）"""
    captured = _capture_prompt(monkeypatch)
    import agents.report_writer as wr
    from utils.config import get_settings

    monkeypatch.setattr(get_settings(), "USE_MULTILEVEL_COMPRESSION", True)
    wr.report_writer_node(_state_for_writer(with_derived=False, with_cross=False))
    assert "派生指标铁律" not in captured["prompt"]
    assert "跨源核对告警" not in captured["prompt"]


def test_e4_old_path_unchanged(monkeypatch):
    """无 PDF（旧路径）保持六大章节，不注入派生指标表"""
    import agents.report_writer as wr
    from utils.config import get_settings

    monkeypatch.setattr(get_settings(), "USE_MULTILEVEL_COMPRESSION", True)
    captured = {}

    def fake_invoke(system_prompt, user_msg):
        captured["prompt"] = system_prompt
        return {"error": False, "content": "# 报告\n内容"}

    monkeypatch.setattr(wr, "is_llm_ready", lambda: True)
    monkeypatch.setattr(wr, "safe_invoke", fake_invoke)

    st = create_initial_state("查询")
    st["analysis_result"] = "分析"
    wr.report_writer_node(st)
    assert "核心结论与投资摘要" in captured["prompt"], "旧路径六大章节不变"
    assert "派生指标铁律" not in captured["prompt"], "旧路径不读 P5 字段"