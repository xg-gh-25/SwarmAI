"""Session state persistence for fast resume after daemon restart.

Persists IDLE session sdk_session_ids to disk every 60s (lifecycle loop)
and on graceful shutdown. On startup, ``load_persisted_state()`` returns the
mapping; SessionRouter injects sdk_session_ids lazily at unit creation time.

Design reference:
    Knowledge/Designs/2026-06-20-session-stability-graceful-degradation-design.md §2B

Key invariants (PE-reviewed):
- Only IDLE sessions are persisted (F1: STREAMING/WAITING_INPUT have incomplete state)
- Atomic write via tmp+rename (crash-safe)
- Staleness check: discard if >24hr old (F9)
- File NOT deleted on read — next persist cycle overwrites atomically (crash-safe)
- Lazy injection at get_or_create_unit (not boot-time restore on empty dict)
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Maximum age (seconds) before state file is considered stale and discarded.
MAX_STATE_AGE_SECONDS = 86400  # 24 hours


def persist_session_state(
    units: Dict[str, Any],
    state_file: Path,
) -> int:
    """Persist IDLE session metadata to disk for crash/restart recovery.

    Args:
        units: Dict of session_id → SessionUnit (or mock with same interface).
        state_file: Path to write the state JSON.

    Returns:
        Number of sessions persisted.
    """
    from .session_unit import SessionState

    state: Dict[str, Any] = {
        "_persisted_at": time.time(),
    }

    count = 0
    for sid, unit in units.items():
        # PE F1: ONLY persist IDLE sessions (safe to resume).
        # STREAMING/WAITING_INPUT have incomplete state — resuming mid-stream corrupts context.
        if unit.state == SessionState.IDLE and getattr(unit, "_sdk_session_id", None):
            health = getattr(unit, "_health_sensor", None)
            state[sid] = {
                "sdk_session_id": unit._sdk_session_id,
                "turn_count": health.turn_count if health else 0,
                "last_used": getattr(unit, "last_used", 0),
            }
            count += 1

    if count == 0:
        # Nothing to persist — don't write empty file
        return 0

    # Atomic write: tmp + rename (crash-safe)
    tmp_file = state_file.with_suffix(".tmp")
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_file.write_text(json.dumps(state, indent=2))
        tmp_file.rename(state_file)
    except OSError as exc:
        logger.warning("Failed to persist session state: %s", exc)
        tmp_file.unlink(missing_ok=True)
        return 0

    logger.debug("Persisted %d session state(s) to %s", count, state_file)
    return count


def load_persisted_state(state_file: Path) -> Dict[str, str]:
    """Load persisted session state and return session_id → sdk_session_id mapping.

    This is the READ side of state persistence. Called once at startup by
    SessionRouter.__init__ to cache the mapping. Individual sessions get their
    sdk_session_id injected lazily when get_or_create_unit() is called.

    Design insight (PE review): The old ``restore_session_state(units, ...)``
    was broken because it iterated ``units`` which is EMPTY at boot (sessions
    are lazy-created). This function simply returns the mapping; the router
    injects at creation time.

    Args:
        state_file: Path to the persisted state JSON.

    Returns:
        Dict mapping session_id → sdk_session_id. Empty dict if file missing,
        corrupt, or stale (>24hr).
    """
    if not state_file.exists():
        # Clean up orphaned .tmp from interrupted writes (MEDIUM-2)
        state_file.with_suffix(".tmp").unlink(missing_ok=True)
        return {}

    # Parse file
    try:
        raw = state_file.read_text()
        state = json.loads(raw)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Corrupt session state file, discarding: %s", exc)
        state_file.unlink(missing_ok=True)
        return {}

    # PE F9: Staleness check — discard if >24hr old or missing timestamp
    persisted_at = state.pop("_persisted_at", None)
    if persisted_at is None:
        logger.warning("Session state file missing _persisted_at timestamp, discarding")
        state_file.unlink(missing_ok=True)
        return {}
    age_seconds = time.time() - persisted_at
    if age_seconds > MAX_STATE_AGE_SECONDS:
        logger.warning(
            "Session state file too old (%.1fh), discarding",
            age_seconds / 3600,
        )
        state_file.unlink(missing_ok=True)
        return {}

    # Extract session_id → sdk_session_id mapping
    result: Dict[str, str] = {}
    for sid, meta in state.items():
        sdk_id = meta.get("sdk_session_id") if isinstance(meta, dict) else None
        if sdk_id:
            result[sid] = sdk_id

    logger.info(
        "Loaded %d persisted session identities from state file (age=%.0fs)",
        len(result), age_seconds,
    )

    # Don't unlink — let the next persist_session_state() overwrite atomically.
    # Unlinking here creates a crash window: if daemon dies after unlink but
    # before any user reconnects, the persisted IDs (only in memory) are lost.
    # The staleness check (24hr) + atomic overwrite handle lifecycle safely.
    return result
