"""
FinScope token 计数（P2，tiktoken）

预算驱动装配的正确性**完全依赖计数准确**，故用 tiktoken（cl100k_base，DeepSeek 分词近似）
而非字符估算。tiktoken 不可用时降级字符估算（中文 ~1.5 token/字），never-throw。

装配器使用 count_tokens_safe()（×1.15 + 常数余量），另有硬上限兜底双保险。
"""

import logging

logger = logging.getLogger(__name__)

_ENCODER = None
_ENCODER_FAILED = False

# 安全余量系数（DeepSeek 分词与 cl100k 偏差 + 跨调用漂移）
_SAFETY_MULTIPLIER = 1.15
_SAFETY_CONSTANT = 8


def _get_encoder():
    global _ENCODER, _ENCODER_FAILED
    if _ENCODER is None and not _ENCODER_FAILED:
        try:
            import tiktoken
            _ENCODER = tiktoken.get_encoding("cl100k_base")
        except Exception as e:
            _ENCODER_FAILED = True
            logger.warning("[TokenCounter] tiktoken 不可用，降级字符估算: %s", str(e)[:100])
    return _ENCODER


def count_tokens(text: str) -> int:
    """精确 token 计数（tiktoken cl100k_base；不可用时字符估算）"""
    if not text:
        return 0
    enc = _get_encoder()
    if enc is not None:
        try:
            return len(enc.encode(str(text)))
        except Exception:
            pass
    # 降级: 中文 ~1.5 token/字，ASCII ~0.3 token/字符
    try:
        cjk = sum(1 for ch in str(text) if "\u4e00" <= ch <= "\u9fff")
        other = len(str(text)) - cjk
        return int(cjk * 1.5 + other * 0.3) + 1
    except Exception:
        return len(str(text))


def count_tokens_safe(text: str) -> int:
    """带安全余量的 token 计数（装配器使用）"""
    return int(count_tokens(text) * _SAFETY_MULTIPLIER) + _SAFETY_CONSTANT
