"""Shared plumbing for the ops commands.

Every nightly job needs the same four things: the repo root, a size-capped log
file, a check that Docker is up, and a healthy db/redis pair. Each shell script
used to carry its own copy; they live here once instead, so the shells can
shrink to one-line shims.

中文说明：每个夜间任务都需要同样的四样东西——仓库根目录、有大小上限的日志
文件、Docker 是否在运行的检测，以及一对健康的 db/redis。以前每个 shell 脚本
各自维护一份，现在统一放在这里，于是那些 shell 脚本就能缩减为一行的转发壳。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# One rotation is enough for jobs that write a few lines a night.
# 中文：对于每晚只写几行日志的任务，做一次轮转就够了。
MAX_LOG_BYTES = 5 * 1024 * 1024


def repo_root() -> Path:
    """The checkout holding docker-compose.yml and .env.

    An editable install leaves this package inside the checkout, so walking up
    from here finds it. MINDBRIDGE_REPO_ROOT overrides that for a wheel
    installed outside the tree, where compose files are elsewhere.

    中文：返回包含 docker-compose.yml 和 .env 的仓库根目录。可编辑安装
    （editable install）会把这个包留在仓库内部，所以从当前文件向上查找就能
    找到它；MINDBRIDGE_REPO_ROOT 用于覆盖这一行为，适用于安装到仓库之外的
    wheel 包场景，此时 compose 文件在别处。

    Returns:
        Absolute path to the repository root. 仓库根目录的绝对路径。
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

    中文：切换当前工作目录到仓库根目录并返回它。这不是无关紧要的装饰性操作:
    Settings 是按相对路径读取 `.env` 的，docker compose 也是根据当前工作
    目录来解析服务定义和挂载路径的。如果从别的目录启动，进程会悄悄退化为
    'hashing' 嵌入器，并得到一个空的 compose 项目。

    Returns:
        Absolute path to the repository root now set as cwd. 已设为当前工作
        目录的仓库根目录绝对路径。
    """
    root = repo_root()
    os.chdir(root)
    return root


def log_dir() -> Path:
    """Directory nightly jobs append their logs to. 返回夜间任务追加写日志所用的目录。"""
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

    中文：某个具名任务的只追加日志。日志行会写入文件(这也是 launchd 留存
    下来的内容)，同时镜像输出到 stderr，让手动运行该命令的人也能看到进度。
    这里刻意不用 isatty() 做门控:`mindbridge ingest | tail` 是很正常的用法，
    一旦被管道接了就变得悄无声息的命令看起来像是坏了。代价是定时任务会把
    同样的内容重复写进 launchd.err.log。
    """

    def __init__(self, name: str) -> None:
        """Open or rotate the log file for a job named ``name``.

        中文：打开或按需轮转名为 ``name`` 的任务日志文件。
        """
        directory = log_dir()
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / f"{name}.log"
        if self.path.is_file() and self.path.stat().st_size > MAX_LOG_BYTES:
            self.path.replace(self.path.with_name(self.path.name + ".1"))
        self._handle = self.path.open("a", encoding="utf-8", errors="replace")

    def __enter__(self) -> "JobLog":
        """Support ``with JobLog(...) as log:``. 支持 with JobLog(...) as log: 用法。"""
        return self

    def __exit__(self, *_exc: object) -> None:
        """Close the file handle when the with-block exits. 退出 with 块时关闭文件句柄。"""
        self._handle.close()

    def line(self, message: str) -> None:
        """Write one timestamped line. 写入一行带时间戳的日志。"""
        self.raw(f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}\n")

    def raw(self, text: str) -> None:
        """Write text to the log file and stderr, flushing both.

        中文：原样写入日志文件和 stderr，并立即刷新两者。
        """
        self._handle.write(text)
        self._handle.flush()
        sys.stderr.write(text)
        sys.stderr.flush()


def docker_running() -> bool:
    """Whether the Docker daemon answers.

    Docker Desktop is not up at boot or just after the laptop wakes, which is
    exactly when a nightly job fires.

    中文：检测 Docker 守护进程是否有响应。Docker Desktop 在开机时或电脑刚
    从睡眠中唤醒时通常还没起来，而这恰好正是夜间任务触发的时刻。

    Returns:
        True if `docker info` succeeds, False otherwise (including when the
        `docker` binary is missing). 若 `docker info` 成功返回 True，否则
        返回 False（包括找不到 docker 命令的情况）。
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
    """Run a child process, tee its output into the log, return its exit code.

    中文：运行一个子进程，把它的输出实时抄送一份到日志，并返回其退出码。

    Args:
        command: Argv to execute. 要执行的命令行参数列表。
        log: The JobLog to tee output into. 用来接收输出的 JobLog。

    Returns:
        The child process's exit code. 子进程的退出码。
    """
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

    中文：启动 db 和 redis 两个容器，并阻塞等待它们的健康检查通过。关键在于
    `--wait` 参数——没有它，ingest 在冷启动时会和数据库产生竞态。

    Args:
        log: Where to record the compose command's output. 记录 compose
            命令输出的日志对象。

    Returns:
        True if both services came up healthy. 两个服务都健康启动时返回 True。
    """
    return run_logged(
        ["docker", "compose", "up", "-d", "--wait", "db", "redis"], log
    ) == 0


def venv_python() -> str:
    """Interpreter for child Python processes.

    sys.executable is right whenever the CLI itself was launched from the venv,
    which an installed console script always is. The override exists for the
    shim scripts, which may be invoked by launchd with a bare PATH.

    中文：返回用于启动子 Python 进程的解释器路径。只要 CLI 本身是从 venv 里
    启动的（已安装的 console script 总是如此），sys.executable 就是正确的
    选择；这里留了一个环境变量覆盖项，供那些可能被 launchd 以裸 PATH 方式
    调用的转发脚本使用。

    Returns:
        Path to the Python interpreter to use. 应使用的 Python 解释器路径。
    """
    return os.environ.get("MINDBRIDGE_PYTHON_BIN") or sys.executable
