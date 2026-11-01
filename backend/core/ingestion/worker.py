"""Owner-only Cognitive Distillation Flow worker.

- ``WeightedLaneScheduler`` provides fair, overdue-aware lane selection.
- ``ingestion_admission`` protects chat latency under load.
- ``process_candidate`` persists the deterministic unit state machine.
- ``cleanup_expired`` atomically summarizes and removes 30-day detail.
- ``WorkerHandle`` runs and joins the worker outside FastAPI's event loop.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sqlite3
import threading
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from . import contracts
from .api import Lane
from .owner import Owner
from .store import connect_rw

logger = logging.getLogger(__name__)

LANE_WEIGHTS: dict[Lane, int] = {
    "immediate": 8, "session": 4, "run": 4, "scheduled": 2,
    "summary": 2, "maintenance": 1,
}
MAX_WAIT = {
    "session": timedelta(minutes=30), "run": timedelta(minutes=30),
    "scheduled": timedelta(hours=12), "summary": timedelta(hours=36),
    "maintenance": timedelta(hours=36),
}
TERMINAL_STATES = frozenset({
    "merged", "written", "reinforced", "rejected", "failed_poison", "expired_unprocessed",
})
# what an outage or a busy store produces (unlike ambiguous_project, which the same
# input reproduces)
OUTAGE_REASONS = frozenset({"unavailable", "stale", "db_busy", "user_race", "stale_plan"})
INFRA_ATTEMPTS = 30     # judge outages: 1, 2, 4 ... 60 min backoff, about a day in all
LOOP_BACKOFF_SECONDS = 30  # a background loop that raised waits this long, then goes on
# the daily recovery requeues a unit stranded between claim and plan only after this
# long without a move (the run lane may be between the two at that moment)
REQUEUE_GRACE = timedelta(minutes=15)


@dataclass(frozen=True)
class Admission:
    """One lane's current execution permissions."""

    run: bool
    llm: bool


def ingestion_admission(lane: Lane, *, maintenance_floor: bool = False) -> Admission:
    """Return the memory-pressure × lane admission decision.

    A streaming chat does not hold any lane: the CPU the two shared was a quadratic
    parse (fixed in ``units``), and waiting for every tab to stop starved CDF for the
    whole working day (2026-10-29-cdf-chat-coexistence.md). The session review's CLI
    fork has its own check, the chat tabs' spawn budget (``run_session_job``)."""
    from core.resource_monitor import resource_monitor

    pressure = resource_monitor.system_memory().pressure_level
    if maintenance_floor:
        return Admission(True, False)
    if pressure in {"warning", "critical"} and lane != "immediate":
        return Admission(False, False)
    return Admission(True, True)


class WeightedLaneScheduler:
    """Deterministic weighted round robin with max waits and burst limiting."""

    def __init__(self) -> None:
        self._wheel = [lane for lane, weight in LANE_WEIGHTS.items() for _ in range(weight)]
        self._cursor = 0
        self._immediate_burst = 0

    def choose(
        self,
        available: Iterable[Lane],
        oldest: dict[Lane, datetime],
        now: datetime,
    ) -> Lane | None:
        """Choose one available lane, prioritizing overdue work."""
        choices = set(available)
        if not choices:
            return None
        overdue = [
            lane for lane in choices
            if lane in MAX_WAIT and now - oldest.get(lane, now) >= MAX_WAIT[lane]
        ]
        if overdue:
            lane = max(overdue, key=lambda item: now - oldest.get(item, now))
            self._immediate_burst = 0
            return lane
        if self._immediate_burst >= 10:
            non_immediate = choices - {"immediate"}
            if non_immediate:
                choices = non_immediate
        for _ in range(len(self._wheel)):
            lane = self._wheel[self._cursor]
            self._cursor = (self._cursor + 1) % len(self._wheel)
            if lane in choices:
                self._immediate_burst = self._immediate_burst + 1 if lane == "immediate" else 0
                return lane
        return None


def aggregate_candidate_state(states: Iterable[str]) -> tuple[str, dict[str, int]]:
    """Apply the normative candidate aggregation precedence."""
    counts = Counter(states)
    if counts["writing"] or counts["judged"]:
        return "in_progress", dict(counts)
    if counts["queued"] or counts["deferred"]:
        return "partial_pending", dict(counts)
    return "done", {state: counts[state] for state in TERMINAL_STATES if counts[state]}


def error_fingerprint(exc: BaseException) -> str:
    """Return a stable exception-class and message fingerprint."""
    material = f"{type(exc).__module__}.{type(exc).__name__}:{exc}"
    return hashlib.sha256(material.encode("utf-8", "replace")).hexdigest()[:24]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _decision_fields(decision: contracts.UnitDecision) -> dict[str, Any]:
    return {
        "decision": decision.model_dump_json(),
        "destination": decision.destination.model_dump_json() if decision.destination else None,
        "project": decision.destination.project if decision.destination else None,
        "action": decision.action,
        "reason": decision.reason,
    }


