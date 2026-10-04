"""Claude Code CLI adapter with Ollama / Anthropic endpoint support."""

import os
import shutil
import subprocess
import urllib.request
from .base import EngineAdapter


class ClaudeAdapter(EngineAdapter):
    name = "claude"

    def __init__(self, default_ollama_model: str = "gemma4:31b-cloud"):
        self.default_ollama_model = default_ollama_model

    def detect(self) -> bool:
        if not shutil.which("claude"):
            return False

        # Check if Ollama endpoint is reachable or ANTHROPIC_API_KEY is set
        base_url = os.environ.get("ANTHROPIC_BASE_URL", "http://localhost:11434")
        if "11434" in base_url or "localhost" in base_url:
            try:
                req = urllib.request.Request(f"{base_url}/api/tags", headers={"User-Agent": "curl/7.88.1"})
                with urllib.request.urlopen(req, timeout=3) as resp:
                    return resp.status == 200
            except Exception:
                pass

        # Otherwise check if claude has existing auth or API key
        if os.environ.get("ANTHROPIC_API_KEY"):
            return True

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
        base_url = env.get("ANTHROPIC_BASE_URL", "http://localhost:11434")
        env["ANTHROPIC_BASE_URL"] = base_url
        if "ANTHROPIC_API_KEY" not in env:
            env["ANTHROPIC_API_KEY"] = "ollama"
        env["CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT"] = "1"

        chosen_model = model
        if not chosen_model and ("11434" in base_url or "localhost" in base_url):
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
