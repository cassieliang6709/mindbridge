"""Unit tests for the CLI's argument translation and skip conditions.

These commands replaced shell scripts that a LaunchAgent runs unattended, so the
things worth pinning are the ones nobody would notice breaking: that a run with
Docker down changes nothing and exits 0, that write mode is off unless it is
asked for, and that unknown flags reach the underlying module instead of being
swallowed.
"""

from __future__ import annotations

import contextlib
import io
import os
import pathlib
import tempfile
import unittest

import plistlib
import subprocess

import cli.__main__ as entry
import cli.install as install_module
import cli.schedule as schedule_module
import cli.verify as verify_module


class CommandBuildingTest(unittest.TestCase):
    def setUp(self) -> None:
        # Point the job log at a scratch directory: a test must not append to
        # ~/Library/Logs/mindbridge, which doctor reads back as job history.
        self._log_dir = tempfile.TemporaryDirectory()
        self._previous = {
            name: os.environ.get(name)
            for name in ("MINDBRIDGE_LOG_DIR", "MINDBRIDGE_PATTERN_APPLY")
        }
        os.environ["MINDBRIDGE_LOG_DIR"] = self._log_dir.name
        os.environ.pop("MINDBRIDGE_PATTERN_APPLY", None)

        self._saved = (entry.docker_running, entry.compose_up_data_layer, entry.run_logged)
        self.commands: list[list[str]] = []
        entry.docker_running = lambda: True
        entry.compose_up_data_layer = lambda log: True
        entry.run_logged = lambda command, log: (self.commands.append(command), 0)[1]

    def tearDown(self) -> None:
        entry.docker_running, entry.compose_up_data_layer, entry.run_logged = self._saved
        for name, value in self._previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self._log_dir.cleanup()

    def run_cli(self, argv: list[str]) -> int:
        with contextlib.redirect_stderr(io.StringIO()):
            return entry.main(argv)

    def test_ingest_defaults_to_a_three_day_window(self) -> None:
        self.assertEqual(self.run_cli(["ingest"]), 0)
        self.assertEqual(
            self.commands[0],
            ["docker", "compose", "run", "--rm", "ingest", "--since", "3d"],
        )

    def test_ingest_forwards_unknown_flags_to_the_runner(self) -> None:
        self.run_cli(["ingest", "--since", "7d", "--dry-run", "--full"])
        self.assertEqual(self.commands[0][-4:], ["--since", "7d", "--dry-run", "--full"])

    def test_ingest_skips_quietly_when_docker_is_down(self) -> None:
        entry.docker_running = lambda: False
        self.assertEqual(self.run_cli(["ingest"]), 0)
        # Nothing ran, so no cursor moved and the next run picks up the same work.
        self.assertEqual(self.commands, [])

    def test_ingest_fails_when_the_data_layer_will_not_start(self) -> None:
        entry.compose_up_data_layer = lambda log: False
        self.assertEqual(self.run_cli(["ingest"]), 1)
        self.assertEqual(self.commands, [])

    def test_ingest_propagates_the_child_exit_code(self) -> None:
        entry.run_logged = lambda command, log: 3
        self.assertEqual(self.run_cli(["ingest"]), 3)

    def test_patterns_is_read_only_by_default(self) -> None:
        self.run_cli(["patterns"])
        self.assertNotIn("--apply", self.commands[0])
        self.assertEqual(self.commands[0][3:5], ["--since", "30d"])

    def test_patterns_writes_only_when_asked(self) -> None:
        self.run_cli(["patterns", "--apply"])
        self.assertIn("--apply", self.commands[0])

    def test_patterns_honours_the_scheduler_environment_variable(self) -> None:
        os.environ["MINDBRIDGE_PATTERN_APPLY"] = "1"
        self.run_cli(["patterns"])
        self.assertIn("--apply", self.commands[0])

    def test_patterns_treats_any_other_value_as_off(self) -> None:
        os.environ["MINDBRIDGE_PATTERN_APPLY"] = "0"
        self.run_cli(["patterns"])
        self.assertNotIn("--apply", self.commands[0])

    def test_patterns_forwards_unknown_flags(self) -> None:
        self.run_cli(["patterns", "--min-evidence", "5"])
        self.assertEqual(self.commands[0][-2:], ["--min-evidence", "5"])

    def test_schedule_run_now_reuses_the_plain_command(self) -> None:
        self.run_cli(["schedule", "ingest", "run-now"])
        self.assertEqual(
            self.commands[0],
            ["docker", "compose", "run", "--rm", "ingest", "--since", "3d"],
        )

    def test_schedule_run_now_uses_the_scheduled_pattern_window(self) -> None:
        os.environ["MINDBRIDGE_PATTERN_SINCE"] = "14d"
        try:
            self.run_cli(["schedule", "patterns", "run-now"])
        finally:
            os.environ.pop("MINDBRIDGE_PATTERN_SINCE")
        self.assertEqual(self.commands[0][3:5], ["--since", "14d"])

    def test_commands_without_passthrough_reject_unknown_flags(self) -> None:
        for argv in (
            ["doctor", "--nope"],
            ["mcp", "--nope"],
            ["install", "claude", "--nope"],
            ["schedule", "ingest", "status", "--nope"],
        ):
            with self.subTest(argv=argv):
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        entry.main(argv)