def _defer_or_poison(
    conn: sqlite3.Connection,
    owner: Owner,
    unit: sqlite3.Row,
    reason: str,
    *,
    fingerprint: str | None = None,
    destination: Any = None,
    environment: bool = False,
) -> None:
    """Defer *unit* for a retry, or poison it when its retries are spent. *environment*
    marks an error from the machine (a file system or a locked database), not from the
    unit: it backs off like a judge outage instead of counting toward a quick poison."""
    from .contracts_v2 import PARKED_DEFER_REASONS

    if reason in PARKED_DEFER_REASONS:
        if not owner.claim_unit_step(
            conn, unit["unit_id"], "queued", "deferred", reason=reason,
            next_attempt_at=None,
        ):
            raise RuntimeError("owner epoch fence rejected parked transition")
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(ingestion_units)")}
        if "defer_reason" in columns:
            conn.execute("UPDATE ingestion_units SET defer_reason=? WHERE unit_id=?",
                         (reason, unit["unit_id"]))
        if reason == "missing_section":
            if destination is not None:
                destination_value = (
                    destination.model_dump(mode="json")
                    if hasattr(destination, "model_dump") else dict(destination)
                )
                conn.execute(
                    "UPDATE ingestion_units SET destination=? WHERE unit_id=?",
                    (json.dumps(destination_value, ensure_ascii=False, sort_keys=True), unit["unit_id"]),
                )
            else:
                destination_value = json.loads(unit["destination"] or "{}")
            target_path = str(destination_value.get("relative_path") or "")
            section = str(destination_value.get("section") or "")
            prompt = f"Add `## {section}` to {target_path}"
            conn.execute(
                "INSERT INTO ingestion_events(at,kind,detail) VALUES(?,'missing_section',?)",
                (_utcnow().isoformat(), json.dumps({
                    "unit_id": unit["unit_id"], "target_path": target_path,
                    "section": section, "prompt": prompt, "summary": prompt,
                }, ensure_ascii=False, sort_keys=True)),
            )
        return
    attempts = int(unit["attempts"]) + 1
    # The judge (Bedrock) being unavailable, or the machine failing (a read-only or full
    # disk, a locked database), says nothing about the unit: back off up to an hour and
    # give up only after about a day, so an outage does not poison every unit tried
    # during it. Anything else is short and bounded; a unit poisoned by an error is
    # tried again at the next startup (``requeue_error_poisons``), when the code that
    # raised may have been fixed.
    infra = (environment or reason in OUTAGE_REASONS
             or reason.startswith(("judge_unavailable", "entailment_unavailable")))
    limit = INFRA_ATTEMPTS if infra else 5
    same_errors = not infra and fingerprint is not None and fingerprint == unit["error_fingerprint"]
    poison = attempts >= limit or (same_errors and attempts >= 3)
    target = "failed_poison" if poison else "deferred"
    final_reason = "retries_exhausted" if attempts >= limit else reason
    delay = min(3600, 60 * 2 ** (attempts - 1)) if infra else min(300, 5 * 2 ** (attempts - 1))
    next_at = None if poison else (_utcnow() + timedelta(seconds=delay)).isoformat()
    if not owner.claim_unit_step(
        conn, unit["unit_id"], "queued", target, attempts=attempts,
        next_attempt_at=next_at, reason=final_reason,
        error_class="infra", error_fingerprint=fingerprint,
    ):
        raise RuntimeError("owner epoch fence rejected deferred transition")
    if poison:
        conn.execute(
            "INSERT INTO ingestion_events(at, kind, detail) VALUES (?, 'poison', ?)",
            (_utcnow().isoformat(), json.dumps({"unit_id": unit["unit_id"], "reason": final_reason})),
        )


def _hand_to_writer(conn: sqlite3.Connection, owner: Owner, unit_id: str,
                    decision: contracts.UnitDecision) -> None:
    """Run the journaled writer for one ``writing`` unit. The writer marks the unit
    ``written`` itself, and only when its plan reached ``done``: claiming it here as
    well marked a unit written whose plan had been abandoned (nothing on disk)."""
    from .writer import apply_unit
    writing_row = conn.execute("SELECT * FROM ingestion_units WHERE unit_id=?", (unit_id,)).fetchone()
    try:
        step = apply_unit(decision, writing_row, conn)
    except Exception as exc:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        _unit_failed(conn, owner, unit_id, exc)
        return
    if step == "done":
        _close_declared_gaps(conn, unit_id)
        _apply_overrides(conn, unit_id)


def _apply_overrides(conn: sqlite3.Connection, unit_id: str | None = None) -> None:
    """Retire what written units declared they override (core/ingestion/overrides). Never
    fails the write: the daily maintenance retries what is left."""
    from .overrides import apply
    try:
        apply(conn, unit_id)
    except Exception:  # noqa: BLE001 -- retried by _maintain
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        logger.warning("CDF override failed for %s; retried later", unit_id or "pending", exc_info=True)


def _withdraw_ticked(workspace_root) -> None:
    """A ticked ``- [x]`` entry is finished, so it leaves the main files (brain-driven §2.2)."""
    from pathlib import Path
    from .gaps import withdraw_ticked
    try:
        n = withdraw_ticked(Path(workspace_root))
        if n:
            logger.info("CDF maintenance: %d ticked entries withdrawn", n)
    except Exception:  # noqa: BLE001 -- the next daily pass tries again
        logger.warning("CDF maintenance: withdrawing ticked entries failed", exc_info=True)


