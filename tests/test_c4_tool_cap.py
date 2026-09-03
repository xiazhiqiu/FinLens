# -*- coding: utf-8 -*-
"""[C4] 工具调用硬熔断测试"""
import sys
sys.path.insert(0, r'd:\develop\财报分析系统\src')
from unittest.mock import patch, MagicMock
from utils import llm_client as lc


def test_tool_calls_hard_cap():
    """超过 40 次真实调用后: 不再执行，以提示性 ToolMessage 应答，循环有界结束"""
    made = {"n": 0}

    class _Counter:
        name = "count"

        def invoke(self, args):
            made["n"] += 1
            return f"ok-{made['n']}"

    # 共享轮次状态: get_llm() 会被调用两次（工具循环 + 强制总结轮），须共享计数
    shared_state = {"round": 0}

    def fake_llm():
        llm = MagicMock()
        llm.bind_tools.return_value = llm

        def _invoke(messages):
            shared_state["round"] += 1
            if shared_state["round"] <= 8:
                msg = MagicMock()
                calls = [{"name": "count", "args": {}, "id": f"c{shared_state['round']}-{i}"} for i in range(6)]
                type(msg).tool_calls = calls
                msg.content = ""
                return msg
            msg = MagicMock()
            type(msg).tool_calls = []
            msg.content = "final answer"
            return msg

        llm.invoke.side_effect = _invoke
        return llm

    with patch.object(lc, "get_llm", side_effect=fake_llm), \
         patch.object(lc, "is_llm_ready", return_value=True):
        # 8 轮上限（>40/6=7 轮即触帽）
        result = lc.safe_invoke_with_tools("s", "u", [_Counter()], max_rounds=8)

    assert made["n"] == 40, f"实际执行应恰好 40 次（硬帽），got {made['n']}"
    assert len(result["tool_calls"]) == 40
    assert result["content"] == "final answer"
    assert result["rounds"] == 9  # 8 轮工具 + 1 轮收尾


def test_rounds_semantics_unchanged():
    """轮数上限语义不变: 少量调用场景行为与原先一致"""
    class _Echo:
        name = "echo"

        def invoke(self, args):
            return "echoed"

    def fake_llm():
        llm = MagicMock()
        llm.bind_tools.return_value = llm
        state = {"round": 0}

        def _invoke(messages):
            state["round"] += 1
            msg = MagicMock()
            if state["round"] == 1:
                type(msg).tool_calls = [{"name": "echo", "args": {}, "id": "x1"}]
                msg.content = ""
            else:
                type(msg).tool_calls = []
                msg.content = "done"
            return msg

        llm.invoke.side_effect = _invoke
        return llm

    with patch.object(lc, "get_llm", side_effect=fake_llm), \
         patch.object(lc, "is_llm_ready", return_value=True):
        result = lc.safe_invoke_with_tools("s", "u", [_Echo()], max_rounds=3)

    assert result["content"] == "done"
    assert result["rounds"] == 2
    assert len(result["tool_calls"]) == 1
