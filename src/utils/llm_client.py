"""
FinScope LLM 客户端

支持多模型后端（DeepSeek / OpenAI / Ollama）:
- 全局单例避免重复实例化
- 同步/异步双模式
- safe_invoke() 统一异常兜底
"""

import threading
import logging
from typing import Optional, Any, Dict, AsyncGenerator, Union

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
