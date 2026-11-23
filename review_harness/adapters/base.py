"""Base adapter interface for LLM CLI engines."""

from abc import ABC, abstractmethod
import re


class EngineAdapter(ABC):
    """Abstract base class for all CLI review engines."""

    name: str
    # Largest prompt (UTF-8 bytes) the CLI can take, or None. A CLI that only accepts the prompt as one argv
    # string is capped by Linux MAX_ARG_STRLEN (128 KiB); a bigger prompt fails with E2BIG before it starts.
    max_prompt_bytes: int | None = None

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
