"""Persistent state management for last successful engine."""

import json
import os
import time
from pathlib import Path


class StateManager:
    """Tracks the last successful engine with TTL to optimize engine selection."""

    def __init__(self, state_file_path: str | None = None, ttl_seconds: int = 3600):
        if state_file_path:
            self.state_file = Path(state_file_path)
        else:
            default_dir = Path.home() / ".llm-pr-reviewer"
            default_dir.mkdir(parents=True, exist_ok=True)
            self.state_file = default_dir / "state.json"
        self.ttl = ttl_seconds

    def get_last_successful_engine(self) -> str | None:
        if not self.state_file.exists():
            return None
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            last_engine = data.get("last_engine")
            updated_at = data.get("updated_at", 0)
            if last_engine and (time.time() - updated_at < self.ttl):
                return last_engine
        except Exception:
            pass
        return None

    def record_success(self, engine_name: str) -> None:
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "last_engine": engine_name,
                "updated_at": time.time(),
            }
            self.state_file.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        except Exception:
            pass

    def prioritize_engines(self, available_engines: list[str]) -> list[str]:
        """Order available engines, placing the last successful engine first if valid."""
        last_engine = self.get_last_successful_engine()
        if last_engine and last_engine in available_engines:
            ordered = [last_engine] + [e for e in available_engines if e != last_engine]
            return ordered
        return list(available_engines)
