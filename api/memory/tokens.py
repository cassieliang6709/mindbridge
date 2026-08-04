"""Token counting.

Uses tiktoken when it is installed, and a character heuristic otherwise. Which
one ran is recorded alongside any measurement, because a heuristic count is not
a token count and a benchmark should say which it used.
"""

from __future__ import annotations

from functools import lru_cache

_ENCODING_NAME = "cl100k_base"
# Mixed CJK/latin technical prose averages near three characters per token.
_CHARS_PER_TOKEN = 3


@lru_cache
def _encoding():  # pragma: no cover - depends on the optional dependency
    try:
        import tiktoken
    except ImportError:
        return None
    try:
        return tiktoken.get_encoding(_ENCODING_NAME)
    except Exception:
        # get_encoding downloads the vocabulary on first use; offline that
        # raises, and the heuristic is the correct fallback.
        return None


def tokenizer_name() -> str:
    return _ENCODING_NAME if _encoding() is not None else "chars-per-3-heuristic"


def count_tokens(text: str) -> int:
    encoding = _encoding()
    if encoding is None:
        return max(1, -(-len(text) // _CHARS_PER_TOKEN))
    return len(encoding.encode(text))


def count_many(texts: list[str]) -> int:
    return sum(count_tokens(text) for text in texts)
