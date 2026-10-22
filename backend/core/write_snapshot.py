"""write_snapshot — did a Bash interpreter script actually write the paths it names?

``python3 - <<'PY' … open(p, 'w') … PY`` writes files the command line never names
as a redirect target; file_change_classifier.parse_bash_heredoc_write_candidates
lists the path literals of such a script, and a literal may be one it only READS.
This module decides by CONTENT: a PreToolUse hook (before the command runs) records
each candidate's content digest, and at the tool's result ``changed`` returns the
candidates whose content is now different (written, created or deleted). A
candidate whose content is the same was not written by the call; one that cannot be
read (too large, unreadable) is left off — never guessed from a timestamp.

Known limit (shared by every Bash write the app reports, redirects included):
another process writing the same named path during the call is not told apart.

State: ``session_context[KEY]`` = {tool_use_id: {absolute path: digest | None}},
popped by the orchestrator at the result (bounded: oldest dropped past MAX_CALLS).

Public: ``create_heredoc_snapshot_hook``, ``changed``, ``take``, ``KEY``.
"""
from __future__ import annotations

import hashlib
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

KEY = "_heredoc_write_snapshots"
MAX_CALLS = 64
MAX_BYTES = 32 * 1024 * 1024
_ABSENT = "absent"


def _abs(raw: str, cwd: Optional[str]) -> str:
    p = os.path.expanduser(raw)
    if not os.path.isabs(p):
        p = os.path.join(cwd or os.getcwd(), p)
    return os.path.normpath(p)


def _digest(path: str) -> Optional[str]:
    """Content digest; ``absent`` for no file; None when it cannot be told."""
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return _ABSENT
    except OSError:
        return None
    if not os.path.isfile(path) or st.st_size > MAX_BYTES:
        return None
    h = hashlib.sha1()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def snapshot(raws: list[str], cwd: Optional[str]) -> dict[str, tuple[str, Optional[str]]]:
    """{raw: (absolute path, digest)} — blocking file reads."""
    return {raw: (ap, _digest(ap)) for raw in raws for ap in (_abs(raw, cwd),)}


def changed(snap: dict[str, tuple[str, Optional[str]]]) -> list[str]:
    """The raw paths of *snap* whose content differs now. Blocking."""
    out = []
    for raw, (ap, before) in snap.items():
        if before is None:
            continue
        now = _digest(ap)
        if now is not None and now != before:
            out.append(raw)
    return out


def take(session_context: Optional[dict], tool_use_id: str) -> Optional[dict]:
    """Pop the snapshot recorded for *tool_use_id* (None: none was taken)."""
    store = (session_context or {}).get(KEY)
    return store.pop(tool_use_id, None) if isinstance(store, dict) else None


def peek(session_context: Optional[dict], tool_use_id: str) -> Optional[dict]:
    store = (session_context or {}).get(KEY)
    return store.get(tool_use_id) if isinstance(store, dict) else None


def create_heredoc_snapshot_hook(session_context: dict):
    """PreToolUse (Bash): record the heredoc write candidates' content before the
    command runs. Never denies; any failure records nothing."""
    from core import executors
    from core.file_change_classifier import parse_bash_heredoc_write_candidates, parse_bash_write_targets

    async def hook(input_data: dict[str, Any], tool_use_id: Optional[str], context: Any) -> dict:
        try:
            cmd = ((input_data or {}).get("tool_input") or {}).get("command") or ""
            if not tool_use_id or "<<" not in cmd:
                return {}
            named = set(parse_bash_write_targets(cmd))
            raws = [p for p in parse_bash_heredoc_write_candidates(cmd) if p not in named]
            if not raws:
                return {}
            cwd = (input_data or {}).get("cwd") or session_context.get("cwd")
            snap = await executors.run_in("io", snapshot, raws, cwd)
            store = session_context.setdefault(KEY, {})
            while len(store) >= MAX_CALLS:
                store.pop(next(iter(store)))
            store[tool_use_id] = snap
        except Exception:  # noqa: BLE001 — a display aid never blocks a command
            logger.debug("heredoc snapshot failed", exc_info=True)
        return {}

    return hook
