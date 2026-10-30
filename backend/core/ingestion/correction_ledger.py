"""Correction ledger: each correction a person made, with the class the person chose
(design docs/design/2026-10-30-autonomy-tenets-gaps.md §2).

A correction is a person rejecting, rewriting or sending back what the agent did. The
ledger keeps one row per correction, and the person (never a model) says which class it
belongs to: an existing knowledge entry or golden task that should have prevented it, an
earlier correction, or a new class. The repeat-correction rate is then a count:

    repeat = rows whose class is an entry, a golden task or an earlier correction
    rate   = repeat / confirmed rows, over a rolling window

This module is the only writer of the ``correction_ledger`` table (the CDF store,
``core/ingestion/store``). Public symbols:

- ``detect(text)`` — whether a user message states a mistake (the error tier of
  ``core.runtime_hooks``' patterns; a redirect such as "go check X" is steering, not a
  mistake, and is not a correction).
- ``open_row(...)`` — record a correction waiting for the person's choice, with up to
  ``MAX_CANDIDATES`` classes found by BM25 over the knowledge a correction can point at
  (no model is asked). One row per (surface, session, client id): a resent message does
  not add a second one.
- ``confirm(row_id, choice)`` — the person's choice: a key among that row's candidates,
  ``"new"`` or ``"not_correction"``. Any other key is refused, so a class can only be
  one the person was shown.
- ``pending(session_id=None, brain=None)`` — rows still waiting: a session's (the
  picker under its messages), a Brain's, or all of them (the CDF panel).
- ``rate(window_days, brain=None, now=None)`` — the rate with its coverage figures.
- ``record_chat_message(session_id, client_id, text)`` — the chat send path's entry:
  detect, find the session's Brain and the reply being corrected, open a row.
- ``record_swarm_out_reject(dispatch_id)`` — a Hive run's delivery the person sent back.
- ``record_escalation(esc)`` — an escalation the person resolved against Swarm's pick.

A pipeline ``human_override`` is a rescue (the person lands past a gate), counted by the
run scorecard, not a correction of the agent's output; withdrawing a Brain entry gets its
entry when the brain-driven withdrawal lands (design §2.2).
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

SURFACES = ("chat", "pipeline_override", "swarm_out_reject", "brain_withdraw", "escalation")
CLASS_KINDS = ("entry", "golden", "ledger", "new")
REPEAT_KINDS = ("entry", "golden", "ledger")
MAX_CANDIDATES = 3
LEDGER_LOOKBACK_DAYS = 30
SMALL_SAMPLE = 10
THRESHOLDS = {"L2": 0.20, "L3": 0.05}
_MAX_TEXT = 4000
_MAX_REPLY = 2000

DDL = """
CREATE TABLE IF NOT EXISTS correction_ledger (
  id TEXT PRIMARY KEY,
  at TEXT NOT NULL,
  surface TEXT NOT NULL CHECK (surface IN
    ('chat','pipeline_override','swarm_out_reject','brain_withdraw','escalation')),
  session_id TEXT,
  client_id TEXT NOT NULL,          -- the source's own key: a chat message's client id, a dispatch id, an escalation id
  brain TEXT,
  run_id TEXT,
  text TEXT NOT NULL,
  candidates TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('unconfirmed','confirmed','not_correction')),
  class_kind TEXT CHECK (class_kind IN ('entry','golden','ledger','new')),
  class_key TEXT,
  confirmed_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_correction_ledger_message
  ON correction_ledger(surface, ifnull(session_id, ''), client_id);
