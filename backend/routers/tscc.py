"""TSCC API router for thread cognitive context and system prompt viewer.

This module provides the ``tscc_router`` mounted at ``/api`` with endpoints
for retrieving live TSCC state and system prompt metadata.

Key endpoints:

- ``GET  /api/chat_threads/{thread_id}/tscc``          — current state
- ``GET  /api/chat/{session_id}/system-prompt``        — system prompt metadata

Both endpoints return a default empty state when no in-memory data exists
(e.g. after backend restart).  This avoids 404 console errors on the
frontend and is semantically correct — "not yet initialized" is a valid
state, not an error.

All responses use snake_case field names per backend convention.

Requirements: 6.1, 6.2, 6.7
"""

import logging
import re
from datetime import datetime, timezone

from fastapi import APIRouter

from schemas.tscc import (
    RecallSnapshot,
    SecurityFinding,
    SecurityScanResult,
    SystemPromptMetadata,
    TSCCContext,
    TSCCLiveState,
    TSCCState,
)

# PII email detector — egress_redactor has no email pattern (it targets
# credential shapes only), so a simple email regex is added here. Reused
# credential detection comes from egress_redactor._CREDENTIAL_PATTERNS below.
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")


def _mask_value(value: str) -> str:
    """Mask a raw secret/PII value: keep first 2 chars, replace the rest with ***.

    Never leaks the raw value into the response detail.
    """
    if not value:
        return "***"
    if len(value) <= 2:
        return "***"
    return f"{value[:2]}***"


def _mask_email(email: str) -> str:
    """Mask an email as ``ab***@domain`` — keeps the domain, masks the local part."""
    local, _, domain = email.partition("@")
    return f"{_mask_value(local)}@{domain}" if domain else _mask_value(email)

logger = logging.getLogger(__name__)

tscc_router = APIRouter()


# Late-bound references — set by register_tscc_dependencies() at app startup
_state_manager = None


def register_tscc_dependencies(state_manager) -> None:
    """Wire up the TSCC state manager at app startup."""
    global _state_manager
    _state_manager = state_manager


def _make_default_tscc_state(thread_id: str) -> TSCCState:
    """Build a default TSCC state for a thread with no in-memory data."""
    return TSCCState(
        thread_id=thread_id,
        project_id=None,
        scope_type="workspace",
        last_updated_at=datetime.now(timezone.utc).isoformat(),
        lifecycle_state="new",
        live_state=TSCCLiveState(
            context=TSCCContext(
                scope_label="Workspace: SwarmWS (General)",
                thread_title="",
            ),
        ),
    )


@tscc_router.get(
    "/chat_threads/{thread_id}/tscc",
    response_model=TSCCState,
)
async def get_tscc_state(thread_id: str):
    """Return the current TSCC state for a thread.

    Returns a default empty state if no in-memory state exists (e.g. after
    backend restart).  This is not an error — the state will be populated
    when the next conversation starts on this thread.
    """
    state = await _state_manager.get_state(thread_id)
    if state is None:
        return _make_default_tscc_state(thread_id)
    return state


@tscc_router.get(
    "/chat/{session_id}/system-prompt",
    response_model=SystemPromptMetadata,
)
async def get_system_prompt(session_id: str):
    """Return the assembled system prompt metadata for a session.

    Returns an empty metadata object if no metadata exists for the given
    session_id (e.g. after backend restart).  The metadata will be
    populated when the next conversation starts on this session.
    """
    # NOTE: system_prompt_metadata is populated by PromptBuilder and stored
    # in session_registry. Shows metadata for sessions using the new architecture.
    from core import session_registry

    metadata = session_registry.system_prompt_metadata.get(session_id)
    if metadata is None:
        return SystemPromptMetadata()
    return SystemPromptMetadata(**metadata)


@tscc_router.get(
    "/chat/{session_id}/recall",
    response_model=RecallSnapshot,
)
async def get_recall(session_id: str):
    """Return the recalled-knowledge snapshot for a session (read-only panel).

    Reads ``session_registry.recall_snapshot`` only — this endpoint NEVER
    triggers recall. The snapshot was captured fire-and-forget as a side effect
    of the ONE recall leg that already runs on the first user message, so this
    is fully decoupled from the chat send path (zero added hot-path work).

    Returns a default snapshot (``ran=False``) when no recall ran for the
    session (channel session / keyword-miss / turn 2+ / backend restart).
    ``ran=False`` is a valid state — "no recall this session" — not an error,
    and is distinct from "recall ran but hit nothing". Never 404s.
    """
    from core import session_registry

    snap = session_registry.recall_snapshot.get(session_id)
    if snap is None:
        return RecallSnapshot()
    return RecallSnapshot(**snap)


