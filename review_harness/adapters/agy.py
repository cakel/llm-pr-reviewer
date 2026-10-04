"""Antigravity (agy) CLI adapter."""

import os
import shutil
import subprocess
from .base import EngineAdapter


class AgyAdapter(EngineAdapter):
    name = "agy"

    def detect(self) -> bool:
        if not shutil.which("agy"):
            return False
        try:
            res = subprocess.run(
                ["agy", "--version"],
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
        cmd = [
            "agy",
            "-p", prompt,
            "--output-format", "text",
            "--disable-slash-commands",
        ]
        if model:
            cmd.extend(["--model", model])
        if effort:
            cmd.extend(["--effort", effort])

        try:
            res = subprocess.run(
                cmd,
                cwd=work_dir,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )
            output = res.stdout if res.stdout else res.stderr
            return output, res.returncode
        except subprocess.TimeoutExpired:
            return "Agy execution timed out", 124
        except Exception as exc:
            return f"Agy execution failed: {exc}", 1
