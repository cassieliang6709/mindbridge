"""`mindbridge` — the operator's entry point.

Four commands, one per thing a person actually does to this system:

    mindbridge mcp                 serve the eleven memory tools over stdio
    mindbridge ingest [--since 3d] Path A: read new transcript bytes into T1/T2
    mindbridge patterns [--apply]  propose Pattern Candidates from T2
    mindbridge doctor              read-only check of the local loop
    mindbridge install claude      register the MCP server with a client
    mindbridge schedule ingest ... manage the nightly LaunchAgents
    mindbridge verify [--plan]     start what is missing and prove the loop runs

Each one is the same work the matching shell script did; the scripts are now
one-line shims so installed launchd plists keep pointing at a stable path.
"""

from __future__ import annotations

import argparse
import os
import sys

from cli._runtime import (
    JobLog,
    compose_up_data_layer,
    docker_running,
    enter_repo_root,
    run_logged,
    venv_python,
)


def _cmd_mcp(args: argparse.Namespace, extra: list[str]) -> int:
    """Serve MCP over stdio.

    The chdir happens before the import chain reads Settings, which loads a
    relative `.env`. Without it a client launching this from its own working
    directory would get the hashing embedder and never be told.
    """
    enter_repo_root()
    from mcp_server.server import main as serve

    serve()
    return 0


def _cmd_ingest(args: argparse.Namespace, extra: list[str]) -> int:
    """Path A. Safe to run at any time and any number of times.

    Turns are keyed by source record and cards are rebuilt from the database, so
    a repeat run cannot duplicate a turn or shrink a card.
    """
    root = enter_repo_root()
    with JobLog("ingest") as log:
        log.line(f"--- ingest starting (repo: {root})")
        if not docker_running():
            # Docker Desktop is down at boot and just after a wake, which is
            # exactly when the nightly job fires. Leave the cursors untouched so
            # the next run picks up where this one would have.
            log.line("SKIPPED: Docker is not running. Nothing was read; cursors unchanged.")
            return 0
        if not compose_up_data_layer(log):
            log.line("FAILED: could not start db/redis. See lines above.")
            return 1

        # --since bounds the file scan by mtime while cursors still decide what
        # is actually new, so a machine that was off for a weekend catches up.
        command = [
            "docker", "compose", "run", "--rm", "ingest",
            "--since", args.since,
            *extra,
        ]
        status = run_logged(command, log)
        if status == 0:
            log.line("--- ingest finished")
        else:
            log.line(f"FAILED: ingest exited {status}")
        return status


def _cmd_patterns(args: argparse.Namespace, extra: list[str]) -> int:
    """Detect recurring T2 signals and propose Pattern Candidates.

    Write-safe by default: without --apply it only prints what it would
    propose. Runs on the host rather than in a container because it talks to the
    same host-side embedder the MCP server uses.
    """
    root = enter_repo_root()
    with JobLog("pattern-discovery") as log:
        if not docker_running():
            log.line("SKIPPED: Docker is not running. Pattern discovery needs the local data layer.")
            print("Docker not running; skipping pattern discovery.", file=sys.stderr)
            return 0
        if not compose_up_data_layer(log):
            log.line("FAILED: could not start db/redis for pattern discovery.")
            print(f"Could not start db/redis; see {log.path}", file=sys.stderr)
            return 1

        # MINDBRIDGE_PATTERN_APPLY stays honoured because the installed launchd
        # plist sets it; --apply is the same switch for a human at a terminal.
        apply = args.apply or os.environ.get("MINDBRIDGE_PATTERN_APPLY") == "1"
        command = [
            venv_python(), "-m", "scripts.suggest_patterns",
            "--since", args.since,
            "--card-limit", os.environ.get("MINDBRIDGE_PATTERN_SCAN_LIMIT", "365"),
            "--max-supporting", os.environ.get("MINDBRIDGE_PATTERN_SUPPORTING", "10"),
            "--max-counter-evidence", "0",
            "--limit", os.environ.get("MINDBRIDGE_PATTERN_DAILY_LIMIT", "40"),
            *(["--apply"] if apply else []),
            *extra,
        ]
        if apply:
            log.line("pattern discovery: running in APPLY mode")
        log.line(f"pattern discovery starting (repo: {root})")
        status = run_logged(command, log)
        if status == 0:
            log.line("--- pattern discovery finished")
        else:
            log.line(f"FAILED: pattern discovery exited {status}")
        return status


def _cmd_doctor(args: argparse.Namespace, extra: list[str]) -> int:
    enter_repo_root()
    from cli.doctor import run

    return run()