CREATE INDEX IF NOT EXISTS ix_correction_ledger_at ON correction_ledger(at);
"""

_DDD_DOCS = ("PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md")
_ENTRY_TYPES = ("correction", "pitfall")


class LedgerError(ValueError):
    """A request the ledger refuses (unknown row, a choice the row did not offer)."""


def _now(now: Optional[datetime] = None) -> datetime:
    return now or datetime.now(timezone.utc)


def _rw() -> sqlite3.Connection:
    from core.ingestion.store import connect_rw
    conn = connect_rw()
    conn.executescript(DDL)
    return conn


def _ro() -> Optional[sqlite3.Connection]:
    """A read-only connection, or None when the store or the table does not exist yet
    (a reader never creates either)."""
    from core.ingestion.store import connect_ro
    try:
        conn = connect_ro()
    except (FileNotFoundError, sqlite3.Error):
        return None
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='correction_ledger'").fetchone()
    if row is None:
        conn.close()
        return None
    return conn


# ── detection ────────────────────────────────────────────────────────────────

def detect(text: str) -> bool:
    """True when *text* (what the person typed) states a mistake."""
    if not isinstance(text, str) or not text.strip():
        return False
    from core.injected_blocks import strip_injected_blocks
    from core.runtime_hooks import _CORRECTION_PATTERNS_EN
    return bool(_CORRECTION_PATTERNS_EN.search(strip_injected_blocks(text)))


# ── candidates ───────────────────────────────────────────────────────────────

def _entry_docs(workspace: Path, brain: Optional[str]) -> dict[str, dict[str, str]]:
    """{key: {kind, title, text}} of the entries a correction can point at: the Brain's
    correction and pitfall entries, the agent's own corrections (EVOLUTION) and the
    world corrections (MEMORY)."""
    from core.ingestion.units import parse_units
    out: dict[str, dict[str, str]] = {}
    files: list[tuple[Path, Optional[str]]] = []
    if brain:
        base = workspace / "Projects" / brain / "2-understanding"
        files += [(base / name, None) for name in _DDD_DOCS]
    ctx = workspace / ".context"
    files += [(ctx / "EVOLUTION.md", "Corrections Captured"), (ctx / "MEMORY.md", None)]
    for path, section in files:
        if not path.is_file():
            continue
        try:
            units = parse_units(path, root=workspace)
        except (OSError, UnicodeDecodeError, ValueError):
            logger.debug("correction candidates: cannot parse %s", path, exc_info=True)
            continue
        for u in units:
            if section is not None:
                if u.section != section:
                    continue
            elif u.entry_type not in _ENTRY_TYPES:
                continue
            out[u.anchor] = {"kind": "entry", "title": u.title or u.section, "text": u.text}
    return out


def _golden_docs(workspace: Path) -> dict[str, dict[str, str]]:
    """Delivery-level golden tasks (design §1.2): a correction can say one failed."""
    try:
        from core.golden_set_store import load_merged
        cases = load_merged(workspace).get("cases") or []
    except Exception:  # noqa: BLE001 — candidates are a convenience, never a failure
        logger.debug("correction candidates: golden set unreadable", exc_info=True)
        return {}
    out: dict[str, dict[str, str]] = {}
    for c in cases:
        if isinstance(c, dict) and c.get("level") == "delivery" and isinstance(c.get("id"), str):
            text = " ".join(str(c.get(k) or "") for k in ("title", "task", "guards"))
            out[c["id"]] = {"kind": "golden", "title": str(c.get("title") or c["id"]), "text": text}
    return out


def _ledger_docs(conn: Optional[sqlite3.Connection], now: datetime,
                 exclude: Optional[str] = None) -> dict[str, dict[str, str]]:
    """Recent corrections that started a class of their own: a later one can repeat them."""
    if conn is None:
        return {}
    since = (now - timedelta(days=LEDGER_LOOKBACK_DAYS)).isoformat()
    rows = conn.execute(
        "SELECT id, text FROM correction_ledger WHERE status='confirmed' AND class_kind='new' "
        "AND at >= ? ORDER BY at DESC", (since,)).fetchall()
    return {r["id"]: {"kind": "ledger", "title": r["text"][:120], "text": r["text"]}
            for r in rows if r["id"] != exclude}


def candidates(text: str, reply: str = "", *, brain: Optional[str] = None,
               workspace: Optional[Path] = None, conn: Optional[sqlite3.Connection] = None,
               now: Optional[datetime] = None) -> list[dict[str, str]]:
    """Up to ``MAX_CANDIDATES`` classes for a correction, best BM25 score first: the
    query is the correction and the reply it corrects."""
    from core.memory_index import _bm25_scores
    if workspace is None:
        from core.project_registry import get_swarmws
        workspace = get_swarmws()
    pool = {**_entry_docs(workspace, brain), **_golden_docs(workspace),
            **_ledger_docs(conn, _now(now))}
    query = f"{text}\n{(reply or '')[:_MAX_REPLY]}"
    scores = _bm25_scores(query, {k: v["text"] for k, v in pool.items()})
    best = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_CANDIDATES]
    return [{"key": k, "kind": pool[k]["kind"], "title": pool[k]["title"][:200]} for k, _ in best]


# ── writes ───────────────────────────────────────────────────────────────────

def open_row(*, surface: str, text: str, client_id: str, session_id: Optional[str] = None,
             brain: Optional[str] = None,
             run_id: Optional[str] = None, reply: str = "",
             workspace: Optional[Path] = None, now: Optional[datetime] = None) -> Optional[str]:
    """Record a correction waiting for the person's choice. ``client_id`` is the
    source's own key (a chat message's client id, a dispatch id, an escalation id).
    Returns the row's id, or the id of the row already recorded for that source."""
    if surface not in SURFACES:
        raise LedgerError(f"unknown surface {surface!r}")
    if not isinstance(text, str) or not text.strip():
        raise LedgerError("a correction needs its text")
    if not isinstance(client_id, str) or not client_id:
        raise LedgerError("a correction needs its source's key")
    at = _now(now)
    conn = _rw()
    try:
        cands = candidates(text, reply, brain=brain, workspace=workspace, conn=conn, now=at)
        row_id = f"cor-{uuid.uuid4().hex[:16]}"
        conn.execute(
            "INSERT INTO correction_ledger(id, at, surface, session_id, client_id, brain, run_id, "
            "text, candidates, status) VALUES (?,?,?,?,?,?,?,?,?, 'unconfirmed')",
            (row_id, at.isoformat(), surface, session_id, client_id, brain, run_id,
             text[:_MAX_TEXT], json.dumps(cands, ensure_ascii=False)))
        return row_id
    except sqlite3.IntegrityError:   # ux_correction_ledger_message: this message has its row
        hit = conn.execute(
            "SELECT id FROM correction_ledger WHERE surface=? AND session_id IS ? AND client_id IS ?",
            (surface, session_id, client_id)).fetchone()
        return hit["id"] if hit else None
    finally:
        conn.close()


