"""Path A CLI: read local agent transcripts into T1, and write T2 day cards.

    # see what would happen, touch nothing
    python -m ingest.runner --dry-run --since 7d

    # ingest everything new since the last run
    python -m ingest.runner

    # re-read from scratch (turns are keyed, so this cannot duplicate)
    python -m ingest.runner --full

    python -m ingest.runner --status

中文说明：Path A 的命令行入口——读取本地 AI 编程工具的转录文件，写入 T1
表，并生成 T2 日卡片。
    # 只看看会发生什么，不实际改动任何数据
    python -m ingest.runner --dry-run --since 7d

    # 摄取上次运行之后新增的内容
    python -m ingest.runner

    # 从头重新读取（对话轮有唯一键，所以不会产生重复数据）
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

from api.memory import IngestRow
from api.models import SummaryCardCreate
from api.service import MemoryService
from api.settings import get_settings

from . import claude_code, codex_cli
from .cursors import CursorStore
from .digest import (
    MIN_SESSION_TURNS,
    build_digest,
    day_bounds,
    digest_for_period,
    format_digest,
    group_by_day,
    rows_to_turns,
    session_digests,
)
from .models import DayDigest, ParsedTurn, ParseOutcome, SourceKind

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("mindbridge.ingest")

READERS = {
    "claude-code": claude_code,
    "codex-cli": codex_cli,
}

_SINCE_RE = re.compile(r"^(\d+)([dhw])$")


def parse_since(value: str | None) -> datetime | None:
    """Turn a `--since` CLI value like "7d" into an absolute UTC cutoff.

    中文：把类似 "7d" 这样的 `--since` 命令行参数值，转换成一个绝对的 UTC
    时间截止点。

    Args:
        value: Duration string ("24h", "7d", "2w"), "all", or None.
            时长字符串（如 "24h"、"7d"、"2w"）、"all"，或 None。

    Returns:
        The UTC cutoff datetime, or None meaning "no lower bound".
        UTC 截止时间；返回 None 表示"没有下限，全量"。

    Raises:
        argparse.ArgumentTypeError: If value doesn't match the expected pattern.
            如果 value 不符合预期格式。
    """
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
    session_cards: bool,
    min_session_turns: int,
    tz_name: str,
    roots: dict[str, Path],
) -> tuple[list[DayDigest], dict[str, int]]:
    """Read new turns from every source, write them to T1, and rebuild T2 day cards.

    中文：从每个来源读取新的对话轮，写入 T1 表，并重新生成 T2 日卡片。

    Args:
        service: Running MemoryService used for all database access.
            用于所有数据库访问的、已启动的 MemoryService。
        sources: Which readers to run (e.g. claude-code, codex-cli).
            要运行哪些读取器（来源）。
        since: Only consider turns at or after this UTC instant; None means
            no lower bound. 只处理这个 UTC 时刻之后的对话轮；None 表示不设下限。
        dry_run: If True, parse and summarize but write nothing.
            如果为 True，只解析和汇总，不写入任何数据。
        full: If True, ignore saved cursors and re-read every file from byte 0.
            如果为 True，忽略已保存的游标，从每个文件的字节 0 处重新读取。
        include_tool_io: Whether to store tool_result / function_call_output text.
            是否存储工具返回结果的文本内容。
        include_thinking: Whether to store assistant "thinking"/"reasoning" blocks.
            是否存储 assistant 的"思考"/"推理"内容块。
        include_sidechains: Whether to store subagent (sidechain) turns.
            是否存储子代理（sidechain）产生的对话轮。
        write_summaries: Whether to write T2 day (and session) cards at all.
            是否要写入 T2 日卡片（及会话卡片）。
        session_cards: Whether to also write one card per qualifying session.
            是否额外为符合条件的每个会话写一张卡片。
        min_session_turns: Sessions with fewer turns than this get no session card.
            轮数少于这个值的会话不会生成会话卡片。
        tz_name: IANA timezone name used for day boundaries and formatting.
            用于划分日期边界和格式化的 IANA 时区名称。
        roots: Optional override of each source's default transcript directory.
            对各来源默认转录目录的可选覆盖。

    Returns:
        A tuple of (day digests rebuilt or parsed this run, running totals).
        一个 (本次生成/解析出的日卡片列表, 运行总计数字典) 元组。
    """
    cursors = CursorStore(service._pool)  # noqa: SLF001 - same package boundary
    totals = {
        "files_seen": 0,
        "files_read": 0,
        "turns_parsed": 0,
        "turns_new": 0,
        "redactions": 0,
        "malformed": 0,
        "skipped_records": 0,
        "session_cards": 0,
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
            # 中文：如果一个文件最后写入的时间早于本次要求的时间窗口，那它就
            # 不会有新东西可摄取，直接跳过。
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
            # 中文：如果一个文件在过去一分钟内刚被写过，说明它很可能还有回复
            # 在流式写入中；这种情况下，把最后那一组记录留到下一次运行再处理
            # （见 assume_complete 参数），避免把"写了一半"的回复当成完整
            # 数据存进去。
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
                    IngestRow(
                        session_id=f"{turn.source}:{turn.session_id}",
                        role=turn.role,
                        content=turn.text,
                        tool=turn.source,
                        token_count=turn.token_count,
                        created_at=turn.created_at,
                        source_key=turn.source_key,
                        project=turn.project,
                        git_branch=turn.git_branch,
                        tool_names=turn.tool_names,
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

    tz = ZoneInfo(tz_name)

    if dry_run:
        # Nothing was written, so the only thing to describe is what was parsed.
        # 中文：因为什么都没有写入数据库，所以能描述的只有这次解析出来的内容。
        return digest_for_period(all_turns, tz_name), totals

    # Rebuild each touched day from the DATABASE, over the whole local day.
    # Building it from `all_turns` would describe only what this run parsed, so
    # an incremental run would overwrite a full card with a partial one — a
    # nightly job would shrink every card it touched.
    # 中文：重新构建每一个被涉及到的日卡片时，一定要基于数据库、覆盖"整个本地
    # 日"的数据，而不能只用 `all_turns`（这次运行解析出的内容）——否则一次
    # 增量运行就会用"只包含这次新解析内容"的不完整卡片，去覆盖掉之前完整的
    # 日卡片。举例说明这个坑有多真实：曾经把一张 683 轮的日卡片，被一次增量
    # 运行错误地重写成了只有 223 轮的卡片。夜间定时任务如果这样跑，会把它碰
    # 到的每一张卡片都"缩水"。
    dates = sorted(group_by_day(all_turns, tz))
    digests: list[DayDigest] = []
    for date in dates:
        start, end = day_bounds(date, tz)
        rows = await service.turns.rows_for_digest(start, end)
        day_turns = rows_to_turns(rows)
        digest = build_digest(date, day_turns, tz)
        digests.append(digest)
        if write_summaries:
            await service.write_summary(
                SummaryCardCreate(
                    period=digest.date,
                    summary=digest.summary,
                    developer_behavior_facts=digest.facts,
                    session_id=None,
                )
            )

            # Session cards, built from the same day's rows so a session card
            # and its day card always agree. These exist mainly to multiply the
            # extraction targets: one card per day caps the training set at one
            # pair per day, which is far too slow to reach a usable fine-tune.
            # 中文：会话卡片是用同一天的行数据构建的，这样会话卡片和日卡片
            # 的口径永远一致、不会打架。之所以要有会话卡片，主要是为了扩充
            # 抽取目标数量——如果只有日卡片，训练集每天最多只能产出一条
            # 样本，速度太慢，不足以支撑一次可用的微调（fine-tune）。
            if session_cards:
                for session_id, session_digest in session_digests(
                    day_turns, tz, min_session_turns
                ).items():
                    await service.write_summary(
                        SummaryCardCreate(
                            period=session_digest.date,
                            summary=session_digest.summary,
                            developer_behavior_facts=session_digest.facts,
                            session_id=session_id,
                        )
                    )
                    totals["session_cards"] += 1

    return digests, totals


async def show_status(service: MemoryService) -> None:
    """Print per-source ingestion progress for `--status`.

    中文：为 `--status` 命令打印每个来源的摄取进度。

    Args:
        service: Running MemoryService used to reach the cursor store.
            用于访问游标存储的、已启动的 MemoryService。
    """
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
    """Build the `python -m ingest.runner` CLI argument parser.

    中文：构建 `python -m ingest.runner` 命令行工具的参数解析器。

    Returns:
        Configured ArgumentParser with every flag this CLI supports.
        配置好的 ArgumentParser，包含该命令行工具支持的所有参数。
    """
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
        "--no-session-cards",
        action="store_true",
        help="Write only day cards, not one card per session.",
    )
    parser.add_argument(
        "--min-session-turns",
        type=int,
        default=MIN_SESSION_TURNS,
        help=(
            f"Skip sessions shorter than this when writing session cards "
            f"(default {MIN_SESSION_TURNS})."
        ),
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
        "--rebuild-cards",
        metavar="DATE",
        nargs="+",
        help=(
            "Rebuild T2 cards for these local dates (YYYY-MM-DD) from T1, "
            "without reading any transcript. Pass 'all' for every known day. "
            "Use after changing digest rules."
        ),
    )
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
    """Parse CLI args and dispatch to the requested Path A action.

    中文：解析命令行参数，并分发到对应的 Path A 操作（摄取、查看状态、重建
    卡片，或重置游标）。

    Args:
        argv: Argument list to parse; None means use `sys.argv`.
            要解析的参数列表；None 表示使用 `sys.argv`。

    Returns:
        Process exit code: 0 on success, 2 on a CLI argument error.
        进程退出码：0 表示成功，2 表示命令行参数错误。
    """
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
        if args.rebuild_cards:
            tz = ZoneInfo(args.timezone)
            dates = args.rebuild_cards
            if dates == ["all"]:
                # Every day that has turns, so a digest-rule change can be
                # applied to the whole history without re-reading transcripts.
                # 中文：取出所有存在对话轮的日期，这样修改了摘要生成规则之后，
                # 可以对整个历史应用新规则，而不需要重新读取原始转录文件。
                dates = await service.summaries.known_periods()
            rebuilt: list[DayDigest] = []
            sessions_written = 0
            for date in dates:
                start, end = day_bounds(date, tz)
                rows = await service.turns.rows_for_digest(start, end)
                day_turns = rows_to_turns(rows)
                digest = build_digest(date, day_turns, tz)
                await service.write_summary(
                    SummaryCardCreate(
                        period=digest.date,
                        summary=digest.summary,
                        developer_behavior_facts=digest.facts,
                        session_id=None,
                    )
                )
                if not args.no_session_cards:
                    for session_id, session_digest in session_digests(
                        day_turns, tz, args.min_session_turns
                    ).items():
                        await service.write_summary(
                            SummaryCardCreate(
                                period=session_digest.date,
                                summary=session_digest.summary,
                                developer_behavior_facts=session_digest.facts,
                                session_id=session_id,
                            )
                        )
                        sessions_written += 1
                rebuilt.append(digest)
            for digest in rebuilt[-args.limit_days :]:
                print(format_digest(digest))
            print(
                f"\nrebuilt {len(rebuilt)} day card(s) and "
                f"{sessions_written} session card(s) from T1"
            )
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
            session_cards=not args.no_session_cards,
            min_session_turns=args.min_session_turns,
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
        source = "parsed this run" if args.dry_run else "rebuilt from T1"
        print(f"\n=== day cards ({len(digests)} total, showing {len(shown)}, {source})")
        for digest in shown:
            print(format_digest(digest))
        print(
            "\nThese cards are rule-based counts, not model-written prose. "
            "Narrative summaries and preference extraction need M2."
        )
    return 0


def main() -> None:
    """Console-script entry point: run the async CLI and exit with its code.

    中文：控制台脚本入口——运行异步 CLI，并以它返回的状态码退出进程。
    """
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
