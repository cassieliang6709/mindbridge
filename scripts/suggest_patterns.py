"""Generate Pattern Candidate suggestions from T2 cards.

This is deterministic: it scans existing day cards only and looks for repeated
factual observations. It never writes reflective T3 directly, it only creates
Pattern Candidates (pending) for later confirmation.

中文说明：从 T2 日卡中确定性地生成 Pattern Candidate。它只查找重复出现的事实性
观察,绝不会直接写入带反思性质的 T3;生成的候选仍需后续确认。
"""

from __future__ import annotations

import argparse
import asyncio
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

from api.models import PatternCandidateCreate, PatternEvidence
from api.service import MemoryService
from api.settings import get_settings

_PROJECT_RE = re.compile(r"^Projects touched:")
_TOOL_RE = re.compile(r"^Tool calls:")
_BRANCH_RE = re.compile(r"^Git branches:")
_SOURCE_RE = re.compile(r"^Sources:")
_PAST_MIDNIGHT_RE = re.compile(r"Worked past midnight")
_TOOL_ITEM_RE = re.compile(r"(?P<name>[^,×]+)\s*×(?P<count>\d+)")
_SINCE_RE = re.compile(r"^(?:(?P<all>all)|(?P<num>\d+)(?P<unit>[hdw]))$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class _Evidence:
    """One pattern-relevant observation extracted from a T2 fact line.

    中文：从 T2 日卡事实行提取的一条模式相关观察。
    """
    period: str
    source_id: str
    summary: str
    context: str


def _normalise(text: str) -> str:
    """Lowercase text and collapse non-alphanumeric characters to spaces.

    中文：把文本转小写,并将非字母数字字符合并为空格,便于稳定比较。

    Args:
        text: Raw text to normalize. 待归一化的原始文本。

    Returns:
        Normalized, trimmed text. 归一化且去掉首尾空白后的文本。
    """
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def parse_period_for_sort(period: str) -> datetime | None:
    """Parse a day-card period into a sortable UTC datetime.

    中文：把日卡日期解析为可排序的 UTC datetime。

    Args:
        period: ``YYYY-MM-DD`` period string. ``YYYY-MM-DD`` 格式的日期。

    Returns:
        A timezone-aware datetime, or None for an invalid date.
        带时区的 datetime;日期非法时返回 None。
    """
    if not _DATE_RE.match(period):
        return None
    try:
        return datetime.strptime(period, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_since(value: str | None) -> datetime | None:
    """Parse a ``--since`` value such as ``7d`` or ``48h`` into a cutoff.

    中文：把 ``7d``、``48h`` 等 ``--since`` 参数解析为截止时间。

    Args:
        value: Raw ``--since`` value, or None. 原始参数值,或 None。

    Returns:
        None for no cutoff, otherwise a UTC cutoff datetime.
        不限时间时返回 None;否则返回 UTC 截止时间。

    Raises:
        ValueError: If the value has an unsupported format. 参数格式不受支持时抛出。
    """
    if value is None or value == "all":
        return None
    match = _SINCE_RE.match(value)
    if not match:
        raise ValueError(f"--since expects e.g. 7d, 14h, 2w or all, got {value!r}")
    if match.group("all"):
        return None
    amount = int(match.group("num"))
    unit = match.group("unit")
    delta = {"h": timedelta(hours=amount), "d": timedelta(days=amount), "w": timedelta(weeks=amount)}[unit]
    return datetime.now(timezone.utc) - delta


def _split_list_field(value: str) -> list[str]:
    """Split a summarized fact field into cleaned item strings.

    中文：把汇总后的事实字段拆成清理过的条目字符串。

    Args:
        value: A possibly labeled, comma-separated fact line. 可能带标签的
            逗号分隔事实行。

    Returns:
        Clean non-empty items without the ``(+N more)`` suffix.
        去掉 ``(+N more)`` 尾巴后的非空条目。
    """
    cleaned = value.split(":", 1)[1] if ":" in value else value
    cleaned = cleaned.strip()
    if not cleaned:
        return []
    bits = [part.strip() for part in cleaned.split(",")]
    out: list[str] = []
    for part in bits:
        part = part.strip()
        if not part:
            continue
        # Remove the '+N more' tail from deterministic summaries.
        # 中文：移除确定性汇总为了简洁附加的 "+N more" 尾巴。
        part = re.sub(r"\(\+\d+\s+more\)", "", part).strip()
        if part:
            out.append(part)
    return out


def extract_events_for_day_card(card) -> list[_Evidence]:
    """Extract pattern-relevant evidence events from one T2 day card.

    中文：从一张 T2 日卡中提取模式相关的证据事件。

    Args:
        card: Day-card-like object with id, period, and fact fields.
            带有 id、period 和事实字段的日卡对象。

    Returns:
        Evidence extracted from this card, possibly empty. 提取到的证据,
        可能为空。
    """
    events: list[_Evidence] = []
    card_id = card.id
    period = card.period

    for idx, fact in enumerate(card.developer_behavior_facts):
        source = f"t2:{card_id}:{idx}"

        if _PROJECT_RE.match(fact):
            for project in _split_list_field(fact):
                norm = _normalise(project)
                if not norm:
                    continue
                events.append(
                    _Evidence(
                        period=period,
                        source_id=source,
                        summary=f"Project seen on {period}: {project}",
                        context=f"project:{norm}",
                    )
                )
            continue

        if _TOOL_RE.match(fact):
            tools_text = fact.split(":", 1)[1] if ":" in fact else ""
            for match in _TOOL_ITEM_RE.finditer(tools_text):
                tool = match.group("name").strip()
                norm = _normalise(tool)
                if not norm:
                    continue
                count = int(match.group("count"))
                events.append(
                    _Evidence(
                        period=period,
                        source_id=source,
                        summary=f"Tool usage on {period}: {tool} ×{count}",
                        context=f"tool:{norm}",
                    )
                )
            continue

        if _BRANCH_RE.match(fact):
            for branch in _split_list_field(fact):
                norm = _normalise(branch)
                if not norm:
                    continue
                events.append(
                    _Evidence(
                        period=period,
                        source_id=source,
                        summary=f"Git branch seen on {period}: {branch}",
                        context=f"git_branch:{norm}",
                    )
                )
            continue

        if _SOURCE_RE.match(fact):
            for source_value in _split_list_field(fact):
                norm = _normalise(source_value)
                if not norm:
                    continue
                events.append(
                    _Evidence(
                        period=period,
                        source_id=source,
                        summary=f"Source seen on {period}: {source_value}",
                        context=f"source:{norm}",
                    )
                )
            continue

        if _PAST_MIDNIGHT_RE.search(fact):
            events.append(
                _Evidence(
                    period=period,
                    source_id=source,
                    summary=f"Observed past-midnight work on {period}",
                    context="pattern:late_hours",
                )
            )

    return events


def make_candidates(
    cards: Iterable[object],
    *,
    min_observations: int,
    min_dates: int,
    max_counter_evidence: int = 2,
    max_supporting: int = 10,
) -> list[PatternCandidateCreate]:
    """Build candidates from evidence groups that meet observation thresholds.

    中文：将达到观察次数和日期数阈值的证据分组构造成候选。

    Args:
        cards: Day cards to scan. 要扫描的日卡。
        min_observations: Minimum evidence events per candidate. 每个候选所需的
            最少证据事件数。
        min_dates: Minimum distinct dates per candidate. 每个候选所需的最少不同日期。
        max_counter_evidence: Maximum counter-evidence items retained.
            保留的最大反例条数。
        max_supporting: Maximum supporting-evidence items retained.
            保留的最大支持证据条数。

    Returns:
        Candidates ordered by evidence count and description. 按证据数和描述排序的候选。
    """
    grouped: dict[str, list[_Evidence]] = defaultdict(list)

    for card in cards:
        grouped_card_events = extract_events_for_day_card(card)
        for event in grouped_card_events:
            grouped[event.context].append(event)

    candidates: list[PatternCandidateCreate] = []
    for context, events in grouped.items():
        if len(events) < min_observations:
            continue
        dates = {event.period for event in events}
        if len(dates) < min_dates:
            continue

        # Sort by period so every run is replayable.
        events_sorted = sorted(events, key=lambda e: parse_period_for_sort(e.period) or datetime.min)
        supporting = [
            PatternEvidence(
                source_date=parsed.date(),
                summary=e.summary,
                source_id=e.source_id,
            )
            for e in events_sorted
            for parsed in [parse_period_for_sort(e.period)]
            if parsed is not None
        ][:max_supporting]

        if len(supporting) < 3:
            continue

        source_count = len({e.source_id.split(":")[0] for e in events_sorted})
        date_count = len(dates)
        confidence = min(
            0.95,
            0.55
            + 0.07 * min(len(supporting) - 3, 4)
            + 0.10 * min(date_count - 2, 3)
            + (0.05 if source_count > 1 else 0.0),
        )

        description = f"Observed repeated signal: {context.replace('_', ' ')} over time."
        if context.startswith("project:"):
            label = context.split(":", 1)[1].replace("-", " ")
            description = f"Repeated project work tied to {label}."
        elif context.startswith("tool:"):
            label = context.split(":", 1)[1].replace("-", " ")
            description = f"Frequent tool preference around {label}."
        elif context.startswith("git_branch:"):
            label = context.split(":", 1)[1].replace("-", " ")
            description = f"Recurring branch context: {label}."

        # Counter-evidence collection is not implemented; candidates must not
        # pretend the absence of a collector is evidence against the pattern.
        # 中文：反例收集尚未实现;不能把“没有收集器”伪装成支持或反对模式的证据。
        counter = []

        candidates.append(
            PatternCandidateCreate(
                description=description,
                supporting_evidence=supporting,
                counter_evidence=counter[:max_counter_evidence],
                contexts=["t2", context.split(":", 1)[0]],
                confidence=confidence,
            )
        )

    return sorted(candidates, key=lambda c: (-len(c.supporting_evidence), c.description))


def _parse_card(card: object, *, since: datetime | None) -> bool:
    """Return whether a card has a valid period within the requested range.

    中文：判断日卡日期是否合法且落在请求的时间范围内。

    Args:
        card: Object with a period attribute. 带有 period 属性的对象。
        since: UTC cutoff, or None for all valid dates. UTC 截止时间;None 表示不限。

    Returns:
        True when the date parses and is not before ``since``.
        日期可解析且不早于 ``since`` 时返回 True。
    """
    period = getattr(card, "period", "")
    parsed = parse_period_for_sort(period)
    if since is None:
        return parsed is not None
    return parsed is not None and parsed >= since


def _normalize_existing_description(candidate_desc: str) -> str:
    """Normalize an existing candidate description for duplicate matching.

    中文：归一化已有候选描述,用于重复匹配。

    Args:
        candidate_desc: Candidate description. 候选描述。

    Returns:
        Normalized description. 归一化后的描述。
    """
    return _normalise(candidate_desc)


async def run(args: argparse.Namespace) -> int:
    """Build Pattern Candidates from T2 cards and optionally persist them.

    中文：根据 T2 日卡生成 Pattern Candidate,并按需持久化。

    Args:
        args: Parsed CLI flags. 解析后的命令行参数。

    Returns:
        Zero when candidates were considered, or one when no cards matched.
        已处理候选时返回 0;没有匹配日卡时返回 1。
    """
    settings = get_settings()
    service = await MemoryService.start(settings)

    try:
        since = parse_since(args.since)
        cards = await service.list_summaries(limit=args.card_limit, scope="day")
        cards = [c for c in cards if _parse_card(c, since=since)]

        if not cards:
            print("No matching T2 day cards found.")
            return 1

        existing = await service.list_patterns(status=None, limit=500)
        existing_signatures = {
            _normalize_existing_description(cand.description) for cand in existing
        }

        candidates = []
        for candidate in make_candidates(
            cards,
            min_observations=args.min_observations,
            min_dates=args.min_dates,
            max_counter_evidence=args.max_counter_evidence,
            max_supporting=args.max_supporting,
        ):
            if _normalize_existing_description(candidate.description) in existing_signatures:
                continue
            candidates.append(candidate)

        if not candidates:
            print("No pattern candidate met the deterministic gate.")
            return 0

        print(
            f"found {len(candidates)} candidate(s) from {len(cards)} day card(s) "
            f"(limit={args.card_limit})"
        )
        for idx, candidate in enumerate(candidates[: args.limit], start=1):
            print(f"\n[{idx}] {candidate.description}")
            print(f"    confidence={candidate.confidence:.2f}")
            print(f"    contexts={candidate.contexts}")
            print(f"    evidence={len(candidate.supporting_evidence)}")
            for evidence in candidate.supporting_evidence:
                print(f"      - {evidence.source_date} · {evidence.summary}")

        if not args.apply:
            print("\nDRY RUN — pass --apply to write Pattern Candidates.")
            return 0

        created = 0
        for candidate in candidates[: args.limit]:
            created_obj = await service.propose_pattern(candidate)
            created += 1
            print(f"CREATE  #{created_obj.id}  {created_obj.description}")

        print(f"\ncreated {created} candidate(s)")
        return 0
    finally:
        await service.close()


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser for pattern suggestions.

    中文：构建模式建议脚本的命令行解析器。

    Returns:
        Configured argument parser. 已配置的参数解析器。
    """
    parser = argparse.ArgumentParser(
        prog="python -m scripts.suggest_patterns",
        description="Generate deterministic Pattern Candidates from T2 day cards.",
    )
    parser.add_argument(
        "--since",
        default="30d",
        help="Only cards newer than this: 24h, 7d, 2w, or all (default: 30d).",
    )
    parser.add_argument(
        "--card-limit",
        type=int,
        default=365,
        help="How many day cards to scan (default 365).",
    )
    parser.add_argument(
        "--min-observations",
        type=int,
        default=3,
        help="Minimum repeated evidence observations needed for a candidate.",
    )
    parser.add_argument(
        "--min-dates",
        type=int,
        default=2,
        help="Minimum distinct dates needed for a candidate.",
    )
    parser.add_argument(
        "--max-supporting",
        type=int,
        default=10,
        help="Max supporting evidence slots per candidate.",
    )
    parser.add_argument(
        "--max-counter-evidence",
        type=int,
        default=0,
        help="Max counter-evidence slots (prototype keeps this at 0).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=30,
        help="Max candidates to print/apply per run.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Create Pattern Candidates in Postgres (default dry-run).",
    )
    return parser


def main() -> int:
    """Parse, validate, and run the pattern-suggestion workflow.

    中文：解析并校验参数,然后运行模式建议流程。

    Returns:
        Process exit code. 进程退出码。
    """
    parser = build_parser()
    args = parser.parse_args()
    if args.max_counter_evidence < 0:
        parser.error("--max-counter-evidence must be >=0")
    if args.min_observations < 1:
        parser.error("--min-observations must be >=1")
    if args.min_dates < 1:
        parser.error("--min-dates must be >=1")
    try:
        return asyncio.run(run(args))
    except ValueError as exc:
        print(f"invalid argument: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
