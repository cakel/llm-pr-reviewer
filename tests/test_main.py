"""Tests for CLI main argument parsing and output handling."""

import os
import tempfile
import unittest
from unittest.mock import patch

from review_harness.main import parse_args


class TestMainArgs(unittest.TestCase):
    def test_default_submit_review(self):
        with patch("sys.argv", ["main", "--repo", "owner/repo", "--pr", "1"]):
            args = parse_args()
            self.assertTrue(args.submit_review)

    def test_no_submit_review_flag(self):
        with patch("sys.argv", ["main", "--repo", "owner/repo", "--pr", "1", "--no-submit-review"]):
            args = parse_args()
            self.assertFalse(args.submit_review)


if __name__ == "__main__":
    unittest.main()
