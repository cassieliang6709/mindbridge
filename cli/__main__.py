"""`mindbridge` — the operator's entry point.

Seven commands cover the things a person actually does to this system:

    mindbridge mcp                 serve the eleven memory tools over stdio
    mindbridge ingest [--since 3d] Path A: read new transcript bytes into T1/T2
    mindbridge patterns [--apply]  propose Pattern Candidates from T2
    mindbridge doctor              read-only check of the local loop
    mindbridge install claude      register the MCP server with a client
    mindbridge schedule ingest ... manage the nightly LaunchAgents
    mindbridge verify [--plan]     start what is missing and prove the loop runs

Each one is the same work the matching shell script did; the scripts are now
one-line shims so installed launchd plists keep pointing at a stable path.

中文说明：`mindbridge` 是操作者的统一入口，共七个子命令：启动 MCP 服务、
摄取新的对话记录、发现并提议记忆模式、做只读健康检查，以及安装、调度和
校验本地闭环。每个命令做的都是原来对应的 shell 脚本做的同一件事；那些脚本
现在都收缩成一行的转发壳，好让已安装的
launchd plist 始终指向一个稳定的路径。
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

    中文：通过 stdio 提供 MCP 服务。chdir 必须发生在导入链读取 Settings 之前
    ——因为 Settings 是按相对路径加载 `.env` 的。如果不做这一步，客户端从
    自己的工作目录启动本命令时会静默退化为 hashing 嵌入器，且不会有任何提示。

    Args:
        args: Parsed arguments (unused here). 解析后的参数(此命令未使用)。
        extra: Unrecognized argv forwarded from main() (unused here). 从
            main() 转发过来的未识别参数(此命令未使用)。

    Returns:
        Always 0; `serve()` blocks until the client disconnects. 恒为 0；
        `serve()` 会一直阻塞直到客户端断开连接。
    """
    enter_repo_root()
    from mcp_server.server import main as serve

    serve()
    return 0


def _cmd_ingest(args: argparse.Namespace, extra: list[str]) -> int:
    """Path A. Safe to run at any time and any number of times.

    Turns are keyed by source record and cards are rebuilt from the database, so
    a repeat run cannot duplicate a turn or shrink a card.

    中文：即路径 A。可以在任何时候重复运行任意次而不会出问题——每个 turn
    都以源记录为键，卡片则整体从数据库重建，所以重复运行既不会产生重复的
    turn，也不会把卡片重建得比原来小。

    Args:
        args: Parsed arguments; only `args.since` is read here. 解析后的
            参数；这里只用到 `args.since`。
        extra: Unrecognized flags forwarded verbatim to the ingest runner.
            原样转发给 ingest runner 的未识别参数。

    Returns:
        0 if Docker is down (skipped quietly), 1 if the data layer would not
        start, or the child process's own exit code otherwise. 若 Docker
        未运行则安静跳过并返回 0；若数据层无法启动则返回 1；否则返回子
        进程自身的退出码。
    """
    root = enter_repo_root()
    with JobLog("ingest") as log:
        log.line(f"--- ingest starting (repo: {root})")
        if not docker_running():
            # Docker Desktop is down at boot and just after a wake, which is
            # exactly when the nightly job fires. Leave the cursors untouched so
            # the next run picks up where this one would have.
            # 中文：Docker Desktop 在开机和刚唤醒时通常还没起来，而这正是
            # 夜间任务触发的时刻。保持游标不动，让下一次运行从本该处理的
            # 位置继续。
            log.line("SKIPPED: Docker is not running. Nothing was read; cursors unchanged.")
            return 0
        if not compose_up_data_layer(log):
            log.line("FAILED: could not start db/redis. See lines above.")
            return 1

        # --since bounds the file scan by mtime while cursors still decide what
        # is actually new, so a machine that was off for a weekend catches up.
        # 中文：--since 只是按文件的 mtime 限定扫描范围，真正决定"哪些是新
        # 内容"的仍然是游标，所以一台关机了一个周末的机器也能追上进度。
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

    中文：检测重复出现的 T2 信号并提议 Pattern Candidate。默认写入安全——
    不带 --apply 时只会打印出它本来会提议的内容。它运行在宿主机上而不是
    容器里，因为它要访问和 MCP 服务器相同的宿主机侧嵌入器。

    Args:
        args: Parsed arguments; reads `args.since` and `args.apply`. 解析
            后的参数；读取 `args.since` 和 `args.apply`。
        extra: Unrecognized flags forwarded verbatim to suggest_patterns.
            原样转发给 suggest_patterns 的未识别参数。

    Returns:
        0 if Docker is down (skipped quietly), 1 if the data layer would not
        start, or the child process's own exit code otherwise. 若 Docker
        未运行则安静跳过并返回 0；若数据层无法启动则返回 1；否则返回子
        进程自身的退出码。
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
        # 中文：之所以仍要读取 MINDBRIDGE_PATTERN_APPLY，是因为已安装的
        # launchd plist 会设置这个环境变量；--apply 则是给终端前的人用的
        # 同一个开关。
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
    """Run the read-only health check. 运行只读的健康检查。

    Args:
        args: Parsed arguments (unused here). 解析后的参数(此命令未使用)。
        extra: Unrecognized argv (unused here). 未识别参数(此命令未使用)。

    Returns:
        0 if healthy, 1 if any check reported FAIL. 健康时返回 0；只要有
        一项检查失败(FAIL)则返回 1。
    """
    enter_repo_root()
    from cli.doctor import run

    return run()


