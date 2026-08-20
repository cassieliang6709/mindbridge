"""Manage the two nightly jobs as macOS LaunchAgents.

Installing a LaunchAgent is a persistent change to the machine, so `install`
prints the exact plist it would write and stops. Nothing reaches
~/Library/LaunchAgents without --confirm. `status`, `run-now` and the plist
preview change nothing.

The plist keeps pointing at scripts/nightly-*.sh rather than at the console
script on purpose: the plist outlives this venv, and a scheduled job should not
break the day the package is reinstalled under a different name. The shim is one
exec away from the CLI.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

from cli._runtime import log_dir, venv_python

# launchd starts with a minimal PATH, so a job would not find docker otherwise.
LAUNCHD_PATH = "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"


@dataclass(frozen=True)
class Job:
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
        return int(os.environ.get(self.hour_env, self.default_hour))

    def minute(self) -> int:
        return int(os.environ.get(self.minute_env, self.default_minute))

    @property
    def plist_path(self) -> Path:
        return Path.home() / "Library/LaunchAgents" / f"{self.label}.plist"

    @property
    def log_path(self) -> Path:
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
_PATTERN_ENV_DEFAULTS = {
    "MINDBRIDGE_PATTERN_APPLY": "0",
    "MINDBRIDGE_PATTERN_SCAN_LIMIT": "365",
    "MINDBRIDGE_PATTERN_SUPPORTING": "10",
    "MINDBRIDGE_PATTERN_DAILY_LIMIT": "40",
    "MINDBRIDGE_PATTERN_SINCE": "30d",
}


def _pattern_settings() -> dict[str, str]:
    return {
        name: os.environ.get(name, default)
        for name, default in _PATTERN_ENV_DEFAULTS.items()
    }


def _program_arguments(job: Job, root: Path) -> list[str]:
    arguments = ["/bin/bash", str(root / job.script)]
    if job.name == "patterns":
        # The window is a positional argument, which is how the installed plist
        # has always passed it.
        arguments.append(_pattern_settings()["MINDBRIDGE_PATTERN_SINCE"])
    return arguments


def _persisted_python(root: Path) -> str:
    """Interpreter to write into the plist.

    Prefers .venv/bin/python over sys.executable's versioned name: the plist
    outlives a Python minor upgrade, and .venv/bin/python3.12 stops existing the
    day the venv is rebuilt on 3.13 while the unversioned symlink follows along.
    """
    candidate = root / ".venv/bin/python"
    return str(candidate) if candidate.exists() else venv_python()


def _environment(job: Job, root: Path) -> dict[str, str]:
    environment = {"PATH": LAUNCHD_PATH}
    if job.name == "patterns":
        environment.update(_pattern_settings())
        environment["MINDBRIDGE_PYTHON_BIN"] = _persisted_python(root)
    return environment


def build_plist(job: Job, root: Path) -> str:
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
    return (
        subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{job.label}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        == 0
    )


def _status(job: Job, root: Path) -> int:
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
        print(f"  {'python':<12} {_persisted_python(root)}")
    return 0


def _install(job: Job, root: Path, confirm: bool) -> int:
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
    subprocess.run(
        ["launchctl", "bootout", f"gui/{os.getuid()}/{job.label}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    job.plist_path.unlink(missing_ok=True)
    print(f"removed: {job.label} (logs kept in {log_dir()})")
    return 0


def run(job_name: str, action: str, root: Path, *, confirm: bool = False) -> int:
    job = JOBS[job_name]
    if action == "status":
        return _status(job, root)
    if action == "install":
        return _install(job, root, confirm)
    if action == "uninstall":
        return _uninstall(job)
    raise ValueError(f"unknown action: {action}")