def _close_declared_gaps(conn: sqlite3.Connection, unit_id: str | None = None) -> None:
    """Close the gaps a written run event declared (core/ingestion/gaps). Never fails
    the write: the daily maintenance retries what is left open."""
    from .gaps import close_declared
    try:
        close_declared(conn, unit_id)
    except Exception:  # noqa: BLE001 — retried by _maintain
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        logger.warning("CDF gap close failed for %s; retried later", unit_id or "pending", exc_info=True)


def _unit_failed(conn: sqlite3.Connection, owner: Owner, unit_id: str, exc: Exception) -> None:
    """Record *exc* on the unit it happened to. A malformed payload is rejected. A unit
    with a live write plan belongs to ``writer.recover``. Otherwise the unit (queued, or
    ``writing`` with no plan yet) is deferred with the error's fingerprint, so the
    bounded retries apply and it is poisoned instead of retried forever. An OS or
    sqlite operational error is the machine's, not the unit's (``environment``)."""
    from pydantic import ValidationError
    logger.warning("CDF unit %s failed: %s: %s", unit_id, type(exc).__name__, exc)
    fresh = conn.execute("SELECT * FROM ingestion_units WHERE unit_id=?", (unit_id,)).fetchone()
    if fresh is None or fresh["state"] in TERMINAL_STATES:
        return
    # an abandoned plan stays in the journal until the unit is planned again; it is
    # not recovery's, so it must not keep the unit from being deferred here
    if conn.execute("SELECT 1 FROM ingestion_writes WHERE unit_id=? AND step<>'abandoned'",
                    (unit_id,)).fetchone():
        return
    if fresh["state"] == "writing":
        if not owner.claim_unit_step(conn, unit_id, "writing", "queued"):
            return
        fresh = conn.execute("SELECT * FROM ingestion_units WHERE unit_id=?", (unit_id,)).fetchone()
    if fresh["state"] != "queued":
        return
    if isinstance(exc, ValidationError):
        owner.claim_unit_step(conn, unit_id, "queued", "rejected", reason="malformed_payload")
        return
    _defer_or_poison(conn, owner, fresh, f"error:{type(exc).__name__}",
                     fingerprint=error_fingerprint(exc),
                     environment=isinstance(exc, (OSError, sqlite3.OperationalError)))


def _json_value(value: Any, default: Any) -> Any:
    """Decode a persisted JSON field while accepting already-decoded fixtures."""
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return default
    return value


