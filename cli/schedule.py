"""Manage the two nightly jobs as macOS LaunchAgents.

Installing a LaunchAgent is a persistent change to the machine, so `install`
prints the exact plist it would write and stops. Nothing reaches
~/Library/LaunchAgents without --confirm. `status`, `run-now` and the plist
preview change nothing.

The plist keeps pointing at scripts/nightly-*.sh rather than at the console
script on purpose: the plist outlives this venv, and a scheduled job should not
break the day the package is reinstalled under a different name. The shim is one
exec away from the CLI.

中文说明：把两个夜间任务作为 macOS LaunchAgent 来管理。安装 LaunchAgent
是对这台机器的持久性改动，所以 `install` 只会打印出它将要写入的完整 plist
内容然后停下，不带 --confirm 就不会有任何东西真正写进
~/Library/LaunchAgents；`status`、`run-now` 以及 plist 预览都不会改变任何
状态。plist 有意继续指向 scripts/nightly-*.sh，而不是直接指向 console
script:因为 plist 的生命周期比这个 venv 更长，重新安装包(哪怕换了名字)
的那天，已调度好的任务也不应该因此坏掉。那个转发壳脚本只需一次 exec 就能
调到 CLI。
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

from cli._runtime import log_dir, venv_python

# launchd starts with a minimal PATH, so a job would not find docker otherwise.
# 中文：launchd 启动时使用的是一个精简过的 PATH，不特意设置的话任务会
# 找不到 docker 命令。
LAUNCHD_PATH = "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"


@dataclass(frozen=True)
class Job:
    """Static description of one nightly job's schedule, script and logging.

    中文：描述某个夜间任务的调度时间、脚本路径和日志配置的静态数据类。
    """

    name: str
    label: str
    script: str
    log_name: str
    hour_env: str
    minute_env: str
    default_hour: int
    default_minute: int
    tail_lines: int
    launchd_stem: str

    def hour(self) -> int:
        """Return the scheduled hour from the environment or default.

        中文：返回由环境变量覆盖或默认的计划运行小时。
        """
        return int(os.environ.get(self.hour_env, self.default_hour))

    def minute(self) -> int:
        """Return the scheduled minute from the environment or default.

        中文：返回由环境变量覆盖或默认的计划运行分钟。
        """
        return int(os.environ.get(self.minute_env, self.default_minute))

    @property
    def plist_path(self) -> Path:
        """Where this job's LaunchAgent plist would live. 该任务的 LaunchAgent plist 文件路径。"""
        return Path.home() / "Library/LaunchAgents" / f"{self.label}.plist"

    @property
    def log_path(self) -> Path:
        """Where this job's log file lives. 该任务的日志文件路径。"""
        return log_dir() / f"{self.log_name}.log"


JOBS = {
    "ingest": Job(
        name="ingest",
        label="com.mindbridge.nightly-ingest",
        script="scripts/nightly-ingest.sh",
        log_name="ingest",
        hour_env="MINDBRIDGE_INGEST_HOUR",
        minute_env="MINDBRIDGE_INGEST_MINUTE",
        default_hour=23,
        default_minute=30,
        tail_lines=5,
        launchd_stem="launchd",
    ),
    "patterns": Job(
        name="patterns",
        label="com.mindbridge.nightly-patterns",
        script="scripts/nightly-patterns.sh",
        log_name="pattern-discovery",
        hour_env="MINDBRIDGE_PATTERN_HOUR",
        minute_env="MINDBRIDGE_PATTERN_MINUTE",
        default_hour=0,
        default_minute=45,
        tail_lines=8,
        launchd_stem="pattern-discovery",
    ),
}

# The pattern job carries its tuning in the plist's environment so a change of
# window or threshold is a re-install, visible in one file, rather than a
# difference between what runs at night and what a human runs by hand.
# 中文：pattern 任务把自己的调优参数放在 plist 的环境变量里，所以修改扫描
# 窗口或阈值就是重新安装一次、变化集中体现在一个文件里，而不会出现"夜里
# 跑的"和"人手动跑的"行为不一致的情况。
_PATTERN_ENV_DEFAULTS = {
    "MINDBRIDGE_PATTERN_APPLY": "0",
    "MINDBRIDGE_PATTERN_SCAN_LIMIT": "365",
    "MINDBRIDGE_PATTERN_SUPPORTING": "10",
    "MINDBRIDGE_PATTERN_DAILY_LIMIT": "40",
    "MINDBRIDGE_PATTERN_SINCE": "30d",
}


def _pattern_settings() -> dict[str, str]:
    """Current values for the pattern job's tunables, env override or default.

    中文：返回 pattern 任务各调优参数的当前值(环境变量覆盖值，否则用默认值)。
    """
    return {
        name: os.environ.get(name, default)
        for name, default in _PATTERN_ENV_DEFAULTS.items()
    }


