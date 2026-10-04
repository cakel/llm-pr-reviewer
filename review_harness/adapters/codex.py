"""Codex CLI adapter."""

import os
import shutil
import subprocess
from pathlib import Path
from .base import EngineAdapter


class CodexAdapter(EngineAdapter):
    name = "codex"

    def detect(self) -> bool:
        if not shutil.which("codex"):
            return False
        try:
            res = subprocess.run(
                ["codex", "--version"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
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
        last_msg_path = Path(work_dir) / "codex_last_msg.txt"
        if last_msg_path.exists():
            last_msg_path.unlink()

        cmd = [
            "codex", "exec",
            "--skip-git-repo-check",
            "--ephemeral",
            "-s", "read-only",
            "--ignore-user-config",
            "--ignore-rules",
            "--color", "never",
            "-C", work_dir,
            "-o", str(last_msg_path),
            "-",
        ]
        if model:
            cmd.extend(["-m", model])

        try:
            res = subprocess.run(
                cmd,
                input=prompt,
                cwd=work_dir,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )
            if last_msg_path.exists():
                output = last_msg_path.read_text(encoding="utf-8", errors="replace")
            else:
                output = res.stdout if res.stdout else res.stderr
            return output, res.returncode
        except subprocess.TimeoutExpired:
            return "Codex execution timed out", 124
        except Exception as exc:
            return f"Codex execution failed: {exc}", 1