def _row_dict(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    """Return a mutable row copy while tolerating pre-v2 test schemas."""
    return dict(row)


def _is_v2_row(row: sqlite3.Row | dict[str, Any]) -> bool:
    value = _row_dict(row)
    return bool(value.get("unit_kind") or value.get("source_v2"))


def _upsert_v2_unit(conn: sqlite3.Connection, row: dict[str, Any], unit_id: str,
                    epoch: int, project: str | None) -> sqlite3.Row:
    payload = _json_value(row.get("payload"), {})
    claim = dict(payload)
    claim["domain"] = _json_value(row.get("envelope"), {}).get("domain")
    claim["evidence_root_ids"] = _json_value(row.get("evidence_root_ids"), [])
    now = _utcnow().isoformat()
    conn.execute(
        """INSERT INTO ingestion_units(unit_id,candidate_id,claim_group,project,owner_epoch,updated_at)
           VALUES(?,?,?,?,?,?) ON CONFLICT(unit_id) DO NOTHING""",
        (unit_id, row["id"], json.dumps(claim, ensure_ascii=False, sort_keys=True),
         project, epoch, now),
    )
    return conn.execute("SELECT * FROM ingestion_units WHERE unit_id=?", (unit_id,)).fetchone()


def _process_v2_candidate(conn: sqlite3.Connection, owner: Owner, row: sqlite3.Row,
                          workspace_root: Path) -> None:
    """Process one v2 source unit (one durable unit row): no model call, the unit's own
    declarations and the facts of its evidence decide (pipeline_v2.run_unit_v2)."""
    from .pipeline_v2 import run_unit_v2

    value = _row_dict(row)
    # one unit per candidate, with the project its source declared (the inbox's
    # project_hint); the destination comes from the unit itself (pipeline_v2)
    expanded = [(f"{value['id']}.unit", value.get("project_hint"))]
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("UPDATE ingestion_inbox SET state='in_progress',owner_epoch=? WHERE id=?",
                     (owner.epoch, value["id"]))
        units = [_upsert_v2_unit(conn, value, unit_id, owner.epoch, project)
                 for unit_id, project in expanded]
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise

    for (unit_id, project), unit_row in zip(expanded, units):
        if unit_row["state"] in TERMINAL_STATES or unit_row["state"] in {"judged", "writing"}:
            continue
        if unit_row["state"] == "deferred":
            from .contracts_v2 import PARKED_DEFER_REASONS
            if unit_row["reason"] in PARKED_DEFER_REASONS or unit_row["next_attempt_at"] is None:
                continue        # parked: waits for requeue_parked
            if (datetime.fromisoformat(unit_row["next_attempt_at"]) > _utcnow()
                    or not owner.claim_unit_step(conn, unit_id, "deferred", "queued")):
                continue
            unit_row = conn.execute("SELECT * FROM ingestion_units WHERE unit_id=?", (unit_id,)).fetchone()
        unit_input = dict(value)
        unit_input.update(unit_id=unit_id, project_hint=project,
                          project_candidates=json.dumps([project] if project else []))
        # One unit's failure is that unit's: recorded on it (rejected, or deferred with its
        # error fingerprint and poisoned after the bounded retries), never raised out of
        # the worker, whose thread would otherwise end and leave this candidate in_progress.
        try:
            decision = run_unit_v2(unit_input, workspace_root)
            fields = _decision_fields(decision)
            if decision.decision == "defer":
                _defer_or_poison(
                    conn, owner, unit_row, decision.reason,
                    destination=decision.destination,
                )
            elif decision.decision == "merged":
                owner.claim_unit_step(conn, unit_id, "queued", "merged", **fields)
            elif decision.decision == "reinforce":
                owner.claim_unit_step(conn, unit_id, "queued", "reinforced", **fields)
            elif decision.decision == "reject":
                owner.claim_unit_step(conn, unit_id, "queued", "rejected", **fields)
            else:
                # no daily write quota and no size refusal (design 2026-10-30-cdf-agent-
                # decides D11): what is written is the agent's; its size is measured
                if not owner.claim_unit_step(conn, unit_id, "queued", "judged", **fields):
                    raise RuntimeError("owner epoch fence rejected v2 judgment")
                if not owner.claim_unit_step(conn, unit_id, "judged", "writing"):
                    raise RuntimeError("owner epoch fence rejected v2 writer handoff")
                _hand_to_writer(conn, owner, unit_id, decision)
        except Exception as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            _unit_failed(conn, owner, unit_id, exc)

    states = [item[0] for item in conn.execute(
        "SELECT state FROM ingestion_units WHERE candidate_id=?", (value["id"],)
    )]
    aggregate, counts = aggregate_candidate_state(states)
    conn.execute("UPDATE ingestion_inbox SET state=?,versions=? WHERE id=? AND owner_epoch=?",
                 (aggregate, json.dumps({"terminal_counts": counts}, sort_keys=True),
                  value["id"], owner.epoch))


def process_candidate(
    conn: sqlite3.Connection,
    owner: Owner,
    row: sqlite3.Row,
    workspace_root: Path,
) -> None:
    """Process every nonterminal unit for one candidate with isolated DB writes."""
    from .llm_usage import attribute
    value = _row_dict(row)
    with attribute(value.get("source_v2") or value.get("source")):
        _process_candidate(conn, owner, row, workspace_root)


def _process_candidate(conn: sqlite3.Connection, owner: Owner, row: sqlite3.Row,
                       workspace_root: Path) -> None:
    if _is_v2_row(row):
        _process_v2_candidate(conn, owner, row, workspace_root)
        return
    # a candidate of the retired v1 path (no source unit): nothing produces one any more,
    # and nothing can judge it; it and any open unit of it are closed (rejected
    # ``retired_v1``), the record kept until the 30-day cleanup
    candidate_id = _row_dict(row)["id"]
    logger.warning("CDF: candidate %s has no source unit (retired v1 shape); closed unprocessed",
                   candidate_id)
    terminal = ",".join("?" for _ in TERMINAL_STATES)
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(f"""UPDATE ingestion_units SET state='rejected', reason='retired_v1', updated_at=?
                         WHERE candidate_id=? AND state NOT IN ({terminal})""",
                     (_utcnow().isoformat(), candidate_id, *TERMINAL_STATES))
        conn.execute("UPDATE ingestion_inbox SET state='done' WHERE id=?", (candidate_id,))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def reaggregate_candidates(conn: sqlite3.Connection,
                           candidate_ids: Iterable[str] | None = None) -> int:
    """Recompute the state of every candidate not yet done from its units' states, or of
    *candidate_ids* whatever their state (a requeue reopens done ones too).

    Only process_candidate wrote a candidate's state, so a candidate left in_progress
    (the process died mid-judgment, or the writer finished its unit later in recovery)
    was never selected again and its units expired after 30 days. Run after recovery,
    at start and in daily maintenance."""
    changed = 0
    if candidate_ids is None:
        rows = conn.execute(
            "SELECT id,state FROM ingestion_inbox WHERE state IN ('in_progress','partial_pending')"
        ).fetchall()
    else:
        ids = sorted(set(candidate_ids))
        rows = conn.execute(
            f"SELECT id,state FROM ingestion_inbox WHERE id IN ({','.join('?' for _ in ids)})", ids
        ).fetchall() if ids else []
    for row in rows:
        states = [item[0] for item in conn.execute(
            "SELECT state FROM ingestion_units WHERE candidate_id=?", (row["id"],))]
        if not states:
            aggregate, counts = "queued", {}
        else:
            # a unit judged/writing with no live writer is put back by writer.recover;
            # whatever is still in flight here is the run lane's, so it stays in_progress
            aggregate, counts = aggregate_candidate_state(states)
        if aggregate != row["state"]:
            conn.execute("UPDATE ingestion_inbox SET state=?,versions=? WHERE id=?",
                         (aggregate, json.dumps({"terminal_counts": counts}, sort_keys=True), row["id"]))
            changed += 1
    conn.commit()
    return changed


def _stats_key(row: sqlite3.Row) -> tuple[str, str, str, str, str, str, str]:
    decision = json.loads(row["decision"]) if row["decision"] else {}
    destination = json.loads(row["destination"]) if row["destination"] else {}
    vector = decision.get("version_vector") or {}
    digest = hashlib.sha256(json.dumps(vector, sort_keys=True).encode()).hexdigest()[:16] if vector else "-"
    return (
        row["created_at"][:10], row["source"], row["lane"], destination.get("store") or "-",
        # a candidate never split into units (a worker Hive judges nothing) expired as it was
        row["state"] or "expired_unprocessed", row["reason"] or "-", digest,
    )


def cleanup_expired(
    conn: sqlite3.Connection,
    *,
    now: datetime | None = None,
    chunk_size: int = 500,
    vacuum: bool = True,
    truncate_wal: bool = True,
) -> int:
    """Summarize, archive (eval_log) and delete expired candidates in atomic chunks.

    *truncate_wal* False checkpoints PASSIVE: a TRUNCATE checkpoint blocks new writers
    while it waits, and the daily pass runs while chat hooks enqueue (1.5 s budget)."""
    current = now or _utcnow()
    cutoff = (current.astimezone(timezone.utc).date() - timedelta(days=30)).isoformat() + "T00:00:00+00:00"
    total = 0
    while True:
        ids = [row[0] for row in conn.execute(
            """SELECT i.id FROM ingestion_inbox i WHERE i.created_at < ?
               AND NOT EXISTS (
                 SELECT 1 FROM ingestion_units u JOIN ingestion_writes w ON w.unit_id=u.unit_id
                 WHERE u.candidate_id=i.id AND w.step NOT IN ('planned','done','abandoned')
               ) ORDER BY i.created_at LIMIT ?""",
            (cutoff, chunk_size),
        )]
        if not ids:
            break
        marks = ",".join("?" for _ in ids)
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                f"""UPDATE ingestion_units SET state='expired_unprocessed', reason='expired_unprocessed',
                       updated_at=? WHERE candidate_id IN ({marks})
                       AND state NOT IN ('merged','written','rejected','failed_poison','expired_unprocessed')""",
                (_utcnow().isoformat(), *ids),
            )
            rows = conn.execute(
                f"""SELECT i.id AS candidate_id, i.created_at, i.source, i.lane, u.state, u.reason,
                            u.decision, u.destination, u.unit_id
                     FROM ingestion_inbox i LEFT JOIN ingestion_units u ON u.candidate_id=i.id
                     WHERE i.id IN ({marks})""",
                ids,
            ).fetchall()
            grouped: dict[tuple[str, str, str, str, str, str, str], list[Any]] = {}
            for row in rows:
                key = _stats_key(row)
                values = grouped.setdefault(key, [set(), 0, 0])
                values[0].add(row["candidate_id"])
                values[1] += 1 if row["unit_id"] else 0
            for key, values in grouped.items():
                conn.execute(
                    """INSERT INTO ingestion_daily_stats(
                           utc_day, source, lane, store, state, reason, rules_digest,
                           candidates, units, bytes_written)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(utc_day, source, lane, store, state, reason, rules_digest)
                       DO UPDATE SET candidates=candidates+excluded.candidates,
                           units=units+excluded.units,
                           bytes_written=bytes_written+excluded.bytes_written""",
                    (*key, len(values[0]), values[1], values[2]),
                )
            from .eval_log import archive_candidates
            archive_candidates(conn, ids)
            unit_ids = [row[0] for row in conn.execute(
                f"SELECT unit_id FROM ingestion_units WHERE candidate_id IN ({marks})", ids
            )]
            if unit_ids:
                unit_marks = ",".join("?" for _ in unit_ids)
                conn.execute(f"DELETE FROM ingestion_writes WHERE unit_id IN ({unit_marks})", unit_ids)
            conn.execute(f"DELETE FROM ingestion_inbox WHERE id IN ({marks})", ids)
            conn.execute("COMMIT")
            total += len(ids)
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    from .usage import collapse_old_usage
    collapse_old_usage(conn, now=current)
    from .eval_log import prune
    prune(conn, now=current)
    if vacuum:
        pages = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
        while pages > 0:
            conn.execute(f"PRAGMA incremental_vacuum({min(1000, pages)})")
            updated = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
            if updated >= pages:
                break
            pages = updated
        conn.execute(f"PRAGMA wal_checkpoint({'TRUNCATE' if truncate_wal else 'PASSIVE'})")
    return total


