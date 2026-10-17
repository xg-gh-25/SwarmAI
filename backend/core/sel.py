"""Security Event Log (SEL) — persistent, structured, append-only tool-audit trail.

## Why this exists (the C050 gap)

Tool/gate decisions had NO persistent structured audit. ``pre_tool_logger`` wrote
one ``logger.info`` line into the daemon log, which rotates at 20MB — so "which
tools ran, when" was unrecoverable after rotation. C050 (the whole ``data.db`` was
wiped) stalled at exactly this: the post-mortem could not reconstruct the window
because the logs had rotated it away.

SEL is an append-only JSONL at ``~/.swarm-ai/security_events.jsonl`` —
**deliberately OUTSIDE data.db and OUTSIDE the git workspace**. That placement is
the load-bearing invariant: auditing a data.db wipe cannot live inside data.db
(it would be destroyed with it), and auditing a workspace reset cannot live inside
the workspace. SEL survives both.

## Scope (narrowed per Gate-0 — read before extending)

This module records the **tool-invocation flow** (the ``pre_tool_logger`` 埋点,
whose outcome is always ``"allow"`` because that hook always approves — the real
deny/hitl decisions happen in the DOWNSTREAM gates, dangerous/adversarial/external).
Emitting a SEL event from those gates on a deny is **Phase 2**, deliberately not in
this build. So today SEL answers "which tools were invoked when" (the C050 question),
not yet "which gate denied what".

**Deliberately NOT here** (Gate-0 ruled these out):
- data-destruction events — already covered by ``data_safety`` (isolate + recovery
  marker). Not re-audited here.
- an HMAC tamper-evidence chain — C050's pain is "the record was LOST", not "the
  record was TAMPERED". A single-user local app has no in-scope local tamperer, so
  a tamper-chain would protect against a threat that is not in the model. The event
  shape leaves room to add ``prev_hash``/``entry_hash`` later behind a flag, but this
  build writes neither and pays zero cost for the absent feature.

## Discipline: never record a payload

An audit trail must not become an exfiltration surface. SEL records
归属 (``caller``) + ``operation`` (tool name / ``METHOD /path``) + ``outcome`` +
a REDACTED, TRUNCATED ``resource`` summary — never raw ``tool_input`` values.
``summarize_resource`` collapses a payload dict to its keys + sizes, never values.

## Fail-soft (hot path)

``pre_tool_logger`` runs on EVERY tool call. A SEL write failure must never block
or slow tool execution: ``append_event`` wraps all I/O in try/except and degrades
to a ``logger.warning``. A broken audit is strictly better than a blocked agent.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

__all__ = ["append_event", "read_events", "sel_log_path", "summarize_resource"]

_LOG_NAME = "security_events.jsonl"
_SEGMENT_DIR_NAME = "security_events.d"
# Seal the live log into a segment once it crosses this size. 32 MiB mirrors the
# KiroCrew SEL segment ceiling; a sealed segment stays readable via read_events().
_MAX_SEGMENT_BYTES = 32 * 1024 * 1024
# Hard cap on a stored resource summary — an audit field, never a content store.
_MAX_RESOURCE_CHARS = 500

try:
    import fcntl  # POSIX only; macOS/Linux daemon + hive. Windows degrades (see _write_line).
    _HAVE_FCNTL = True
except ImportError:  # pragma: no cover - Windows desktop subprocess mode
    _HAVE_FCNTL = False


def _app_data_dir() -> Path:
    """App-data root (~/.swarm-ai). Indirected through config so a test can
    monkeypatch it to a tmp dir, and so SEL shares the daemon's data home."""
    try:
        from config import get_app_data_dir
        return get_app_data_dir()
    except Exception:  # pragma: no cover - config import guard
        logger.debug("_app_data_dir: suppressed exception on a fail-safe path", exc_info=True)
        return Path.home() / ".swarm-ai"


def sel_log_path() -> Path:
    """Absolute path of the live SEL log. OUTSIDE data.db, OUTSIDE the workspace."""
    return _app_data_dir() / _LOG_NAME


def _segment_dir() -> Path:
    return _app_data_dir() / _SEGMENT_DIR_NAME


def summarize_resource(payload: Any) -> str:
    """Collapse a tool_input (or any payload) to a NON-SENSITIVE summary.

    Records the SHAPE (keys + value sizes), never the VALUES — so a secret carried
    in a command/path/token *value* never lands in the audit. Truncated to
    _MAX_RESOURCE_CHARS.

    ⚠️ KEY boundary (Gate-2 run_7ada39d6): dict KEY NAMES are recorded verbatim (the
    key name — ``command``, ``file_path`` — IS the audit's value: it says which
    parameters a tool was called with). This is safe for schema-keyed tool_input
    (the only caller today — keys are fixed tool-schema names, never secrets). Do NOT
    feed this a dict whose KEYS are attacker/secret-controlled (e.g. an env-map keyed
    by secret names); summarize those by shape before passing them in.
    """
    try:
        if isinstance(payload, dict):
            parts = []
            for k in sorted(payload.keys()):
                v = payload[k]
                vlen = len(v) if isinstance(v, (str, bytes, list, dict)) else len(str(v))
                parts.append(f"{k}({vlen})")
            s = ",".join(parts)
        elif isinstance(payload, str):
            s = f"str({len(payload)})"
        else:
            s = type(payload).__name__
    except Exception:
        logger.debug("summarize_resource: suppressed exception on a fail-safe path", exc_info=True)
        s = "<unsummarizable>"
    return s[:_MAX_RESOURCE_CHARS]


