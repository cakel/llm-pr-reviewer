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

    def test_diff_file_flag(self):
        with patch("sys.argv", ["main", "--repo", "owner/repo", "--pr", "1", "--diff-file", "/tmp/test.diff"]):
            args = parse_args()
            self.assertEqual(args.diff_file, "/tmp/test.diff")

    def test_summary_flags(self):
        with patch("sys.argv", ["main", "--repo", "owner/repo", "--pr", "1", "--summary-key", "SECRET_KEY_123", "--summary-file", "/tmp/summary.json"]):
            args = parse_args()
            self.assertEqual(args.summary_key, "SECRET_KEY_123")
            self.assertEqual(args.summary_file, "/tmp/summary.json")

    def test_max_diff_bytes_flag(self):
        with patch("sys.argv", ["main", "--repo", "owner/repo", "--pr", "1"]):
            self.assertEqual(parse_args().max_diff_bytes, 200_000)
        with patch("sys.argv", ["main", "--repo", "owner/repo", "--pr", "1", "--max-diff-bytes", "500000"]):
            self.assertEqual(parse_args().max_diff_bytes, 500_000)


class TestOversizeDiff(unittest.TestCase):
    ARGV = ["main", "--repo", "owner/repo", "--pr", "7", "--base", "a" * 40, "--head", "b" * 40,
            "--pr-author", "owner", "--max-diff-bytes", "30"]

    def run_main(self, diff, argv=()):
        from review_harness import main as m
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "github_output")
            with patch("sys.argv", self.ARGV + list(argv)), patch.dict(os.environ, {"GITHUB_OUTPUT": out}), \
                 patch.object(m.GateKeeper, "is_authorized", return_value=(True, "ok")), \
                 patch.object(m.DiffGuard, "_git", return_value=diff), \
                 patch.object(m.DiffGuard, "verify_safety", return_value=(False, "stop here")) as verify, \
                 patch.object(m.CommentManager, "post_and_collapse_reviews") as post:
                code = m.main()
            self.output = open(out, encoding="utf-8").read() if os.path.exists(out) else ""
        return code, post, verify

    def test_oversize_is_neutral_and_says_stop(self):
        code, post, verify = self.run_main("+" + "가" * 10)   # 31 bytes > 30
        self.assertEqual(code, 0)                               # not red: a red check invites endless retries
        self.assertIn("decision=skipped_oversize\n", self.output)  # never 'approve' — gates on decision still block
        self.assertNotIn("approve", self.output)
        verify.assert_not_called()
        body = post.call_args.kwargs["review_comment_markdown"]
        self.assertEqual(post.call_args.kwargs["head_sha"], "b" * 40)
        self.assertIn("<!-- kiro-review-comment -->", body)    # same marker → older reviews get folded
        self.assertIn(f"kiro-review-sha:{'b' * 40}", body)
        self.assertIn("**31 bytes** > 한도 30 bytes", body)
        self.assertIn("추가 리뷰를 요청하지 마세요", body)
        self.assertIn("VERDICT: skipped_oversize", body)       # folded title shows it, not 'unknown'
        self.assertIn("--max-diff-bytes 31", body)

    def test_oversize_without_comment_posting(self):
        code, post, _ = self.run_main("+" + "가" * 10, ["--no-post-comment"])
        self.assertEqual(code, 0)
        self.assertIn("decision=skipped_oversize\n", self.output)
        post.assert_not_called()

    def test_within_limit_reaches_safety_check_with_same_limit(self):
        code, post, verify = self.run_main("+" + "가" * 9)   # 28 bytes
        self.assertEqual(code, 1)                              # stopped by the mocked safety check
        post.assert_not_called()
        self.assertEqual(verify.call_args.kwargs["max_bytes"], 30)


if __name__ == "__main__":
    unittest.main()