# A candidate is ready only when it has a unit that can run now (no head-of-line blocking
# behind a unit still backing off). A parked unit (missing_section) has
# no next_attempt_at: it waits for requeue_parked, so it is never ready -- counting it
# made the worker spin on a pass that does nothing and starved every lane.
_READY = """(state='queued' OR (state='partial_pending' AND EXISTS (
                 SELECT 1 FROM ingestion_units u WHERE u.candidate_id=ingestion_inbox.id
                 AND (u.state='queued' OR (u.state='deferred' AND u.next_attempt_at IS NOT NULL
                                           AND u.next_attempt_at<=?)))))"""


def _candidate_order(conn: sqlite3.Connection) -> str:
    """Put user-utterance/human-answer rows before the same batch's main rows."""
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(ingestion_inbox)")}
    if "extraction_pass" not in columns:
        return "created_at, id"
    return ("CASE extraction_pass WHEN 'user_utterance' THEN 0 "
            "WHEN 'human_answer' THEN 0 WHEN 's1' THEN 1 ELSE 2 END, created_at, id")


class IngestionWorker:
    """Serial owner worker with a 60-second polling fallback."""

    def __init__(
        self,
        owner: Owner,
        workspace_root: Path,
    ) -> None:
        self.owner = owner
        self.workspace_root = workspace_root
        self.stop_event = asyncio.Event()
        self.on_ready: Any = None        # called once startup recovery is done
        self.wake_event = asyncio.Event()
        self.scheduler = WeightedLaneScheduler()
        self._maintenance_due = _utcnow()
        self._summary_due = _utcnow()
        from .summary_lane import SummaryLane
        self._summary_lane = SummaryLane(workspace_root)

    async def run(self) -> None:
        """Recover, clean, then process lanes until stopped."""
        from .wake import install_owner_event, uninstall_owner_event

        conn = connect_rw()
        install_owner_event(self.wake_event, asyncio.get_running_loop())
        try:
            self.owner.adopt(conn)
            try:
                self._maintain(conn, startup=True)
            except Exception:  # start anyway; maintenance is retried in a day
                logger.exception("CDF startup maintenance failed")
            finally:
                if self.on_ready is not None:
                    self.on_ready()       # the run lane starts once recovery is done
            self._maintenance_due = _utcnow() + timedelta(days=1)
            while not self.stop_event.is_set():
                try:
                    worked = self.run_once(conn)
                except Exception:
                    # one bad pass must not end the worker for the rest of the process
                    logger.exception("CDF worker pass failed; retrying in %ss", LOOP_BACKOFF_SECONDS)
                    if conn.in_transaction:
                        conn.rollback()
                    worked = False
                    await self._sleep(LOOP_BACKOFF_SECONDS)
                    continue
                if worked:
                    await asyncio.sleep(0)
                    continue
                await self._sleep(60)
        finally:
            uninstall_owner_event()
            conn.close()

    async def _sleep(self, seconds: float) -> None:
        self.wake_event.clear()
        if self.stop_event.is_set():
            return          # a stop asked during the pass: its wake was just cleared
        try:
            await asyncio.wait_for(self.wake_event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    def _maintain(self, conn: sqlite3.Connection, *, startup: bool = False) -> None:
        """Recovery and housekeeping: resume write plans, recompute stranded
        candidates, clean up, requeue parked units whose domain or section now exists
        (and, at startup, units an error poisoned: the code may have changed), close the gaps a written run event declared and a concurrent edit left open.

        At startup the run lane has not started, so every unit between its claim and
        its plan is stranded; the daily pass leaves alone a unit that moved in the last
        ``REQUEUE_GRACE`` (the run lane may be writing it)."""
        from .writer import recover
        recover(conn, requeue_grace=None if startup else REQUEUE_GRACE)
        reaggregate_candidates(conn)
        cleanup_expired(conn, truncate_wal=startup)
        from .pipeline_v2 import requeue_error_poisons, requeue_missing_sections
        requeue_missing_sections(self.workspace_root)
        if startup:
            requeue_error_poisons(conn)
        _close_declared_gaps(conn)
        _apply_overrides(conn)
        _withdraw_ticked(self.workspace_root)

    def run_once(self, conn: sqlite3.Connection) -> bool:
        """Process one eligible candidate selected by lane policy."""
        now = _utcnow()
        # The summary lane never starves the other lanes: refused, failed or idle, the
        # rest of this pass still runs.
        summary_lane = "summary"  # frozen v1 Lane typing does not include v2 yet
        if now >= self._summary_due and ingestion_admission(summary_lane).run:  # type: ignore[arg-type]
            try:
                worked = self._summary_lane.run_due(conn, now=now)
            except Exception as exc:
                logger.warning("CDF summary lane failed; retrying: %s", exc)
                self._summary_due = now + timedelta(minutes=5)
                worked = False
            else:
                self._summary_due = now + timedelta(days=1)
            if worked:
                return True
        if now >= self._maintenance_due:
            self._maintenance_due = now + timedelta(days=1)
            self._maintain(conn)
            return True
        rows = conn.execute(
            f"""SELECT lane, MIN(created_at) oldest FROM ingestion_inbox
               WHERE {_READY} GROUP BY lane""", (now.isoformat(),)
        ).fetchall()
        available = [row["lane"] for row in rows if row["lane"] != "run"]
        oldest = {row["lane"]: datetime.fromisoformat(row["oldest"]) for row in rows}
        lane = self.scheduler.choose(available, oldest, now)
        if lane is None or not ingestion_admission(lane).run:
            return False
        order = _candidate_order(conn)
        row = conn.execute(
            f"""SELECT * FROM ingestion_inbox WHERE lane=? AND {_READY}
               ORDER BY {order} LIMIT 1""",
            (lane, now.isoformat()),
        ).fetchone()
        if row is None:
            return False
        process_candidate(conn, self.owner, row, self.workspace_root)
        return True

    def stop(self) -> None:
        """Request a clean stop from any thread."""
        self.stop_event.set()
        self.wake_event.set()


class RunLaneExecutor:
    """Dedicated run-lane thread: concurrency one, ten-minute drain budget."""

    MAX_RUN_SECONDS = 10 * 60

    def __init__(self, owner: Owner, workspace_root: Path) -> None:
        self.owner = owner
        self.workspace_root = workspace_root
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="cdf-run-lane", daemon=True)
        self._start_lock = threading.Lock()

    def start(self) -> None:
        with self._start_lock:       # never started after stop_and_join returned
            if not self._stop.is_set():
                self._thread.start()
        self._wake.set()

    def wake(self) -> None:
        """Wake after S3 enqueue; landing never waits for this thread."""
        self._wake.set()

    def _run(self) -> None:
        conn = connect_rw()
        try:
            self.owner.adopt(conn)
            while not self._stop.is_set():
                self._wake.wait(60)
                self._wake.clear()
                try:
                    self._drain(conn)
                except Exception:
                    logger.exception("CDF run lane pass failed; retrying in %ss", LOOP_BACKOFF_SECONDS)
                    if conn.in_transaction:
                        conn.rollback()
                    self._stop.wait(LOOP_BACKOFF_SECONDS)
        finally:
            conn.close()

    def _drain(self, conn: sqlite3.Connection) -> int:
        deadline = time.monotonic() + self.MAX_RUN_SECONDS
        processed = 0
        while not self._stop.is_set() and time.monotonic() < deadline:
            order = _candidate_order(conn)
            row = conn.execute(
                f"""SELECT * FROM ingestion_inbox WHERE lane='run' AND {_READY}
                    ORDER BY {order} LIMIT 1""",
                (_utcnow().isoformat(),),
            ).fetchone()
            if row is None:
                break
            process_candidate(conn, self.owner, row, self.workspace_root)
            processed += 1
        return processed

    def stop_and_join(self, timeout: float = 30) -> None:
        with self._start_lock:
            self._stop.set()
        self._wake.set()
        if self._thread.ident is None:       # stopped before the worker started it
            return
        self._thread.join(timeout)
        if self._thread.is_alive():
            raise RuntimeError("CDF run-lane executor did not stop")


