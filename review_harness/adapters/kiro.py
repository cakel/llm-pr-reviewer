"""Kiro CLI adapter."""

import os
import shutil
import subprocess
from .base import EngineAdapter


class KiroAdapter(EngineAdapter):
    name = "kiro"

    def detect(self) -> bool:
        if not shutil.which("kiro-cli"):
            return False
        try:
            res = subprocess.run(
                ["kiro-cli", "whoami"],
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
        env = os.environ.copy()
        env["KIRO_LOG_NO_COLOR"] = "1"
        cmd = [
            "kiro-cli", "chat", "--no-interactive",
            "--agent-engine", "v1",
            "--trust-tools=",
        ]
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
            return "Kiro execution timed out", 124
        except Exception as exc:
            return f"Kiro execution failed: {exc}", 1