def _cmd_install(args: argparse.Namespace, extra: list[str]) -> int:
    """Register the MCP server with the chosen client. 向指定客户端注册 MCP 服务器。

    Args:
        args: Parsed arguments; reads `args.client`. 解析后的参数；读取
            `args.client`。
        extra: Unrecognized argv (unused here). 未识别参数(此命令未使用)。

    Returns:
        0 on success, 1 (or the subprocess's exit code) on failure. 成功时
        返回 0；失败时返回 1(或子进程本身的退出码)。
    """
    root = enter_repo_root()
    from cli.install import run

    return run(args.client, root)


def _cmd_schedule(args: argparse.Namespace, extra: list[str]) -> int:
    """Manage or trigger a nightly LaunchAgent. 管理或触发某个夜间 LaunchAgent 任务。

    Args:
        args: Parsed arguments; reads `args.job`, `args.action` and
            `args.confirm`. 解析后的参数；读取 `args.job`、`args.action`
            和 `args.confirm`。
        extra: Unrecognized argv (unused here). 未识别参数(此命令未使用)。

    Returns:
        0 on success (status/install-preview/uninstall), or whatever
        `main(["ingest"])`/`main(["patterns", ...])` returns for `run-now`.
        对 status/安装预览/卸载成功时返回 0；对 `run-now` 则返回
        `main(["ingest"])` 或 `main(["patterns", ...])` 的结果。
    """
    root = enter_repo_root()
    if args.action == "run-now":
        # Deliberately the same code path as the plain command rather than a
        # second one that could drift from it. The window comes from the
        # environment so `run-now` reproduces what the installed job does.
        # 中文：这里故意复用普通命令的同一条代码路径，而不是另建一条容易
        # 与之走偏的路径。时间窗口从环境变量读取，所以 `run-now` 能重现
        # 已安装任务的实际行为。
        if args.job == "ingest":
            return main(["ingest"])
        since = os.environ.get("MINDBRIDGE_PATTERN_SINCE", "30d")
        return main(["patterns", "--since", since])

    from cli.schedule import run

    return run(args.job, args.action, root, confirm=args.confirm)


def _cmd_verify(args: argparse.Namespace, extra: list[str]) -> int:
    """Start what's missing and prove the local loop runs end to end.

    中文：启动缺失的服务并端到端验证本地闭环可运行。

    Args:
        args: Parsed arguments; reads `args.date` and `args.plan`. 解析后
            的参数；读取 `args.date` 和 `args.plan`。
        extra: Unrecognized argv (unused here). 未识别参数(此命令未使用)。

    Returns:
        0 on success (or when `--plan` only prints a plan), 1 on failure.
        成功时返回 0(或 `--plan` 仅打印计划时也返回 0)；失败时返回 1。
    """
    root = enter_repo_root()
    from cli.verify import run

    return run(root, date=args.date, plan_only=args.plan)


def build_parser() -> argparse.ArgumentParser:
    """Build the `mindbridge` argparse parser with all seven subcommands.

    中文：构建 `mindbridge` 的 argparse 解析器，注册全部七个子命令。

    Returns:
        The configured top-level parser. 配置完成的顶层解析器。
    """
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
    # 中文：只有 ingest 和 patterns 会转发未识别的参数；其余子命令下，一个
    # 未识别的参数意味着打字错误，悄悄吞掉它比直接报错更糟糕。
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
    """Parse argv, dispatch to the matching subcommand handler, return its exit code.

    中文：解析命令行参数，分发给匹配的子命令处理函数，并返回其退出码。

    Args:
        argv: Argument list to parse, or None to use `sys.argv`. 要解析的
            参数列表；为 None 时使用 `sys.argv`。

    Returns:
        The exit code from the dispatched handler. 被分发到的处理函数返回
        的退出码。
    """
    parser = build_parser()
    # parse_known_args rather than REMAINDER: unknown flags are forwarded
    # verbatim to ingest.runner / suggest_patterns, which have far more options
    # than are worth re-declaring here, and REMAINDER mis-handles a flag that
    # appears before the positional.
    # 中文：用 parse_known_args 而不是 REMAINDER:未识别的参数会原样转发给
    # ingest.runner / suggest_patterns，它们的选项远比在这里重新声明一遍
    # 划算；而且 REMAINDER 在遇到出现在位置参数之前的 flag 时处理是有问题的。
    args, extra = parser.parse_known_args(argv)
    if extra and not args.passthrough:
        parser.error(f"unrecognized arguments: {' '.join(extra)}")
    return args.handler(args, extra)


def mcp_main() -> int:
    """Console-script entry point equivalent to `mindbridge mcp`.

    中文：等同于 `mindbridge mcp` 的 console-script 入口函数。
    """
    return main(["mcp"])


if __name__ == "__main__":
    raise SystemExit(main())
