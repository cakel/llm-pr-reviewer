"""Tests for CLI main argument parsing and output handling."""

import contextlib
import io
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
            "--pr-author", "owner", "--max-diff-bytes", "20", "--max-total-diff-bytes", "30"]

    def run_main(self, diff, argv=()):
        from review_harness import main as m
        with tempfile.TemporaryDirectory() as tmp:
            out, summary = os.path.join(tmp, "github_output"), os.path.join(tmp, "step_summary")
            stdout = io.StringIO()
            with patch("sys.argv", self.ARGV + list(argv)), \
                 patch.dict(os.environ, {"GITHUB_OUTPUT": out, "GITHUB_STEP_SUMMARY": summary}), \
                 patch.object(m.GateKeeper, "is_authorized", return_value=(True, "ok")), \
                 patch.object(m.DiffGuard, "_git", return_value=diff), \
                 patch.object(m.DiffGuard, "verify_safety", return_value=(False, "stop here")) as verify, \
                 patch.object(m.CommentManager, "post_and_collapse_reviews") as post, \
                 contextlib.redirect_stdout(stdout):
                code = m.main()
            read = lambda p: open(p, encoding="utf-8").read() if os.path.exists(p) else ""
            self.output, self.summary, self.stdout = read(out), read(summary), stdout.getvalue()
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
        self.assertIn("--max-total-diff-bytes 31", body)
        self.assertIn("저장소의 `max-total-diff-bytes` 조정", body)     # raising max-diff-bytes only changes part size
        # The job ends green, so the check itself must say "not an approval"
        self.assertIn("::warning title=AI 리뷰 중지 (승인 아님)::diff 31 bytes > 한도 30 bytes", self.stdout)
        self.assertIn("추가 리뷰를 요청하지 마세요", self.summary)

    def test_oversize_without_comment_posting(self):
        code, post, _ = self.run_main("+" + "가" * 10, ["--no-post-comment"])
        self.assertEqual(code, 0)
        self.assertIn("decision=skipped_oversize\n", self.output)
        self.assertIn("AI 리뷰 중지 (승인 아님)", self.stdout)   # annotation does not depend on comment posting
        post.assert_not_called()

    def test_within_limit_reaches_safety_check_with_same_limit(self):
        code, post, verify = self.run_main("+" + "가" * 9)   # 28 bytes: above the per-call 20, so it is split, not skipped
        self.assertEqual(code, 1)                              # stopped by the mocked safety check
        post.assert_not_called()
        self.assertEqual(verify.call_args.kwargs["max_bytes"], 30)

    def test_fix_mode_is_never_split(self):
        # /fix stops at the per-call limit: 28 bytes > 20 is skipped even though the total cap is 30
        code, post, verify = self.run_main("+" + "가" * 9, ["--event-name", "issue_comment", "--comment-body", "/fix"])
        self.assertEqual(code, 0)
        self.assertIn("decision=skipped_oversize\n", self.output)
        verify.assert_not_called()
        self.assertIn("한도 20 bytes", post.call_args.kwargs["review_comment_markdown"])
        self.assertIn("저장소의 `max-diff-bytes` 조정", post.call_args.kwargs["review_comment_markdown"])

    def test_max_total_defaults_to_one_megabyte(self):
        with patch("sys.argv", ["main", "--repo", "owner/repo", "--pr", "1"]):
            self.assertEqual(parse_args().max_total_diff_bytes, 1_000_000)


class TestSplitReview(unittest.TestCase):
    def test_large_diff_is_reviewed_in_parts_and_posted_once(self):
        from review_harness import main as m
        from test_chunking import FakeAdapter, file_diff
        diff = "".join(file_diff(f"f{i}.py", lines=10) for i in range(4))
        fake = FakeAdapter("kiro")
        argv = ["main", "--repo", "owner/repo", "--pr", "7", "--base", "a" * 40, "--head", "b" * 40,
                "--pr-author", "owner", "--engines", "kiro", "--max-diff-bytes", str(len(diff) // 2),
                "--max-parallel", "2", "--no-submit-review"]
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "github_output")
            with patch("sys.argv", argv), patch.dict(os.environ, {"GITHUB_OUTPUT": out}), \
                 patch.dict(m.AVAILABLE_ADAPTERS, {"kiro": fake}, clear=True), \
                 patch.object(m.GateKeeper, "is_authorized", return_value=(True, "ok")), \
                 patch.object(m.DiffGuard, "_git", return_value=diff), \
                 patch.object(m.DiffGuard, "verify_safety", return_value=(True, "ok")), \
                 patch.object(m.DiffGuard, "get_sensitive_paths", return_value=[]), \
                 patch.object(m.DiffGuard, "build_change_summary", return_value=""), \
                 patch.object(m.CommentManager, "update_pr_description"), \
                 patch.object(m.CommentManager, "fetch_previous_findings", return_value=None), \
                 patch.object(m.StateManager, "record_success"), \
                 patch.object(m.CommentManager, "post_and_collapse_reviews") as post, \
                 contextlib.redirect_stdout(io.StringIO()):
                code = m.main()
            with open(out, encoding="utf-8") as f:
                output = f.read()
        self.assertEqual(code, 0)
        self.assertGreaterEqual(len(fake.calls), 2)
        post.assert_called_once()
        body = post.call_args.kwargs["review_comment_markdown"]
        self.assertIn(f"{len(fake.calls)}개 묶음으로 분할 리뷰", body)
        self.assertIn(f"minor {len(fake.calls)}", body)          # counts summed over parts
        self.assertIn("decision=approve\n", output)


if __name__ == "__main__":
    unittest.main()
