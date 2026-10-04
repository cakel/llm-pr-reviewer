"""Claude Code CLI adapter with standard Anthropic auth and custom endpoint support."""

import os
import shutil
import subprocess
from .base import EngineAdapter


class ClaudeAdapter(EngineAdapter):
    name = "claude"

    def __init__(self, default_ollama_model: str = "gemma4:31b-cloud"):
        self.default_ollama_model = default_ollama_model

    def detect(self) -> bool:
        """Check if claude CLI binary is installed and executable."""
        if not shutil.which("claude"):
            return False
        try:
            res = subprocess.run(
                ["claude", "--version"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            return res.returncode == 0
        except Exception:
            return False

    def run(
        self,
        prompt: str,
        work_dir: str,
        model: str | None = None,
        effort: str | None = None,
        timeout_sec: int = 300,
    ) -> tuple[str, int]:
        env = os.environ.copy()

        # If user explicitly configured ANTHROPIC_BASE_URL (e.g. Ollama or custom proxy)
        base_url = env.get("ANTHROPIC_BASE_URL", "")
        chosen_model = model

        if base_url and ("11434" in base_url or "localhost" in base_url):
            if "ANTHROPIC_API_KEY" not in env:
                env["ANTHROPIC_API_KEY"] = "ollama"
            env["CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT"] = "1"
            if not chosen_model:
                chosen_model = self.default_ollama_model

        cmd = [
            "claude", "-p", prompt,
            "--tools", "",
            "--no-session-persistence",
        ]
        if chosen_model:
            cmd.extend(["--model", chosen_model])
        if effort:
            cmd.extend(["--effort", effort])

        try:
            res = subprocess.run(
                cmd,
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