def _program_arguments(job: Job, root: Path) -> list[str]:
    """Argv the plist should launch for this job.

    中文：该任务在 plist 中应当启动的完整命令行参数列表。
    """
    arguments = ["/bin/bash", str(root / job.script)]
    if job.name == "patterns":
        # The window is a positional argument, which is how the installed plist
        # has always passed it.
        # 中文：扫描窗口是一个位置参数，已安装的 plist 一直都是这样传递的。
        arguments.append(_pattern_settings()["MINDBRIDGE_PATTERN_SINCE"])
    return arguments


def _persisted_python(root: Path) -> str:
    """Interpreter to write into the plist.

    Prefers .venv/bin/python over sys.executable's versioned name: the plist
    outlives a Python minor upgrade, and .venv/bin/python3.12 stops existing the
    day the venv is rebuilt on 3.13 while the unversioned symlink follows along.

    中文：返回要写入 plist 的解释器路径。优先使用 .venv/bin/python，而不是
    sys.executable 那个带版本号的名字:plist 的寿命比一次 Python 小版本升级
    更长，一旦 venv 在 3.13 上重建，.venv/bin/python3.12 就不存在了，而不带
    版本号的符号链接会一直跟着更新。

    Args:
        root: Repository root, used to locate .venv. 仓库根目录，用于定位 .venv。

    Returns:
        Path to the interpreter to write into the plist. 应写入 plist 的
        解释器路径。
    """
    candidate = root / ".venv/bin/python"
    return str(candidate) if candidate.exists() else venv_python()


def _environment(job: Job, root: Path) -> dict[str, str]:
    """Environment variables the plist should set for this job.

    中文：该任务在 plist 中应当设置的环境变量。
    """
    environment = {"PATH": LAUNCHD_PATH}
    if job.name == "patterns":
        environment.update(_pattern_settings())
        environment["MINDBRIDGE_PYTHON_BIN"] = _persisted_python(root)
    return environment


