"""
A2 测试: Reviewer 用 query_fact 核验报告数字（多级压缩新链路）

覆盖:
- flag 开启 + facts 就绪 → 挂 3 工具、数字核验职责进 prompt、审计留痕 agent="reviewer"
- 核验闭环语义: 工具查证 → revise JSON「报告值 vs 事实表值」→ prev 备份 + tool_call_history 回流
- flag 关闭 → 旧路径纯文本审查（防回归，prompt 无核验段）
- flag 开但 facts 空 → 门控不放行（走旧路径）
- LLM 失败新链路 → pass 放行 + 留痕不丢
"""

import json

from graphs.state import create_initial_state
from extractors.l1_builder import build_l1


def _tiny_l1():
    pages = [
        {"page_idx": 0, "items": [
            {"type": "heading", "content": "合并利润表", "level": 2},
            {"type": "table", "content": (
                "<table><tr><td>项目</td><td>2024年</td><td>2023年</td></tr>"
                "<tr><td>营业收入</td><td>204.42</td><td>195.14</td></tr></table>"
            ), "caption": [], "footnote": []},
        ]},
    ]
    return build_l1(pages, companies=["复星医药"])


def _state_for_reviewer():
    st = create_initial_state("撰写复星医药投资分析报告", pdf_path="")
    st["pdf_l1"] = _tiny_l1()
    st["final_report"] = "# 复星医药投资分析报告\n2024年营业收入 204.43 亿元 [P 0]\n> 免责声明: 不构成投资建议"
    return st


