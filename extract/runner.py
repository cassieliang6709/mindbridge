"""M2 stage one CLI: turn a day's turns into prose + preferences.

    # show the exact prompt and what it would cost, call nothing
    python -m extract.runner --date 2026-08-04 --dry-run

    # extract with the signed-in Claude Code CLI (no API key)
    python -m extract.runner --date 2026-08-04 --provider claude-cli \
        --send-to-provider

    # extract with the fine-tuned MLX adapter served on this Mac (fully local)
    python -m extract.runner --date 2026-08-04 --provider mlx

    # every card that has no narrative yet, newest first
    python -m extract.runner --missing --limit 5

    # compliance over everything extracted so far
    python -m extract.runner --stats

OpenAI/Gemini keys are read from the environment and never passed on the command
line. The claude-cli provider instead uses Claude Code's existing sign-in.

中文说明：这是 M2 第一阶段的命令行入口，把 T2 卡片背后的事实和对话转换成
叙事与候选偏好。托管服务商需要显式授权发送数据；MLX 在本机运行。dry-run
会逐字展示将发送的内容，并且不会调用模型或写入数据库。
"""

from __future__ import annotations

import argparse
from collections import Counter
import asyncio
import json
import logging
import os
import sys
from zoneinfo import ZoneInfo

from api.memory_candidates import MemoryCandidateCreate
from api.models import NarrativeUpdate
from api.service import MemoryService
from api.settings import get_settings
from ingest.digest import day_bounds

from .dataset import DatasetWriter, compliance_stats
from .pipeline import ExtractionResult, extract_day
from .prompts import build_day_input
from .providers import build_provider

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("mindbridge.extract")

# Rough public prices per 1M tokens, only used for the --dry-run estimate. They
# drift, so the number is labelled an estimate wherever it is printed.
# 中文：这些公开价格只用于 dry-run 估算且可能变化，因此输出始终标为估算值。
_PRICES = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gemini-2.5-flash": (0.30, 2.50),
}


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """Estimate request cost when the model has a configured public price.

    中文：模型存在已配置的公开价格时，按输入/输出 token 数估算美元费用。

    Args:
        model: Model name used as the price-table key.
        input_tokens: Estimated input-token count.
        output_tokens: Estimated output-token count.

    Returns:
        Estimated USD cost, or None when the model is not in the table.
    """
    price = _PRICES.get(model)
    if price is None:
        return None
    return input_tokens / 1e6 * price[0] + output_tokens / 1e6 * price[1]


async def _load_target(
    service: MemoryService, date: str, session_id: str | None, tz_name: str
):
    """Fetch the facts and turns behind one card.

    A day card sees every turn in the local day; a session card sees only that
    session's, so the prose describes the session rather than the whole day.

    中文：日卡读取本地日期内的全部发言；会话卡只读取指定会话。项目名取这些
    发言中最常见的 project，供模型判断偏好是否只在该项目内成立。

    Args:
        service: Running memory service used for reads.
        date: Local date in YYYY-MM-DD form.
        session_id: Session scope, or None for a whole-day card.
        tz_name: IANA timezone used to calculate day boundaries.

    Returns:
        ``(card, facts, turns, project)`` for the requested scope.
    """
    tz = ZoneInfo(tz_name)
    start, end = day_bounds(date, tz)
    card = await service.summaries.get(date, session_id)
    rows = await service.turns.list_between(start, end, limit=4000)
    if session_id is not None:
        rows = [row for row in rows if row.session_id == session_id]
    turns = [(row.role, row.content) for row in rows]
    facts = card.developer_behavior_facts if card else []
    names = Counter(row.project for row in rows if row.project)
    project = names.most_common(1)[0][0] if names else None
    return card, facts, turns, project


