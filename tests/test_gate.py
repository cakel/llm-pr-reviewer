"""Unit tests for maintainer gate."""

import unittest
from unittest.mock import patch
from review_harness.gate import GateKeeper


class TestGate(unittest.TestCase):
    def test_unsupported_event(self):
        gate = GateKeeper(token="dummy")
        auth, reason = gate.is_authorized("owner/repo", "push", "actor", "author")
        self.assertFalse(auth)
        self.assertIn("Unsupported event", reason)

    def test_pr_event_author_authorized(self):
        gate = GateKeeper(token="dummy")
        with patch.object(gate, "check_user_permission", return_value=True):
            auth, reason = gate.is_authorized("owner/repo", "pull_request", "alice", "alice")
            self.assertTrue(auth)
            self.assertIn("authorized maintainer", reason)

    def test_pr_event_unauthorized(self):
        gate = GateKeeper(token="dummy")
        with patch.object(gate, "check_user_permission", return_value=False):
            auth, reason = gate.is_authorized("owner/repo", "pull_request", "stranger", "stranger")
            self.assertFalse(auth)
            self.assertIn("Neither PR author", reason)

    def test_comment_event_with_review_command(self):
        gate = GateKeeper(token="dummy")
        with patch.object(gate, "check_user_permission", return_value=True):
            auth, reason = gate.is_authorized(
                "owner/repo", "issue_comment", "maintainer", "stranger", comment_body="/review please check"
            )
            self.assertTrue(auth)
            self.assertIn("Review requested via '/review'", reason)

    def test_comment_event_without_command(self):
        gate = GateKeeper(token="dummy")
        with patch.object(gate, "check_user_permission", return_value=True):
            auth, reason = gate.is_authorized(
                "owner/repo", "issue_comment", "maintainer", "stranger", comment_body="just a regular comment"
            )
            self.assertFalse(auth)
            self.assertIn("does not contain explicit '/review'", reason)


if __name__ == "__main__":
    unittest.main()