_REVISE_JSON = json.dumps({
    "verdict": "revise",
    "defect_locus": "report",
    "issues_found": ["财务数字与事实表不符: 报告值 204.43 亿元 vs 事实表 204.42 (src p0 t_000)"],
    "feedback": "将「营业收入 204.43 亿元」改为事实表值 204.42 亿元 [P 0]",
}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 1. flag 开启 → 新链路（工具 + 核验职责 + 审计）
# ---------------------------------------------------------------------------

def test_reviewer_uses_fact_check_path_when_flag_on(monkeypatch):
    import agents.reviewer as rv
    from utils.config import get_settings

    monkeypatch.setattr(get_settings(), "USE_MULTILEVEL_COMPRESSION", True)
    monkeypatch.setattr(rv, "is_llm_ready", lambda: True)

    captured = {}

    def fake_with_tools(system_prompt, user_message, tools, max_rounds=None, on_tool_call=None):
        captured["prompt"] = system_prompt
        captured["n_tools"] = len(tools)
        captured["tool_names"] = sorted(t.name for t in tools)
        captured["on_tool_call"] = on_tool_call
        # 模拟 LLM 核验: 调 query_fact 后判 revise
        if on_tool_call:
            on_tool_call("query_fact", {"metric": "营业收入", "period": "FY2024"}, "复星医药 | 营业收入 | FY2024 = 204.42 (src p0 t_000)")
        return {"error": False, "content": _REVISE_JSON, "tool_calls": [{"tool": "query_fact"}], "rounds": 2}

    monkeypatch.setattr(rv, "safe_invoke_with_tools", fake_with_tools)

    out = rv.reviewer_node(_state_for_reviewer())

    assert captured["n_tools"] == 3
    assert captured["tool_names"] == ["fetch_context", "query_fact", "search_section"]
    # 数字核验职责必须进 prompt
    assert "数字核验职责" in captured["prompt"]
    assert "query_fact" in captured["prompt"]
    # 判定解析: revise + 具体反馈
    assert out["review_result"] == "revise"
    assert "204.42" in out["review_feedback"] and "204.43" in out["review_feedback"]
    assert out["defect_locus"] == "report"
    assert out["review_revision_count"] == 1
    # revise 必须备份上一版（供 Writer 增量修订）
    assert "204.43" in out["prev_final_report"]
    # 审计留痕: agent 标记 reviewer
    calls = out["tool_call_history"]
    assert calls and calls[-1]["agent"] == "reviewer"
    assert calls[-1]["tool"] == "query_fact"
    assert "ts" in calls[-1] and "result_len" in calls[-1]


def test_reviewer_pass_verdict_passthrough(monkeypatch):
    """pass 判定: 不备份、revision_count 不增、留痕仍回流"""
    import agents.reviewer as rv
    from utils.config import get_settings

    monkeypatch.setattr(get_settings(), "USE_MULTILEVEL_COMPRESSION", True)
    monkeypatch.setattr(rv, "is_llm_ready", lambda: True)
    pass_json = json.dumps({"verdict": "pass", "issues_found": [], "feedback": ""}, ensure_ascii=False)
    monkeypatch.setattr(rv, "safe_invoke_with_tools",
                        lambda *a, **kw: {"error": False, "content": pass_json, "tool_calls": [], "rounds": 1})

    st = _state_for_reviewer()
    out = rv.reviewer_node(st)

    assert out["review_result"] == "pass"
    assert out["review_revision_count"] == 0
    assert out.get("prev_final_report", "") == ""


# ---------------------------------------------------------------------------
# 2. flag 关闭 → 旧路径（防回归）
# ---------------------------------------------------------------------------

def test_reviewer_keeps_old_path_when_flag_off(monkeypatch):
    import agents.reviewer as rv
    from utils.config import get_settings

    monkeypatch.setattr(get_settings(), "USE_MULTILEVEL_COMPRESSION", False)
    monkeypatch.setattr(rv, "is_llm_ready", lambda: True)

    called_new = {}

    def fake_with_tools(*a, **kw):
        called_new["called"] = True
        return {"error": False, "content": "{}"}

    def fake_safe_invoke(system_prompt, user_message, **kw):
        called_new["prompt"] = system_prompt
        return {"error": False, "content": _REVISE_JSON}

    monkeypatch.setattr(rv, "safe_invoke_with_tools", fake_with_tools)
    monkeypatch.setattr(rv, "safe_invoke", fake_safe_invoke)

    out = rv.reviewer_node(_state_for_reviewer())

    assert "called" not in called_new, "flag 关闭不得走工具链路"
    assert "数字核验职责" not in called_new["prompt"], "旧路径 prompt 不得混入核验段"
    assert out["review_result"] == "revise", "旧路径 JSON 解析行为不变"
    assert "204.42" in out["review_feedback"]


def test_reviewer_gate_requires_facts(monkeypatch):
    """flag 开但 facts 空 → 门控不放行（Reviewer 的价值在事实表，没 facts 不挂工具）"""
    import agents.reviewer as rv
    from utils.config import get_settings

    monkeypatch.setattr(get_settings(), "USE_MULTILEVEL_COMPRESSION", True)
    monkeypatch.setattr(rv, "is_llm_ready", lambda: True)

    called_new = {}

    def fake_with_tools(*a, **kw):
        called_new["called"] = True
        return {"error": False, "content": "{}"}

    def fake_safe_invoke(*a, **kw):
        return {"error": False, "content": json.dumps({"verdict": "pass"}, ensure_ascii=False)}

    monkeypatch.setattr(rv, "safe_invoke_with_tools", fake_with_tools)
    monkeypatch.setattr(rv, "safe_invoke", fake_safe_invoke)

    st = _state_for_reviewer()
    st["pdf_l1"] = {"sections": [{"section_id": "s_000", "title": "利润表"}], "tables": [], "facts": []}
    out = rv.reviewer_node(st)

    assert "called" not in called_new, "facts 为空不得走工具核验链路"
    assert out["review_result"] == "pass"


# ---------------------------------------------------------------------------
# 3. 降级与边界
# ---------------------------------------------------------------------------

def test_reviewer_llm_error_new_path(monkeypatch):
    """新链路 LLM 失败: pass 放行（既有降级语义）+ 工具留痕不丢"""
    import agents.reviewer as rv
    from utils.config import get_settings

    monkeypatch.setattr(get_settings(), "USE_MULTILEVEL_COMPRESSION", True)
    monkeypatch.setattr(rv, "is_llm_ready", lambda: True)

    def fake_with_tools(system_prompt, user_message, tools, max_rounds=None, on_tool_call=None):
        if on_tool_call:
            on_tool_call("search_section", {"query": "营业收入"}, "命中 s_000")
        return {"error": True, "message": "API 超时"}

    monkeypatch.setattr(rv, "safe_invoke_with_tools", fake_with_tools)

    out = rv.reviewer_node(_state_for_reviewer())

    assert out["review_result"] == "pass"
    assert "审查失败" in out["review_feedback"]
    assert out["error_log"], "失败必须留痕 error_log"
    assert out["tool_call_history"] and out["tool_call_history"][-1]["agent"] == "reviewer", "失败前的工具调用留痕不丢"


def test_reviewer_revision_limit_still_enforced(monkeypatch):
    """修订次数熔断优先于一切（既有语义，新链路不得绕过）"""
    import agents.reviewer as rv
    from utils.config import get_settings

    monkeypatch.setattr(get_settings(), "USE_MULTILEVEL_COMPRESSION", True)
    monkeypatch.setattr(rv, "is_llm_ready", lambda: True)
    called = {}
    monkeypatch.setattr(rv, "safe_invoke_with_tools",
                        lambda *a, **kw: called.setdefault("called", True) or {"error": False, "content": "{}"})

    st = _state_for_reviewer()
    st["review_revision_count"] = rv.MAX_REVIEW_REVISIONS
    out = rv.reviewer_node(st)

    assert not called.get("called"), "熔断后不得再调 LLM"
    assert out["review_result"] == "pass"
    assert "上限" in out["review_feedback"]


def test_reviewer_json_with_preamble(monkeypatch):
    """[A5] 工具循环回复带元评论前缀的 JSON 也必须解析成功（不落默认通过）"""
    import agents.reviewer as rv
    from utils.config import get_settings

    monkeypatch.setattr(get_settings(), "USE_MULTILEVEL_COMPRESSION", True)
    monkeypatch.setattr(rv, "is_llm_ready", lambda: True)
    noisy = (
        "我已完成对报告的审查，核对了几处关键数字。审查结论 JSON 如下：\n"
        '综合判断 {"verdict": "revise", "defect_locus": "report", '
        '"issues_found": ["营收数字与事实表不符"], "feedback": "改为 204.42"}'
    )
    monkeypatch.setattr(rv, "safe_invoke_with_tools",
                        lambda *a, **kw: {"error": False, "content": noisy, "tool_calls": [], "rounds": 1})

    out = rv.reviewer_node(_state_for_reviewer())

    assert out["review_result"] == "revise", "前缀元评论不得导致 JSON 解析失败落默认 pass"
    assert "204.42" in out["review_feedback"]
    assert out["defect_locus"] == "report"


# ---------------------------------------------------------------------------
# 4. [C组修复] 解析失败不得静默通过: 重试一次 + 保守降级可见
# ---------------------------------------------------------------------------

def test_reviewer_parse_fail_retries_then_succeeds(monkeypatch):
    """[furui 实测] 首轮输出纯文本（无 JSON）→ schema 强约束重试拿到判定 → 正常 verdict，不降级"""
    import agents.reviewer as rv
    from utils.config import get_settings

    monkeypatch.setattr(get_settings(), "USE_MULTILEVEL_COMPRESSION", True)
    monkeypatch.setattr(rv, "is_llm_ready", lambda: True)

    with_tools_calls = []
    schema_calls = []

    def fake_with_tools(system_prompt, user_message, tools, max_rounds=None, on_tool_call=None):
        with_tools_calls.append(user_message)
        return {"error": False, "content": "我认为这份报告整体质量尚可，部分数字已核对。", "tool_calls": [], "rounds": 1}

    def fake_schema_retry(system_prompt, user_message):
        schema_calls.append(user_message)
        return {
            "verdict": "revise", "defect_locus": "report",
            "issues_found": ["营收数字与事实表不符"],
            "feedback": "将「营业收入 204.43 亿元」改为事实表值 204.42 亿元 [P 0]",
        }

    monkeypatch.setattr(rv, "safe_invoke_with_tools", fake_with_tools)
    monkeypatch.setattr(rv, "_schema_retry", fake_schema_retry)

    out = rv.reviewer_node(_state_for_reviewer())

    assert len(with_tools_calls) == 1
    assert len(schema_calls) == 1, "解析失败必须恰好重试一次（schema 通道）"
    assert out["review_result"] == "revise", "schema 重试成功后按其结果判定"
    assert "204.42" in out["review_feedback"]
    assert out["review_revision_count"] == 1


def test_reviewer_parse_fail_degrades_visibly(monkeypatch):
    """首轮纯文本 + schema 重试也失败 → 保守降级: pass 但 feedback 警示 + error_log 留痕"""
    import agents.reviewer as rv
    from utils.config import get_settings

    monkeypatch.setattr(get_settings(), "USE_MULTILEVEL_COMPRESSION", True)
    monkeypatch.setattr(rv, "is_llm_ready", lambda: True)

    with_tools_calls = []
    monkeypatch.setattr(rv, "safe_invoke_with_tools",
                        lambda sp, um, tools, max_rounds=None, on_tool_call=None:
                        with_tools_calls.append(um) or {"error": False, "content": "纯文本无 JSON", "tool_calls": [], "rounds": 1})
    schema_calls = []
    monkeypatch.setattr(rv, "_schema_retry",
                        lambda sp, um: schema_calls.append(um) or None)

    out = rv.reviewer_node(_state_for_reviewer())

    assert len(schema_calls) == 1, "恰好一次 schema 重试（有界）"
    assert out["review_result"] == "pass", "不烧修订轮数（问题不在 Writer）"
    assert "审查降级" in out["review_feedback"], "降级必须在 feedback 显式可见"
    assert "人工复核" in out["review_feedback"]
    assert out["review_revision_count"] == 0
    assert any("不可解析" in e for e in out["error_log"]), "降级必须留痕 error_log"
    assert "审查未完成" in out["review_feedback"], "feedback 必须标注审查未完成"


def test_reviewer_llm_error_degrades_visibly(monkeypatch):
    """LLM 调用失败 → pass 但显式降级标注（审查未发生不得静默当通过）"""
    import agents.reviewer as rv
    from utils.config import get_settings

    monkeypatch.setattr(get_settings(), "USE_MULTILEVEL_COMPRESSION", True)
    monkeypatch.setattr(rv, "is_llm_ready", lambda: True)
    monkeypatch.setattr(rv, "safe_invoke_with_tools",
                        lambda *a, **kw: {"error": True, "message": "API 超时"})

    out = rv.reviewer_node(_state_for_reviewer())

    assert out["review_result"] == "pass"
    assert "审查降级" in out["review_feedback"]
    assert "未经过审查" in out["review_feedback"]
    assert out["error_log"]