def confirm(row_id: str, choice: str, *, now: Optional[datetime] = None) -> dict[str, Any]:
    """Record the person's choice for a row: a candidate key, ``"new"`` or
    ``"not_correction"``. Choosing again replaces the earlier choice."""
    conn = _rw()
    try:
        row = conn.execute("SELECT * FROM correction_ledger WHERE id=?", (row_id,)).fetchone()
        if row is None:
            raise LedgerError(f"no correction {row_id!r}")
        at = _now(now).isoformat()
        if choice == "not_correction":
            status, kind, key = "not_correction", None, None
        elif choice == "new":
            status, kind, key = "confirmed", "new", None
        else:
            offered = {c["key"]: c["kind"] for c in json.loads(row["candidates"] or "[]")}
            if choice not in offered:
                raise LedgerError("the choice is not one this correction offered")
            status, kind, key = "confirmed", offered[choice], choice
        conn.execute(
            "UPDATE correction_ledger SET status=?, class_kind=?, class_key=?, confirmed_at=? WHERE id=?",
            (status, kind, key, at, row_id))
        return {"id": row_id, "status": status, "class_kind": kind, "class_key": key,
                "repeat": kind in REPEAT_KINDS}
    finally:
        conn.close()


# ── reads ────────────────────────────────────────────────────────────────────

def _row(r: sqlite3.Row) -> dict[str, Any]:
    return {"id": r["id"], "at": r["at"], "surface": r["surface"], "session_id": r["session_id"],
            "client_id": r["client_id"], "brain": r["brain"], "run_id": r["run_id"],
            "text": r["text"], "candidates": json.loads(r["candidates"] or "[]"),
            "status": r["status"], "class_kind": r["class_kind"], "class_key": r["class_key"]}


PENDING_LIMIT = 50


def pending(session_id: Optional[str] = None, *, brain: Optional[str] = None,
            window_days: int = 28, now: Optional[datetime] = None) -> list[dict[str, Any]]:
    """Rows still waiting for the person within the window, oldest first: a session's,
    a Brain's, or (neither named) all of them, at most ``PENDING_LIMIT``."""
    conn = _ro()
    if conn is None:
        return []
    try:
        since = (_now(now) - timedelta(days=window_days)).isoformat()
        sql, args = "SELECT * FROM correction_ledger WHERE status='unconfirmed' AND at >= ?", [since]
        if session_id is not None:
            sql += " AND session_id=?"
            args.append(session_id)
        if brain is not None:
            sql += " AND brain=?"
            args.append(brain)
        return [_row(r) for r in conn.execute(sql + f" ORDER BY at LIMIT {PENDING_LIMIT}", args).fetchall()]
    finally:
        conn.close()