def _write_line(path: Path, line: str) -> None:
    """Atomic append of one line, concurrency-safe via fcntl.flock.

    Mirrors daily_activity_writer's proven pattern. Isolated into its own function
    so append_event's fail-soft wrapper (and tests) have one seam to target.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        if _HAVE_FCNTL:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                f.write(line + "\n")
                # flush() hands bytes to the OS page cache — survives a PROCESS crash
                # (the realistic failure). We deliberately do NOT os.fsync per call:
                # this runs on EVERY tool invocation of every session, and a serialized
                # physical-disk sync here would violate SEL's own "never slow the hot
                # path" contract (Gate-2 run_7ada39d6). The C050 threat is a LOGICAL
                # wipe (a process deleting data.db), not power loss — flush() covers it.
                f.flush()
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        else:  # pragma: no cover - Windows: O_APPEND is atomic for small writes
            f.write(line + "\n")
            f.flush()


def _maybe_rotate() -> None:
    """If the live log exceeds the segment ceiling, seal it into security_events.d/
    and start a fresh live log with a rotation-boundary event. Best-effort.

    Concurrency note (Gate-2 run_7ada39d6): the ``os.replace`` rename is atomic, but
    the full check→replace→boundary-write SEQUENCE is NOT held under a single lock, so
    two processes both crossing the ceiling in the same instant may double-seal (one
    ``os.replace`` hits a path the other just renamed → FileNotFoundError, caught
    below as non-fatal). This NEVER loses an event — each real append opens the live
    path fresh under flock with O_APPEND, always landing in a valid live log — a
    double-seal only yields a near-empty sealed segment, which read_events tolerates.
    Rotation is deliberately best-effort, not globally serialized: holding a lock
    across the whole sequence would add contention to the hot path for a cosmetic
    guarantee (segment boundaries), not a correctness one."""
    live = sel_log_path()
    try:
        if not live.exists() or live.stat().st_size < _MAX_SEGMENT_BYTES:
            return
        seg_dir = _segment_dir()
        seg_dir.mkdir(parents=True, exist_ok=True)
        # Monotonic-ish segment name from the file's own mtime (no Date.now reliance).
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        sealed = seg_dir / f"segment-{stamp}.jsonl"
        os.replace(live, sealed)  # atomic rename; the live path is now free
        # Start the new live log with a boundary event so a reader can see the seam.
        boundary = {
            "event_id": uuid4().hex[:16],
            "ts": datetime.now(timezone.utc).isoformat(),
            "event_type": "sel_rotation",
            "caller": "sel",
            "operation": "rotate",
            "outcome": "allow",
            "resource": f"sealed={sealed.name}",
            "reason": "segment size ceiling reached",
        }
        _write_line(live, json.dumps(boundary, ensure_ascii=False))
    except Exception as exc:
        logger.warning("sel: rotation failed (non-fatal): %s", exc)


def append_event(
    *,
    event_type: str,
    caller: str,
    operation: str,
    outcome: str,
    resource: str = "",
    reason: str = "",
) -> None:
    """Append one security event. FAIL-SOFT: never raises, never blocks the caller.

    Args:
        event_type: event family, e.g. ``tool_invocation`` / ``sel_rotation``.
        caller: 归属 — session/cron/subagent key (identity, not content).
        operation: tool name or ``METHOD /api/path``.
        outcome: a verdict.Disposition name lower-cased (``allow``/``warn``/``hitl``/``deny``).
        resource: a REDACTED summary (use summarize_resource for a payload) — never raw values.
        reason: short human reason (deny/warn rationale), optional.
    """
    try:
        _maybe_rotate()
        event = {
            "event_id": uuid4().hex[:16],
            "ts": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "caller": caller,
            "operation": operation,
            "outcome": outcome,
            "resource": resource[:_MAX_RESOURCE_CHARS] if resource else "",
            "reason": reason[:_MAX_RESOURCE_CHARS] if reason else "",
        }
        _write_line(sel_log_path(), json.dumps(event, ensure_ascii=False))
    except Exception as exc:
        # Hot path: a broken audit must never break tool execution.
        logger.warning("sel: append_event failed (non-fatal): %s", exc)


def read_events() -> list[dict]:
    """Return ALL events — sealed segments (chronological) then the live log.

    This is the事后追溯 path: a reader gets the full history even across rotations.
    Malformed lines are skipped (a corrupt line never hides the rest)."""
    events: list[dict] = []

    def _read_file(p: Path) -> None:
        try:
            if not p.exists():
                return
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # skip a corrupt line, keep the rest
        except Exception as exc:
            logger.warning("sel: read failed for %s (non-fatal): %s", p, exc)

    seg_dir = _segment_dir()
    if seg_dir.exists():
        for seg in sorted(seg_dir.glob("segment-*.jsonl")):
            _read_file(seg)
    _read_file(sel_log_path())
    return events
