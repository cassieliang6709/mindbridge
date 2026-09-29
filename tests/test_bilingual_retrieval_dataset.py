"""The bilingual retrieval dataset is well-formed. No network, no embedder."""

from __future__ import annotations

import re
import unittest

from evals.bilingual_retrieval import CONDITIONS, load_dataset

HAN = re.compile(r"[一-鿿]")


class BilingualRetrievalDatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.memories, cls.queries = load_dataset()

    def test_memory_keys_and_texts_are_unique(self) -> None:
        keys = [memory["key"] for memory in self.memories]
        texts = [memory["text"] for memory in self.memories]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(len(texts), len(set(texts)))

    def test_memory_lang_matches_text(self) -> None:
        for memory in self.memories:
            with self.subTest(key=memory["key"]):
                self.assertIn(memory["lang"], ("en", "zh"))
                self.assertEqual(
                    memory["lang"] == "zh", bool(HAN.search(memory["text"]))
                )

    def test_every_target_exists_and_is_english(self) -> None:
        by_key = {memory["key"]: memory for memory in self.memories}
        for query in self.queries:
            with self.subTest(target=query["target"]):
                self.assertIn(query["target"], by_key)
                self.assertEqual(by_key[query["target"]]["lang"], "en")

    def test_one_query_per_target(self) -> None:
        targets = [query["target"] for query in self.queries]
        self.assertEqual(len(targets), len(set(targets)))

    def test_all_three_conditions_present(self) -> None:
        for query in self.queries:
            with self.subTest(target=query["target"]):
                for condition in CONDITIONS:
                    self.assertTrue(query.get(condition, "").strip())
                self.assertFalse(HAN.search(query["en"]))
                self.assertTrue(HAN.search(query["zh"]))
                self.assertTrue(HAN.search(query["mixed"]))
                self.assertTrue(re.search(r"[A-Za-z]", query["mixed"]))


if __name__ == "__main__":
    unittest.main()