def rate(window_days: int = 28, *, brain: Optional[str] = None,
         now: Optional[datetime] = None) -> dict[str, Any]:
    """The repeat-correction rate over the last *window_days*, with the figures that
    keep it honest: how many corrections are still unconfirmed, and how many the
    person said were not corrections. ``rate`` is None with no confirmed row."""
    since = (_now(now) - timedelta(days=window_days)).isoformat()
    out: dict[str, Any] = {"window_days": window_days, "brain": brain, "total": 0,
                           "confirmed": 0, "unconfirmed": 0, "not_correction": 0, "repeat": 0,
                           "by_kind": {k: 0 for k in CLASS_KINDS}, "rate": None,
                           "small_sample": True, "thresholds": dict(THRESHOLDS)}
    conn = _ro()
    if conn is None:
        return out
    try:
        sql, args = "SELECT status, class_kind FROM correction_ledger WHERE at >= ?", [since]
        if brain is not None:
            sql += " AND brain=?"
            args.append(brain)
        for r in conn.execute(sql, args).fetchall():
            out["total"] += 1
            out[r["status"]] += 1
            if r["status"] == "confirmed" and r["class_kind"] in CLASS_KINDS:
                out["by_kind"][r["class_kind"]] += 1
                out["repeat"] += r["class_kind"] in REPEAT_KINDS
    finally:
        conn.close()
    if out["confirmed"]:
        out["rate"] = round(out["repeat"] / out["confirmed"], 4)
    out["small_sample"] = out["confirmed"] < SMALL_SAMPLE
    return out


# ── the chat send path ───────────────────────────────────────────────────────

def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str))
    return ""


def last_reply(messages: Iterable[dict]) -> str:
    """The text of the newest assistant message in *messages* (oldest first)."""
    for m in reversed(list(messages)):
        if isinstance(m, dict) and m.get("role") == "assistant":
            text = _text_of(m.get("content"))
            if text.strip():
                return text
    return ""


def session_brain(session_id: str, text: str) -> Optional[str]:
    """The session's Brain, by the same fail-closed detection recall uses."""
    try:
        from core.recall_multi import detect_active_project
        from core.repo_guidance import session_bound_project
        project, _signal = detect_active_project(
            query=text, session_bound_project=session_bound_project(session_id))
        return project
    except Exception:  # noqa: BLE001 — a correction without a Brain is still recorded
        logger.debug("correction ledger: brain detection failed", exc_info=True)
        return None


def record_chat_message(session_id: Optional[str], client_id: Optional[str], text: str,
                        reply: str = "") -> Optional[str]:
    """Open a row when a chat message states a mistake. Only an existing session can
    correct a reply; a message without a client id cannot be shown a picker."""
    if not session_id or not client_id or not detect(text):
        return None
    return open_row(surface="chat", text=text, session_id=session_id, client_id=client_id,
                    brain=session_brain(session_id, text), reply=reply)


# ── explicit actions ─────────────────────────────────────────────────────────

def record_swarm_out_reject(dispatch_id: str) -> Optional[str]:
    """The person sent a Hive run's delivery back: a correction of that run."""
    from core.swarm_out import _log_row_of
    row = _log_row_of(dispatch_id)
    agent = row.get("project_agent") if isinstance(row.get("project_agent"), dict) else {}
    title = str(row.get("title") or dispatch_id)
    return open_row(surface="swarm_out_reject", client_id=dispatch_id,
                    session_id=row.get("session_id") or None,
                    brain=agent.get("brain") or None, run_id=dispatch_id,
                    text=f"Delivery sent back: {title}")


def _norm(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def record_escalation(esc: Any) -> Optional[str]:
    """An escalation the person resolved with something other than Swarm's pick. One
    with no pick (a genuinely open question), or resolved by timeout or by Swarm, is
    not a correction."""
    if getattr(esc, "resolved_by", None) != "user" or not getattr(esc, "recommendation", None):
        return None
    picks = {_norm(esc.recommendation)}
    picks |= {_norm(o.label) for o in (getattr(esc, "options", None) or [])
              if getattr(o, "is_recommendation", False)}
    resolution = getattr(esc, "resolution", "") or ""
    if _norm(resolution) in picks:
        return None
    return open_row(surface="escalation", client_id=str(esc.id),
                    brain=getattr(esc, "project", None) or None,
                    text=f"{esc.title}: chose {resolution!s:.300}, not the recommended {esc.recommendation!s:.300}")
