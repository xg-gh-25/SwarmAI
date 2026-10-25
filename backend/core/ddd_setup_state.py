"""ddd_setup_state — whether a Brain's setup (s_project-manager P0..P6) is finished.

One small file per Brain, ``.artifacts/setup.json``:

    {"state": "setting-up" | "ready", "updated_at": "<ISO>", "counts": {...}}

Writers (exactly two, one per transition):
- ``create_project`` writes ``setting-up`` when a NEW project is created (never on
  a re-provision or migration of an existing one).
- the P6 gate (``verify_ddd_complete.py --record``) writes ``ready`` on PASS, or
  keeps ``setting-up`` on FAIL.

Reader: the Brain Hub gallery (``setupState``) shows "being born" while it is
``setting-up``, with ``setupProgress`` (checks done of all, from the last P6 counts)
when a P6 run recorded them. A Brain without the file (made before this existed) is
``None`` and is never shown as being born.

Key public symbols: ``SETUP_FILE``, ``STATES``, ``read_setup_state``,
``read_setup_progress``, ``write_setup_state``.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

SETUP_FILE = ".artifacts/setup.json"
STATES = frozenset({"setting-up", "ready"})


def _read_record(project_dir: Path) -> Optional[dict]:
    p = project_dir / SETUP_FILE
    try:
        if not p.is_file() or p.is_symlink() or p.stat().st_size > 8192:
            return None
        body = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        logger.debug("setup.json unreadable in %s: %s", project_dir, exc)
        return None
    return body if isinstance(body, dict) else None


def read_setup_state(project_dir: Path) -> Optional[str]:
    """``setting-up`` / ``ready``, or None when the Brain has no (valid) record."""
    body = _read_record(project_dir)
    state = body.get("state") if body else None
    return state if state in STATES else None


def read_setup_progress(project_dir: Path) -> Optional[dict[str, int]]:
    """How far an unfinished setup got, from the last P6 ``--record``:
    ``{"done": PASS + N/A, "total": every check}``. None when the Brain is not
    setting up, or no P6 run recorded counts yet (right after create_project)."""
    body = _read_record(project_dir)
    if not body or body.get("state") != "setting-up":
        return None
    counts = body.get("counts")
    if not isinstance(counts, dict):
        return None
    try:
        nums = {str(k): int(v) for k, v in counts.items()}
    except (TypeError, ValueError):
        return None
    total = sum(nums.values())
    if total <= 0:
        return None
    return {"done": nums.get("PASS", 0) + nums.get("N/A", 0), "total": total}


def write_setup_state(project_dir: Path, state: str, counts: Optional[dict[str, Any]] = None) -> None:
    """Atomically write the record (temp file + rename)."""
    if state not in STATES:
        raise ValueError(f"unknown setup state {state!r}")
    p = project_dir / SETUP_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    body = {"state": state, "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    if counts:
        body["counts"] = counts
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".setup-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(body, fh, indent=2)
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
