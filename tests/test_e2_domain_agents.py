"""P5-E2 测试: 领域 agent 组 + Synthesizer + Analyst 门控"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from graphs.state import create_initial_state


def _state_with_domains():
    st = create_initial_state("分析富瑞特装", pdf_path="")
    st["pdf_l1"] = {
        "sections": [{"section_id": "s_001", "title": "第三节 管理层讨论与分析",
                      "tier": "T1", "text": "经营分析。", "page_range": [10], "table_ids": []}],
        "tables": [], "facts": [],
    }
    st["domain_contexts"] = {
        "overview": "## 概览上下文\n公司主营特种装备…",
        "operating": "## 经营上下文\nMD&A 原文…",
        "financial": "## 财务上下文\n利润表…",
    }
    st["extracted_entities"] = [{"entity_type": "company", "entity_name": "富瑞特装"}]
    return st


def _patch_llm(monkeypatch, responses=None):
    """统一 mock domain_analysts 的 LLM 面（is_llm_ready / safe_invoke_with_tools / safe_invoke）"""
    import agents.domain_analysts as da
    from extractors.chapter_tagger import DOMAINS
    name2key = {d["name"]: d["key"] for d in DOMAINS}
    calls = []

    def fake_with_tools(system_prompt, user_msg, tools, max_rounds=5, on_tool_call=None):
        key = next((k for nm, k in name2key.items() if nm in system_prompt), "?")
        calls.append(key)
        if responses and key in responses:
            return responses[key]()
        return {"error": False, "content": f"[{key} 领域结论] 营收增长 [P 10]",
                "tool_calls": [], "rounds": 1}

    monkeypatch.setattr(da, "is_llm_ready", lambda: True)
    monkeypatch.setattr(da, "safe_invoke_with_tools", fake_with_tools)
    monkeypatch.setattr(da, "safe_invoke",
                        lambda sp, um: {"error": False, "content": "## 综合结论\n已合并", "tool_calls": []})
    return da, calls


def test_e2_domain_agents_run_all(monkeypatch):
    da, calls = _patch_llm(monkeypatch)
    out = da.run_domain_agents(_state_with_domains(), {}, [])
    assert set(out["analyses"]) == {"overview", "operating", "financial"}
    assert sorted(calls) == ["financial", "operating", "overview"]
    assert all("[P 10]" in t for t in out["analyses"].values()), "领域产出必须带页码引用"


def test_e2_domain_failure_isolated(monkeypatch):
    """单领域炸掉不阻断其余"""
    def boom():
        raise RuntimeError("boom")

    da, _ = _patch_llm(monkeypatch, responses={"operating": boom})
    error_log = []
    out = da.run_domain_agents(_state_with_domains(), {}, error_log)
    assert set(out["analyses"]) == {"overview", "financial"}
    assert any("operating" in e for e in error_log)


def test_e2_synthesize_llm_and_fallback(monkeypatch):
    da, _ = _patch_llm(monkeypatch)
    st = _state_with_domains()
    analyses = {"operating": "经营结论A", "financial": "财务结论B"}

    text = da.synthesize_analyses(analyses, st)
    assert "## 综合结论" in text and "已合并" in text, "LLM 可用走合并"

    monkeypatch.setattr(da, "is_llm_ready", lambda: False)
    text2 = da.synthesize_analyses(analyses, st)
    assert "经营结论A" in text2 and "财务结论B" in text2, "兜底拼接不丢内容"
    assert "拼接" in text2 and "经营结论A" in text2.split("财务结论B")[0], "固定领域序"


def test_e2_analyst_domain_mode_routing(monkeypatch):
    """domain_contexts 非空 + flag 开 → Analyst 走领域模式（产出 = Synthesizer 结果）"""
    import agents.financial_analyst as fa
    import agents.domain_analysts as da
    from utils.config import get_settings

    monkeypatch.setattr(get_settings(), "USE_MULTILEVEL_COMPRESSION", True)
    monkeypatch.setattr(get_settings(), "USE_DOMAIN_AGENTS", True)
    monkeypatch.setattr(da, "run_domain_agents",
                        lambda st, a, e: {"analyses": {"operating": "经营结论"}, "tool_history": []})
    monkeypatch.setattr(da, "synthesize_analyses", lambda ans, st: "## 综合分析\n经营结论")

    out = fa.financial_analyst_node(_state_with_domains())
    assert out["analysis_result"] == "## 综合分析\n经营结论"
    assert out["domain_analyses"] == {"operating": "经营结论"}, "领域产出写回 state（修订回炉缓存）"


def test_e2_analyst_fallback_when_no_domain_contexts(monkeypatch):
    """domain_contexts 空（joinn/flag 关）→ 回退全局装配路径"""
    import agents.financial_analyst as fa
    from utils.config import get_settings

    monkeypatch.setattr(get_settings(), "USE_MULTILEVEL_COMPRESSION", True)
    monkeypatch.setattr(fa, "is_llm_ready", lambda: True)
    monkeypatch.setattr(fa, "safe_invoke_with_tools",
                        lambda *a, **kw: {"error": False, "content": "## 公司基本面\n全局路径 [P 0]",
                                          "tool_calls": [], "rounds": 1})
    st = _state_with_domains()
    st["domain_contexts"] = {}
    out = fa.financial_analyst_node(st)
    assert "全局路径" in out["analysis_result"]


def test_e2_revision_reruns_only_defect_domain(monkeypatch):
    """defect_domain=operating → 只回炉 operating，其余从 state.domain_analyses 携带复用"""
    da, calls = _patch_llm(monkeypatch)
    st = _state_with_domains()
    st["domain_analyses"] = {"overview": "旧概览", "operating": "旧经营", "financial": "旧财务"}
    st["defect_domain"] = "operating"
    st["review_feedback"] = "经营分析缺少毛利率讨论"
    st["prev_analysis_result"] = "上一版综合"

    out = da.run_domain_agents(st, {}, [])
    assert calls == ["operating"], "只回炉 operating"
    assert out["analyses"]["overview"] == "旧概览" and out["analyses"]["financial"] == "旧财务"
    assert "领域结论" in out["analyses"]["operating"], "operating 必须重跑出新产出"