async def run(args: argparse.Namespace) -> int:
    """Run stats, dry-run, or extraction mode for the parsed CLI arguments.

    中文：根据命令行参数运行统计、dry-run 或真实抽取流程。

    Args:
        args: Namespace produced by build_parser().

    Returns:
        Process exit code; zero means the selected operation completed.
    """
    settings = get_settings()
    service = await MemoryService.start(settings)
    writer = DatasetWriter()

    try:
        if args.stats:
            print(json.dumps(compliance_stats(), indent=2, ensure_ascii=False))
            return 0

        # Work items are (period, session_id); session_id None means the day card.
        # 中文：待处理项为 (period, session_id)；session_id=None 表示日卡。
        targets: list[tuple[str, str | None]] = []
        if args.date:
            targets = [(date, None) for date in args.date]
        elif args.missing:
            cards = await service.list_summaries(limit=365, scope=args.scope)
            targets = [
                (card.period, card.session_id)
                for card in cards
                if not card.narrative
            ][: args.limit]
        else:
            print("give --date DATE... or --missing", file=sys.stderr)
            return 2

        if not targets:
            print(
                f"nothing to do: every {args.scope} card already has a narrative"
            )
            return 0

        provider = None
        if not args.dry_run:
            # Path A and Path B never leave the machine. Hosted M2 providers do,
            # so they are gated on an explicit flag rather than a default.
            # Excerpts of real conversations — file paths, project names,
            # whatever was discussed — go to a third party and may be retained
            # under its policy. MLX instead serves the tuned model locally.
            # 中文：Path A 与 Path B 不离开本机；托管 M2 会发送真实对话片段，
            # 因此必须显式授权。MLX 则在本机提供模型服务。
            if args.provider != "mlx" and not args.send_to_provider:
                print(
                    "REFUSING TO SEND.\n\n"
                    f"Extraction would send excerpts of your transcripts to the "
                    f"{args.provider} hosted provider. Everything else in MindBridge stays "
                    "local; this step does not.\n\n"
                    "Inspect exactly what would be sent:\n"
                    "    --dry-run\n\n"
                    "Then, if you accept that:\n"
                    "    --send-to-provider\n",
                    file=sys.stderr,
                )
                return 4
            key = None
            if args.provider == "openai":
                key = settings.openai_api_key
            elif args.provider == "gemini":
                key = settings.gemini_api_key

            if args.provider in {"openai", "gemini"} and not key:
                env = (
                    "MINDBRIDGE_OPENAI_API_KEY"
                    if args.provider == "openai"
                    else "MINDBRIDGE_GEMINI_API_KEY"
                )
                print(
                    f"no API key found. Set {env} in your environment or .env, "
                    "then re-run. Use --dry-run to inspect the prompt without a "
                    "key.",
                    file=sys.stderr,
                )
                return 3
            provider = build_provider(
                args.provider,
                key,
                args.model or (settings.mlx_model if args.provider == "mlx" else None),
                base_url=args.base_url or (
                    settings.mlx_url if args.provider == "mlx" else None
                ),
                timeout=(
                    settings.mlx_timeout_seconds if args.provider == "mlx" else None
                ),
                wire_model=args.wire_model,
            )

        default_models = {
            "openai": "gpt-4o-mini",
            "gemini": "gemini-2.5-flash",
            "claude-cli": "sonnet",
            "mlx": settings.mlx_model,
        }
        model_name = args.model or default_models[args.provider]
        estimated_input = 0
        results: list[ExtractionResult] = []

        for date, session_id in targets:
            label = date if session_id is None else f"{date} {session_id[-12:]}"
            card, facts, turns, project = await _load_target(
                service, date, session_id, args.timezone
            )
            if card is None:
                print(f"{label}: no T2 card — run ingest first", file=sys.stderr)
                continue
            if card.narrative and not args.force:
                print(f"{label}: already has a narrative (use --force to redo)")
                continue

            max_input_tokens = args.max_input_tokens
            if max_input_tokens is None:
                max_input_tokens = 4_000 if args.provider == "mlx" else 12_000
            day = build_day_input(
                date, facts, turns, max_input_tokens=max_input_tokens,
                project=project,
            )
            estimated_input += day.estimated_tokens()

            if args.dry_run:
                print("=" * 72)
                print(day.render())
                print("-" * 72)
                print(
                    f"{label}: {day.sampled_turns}/{day.total_turns} turns kept, "
                    f"~{day.estimated_tokens()} input tokens"
                )
                continue

            assert provider is not None
            result = await extract_day(
                provider,
                day,
                max_attempts=args.max_attempts,
                session_id=session_id,
            )
            results.append(result)
            captured = writer.append(result)

            if not result.ok:
                print(
                    f"{label}: FAILED after {len(result.attempts)} attempt(s); "
                    "card left as the rule-based version"
                )
                continue

            draft = result.draft
            assert draft is not None
            await service.summaries.set_narrative(
                NarrativeUpdate(
                    period=date,
                    session_id=session_id,
                    narrative=draft.narrative,
                    highlights=draft.highlights,
                    open_threads=draft.open_threads,
                    generated_by=f"{result.provider}:{result.model}",
                    model=result.model,
                )
            )

            staged = 0
            if not args.no_preferences:
                for preference in draft.preferences:
                    if preference.confidence < args.min_confidence:
                        continue
                    candidate = await service.propose_memory_candidate(
                        MemoryCandidateCreate(
                            content=preference.content,
                            namespace="operational",
                            category=preference.category,
                            project=preference.project,
                            confidence=preference.confidence,
                            evidence=preference.evidence,
                            source_period=date,
                            source_session_id=session_id,
                            source_summary_id=card.id,
                            source_receipt={
                                "provider": result.provider,
                                "model": result.model,
                                "prompt_version": result.prompt_version,
                                "attempts": len(result.attempts),
                                "first_attempt_valid": result.first_attempt_valid,
                            },
                        )
                    )
                    staged += 1
                    print(
                        f"  candidate {candidate.id}: {preference.content} "
                        f"(confidence {preference.confidence:.2f})"
                    )

            print(
                f"{label}: ok in {len(result.attempts)} attempt(s), "
                f"{len(draft.highlights)} highlight(s), {staged} candidate(s), "
                f"dataset={'+1' if captured else 'skipped'}"
            )

        if args.dry_run:
            cost = _estimate_cost(model_name, estimated_input, len(targets) * 500)
            print("=" * 72)
            print(
                f"DRY RUN: {len(targets)} card(s), ~{estimated_input} input tokens "
                f"total for {model_name}"
            )
            if cost is not None:
                print(f"estimated cost: ~${cost:.4f} (list prices, may be stale)")
            print("Nothing was sent and nothing was written.")
            return 0

        if results:
            valid_first = sum(1 for r in results if r.first_attempt_valid)
            print()
            print(
                f"=== {len(results)} day(s): {valid_first} valid on first attempt "
                f"({valid_first / len(results):.0%}), "
                f"{sum(1 for r in results if r.ok)} eventually valid"
            )
            print("Run --stats for the cumulative figures behind the eval.")
        return 0
    finally:
        await service.close()


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser for the extraction runner.

    中文：构建抽取命令的 argparse 解析器。

    Returns:
        A configured ArgumentParser.
    """
    parser = argparse.ArgumentParser(prog="python -m extract.runner")
    parser.add_argument("--date", nargs="+", help="Local dates, YYYY-MM-DD.")
    parser.add_argument(
        "--missing",
        action="store_true",
        help="Process cards that have no narrative yet, newest first.",
    )
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument(
        "--scope",
        choices=["day", "session", "all"],
        default="day",
        help=(
            "Which cards --missing walks. 'session' is the volume play: one "
            "card per session instead of one per day."
        ),
    )
    parser.add_argument(
        "--provider",
        choices=["openai", "gemini", "claude-cli", "mlx"],
        default="openai",
    )
    parser.add_argument("--model", default=None)
    parser.add_argument(
        "--wire-model",
        default=None,
        help=(
            "Model name to put in the request body for --provider mlx. "
            "Defaults to mlx_lm.server's 'default_model' alias; pass the real "
            "name for servers that route by it, e.g. ollama."
        ),
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="OpenAI-compatible base URL for --provider mlx.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the prompt and a cost estimate; call nothing, write nothing.",
    )
    parser.add_argument(
        "--send-to-provider",
        action="store_true",
        help=(
            "Required to actually call a hosted provider. Transcript excerpts "
            "leave your machine when you pass this."
        ),
    )
    parser.add_argument(
        "--force", action="store_true", help="Re-extract a day that already has prose."
    )
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument(
        "--max-input-tokens",
        type=int,
        default=None,
        help="Prompt budget. Defaults to 4,000 for MLX and 12,000 otherwise.",
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.6,
        help="Skip extracted preferences below this confidence (default 0.6).",
    )
    parser.add_argument(
        "--no-preferences",
        action="store_true",
        help="Write the narrative but do not touch T3.",
    )
    parser.add_argument("--timezone", default="America/New_York")
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Print cumulative schema-compliance figures and exit.",
    )
    return parser


def main() -> None:
    """Parse arguments, mirror provider keys, and run the async entry point.

    中文：解析参数、兼容常见服务商密钥变量，然后运行异步主流程。
    """
    args = build_parser().parse_args()
    # Accept the bare provider names too, so a key already exported for other
    # tooling works without being copied into a second variable.
    # 中文：兼容服务商原生环境变量，避免为 MindBridge 重复保存同一密钥。
    for src, dest in (
        ("OPENAI_API_KEY", "MINDBRIDGE_OPENAI_API_KEY"),
        ("GEMINI_API_KEY", "MINDBRIDGE_GEMINI_API_KEY"),
    ):
        if os.environ.get(src) and not os.environ.get(dest):
            os.environ[dest] = os.environ[src]
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
