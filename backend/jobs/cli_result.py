"""Pure helpers for judging a headless Claude CLI run — no I/O, no side effects.

Why a separate leaf module: ``jobs/executor`` runs a ``zsh -lic`` PATH probe at
import time, so pure classifiers that tests import must not live there.

Key public symbols:
  - ``parse_cli_version`` — ``"2.1.302 (Claude Code)"`` -> ``(2, 1, 302)``, or None.
  - ``cli_too_old_message`` — explicit refusal text when the CLI is older than the
    resolved model's floor (``model_registry.min_cli_version_for``), else None.
  - ``cli_api_error`` — the CLI exited 0 but the run was an API error
    (``is_error`` true, or a result that starts with ``"API Error"``). Returns
    ``(kind, text)`` with ``kind`` in ``{"auth", "transient", "error"}``, or None.

Why ``cli_api_error`` exists: the CLI can report an API failure inside an exit-0
JSON envelope (prod has recorded ``is_error=True, subtype="success"``). Judging a
run by its return code alone recorded such runs as successes.
"""
from __future__ import annotations

import re

# An API error the CLI reports in its result text. Auth-like errors are routed
# to the scheduler's transient auth path (``auth_failed``: next-tick retry, not
# counted by the circuit breaker); throttling / overload / 5xx are transient
# (one opt-in retry); anything else (e.g. "model not supported") is a real error.
_AUTH_PATTERNS = re.compile(
    r"\b(401|403)\b|expired|security token|credential|not authori[sz]ed|mwinit|"
    r"unrecognizedclient|accessdenied|access denied",
    re.IGNORECASE,
)
_TRANSIENT_PATTERNS = re.compile(
    r"\b(429|500|502|503|504|529)\b|throttl|overloaded|rate limit|too many requests|"
    r"temporarily unavailable|service unavailable|unexpected error|"
    r"connection error|timed out|timeout|econnreset|socket hang up",
    re.IGNORECASE,
)

# A denial that IS deterministic even though it looks auth-shaped: the account has
# no access to this model / the model is unsupported. Retrying or re-authenticating
# cannot fix it, so it must be a plain error (checked before the auth patterns).
_MODEL_ACCESS_PATTERNS = re.compile(
    r"access to the model|model access|not supported|does not support|"
    r"unsupported model|model identifier is invalid",
    re.IGNORECASE,
)
_STATUS_RE = re.compile(r"^\s*API Error:\s*(\d{3})\b")

_VERSION_RE = re.compile(r"^\s*(\d+)\.(\d+)\.(\d+)")


def parse_cli_version(version_output: str | None) -> tuple[int, int, int] | None:
    """Parse ``claude --version`` output; None when it is not ``X.Y.Z...``."""
    if not version_output:
        return None
    m = _VERSION_RE.match(version_output)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def _fmt(version: tuple[int, ...]) -> str:
    return ".".join(str(p) for p in version)


def cli_too_old_message(version: tuple[int, int, int] | None, model: str | None) -> str | None:
    """Refusal text when ``version`` is below the floor ``model`` requires.

    Returns None (proceed) when the model has no floor or the version is unknown —
    an unparseable version string must never block a job; the result
    classification (``cli_api_error``) still catches a rejection.
    """
    try:
        from model_registry import min_cli_version_for, normalize_short_name
    except ImportError:  # registry unavailable — never block a job on our own bug
        return None

    floor = min_cli_version_for(model)
    if floor is None or version is None or version >= floor:
        return None
    return (
        f"Claude CLI {_fmt(version)} is too old for {normalize_short_name(model)} "
        f"(requires >= {_fmt(floor)}). Upgrade the CLI on PATH "
        f"(npm i -g @anthropic-ai/claude-code) — not starting the run."
    )


def cli_api_error(output: dict | None) -> tuple[str, str] | None:
    """Classify an exit-0 CLI JSON result that is actually an API error."""
    if not isinstance(output, dict):
        return None
    result = output.get("result")
    text = result if isinstance(result, str) else ""
    errors = [e for e in (output.get("errors") or []) if isinstance(e, str)]
    is_api_error_text = text.lstrip().startswith("API Error")
    if not (output.get("is_error") is True or is_api_error_text):
        return None
    detail = (text if is_api_error_text or not errors else "; ".join(errors)) or "is_error with no detail"
    detail = detail.strip()[:500]
    kind = _classify_detail(detail)
    subtype = output.get("subtype")
    if not is_api_error_text and isinstance(subtype, str) and subtype not in ("", "success"):
        # A CLI-side error (e.g. error_max_turns / error_during_execution) — label
        # it as such, but keep the kind its detail implies (an overload reported
        # through errors[] is still transient).
        return kind, f"CLI {subtype}: {detail}"
    return kind, detail


def _classify_detail(detail: str) -> str:
    """auth / transient / error for an API-error detail string."""
    if _MODEL_ACCESS_PATTERNS.search(detail):
        return "error"
    status = _STATUS_RE.match(detail)
    if status:
        code = int(status.group(1))
        if code in (401, 403):
            return "auth"
        if code == 429 or code >= 500:
            return "transient"
        return "error"  # any other 4xx is deterministic
    if _AUTH_PATTERNS.search(detail):
        return "auth"
    if _TRANSIENT_PATTERNS.search(detail):
        return "transient"
    return "error"
