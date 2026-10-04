"""Unit tests for state manager."""

import tempfile
import time
import unittest
from pathlib import Path
from review_harness.state import StateManager


class TestState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state_file = Path(self.tmp.name) / "test_state.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_record_and_prioritize(self):
        mgr = StateManager(state_file_path=str(self.state_file), ttl_seconds=60)
        self.assertIsNone(mgr.get_last_successful_engine())

        mgr.record_success("codex")
        self.assertEqual(mgr.get_last_successful_engine(), "codex")

        ordered = mgr.prioritize_engines(["kiro", "codex", "agy"])
        self.assertEqual(ordered, ["codex", "kiro", "agy"])

    def test_ttl_expiry(self):
        mgr = StateManager(state_file_path=str(self.state_file), ttl_seconds=1)
        mgr.record_success("agy")
        time.sleep(1.2)
        self.assertIsNone(mgr.get_last_successful_engine())
        ordered = mgr.prioritize_engines(["kiro", "codex", "agy"])
        self.assertEqual(ordered, ["kiro", "codex", "agy"])


if __name__ == "__main__":
    unittest.main()