def _cmd_install(args: argparse.Namespace, extra: list[str]) -> int:
    root = enter_repo_root()
    from cli.install import run

    return run(args.client, root)


def _cmd_schedule(args: argparse.Namespace, extra: list[str]) -> int:
    root = enter_repo_root()
    if args.action == "run-now":
        # Deliberately the same code path as the plain command rather than a
        # second one that could drift from it. The window comes from the
        # environment so `run-now` reproduces what the installed job does.
        if args.job == "ingest":
            return main(["ingest"])
        since = os.environ.get("MINDBRIDGE_PATTERN_SINCE", "30d")
        return main(["patterns", "--since", since])

    from cli.schedule import run

    return run(args.job, args.action, root, confirm=args.confirm)


def _cmd_verify(args: argparse.Namespace, extra: list[str]) -> int:
    root = enter_repo_root()
    from cli.verify import run

    return run(root, date=args.date, plan_only=args.plan)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mindbridge",
        description="Operate the local MindBridge memory loop.",
        epilog=(
            "Reading and revising memory is not here on purpose: that happens "
            "over MCP, in a client that has the conversation as context."
        ),
    )
    # Only ingest and patterns forward unknown flags; everywhere else an
    # unrecognised flag is a typo, and swallowing it would be worse than failing.
    parser.set_defaults(passthrough=False)
    subcommands = parser.add_subparsers(dest="command", required=True)

    mcp = subcommands.add_parser(
        "mcp", help="serve the memory tools over stdio (what an MCP client launches)"
    )
    mcp.set_defaults(handler=_cmd_mcp)

    ingest = subcommands.add_parser(
        "ingest", help="read new transcript bytes into T1 and rebuild touched T2 cards"
    )
    ingest.add_argument(
        "--since",
        default="3d",
        help=(
            "Bound the file scan by mtime: 24h, 7d, 2w, or 'all' (default: 3d, "
            "which lets a machine that was off for a weekend catch up)."
        ),
    )
    ingest.set_defaults(handler=_cmd_ingest, passthrough=True)

    patterns = subcommands.add_parser(
        "patterns", help="propose Pattern Candidates from recurring T2 signals"
    )
    patterns.add_argument("--since", default="30d", help="How far back to scan (default: 30d).")
    patterns.add_argument(
        "--apply",
        action="store_true",
        help="Write the candidates. Off by default: the run only prints them.",
    )
    patterns.set_defaults(handler=_cmd_patterns, passthrough=True)

    doctor = subcommands.add_parser(
        "doctor", help="read-only check: containers, store, embedder, clients, jobs"
    )
    doctor.set_defaults(handler=_cmd_doctor)

    install = subcommands.add_parser(
        "install", help="register the MCP server with a local client"
    )
    install.add_argument("client", choices=["claude", "codex"])
    install.set_defaults(handler=_cmd_install)

    schedule = subcommands.add_parser(
        "schedule", help="manage the nightly LaunchAgents (install asks first)"
    )
    schedule.add_argument("job", choices=["ingest", "patterns"])
    schedule.add_argument(
        "action",
        nargs="?",
        default="status",
        choices=["status", "run-now", "install", "uninstall"],
        help="Default: status, which changes nothing.",
    )
    schedule.add_argument(
        "--confirm",
        action="store_true",
        help=(
            "Actually write the LaunchAgent. Without it, `install` prints the "
            "plist it would write and stops."
        ),
    )
    schedule.set_defaults(handler=_cmd_schedule)

    verify = subcommands.add_parser(
        "verify",
        help="start missing local services and prove the loop end to end",
    )
    verify.add_argument(
        "--date",
        default=None,
        help="Which T2 day card to re-extract (default: the newest one).",
    )
    verify.add_argument(
        "--plan",
        action="store_true",
        help="Probe every service and print what would be started. Starts nothing.",
    )
    verify.set_defaults(handler=_cmd_verify)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    # parse_known_args rather than REMAINDER: unknown flags are forwarded
    # verbatim to ingest.runner / suggest_patterns, which have far more options
    # than are worth re-declaring here, and REMAINDER mis-handles a flag that
    # appears before the positional.
    args, extra = parser.parse_known_args(argv)
    if extra and not args.passthrough:
        parser.error(f"unrecognized arguments: {' '.join(extra)}")
    return args.handler(args, extra)


def mcp_main() -> int:
    """Console-script entry point equivalent to `mindbridge mcp`."""
    return main(["mcp"])


if __name__ == "__main__":
    raise SystemExit(main())
