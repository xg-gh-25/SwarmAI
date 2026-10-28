"""Shipped Project Agent definitions: the default SwarmAI Brain's and the Runner Agent's
(design 2026-10-28-project-agent-flow §5).

Both ship in ``templates/project-agents/`` as drafts, and each user reviews their own
copy. On every workspace start ``seed_shipped`` places them:

- no definition yet: write the shipped one;
- the definition still has the bytes this product last wrote (its sha256 equals the
  one recorded in ``.project-agent-shipped.json`` beside it): replace it with the
  newly shipped one;
- anything else — the user edited or confirmed it, or it was never written by this
  seeder: leave it alone.

This record is its own rule, not the ``user_customized`` flag of the context files
(that flag is fixed per file; this compares the bytes on disk with what was shipped).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Callable

from core.project_agent import PROJECT_AGENT_FILE, general_brain_dir

logger = logging.getLogger(__name__)

SHIPPED_DIR = Path(__file__).resolve().parent.parent / "templates" / "project-agents"
STATE_FILE = ".project-agent-shipped.json"

#: shipped file -> the Brain directory it belongs in, from the workspace root
SHIPPED: dict[str, Callable[[Path], Path]] = {
    "swarmai.yaml": lambda ws: ws / "Projects" / "SwarmAI",
    "runner.yaml": general_brain_dir,
}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _recorded(state: Path) -> str | None:
    try:
        rec = json.loads(state.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return rec.get("sha256") if isinstance(rec, dict) and isinstance(rec.get("sha256"), str) else None


def _write(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def place(shipped: bytes, brain_dir: Path) -> str:
    """Place one shipped definition into *brain_dir*. Returns what happened:
    ``written`` · ``updated`` · ``current`` · ``kept`` (edited or not ours) ·
    ``no-brain`` (the directory does not exist)."""
    if not brain_dir.is_dir():
        return "no-brain"
    target, state = brain_dir / PROJECT_AGENT_FILE, brain_dir / STATE_FILE
    new = _sha(shipped)
    if not target.exists():
        _write(target, shipped)
        _write(state, json.dumps({"sha256": new}).encode())
        return "written"
    try:
        current = _sha(target.read_bytes())
    except OSError:
        return "kept"
    recorded = _recorded(state)
    if current == new:
        if recorded != new:
            _write(state, json.dumps({"sha256": new}).encode())
        return "current"
    if recorded is not None and current == recorded:
        _write(target, shipped)
        _write(state, json.dumps({"sha256": new}).encode())
        return "updated"
    return "kept"


def seed_shipped(workspace: Path) -> dict[str, str]:
    """Place every shipped definition; never raises (a failure is logged and the
    workspace starts without it)."""
    out: dict[str, str] = {}
    for name, where in SHIPPED.items():
        try:
            shipped = (SHIPPED_DIR / name).read_bytes()
            out[name] = place(shipped, where(Path(workspace)))
        except OSError as exc:
            logger.warning("shipped Project Agent %s not placed: %s: %s", name, type(exc).__name__, exc)
            out[name] = "error"
    return out
