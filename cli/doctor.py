"""Read-only health check for the local loop.

Answers one question — "is the machine in a state where MindBridge would
actually work right now?" — and answers it by observation. Every line names the
thing it read, because a green check nobody can trace is worth nothing here.

This command writes nothing: no schema, no launchd agent, no compose lifecycle
beyond reading container state. Fixes are printed for the operator to run.
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
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str, str]] = []

    def add(self, status: str, area: str, detail: str, source: str = "") -> None:
        self.rows.append((status, area, detail, source))

    @property
    def failed(self) -> bool:
        return any(status == FAIL for status, *_ in self.rows)

    def render(self) -> str:
        width = max(len(area) for _, area, _, _ in self.rows)
        lines = []
        for status, area, detail, source in self.rows:
            lines.append(f"[{_MARK[status]}] {area.ljust(width)}  {detail}")
            if source:
                lines.append(f"{' ' * (width + 9)}↳ {source}")
        return "\n".join(lines)


def _command_ok(command: list[str]) -> bool:
    if shutil.which(command[0]) is None:
        return False
    return (
        subprocess.run(
            command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        ).returncode
        == 0
    )


def _check_checkout(report: Report) -> Path:
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
    """Import the packages the MCP server needs before a client tries to."""
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
    """Connect with the real settings and count what is actually stored."""
    try:
        import asyncpg

        from api.settings import get_settings
    except ImportError as error:
        report.add(FAIL, "postgres", f"cannot load settings: {error}")
        return

    settings = get_settings()
    try:
        connection = await asyncpg.connect(str(settings.database_url), timeout=5)
    except Exception as error:  # asyncpg raises a wide family here
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
    """The local extractor is opt-in, so a silent one is a warning, not a failure."""
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
    await _probe_postgres(report)
    await _probe_embedder(report)
    await _probe_mlx(report)


def run() -> int:
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