class PlistTest(unittest.TestCase):
    """The plist is a persistent artifact, so its contents are pinned."""

    def setUp(self) -> None:
        self.root = pathlib.Path("/checkout")
        self._previous = {
            name: os.environ.get(name)
            for name in (
                "MINDBRIDGE_LOG_DIR",
                "MINDBRIDGE_INGEST_HOUR",
                "MINDBRIDGE_PATTERN_APPLY",
                "MINDBRIDGE_PATTERN_SINCE",
            )
        }
        os.environ["MINDBRIDGE_LOG_DIR"] = "/logs"
        for name in list(self._previous)[1:]:
            os.environ.pop(name, None)

    def tearDown(self) -> None:
        for name, value in self._previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def parse(self, job_name: str) -> dict:
        job = schedule_module.JOBS[job_name]
        return plistlib.loads(schedule_module.build_plist(job, self.root).encode())

    def test_ingest_plist_matches_the_shell_script_it_replaced(self) -> None:
        plist = self.parse("ingest")
        self.assertEqual(plist["Label"], "com.mindbridge.nightly-ingest")
        self.assertEqual(
            plist["ProgramArguments"],
            ["/bin/bash", "/checkout/scripts/nightly-ingest.sh"],
        )
        self.assertEqual(plist["StartCalendarInterval"], {"Hour": 23, "Minute": 30})
        self.assertFalse(plist["RunAtLoad"])
        # PATH only: the ingest job reads its window from the command line.
        self.assertEqual(list(plist["EnvironmentVariables"]), ["PATH"])

    def test_patterns_plist_carries_its_tuning_and_window(self) -> None:
        plist = self.parse("patterns")
        self.assertEqual(plist["Label"], "com.mindbridge.nightly-patterns")
        self.assertEqual(plist["ProgramArguments"][-1], "30d")
        environment = plist["EnvironmentVariables"]
        self.assertEqual(environment["MINDBRIDGE_PATTERN_APPLY"], "0")
        self.assertEqual(environment["MINDBRIDGE_PATTERN_SINCE"], "30d")
        self.assertEqual(plist["StartCalendarInterval"], {"Hour": 0, "Minute": 45})

    def test_apply_mode_is_only_written_when_asked_for(self) -> None:
        os.environ["MINDBRIDGE_PATTERN_APPLY"] = "1"
        os.environ["MINDBRIDGE_PATTERN_SINCE"] = "14d"
        plist = self.parse("patterns")
        self.assertEqual(plist["EnvironmentVariables"]["MINDBRIDGE_PATTERN_APPLY"], "1")
        self.assertEqual(plist["ProgramArguments"][-1], "14d")

    def test_hour_override_reaches_the_schedule(self) -> None:
        os.environ["MINDBRIDGE_INGEST_HOUR"] = "2"
        self.assertEqual(self.parse("ingest")["StartCalendarInterval"]["Hour"], 2)


