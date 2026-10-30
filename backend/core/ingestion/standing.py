"""What readers outside CDF may know about a unit, published (design
docs/design/2026-10-30-cdf-fixing-spec.md §2 C2).

A reader of the main files (MEMORY / EVOLUTION injection, Hive lessons, the usage
ledger, the knowledge CLI) reads two things only: the file's text, and the functions
here. The ingestion tables stay private to ``core.ingestion``: when their meaning
changes (trust levels, write records), these functions change with them and the readers
do not. A store that cannot be read gives the empty answer, never an error.

Public: ``connect``, ``Standing``, ``unit_standing``, ``Selection``, ``latest_selection``,
``record_selection``, ``recent_run_refs``, ``lesson_rows``, ``unit_states``, ``llm_usage_rows``.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)


def connect(*, write: bool = False, busy_timeout_ms: int = 500) -> Optional[sqlite3.Connection]:
    """A connection to the CDF store for the functions below, or None when it cannot be
    opened (a reader then works without it). *write*: needed only by ``record_selection``;
    a read never creates the store."""
    try:
        if write:
            from .store import connect_rw
            return connect_rw(busy_timeout_ms=busy_timeout_ms)
        from .store import connect_ro
        return connect_ro()
    except (OSError, sqlite3.Error):
        logger.debug("CDF store unavailable", exc_info=True)
        return None


@dataclass(frozen=True)
class Standing:
    """How much a unit is borne out and used: what the injection ranking reads."""
    support: float = 0.0          # summed weight of supporting evidence (incl. the origin)
    refute: float = 0.0           # summed weight of refuting evidence
    strength: int = 0             # 3 user's words, 2 measured, 1 a run's lesson, 0 other
    evidence_at: Optional[str] = None   # newest observed_at of its evidence (ISO)
    used: int = 0                 # times it was shown or cited
    last_used: Optional[str] = None     # ISO
    written_at: Optional[str] = None    # when CDF last wrote it (ISO), CDF units only


def unit_standing(conn: Optional[sqlite3.Connection], anchor: str) -> Standing:
    """The standing of the unit at *anchor*; the empty standing when there is no store or
    it cannot be read."""
    if conn is None:
        return Standing()
    try:
        return _standing(conn, anchor)
    except sqlite3.Error:
        logger.debug("CDF standing lookup failed for %s", anchor, exc_info=True)
        return Standing()


def _standing(conn: sqlite3.Connection, anchor: str) -> Standing:
    rows = conn.execute(
        """SELECT cs.weight,cs.polarity,r.trust,r.root_kind,r.observed_at
           FROM claim_support cs JOIN evidence_roots r ON r.root_id=cs.root_id
           WHERE cs.claim_anchor=? AND cs.inactive=0""",
        (anchor,),
    ).fetchall()
    identity = anchor.rsplit("#", 1)[-1]
    written_at = None
    if identity.startswith("cand:"):
        unit_id = identity[5:]
        initial = conn.execute(
            """SELECT i.evidence_root_ids FROM ingestion_units u
               JOIN ingestion_inbox i ON i.id=u.candidate_id WHERE u.unit_id=?""",
            (unit_id,),
        ).fetchone()
        if initial:
            try:
                root_ids = [str(item) for item in json.loads(str(initial[0]) or "[]")]
            except (TypeError, ValueError, json.JSONDecodeError):
                root_ids = []
            if root_ids:
                marks = ",".join("?" for _ in root_ids)
                rows = [*rows, *conn.execute(
                    f"SELECT 0.0,'origin',trust,root_kind,observed_at FROM evidence_roots "
                    f"WHERE root_id IN ({marks})", root_ids,
                ).fetchall()]
        row = conn.execute(
            "SELECT updated_at FROM ingestion_writes WHERE unit_id=? AND step='done' "
            "ORDER BY updated_at DESC LIMIT 1", (unit_id,),
        ).fetchone()
        written_at = str(row[0]) if row and row[0] else None
    support = sum(float(row[0]) for row in rows if str(row[1]) in {"support", "origin"})
    refute = sum(float(row[0]) for row in rows if str(row[1]) == "refute")
    strength = 0
    observed = [str(row[4]) for row in rows if row[4]]
    for row in rows:
        trust, kind = str(row[2]), str(row[3])
        strength = max(strength, 3 if trust == "user" else 2 if trust == "measured" else
                       1 if kind.startswith("run_") else 0)
    usage = conn.execute("SELECT COUNT(*),MAX(at) FROM unit_usage WHERE anchor=?", (anchor,)).fetchone()
    return Standing(support=support, refute=refute, strength=strength,
                    evidence_at=max(observed) if observed else None,
                    used=int(usage[0] or 0) if usage else 0,
                    last_used=str(usage[1]) if usage and usage[1] else None,
                    written_at=written_at)


@dataclass(frozen=True)
class Selection:
    """The injection selection recorded for one file."""
    file_hash: str
    anchors: tuple[str, ...]
    version: str
    computed_at: str
    omitted: int


def latest_selection(conn: Optional[sqlite3.Connection], file_path: str) -> Optional[Selection]:
    """The newest selection recorded for *file_path*, or None."""
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT file_hash,selected_anchors,score_snapshot_version,computed_at,omitted_count "
            "FROM injection_selection WHERE file_path=? ORDER BY id DESC LIMIT 1",
            (file_path,),
        ).fetchone()
        if row is None:
            return None
        return Selection(str(row[0]), tuple(str(a) for a in json.loads(str(row[1]))),
                         str(row[2]), str(row[3]), int(row[4]))
    except (sqlite3.Error, TypeError, ValueError, json.JSONDecodeError):
        logger.debug("CDF injection selection lookup failed", exc_info=True)
        return None


def record_selection(conn: Optional[sqlite3.Connection], *, file_path: str, file_hash: str,
                     anchors: tuple[str, ...], version: str, computed_at: str, omitted: int) -> None:
    """Record one injection selection (best effort: a failure is logged, not raised)."""
    if conn is None:
        return
    try:
        conn.execute(
            """INSERT INTO injection_selection(
                 file_path,file_hash,selected_anchors,score_snapshot_version,computed_at,omitted_count
               ) VALUES(?,?,?,?,?,?)""",
            (file_path, file_hash, json.dumps(list(anchors), ensure_ascii=False),
             version, computed_at, omitted),
        )
        conn.commit()
    except sqlite3.Error:
        logger.debug("CDF injection selection record failed", exc_info=True)


# ── Hive lessons (core/hive_lessons) ──────────────────────────────────────────

def recent_run_refs(conn: sqlite3.Connection, limit: int) -> list[str]:
    """The source refs (run ids) of the *limit* most recent S3 runs, newest first."""
    return [str(r[0]) for r in conn.execute(
        "SELECT source_ref FROM ingestion_inbox WHERE source_v2='S3' GROUP BY source_ref "
        "ORDER BY MAX(created_at) DESC LIMIT ?", (limit,))]


def lesson_rows(conn: sqlite3.Connection, kinds, session_id: str,
                run_refs) -> list[tuple[str, str, Any]]:
    """(inbox id, source, payload) of the main-pass units of *kinds* that S2/S7 extracted
    from *session_id* or S3 from one of *run_refs*, oldest first."""
    kinds, run_refs = list(kinds), list(run_refs)
    marks = ",".join("?" for _ in kinds)
    in_runs = ",".join("?" for _ in run_refs) or "NULL"
    return [tuple(r) for r in conn.execute(
        f"SELECT id, source_v2, payload FROM ingestion_inbox "
        f"WHERE unit_kind IN ({marks}) AND extraction_pass='main' AND ("
        f"(source_v2 IN ('S2','S7') AND source_ref=?) OR (source_v2='S3' AND source_ref IN ({in_runs}))) "
        f"ORDER BY created_at, id", (*kinds, session_id, *run_refs)).fetchall()]


def unit_states(conn: sqlite3.Connection, candidate_ids) -> list[tuple[str, str]]:
    """(inbox id, state) of the units judged from *candidate_ids*."""
    ids = list(candidate_ids)
    if not ids:
        return []
    marks = ",".join("?" for _ in ids)
    return [tuple(r) for r in conn.execute(
        f"SELECT candidate_id, state FROM ingestion_units WHERE candidate_id IN ({marks})", ids)]


# ── the usage ledger import (core/usage_import) ───────────────────────────────

def llm_usage_rows(ingestion_db, until_day: str) -> list[tuple[str, str, int, int]]:
    """(utc_day, source, input_tokens, output_tokens) of CDF's own model calls up to
    *until_day*, read-only from the store at *ingestion_db*; none when it is absent or
    unreadable."""
    from pathlib import Path
    if not Path(ingestion_db).is_file():
        return []
    try:
        src = sqlite3.connect(f"file:{ingestion_db}?mode=ro", uri=True, timeout=5.0)
    except sqlite3.Error:
        return []
    try:
        return [tuple(r) for r in src.execute(
            "SELECT utc_day, source, input_tokens, output_tokens FROM ingestion_llm_usage "
            "WHERE utc_day <= ?", (until_day,)).fetchall()]
    except sqlite3.Error:
        return []
    finally:
        src.close()
