"""Path A — passive ingestion of local AI coding-tool transcripts.

Readers normalise each source into ParsedTurn; the runner writes those into T1
and derives rule-based T2 day cards. Nothing here calls a model.
"""

from .models import DayDigest, DayStats, FileCursor, ParsedTurn, ParseOutcome

__all__ = [
    "DayDigest",
    "DayStats",
    "FileCursor",
    "ParseOutcome",
    "ParsedTurn",
]
