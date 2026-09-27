"""Expose the offline extraction contract checks to unittest discovery."""

import unittest

from extract.test_pipeline import main


class ExtractionPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_offline_contract_and_repair_loop(self) -> None:
        self.assertEqual(await main(), 0)