def build_plist(job: Job, root: Path) -> str:
    """Render the full plist XML this job would be installed with.

    中文：渲染出该任务将被安装时使用的完整 plist XML 内容。

    Args:
        job: The job to build a plist for. 要为其生成 plist 的任务。
        root: Repository root, used to resolve the job's script path and
            interpreter. 仓库根目录，用于解析该任务的脚本路径和解释器路径。

    Returns:
        The plist file contents as a string. plist 文件内容的字符串形式。
    """
    directory = log_dir()
    arguments = "\n".join(
        f"    <string>{escape(argument)}</string>"
        for argument in _program_arguments(job, root)
    )
    environment = "\n".join(
        f"    <key>{escape(name)}</key>\n    <string>{escape(value)}</string>"
        for name, value in _environment(job, root).items()
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{job.label}</string>
  <key>ProgramArguments</key>
  <array>
{arguments}
  </array>
  <key>EnvironmentVariables</key>
  <dict>
{environment}
  </dict>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>{job.hour()}</integer>
    <key>Minute</key><integer>{job.minute()}</integer>
  </dict>
  <key>RunAtLoad</key>
  <false/>
  <key>StandardOutPath</key>
  <string>{escape(str(directory / f"{job.launchd_stem}.out.log"))}</string>
  <key>StandardErrorPath</key>
  <string>{escape(str(directory / f"{job.launchd_stem}.err.log"))}</string>
</dict>
</plist>
"""


def _loaded(job: Job) -> bool:
    """Whether launchd currently has this job's LaunchAgent loaded.

    中文：launchd 当前是否已加载该任务对应的 LaunchAgent。
    """
    return (
        subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{job.label}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        == 0
    )


def _status(job: Job, root: Path) -> int:
    """Print the job's plist/launchd/log/settings state. Changes nothing.

    中文：打印该任务的 plist、launchd 加载状态、日志和配置信息，不改动任何东西。

    Args:
        job: The job to report on. 要报告状态的任务。
        root: Repository root, used to resolve the persisted interpreter
            path. 仓库根目录，用于解析持久化的解释器路径。

    Returns:
        Always 0. 恒为 0。
    """
    if job.plist_path.is_file():
        print(f"plist:     present ({job.plist_path})")
    else:
        print("plist:     not installed")
    when = f"{job.hour():02d}:{job.minute():02d}"
    print(f"launchd:   {'loaded, daily at ' + when if _loaded(job) else 'not loaded'}")

    if job.log_path.is_file():
        lines = job.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        print("last log lines:")
        for line in lines[-job.tail_lines :]:
            print(f"  {line}")
    else:
        print(f"log:       none yet ({job.log_path})")

    if job.name == "patterns":
        print("settings:")
        for name, value in _pattern_settings().items():
            print(f"  {name.removeprefix('MINDBRIDGE_PATTERN_').lower():<12} {value}")
        # The interpreter the plist would carry, not the one running this command.
        # 中文：这里打印的是 plist 里将会写入的解释器路径，而不是正在运行
        # 本命令的解释器。
        print(f"  {'python':<12} {_persisted_python(root)}")
    return 0


def _install(job: Job, root: Path, confirm: bool) -> int:
    """Print the plist, and write + bootstrap it only if `confirm` is set.

    中文：打印出 plist 内容；只有当 confirm 为真时才会真正写入文件并让
    launchd 加载它。

    Args:
        job: The job to install. 要安装的任务。
        root: Repository root, used to resolve script and interpreter paths.
            仓库根目录，用于解析脚本路径和解释器路径。
        confirm: Whether to actually write the plist and load it. 是否真正
            写入 plist 并加载它。

    Returns:
        0 on success (including the dry-run preview), or the `launchctl
        bootstrap` subprocess's exit code on failure. 成功时返回 0(包括
        仅预览的 dry-run 情况)；失败时返回 `launchctl bootstrap` 子进程的
        退出码。
    """
    plist = build_plist(job, root)
    print(f"# {job.plist_path}")
    print(plist)
    if not confirm:
        print(
            f"Nothing was written. This would schedule {job.label} daily at "
            f"{job.hour():02d}:{job.minute():02d} and register it with launchd — "
            "a persistent change to this machine.\n"
            f"Re-run with --confirm to write it: "
            f"mindbridge schedule {job.name} install --confirm"
        )
        return 0

    job.plist_path.parent.mkdir(parents=True, exist_ok=True)
    log_dir().mkdir(parents=True, exist_ok=True)
    job.plist_path.write_text(plist, encoding="utf-8")
    # bootout first so re-installing picks up a changed schedule.
    # 中文：先执行一次 bootout，这样重新安装时才能应用改动后的调度时间。
    subprocess.run(
        ["launchctl", "bootout", f"gui/{os.getuid()}/{job.label}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    result = subprocess.run(
        ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(job.plist_path)]
    )
    if result.returncode != 0:
        return result.returncode
    print(
        f"installed: {job.label} runs daily at {job.hour():02d}:{job.minute():02d}"
    )
    print(f"plist:     {job.plist_path}")
    print(f"log:       {job.log_path}")
    if job.name == "ingest":
        print()
        print("Docker Desktop must be running at that hour, or the job logs a")
        print("skip and leaves the cursors alone.")
    else:
        print()
        print("Dry-run mode unless MINDBRIDGE_PATTERN_APPLY=1 was set for this")
        print("install; check `settings` in `mindbridge schedule patterns status`.")
    return 0


def _uninstall(job: Job) -> int:
    """Unload the LaunchAgent (if loaded) and delete its plist. Logs are kept.

    中文：卸载(若已加载则先移除)该任务的 LaunchAgent 并删除其 plist 文件，
    日志文件保留不动。

    Args:
        job: The job to uninstall. 要卸载的任务。

    Returns:
        Always 0. 恒为 0。
    """
    subprocess.run(
        ["launchctl", "bootout", f"gui/{os.getuid()}/{job.label}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    job.plist_path.unlink(missing_ok=True)
    print(f"removed: {job.label} (logs kept in {log_dir()})")
    return 0


def run(job_name: str, action: str, root: Path, *, confirm: bool = False) -> int:
    """Dispatch to status/install/uninstall for the named job.

    中文：根据指定的 job 名称，把请求分发给 status/install/uninstall 之一。

    Args:
        job_name: Key into JOBS, either "ingest" or "patterns". JOBS 字典的
            键，取值为 "ingest" 或 "patterns"。
        action: One of "status", "install" or "uninstall". 三种动作之一。
        root: Repository root, forwarded to `_status`/`_install`. 仓库根
            目录，转发给 `_status`/`_install`。
        confirm: Forwarded to `_install`; ignored for other actions. 转发给
            `_install`；其余动作忽略此参数。

    Returns:
        The exit code from the dispatched function. 被分发到的函数返回的
        退出码。

    Raises:
        ValueError: If `action` is not one of the three known actions.
            当 action 不是三种已知动作之一时抛出。
    """
    job = JOBS[job_name]
    if action == "status":
        return _status(job, root)
    if action == "install":
        return _install(job, root, confirm)
    if action == "uninstall":
        return _uninstall(job)
    raise ValueError(f"unknown action: {action}")
