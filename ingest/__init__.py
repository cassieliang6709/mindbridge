"""Path A — passive ingestion of local AI coding-tool transcripts.

Readers normalise each source into ParsedTurn; the runner writes those into T1
and derives rule-based T2 day cards. Nothing here calls a model.

中文说明：这是 Path A（被动摄取）模块的入口。各来源（Claude Code、Codex CLI）的
读取器把原始记录统一整理成 ParsedTurn，再由 runner 写入 T1 表，并基于规则生成
T2 日卡片。整个流程不调用任何模型。
"""

from .models import DayDigest, DayStats, FileCursor, ParsedTurn, ParseOutcome

__all__ = [
    "DayDigest",
    "DayStats",
    "FileCursor",
    "ParseOutcome",
    "ParsedTurn",
]
