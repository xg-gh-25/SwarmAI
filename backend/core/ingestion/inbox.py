"""Bounded-latency candidate enqueueing for CDF.

- ``enqueue`` validates source payloads and trust envelopes.
- Idempotency conflicts distinguish duplicates from ID collisions.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from . import contracts
from .api import EnqueueResult, SOURCE_LANES
from .normalize import resolve_project_candidates
from .store import connect_rw


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _result_id(source: str, source_ref: str, payload_schema: str, slot: str) -> tuple[str, str]:
    material = f"{source}|{source_ref}|{payload_schema}|{slot}"
    idem_key = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return f"cand-{idem_key[:24]}", idem_key


def enqueue(
    source: str,
    source_ref: str,
    slot: str,
    payload: dict[str, Any],
    *,
    envelope: dict[str, Any],
    project_snapshot: dict[str, Any] | None = None,
) -> EnqueueResult:
    """Validate and enqueue one candidate, retrying SQLite busy twice."""
    try:
        lane, expected_kind = SOURCE_LANES[source]
        typed_payload = contracts.SOURCE_PAYLOAD.validate_python(payload)
        typed_envelope = contracts.Envelope.model_validate(envelope)
        if typed_payload.kind != expected_kind:
            raise ValueError("payload kind does not match source")
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        return EnqueueResult("rejected", None, f"invalid payload: {exc}")

    snapshot = project_snapshot or {}
    payload_json = _canonical(typed_payload.model_dump(mode="json"))
    envelope_json = _canonical(typed_envelope.model_dump(mode="json"))
    snapshot_json = _canonical(snapshot)
    payload_schema = f"{typed_payload.kind}:v1"
    content_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    candidate_id, idem_key = _result_id(source, source_ref, payload_schema, slot)
    candidate = contracts.Candidate(
        id=candidate_id,
        source=source,
        payload=typed_payload,
        envelope=typed_envelope,
        project_snapshot=snapshot,
        cluster_id=source_ref,
    )
    projects_json = _canonical(list(resolve_project_candidates(candidate)))
    created_at = datetime.now(timezone.utc).isoformat()

    # One wall-clock budget for all attempts (§4.1: a source never waits more than
    # ~1.5 s): each attempt's SQLite busy timeout is what is left, capped at 500 ms.
    deadline = time.monotonic() + 1.5
    for attempt in range(3):
        conn: sqlite3.Connection | None = None
        remaining_ms = int((deadline - time.monotonic()) * 1000)
        if remaining_ms <= 0:
            return EnqueueResult("busy", None, "ingestion database busy")
        try:
            conn = connect_rw(busy_timeout_ms=min(500, remaining_ms))
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """INSERT INTO ingestion_inbox(
                       id, idem_key, source, source_ref, lane, payload, payload_schema,
                       content_hash, envelope, project_snapshot, project_candidates, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(idem_key) DO NOTHING RETURNING id""",
                (candidate_id, idem_key, source, source_ref, lane, payload_json, payload_schema,
                 content_hash, envelope_json, snapshot_json, projects_json, created_at),
            )
            inserted = cursor.fetchone()
            if inserted:
                conn.execute("COMMIT")
                from .wake import wake
                wake(lane)
                return EnqueueResult("queued", candidate_id)

            prior = conn.execute(
                "SELECT id, content_hash FROM ingestion_inbox WHERE idem_key=?", (idem_key,)
            ).fetchone()
            if prior is None:
                raise sqlite3.IntegrityError("idempotency conflict without row")
            if prior["content_hash"] == content_hash:
                conn.execute("COMMIT")
                return EnqueueResult("duplicate", prior["id"])
            detail = _canonical({
                "candidate_id": prior["id"], "idem_key": idem_key,
                "existing_hash": prior["content_hash"], "incoming_hash": content_hash,
            })
            conn.execute(
                "INSERT INTO ingestion_events(at, kind, detail) VALUES (?, 'id_collision', ?)",
                (created_at, detail),
            )
            conn.execute("COMMIT")
            return EnqueueResult("id_collision", prior["id"], "content hash differs")
        except sqlite3.OperationalError as exc:
            if conn is not None and conn.in_transaction:
                conn.execute("ROLLBACK")
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                return EnqueueResult("rejected", None, str(exc))
            if attempt == 2:
                return EnqueueResult("busy", None, "ingestion database busy")
            time.sleep(0.01)
        except (sqlite3.DatabaseError, OSError) as exc:
            if conn is not None and conn.in_transaction:
                conn.execute("ROLLBACK")
            return EnqueueResult("rejected", None, str(exc))
        finally:
            if conn is not None:
                conn.close()
    return EnqueueResult("busy", None, "ingestion database busy")
