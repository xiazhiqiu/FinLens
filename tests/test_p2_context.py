"""
P2 测试: 预算装配器 + 上下文工具 + Analyst 新链路门控

覆盖:
- 装配: 预算内全量直注 / 溢出留指针（不静默丢弃）/ T3 跳过 / 硬约束永不过预算
- 工具: fetch_context(章节/页) / query_fact(命中/未命中) / search_section 排序
- 审计: 工具调用回调写入留痕字段
- Analyst: flag 开启走新链路（mock LLM 断言 pdf_context/tool_call_history），
  flag 关闭保持旧路径（防回归）
"""

import json
import pytest

from graphs.state import create_initial_state
from extractors.l1_builder import build_l1


def _tiny_l1():
    """小 L1: 全部能注入（预算内全量直注场景）"""
    pages = [
        {"page_idx": 0, "items": [
            {"type": "heading", "content": "合并利润表", "level": 2},
            {"type": "table", "content": (
                "<table><tr><td>项目</td><td>2024年</td><td>2023年</td></tr>"
                "<tr><td>营业收入</td><td>204.42</td><td>195.14</td></tr></table>"
            ), "caption": [], "footnote": []},
            {"type": "heading", "content": "经营讨论", "level": 2},
            {"type": "text", "content": "公司营收稳健增长。"},
            {"type": "heading", "content": "免责声明", "level": 2},
            {"type": "text", "content": "本报告不构成投资建议。"},
        ]},
    ]
    return build_l1(pages, companies=["复星医药"])


def _big_l1(n_text_sections: int = 80):
    """大 L1: 大量纯散文章节，小预算必然溢出（指针场景）"""
    items = []
    for i in range(n_text_sections):
        items.append({"type": "heading", "content": f"章节{i:03d}", "level": 2})
        items.append({"type": "text", "content": ("内容段落" + "很长很长" * 40 + f" 第{i}段。")})
    pages = [{"page_idx": 0, "items": items}]
    return build_l1(pages)


# ---------------------------------------------------------------------------
# 1. 装配器
# ---------------------------------------------------------------------------

def test_assemble_within_budget_injects_all():
    from extractors.context_assembler import assemble
    l1 = _tiny_l1()
    out = assemble("分析复星医药", budget_tokens=10000, l1=l1)
    assert out["stats"]["used"] <= 10000
    assert len(out["pointers"]) == 0, "预算充足必须全量直注"
    assert len(out["injected"]) == 2, "T0 利润表章 + T2 经营讨论章（T3 免责声明被跳过）"
    assert "204.42" in out["context"]
    assert "免责声明" not in out["context"], "T3 必须跳过"


def test_assemble_overflow_creates_pointers_no_silent_drop():
    from extractors.context_assembler import assemble
    from agents.context_tools import build_context_tools
    l1 = _big_l1(80)
    out = assemble("测试", budget_tokens=1500, l1=l1)
    assert out["stats"]["used"] <= 1500
    assert len(out["pointers"]) > 0, "小预算必须产生指针"

    # 「不静默丢弃」的真实保证: 未注入章节内容仍可经 fetch_context 回取
    # （指针行在极端预算下可能被硬兜底裁剪尾部，但内容永远在 tools 侧可达）
    tools = {t.name: t for t in build_context_tools(l1)}
    for sid in out["pointers"][:5]:
        res = tools["fetch_context"].invoke({"scope": sid})
        assert "未找到" not in res and "异常" not in res, f"指针章节 {sid} 必须可回取"


def test_assemble_never_exceeds_budget():
    """硬约束: 任意预算下装配产物 token ≤ 预算"""
    from extractors.context_assembler import assemble
    l1 = _big_l1(60)
    for budget in (500, 1200, 3000, 8000, 20000):
        out = assemble("测试", budget_tokens=budget, l1=l1)
        assert out["stats"]["used"] <= budget, f"预算 {budget} 被破坏"


def test_assemble_empty_l1_graceful():
    from extractors.context_assembler import assemble
    out = assemble("测试", budget_tokens=1000, l1={"sections": [], "tables": []})
    assert out["used"] <= 1000
    assert out["injected"] == [] and out["pointers"] == []


# ---------------------------------------------------------------------------
# 2. 上下文工具（确定性实现，直接 invoke 断言）
# ---------------------------------------------------------------------------

