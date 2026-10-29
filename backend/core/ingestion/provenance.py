"""Server-derived provenance and bounded v2 enqueueing for CDF.

- ``register_root`` stores immutable, content-addressed evidence roots.
- ``resolve_evidence`` validates anchor ownership and closes parent lineage.
- ``record_extraction_attempt`` records source coverage without failing hot paths.
- ``enqueue_unit`` validates and enqueues one v2 source unit within 1.5 seconds.
- ``ingest_link_status`` records deterministic link-check classifications.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import ValidationError

from core.memory_guard import MemoryGuard

from . import contracts_v2
from .api import EnqueueResult
from .api_v2 import EvidenceRoot, ExtractionAttempt, ResolvedEvidence
from .store import connect_rw

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(data: bytes | str) -> str:
    raw = data if isinstance(data, bytes) else data.encode("utf-8", errors="surrogatepass")
    return hashlib.sha256(raw).hexdigest()


def _root_id(kind: str, locator: str, content_hash: str) -> str:
    return _digest(f"{kind}|{locator}|{content_hash}")


def _canonical_url(locator: str) -> str:
    sanitized = contracts_v2.sanitize_source_url(locator)
    parts = urlsplit(sanitized)
    kept = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if key.lower() not in {"gclid", "fbclid"}]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"),
                       urlencode(kept), ""))


def _row_root(row: sqlite3.Row | Mapping[str, Any]) -> EvidenceRoot | None:
    try:
        parents = tuple(json.loads(row["parent_root_ids"]))
        root = EvidenceRoot(
            root_id=str(row["root_id"]), root_kind=row["root_kind"],
            locator=str(row["locator"]), content_hash=str(row["content_hash"]),
            trust=row["trust"], parent_root_ids=parents,
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, ValidationError):
        return None
    if root.root_id != _root_id(root.root_kind, root.locator, root.content_hash):
        return None
    return root


def register_root(kind: contracts_v2.EvidenceRootKind, locator: str, content: bytes | str,
                  trust: contracts_v2.RootTrust, *, parent_root_ids: tuple[str, ...] = (),
                  observed_at: str | None = None) -> str:
    """Idempotently register one immutable evidence root."""
    if kind not in contracts_v2.EvidenceRootKind.__args__:
        raise ValueError("invalid evidence root kind")
    if trust not in contracts_v2.ROOT_TRUST_ORDER:
        raise ValueError("invalid evidence trust")
    canonical_locator = _canonical_url(locator) if kind == "web_document" else str(locator)
    content_hash = _digest(content)
    parents = tuple(sorted(set(parent_root_ids)))
    conn = connect_rw()
    try:
        parent_rows: dict[str, EvidenceRoot] = {}
        if parents:
            marks = ",".join("?" for _ in parents)
            for row in conn.execute(
                    f"SELECT * FROM evidence_roots WHERE root_id IN ({marks})", parents):
                parsed = _row_root(row)
                if parsed is not None:
                    parent_rows[parsed.root_id] = parsed
        if kind == "agent_document":
            trust = (min((root.trust for root in parent_rows.values()),
                         key=contracts_v2.ROOT_TRUST_ORDER.__getitem__)
                     if len(parent_rows) == len(parents) and parents else "external")
        root_id = _root_id(kind, canonical_locator, content_hash)
        now = _now()
        parent_json = _canonical(list(parents))
        conn.execute(
            "INSERT INTO evidence_roots(root_id,root_kind,locator,content_hash,trust,"
            "parent_root_ids,observed_at,created_at) VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(root_id) DO NOTHING",
            (root_id, kind, canonical_locator, content_hash, trust, parent_json,
             observed_at or now, now),
        )
        existing = conn.execute("SELECT * FROM evidence_roots WHERE root_id=?", (root_id,)).fetchone()
        parsed = _row_root(existing) if existing is not None else None
        if parsed is None or parsed.trust != trust or parsed.parent_root_ids != parents:
            raise sqlite3.IntegrityError("evidence root conflicts with immutable provenance")
        return root_id
    finally:
        conn.close()




def register_corpus_root(locator: str, content_hash: str,
                         trust: contracts_v2.RootTrust, *,
                         conn: sqlite3.Connection | None = None) -> str:
    """Register one parsed baseline unit without re-reading or rewriting its text."""
    if trust not in contracts_v2.ROOT_TRUST_ORDER:
        raise ValueError("invalid evidence trust")
    if len(content_hash) != 64 or any(char not in "0123456789abcdef" for char in content_hash):
        raise ValueError("invalid corpus unit content hash")
    root_id = _root_id("corpus_unit", locator, content_hash)
    owned = conn is None
    connection = conn or connect_rw()
    try:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='evidence_roots'"
        ).fetchone()
        if table is None:
            return root_id
        now = _now()
        connection.execute(
            "INSERT INTO evidence_roots(root_id,root_kind,locator,content_hash,trust,"
            "parent_root_ids,observed_at,created_at) VALUES(?,?,?,?,?,'[]',?,?) "
            "ON CONFLICT(root_id) DO NOTHING",
            (root_id, "corpus_unit", locator, content_hash, trust, now, now),
        )
        connection.commit()
        return root_id
    finally:
        if owned:
            connection.close()
def _lookup_anchor(conn: sqlite3.Connection, anchor: str, *, session_id: str | None,
                   run_id: str | None) -> EvidenceRoot | None:
    if anchor.startswith("event:"):
        event_id = anchor[len("event:"):]
        if not session_id:
            return None
        try:
            data_path = __import__("core.session_inputs", fromlist=["_db_path"])._db_path()
            uri = f"file:{data_path.resolve()}?mode=ro"
            with sqlite3.connect(uri, uri=True, timeout=1.0) as data:
                row = data.execute(
                    "SELECT content_hash,root_id FROM session_inputs "
                    "WHERE event_id=? AND session_id=?", (event_id, session_id)).fetchone()
        except (OSError, sqlite3.Error):
            return None
        if row is None or not row[1]:
            return None
        root_row = conn.execute("SELECT * FROM evidence_roots WHERE root_id=?", (row[1],)).fetchone()
        root = _row_root(root_row) if root_row is not None else None
        return root if root is not None and root.content_hash == row[0] else None
    if anchor.startswith("finding:"):
        rest = anchor[len("finding:"):]
        owner, separator, finding_id = rest.partition(":")
        if not separator or owner != run_id:
            return None
        row = conn.execute(
            "SELECT * FROM evidence_roots WHERE root_kind='run_finding' AND locator=?",
            (f"{owner}:{finding_id}",)).fetchone()
    elif anchor.startswith("run_event:"):
        rest = anchor[len("run_event:"):]
        owner, separator, event_id = rest.partition(":")
        if not separator or owner != run_id:
            return None
        row = conn.execute("SELECT * FROM evidence_roots WHERE locator=?",
                           (f"{owner}:{event_id}",)).fetchone()
    elif anchor.startswith("commit:"):
        sha = anchor[len("commit:"):]
        if run_id:
            row = conn.execute(
                "SELECT * FROM evidence_roots WHERE root_kind='commit' AND locator=?",
                (f"{run_id}:{sha}",)).fetchone()
        else:
            candidates = conn.execute(
                "SELECT * FROM evidence_roots WHERE root_kind='commit' AND locator LIKE 'repo:%' "
                "ORDER BY locator"
            ).fetchall()
            rows = [candidate for candidate in candidates
                    if str(candidate["locator"]).endswith(f":{sha}")]
            if len(rows) != 1:
                return None
            row = rows[0]
    elif anchor.startswith("unit:"):
        row = conn.execute(
            "SELECT * FROM evidence_roots WHERE root_kind='corpus_unit' AND locator=? "
            "ORDER BY created_at DESC, root_id DESC LIMIT 1",
            (anchor[len("unit:"):],)).fetchone()
    elif anchor.startswith("metric:"):
        window = anchor[len("metric:"):]
        locator = f"{run_id}:{window}" if run_id else window
        row = conn.execute(
            "SELECT * FROM evidence_roots WHERE root_kind='metric_window' AND locator=?",
            (locator,)).fetchone()
    elif anchor.startswith(("doc:", "web:")):
        prefix = "doc:" if anchor.startswith("doc:") else "web:"
        row = conn.execute("SELECT * FROM evidence_roots WHERE root_id=?",
                           (anchor[len(prefix):],)).fetchone()
        if row is not None:
            expected = "web_document" if prefix == "web:" else None
            if expected is not None and row["root_kind"] != expected:
                return None
            if row["root_kind"] == "agent_document" and ":" in str(row["locator"]):
                owner = str(row["locator"]).partition(":")[0]
                if owner.startswith("run_") and owner != run_id:
                    return None
                if not owner.startswith("run_") and session_id is not None \
                        and owner not in {session_id, "workspace", "repo"}:
                    return None
    else:
        return None
    return _row_root(row) if row is not None else None


def _closure(conn: sqlite3.Connection, roots: tuple[EvidenceRoot, ...]) -> tuple[set[str], bool]:
    lineage: set[str] = set()
    pending = [root.root_id for root in roots]
    complete = True
    while pending:
        root_id = pending.pop()
        if root_id in lineage:
            continue
        row = conn.execute("SELECT * FROM evidence_roots WHERE root_id=?", (root_id,)).fetchone()
        root = _row_root(row) if row is not None else None
        if root is None:
            complete = False
            continue
        lineage.add(root_id)
        pending.extend(root.parent_root_ids)
    return lineage, complete


def resolve_evidence(anchors: list[str], *, session_id: str | None, run_id: str | None,
                     read_root_ids: tuple[str, ...] = ()) -> ResolvedEvidence:
    """Resolve owned anchors, close their lineage, and derive strength and taint."""
    conn = connect_rw()
    try:
        roots: list[EvidenceRoot] = []
        unresolved: list[str] = []
        for anchor in anchors:
            root = _lookup_anchor(conn, anchor, session_id=session_id, run_id=run_id)
            if root is None:
                unresolved.append(anchor)
            else:
                roots.append(root)
        direct = tuple(dict.fromkeys(root.root_id for root in roots))
        lineage, complete = _closure(conn, tuple(roots))
        if not complete:
            unresolved.extend(anchor for anchor in anchors if anchor not in unresolved)
        read_rows: list[EvidenceRoot] = []
        for root_id in dict.fromkeys(read_root_ids):
            row = conn.execute("SELECT * FROM evidence_roots WHERE root_id=?", (root_id,)).fetchone()
            root = _row_root(row) if row is not None else None
            if root is not None:
                read_rows.append(root)
        read_lineage, read_parents_complete = _closure(conn, tuple(read_rows))
        read_complete = read_parents_complete and len(read_rows) == len(set(read_root_ids))
        all_trusts = {
            str(row[0]) for root_id in lineage
            if (row := conn.execute("SELECT trust FROM evidence_roots WHERE root_id=?",
                                    (root_id,)).fetchone()) is not None
        }
        if lineage and all_trusts == {"user"}:
            strength = "user_explicit"
        elif lineage and all_trusts <= {"user", "measured"}:
            strength = "measured"
        else:
            strength = "run_lesson" if run_id else "inferred"
        tainted_ids = lineage | read_lineage
        tainted = not read_complete or any(
            conn.execute("SELECT trust FROM evidence_roots WHERE root_id=?", (root_id,)).fetchone()[0]
            == "external" for root_id in tainted_ids
        )
        return ResolvedEvidence(
            root_ids=direct, lineage_root_ids=tuple(sorted(lineage)), strength=strength,
            tainted=tainted, unresolved=tuple(dict.fromkeys(unresolved)),
        )
    finally:
        conn.close()


def record_extraction_attempt(attempt: ExtractionAttempt, *, status: str, units_emitted: int = 0,
                              invalid_units: list[dict] | None = None, splits: int = 0,
                              error: str | None = None) -> str:
    """Insert or finish an extraction attempt; DB errors never reach a source."""
    if status not in {"running", "ok", "no_unit", "failed"}:
        return ""
    attempt_id = attempt.attempt_id or f"extract-{uuid.uuid4().hex}"
    try:
        conn = connect_rw(busy_timeout_ms=500)
        try:
            now = _now()
            existing = conn.execute(
                "SELECT invalid_units FROM extraction_attempts WHERE attempt_id=?", (attempt_id,)
            ).fetchone()
            prior_invalid = json.loads(existing[0]) if existing is not None else []
            combined_invalid = [*prior_invalid, *(invalid_units or [])]
            conn.execute(
                "INSERT INTO extraction_attempts(attempt_id,source,evidence_range,extractor_version,"
                "started_at,finished_at,units_emitted,units_invalid,splits,invalid_units,status,error) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(attempt_id) DO UPDATE SET "
                "finished_at=excluded.finished_at,units_emitted=excluded.units_emitted,"
                "units_invalid=excluded.units_invalid,splits=excluded.splits,"
                "invalid_units=excluded.invalid_units,status=excluded.status,error=excluded.error",
                (attempt_id, attempt.source, _canonical(attempt.evidence_range),
                 attempt.extractor_version, now, None if status == "running" else now,
                 units_emitted, len(combined_invalid), splits,
                 _canonical(combined_invalid), status, error),
            )
            return attempt_id
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        logger.warning("failed to record CDF extraction attempt", exc_info=True)
        return ""


def _record_invalid(attempt_id: str, raw: Any, reason: str) -> None:
    if not attempt_id:
        return
    try:
        conn = connect_rw(busy_timeout_ms=500)
        try:
            row = conn.execute(
                "SELECT invalid_units FROM extraction_attempts WHERE attempt_id=?", (attempt_id,)
            ).fetchone()
            if row is None:
                return
            values = json.loads(row[0])
            values.append({"raw": raw, "reason": reason, "status": "rejected_at_source"})
            conn.execute(
                "UPDATE extraction_attempts SET units_invalid=units_invalid+1,invalid_units=? "
                "WHERE attempt_id=?", (_canonical(values), attempt_id))
        finally:
            conn.close()
    except (OSError, sqlite3.Error, ValueError):
        logger.warning("failed to append invalid CDF unit", exc_info=True)


def _semantic_claim(unit: Any) -> str:
    values = {}
    for field in contracts_v2.SEMANTIC_FIELDS[unit.kind]:
        value = getattr(unit, field, None)
        values[field] = " ".join(value.split()) if isinstance(value, str) else value
    return _digest(_canonical(values))


def _owned_root(root: EvidenceRoot, session_id: str | None, run_id: str | None) -> bool:
    if root.root_kind == "user_message":
        return bool(session_id and root.locator.startswith(f"{session_id}:"))
    if root.root_kind == "human_answer":
        return bool(run_id and root.locator.startswith(f"{run_id}:"))
    return False


def _reject(attempt_id: str, raw: Any, reason: str) -> EnqueueResult:
    _record_invalid(attempt_id, raw, reason)
    return EnqueueResult("rejected", None, reason)


def enqueue_unit(source: contracts_v2.SourceId, source_ref: str, unit: contracts_v2.SourceUnit, *,
                 session_id: str | None, run_id: str | None,
                 read_root_ids: tuple[str, ...] = (),
                 project_snapshot: dict[str, Any] | None = None, attempt_id: str = "",
                 extraction_pass: contracts_v2.ExtractionPass = "main") -> EnqueueResult:
    """Validate provenance and enqueue one v2 unit with separate idem/dedup keys."""
    raw = unit.model_dump(mode="json") if hasattr(unit, "model_dump") else dict(unit)
    try:
        if raw.get("kind") == "reference" and raw.get("source_url"):
            raw["source_url"] = contracts_v2.sanitize_source_url(raw["source_url"])
        if contracts_v2.semantic_urls_have_credentials(raw.get("summary"), raw.get("body")):
            return _reject(attempt_id, raw, "credentialed_url")
        typed = contracts_v2.SOURCE_UNIT.validate_python(raw)
    except (TypeError, ValueError, ValidationError) as exc:
        reason = "credentialed_url" if "credentialed_url" in str(exc) else f"invalid_unit: {exc}"
        return _reject(attempt_id, raw, reason)

    scan = MemoryGuard().scan(_canonical(typed.model_dump(mode="json")))
    if scan.rejected or scan.sanitized_content != _canonical(typed.model_dump(mode="json")):
        return _reject(attempt_id, raw, "unsafe")
    resolved = resolve_evidence(
        list(typed.evidence), session_id=session_id, run_id=run_id,
        read_root_ids=read_root_ids,
    )
    if resolved.unresolved or not resolved.root_ids:
        return _reject(attempt_id, raw, "missing_evidence")

    conn = connect_rw()
    try:
        roots = tuple(
            root for root_id in resolved.root_ids
            if (row := conn.execute("SELECT * FROM evidence_roots WHERE root_id=?", (root_id,)).fetchone())
            is not None and (root := _row_root(row)) is not None
        )
    finally:
        conn.close()
    from .usage import record_many_async
    record_many_async(
        (root.locator for root in roots if root.root_kind == "corpus_unit"),
        "cited_as_evidence", session_id or run_id or "",
    )
    trusted_kinds = contracts_v2.TRUSTED_PASS_ROOT_KINDS.get(extraction_pass)
    if trusted_kinds is not None and (
            len(roots) != len(resolved.root_ids)
            or any(root.root_kind not in trusted_kinds or not _owned_root(
                root, session_id, run_id) for root in roots)):
        return _reject(attempt_id, raw, "untrusted_pass_root")

    claim_hash = _semantic_claim(typed)
    idem_key = _digest(f"{source}|{source_ref}|{extraction_pass}|{claim_hash}")
    dedup_key = _digest(
        f"{'|'.join(sorted(resolved.root_ids))}|{typed.kind}|{claim_hash}"
    )
    candidate_id = f"cand-{idem_key[:24]}"
    payload = typed.model_dump(mode="json")
    payload_json = _canonical(payload)
    evidence_json = _canonical(list(resolved.root_ids))
    now = _now()
    lane = contracts_v2.SOURCES_V2[source]["lane"]
    snapshot = project_snapshot or {}

    # SQLite waits about twice its busy timeout for one BEGIN IMMEDIATE on a WAL database
    # held by a writer (measured on 3.47: 500 ms -> 0.93 s), so each attempt gets half of
    # what is left: the 1.5 s budget is then the wall clock it promises.
    deadline = time.monotonic() + 1.5
    for attempt in range(3):
        conn = None
        remaining_ms = int((deadline - time.monotonic()) * 1000)
        if remaining_ms <= 0:
            return EnqueueResult("busy", None, "ingestion database busy")
        try:
            conn = connect_rw(busy_timeout_ms=min(500, remaining_ms) // 2)
            conn.execute("BEGIN IMMEDIATE")
            if source == "S1":
                if not session_id or len(roots) != 1 or roots[0].root_kind != "user_message":
                    conn.execute("ROLLBACK")
                    return _reject(attempt_id, raw, "s1_stale_evidence")
                data_path = __import__("core.session_inputs", fromlist=["_db_path"])._db_path()
                uri = f"file:{data_path.resolve()}?mode=ro"
                with sqlite3.connect(uri, uri=True, timeout=1.0) as data:
                    # The newest TURN-OPENING input (a person's message, a channel peer's
                    # message, or a system-built prompt) must be this user message:
                    # a later system / peer turn makes an older user message stale (§9.2).
                    latest = data.execute(
                        "SELECT root_id FROM session_inputs WHERE session_id=? AND kind IN "
                        "('user_message','channel_message','system_prompt') "
                        "ORDER BY created_at DESC,event_id DESC LIMIT 1", (session_id,)).fetchone()
                if latest is None or latest[0] != roots[0].root_id:
                    conn.execute("ROLLBACK")
                    return _reject(attempt_id, raw, "s1_stale_evidence")
                count = 0
                for row in conn.execute(
                        "SELECT evidence_root_ids FROM ingestion_inbox WHERE source_v2='S1'"):
                    if roots[0].root_id in json.loads(row[0] or "[]"):
                        count += 1
                if count >= contracts_v2.S1_MAX_UNITS_PER_MESSAGE:
                    conn.execute("ROLLBACK")
                    return _reject(attempt_id, raw, "s1_unit_cap")
            cursor = conn.execute(
                "INSERT INTO ingestion_inbox(id,idem_key,source,source_ref,lane,payload,"
                "payload_schema,content_hash,envelope,project_snapshot,project_candidates,"
                "created_at,unit_kind,source_v2,extraction_pass,evidence_root_ids,project_hint,dedup_key) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(idem_key) DO NOTHING RETURNING id",
                (candidate_id, idem_key, source, source_ref, lane, payload_json,
                 f"{typed.kind}:v2", _digest(payload_json),
                 _canonical({"strength": resolved.strength, "tainted": resolved.tainted,
                             "lineage_root_ids": list(resolved.lineage_root_ids)}),
                 _canonical(snapshot), _canonical([]), now, typed.kind, source,
                 extraction_pass, evidence_json, contracts_v2.snapshot_project(snapshot), dedup_key),
            )
            inserted = cursor.fetchone()
            if inserted:
                conn.execute("COMMIT")
                from .wake import wake
                wake(lane)
                return EnqueueResult("queued", candidate_id)
            prior = conn.execute(
                "SELECT id,content_hash FROM ingestion_inbox WHERE idem_key=?", (idem_key,)
            ).fetchone()
            conn.execute("COMMIT")
            if prior is not None and prior["content_hash"] == _digest(payload_json):
                return EnqueueResult("duplicate", prior["id"])
            return EnqueueResult("id_collision", prior["id"] if prior else None,
                                 "content hash differs")
        except sqlite3.OperationalError as exc:
            if conn is not None and conn.in_transaction:
                conn.execute("ROLLBACK")
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                return _reject(attempt_id, raw, str(exc))
            if attempt == 2:
                return EnqueueResult("busy", None, "ingestion database busy")
            time.sleep(0.01)
        except (OSError, sqlite3.DatabaseError) as exc:
            if conn is not None and conn.in_transaction:
                conn.execute("ROLLBACK")
            return _reject(attempt_id, raw, str(exc))
        finally:
            if conn is not None:
                conn.close()
    return EnqueueResult("busy", None, "ingestion database busy")


def ingest_link_status(url_root_id: str, status: str, *, http_status: int | None = None) -> None:
    """Record one link check without modifying the referenced document."""
    lowered = status.lower()
    if lowered == "unchecked":
        classified = "unchecked"
    elif lowered == "ok" and http_status is not None and 200 <= http_status < 400:
        classified = "ok"
    elif lowered in {"nxdomain", "dead"} and (lowered == "nxdomain" or http_status in {404, 410}):
        classified = "dead"
    else:
        classified = "unreachable"
    conn = connect_rw()
    try:
        root = conn.execute(
            "SELECT locator FROM evidence_roots WHERE root_id=? AND root_kind='web_document'",
            (url_root_id,),).fetchone()
        if root is None:
            raise ValueError("link status requires a web_document root")
        checked = None if classified == "unchecked" else _now()
        conn.execute(
            "INSERT INTO link_status(url_root_id,url,last_checked,status,http_status) VALUES(?,?,?,?,?) "
            "ON CONFLICT(url_root_id) DO UPDATE SET url=excluded.url,last_checked=excluded.last_checked,"
            "status=excluded.status,http_status=excluded.http_status",
            (url_root_id, root[0], checked, classified, http_status),
        )
    finally:
        conn.close()
