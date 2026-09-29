"""Turn a day's parsed turns into a T2 card — deterministically.

Every line is computed from counts and timestamps. There is no model call here,
so the card is reproducible and cannot invent a detail the transcript does not
contain. That is a deliberate limit, not an oversight:

- Prose narration of a day ("spent most of today on the retrieval path") and
  extraction of durable preferences ("prefers uv over pip") both need the M2
  local extractor, which is not built.
- Behavioural facts are stated as observations with their evidence attached
  ("last activity 01:12"), never as inferences about mood or state.

中文说明：把某一天已解析的对话轮，以完全确定性的方式转换成一张 T2 卡片。每一
行内容都是从计数和时间戳计算出来的，这里不调用任何模型，所以卡片可以复现，
也不会编造出转录文本里没有的细节。这是刻意的限制，不是疏漏：把一天的活动写
成自然语言叙述、以及提炼出持久的用户偏好，都需要 M2 阶段的本地抽取器（尚未
构建）；行为事实只以"观察结果 + 证据"的形式陈述（例如"最后活动时间 01:12"），
绝不推断情绪或状态。
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
# 中文：本地时间落在这个小时区间内的活动都算作"深夜时段"，只作为带时间戳的
# 客观观察记录下来，而不是一个价值判断。
LATE_HOUR = 0
LATE_HOUR_END = 5


def group_by_day(
    turns: list[ParsedTurn], tz: ZoneInfo
) -> dict[str, list[ParsedTurn]]:
    """Bucket turns by local calendar date.

    Local, not UTC: a day boundary that does not match the user's own midnight
    would split a late-night session across two cards.

    中文：按本地日历日期给对话轮分组。之所以用本地时区而不是 UTC，是因为如果
    "一天"的边界和用户自己的午夜对不上，就会把一次深夜进行的会话硬生生拆成
    两张卡片。

    Args:
        turns: All turns to bucket, from any day. 待分组的所有对话轮（可能跨多天）。
        tz: Local timezone used to compute each turn's calendar date.
            用于计算每一轮所在日历日期的本地时区。

    Returns:
        Mapping from ISO date string to the turns that fell on that local day.
        从 ISO 日期字符串到当天对话轮列表的映射。
    """
    days: dict[str, list[ParsedTurn]] = {}
    for turn in turns:
        key = turn.created_at.astimezone(tz).date().isoformat()
        days.setdefault(key, []).append(turn)
    return days


def build_stats(turns: list[ParsedTurn], tz: ZoneInfo) -> DayStats:
    """Aggregate a list of turns into DayStats counts.

    中文：把一批对话轮聚合成 DayStats 统计数据。

    Args:
        turns: Turns to aggregate, typically all turns for one local day.
            要聚合的对话轮，通常是某一天的全部对话轮。
        tz: Local timezone used for the "small hours" check.
            用于判断"深夜时段"的本地时区。

    Returns:
        A DayStats summarizing sessions, turns, tools, projects and activity
        window. 一份汇总了会话数、对话轮数、工具、项目和活动时间窗口的 DayStats。
    """
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
        # 中文："过了午夜"指的是某一轮自己的本地小时数落在深夜区间。这里记录
        # 的是"落在深夜区间的那些轮次里最晚的一个"的时间戳——早期版本是先按
        # 相对当天的时间刻度排序小时数、再打印当天最后一次活动的时间，结果
        # 04:43 的一次会话会让 20:00 结束的活动被误判成"过了午夜"。
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
    """Build a full DayDigest (summary line plus fact list) for one local day.

    中文：为某一个本地日期构建完整的 DayDigest（一句话摘要 + 事实列表）。

    Args:
        date: ISO date string this digest is filed under. 该卡片所属的 ISO 日期字符串。
        turns: All turns for this day. 这一天的全部对话轮。
        tz: Local timezone for formatting activity windows. 用于格式化活动时间窗口的本地时区。

    Returns:
        A DayDigest with a summary, ordered facts and the underlying stats.
        包含一句话摘要、有序事实列表和底层统计数据的 DayDigest。
    """
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

    中文：一句纯事实性的标题，靠模板填充生成——不含任何叙述性文字。真正的
    自然语言叙述需要 M2 阶段的功能；这里只陈述这一天的"形状"（数据概况）。

    Args:
        stats: Aggregated stats for the day. 这一天的聚合统计数据。

    Returns:
        A one-line, template-filled summary string. 一句模板填充生成的摘要文字。
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
    """Build one DayDigest per local day represented in `turns`.

    中文：为 `turns` 覆盖到的每一个本地日期分别构建一张 DayDigest。

    Args:
        turns: Turns spanning any number of days. 可能跨越多天的对话轮。
        tz_name: IANA timezone name, e.g. "America/New_York". IANA 时区名称。

    Returns:
        DayDigests sorted oldest to newest. 按日期从旧到新排序的 DayDigest 列表。
    """
    tz = ZoneInfo(tz_name)
    return [
        build_digest(date, day_turns, tz)
        for date, day_turns in sorted(group_by_day(turns, tz).items())
    ]


def format_digest(digest: DayDigest) -> str:
    """Render a DayDigest as human-readable text for CLI output.

    中文：把 DayDigest 渲染成给 CLI 输出用的人类可读文本。

    Args:
        digest: The digest to render. 要渲染的卡片。

    Returns:
        A multi-line string: headline then one bullet per fact.
        多行字符串：第一行是标题，后面每行一个事实要点。
    """
    lines = [f"{digest.date} — {digest.summary}"]
    lines.extend(f"  · {fact}" for fact in digest.facts)
    return "\n".join(lines)


# A session shorter than this has nothing to narrate: a single question and a
# one-line answer would produce a card that says less than its own metadata, and
# as training data it teaches the model to pad.
# 中文：短于这个轮数的会话没什么可写的——一问一答式的短会话生成的卡片，信息
# 量还不如它自己的元数据；如果当作训练数据使用，只会教会模型去"注水凑字数"。
MIN_SESSION_TURNS = 6


def group_by_session(turns: list[ParsedTurn]) -> dict[str, list[ParsedTurn]]:
    """Bucket turns by their source session.

    中文：按所属会话（session）给对话轮分组。

    Args:
        turns: Turns to bucket, from any number of sessions.
            待分组的对话轮（可能来自多个会话）。

    Returns:
        Mapping from session_id to the turns belonging to that session.
        从 session_id 到该会话对话轮列表的映射。
    """
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

    中文：某一个会话的卡片。复用日卡片的构建逻辑，这样会话卡片和日卡片在措辞
    和统计口径上就不会走样、产生分歧。`date` 取的是这个会话自己的本地开始
    日期，卡片就归档在这一天下面。

    Args:
        session_id: Identifier of the session this card describes.
            这张卡片对应的会话标识。
        turns: All turns belonging to this session. 属于这个会话的全部对话轮。
        tz: Local timezone for formatting the start time. 用于格式化开始时间的本地时区。

    Returns:
        A DayDigest whose summary line describes this one session.
        一份 summary 描述的是这一个会话（而非整天）的 DayDigest。
    """
    start = min(turn.created_at for turn in turns)
    date = start.astimezone(tz).date().isoformat()
    digest = build_digest(date, turns, tz)

    # The day headline counts sessions, which reads as nonsense on a card that
    # IS one session ("36 turns across 1 session(s)"). Session cards get their
    # own line: a short id to tell them apart, then what the session did.
    # 中文：日卡片的标题里会数"会话数"，但放在本身就是"一个会话"的卡片上会显得
    # 很奇怪（比如显示"36 个轮次，跨 1 个会话"）。所以会话卡片用自己的一套
    # 标题格式：一个简短 id 用来区分彼此，然后描述这个会话做了什么。
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
    """One card per session that clears the turn floor.

    中文：为每个达到最少轮数要求的会话生成一张卡片。

    Args:
        turns: Turns spanning any number of sessions. 可能来自多个会话的对话轮。
        tz: Local timezone for formatting each session's card.
            用于格式化每张会话卡片的本地时区。
        min_turns: Sessions with fewer turns than this are skipped (see
            MIN_SESSION_TURNS). 轮数少于这个值的会话会被跳过（参见 MIN_SESSION_TURNS）。

    Returns:
        Mapping from session_id to that session's DayDigest.
        从 session_id 到该会话 DayDigest 的映射。
    """
    return {
        session_id: build_session_digest(session_id, session_turns, tz)
        for session_id, session_turns in group_by_session(turns).items()
        if len(session_turns) >= min_turns
    }