class InstallGuardTest(unittest.TestCase):
    """`install` must not touch the machine until it is confirmed."""

    def setUp(self) -> None:
        self.home = tempfile.TemporaryDirectory()
        self._previous_home = os.environ.get("HOME")
        self._previous_log = os.environ.get("MINDBRIDGE_LOG_DIR")
        os.environ["HOME"] = self.home.name
        os.environ["MINDBRIDGE_LOG_DIR"] = str(pathlib.Path(self.home.name) / "logs")
        self.calls: list[list[str]] = []
        self._saved_run = schedule_module.subprocess.run
        schedule_module.subprocess.run = lambda command, **kwargs: self.calls.append(
            command
        ) or subprocess.CompletedProcess(command, 0)

    def tearDown(self) -> None:
        schedule_module.subprocess.run = self._saved_run
        for name, value in (
            ("HOME", self._previous_home),
            ("MINDBRIDGE_LOG_DIR", self._previous_log),
        ):
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self.home.cleanup()

    def test_preview_writes_nothing_and_calls_no_launchctl(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()) as output:
            status = schedule_module.run(
                "patterns", "install", pathlib.Path("/checkout"), confirm=False
            )
        self.assertEqual(status, 0)
        self.assertEqual(self.calls, [])
        self.assertFalse(schedule_module.JOBS["patterns"].plist_path.exists())
        self.assertIn("Nothing was written", output.getvalue())

    def test_confirm_writes_the_plist_and_bootstraps_it(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            status = schedule_module.run(
                "patterns", "install", pathlib.Path("/checkout"), confirm=True
            )
        self.assertEqual(status, 0)
        path = schedule_module.JOBS["patterns"].plist_path
        self.assertTrue(path.is_file())
        self.assertEqual([call[1] for call in self.calls], ["bootout", "bootstrap"])

    def test_uninstall_removes_the_plist(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            schedule_module.run(
                "patterns", "install", pathlib.Path("/checkout"), confirm=True
            )
            schedule_module.run("patterns", "uninstall", pathlib.Path("/checkout"))
        self.assertFalse(schedule_module.JOBS["patterns"].plist_path.exists())


class ClientRegistrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.root.name)
        (root / ".env").write_text("MINDBRIDGE_EMBEDDING_PROVIDER=ollama\n")
        (root / ".venv/bin").mkdir(parents=True)
        (root / ".venv/bin/mindbridge-mcp").write_text("#!/bin/sh\n")
        self.calls: list[list[str]] = []
        self._saved = (install_module.shutil.which, install_module.subprocess.run)
        install_module.shutil.which = lambda command: f"/usr/bin/{command}"

    def tearDown(self) -> None:
        install_module.shutil.which, install_module.subprocess.run = self._saved
        self.root.cleanup()

    def _respond(self, get_returncode: int):
        def fake_run(command, **kwargs):
            self.calls.append(command)
            code = get_returncode if command[1:3] == ["mcp", "get"] else 0
            return subprocess.CompletedProcess(command, code)

        install_module.subprocess.run = fake_run

    def test_an_existing_registration_is_left_alone(self) -> None:
        self._respond(0)
        with contextlib.redirect_stdout(io.StringIO()):
            status = install_module.run("claude", pathlib.Path(self.root.name))
        self.assertEqual(status, 0)
        self.assertEqual([call[2] for call in self.calls], ["get"])

    def test_claude_is_registered_at_user_scope(self) -> None:
        self._respond(1)
        with contextlib.redirect_stdout(io.StringIO()):
            install_module.run("claude", pathlib.Path(self.root.name))
        add = self.calls[-1]
        self.assertEqual(add[:5], ["claude", "mcp", "add", "--scope", "user"])
        self.assertTrue(add[-1].endswith(".venv/bin/mindbridge-mcp"))

    def test_codex_has_no_scope_flag(self) -> None:
        self._respond(1)
        with contextlib.redirect_stdout(io.StringIO()):
            install_module.run("codex", pathlib.Path(self.root.name))
        self.assertEqual(self.calls[-1][:3], ["codex", "mcp", "add"])
        self.assertNotIn("--scope", self.calls[-1])

    def test_a_missing_launcher_stops_before_touching_the_client(self) -> None:
        self._respond(1)
        (pathlib.Path(self.root.name) / ".venv/bin/mindbridge-mcp").unlink()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            status = install_module.run("claude", pathlib.Path(self.root.name))
        self.assertEqual(status, 1)
        self.assertEqual(self.calls, [])
        self.assertIn("pip install -e .", output.getvalue())


class VerifyPlanTest(unittest.TestCase):
    """`verify` decides what to start by probing, and undoes only its own work."""

    def setUp(self) -> None:
        self.root = pathlib.Path("/checkout")
        self.calls: list[list[str]] = []
        self._saved = {
            name: getattr(verify_module, name)
            for name in ("_probe", "_docker_up", "_compose_running", "_preflight", "_check_embedder")
        }
        self._saved_run = verify_module.subprocess.run
        verify_module._preflight = lambda root: None
        verify_module._check_embedder = lambda: None
        verify_module._probe = lambda url, timeout=2.0: True
        verify_module._docker_up = lambda: True
        verify_module._compose_running = lambda service: True
        verify_module.subprocess.run = lambda command, **kwargs: self.calls.append(
            list(command)
        ) or subprocess.CompletedProcess(list(command), 0)

    def tearDown(self) -> None:
        for name, value in self._saved.items():
            setattr(verify_module, name, value)
        verify_module.subprocess.run = self._saved_run

    def run_verify(self, root: pathlib.Path | None = None, **kwargs) -> tuple[int, str]:
        with contextlib.redirect_stdout(io.StringIO()) as output:
            status = verify_module.run(root or self.root, **kwargs)
        return status, output.getvalue()

    def test_plan_reports_a_fully_running_stack_and_starts_nothing(self) -> None:
        status, output = self.run_verify(plan_only=True)
        self.assertEqual(status, 0)
        self.assertNotIn("START", output)
        self.assertIn("Nothing was started", output)
        self.assertEqual(self.calls, [])

    def test_plan_marks_every_missing_service(self) -> None:
        verify_module._probe = lambda url, timeout=2.0: False
        verify_module._compose_running = lambda service: False
        _, output = self.run_verify(plan_only=True)
        self.assertEqual(output.count("START"), 7)

    def test_a_docker_daemon_that_is_down_is_reported_not_guessed(self) -> None:
        verify_module._docker_up = lambda: False
        plan = verify_module.build_plan(self.root)
        self.assertTrue(plan.docker and plan.db and plan.redis)
        self.assertIn("assumed missing", plan.notes[0])

    def test_nothing_already_running_is_shut_down(self) -> None:
        status, _ = self.run_verify()
        self.assertEqual(status, 0)
        commands = [" ".join(call) for call in self.calls]
        self.assertNotIn("open -gj -a Docker", commands)
        self.assertFalse([c for c in commands if "compose stop" in c])
        self.assertTrue([c for c in commands if c.endswith("scripts.verify_local_loop")])

    def test_a_service_this_run_started_is_stopped_again(self) -> None:
        verify_module._compose_running = lambda service: service != "db"
        self.run_verify()
        commands = [" ".join(call) for call in self.calls]
        self.assertIn("docker compose stop db", commands)
        self.assertNotIn("docker compose stop redis", commands)
        # The teardown must come after the verification, not instead of it.
        self.assertLess(
            next(i for i, c in enumerate(commands) if c.endswith("verify_local_loop")),
            commands.index("docker compose stop db"),
        )

    def test_a_failed_run_keeps_its_logs(self) -> None:
        verify_module.subprocess.run = lambda command, **kwargs: self.calls.append(
            list(command)
        ) or subprocess.CompletedProcess(
            list(command), 1 if "scripts.verify_local_loop" in command else 0
        )
        status, output = self.run_verify()
        self.assertEqual(status, 1)
        kept = output.rsplit("Service logs kept in ", 1)[1].strip()
        self.assertTrue(pathlib.Path(kept).is_dir())
        pathlib.Path(kept).rmdir()

    def test_a_passing_run_cleans_up_after_itself(self) -> None:
        _, output = self.run_verify()
        self.assertNotIn("Service logs kept", output)

    def test_a_missing_adapter_fails_before_anything_starts(self) -> None:
        verify_module._preflight = self._saved["_preflight"]
        status, output = self.run_verify(root=pathlib.Path("/nowhere"))
        self.assertEqual(status, 1)
        self.assertIn("FAIL", output)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
