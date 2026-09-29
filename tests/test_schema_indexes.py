"""Pins the decision that T3 embeddings have no approximate index."""

import re
import unittest
from pathlib import Path

SCHEMA = (Path(__file__).resolve().parents[1] / "api" / "schema.sql").read_text()


class EmbeddingIndexTests(unittest.TestCase):
    def test_no_ann_index_on_memory_vectors(self) -> None:
        # Write-time dedup needs the exact nearest neighbour. An ivfflat index at
        # default probes found 0 of 96 real duplicates; see AGENTS.md.
        creates = re.findall(r"CREATE INDEX[^;]*;", SCHEMA, flags=re.IGNORECASE)
        ann = [c for c in creates if re.search(r"\b(ivfflat|hnsw)\b", c.lower())]
        self.assertEqual(ann, [])

    def test_existing_stores_drop_the_old_index(self) -> None:
        self.assertIn("DROP INDEX IF EXISTS memory_vectors_embedding_idx", SCHEMA)


if __name__ == "__main__":
    unittest.main()