class ReviewPostponed(Exception):
    """The session review cannot fork its CLI now; the job waits, spending no attempt."""


def _can_fork_review() -> bool:
    """The memory check chat runs before spawning a CLI (``spawn_budget`` with the live
    session count): the review forks the same kind of process. Unlike chat, there is
    no exception for zero live tabs (that one exists so a person can always open a
    tab); a background review waits. A count that cannot be read (the router changing
    under this thread) also waits: the next look is minutes away."""
    from core.resource_monitor import resource_monitor
    alive = 0
    try:
        from core import session_registry
        router = session_registry.session_router
        if router:
            alive = router.alive_count
    except RuntimeError:          # the router's dict changed during the count
        return False
    except Exception:  # noqa: BLE001 - no registry: no concurrency penalty, as for chat
        logger.debug("CDF review: session registry unavailable", exc_info=True)
    return bool(resource_monitor.spawn_budget(alive_count=alive).can_spawn)


def run_session_job(conn: sqlite3.Connection, job: dict[str, Any], *,
                    runner: Any = None) -> dict[str, Any]:
    """One session job: the after-session review by the session's own agent (S7; design
    2026-10-30-cdf-agent-decides D3/D4). A session that cannot be resumed is reviewed from
    its record; there is no second extraction pass."""
    from . import s2_jobs
    from .sources import s7_review
    session_id = job["session_id"]
    if runner is None and not _can_fork_review():
        raise ReviewPostponed("not enough memory for the review's CLI")
    # the project of the repo the session worked in (s2_jobs.request): the DDD searched
    # for related entries and the fallback project, never a declaration
    snapshot = {"session_project": job["project"]} if job.get("project") else None
    outcome = s7_review.review_session(
        session_id, sdk_session_id=job.get("sdk_session_id"), watermark=job.get("reviewed_to"),
        project_snapshot=snapshot, runner=runner)
    if outcome["status"] == "reviewed":
        s2_jobs.set_watermark(conn, session_id, outcome["reviewed_to"])
    logger.info("CDF session job %s: %s", session_id, outcome)
    return outcome


