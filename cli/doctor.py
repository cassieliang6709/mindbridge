"""Read-only health check for the local loop.

Answers one question — "is the machine in a state where MindBridge would
actually work right now?" — and answers it by observation. Every line names the
thing it read, because a green check nobody can trace is worth nothing here.

This command writes nothing: no schema, no launchd agent, no compose lifecycle
beyond reading container state. Fixes are printed for the operator to run.

中文说明：这是对本地闭环的只读健康检查，只回答一个问题——"这台机器现在的
状态是否能让 MindBridge 真正跑起来?"——并且完全靠实际观察来回答。每一行
都写明它读取的是什么，因为一个查不到出处的绿色对勾在这里毫无价值。此命令
不写任何东西:不建 schema，不装 launchd 任务，除了读取容器状态外不做任何
compose 生命周期操作。修复建议只是打印出来，交给操作者自己去执行。
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from cli._runtime import log_dir, repo_root

OK = "ok"
WARN = "warn"
FAIL = "fail"

_MARK = {OK: "ok  ", WARN: "warn", FAIL: "FAIL"}


class Report:
    """Ordered collection of health-check rows, rendered as one aligned block.

    中文：健康检查结果行的有序集合，最终渲染成一个对齐的文本块。
    """

    def __init__(self) -> None:
        """Start with an empty list of rows. 初始化为空的检查结果列表。"""
        self.rows: list[tuple[str, str, str, str]] = []

    def add(self, status: str, area: str, detail: str, source: str = "") -> None:
        """Record one check result.

        中文：记录一条检查结果。

        Args:
            status: One of OK, WARN, FAIL. 三种状态之一:OK、WARN 或 FAIL。
            area: Short label for what was checked. 被检查项的简短名称。
            detail: Human-readable finding. 人类可读的检查结论。
            source: Optional command or path the finding came from, or the fix
                to run. 可选的、结论所依据的命令/路径，或建议执行的修复
                命令。
        """
        self.rows.append((status, area, detail, source))

    @property
    def failed(self) -> bool:
        """Whether any row is FAIL. 是否存在任何一条 FAIL 状态的记录。"""
        return any(status == FAIL for status, *_ in self.rows)

    def render(self) -> str:
        """Format all rows into an aligned, multi-line report string.

        中文：将全部记录格式化为对齐的多行报告字符串。

        Returns:
            The full report text, ready to print. 可直接打印的完整报告文本。
        """
        width = max(len(area) for _, area, _, _ in self.rows)
        lines = []
        for status, area, detail, source in self.rows:
            lines.append(f"[{_MARK[status]}] {area.ljust(width)}  {detail}")
            if source:
                lines.append(f"{' ' * (width + 9)}↳ {source}")
        return "\n".join(lines)


def _command_ok(command: list[str]) -> bool:
    """Whether `command[0]` exists on PATH and running `command` exits 0.

    中文：检查 `command[0]` 是否存在于 PATH 中，且执行 `command` 的退出码为 0。
    """
    if shutil.which(command[0]) is None:
        return False
    return (
        subprocess.run(
            command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        ).returncode
        == 0
    )


def _check_checkout(report: Report) -> Path:
    """Record whether the repo root and `.env` are present, and return the root.

    中文：记录仓库根目录和 `.env` 是否存在，并返回该根目录。

    Args:
        report: Report to append rows to. 用于追加检查结果的 Report。

    Returns:
        The repo root, as found by `repo_root()`. `repo_root()` 找到的仓库
        根目录。
    """
    root = repo_root()
    report.add(OK, "checkout", str(root))
    if (root / ".env").is_file():
        report.add(OK, ".env", "present")
    else:
        report.add(
            FAIL,
            ".env",
            "missing — settings fall back to the hashing embedder",
            "cp .env.example .env",
        )
    return root


def _check_imports(report: Report) -> None:
    """Import the packages the MCP server needs before a client tries to.

    中文：提前导入 MCP 服务器所需的依赖包，赶在客户端尝试连接之前发现问题。

    Args:
        report: Report to append rows to. 用于追加检查结果的 Report。
    """
    missing = []
    for module in ("mcp", "fastapi", "asyncpg", "httpx", "pydantic_settings"):
        try:
            __import__(module)
        except ImportError:
            missing.append(module)
    if missing:
        report.add(
            FAIL,
            "python deps",
            f"not importable: {', '.join(missing)}",
            "pip install -e .",
        )
    else:
        report.add(OK, "python deps", "mcp, fastapi, asyncpg, httpx, pydantic-settings")


def _check_docker(report: Report) -> bool:
    """Record whether Docker is up, and if so, whether db/redis are healthy.

    中文：记录 Docker 是否在运行，以及(若在运行)db/redis 两个容器是否健康。

    Args:
        report: Report to append rows to. 用于追加检查结果的 Report。

    Returns:
        True if the Docker daemon answered. Docker 守护进程有响应时返回 True。
    """
    if not _command_ok(["docker", "info"]):
        report.add(
            FAIL,
            "docker",
            "daemon not answering",
            "start Docker Desktop; nightly jobs skip quietly without it",
        )
        return False
    report.add(OK, "docker", "daemon answering", "docker info")

    # `compose ps` reports health only for services it can see, so an absent
    # service and an unhealthy one are different findings.
    # 中文：`compose ps` 只会报告它能看到的服务的健康状态，所以"服务不存在"
    # 和"服务存在但不健康"是两种不同的结论，需要分开处理。
    result = subprocess.run(
        ["docker", "compose", "ps", "--format", "json", "db", "redis"],
        capture_output=True,
        text=True,
    )
    states: dict[str, str] = {}
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        states[entry.get("Service", "?")] = entry.get("Health") or entry.get(
            "State", "?"
        )
    for service in ("db", "redis"):
        state = states.get(service)
        if state == "healthy":
            report.add(OK, f"container {service}", state)
        elif state:
            report.add(WARN, f"container {service}", state, "docker compose ps")
        else:
            report.add(
                WARN,
                f"container {service}",
                "not running",
                "docker compose up -d --wait db redis",
            )
    return True


async def _probe_postgres(report: Report) -> None:
    """Connect with the real settings and count what is actually stored.

    中文：用真实的 Settings 连接 Postgres，并统计各表中实际存储的行数。

    Args:
        report: Report to append rows to. 用于追加检查结果的 Report。
    """
    try:
        import asyncpg

        from api.settings import get_settings
    except ImportError as error:
        report.add(FAIL, "postgres", f"cannot load settings: {error}")
        return

    settings = get_settings()
    try:
        connection = await asyncpg.connect(str(settings.database_url), timeout=5)
    # asyncpg raises a wide family here
    # 中文：asyncpg 在连接失败时会抛出很多种不同的异常类型，这里统一兜住。
    except Exception as error:
        report.add(
            FAIL,
            "postgres",
            f"{type(error).__name__}: {error}",
            str(settings.database_url),
        )
        return

    try:
        counts = {}
        for table in (
            "session_turns",
            "rolling_summaries",
            "memory_vectors",
            "pattern_candidates",
        ):
            try:
                counts[table] = await connection.fetchval(
                    f"SELECT count(*) FROM {table}"  # noqa: S608 - fixed literals
                )
            except Exception:
                counts[table] = None
        missing = [table for table, count in counts.items() if count is None]
        if missing:
            report.add(
                FAIL,
                "postgres",
                f"connected, but no table: {', '.join(missing)}",
                "start the API once to apply api/schema.sql",
            )
        else:
            report.add(
                OK,
                "postgres",
                f"T1 {counts['session_turns']} turns · "
                f"T2 {counts['rolling_summaries']} cards · "
                f"T3 {counts['memory_vectors']} memories · "
                f"{counts['pattern_candidates']} pattern candidates",
                str(settings.database_url),
            )

        width = await connection.fetchval(
            """
            SELECT atttypmod
            FROM pg_attribute
            WHERE attrelid = 'memory_vectors'::regclass
              AND attname = 'embedding'
            """
        )
        if width and width > 0 and width != settings.embedding_dim:
            report.add(
                FAIL,
                "vector width",
                f"column is vector({width}) but MINDBRIDGE_EMBEDDING_DIM is "
                f"{settings.embedding_dim} — writes will fail",
                "recreate memory_vectors or set the dim back",
            )
        elif width:
            report.add(OK, "vector width", f"vector({width}) matches configured dim")

        newest = await connection.fetchval(
            "SELECT max(created_at) FROM session_turns"
        )
        if newest is None:
            report.add(WARN, "ingest freshness", "no turns stored yet")
        else:
            age = datetime.now(newest.tzinfo) - newest
            status = OK if age < timedelta(days=2) else WARN
            report.add(
                status,
                "ingest freshness",
                f"newest T1 turn {newest:%Y-%m-%d %H:%M} ({age.days}d old)",
            )
    finally:
        await connection.close()


async def _probe_embedder(report: Report) -> None:
    """Record whether the configured embedder provider is reachable and pulled.

    中文：记录配置的嵌入器 provider 是否可达、所需模型是否已拉取。

    Args:
        report: Report to append rows to. 用于追加检查结果的 Report。
    """
    try:
        import httpx

        from api.settings import get_settings
    except ImportError as error:
        report.add(FAIL, "embeddings", f"cannot load settings: {error}")
        return

    settings = get_settings()
    provider = settings.embedding_provider
    if provider != "ollama":
        # Not a style preference: AGENTS.md records that hashing scores real
        # duplicates 0.13-0.73, so dedup never fires under it.
        # 中文：这不是风格偏好问题——AGENTS.md 记录过 hashing 给真实重复项
        # 打出的分数在 0.13-0.73 之间，导致去重在这种 provider 下永远不会
        # 触发。
        report.add(
            WARN,
            "embeddings",
            f"provider is '{provider}' — write-time dedup only works under ollama",
            "MINDBRIDGE_EMBEDDING_PROVIDER=ollama in .env",
        )
        return

    url = str(settings.ollama_url).rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            payload = (await client.get(f"{url}/api/tags")).json()
    except Exception as error:
        report.add(
            FAIL,
            "embeddings",
            f"ollama unreachable at {url}: {type(error).__name__}",
            "ollama serve",
        )
        return

    tags = [model.get("name", "") for model in payload.get("models", [])]
    wanted = settings.embedding_model
    if any(tag == wanted or tag.startswith(f"{wanted}:") for tag in tags):
        report.add(OK, "embeddings", f"ollama has {wanted}", f"{url}/api/tags")
    else:
        report.add(
            FAIL,
            "embeddings",
            f"ollama is up but {wanted} is not pulled",
            f"ollama pull {wanted}",
        )


async def _probe_mlx(report: Report) -> None:
    """The local extractor is opt-in, so a silent one is a warning, not a failure.

    中文：本地提取器是可选启用的功能，所以它没有在跑只算警告，而不是失败。

    Args:
        report: Report to append rows to. 用于追加检查结果的 Report。
    """
    try:
        import httpx

        from api.settings import get_settings
    except ImportError:
        return

    settings = get_settings()
    url = str(settings.mlx_url).rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            response = await client.get(f"{url}/models")
        response.raise_for_status()
    except Exception:
        report.add(
            WARN,
            "mlx extractor",
            f"not serving at {url} — local extraction unavailable",
            "start mlx_lm.server when you need it",
        )
        return
    report.add(OK, "mlx extractor", f"serving at {url}", f"{url}/models")


def _check_mcp_clients(report: Report) -> None:
    """Record whether Claude Code / Codex have MindBridge registered as an MCP server.

    中文：记录 Claude Code / Codex 是否已经把 MindBridge 注册为 MCP 服务器。

    Args:
        report: Report to append rows to. 用于追加检查结果的 Report。
    """
    for client, probe in (
        ("claude", ["claude", "mcp", "get", "mindbridge"]),
        ("codex", ["codex", "mcp", "get", "mindbridge"]),
    ):
        if shutil.which(client) is None:
            report.add(WARN, f"mcp/{client}", "client not installed")
        elif _command_ok(probe):
            report.add(OK, f"mcp/{client}", "mindbridge registered", " ".join(probe))
        else:
            report.add(
                WARN,
                f"mcp/{client}",
                "not registered",
                f"scripts/install-{client}-mcp.sh",
            )


def _check_schedulers(report: Report) -> None:
    """Record whether the nightly ingest/patterns LaunchAgents are loaded.

    中文：记录夜间 ingest/patterns 两个 LaunchAgent 是否已被 launchd 加载。

    Args:
        report: Report to append rows to. 用于追加检查结果的 Report。
    """
    uid = os.getuid()
    for label, job in (
        ("com.mindbridge.nightly-ingest", "ingest"),
        ("com.mindbridge.nightly-patterns", "patterns"),
    ):
        loaded = _command_ok(["launchctl", "print", f"gui/{uid}/{label}"])
        if loaded:
            report.add(OK, f"launchd {label.split('.')[-1]}", "loaded", label)
        else:
            report.add(
                WARN,
                f"launchd {label.split('.')[-1]}",
                "not loaded",
                f"mindbridge schedule {job} install",
            )


def _check_logs(report: Report) -> None:
    """Record the last line and mtime of each nightly job's log file.

    中文：记录每个夜间任务日志文件的最后一行和最后修改时间。

    Args:
        report: Report to append rows to. 用于追加检查结果的 Report。
    """
    directory = log_dir()
    for name in ("ingest", "pattern-discovery"):
        path = directory / f"{name}.log"
        if not path.is_file():
            report.add(WARN, f"log {name}", f"none yet ({path})")
            continue
        last = ""
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip():
                last = line.strip()
        stamp = datetime.fromtimestamp(path.stat().st_mtime)
        status = WARN if "FAILED" in last else OK
        report.add(status, f"log {name}", f"{stamp:%Y-%m-%d %H:%M} · {last[:96]}", str(path))


async def _run_async_checks(report: Report) -> None:
    """Run the three async probes (postgres, embedder, mlx) in sequence.

    中文：依次运行三个异步探测(postgres、embedder、mlx)。

    Args:
        report: Report to append rows to. 用于追加检查结果的 Report。
    """
    await _probe_postgres(report)
    await _probe_embedder(report)
    await _probe_mlx(report)


def run() -> int:
    """Run every check and print the combined report.

    中文：运行全部检查项并打印汇总报告。

    Returns:
        0 if healthy, 1 if any check reported FAIL. 健康时返回 0；只要有
        一项检查失败(FAIL)则返回 1。
    """
    report = Report()
    _check_checkout(report)
    _check_imports(report)
    docker_up = _check_docker(report)
    if docker_up:
        asyncio.run(_run_async_checks(report))
    else:
        report.add(
            WARN,
            "postgres",
            "not probed — docker is down",
            "nothing was read from the store",
        )
    _check_mcp_clients(report)
    _check_schedulers(report)
    _check_logs(report)

    print(report.render())
    print()
    if report.failed:
        print("Not healthy: fix the FAIL lines above.")
        return 1
    print("Healthy. Warnings above are optional parts of the loop.")
    return 0