@tscc_router.get(
    "/chat/{session_id}/security-scan",
    response_model=SecurityScanResult,
)
async def get_security_scan(session_id: str):
    """Scan the assembled system prompt for secrets/PII (read-only panel).

    Fully decoupled from the chat hot path — runs only when this endpoint is
    called (i.e. when a user opens the security panel), never during message
    send.

    Returns a neutral empty result (grade "n/a", no findings) if no metadata
    exists for the session (e.g. after backend restart).  Never 404s.

    Reuses the credential detectors from ``channels.egress_redactor`` — no new
    secret detectors are defined here.  All ``detail`` values are masked so the
    raw secret/PII value is never echoed back into the response.
    """
    from channels.egress_redactor import _CREDENTIAL_PATTERNS, _SECRET_ASSIGN
    from core import session_registry

    metadata = session_registry.system_prompt_metadata.get(session_id)
    if metadata is None:
        return SecurityScanResult(grade="n/a")

    full_text = metadata.get("full_text", "") or ""
    scanned_files = len(metadata.get("files", []) or [])

    findings: list[SecurityFinding] = []

    # --- Secret/credential scan (reused egress_redactor patterns) ------------
    # _CREDENTIAL_PATTERNS covers the credential SHAPES (AWS/PEM/JWT/bearer/prefixed
    # token/URL-cred). It deliberately EXCLUDES _SECRET_ASSIGN (the `password=` /
    # `api_key: ...` assignment detector) because egress_redactor applies that one
    # separately inside redact_credentials(). For a SCAN we must include it too,
    # or a prompt containing `password: hunter2` scores a false "A" (adversarial
    # finding, run_a5a101b9). Scan both; _SECRET_ASSIGN carries the secret value in
    # group(1), so mask that group, never the whole `key=value` match.
    cred_matches: list[str] = []
    for pat in _CREDENTIAL_PATTERNS:
        cred_matches.extend(pat.findall(full_text))
    # findall can yield tuples for grouped patterns; normalize to str.
    cred_matches = [
        (m if isinstance(m, str) else next((g for g in m if g), "")) for m in cred_matches
    ]
    # _SECRET_ASSIGN: findall returns the secret value (group 1) — a `key=value`
    # assignment where the value is the sensitive part.
    for m in _SECRET_ASSIGN.findall(full_text):
        cred_matches.append(m if isinstance(m, str) else next((g for g in m if g), ""))
    # Dedupe before counting: one secret can match BOTH _SECRET_ASSIGN (as the
    # assignment value) AND a raw shape pattern (e.g. `aws_secret_access_key = AKIA…`
    # matches the assignment AND _AWS_ACCESS_KEY), which would double-count and make
    # the "Detected N" detail wrong (adversarial finding, run_a5a101b9). dict.fromkeys
    # preserves first-seen order.
    cred_matches = [m for m in dict.fromkeys(cred_matches) if m]

    if cred_matches:
        masked = ", ".join(_mask_value(m) for m in cred_matches[:5])
        findings.append(
            SecurityFinding(
                detector="credentials",
                severity="critical",
                status="warn",
                count=len(cred_matches),
                detail=f"Detected {len(cred_matches)} credential-shaped value(s): {masked}",
            )
        )
    else:
        findings.append(
            SecurityFinding(
                detector="credentials",
                severity="critical",
                status="pass",
                count=0,
                detail="No credential-shaped values detected.",
            )
        )

    # --- PII email scan ------------------------------------------------------
    email_matches = _EMAIL_RE.findall(full_text)
    if email_matches:
        masked_emails = ", ".join(_mask_email(e) for e in email_matches[:5])
        findings.append(
            SecurityFinding(
                detector="pii_email",
                severity="info",
                status="warn",
                count=len(email_matches),
                detail=f"Detected {len(email_matches)} email address(es): {masked_emails}",
            )
        )

    # --- Aggregate counts + grade -------------------------------------------
    critical = sum(1 for f in findings if f.severity == "critical" and f.status == "warn")
    high = sum(1 for f in findings if f.severity == "high" and f.status == "warn")
    medium = sum(1 for f in findings if f.severity == "medium" and f.status == "warn")
    info = sum(1 for f in findings if f.severity == "info" and f.status == "warn")

    if critical:
        grade = "C"
    elif high:
        grade = "B"
    elif info:
        grade = "A-"
    else:
        grade = "A"

    return SecurityScanResult(
        grade=grade,
        findings=findings,
        critical=critical,
        high=high,
        medium=medium,
        info=info,
        scanned_files=scanned_files,
    )
