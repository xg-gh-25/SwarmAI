"""What every CDF model call shares.

- ``UntypedOutput``: a reply that is not the declared typed answer (truncated, missing,
  invalid); deterministic for the same input, so it is not retried.
- ``prompt_hash``: the stable hash a version vector records for a prompt.
"""
from __future__ import annotations

import hashlib


class UntypedOutput(ValueError):
    """Raised when a model's output is not the exact declared type."""

    reason = "untyped"

    def __init__(self, message: str = "untyped", raw: str | None = None) -> None:
        super().__init__(message)
        self.raw = raw


def prompt_hash(prompt: str) -> str:
    """Return a stable SHA-256 prompt hash."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()