class S2JobExecutor:
    """One thread that runs queued S2 extractions in order (design §12 r16.13).

    It only extracts and enqueues units; the scheduler thread judges and writes them.
    A job waits while the session lane's admission says the host is loaded.
    """

    POLL_SECONDS = 60

    def __init__(self, extract: Any = None) -> None:
        self._extract = extract
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="cdf-s2-jobs", daemon=True)

    def start(self) -> None:
        from . import s2_jobs
        s2_jobs.set_waker(self.wake)
        self._thread.start()
        self._wake.set()

    def wake(self) -> None:
        self._wake.set()

    def _run(self) -> None:
        from . import s2_jobs
        conn = connect_rw()
        try:
            self._recover(conn)
            while not self._stop.is_set():
                self._wake.wait(self.POLL_SECONDS)
                self._wake.clear()
                try:
                    self.drain(conn)
                except Exception:
                    logger.exception("CDF session job pass failed; retrying in %ss", LOOP_BACKOFF_SECONDS)
                    if conn.in_transaction:
                        conn.rollback()
                    self._stop.wait(LOOP_BACKOFF_SECONDS)
                    self._recover(conn)   # a job left running by the failure goes back to the queue
        finally:
            conn.close()

    @staticmethod
    def _recover(conn: sqlite3.Connection) -> None:
        """``s2_jobs.recover``; a failure (database still busy) is logged, never the
        end of the thread: the next pass recovers again. A job it failed for good closes
        its session's run record on a Hive (``on_session_job_end``, never raises)."""
        from . import s2_jobs
        failed: list = []
        try:
            s2_jobs.recover(conn, failed)
        except Exception:
            logger.exception("CDF session job recovery failed; retrying after the next pass")
            if conn.in_transaction:
                conn.rollback()
            return
        # a job a restart failed for good ends its session too (core/hive_records)
        from core import hive_records
        for sid in failed:
            hive_records.on_session_job_end(sid, "failed")

    def drain(self, conn: sqlite3.Connection) -> int:
        from . import s2_jobs
        done = 0
        while not self._stop.is_set():
            if not ingestion_admission("session").run:
                break
            job = s2_jobs.claim(conn)
            if job is None:
                break
            try:
                if self._extract is not None:
                    self._extract(job["session_id"])
                else:
                    run_session_job(conn, job)
            except ReviewPostponed as exc:
                logger.info("CDF S2 job %s postponed: %s", job["session_id"], exc)
                try:
                    s2_jobs.postpone(conn, job["session_id"], s2_jobs.POSTPONE_DELAY)
                except Exception:  # noqa: BLE001 - recover would spend the attempt; next pass
                    logger.exception("CDF S2 job %s could not be postponed", job["session_id"])
                    break
                continue
            except Exception as exc:  # recorded on the job, retried up to MAX_ATTEMPTS
                logger.warning("CDF S2 job %s failed: %s", job["session_id"], exc, exc_info=True)
                state = s2_jobs.finish(conn, job["session_id"], error=f"{type(exc).__name__}: {exc}"[:500])
            else:
                state = s2_jobs.finish(conn, job["session_id"])
            # on a Hive, a job that ended (done, or failed for good) seals the session's
            # lessons and ``closed`` into its run record (core/hive_records)
            from core import hive_records
            hive_records.on_session_job_end(job["session_id"], state)
            done += 1
        return done

    def stop_and_join(self, timeout: float = 30) -> None:
        from . import s2_jobs
        self._stop.set()
        self._wake.set()
        s2_jobs.set_waker(None)
        self._thread.join(timeout)


