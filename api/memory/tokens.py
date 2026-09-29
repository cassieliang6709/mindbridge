"""Token counting.

Uses tiktoken when it is installed, and a character heuristic otherwise. Which
one ran is recorded alongside any measurement, because a heuristic count is not
a token count and a benchmark should say which it used.

中文说明：优先使用 tiktoken；依赖不可用或离线加载失败时，退化为字符数估算。
任何评测都必须同时记录实际使用的计数器，因为估算值不能冒充精确 token 数。
"""

from __future__ import annotations

from functools import lru_cache

_ENCODING_NAME = "cl100k_base"
# Mixed CJK/latin technical prose averages near three characters per token.
# 中文：中英混合的技术文本平均约每三个字符一个 token，仅作为离线兜底。
_CHARS_PER_TOKEN = 3


@lru_cache
def _encoding():  # pragma: no cover - depends on the optional dependency
    """Load and cache the optional tiktoken encoding.

    中文：加载并缓存可选的 tiktoken 编码器；依赖或词表不可用时返回 None。
    """
    try:
        import tiktoken
    except ImportError:
        return None
    try:
        return tiktoken.get_encoding(_ENCODING_NAME)
    except Exception:
        # get_encoding downloads the vocabulary on first use; offline that
        # raises, and the heuristic is the correct fallback.
        # 中文：首次加载可能需要下载词表；离线失败时应使用可解释的字符估算。
        return None


def tokenizer_name() -> str:
    """Return the exact counter name that current measurements use.

    中文：返回当前计数实际使用的编码器或估算策略名称。
    """
    return _ENCODING_NAME if _encoding() is not None else "chars-per-3-heuristic"


def count_tokens(text: str) -> int:
    """Count tokens exactly when possible, otherwise use the documented estimate.

    中文：可用时精确计数，否则使用已记录名称的字符估算。

    Args:
        text: Text to measure.

    Returns:
        A positive token count or estimate.
    """
    encoding = _encoding()
    if encoding is None:
        return max(1, -(-len(text) // _CHARS_PER_TOKEN))
    return len(encoding.encode(text))


def count_many(texts: list[str]) -> int:
    """Return the sum of counts for several independent texts.

    中文：分别计数多段文本并返回总和。

    Args:
        texts: Text fragments to measure.

    Returns:
        Sum of count_tokens() over all fragments.
    """
    return sum(count_tokens(text) for text in texts)
