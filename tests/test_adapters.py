"""Unit tests for engine adapters."""

import unittest
from unittest.mock import patch, MagicMock
from review_harness.adapters.claude import ClaudeAdapter
from review_harness.adapters.kiro import KiroAdapter
from review_harness.adapters.codex import CodexAdapter
from review_harness.adapters.agy import AgyAdapter


class TestAdapters(unittest.TestCase):
    def test_adapter_names(self):
        self.assertEqual(KiroAdapter().name, "kiro")
        self.assertEqual(CodexAdapter().name, "codex")
        self.assertEqual(AgyAdapter().name, "agy")
        self.assertEqual(ClaudeAdapter().name, "claude")

    def test_claude_detect_mocked(self):
        claude = ClaudeAdapter()
        # When claude binary is missing
        with patch("shutil.which", return_value=None):
            self.assertFalse(claude.detect())

        # When claude binary is present and --version succeeds
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("shutil.which", return_value="/usr/bin/claude"):
            with patch("subprocess.run", return_value=mock_result):
                self.assertTrue(claude.detect())

    def test_classify_error(self):
        claude = ClaudeAdapter()
        self.assertEqual(claude.classify_error("Rate limit exceeded 429", 1), "rate_limited")
        self.assertEqual(claude.classify_error("Insufficient quota for current plan", 1), "quota_exceeded")
        self.assertEqual(claude.classify_error("Unauthorized 401 token invalid", 1), "auth_failed")


if __name__ == "__main__":
    unittest.main()
