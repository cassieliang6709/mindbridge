"""Shared plumbing for the ops commands.

Every nightly job needs the same four things: the repo root, a size-capped log
file, a check that Docker is up, and a healthy db/redis pair. Each shell script
used to carry its own copy; they live here once instead, so the shells can
shrink to one-line shims.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# One rotation is enough for jobs that write a few lines a night.
MAX_LOG_BYTES = 5 * 1024 * 1024


def repo_root() -> Path:
    """The checkout holding docker-compose.yml and .env.

    An editable install leaves this package inside the checkout, so walking up
    from here finds it. MINDBRIDGE_REPO_ROOT overrides that for a wheel
    installed outside the tree, where compose files are elsewhere.
    """
    override = os.environ.get("MINDBRIDGE_REPO_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "docker-compose.yml").is_file():
            return candidate
    return Path.cwd()


def enter_repo_root() -> Path:
    """chdir to the checkout and return it.

    Not cosmetic: Settings reads `.env` by relative path, and docker compose
    resolves service definitions and bind mounts against the working directory.
    Launched from anywhere else, the process would silently fall back to the
    'hashing' embedder and an empty compose project.
    """
    root = repo_root()
    os.chdir(root)
    return root


def log_dir() -> Path:
    return Path(
        os.environ.get("MINDBRIDGE_LOG_DIR", Path.home() / "Library/Logs/mindbridge")
    ).expanduser()


class JobLog:
    """Append-only log for one named job.

    Lines land in the file — which is what launchd leaves behind — and are
    mirrored to stderr so anyone running the command sees it work. Deliberately
    not gated on isatty(): `mindbridge ingest | tail` is a normal thing to type,
    and a command that goes silent the moment it is piped looks broken. The
    scheduled run pays for this by repeating the lines into launchd.err.log.
    """

    def __init__(self, name: str) -> None:
        directory = log_dir()
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / f"{name}.log"
        if self.path.is_file() and self.path.stat().st_size > MAX_LOG_BYTES:
            self.path.replace(self.path.with_name(self.path.name + ".1"))
        self._handle = self.path.open("a", encoding="utf-8", errors="replace")

    def __enter__(self) -> "JobLog":
        return self

    def __exit__(self, *_exc: object) -> None:
        self._handle.close()

    def line(self, message: str) -> None:
        self.raw(f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}\n")

    def raw(self, text: str) -> None:
        self._handle.write(text)
        self._handle.flush()
        sys.stderr.write(text)
        sys.stderr.flush()


def docker_running() -> bool:
    """Whether the Docker daemon answers.

    Docker Desktop is not up at boot or just after the laptop wakes, which is
    exactly when a nightly job fires.
    """
    if shutil.which("docker") is None:
        return False
    return (
        subprocess.run(
            ["docker", "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        == 0
    )


def run_logged(command: list[str], log: JobLog) -> int:
    """Run a child process, tee its output into the log, return its exit code."""
    log.line(f"$ {' '.join(command)}")
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert process.stdout is not None
    for output_line in process.stdout:
        log.raw(output_line)
    return process.wait()


def compose_up_data_layer(log: JobLog) -> bool:
    """Start db and redis and block until their healthchecks pass.

    `--wait` is the point: without it ingest races the database on a cold start.
    """
    return run_logged(
        ["docker", "compose", "up", "-d", "--wait", "db", "redis"], log
    ) == 0


def venv_python() -> str:
    """Interpreter for child Python processes.

    sys.executable is right whenever the CLI itself was launched from the venv,
    which an installed console script always is. The override exists for the
    shim scripts, which may be invoked by launchd with a bare PATH.
    """
    return os.environ.get("MINDBRIDGE_PYTHON_BIN") or sys.executable
