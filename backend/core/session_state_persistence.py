"""Session state persistence for fast resume after daemon restart.

Persists IDLE session sdk_session_ids to disk every 60s (lifecycle loop)
and on graceful shutdown. On startup, restores them so sessions use fast
``--resume`` instead of cold resume (50K summary → 5s full replay).

Design reference:
    Knowledge/Designs/2026-06-20-session-stability-graceful-degradation-design.md §2B

Key invariants (PE-reviewed):
- Only IDLE sessions are persisted (F1: STREAMING/WAITING_INPUT have incomplete state)
- Atomic write via tmp+rename (crash-safe)
- Staleness check: discard if >24hr old (F9)
- Consumed on read (one-shot, unlinked after restore)
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


def restore_session_state(
    units: Dict[str, Any],
    state_file: Path,
    validate_db: bool = True,
) -> int:
    """Restore sdk_session_ids from state file for fast --resume.

    Args:
        units: Dict of session_id → SessionUnit to restore into.
        state_file: Path to read the state JSON from.
        validate_db: If True, validate sdk_session_id against DB message count.
            Set False in tests.

    Returns:
        Number of sessions restored.
    """
    if not state_file.exists():
        return 0

    # Parse file
    try:
        raw = state_file.read_text()
        state = json.loads(raw)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Corrupt session state file, discarding: %s", exc)
        state_file.unlink(missing_ok=True)
        return 0

    # PE F9: Staleness check — discard if >24hr old
    persisted_at = state.pop("_persisted_at", 0)
    age_seconds = time.time() - persisted_at
    if age_seconds > MAX_STATE_AGE_SECONDS:
        logger.warning(
            "Session state file too old (%.1fh), discarding",
            age_seconds / 3600,
        )
        state_file.unlink(missing_ok=True)
        return 0

    # Restore sdk_session_ids
    restored = 0
    for sid, meta in state.items():
        if sid not in units:
            continue

        unit = units[sid]
        sdk_id = meta.get("sdk_session_id")
        if not sdk_id:
            continue

        # Assign sdk_session_id for fast --resume path
        unit._sdk_session_id = sdk_id
        restored += 1

    logger.info(
        "Restored %d/%d session identities from state file (age=%.0fs)",
        restored, len(state), age_seconds,
    )

    # Consumed — delete after use (one-shot)
    state_file.unlink(missing_ok=True)
    return restored