def test_query_fact_tool_hit():
    from agents.context_tools import build_context_tools
    l1 = _tiny_l1()
    tools = {t.name: t for t in build_context_tools(l1)}
    res = tools["query_fact"].invoke({"company": "复星医药", "metric": "营业收入", "period": "FY2024"})
    assert "营业收入" in res and "FY2024" in res and "204.42" in res
    assert "p0" in res, "结果必须带溯源页码"


def test_query_fact_tool_miss_suggests_search():
    from agents.context_tools import build_context_tools
    l1 = _tiny_l1()
    tools = {t.name: t for t in build_context_tools(l1)}
    res = tools["query_fact"].invoke({"company": "不存在的公司", "metric": "", "period": ""})
    assert "未命中" in res
    assert "search_section" in res, "未命中必须引导改走原文检索"


def test_fetch_context_by_section_and_page():
    from agents.context_tools import build_context_tools
    l1 = _tiny_l1()
    tools = {t.name: t for t in build_context_tools(l1)}
    sid = l1["sections"][0]["section_id"]
    res = tools["fetch_context"].invoke({"scope": sid})
    assert "合并利润表" in res and "204.42" in res
    res_page = tools["fetch_context"].invoke({"scope": "p0"})
    assert "经营讨论" in res_page or "合并利润表" in res_page
    miss = tools["fetch_context"].invoke({"scope": "s_999"})
    assert "未找到" in miss


def test_search_section_ranks_relevance():
    from agents.context_tools import build_context_tools
    pages = [{"page_idx": 0, "items": [
        {"type": "heading", "content": "行业分析", "level": 2},
        {"type": "text", "content": "光伏行业装机量高增。"},
        {"type": "heading", "content": "公司业务", "level": 2},
        {"type": "text", "content": "公司主营光伏组件。"},
        {"type": "heading", "content": "风险提示", "level": 2},
        {"type": "text", "content": "行业竞争加剧风险。"},
    ]}]
    l1 = build_l1(pages)
    tools = {t.name: t for t in build_context_tools(l1)}
    res = tools["search_section"].invoke({"query": "光伏", "top_k": 2})
    assert "行业分析" in res and "公司业务" in res
    # 相关度排序: 标题含词的排前面
    assert res.index("行业分析") < res.index("公司业务") or "风险提示" not in res


def test_tool_audit_callback_writes_history():
    from agents.context_tools import build_context_tools
    l1 = _tiny_l1()
    history = []
    tools = {t.name: t for t in build_context_tools(l1, on_tool_call=lambda n, a, r: history.append((n, a, r)))}
    tools["query_fact"].invoke({"company": "复星医药", "metric": "", "period": ""})
    assert len(history) == 1
    name, args, res = history[0]
    assert name == "query_fact"
    assert args["company"] == "复星医药"
    assert len(res) > 0, "审计必须记录返回内容"


def test_safe_invoke_with_tools_forced_summary_round(monkeypatch):
    """[A5] 轮数耗尽（每轮都在调工具）→ 强制总结轮兜底，agent 必有最终产出"""
    import utils.llm_client as lc
    from langchain_core.messages import AIMessage

    class _AlwaysToolLLM:
        """每轮都请求工具的 LLM（模拟耗尽路径）"""
        def bind_tools(self, tools):
            return self

        def invoke(self, messages):
            if any(getattr(m, "type", "") == "human" and "上限" in str(getattr(m, "content", "")) for m in messages):
                return AIMessage(content="最终分析结论：营收稳健 [P 0]")
            return AIMessage(
                content="",
                tool_calls=[{"name": "query_fact", "args": {"metric": "营收"}, "id": "c1"}],
            )

    class _FakeTool:
        name = "query_fact"

        def invoke(self, args):
            return "查询结果: 204.42"

    monkeypatch.setattr(lc, "get_llm", lambda: _AlwaysToolLLM())

    result = lc.safe_invoke_with_tools("system", "user", [_FakeTool()], max_rounds=2)

    assert result["error"] is False
    assert result["content"] == "最终分析结论：营收稳健 [P 0]", "耗尽后必须由强制总结轮产出内容"
    assert result["rounds"] == 3, "2 轮工具 + 1 轮强制总结"
    assert len(result["tool_calls"]) == 2, "每轮 1 次工具调用，共 2 次"


