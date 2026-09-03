"""
FinScope LLM 客户端

支持多模型后端（DeepSeek / OpenAI / Ollama）:
- 全局单例避免重复实例化
- 同步/异步双模式
- safe_invoke() 统一异常兜底
"""

import threading
import logging
from typing import Optional, Any, Dict, AsyncGenerator, Union, List

from langchain_core.messages import HumanMessage, SystemMessage

from .config import get_settings

logger = logging.getLogger(__name__)

_llm_instance = None
_async_llm_instance = None
_lock = threading.Lock()


def _create_llm(streaming: bool = False):
    """根据配置创建 LLM 实例"""
    s = get_settings()
    provider = s.LLM_PROVIDER.lower()

    if provider == "deepseek":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=s.DEEPSEEK_MODEL,
            openai_api_key=s.DEEPSEEK_API_KEY or "placeholder",
            openai_api_base=s.DEEPSEEK_BASE_URL,
            temperature=s.LLM_TEMPERATURE,
            max_tokens=s.LLM_MAX_TOKENS,
            streaming=streaming,
            request_timeout=s.AGENT_TIMEOUT_SECONDS,
            max_retries=1,
        )
    elif provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=s.OPENAI_MODEL,
            openai_api_key=s.OPENAI_API_KEY or "placeholder",
            openai_api_base=s.OPENAI_BASE_URL,
            temperature=s.LLM_TEMPERATURE,
            max_tokens=s.LLM_MAX_TOKENS,
            streaming=streaming,
            request_timeout=s.AGENT_TIMEOUT_SECONDS,
            max_retries=1,
        )
    elif provider == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(
            model=s.OLLAMA_MODEL,
            base_url=s.OLLAMA_BASE_URL,
            temperature=s.LLM_TEMPERATURE,
            num_predict=s.LLM_MAX_TOKENS,
        )
    else:
        raise ValueError(f"不支持的 LLM 提供商: {provider}")


def get_llm():
    """获取全局同步 LLM 客户端（单例）"""
    global _llm_instance
    if _llm_instance is None:
        with _lock:
            if _llm_instance is None:
                _llm_instance = _create_llm(streaming=False)
                s = get_settings()
                logger.info("LLM 客户端已初始化: provider=%s", s.LLM_PROVIDER)
    return _llm_instance


def get_async_llm():
    """获取全局异步 LLM 客户端（单例，streaming=True）"""
    global _async_llm_instance
    if _async_llm_instance is None:
        with _lock:
            if _async_llm_instance is None:
                _async_llm_instance = _create_llm(streaming=True)
                logger.info("异步 LLM 客户端已初始化")
    return _async_llm_instance


def safe_invoke(
    system_prompt: str,
    user_message: str,
    temperature: Optional[float] = None,
) -> Dict[str, Any]:
    """
    安全同步调用 LLM（统一异常兜底）

    Returns:
        {"error": False, "content": "...", "model": "...", ...}
        或
        {"error": True, "message": "...", "error_type": "..."}
    """
    result = {
        "error": False,
        "content": "",
        "model": "",
        "finish_reason": "",
        "usage": {},
    }

    try:
        llm = get_llm()
        if temperature is not None:
            llm.temperature = temperature

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_message),
        ]
        response = llm.invoke(messages)

        result["content"] = response.content or ""
        result["model"] = getattr(response, "response_metadata", {}).get("model_name", "")
        result["finish_reason"] = getattr(
            response, "response_metadata", {}
        ).get("finish_reason", "stop")
        result["usage"] = getattr(response, "response_metadata", {}).get("token_usage", {})

    except Exception as e:
        error_str = str(e).lower()
        if "401" in error_str or "authentication" in error_str or "invalid" in error_str:
            result["error"] = True
            result["message"] = "API Key 无效或已过期"
            result["error_type"] = "auth_error"
        elif "429" in error_str or "rate" in error_str or "quota" in error_str:
            result["error"] = True
            result["message"] = "API 调用额度不足或频率过高"
            result["error_type"] = "rate_limit"
        elif "timeout" in error_str or "timed out" in error_str:
            result["error"] = True
            result["message"] = f"API 调用超时（>{get_settings().AGENT_TIMEOUT_SECONDS}秒）"
            result["error_type"] = "timeout_error"
        elif "connection" in error_str or "network" in error_str or "refused" in error_str:
            result["error"] = True
            result["message"] = "网络连接失败"
            result["error_type"] = "network_error"
        else:
            result["error"] = True
            result["message"] = f"LLM 调用异常: {str(e)[:200]}"
            result["error_type"] = "unknown"

        logger.error("LLM safe_invoke 异常: type=%s msg=%s", result.get("error_type"), result.get("message"))

    return result


async def safe_astream(
    system_prompt: str,
    user_message: str,
) -> AsyncGenerator[str, None]:
    """安全异步流式调用 LLM"""
    try:
        llm = get_async_llm()
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_message),
        ]
        async for chunk in llm.astream(messages):
            if hasattr(chunk, "content") and chunk.content:
                yield chunk.content
    except Exception as e:
        error_msg = f"\n\n[流式输出中断: {str(e)[:100]}]"
        logger.error("LLM safe_astream 异常: %s", e)
        yield error_msg


