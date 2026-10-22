"""Shared identifiers for Pipeline v1.

Public symbols:
- ``validate_run_id`` — require one safe ``run_*`` path component everywhere.
"""
from __future__ import annotations

import re

_RUN_ID = re.compile(r"^run_[A-Za-z0-9_-]+$")


class IdentifierError(ValueError):
    """A protocol identifier is unsafe or malformed."""


def validate_run_id(value: object) -> str:
    """Return a safe run ID or raise; rejects traversal, controls and separators."""
    if not isinstance(value, str) or not _RUN_ID.fullmatch(value):
        raise IdentifierError("run_id must match run_[A-Za-z0-9_-]+")
    return value