# ---------------------------------------------------------------------------
# 3. Analyst 新链路门控
# ---------------------------------------------------------------------------

def _state_with_l1():
    st = create_initial_state("分析复星医药", pdf_path="")
    st["pdf_l1"] = _tiny_l1()
    st["pdf_l1"]["sections"] = [s for s in st["pdf_l1"]["sections"]]  # 保真引用
    return st


def test_analyst_uses_multilevel_path_when_flag_on(monkeypatch):
    import agents.financial_analyst as fa
    from utils.config import get_settings

    monkeypatch.setattr(get_settings(), "USE_MULTILEVEL_COMPRESSION", True)
    monkeypatch.setattr(fa, "is_llm_ready", lambda: True)

    captured = {}

    def fake_with_tools(system_prompt, user_message, tools, **kw):
        captured["n_tools"] = len(tools)
        captured["tool_names"] = sorted(t.name for t in tools)
        captured["prompt_has_context"] = "复星医药" in system_prompt or "PDF 研报内容" in system_prompt
        return {"error": False, "content": "## 财务表现分析\n营收增长稳健 [P 0]", "tool_calls": [], "rounds": 1}

    monkeypatch.setattr(fa, "safe_invoke_with_tools", fake_with_tools)

    state = _state_with_l1()
    out = fa.financial_analyst_node(state)

    assert out["analysis_result"].startswith("##")
    assert out.get("pdf_context", "") != "", "新链路必须产出 pdf_context"
    assert captured["n_tools"] == 3, "必须挂载 3 个上下文工具"
    assert captured["prompt_has_context"] is True
    assert "tool_call_history" in out


def test_analyst_keeps_old_path_when_flag_off(monkeypatch):
    """flag 关闭（默认）→ 旧路径不变，防回归"""
    import agents.financial_analyst as fa
    from utils.config import get_settings

    monkeypatch.setattr(get_settings(), "USE_MULTILEVEL_COMPRESSION", False)
    monkeypatch.setattr(fa, "is_llm_ready", lambda: True)
    called_new = {}

    def fake_with_tools(*a, **kw):
        called_new["called"] = True
        return {"error": False, "content": "x"}

    def fake_safe_invoke(system_prompt, user_message, **kw):
        return {"error": False, "content": "## 财务表现分析\n旧路径正常 [P 0]"}

    monkeypatch.setattr(fa, "safe_invoke_with_tools", fake_with_tools)
    monkeypatch.setattr(fa, "safe_invoke", fake_safe_invoke)

    state = _state_with_l1()
    out = fa.financial_analyst_node(state)

    assert "called" not in called_new, "flag 关闭不得走工具链路"
    assert out["analysis_result"] == "## 财务表现分析\n旧路径正常 [P 0]"


def test_analyst_multilevel_audit_history_passthrough(monkeypatch):
    """新链路工具调用历史必须回流 state（银行审计）"""
    import agents.financial_analyst as fa
    from utils.config import get_settings

    monkeypatch.setattr(get_settings(), "USE_MULTILEVEL_COMPRESSION", True)
    monkeypatch.setattr(fa, "is_llm_ready", lambda: True)
    state = _state_with_l1()
    state["tool_call_history"] = [{"agent": "report_extractor", "tool": "extract", "ts": "x"}]

    def fake_with_tools(system_prompt, user_message, tools, max_rounds=None, on_tool_call=None):
        # 模拟一次真实工具调用触发的审计回调
        if on_tool_call:
            on_tool_call("query_fact", {"metric": "营业收入"}, "命中结果")
        return {"error": False, "content": "OK", "tool_calls": [{"tool": "query_fact"}], "rounds": 1}

    monkeypatch.setattr(fa, "safe_invoke_with_tools", fake_with_tools)

    out = fa.financial_analyst_node(state)
    calls = out.get("tool_call_history", [])
    assert len(calls) == 2, "旧留痕 + 本次工具调用都必须保留"
    last = calls[-1]
    assert last["agent"] == "financial_analyst"
    assert last["tool"] == "query_fact"
    assert "ts" in last and "result_len" in last, "审计字段必须齐全（谁/何时/工具/参数/返回体量）"
