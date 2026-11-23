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

    def run_detect(self, status_stdout, env=None, status_exc=None):
        import os
        version = MagicMock(returncode=0)
        status = MagicMock(returncode=0 if '"loggedIn": true' in status_stdout else 1, stdout=status_stdout)
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[:2] == ["claude", "--version"]:
                return version
            if status_exc:
                raise status_exc
            return status

        clean = {k: v for k, v in os.environ.items() if k not in ClaudeAdapter.NON_OAUTH_ENV}
        with patch("shutil.which", return_value="/usr/bin/claude"), \
             patch("subprocess.run", side_effect=fake_run), \
             patch.dict("os.environ", {**clean, **(env or {})}, clear=True):
            return ClaudeAdapter().detect(), calls

    def test_claude_detect_requires_login(self):
        ok, calls = self.run_detect('{"loggedIn": true, "authMethod": "claude.ai"}')
        self.assertTrue(ok)
        self.assertIn(["claude", "auth", "status", "--json"], calls)
        ok, _ = self.run_detect('{"loggedIn": false, "authMethod": "none"}')   # expired OAuth session
        self.assertFalse(ok)

    def test_claude_detect_trusts_api_key_and_old_cli(self):
        ok, calls = self.run_detect('{"loggedIn": false}', env={"ANTHROPIC_API_KEY": "k"})
        self.assertTrue(ok)
        self.assertNotIn(["claude", "auth", "status", "--json"], calls)  # status describes claude.ai login only
        ok, _ = self.run_detect("error: unknown command 'auth'")        # CLI without the subcommand
        self.assertTrue(ok)
        ok, _ = self.run_detect("", status_exc=OSError("boom"))
        self.assertTrue(ok)

    def test_claude_run_sends_prompt_on_stdin_with_model(self):
        res = MagicMock(returncode=0, stdout="out", stderr="")
        with patch("subprocess.run", return_value=res) as run:
            ClaudeAdapter().run("PROMPT" * 30000, "/tmp", model="sonnet", effort="medium")
        cmd, kwargs = run.call_args.args[0], run.call_args.kwargs
        self.assertEqual(kwargs["input"], "PROMPT" * 30000)          # not argv: E2BIG above 128 KiB
        self.assertNotIn("PROMPT" * 30000, cmd)
        self.assertEqual(cmd[cmd.index("--model") + 1], "sonnet")

    def test_classify_error(self):
        claude = ClaudeAdapter()
        self.assertEqual(claude.classify_error("Rate limit exceeded 429", 1), "rate_limited")
        self.assertEqual(claude.classify_error("Insufficient quota for current plan", 1), "quota_exceeded")
        self.assertEqual(claude.classify_error("Unauthorized 401 token invalid", 1), "auth_failed")


if __name__ == "__main__":
    unittest.main()
