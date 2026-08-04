"""Turn a day's parsed turns into a T2 card — deterministically.

Every line is computed from counts and timestamps. There is no model call here,
so the card is reproducible and cannot invent a detail the transcript does not
contain. That is a deliberate limit, not an oversight:

- Prose narration of a day ("spent most of today on the retrieval path") and
  extraction of durable preferences ("prefers uv over pip") both need the M2
  local extractor, which is not built.
- Behavioural facts are stated as observations with their evidence attached
  ("last activity 01:12"), never as inferences about mood or state.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from .models import DayDigest, DayStats, ParsedTurn

# Anything at or after this local hour counts as a late session, reported as an
# observation with the timestamp attached rather than as a judgement.
LATE_HOUR = 0
LATE_HOUR_END = 5


def group_by_day(
    turns: list[ParsedTurn], tz: ZoneInfo
) -> dict[str, list[ParsedTurn]]:
    """Bucket turns by local calendar date.

    Local, not UTC: a day boundary that does not match the user's own midnight
    would split a late-night session across two cards.
    """
    days: dict[str, list[ParsedTurn]] = {}
    for turn in turns:
        key = turn.created_at.astimezone(tz).date().isoformat()
        days.setdefault(key, []).append(turn)
    return days


def build_stats(turns: list[ParsedTurn], tz: ZoneInfo) -> DayStats:
    tools: Counter[str] = Counter()
    projects: Counter[str] = Counter()
    branches: set[str] = set()
    sources: set[str] = set()
    sessions: set[str] = set()
    user_turns = assistant_turns = tokens = 0
    first = last = None
    latest_late = None

    for turn in turns:
        sessions.add(f"{turn.source}:{turn.session_id}")
        sources.add(turn.source)
        tools.update(turn.tool_names)
        if turn.project:
            projects[turn.project] += 1
        if turn.git_branch:
            branches.add(turn.git_branch)
        tokens += turn.token_count
        if turn.role == "user":
            user_turns += 1
        elif turn.role == "assistant":
            assistant_turns += 1

        local = turn.created_at.astimezone(tz)
        if first is None or turn.created_at < first:
            first = turn.created_at
        if last is None or turn.created_at > last:
            last = turn.created_at
        # "After midnight" means a turn whose own local hour is in the small
        # hours. Report the latest such turn's timestamp — an earlier version
        # ranked hours on a day-relative scale and then printed the day's last
        # activity, so a 04:43 session made a 20:00 finish read as after
        # midnight.
        if LATE_HOUR <= local.hour <= LATE_HOUR_END:
            if latest_late is None or turn.created_at > latest_late:
                latest_late = turn.created_at

    return DayStats(
        sessions=len(sessions),
        turns=len(turns),
        user_turns=user_turns,
        assistant_turns=assistant_turns,
        tokens=tokens,
        tool_counts=dict(tools.most_common()),
        projects=[name for name, _ in projects.most_common()],
        git_branches=sorted(branches),
        sources=sorted(sources),
        first_activity=first,
        last_activity=last,
        latest_late_activity=latest_late,
    )


def build_digest(date: str, turns: list[ParsedTurn], tz: ZoneInfo) -> DayDigest:
    stats = build_stats(turns, tz)
    facts: list[str] = []

    if stats.projects:
        shown = ", ".join(stats.projects[:3])
        more = (
            f" (+{len(stats.projects) - 3} more)" if len(stats.projects) > 3 else ""
        )
        facts.append(f"Projects touched: {shown}{more}")

    if stats.tool_counts:
        top = ", ".join(
            f"{name} ×{count}" for name, count in list(stats.tool_counts.items())[:5]
        )
        facts.append(f"Tool calls: {top}")

    if stats.first_activity and stats.last_activity:
        start = stats.first_activity.astimezone(tz)
        end = stats.last_activity.astimezone(tz)
        span_minutes = int((end - start).total_seconds() // 60)
        facts.append(
            f"Active {start:%H:%M}–{end:%H:%M} local "
            f"({span_minutes // 60}h{span_minutes % 60:02d}m span)"
        )
        if stats.latest_late_activity is not None:
            late = stats.latest_late_activity.astimezone(tz)
            facts.append(
                f"Worked past midnight — latest small-hours turn at "
                f"{late:%H:%M} local (an observation, not a claim about how "
                "you felt)"
            )

    facts.append(
        f"{stats.sessions} session(s), {stats.turns} turns "
        f"({stats.user_turns} from you), ~{stats.tokens} tokens"
    )
    if stats.git_branches:
        facts.append(f"Git branches: {', '.join(stats.git_branches[:5])}")
    facts.append(f"Sources: {', '.join(stats.sources)}")

    summary = _summary_line(stats)
    return DayDigest(date=date, summary=summary, facts=facts, stats=stats)


def _summary_line(stats: DayStats) -> str:
    """A factual headline, template-filled — no narration.

    Prose would need M2; this states the shape of the day instead.
    """
    if stats.turns == 0:
        return "No agent activity recorded."
    project = stats.projects[0] if stats.projects else "unknown project"
    tool = next(iter(stats.tool_counts), None)
    tool_part = f", mostly {tool}" if tool else ""
    return (
        f"{stats.turns} turns across {stats.sessions} session(s), "
        f"led by {project}{tool_part}."
    )


def digest_for_period(
    turns: list[ParsedTurn], tz_name: str
) -> list[DayDigest]:
    tz = ZoneInfo(tz_name)
    return [
        build_digest(date, day_turns, tz)
        for date, day_turns in sorted(group_by_day(turns, tz).items())
    ]


def format_digest(digest: DayDigest) -> str:
    lines = [f"{digest.date} — {digest.summary}"]
    lines.extend(f"  · {fact}" for fact in digest.facts)
    return "\n".join(lines)


# A session shorter than this has nothing to narrate: a single question and a
# one-line answer would produce a card that says less than its own metadata, and
# as training data it teaches the model to pad.
MIN_SESSION_TURNS = 6


def group_by_session(turns: list[ParsedTurn]) -> dict[str, list[ParsedTurn]]:
    """Bucket turns by their source session."""
    sessions: dict[str, list[ParsedTurn]] = {}
    for turn in turns:
        sessions.setdefault(turn.session_id, []).append(turn)
    return sessions


def build_session_digest(
    session_id: str, turns: list[ParsedTurn], tz: ZoneInfo
) -> DayDigest:
    """A card for one session.

    Reuses the day builder so a session card and a day card cannot drift apart
    in wording or in what they count. `date` is the session's own local start
    date, which is what the card is filed under.
    """
    start = min(turn.created_at for turn in turns)
    date = start.astimezone(tz).date().isoformat()
    digest = build_digest(date, turns, tz)

    # The day headline counts sessions, which reads as nonsense on a card that
    # IS one session ("36 turns across 1 session(s)"). Session cards get their
    # own line: a short id to tell them apart, then what the session did.
    stats = digest.stats
    short_id = session_id.split(":")[-1][:8]
    project = stats.projects[0] if stats.projects else "unknown project"
    tool = next(iter(stats.tool_counts), None)
    local = start.astimezone(tz)
    summary = (
        f"session {short_id} — {stats.turns} turns in {project} "
        f"from {local:%H:%M}"
        + (f", mostly {tool}." if tool else ".")
    )
    return digest.model_copy(update={"summary": summary})


def session_digests(
    turns: list[ParsedTurn],
    tz: ZoneInfo,
    min_turns: int = MIN_SESSION_TURNS,
) -> dict[str, DayDigest]:
    """One card per session that clears the turn floor."""
    return {
        session_id: build_session_digest(session_id, session_turns, tz)
        for session_id, session_turns in group_by_session(turns).items()
        if len(session_turns) >= min_turns
    }


def day_bounds(date: str, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """The UTC instants bracketing one local calendar day."""
    midnight = datetime.fromisoformat(f"{date}T00:00:00").replace(tzinfo=tz)
    return midnight, midnight + timedelta(days=1)


def rows_to_turns(rows: Iterable[Mapping[str, Any]]) -> list[ParsedTurn]:
    """Rebuild ParsedTurns from stored T1 rows.

    Only the fields the digest reads are reconstructed — the text is not needed
    to count a day, so it is left out of the query entirely.
    """
    turns: list[ParsedTurn] = []
    for row in rows:
        tool_names = row["tool_names"]
        if isinstance(tool_names, str):
            tool_names = json.loads(tool_names)
        source = row["tool"] or "claude-code"
        turns.append(
            ParsedTurn(
                source=source,  # type: ignore[arg-type]
                session_id=row["session_id"],
                source_key="",
                role=row["role"],
                text="",
                created_at=row["created_at"],
                token_count=row["token_count"] or 0,
                tool_names=list(tool_names or []),
                project=row["project"],
                git_branch=row["git_branch"],
            )
        )
    return turns