def day_bounds(date: str, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """The UTC instants bracketing one local calendar day.

    中文：某个本地日历日在 UTC 时间下对应的起止时刻。

    Args:
        date: ISO date string, e.g. "2026-08-25". ISO 日期字符串。
        tz: Local timezone the date is interpreted in. 解释该日期所用的本地时区。

    Returns:
        A (start, end) tuple of datetimes bracketing that local day.
        一个 (起始时刻, 结束时刻) 元组，框定该本地日的时间范围。
    """
    midnight = datetime.fromisoformat(f"{date}T00:00:00").replace(tzinfo=tz)
    return midnight, midnight + timedelta(days=1)


def rows_to_turns(rows: Iterable[Mapping[str, Any]]) -> list[ParsedTurn]:
    """Rebuild ParsedTurns from stored T1 rows.

    Only the fields the digest reads are reconstructed — the text is not needed
    to count a day, so it is left out of the query entirely.

    中文：从已存储的 T1 行数据重建出 ParsedTurn 对象。只重建摘要计算真正会用到
    的字段——文本内容对"统计一天的数据"来说是不需要的，所以查询时直接就不
    获取它，这也是为什么这里始终传 text=""。

    Args:
        rows: Rows fetched from the T1 turns table (e.g. via
            `service.turns.rows_for_digest`). 从 T1 turns 表查询出的行（例如
            通过 `service.turns.rows_for_digest`）。

    Returns:
        ParsedTurns reconstructed with digest-relevant fields only.
        只重建了摘要相关字段的 ParsedTurn 列表。
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