class WorkerHandle:
    """Thread owner that joins the worker before releasing its owner lock.

    On a worker Hive (core/app_role) only the extraction runs: the S2/S7 session jobs,
    plus a daily expiry of old inbox rows. The judge and writer (the scheduler lane, the
    run lane, the summary lane) never start, so the Hive writes none of its own
    cognition; its lessons stay in the inbox for the host (design
    2026-10-29-hive-run-record-and-worker-role §3.2)."""

    MAINTENANCE_S = 24 * 3600

    def __init__(self, owner: Owner, workspace_root: Path) -> None:
        self.owner = owner
        self.worker = IngestionWorker(owner, workspace_root)
        self.run_executor = RunLaneExecutor(owner, workspace_root)
        self.s2_executor = S2JobExecutor()
        self._thread = threading.Thread(target=self._run, name="cdf-ingestion", daemon=True)
        self._loop: asyncio.AbstractEventLoop | None = None
        self.extract_only = False
        self._maint_stop = threading.Event()
        self._maint = threading.Thread(target=self._maintain, name="cdf-worker-maintenance", daemon=True)

    def _maintain(self) -> None:
        """Worker role: expire old inbox rows at start and once a day (the job the
        scheduler lane does on a full instance)."""
        while True:
            try:
                conn = connect_rw()
                try:
                    removed = cleanup_expired(conn)
                finally:
                    conn.close()
                if removed:
                    logger.info("CDF worker maintenance: %d expired candidates removed", removed)
            except Exception:  # noqa: BLE001 — tried again on the next tick
                logger.warning("CDF worker maintenance failed", exc_info=True)
            if self._maint_stop.wait(self.MAINTENANCE_S):
                return

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        try:
            self._loop.run_until_complete(self.worker.run())
        finally:
            self._loop.close()

    def start(self) -> None:
        """Start the scheduler and the session-job threads; the run lane starts when
        the scheduler's startup recovery is done (it requeues units the run lane
        could otherwise be writing at the same time). On a worker Hive, the
        extraction and its maintenance only."""
        from core.app_role import is_worker
        self.extract_only = is_worker()
        if self.extract_only:
            self.s2_executor.start()
            try:
                self._maint.start()
            except BaseException:
                self.s2_executor.stop_and_join(10)
                raise
            return
        self.worker.on_ready = self.run_executor.start
        self._thread.start()
        self.s2_executor.start()

    def wake_run(self) -> None:
        """Notify the dedicated executor after an S3 report is enqueued."""
        self.run_executor.wake()

    def stop_and_join(self, timeout: float = 30) -> None:
        """Stop and join all worker activity, then release ownership."""
        if self.extract_only:
            self._maint_stop.set()
            self.s2_executor.stop_and_join(timeout)
            if self._maint.ident is not None:
                self._maint.join(timeout)
            if self._maint.is_alive():
                raise RuntimeError("CDF worker maintenance did not stop")
            self.owner.release()
            return
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self.worker.stop)
        self.run_executor.stop_and_join(timeout)
        # A running extraction finishes its current model call first; a job it leaves
        # running is put back in the queue by s2_jobs.recover at the next start.
        self.s2_executor.stop_and_join(timeout)
        self._thread.join(timeout)
        if self._thread.is_alive():
            raise RuntimeError("CDF worker did not stop")
        self.owner.release()
