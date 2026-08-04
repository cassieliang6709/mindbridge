"""Path A CLI: read local agent transcripts into T1, and write T2 day cards.

    # see what would happen, touch nothing
    python -m ingest.runner --dry-run --since 7d

    # ingest everything new since the last run
    python -m ingest.runner

    # re-read from scratch (turns are keyed, so this cannot duplicate)
    python -m ingest.runner --full

    python -m ingest.runner --status
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from api.models import SummaryCardCreate
from api.service import MemoryService
from api.settings import get_settings

from . import claude_code, codex_cli
from .cursors import CursorStore
from .digest import digest_for_period, format_digest
from .models import DayDigest, ParsedTurn, ParseOutcome, SourceKind

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("mindbridge.ingest")

READERS = {
    "claude-code": claude_code,
    "codex-cli": codex_cli,
}

_SINCE_RE = re.compile(r"^(\d+)([dhw])$")


def parse_since(value: str | None) -> datetime | None:
    if value is None or value == "all":
        return None
    match = _SINCE_RE.match(value)
    if not match:
        raise argparse.ArgumentTypeError(
            f"--since expects e.g. 7d, 24h, 2w or 'all', got {value!r}"
        )
    amount, unit = int(match.group(1)), match.group(2)
    delta = {"h": timedelta(hours=amount), "d": timedelta(days=amount), "w": timedelta(weeks=amount)}[unit]
    return datetime.now(timezone.utc) - delta


async def ingest(
    service: MemoryService,
    *,
    sources: list[SourceKind],
    since: datetime | None,
    dry_run: bool,
    full: bool,
    include_tool_io: bool,
    include_thinking: bool,
    include_sidechains: bool,
    write_summaries: bool,
    tz_name: str,
    roots: dict[str, Path],
) -> tuple[list[DayDigest], dict[str, int]]:
    cursors = CursorStore(service._pool)  # noqa: SLF001 - same package boundary
    totals = {
        "files_seen": 0,
        "files_read": 0,
        "turns_parsed": 0,
        "turns_new": 0,
        "redactions": 0,
        "malformed": 0,
        "skipped_records": 0,
    }
    all_turns: list[ParsedTurn] = []

    for source in sources:
        reader = READERS[source]
        root = roots.get(source)
        paths = reader.discover(root)
        logger.info("%s: %d transcript file(s) under %s", source, len(paths),
                    root or reader.default_root())

        for path in paths:
            totals["files_seen"] += 1
            try:
                stat = path.stat()
            except OSError:
                continue
            # A file whose last write predates the window has nothing to add.
            if since is not None:
                mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
                if mtime < since:
                    continue

            cursor = await cursors.get(source, str(path))
            start = 0 if full else cursor.bytes_read
            if not full and start >= stat.st_size:
                continue

            # A file written to in the last minute may have a response still
            # streaming; hold its trailing group back for the next run.
            quiet_for = datetime.now(timezone.utc) - datetime.fromtimestamp(
                stat.st_mtime, tz=timezone.utc
            )
            outcome: ParseOutcome = reader.parse_file(
                path,
                start,
                include_tool_io=include_tool_io,
                include_thinking=include_thinking,
                include_sidechains=include_sidechains,
                assume_complete=quiet_for > timedelta(seconds=60),
            )
            totals["files_read"] += 1
            totals["malformed"] += outcome.malformed_lines
            totals["skipped_records"] += outcome.lines_skipped

            turns = outcome.turns
            if since is not None:
                turns = [turn for turn in turns if turn.created_at >= since]

            totals["turns_parsed"] += len(turns)
            totals["redactions"] += sum(turn.redactions for turn in turns)
            all_turns.extend(turns)

            if dry_run:
                continue

            new_rows = await service.turns.append_many(
                [
                    (
                        f"{turn.source}:{turn.session_id}",
                        turn.role,
                        turn.text,
                        turn.source,
                        turn.token_count,
                        turn.created_at,
                        turn.source_key,
                    )
                    for turn in turns
                ]
            )
            totals["turns_new"] += new_rows
            await cursors.save(
                source,
                str(path),
                outcome.bytes_read,
                new_rows,
                turns[-1].source_key if turns else cursor.last_uuid,
            )

    digests = digest_for_period(all_turns, tz_name)

    if write_summaries and not dry_run:
        for digest in digests:
            await service.write_summary(
                SummaryCardCreate(
                    period=digest.date,
                    summary=digest.summary,
                    developer_behavior_facts=digest.facts,
                    session_id=None,
                )
            )

    return digests, totals


async def show_status(service: MemoryService) -> None:
    cursors = CursorStore(service._pool)  # noqa: SLF001
    rows = await cursors.summary()
    if not rows:
        print("no ingestion has run yet")
        return
    print(f"{'source':<14}{'files':>7}{'MB read':>10}{'turns':>8}  last run")
    for row in rows:
        megabytes = (row["bytes_read"] or 0) / 1_048_576
        print(
            f"{row['source']:<14}{row['files']:>7}{megabytes:>10.1f}"
            f"{row['turns']:>8}  {row['last_run']:%Y-%m-%d %H:%M}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ingest.runner",
        description="Ingest local AI coding-tool transcripts into MindBridge.",
    )
    parser.add_argument(
        "--source",
        choices=[*READERS, "all"],
        default="all",
        help="Which transcript source to read (default: all).",
    )
    parser.add_argument(
        "--since",
        default="all",
        help="Only turns newer than this: 24h, 7d, 2w, or 'all' (default).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and print the digest without writing anything.",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Ignore saved cursors and re-read every file from the start.",
    )
    parser.add_argument(
        "--include-tool-io",
        action="store_true",
        help=(
            "Store tool results too. Off by default: they are large and often "
            "contain whole files."
        ),
    )
    parser.add_argument(
        "--include-thinking",
        action="store_true",
        help="Store assistant thinking blocks.",
    )
    parser.add_argument(
        "--include-sidechains",
        action="store_true",
        help="Store subagent turns (Claude Code sidechains).",
    )
    parser.add_argument(
        "--no-summaries",
        action="store_true",
        help="Write T1 turns but skip the T2 day cards.",
    )
    parser.add_argument(
        "--timezone",
        default="America/New_York",
        help=(
            "Local timezone for day boundaries and 'after midnight' facts "
            "(default: America/New_York)."
        ),
    )
    parser.add_argument("--claude-root", type=Path, default=None)
    parser.add_argument("--codex-root", type=Path, default=None)
    parser.add_argument(
        "--status", action="store_true", help="Print ingestion state and exit."
    )
    parser.add_argument(
        "--reset-cursors",
        action="store_true",
        help="Forget all resume points, then exit.",
    )
    parser.add_argument(
        "--limit-days",
        type=int,
        default=7,
        help="How many day digests to print (default: 7, newest last).",
    )
    return parser


async def main_async(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        since = parse_since(args.since)
    except argparse.ArgumentTypeError as error:
        print(error, file=sys.stderr)
        return 2

    try:
        ZoneInfo(args.timezone)
    except Exception:
        print(f"unknown timezone: {args.timezone}", file=sys.stderr)
        return 2

    service = await MemoryService.start(get_settings())
    try:
        if args.status:
            await show_status(service)
            return 0
        if args.reset_cursors:
            removed = await CursorStore(service._pool).reset()  # noqa: SLF001
            print(f"cleared {removed} cursor(s)")
            return 0

        sources: list[SourceKind] = (
            [*READERS] if args.source == "all" else [args.source]  # type: ignore[list-item]
        )
        digests, totals = await ingest(
            service,
            sources=sources,
            since=since,
            dry_run=args.dry_run,
            full=args.full,
            include_tool_io=args.include_tool_io,
            include_thinking=args.include_thinking,
            include_sidechains=args.include_sidechains,
            write_summaries=not args.no_summaries,
            tz_name=args.timezone,
            roots={
                "claude-code": args.claude_root,
                "codex-cli": args.codex_root,
            },
        )
    finally:
        await service.close()

    mode = "DRY RUN — nothing written" if args.dry_run else "written"
    print(f"\n=== Path A ingestion ({mode})")
    for key, value in totals.items():
        print(f"  {key:<16} {value}")
    if totals["redactions"]:
        print(
            f"  note: masked {totals['redactions']} suspected secret(s) before storing"
        )

    if digests:
        shown = digests[-args.limit_days :]
        print(f"\n=== day cards ({len(digests)} total, showing {len(shown)})")
        for digest in shown:
            print(format_digest(digest))
        print(
            "\nThese cards are rule-based counts, not model-written prose. "
            "Narrative summaries and preference extraction need M2."
        )
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
