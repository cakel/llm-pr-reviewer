"""Base adapter interface for LLM CLI engines."""

from abc import ABC, abstractmethod
import re


class EngineAdapter(ABC):
    """Abstract base class for all CLI review engines."""

    name: str

    @abstractmethod
    def detect(self) -> bool:
        """Check if the CLI executable is available and authenticated."""
        pass

    @abstractmethod
    def run(
        self,
        prompt: str,
        work_dir: str,
        model: str | None = None,
        effort: str | None = None,
        timeout_sec: int = 300,
    ) -> tuple[str, int]:
        """Execute the CLI engine with the provided prompt.

        Returns:
            tuple[output_text, returncode]
        """
        pass

    def classify_error(self, output: str, returncode: int) -> str:
        """Heuristically classify the failure reason."""
        lowered = output.lower()
        if any(term in lowered for term in ["quota", "insufficient_quota", "credit limit", "billing"]):
            return "quota_exceeded"
        if any(term in lowered for term in ["rate limit", "too many requests", "429", "throttl"]):
            return "rate_limited"
        if any(term in lowered for term in ["auth", "unauthorized", "login", "expired", "token invalid"]):
            return "auth_failed"
        return "execution_failed"
