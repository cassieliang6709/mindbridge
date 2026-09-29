"""The three memory tiers.

T1 session_buffer  — raw turns, newest window
T2 rolling_summary — one structured card per period
T3 vector_store    — long-term preferences with time decay

中文说明：本包公开三层记忆存储及其共享 token 计数工具：T1 保存原始对话，
T2 保存按时间段生成的结构化卡片，T3 保存带时间衰减的长期记忆。
"""

from .rolling_summary import RollingSummaryStore
from .session_buffer import IngestRow, SessionBufferStore
from .tokens import count_many, count_tokens, tokenizer_name
from .vector_store import NearestMatch, VectorMemoryStore
from .pattern_store import PatternCandidateStore

__all__ = [
    "NearestMatch",
    "PatternCandidateStore",
    "RollingSummaryStore",
    "IngestRow",
    "SessionBufferStore",
    "VectorMemoryStore",
    "count_many",
    "count_tokens",
    "tokenizer_name",
]
