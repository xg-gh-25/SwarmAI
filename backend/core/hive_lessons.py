"""A Hive run's lessons come back to the host's Brain (design
docs/design/2026-10-28-project-agent-flow.md §13, step 10).

A Hive is a full SwarmAI: its own CDF sources extract units from a dispatched session
(the after-session review S7, the session pass S2) and from the pipeline runs that
session created (the REFLECT report S3), and queue them in the Hive's ingestion inbox.
A worker Hive (core/app_role) runs no judge of its own, so they stay there until the
host takes them; a run's record (core/hive_records) seals them.

**Hive side — ``collect(session_id)``** (served at ``GET /api/hive/lessons/{session}``,
routers/hive_brains): the cognition and gap units of that session's main passes, and the
units of every S3 run whose contract names the session as its creator, oldest first, all
of them, whatever their size (a cap of 50 units and a skip of any unit over 8 KB lost
lessons; the record seals them in as many entries as they need). A read-only connection
to the inbox; nothing on the Hive changes. ``exclude`` leaves out units already sealed.

**Host side.** The units come back in the run's record (core/hive_records), or, from a
Hive that keeps none, through ``take`` (``GET /api/hive/lessons/{session}``), and are kept
on this machine. ``settle`` queues every one this Brain takes as soon as it is here
(design 2026-10-30-cdf-agent-decides D13: no person is asked to classify knowledge),
through the same CDF path as a local unit (``ingest`` → ``api_v2.enqueue_unit``), so the
same provenance rule, routing and archive of rejected units apply, and the host stays the
only writer of a Brain; a store that is busy or failing leaves them for the next check:

- only kinds ``cognition`` and ``gap``, and only scope ``project:<the run's Brain>`` or
  ``world`` (for the General Brain, ``world`` only). Everything else is skipped.
- the Brain is fixed in code, not by the judge: source ``S6``, source_ref
  ``hive:<dispatch>`` (``contracts_v2.HIVE_REF_PREFIX``), ``project_hint`` = the Brain (none
  for General), and pipeline_v2 rejects (``project_outside_brain``) a Hive unit whose
  judged project is anything else.
- the Hive's evidence anchors name Hive-local records, so they are replaced by one
  evidence root this machine registers for the unit itself: kind ``agent_document``
  (with no parents its trust is always ``external``), locator ``hive:<dispatch>:<digest>``.
  It cites external content and no user message of this machine, so the provenance
  rule (pipeline_v2._admitted) keeps it out of every per-turn injected store; it reaches its
  Brain's DDD, marked external. ``relation_hint`` is dropped (its target is a Hive anchor).
- every unit of the run is taken, however many; ``INGEST_BUDGET_S`` of queueing per call
  (the rest waits for the next check); a unit is validated before any row is written
  for it. A unit
  settled for the run (taken, or refused by this machine's contract) is not sent again;
  a busy or failing store leaves it pending.

The record (``lessons`` on the run's results record) keeps the candidate ids and the
settled digests; ``states`` reads what the judge decided. /runs gets counts only, no
Hive text.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# the HTTP body read from an older Hive that keeps no record (``take``): it caps itself
# at 50 units of 8 KB, so this is a transport guard, never what is kept
MAX_BODY_BYTES = 512 * 1024
INGEST_BUDGET_S = 20.0      # one check's queueing; the rest waits for the next check
SOURCES = ("S2", "S3", "S7")
KINDS = ("cognition", "gap")
MAX_RUNS = 500              # newest S3 runs whose contracts are read per request
# enqueue_unit refusals that are about the unit itself (retrying cannot change them);
# any other refusal is an infrastructure failure and the unit is tried again
_CONTRACT_REFUSALS = ("credentialed_url", "unsafe", "missing_evidence", "untrusted_pass_root",
                      "invalid_unit")


# ── Hive side ──────────────────────────────────────────────────────────────────

def _audit_runs() -> Path:
    from config import get_app_data_dir
    return Path(get_app_data_dir()) / "state" / "pipeline_agent_audit" / "runs"


def _created_by(run_id: str, session_id: str) -> bool:
    from core.run_protocol.identifiers import _RUN_ID
    if not _RUN_ID.fullmatch(run_id or ""):
        return False
    path = _audit_runs() / run_id / "contract.json"
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1 << 20:
            return False
        contract = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(contract, dict) and contract.get("session_id") == session_id


def collect(session_id: str, exclude: Optional[set] = None) -> dict[str, Any]:
    """Every lesson the Hive's sources extracted for one dispatched session, oldest
    first, each with its inbox ``id``; ``exclude``: inbox ids to leave out (already
    sealed into the run's record). ``truncated`` is always False (kept for the record's
    readers)."""
    from core.hive_bundle import SESSION_RE
    from core.ingestion import standing
    if not SESSION_RE.fullmatch(session_id or ""):
        raise ValueError("bad session")
    conn = standing.connect()
    if conn is None:
        return {"lessons": [], "truncated": False}
    try:
        owned = [r for r in standing.recent_run_refs(conn, MAX_RUNS) if _created_by(r, session_id)]
        rows = standing.lesson_rows(conn, KINDS, session_id, owned)
    except Exception as exc:  # noqa: BLE001 — an older Hive schema: nothing to give
        logger.warning("hive_lessons: inbox unreadable (%s)", type(exc).__name__)
        rows = []
    finally:
        conn.close()
    out: list[dict[str, Any]] = []
    skip = exclude or set()
    for row_id, source, payload in rows:
        if row_id in skip or not isinstance(payload, str):
            continue
        try:
            unit = json.loads(payload)
        except ValueError:
            continue
        if isinstance(unit, dict):
            out.append({"id": row_id, "source": source, "unit": unit})
    return {"lessons": out, "truncated": False}


# ── host side ──────────────────────────────────────────────────────────────────

def _scopes_for(brain: str) -> frozenset[str]:
    from core.project_agent import GENERAL_BRAIN
    return frozenset({"world"} if brain == GENERAL_BRAIN else {"world", f"project:{brain}"})


def _digest(unit: dict) -> str:
    body = {k: v for k, v in unit.items() if k not in ("evidence", "relation_hint")}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def ingest(dispatch_id: str, brain: str, lessons: list[Any], done: set[str]) -> dict[str, Any]:
    """Queue the Hive's lessons in this machine's CDF, as many as INGEST_BUDGET_S allows
    in this call (the rest waits for the next check). Never raises for one unit.

    ``done`` = digests already settled for this run (taken, or refused for good); they
    are not sent again. Returns ``candidates`` (queued or already known to the CDF),
    ``taken`` and ``refused`` (new settled digests), ``skipped`` (another kind or
    scope) and ``unreadable`` (items with no unit)."""
    import time

    from core.ingestion import api_v2, contracts_v2
    from core.project_agent import GENERAL_BRAIN
    out: dict[str, Any] = {"candidates": [], "taken": [], "refused": [], "skipped": 0, "unreadable": 0}
    scopes = _scopes_for(brain)
    snapshot = {} if brain == GENERAL_BRAIN else {"explicit_project": brain}
    ref = f"{contracts_v2.HIVE_REF_PREFIX}{dispatch_id}"
    attempt = None
    deadline = time.monotonic() + INGEST_BUDGET_S
    known = set(done)
    for item in lessons:
        if time.monotonic() > deadline:
            break                   # this check's time: the rest waits for the next one
        unit = item.get("unit") if isinstance(item, dict) else None
        if not isinstance(unit, dict):
            out["unreadable"] += 1
            continue
        if unit.get("kind") not in KINDS or unit.get("scope") not in scopes:
            out["skipped"] += 1
            continue
        try:
            text = json.dumps(unit, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            size = len(text.encode("utf-8"))
            d = _digest(unit)
        except (TypeError, ValueError):     # incl. a lone surrogate anywhere in the unit
            out["unreadable"] += 1
            continue
        if d in known:
            continue
        raw = {k: v for k, v in unit.items() if k not in ("evidence", "relation_hint")}
        try:
            # checked before anything is written: an invalid unit leaves no row
            contracts_v2.SOURCE_UNIT.validate_python({**raw, "evidence": ["doc:pending"]})
        except ValueError:          # this machine's contract refuses the unit
            out["refused"].append(d)
            known.add(d)
            continue
        try:
            root = api_v2.register_root(
                "agent_document", f"{ref}:{d[:24]}",
                json.dumps({"dispatch": dispatch_id, "brain": brain, "unit": raw},
                           sort_keys=True, ensure_ascii=False), "external")
            typed = contracts_v2.SOURCE_UNIT.validate_python({**raw, "evidence": [f"doc:{root}"]})
            if attempt is None:
                attempt = api_v2.ExtractionAttempt(
                    source="S6", evidence_range={"external": ref},
                    extractor_version="hive-lessons-1", attempt_id=f"attempt:{uuid.uuid4().hex}")
            res = api_v2.enqueue_unit(
                "S6", ref, typed, session_id=None, run_id=None, read_root_ids=(root,),
                project_snapshot=snapshot, attempt_id=attempt.attempt_id)
        except Exception as exc:  # noqa: BLE001 — the CDF store: not settled, tried next check
            logger.warning("hive_lessons: a unit of %s not queued (%s)", dispatch_id, type(exc).__name__)
            continue
        status, reason = getattr(res, "status", None), str(getattr(res, "detail", "") or "")
        if status in ("queued", "duplicate") and getattr(res, "candidate_id", None):
            out["candidates"].append(res.candidate_id)
            out["taken"].append(d)
        elif status == "id_collision" or (status == "rejected" and reason.startswith(_CONTRACT_REFUSALS)):
            out["refused"].append(d)
        else:
            continue                # busy, or the store failed: not settled, tried again
        known.add(d)
    if attempt is not None:
        queued = len(out["candidates"])
        api_v2.record_extraction_attempt(attempt, status="ok" if queued else "no_unit",
                                         units_emitted=queued)
    return out


def _strs(v: Any) -> list[str]:
    return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []


def _n(v: Any) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else 0


async def _get_body(client: Any, path: str) -> tuple[Optional[int], Any]:
    """(status, parsed body) of a GET, never reading more than MAX_BODY_BYTES."""
    async with client.stream("GET", path) as r:
        if r.status_code != 200:
            return r.status_code, None
        length = r.headers.get("content-length")
        if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
            return None, None
        chunks: list[bytes] = []
        size = 0
        async for chunk in r.aiter_bytes():
            size += len(chunk)
            if size > MAX_BODY_BYTES:
                return None, None
            chunks.append(chunk)
    return 200, json.loads(b"".join(chunks))


def _agent_of(row: dict) -> Optional[tuple[str, str]]:
    """(dispatch id, Brain) of a run dispatched under a Project Agent, else None."""
    from core.swarm_out_results import DISPATCH_ID_RE
    agent = row.get("project_agent") if isinstance(row.get("project_agent"), dict) else None
    brain = agent.get("brain") if agent else None
    did = row.get("dispatch_id")
    if not isinstance(brain, str) or not brain or not isinstance(did, str) or not DISPATCH_ID_RE.fullmatch(did):
        return None
    return did, brain


def _legacy_path(dispatch_id: str) -> Path:
    from core import hive_records
    return hive_records.local_dir(dispatch_id) / "legacy-lessons.json"


def available(dispatch_id: str) -> list[dict]:
    """Every lesson unit kept on this machine for a run: from its run record
    (core/hive_records), or, from a Hive that keeps none, what ``take`` stored."""
    from core import hive_records
    units = hive_records.lesson_units(dispatch_id)
    try:
        legacy = json.loads(_legacy_path(dispatch_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        legacy = None
    if isinstance(legacy, dict) and isinstance(legacy.get("lessons"), list):
        units += [x for x in legacy["lessons"] if isinstance(x, dict)]
    return units


def pending_state(dispatch_id: str, brain: str, prev: Optional[dict], *,
                  error: Optional[str] = None, truncated: Optional[bool] = None) -> dict:
    """The ``lessons`` part of a run's record: what is kept here (``returned``), what this
    Brain takes that is not queued yet (``pending``: a store that could not queue them),
    what it would not take (``skipped``), and what was queued (``candidates``)."""
    old = prev if isinstance(prev, dict) else {}
    rec = {"candidates": _strs(old.get("candidates")),
           "taken": _strs(old.get("taken")),
           "refused": _strs(old.get("refused")),
           "accepted_at": old.get("accepted_at") if isinstance(old.get("accepted_at"), str) else None,
           "truncated": old.get("truncated") is True if truncated is None else truncated,
           "error": error}
    waiting, skipped, seen = _waiting(dispatch_id, brain, set(rec["taken"]) | set(rec["refused"]))
    rec.update(returned=seen, pending=len(waiting), skipped=skipped,
               # the waiting units by host-made hashes, no Hive text
               digests=[_digest(w["unit"]) for w in waiting])
    return rec


def _waiting(dispatch_id: str, brain: str, settled: set) -> tuple[list[dict], int, int]:
    """(the kept units this Brain would take that are not settled, one per digest, in
    order; how many it would not take; how many distinct units are kept)."""
    scopes = _scopes_for(brain)
    waiting: list[dict] = []
    seen: set = set()
    skipped = unreadable = 0
    for item in available(dispatch_id):
        unit = item.get("unit")
        try:
            d = _digest(unit) if isinstance(unit, dict) else None
        except (TypeError, ValueError):
            d = None
        if d is None:
            unreadable += 1
        elif d in seen:
            continue                       # the same unit from the record and the old path
        else:
            seen.add(d)
        if d is None or unit.get("kind") not in KINDS or unit.get("scope") not in scopes:
            skipped += 1
            continue
        if d not in settled:
            waiting.append(item)
    return waiting, skipped, len(seen) + unreadable


def settle(dispatch_id: str, brain: str, prev: Optional[dict], *,
           truncated: Optional[bool] = None) -> dict:
    """Queue for this machine's CDF (``ingest``) every lesson kept here that this Brain
    takes and that is not settled yet; returns the new ``lessons`` part. A store that
    cannot queue now leaves them waiting (``error``: not_queued), and the next check
    queues them."""
    old = prev if isinstance(prev, dict) else {}
    rec = pending_state(dispatch_id, brain, old, error=None, truncated=truncated)
    if rec["pending"]:
        settled = set(rec["taken"]) | set(rec["refused"])
        waiting, _skipped, _seen = _waiting(dispatch_id, brain, settled)
        try:
            out = ingest(dispatch_id, brain, waiting, settled)
        except Exception as exc:  # noqa: BLE001 — the CDF store unavailable: they stay waiting
            logger.error("hive_lessons: queueing %s failed: %s", dispatch_id, type(exc).__name__)
            return pending_state(dispatch_id, brain, rec, error="not_queued")
        rec["candidates"] = list(dict.fromkeys(rec["candidates"] + out["candidates"]))
        rec["taken"] = rec["taken"] + out["taken"]
        rec["refused"] = rec["refused"] + out["refused"]
        rec["accepted_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return pending_state(dispatch_id, brain, rec, error=None)


def _keep_legacy(did: str, lessons: list[dict]) -> None:
    """Keep what an older Hive returned (``take``), replacing what was kept before."""
    path = _legacy_path(did)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"lessons": lessons}, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


async def take(row: dict, inst: dict, prev: Optional[dict]) -> Optional[dict]:
    """From a Hive that keeps no run records: fetch the run's lessons, keep them here and
    queue them (``settle``). The new ``lessons`` part, or None for a run not dispatched
    under a Project Agent."""
    from core import swarm_out
    got = _agent_of(row)
    sid = row.get("session_id")
    if got is None or not isinstance(sid, str):
        return None
    did, brain = got
    old = prev if isinstance(prev, dict) else {}
    try:
        async with swarm_out._client(inst) as client:
            status, body = await _get_body(client, f"/api/hive/lessons/{sid}")
    except swarm_out.SwarmOutError:
        return pending_state(did, brain, old, error="hive_unusable")
    except ValueError:
        return pending_state(did, brain, old, error="unexpected_body")
    except Exception as exc:  # noqa: BLE001 — slow or unreachable: keep what was kept
        logger.warning("hive_lessons: fetching %s failed (%s)", did, type(exc).__name__)
        return pending_state(did, brain, old, error="hive_silent")
    if status is None:
        return pending_state(did, brain, old, error="too_large")
    if status != 200:
        return pending_state(did, brain, old, error=(
            "not_supported" if status == 404 else "bad_session" if status == 400 else f"http_{status}"))
    if not isinstance(body, dict) or not isinstance(body.get("lessons"), list):
        return pending_state(did, brain, old, error="unexpected_body")
    lessons = [{"source": x.get("source"), "unit": x.get("unit")} for x in body["lessons"]
               if isinstance(x, dict)]
    await asyncio.to_thread(_keep_legacy, did, lessons)
    return await asyncio.to_thread(settle, did, brain, old, truncated=body.get("truncated") is True)


_DONE = ("written", "reinforced", "merged")
_REJECTED = ("rejected", "failed_poison", "expired_unprocessed")


def states(candidates: list[str]) -> dict[str, int]:
    """What the judge decided for the units queued from a run, one state per unit:
    written (any of its rows written), rejected (every row ended without a write),
    pending (not judged yet, or a row still open)."""
    out = {"written": 0, "rejected": 0, "pending": 0}
    ids = list(dict.fromkeys(_strs(candidates)))
    if not ids:
        return out
    from core.ingestion import standing
    conn = standing.connect()
    if conn is None:
        out["pending"] = len(ids)
        return out
    try:
        rows = standing.unit_states(conn, ids)
    except Exception as exc:  # noqa: BLE001 — unreadable: shown as not judged
        logger.warning("hive_lessons: lesson states unreadable (%s)", type(exc).__name__)
        rows = []
    finally:
        conn.close()
    by: dict[str, list[str]] = {}
    for cand, state in rows:
        by.setdefault(cand, []).append(state)
    for cand in ids:
        got = by.get(cand) or []
        if any(s in _DONE for s in got):
            out["written"] += 1
        elif got and all(s in _REJECTED for s in got):
            out["rejected"] += 1
        else:
            out["pending"] += 1
    return out
