"""Generate Pattern Candidate suggestions from T2 cards.

This is deterministic: it scans existing day cards only and looks for repeated
factual observations. It never writes reflective T3 directly, it only creates
Pattern Candidates (pending) for later confirmation.
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
    period: str
    source_id: str
    summary: str
    context: str


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def parse_period_for_sort(period: str) -> datetime | None:
    if not _DATE_RE.match(period):
        return None
    try:
        return datetime.strptime(period, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_since(value: str | None) -> datetime | None:
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
        part = re.sub(r"\(\+\d+\s+more\)", "", part).strip()
        if part:
            out.append(part)
    return out


def extract_events_for_day_card(card) -> list[_Evidence]:
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
    period = getattr(card, "period", "")
    parsed = parse_period_for_sort(period)
    if since is None:
        return parsed is not None
    return parsed is not None and parsed >= since


def _normalize_existing_description(candidate_desc: str) -> str:
    return _normalise(candidate_desc)


async def run(args: argparse.Namespace) -> int:
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
