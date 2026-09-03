"""
修订闭环回归测试

覆盖四个关键机制:
1. Reviewer revise 时备份上一版分析/报告（供下游增量修订）
2. CRITICAL 合规违规不可被 LLM 判 pass 覆盖（强制 revise）
3. Supervisor 规则回退下的 revise 路由状态机（analyst → writer → reviewer）
4. 终态闸门：敏感信息脱敏 + CRITICAL 告警横幅
"""

import sys
import os
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from graphs.state import create_initial_state


def _fake_llm_response(content: str) -> dict:
    return {"error": False, "content": content, "model": "fake", "finish_reason": "stop", "usage": {}}


def test_reviewer_revise_backs_up_previous_versions():
    """revise 时必须备份当前 analysis_result / final_report，否则下游无从修订"""
    import agents.reviewer as reviewer

    state = create_initial_state("分析复星医药")
    state["analysis_result"] = "## 上一版分析结论\n营收增长 20.18 亿元 [P 102]"
    state["final_report"] = "# 报告 v1\n## 六、投资建议（仅供参考）\n> 免责声明: 测试"

    llm_json = (
        '{"verdict": "revise", "defect_locus": "report", '
        '"issues_found": ["缺少数据来源标注"], '
        '"feedback": "请在财务数据后标注来源"}'
    )
    reviewer.is_llm_ready = lambda: True
    reviewer.safe_invoke = lambda sp, um, **kw: _fake_llm_response(llm_json)

    result = reviewer.reviewer_node(state)

    assert result["review_result"] == "revise"
    assert result["defect_locus"] == "report"
    assert result["prev_final_report"] == state["final_report"]
    assert result["prev_analysis_result"] == state["analysis_result"]
    assert result["review_revision_count"] == 1
    assert "compliance_violations" in result
    print("PASS: reviewer revise 备份机制")


def test_critical_violation_overrides_llm_pass():
    """CRITICAL 违规（承诺收益）不可被 LLM 判 pass 静默放行"""
    import agents.reviewer as reviewer

    state = create_initial_state("分析某股票")
    state["analysis_result"] = "分析内容"
    # "保证...收益" 命中 CSRC-001 (CRITICAL)
    state["final_report"] = "# 报告\n该公司保证收益稳定，值得投资。"

    # LLM 错误地判 pass → 必须被规则覆盖为 revise
    llm_json = '{"verdict": "pass", "defect_locus": "report", "issues_found": [], "feedback": ""}'
    reviewer.is_llm_ready = lambda: True
    reviewer.safe_invoke = lambda sp, um, **kw: _fake_llm_response(llm_json)

    result = reviewer.reviewer_node(state)

    assert result["review_result"] == "revise", "CRITICAL 违规必须强制 revise"
    assert result["defect_locus"] == "report"
    assert "CSRC-001" in result["review_feedback"]
    assert len(result["compliance_violations"]) > 0
    print("PASS: CRITICAL 违规强制 revise")


def test_supervisor_fallback_revise_routing():
    """无 LLM 时 revise 按缺陷归属路由: analyst → writer → reviewer 状态机"""
    import agents.supervisor as supervisor

    supervisor.is_llm_ready = lambda: False

    base_history = ["report_extractor", "data_retriever", "financial_analyst", "report_writer", "reviewer"]

    def _state(locus, history):
        s = create_initial_state("测试")
        s["review_result"] = "revise"
        s["defect_locus"] = locus
        s["agent_call_history"] = list(history)
        s["agent_status"] = {k: "done" for k in s["agent_status"]}
        s["iteration_count"] = 6
        return s

    # 1) 报告层缺陷 → 只返工 report_writer
    r = supervisor.supervisor_node(_state("report", base_history))
    assert r["next_agent"] == "report_writer", f"期望 report_writer, 实际 {r['next_agent']}"

    # 2) 分析层缺陷 → 先返工 financial_analyst
    r = supervisor.supervisor_node(_state("analysis", base_history))
    assert r["next_agent"] == "financial_analyst", f"期望 financial_analyst, 实际 {r['next_agent']}"

    # 3) 分析层缺陷、analyst 已重跑 → 接着返工 report_writer
    r = supervisor.supervisor_node(_state("analysis", base_history + ["financial_analyst"]))
    assert r["next_agent"] == "report_writer", f"期望 report_writer, 实际 {r['next_agent']}"

    # 4) writer 已重跑 → 回 reviewer 重新审查
    r = supervisor.supervisor_node(_state("analysis", base_history + ["financial_analyst", "report_writer"]))
    assert r["next_agent"] == "reviewer", f"期望 reviewer, 实际 {r['next_agent']}"
    print("PASS: supervisor revise 路由状态机 (report/analysis/续跑/重审)")


def test_terminal_gate_masks_and_flags():
    """终态闸门: 手机号脱敏 + CRITICAL 违规插入显式告警横幅"""
    from graphs.financial_graph import FinancialAnalysisGraph

    tmp_db = os.path.join(tempfile.mkdtemp(prefix="finscope_test_"), "test.db")
    graph = FinancialAnalysisGraph(sqlite_path=tmp_db)

    report = "联系方式: 13812345678。该公司保证收益稳定。"
    gated, violations = graph._terminal_gate(report)

    assert "13812345678" not in gated, "手机号必须被脱敏"
    assert "138****" in gated
    assert len(violations) > 0, "必须产出违规记录"
    assert any(v.get("severity") == "critical" for v in violations), "承诺收益应为 CRITICAL"
    assert "CRITICAL" in gated and "合规告警" in gated, "必须有显式告警横幅"
    print("PASS: 终态闸门脱敏 + CRITICAL 横幅")


def test_stream_path_applies_gate():
    """stream() 路径必须对 report_writer 产出执行终态闸门（修复 UI 主路径绕过）"""
    import inspect
    from graphs.financial_graph import FinancialAnalysisGraph

    src = inspect.getsource(FinancialAnalysisGraph.stream)
    assert "_terminal_gate" in src, "stream() 必须调用终态闸门"
    assert '"report_writer" in chunk' in src
    print("PASS: stream() 已接入终态闸门")


if __name__ == "__main__":
    test_reviewer_revise_backs_up_previous_versions()
    test_critical_violation_overrides_llm_pass()
    test_supervisor_fallback_revise_routing()
    test_terminal_gate_masks_and_flags()
    test_stream_path_applies_gate()
    print("\n全部通过 ✓")