def is_llm_ready() -> bool:
    """检查 LLM 是否配置就绪"""
    return get_settings().is_api_ready()


# [C4] 单次 invoke 的工具调用总数硬熔断（max_rounds 是轮数语义，并行调用不受其约束）
_TOOL_CALLS_HARD_CAP = 40


def safe_invoke_with_tools(
    system_prompt: str,
    user_message: str,
    tools: List[Any],
    max_rounds: int = 3,
    on_tool_call=None,
) -> Dict[str, Any]:
    """
    有界工具循环调用 LLM（ReAct-lite，P2 Agent 工具化地基）

    - tools: langchain @tool 装饰的函数列表（bind_tools 绑定）
    - 循环: 调 LLM → 若返回 tool_calls 则逐条执行并回填 ToolMessage → 再调，
      直到无 tool_calls 或达 max_rounds（防 agent 无限循环烧钱）
    - on_tool_call: 可选回调 (tool_name, args, result)，用于工具审计留痕
      （银行要求: 谁/何时/调了什么/参数/返回体量，由 Agent 层写入 tool_call_history）

    Returns:
        {"error": False, "content": "...", "tool_calls": [...], "rounds": N, "usage": {}}
        或 {"error": True, "message": "...", "error_type": "..."}
    """
    result = {
        "error": False, "content": "", "model": "",
        "tool_calls": [], "rounds": 0, "usage": {},
    }
    if not tools:
        return safe_invoke(system_prompt, user_message)  # 无工具降级为普通调用

    from langchain_core.messages import AIMessage, ToolMessage

    try:
        llm = get_llm().bind_tools(tools)
        messages: List[Any] = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_message),
        ]
        tool_calls_log: List[Dict[str, Any]] = []

        for _round in range(max_rounds):
            response = llm.invoke(messages)
            messages.append(response)
            result["rounds"] = _round + 1

            calls = getattr(response, "tool_calls", None) or []
            if not calls:
                result["content"] = response.content or ""
                result["model"] = getattr(response, "response_metadata", {}).get("model_name", "")
                result["usage"] = getattr(response, "response_metadata", {}).get("token_usage", {})
                break

            for call in calls:
                name = call.get("name", "")
                args = call.get("args", {}) or {}
                call_id = call.get("id", "")

                # [C4] 真实调用数硬熔断: max_rounds 是轮数上限，每轮可带多条并行调用，
                # 病态场景下总数无界（实测 furui 28 调用/5 轮）。超帽的调用不执行，
                # 以提示性 ToolMessage 应答保持消息链一致（模型可基于已有结果收尾）。
                if len(tool_calls_log) >= _TOOL_CALLS_HARD_CAP:
                    messages.append(ToolMessage(
                        content="工具调用总数已达硬上限，此调用未执行。请基于已有结果作答。",
                        tool_call_id=call_id,
                    ))
                    continue

                tool_func = next((t for t in tools if getattr(t, "name", "") == name), None)

                if tool_func is None:
                    reply = f"错误: 未知工具 {name}"
                else:
                    try:
                        raw = tool_func.invoke(args)
                        reply = raw if isinstance(raw, str) else str(raw)
                    except Exception as e:
                        reply = f"工具执行异常: {str(e)[:200]}"

                messages.append(ToolMessage(content=reply[:8000], tool_call_id=call_id))
                entry = {"tool": name, "args": args, "result_preview": reply[:200]}
                tool_calls_log.append(entry)
                if on_tool_call:
                    try:
                        on_tool_call(name, args, reply)
                    except Exception:
                        pass

            result["tool_calls"] = tool_calls_log

        # [A5] 轮数耗尽仍无文本（每轮都在调工具）→ 强制总结轮：解绑工具 +1 调用，
        # 保证 agent 必有最终产出（有界：仅在耗尽且 content 为空时触发一次）
        if not result["content"] and tool_calls_log:
            messages.append(HumanMessage(
                content="工具调用轮次已达上限。请立即基于以上全部工具结果给出最终回答，不要再请求任何工具。"
            ))
            final = get_llm().invoke(messages)
            result["content"] = getattr(final, "content", "") or ""
            result["rounds"] += 1

        if not result["content"] and not tool_calls_log:
            last_content = getattr(messages[-1], "content", "") or ""
            result["content"] = str(last_content)

    except Exception as e:
        error_str = str(e).lower()
        if "429" in error_str or "rate" in error_str or "quota" in error_str:
            result.update({"error": True, "message": "API 调用额度不足或频率过高", "error_type": "rate_limit"})
        elif "timeout" in error_str or "timed out" in error_str:
            result.update({"error": True, "message": "API 调用超时", "error_type": "timeout_error"})
        else:
            result.update({"error": True, "message": f"LLM 工具调用异常: {str(e)[:200]}", "error_type": "unknown"})
        logger.error("LLM safe_invoke_with_tools 异常: %s", e)

    return result
