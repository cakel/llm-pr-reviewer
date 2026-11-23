"""Claude Code CLI adapter with default configuration respect and custom endpoint support."""

import json
import os
import shutil
import subprocess
from .base import EngineAdapter


class ClaudeAdapter(EngineAdapter):
    name = "claude"

    # Auth that `claude auth status` (the claude.ai login) does not describe: an API key, a token, a proxy or
    # a cloud provider. With one of these set, trust the run itself to report a failure.
    NON_OAUTH_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
                     "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX")

    def detect(self) -> bool:
        """Check that the claude CLI is installed and, for a claude.ai login, still signed in.

        `--version` alone passed with an expired OAuth session; the review then failed at run time.
        """
        if not shutil.which("claude"):
            return False
        try:
            res = subprocess.run(
                ["claude", "--version"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            if res.returncode != 0:
                return False
        except Exception:
            return False
        if any(os.environ.get(k) for k in self.NON_OAUTH_ENV):
            return True
        try:
            status = subprocess.run(["claude", "auth", "status", "--json"],
                                    capture_output=True, text=True, timeout=15)
            info = json.loads(status.stdout)
        except Exception:
            return True   # a CLI without `auth status`: let the run decide (it falls back on failure)
        if isinstance(info, dict) and "loggedIn" in info:
            return bool(info["loggedIn"])
        return True

    def run(
        self,
        prompt: str,
        work_dir: str,
        model: str | None = None,
        effort: str | None = None,
        timeout_sec: int = 300,
    ) -> tuple[str, int]:
        env = os.environ.copy()

        # If user explicitly configured custom ANTHROPIC_BASE_URL (e.g. Ollama or proxy)
        base_url = env.get("ANTHROPIC_BASE_URL", "")
        if base_url and ("11434" in base_url or "localhost" in base_url):
            if "ANTHROPIC_API_KEY" not in env:
                env["ANTHROPIC_API_KEY"] = "ollama"
            env["CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT"] = "1"

        # Prompt goes on stdin: as an argv string it fails with E2BIG above 128 KiB (Linux MAX_ARG_STRLEN)
        cmd = [
            "claude", "-p",
            "--tools", "",
            "--no-session-persistence",
        ]
        # Only pass --model if explicitly requested; otherwise respect Claude Code's configured default
        if model:
            cmd.extend(["--model", model])
        if effort:
            cmd.extend(["--effort", effort])

        try:
            res = subprocess.run(
                cmd,
                input=prompt,
                cwd=work_dir,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )
            output = res.stdout if res.stdout else res.stderr
            return output, res.returncode
        except subprocess.TimeoutExpired:
            return "Claude execution timed out", 124
        except Exception as exc:
            return f"Claude execution failed: {exc}", 1

    def classify_error(self, output: str, returncode: int) -> str:
        lowered = output.lower()
        if any(term in lowered for term in [
            "oauth session expired",
            "failed to authenticate",
            "not logged in",
            "invalid api key",
            "unauthorized",
            "auth",
            "login required",
            "fix external api key",
        ]):
            return "auth_failed"
        return super().classify_error(output, returncode)
