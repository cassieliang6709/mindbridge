"""One-command proof that the shipped local loop actually runs.

Starts only the services that are missing, refreshes one real T2 day card
through the private MLX adapter, then checks the same store over REST, the Diary
and MCP. T3 is read-only throughout: a verification run must not leave a
synthetic preference in durable memory to prove that writes work.

Two behaviours are deliberately different from the shell script this replaces:

- Logs survive a failure. The shell printed "see $TMP_DIR" from a handler that
  had already deleted it, so the one message you needed pointed at nothing. The
  directory is now kept when the run fails and removed when it passes.
- Readiness probes use httpx instead of shelling out to curl, so curl is no
  longer a dependency of proving the project works.

Every progress line is flushed. Child processes inherit this stdout and write to
it unbuffered, so a block-buffered parent would land its START/READY lines after
their output whenever the run is redirected to a log — which is exactly when
someone is reading it to find out what broke.

Everything else is the same, including the rule that only services this run
started are shut down afterwards.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

OLLAMA_TAGS = "http://127.0.0.1:11434/api/tags"
MLX_MODELS = "http://127.0.0.1:8080/v1/models"
API_HEALTH = "http://127.0.0.1:8000/healthz"
DIARY_API = "http://127.0.0.1:3000/api/diary"

EMBEDDER = "nomic-embed-text"


class VerifyError(RuntimeError):
    """A precondition failed or a service never became ready."""


@dataclass
class Plan:
    """What this run would have to start, decided by probing only."""

    docker: bool = False
    db: bool = False
    redis: bool = False
    ollama: bool = False
    mlx: bool = False
    api: bool = False
    web: bool = False
    next_build: bool = False
    notes: list[str] = field(default_factory=list)

    def render(self) -> str:
        rows = [
            ("Docker Desktop", self.docker),
            ("Postgres (compose db)", self.db),
            ("Redis (compose redis)", self.redis),
            ("Ollama", self.ollama),
            ("Qwen2.5-3B + private MLX adapter", self.mlx),
            ("FastAPI", self.api),
            ("Next.js build", self.next_build),
            ("Next.js diary", self.web),
        ]
        lines = [
            f"  {'START' if needed else 'in place':>9}  {label}" for label, needed in rows
        ]
        return "\n".join(lines + [f"  {'note':>9}  {note}" for note in self.notes])


def _probe(url: str, timeout: float = 2.0) -> bool:
    try:
        return httpx.get(url, timeout=timeout).status_code < 400
    except Exception:
        return False


def _compose_running(service: str) -> bool:
    result = subprocess.run(
        ["docker", "compose", "ps", "--status", "running", "-q", service],
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


def _docker_up() -> bool:
    return (
        subprocess.run(
            ["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        ).returncode
        == 0
    )


def _preflight(root: Path) -> None:
    required = [
        (root / ".venv/bin/python", "missing .venv; install requirements first"),
        (root / ".venv/bin/mlx_lm.server", "mlx_lm.server is not installed in .venv"),
        (
            root / "train/outputs/mlx-adapters/adapters.safetensors",
            "missing private adapter: train/outputs/mlx-adapters/adapters.safetensors",
        ),
    ]
    for path, message in required:
        if not path.exists():
            raise VerifyError(message)
    if subprocess.run(["which", "docker"], stdout=subprocess.DEVNULL).returncode != 0:
        raise VerifyError("Docker CLI is not installed")


def build_plan(root: Path) -> Plan:
    """Probe every service without starting anything."""
    plan = Plan()
    plan.docker = not _docker_up()
    if plan.docker:
        # Container state cannot be read while the daemon is down, so those two
        # lines would be a guess. Say so instead of guessing.
        plan.db = plan.redis = True
        plan.notes.append("db/redis assumed missing: the Docker daemon is not answering")
    else:
        plan.db = not _compose_running("db")
        plan.redis = not _compose_running("redis")
    plan.ollama = not _probe(OLLAMA_TAGS)
    plan.mlx = not _probe(MLX_MODELS)
    plan.api = not _probe(API_HEALTH)
    plan.web = not _probe(DIARY_API)
    plan.next_build = plan.web and not (root / ".next/BUILD_ID").is_file()
    return plan


def _wait_for(
    label: str, url: str, attempts: int, process: subprocess.Popen | None = None
) -> None:
    for _ in range(attempts):
        if _probe(url):
            print(f"READY {label}", flush=True)
            return
        if process is not None and process.poll() is not None:
            raise VerifyError(f"{label} exited before becoming ready")
        time.sleep(1)
    raise VerifyError(f"{label} did not become ready")


def _spawn(
    command: list[str], log_path: Path, stack: contextlib.ExitStack
) -> subprocess.Popen:
    """Start a long-running child and register its shutdown."""
    handle = stack.enter_context(log_path.open("w", encoding="utf-8"))
    process = subprocess.Popen(command, stdout=handle, stderr=subprocess.STDOUT)

    def stop() -> None:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()

    stack.callback(stop)
    return process


def _quit_mac_app(name: str) -> None:
    subprocess.run(
        ["osascript", "-e", f'tell application "{name}" to quit'],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _check_embedder() -> None:
    payload = httpx.get(OLLAMA_TAGS, timeout=5).json()
    names = [model.get("name", "") for model in payload.get("models", [])]
    if not any(name.startswith(EMBEDDER) for name in names):
        raise VerifyError(
            f"Ollama model {EMBEDDER} is missing; run: ollama pull {EMBEDDER}"
        )


def _raise_interrupt(*_: object) -> None:
    raise KeyboardInterrupt


def run(root: Path, date: str | None = None, plan_only: bool = False) -> int:
    try:
        _preflight(root)
        plan = build_plan(root)
    except VerifyError as error:
        print(f"FAIL  {error}", flush=True)
        return 1

    if plan_only:
        print("Would run scripts.verify_local_loop after bringing this up:", flush=True)
        print(plan.render(), flush=True)
        print("\nNothing was started. Drop --plan to run it.", flush=True)
        return 0

    # SIGTERM would otherwise bypass the finally block and leave a 3B model
    # resident and two web servers listening.
    previous_term = signal.signal(signal.SIGTERM, _raise_interrupt)
    temporary = Path(tempfile.mkdtemp(prefix="mindbridge-verify."))
    passed = False
    try:
        with contextlib.ExitStack() as stack:
            if plan.docker:
                print("START Docker Desktop", flush=True)
                subprocess.run(["open", "-gj", "-a", "Docker"], check=True)
                stack.callback(_quit_mac_app, "Docker")
                for _ in range(120):
                    if _docker_up():
                        break
                    time.sleep(1)
                if not _docker_up():
                    raise VerifyError("Docker Desktop did not become ready")
                plan.db = not _compose_running("db")
                plan.redis = not _compose_running("redis")

            print("START Postgres + Redis", flush=True)
            # Registered before `up` so a partial start is still torn down, and
            # only for the services that were not already serving something else.
            for service, was_missing in (("redis", plan.redis), ("db", plan.db)):
                if was_missing:
                    stack.callback(
                        subprocess.run,
                        ["docker", "compose", "stop", service],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
            subprocess.run(
                ["docker", "compose", "up", "-d", "--wait", "db", "redis"], check=True
            )

            if plan.ollama:
                print("START Ollama", flush=True)
                subprocess.run(["open", "-gj", "-a", "Ollama"], check=True)
                stack.callback(_quit_mac_app, "Ollama")
                _wait_for("Ollama", OLLAMA_TAGS, 60)
            _check_embedder()

            if plan.mlx:
                print("START Qwen2.5-3B + private MLX adapter", flush=True)
                process = _spawn(
                    [
                        str(root / ".venv/bin/mlx_lm.server"),
                        "--model", "mlx-community/Qwen2.5-3B-Instruct-4bit",
                        "--adapter-path", "train/outputs/mlx-adapters",
                        "--host", "127.0.0.1",
                        "--port", "8080",
                        "--max-tokens", "1200",
                        "--temp", "0.2",
                    ],
                    temporary / "mlx.log",
                    stack,
                )
                _wait_for("MLX server", MLX_MODELS, 180, process)

            if plan.api:
                print("START FastAPI", flush=True)
                process = _spawn(
                    [
                        str(root / ".venv/bin/uvicorn"),
                        "api.main:app",
                        "--host", "127.0.0.1",
                        "--port", "8000",
                    ],
                    temporary / "api.log",
                    stack,
                )
                _wait_for("FastAPI", API_HEALTH, 60, process)

            if plan.web:
                if plan.next_build:
                    print("BUILD Next.js", flush=True)
                    with (temporary / "next-build.log").open("w") as log:
                        build = subprocess.run(
                            ["npm", "run", "build"], stdout=log, stderr=subprocess.STDOUT
                        )
                    if build.returncode != 0:
                        raise VerifyError("npm run build failed")
                print("START Next.js diary", flush=True)
                process = _spawn(
                    [
                        "node_modules/.bin/next", "start",
                        "--hostname", "127.0.0.1",
                        "--port", "3000",
                    ],
                    temporary / "next.log",
                    stack,
                )
                _wait_for("Next.js diary", DIARY_API, 60, process)

            command = [str(root / ".venv/bin/python"), "-m", "scripts.verify_local_loop"]
            if date:
                command += ["--date", date]
            passed = subprocess.run(command).returncode == 0
            return 0 if passed else 1
    except VerifyError as error:
        print(f"FAIL  {error}", flush=True)
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous_term)
        if passed:
            for path in temporary.iterdir():
                path.unlink()
            temporary.rmdir()
        else:
            print(f"Service logs kept in {temporary}", flush=True)
