"""Codex transcript discovery must include live sessions without reading config."""

import tempfile
import unittest
from pathlib import Path

from ingest.codex_cli import discover


class CodexDiscoveryTests(unittest.TestCase):
    def test_discovers_active_and_archived_rollouts_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archived = root / "archived_sessions" / "rollout-old.jsonl"
            active = root / "sessions" / "2026" / "08" / "rollout-live.jsonl"
            unrelated = root / "credentials.jsonl"
            for path in (archived, active, unrelated):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}\n", encoding="utf-8")

            self.assertEqual(discover(root), sorted([archived, active]))

    def test_flat_archived_root_remains_supported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rollout = root / "rollout-legacy.jsonl"
            rollout.write_text("{}\n", encoding="utf-8")
            self.assertEqual(discover(root), [rollout])


if __name__ == "__main__":
    unittest.main()
