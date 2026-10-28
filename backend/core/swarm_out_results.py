"""What a dispatched Swarm Out run ended with, fetched from its Hive and kept here.

Design: docs/design/2026-10-28-swarm-out-run-results.md §4.2–§4.5. The Host reads the
run's session from the Hive's own messages API (``/api/chat/sessions/{id}/messages``,
the last ``FETCH_TURNS`` user turns, each whole, with the previous ETag), turns the
blocks the Hive persisted into one result (``summarize``) and keeps it in
``state/swarm-out/results/<dispatch_id>.json``. A Hive may be stopped or deleted
later; the kept file is then the only copy, so nothing here deletes one.

Everything read from a Hive is data: each field is checked for type and length and a
field that is not as expected is dropped and counted (``dropped``). ``public`` builds
the ``/runs`` copy, which the CLI prints and the local agent reads, from an allowlist
of enums, numbers, shas and accepted repository names: no text the Hive's agent wrote
gets in. The whole record is served to the panel (``/runs/{id}/result``).

- ``summarize(messages, has_more)`` — pure: the result of one run.
- ``load(dispatch_id)`` / ``save(record)`` — the kept record (atomic write).
- ``fetch(row)``   — check the Hive once (async; never raises).
- ``attach(rows, status_of)`` — add each row's kept result, start the checks due.
- ``public(record)`` — the record as ``/runs`` carries it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from core.turn_outcome import turn_costs

logger = logging.getLogger(__name__)

#: user turns read per check (the messages API's ``turns`` page, each turn whole)
FETCH_TURNS = 20
#: a run stopped this recently is checked on every poll (the panel's recent window)
RECENT_S = 24 * 3600
#: checks running at once, across all Hives
MAX_CONCURRENT_CHECKS = 2
REPORT_CHARS = 4000
MAX_DELIVERABLES = 50
MAX_TOOL_NAMES = 20
#: bumped whenever ``summarize`` changes what it derives: a kept result of another
#: version is fetched again in full (no ETag), so it is re-summarised instead of
#: staying as the older rules made it
SUMMARY_VERSION = 2

DISPATCH_ID_RE = re.compile(r"so_[0-9a-f]{12}")
_SHA_RE = re.compile(r"[0-9a-f]{7,64}")
_NAME_RE = re.compile(r"[A-Za-z0-9_.:-]{1,64}")

ENDED = ("completed", "turn_limit", "error", "crashed", "paused", "no_terminal", "stopped",
         "restarted", "superseded", "gave_up", "no_record")
_NOTICE_REASONS = ("restarted", "superseded", "gave_up")
_VERDICTS = ("PASS", "WARN", "BLOCK", "NA", "N/A", "PENDING")


# ── field checks ──────────────────────────────────────────────────────────────

class _Drops:
    def __init__(self) -> None:
        self.n = 0

    def s(self, v: Any, n: int) -> Optional[str]:
        if v is None:
            return None
        if not isinstance(v, str):
            self.n += 1
            return None
        return v[:n]

    def i(self, v: Any) -> Optional[int]:
        if v is None:
            return None
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            self.n += 1
            return None
        return v


def _blocks(row: Any) -> list[dict]:
    content = row.get("content") if isinstance(row, dict) else None
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _repo_fact(f: Any, d: _Drops) -> Optional[dict]:
    if not isinstance(f, dict):
        d.n += 1
        return None
    files = []
    for x in (f.get("files") or [])[:MAX_DELIVERABLES] if isinstance(f.get("files"), list) else []:
        if isinstance(x, dict) and isinstance(x.get("path"), str):
            files.append({"path": x["path"][:300], "added": d.i(x.get("added")),
                          "deleted": d.i(x.get("deleted"))})
    commits = [c for c in (f.get("commits") or []) if isinstance(c, str) and _SHA_RE.fullmatch(c)] \
        if isinstance(f.get("commits"), list) else []
    pushed = f.get("pushed") if f.get("pushed") in ("yes", "no", "nothing_to_push", "unknown") else "unknown"
    present = f.get("present") if isinstance(f.get("present"), bool) else None
    om = f.get("origin_matches") if isinstance(f.get("origin_matches"), bool) else None
    return {"url": d.s(f.get("url"), 300), "ref": d.s(f.get("ref"), 100), "dir": d.s(f.get("dir"), 300),
            "present": present, "origin_matches": om,
            "base": f["base"] if isinstance(f.get("base"), str) and _SHA_RE.fullmatch(f["base"]) else None,
            "head": f["head"] if isinstance(f.get("head"), str) and _SHA_RE.fullmatch(f["head"]) else None,
            "commits": commits[:20], "commits_total": d.i(f.get("commits_total")) or 0,
            "files": files, "files_total": d.i(f.get("files_total")) or 0,
            "added": d.i(f.get("added")) or 0, "deleted": d.i(f.get("deleted")) or 0,
            "untracked": d.i(f.get("untracked")) or 0, "pushed": pushed,
            "error": d.s(f.get("error"), 200)}


# ── summarize ─────────────────────────────────────────────────────────────────

def _is_turn_start(row: dict) -> bool:
    """A user row that starts a turn: not a steer, which the running turn reads
    mid-way (``metadata.steer`` / ``metadata.steered``, core/session_pending); the
    turn's one outcome may land after it."""
    if row.get("role") != "user":
        return False
    meta = row.get("metadata")
    return not (isinstance(meta, dict) and ("steered" in meta or "steer" in meta))


def summarize(messages: Any, has_more: bool = False) -> dict[str, Any]:
    """The result of one run from its session's rows (chronological, as the
    messages API returns them). Pure; never raises on malformed input."""
    d = _Drops()
    rows = [m for m in messages if isinstance(m, dict)] if isinstance(messages, list) else []
    last_user = max((i for i, r in enumerate(rows) if _is_turn_start(r)), default=-1)
    turns = sum(1 for r in rows if _is_turn_start(r))
    all_blocks = [b for r in rows if r.get("role") == "assistant" for b in _blocks(r)]
    after_rows = [r for r in rows[last_user + 1:] if r.get("role") == "assistant"]
    after = [b for r in after_rows for b in _blocks(r)]

    outcomes = [b for b in all_blocks if b.get("type") == "turn_outcome"]
    after_outcomes = [b for b in after if b.get("type") == "turn_outcome"]
    last_outcome = after_outcomes[-1] if after_outcomes else None
    stopped = any(b.get("type") == "turn_stop" for b in after)
    notices = [b.get("reason") for b in after if b.get("type") == "turn_notice"
               and b.get("reason") in _NOTICE_REASONS]

    ended_raw = last_outcome.get("ended") if last_outcome else None
    if stopped:
        ended = "stopped"
    elif ended_raw in ("completed", "turn_limit", "error", "crashed", "paused"):
        ended = ended_raw
    elif notices:
        ended = notices[-1]
    elif ended_raw == "none":
        ended = "no_terminal"        # recorded: the turn ended with no terminal event
    else:
        ended = "no_record"
    provisional = ended in ("paused", "no_record") and not stopped

    error = None
    if ended == "error" and last_outcome:
        error = {"code": d.s(last_outcome.get("code"), 64) or d.s(last_outcome.get("subtype"), 32),
                 "message": d.s(last_outcome.get("message"), 300)}
    elif ended == "crashed" and last_outcome:
        error = {"code": "crashed", "message": d.s(last_outcome.get("exception"), 64)}

    ended_at = d.s(last_outcome.get("at"), 40) if last_outcome else None
    if ended_at is None and rows:
        ended_at = d.s(rows[-1].get("created_at"), 40)

    active_ms = sum(d.i(o.get("duration_ms")) or 0 for o in outcomes)
    tokens_in = tokens_out = 0
    for o in outcomes:
        u = o.get("usage")
        if isinstance(u, dict):
            tokens_in += d.i(u.get("input_tokens")) or 0
            tokens_out += d.i(u.get("output_tokens")) or 0
    costs, missing = turn_costs(outcomes)
    known = [c for c in costs if c is not None]
    cost = round(sum(known), 6) if known else None
    # a user turn with no outcome of its own (killed, or a Hive before the record)
    # left its spend out; counted per turn, as continuations and injected turns add
    # outcomes without a user row
    per_turn: list[int] = []
    for r in rows:
        if _is_turn_start(r):
            per_turn.append(0)
        elif per_turn and r.get("role") == "assistant":
            per_turn[-1] += sum(1 for b in _blocks(r) if b.get("type") == "turn_outcome")
    cost_partial = bool(missing or has_more or not outcomes or any(n == 0 for n in per_turn))

    texts = [b.get("text") for b in after if b.get("type") == "text"
             and isinstance(b.get("text"), str) and b["text"].strip()]
    report_full = texts[-1] if texts else ""

    deliverables: list[dict] = []
    seen: set[str] = set()
    deliverables_omitted = 0
    for b in all_blocks:
        if b.get("type") != "turn_deliverables":
            continue
        deliverables_omitted += d.i(b.get("omitted")) or 0
        for f in b.get("files") or [] if isinstance(b.get("files"), list) else []:
            if not isinstance(f, dict) or not isinstance(f.get("path"), str) or f["path"] in seen:
                continue
            seen.add(f["path"])
            if len(deliverables) >= MAX_DELIVERABLES:
                deliverables_omitted += 1
                continue
            kind = f.get("kind") if f.get("kind") in ("content", "knowledge", "external-diff",
                                                       "external-nodiff") else "other"
            deliverables.append({"path": f["path"][:300], "kind": kind})

    bound: dict[str, dict] = {}            # turn_changes, by git root
    for b in all_blocks:
        if b.get("type") != "turn_changes":
            continue
        for f in b.get("files") or [] if isinstance(b.get("files"), list) else []:
            if not isinstance(f, dict):
                continue
            root = d.s(f.get("gitRoot"), 300) or "?"
            e = bound.setdefault(root, {"root": root, "paths": set(), "added": 0, "deleted": 0, "landed": []})
            if isinstance(f.get("path"), str):
                e["paths"].add(f["path"][:300])
            e["added"] += d.i(f.get("added")) or 0
            e["deleted"] += d.i(f.get("deleted")) or 0
        # a landing (core/session_landings._landing): its commits ``onto..new`` and its
        # files, which turn_changes lists under the landing instead of in ``files``
        for land in b.get("landed") or [] if isinstance(b.get("landed"), list) else []:
            if not isinstance(land, dict):
                continue
            root = d.s(land.get("gitRoot"), 300) or "?"
            e = bound.setdefault(root, {"root": root, "paths": set(), "added": 0, "deleted": 0, "landed": []})
            if isinstance(land.get("new"), str) and _SHA_RE.fullmatch(land["new"]):
                e["landed"].append(land["new"][:12])
            for f in land.get("files") or [] if isinstance(land.get("files"), list) else []:
                if isinstance(f, dict) and isinstance(f.get("path"), str):
                    e["paths"].add(f["path"][:300])
                    e["added"] += d.i(f.get("added")) or 0
                    e["deleted"] += d.i(f.get("deleted")) or 0
    changes = [{"root": e["root"], "repo": os.path.basename(e["root"]), "files": len(e["paths"]), "added": e["added"],
                "deleted": e["deleted"], "landed": e["landed"][:20]} for e in bound.values()]

    repos = None
    for o in reversed(outcomes):
        if isinstance(o.get("repos"), list):
            repos = [r for r in (_repo_fact(x, d) for x in o["repos"][:10]) if r]
            break

    tools: dict[str, int] = {}
    steps_total = failed = agents = 0
    for b in all_blocks:
        if b.get("type") == "tool_use":
            steps_total += 1
            name = b.get("name") if isinstance(b.get("name"), str) and _NAME_RE.fullmatch(b["name"]) else "other"
            tools[name] = tools.get(name, 0) + 1
            if name in ("Agent", "Task"):
                agents += 1
        elif b.get("type") == "tool_result" and b.get("is_error") is True:
            failed += 1
    top = dict(sorted(tools.items(), key=lambda kv: -kv[1])[:MAX_TOOL_NAMES])

    approved = denied = unanswered_perm = answered_q = unanswered_q = 0
    for b in all_blocks:
        if b.get("type") == "cmd_permission_request":
            dec = b.get("decision")
            if dec == "approve":
                approved += 1
            elif dec == "deny":
                denied += 1
            else:
                unanswered_perm += 1
        elif b.get("type") == "ask_user_question":
            if isinstance(b.get("answers"), dict) and b["answers"]:
                answered_q += 1
            else:
                unanswered_q += 1

    pipeline = None
    summaries = [b for b in all_blocks if b.get("type") == "pipeline_summary"]
    if summaries:
        s = summaries[-1]
        audit = []
        for r in s.get("audit") or [] if isinstance(s.get("audit"), list) else []:
            if isinstance(r, dict) and isinstance(r.get("dim"), str):
                v = r.get("verdict") if r.get("verdict") in _VERDICTS else None
                audit.append({"dim": r["dim"][:40], "verdict": v, "detail": d.s(r.get("detail"), 200)})
        landed = s.get("landedSha")
        pipeline = {"run_id": d.s(s.get("runId"), 64), "profile": d.s(s.get("profile"), 20),
                    "status": d.s(s.get("status"), 20), "landing": d.s(s.get("landing"), 20),
                    "landed_sha": landed[:12] if isinstance(landed, str) and _SHA_RE.fullmatch(landed) else None,
                    "audit": audit[:5]}

    # a file written inside a declared repository is that repository's change (its
    # line in ``repos``), not a document of its own
    repo_dirs = ["/" + r["dir"].strip("/") + "/" for r in repos or [] if isinstance(r.get("dir"), str) and r["dir"]]
    deliverables = [f for f in deliverables
                    if not any(rd in "/" + f["path"].lstrip("/") for rd in repo_dirs)]

    kinds = []
    if pipeline:
        kinds.append("pipeline")
    if changes or any((r.get("files_total") or r.get("commits_total")) for r in repos or []):
        kinds.append("code")
    if deliverables:
        kinds.append("files")
    if not kinds and report_full:
        kinds.append("answer")

    return {
        "ended": ended, "provisional": provisional, "outcome_recorded": bool(after_outcomes),
        # any turn of the session has an outcome: the Hive records them, so a missing one
        # means the turn did not reach its end (a killed process), not an old Hive
        "hive_records_outcomes": bool(outcomes),
        "error": error, "ended_at": ended_at, "turns": turns,
        "older_turns_not_read": bool(has_more),
        "active_s": round(active_ms / 1000) if outcomes else None,
        "tokens": {"input": tokens_in, "output": tokens_out} if outcomes else None,
        "cost_usd": cost, "cost_partial": cost_partial,
        "report": report_full[:REPORT_CHARS], "report_chars": len(report_full),
        "kinds": kinds,
        "work": {
            "repos": repos, "changes": changes,
            "files": deliverables, "files_omitted": deliverables_omitted,
            "steps": {"total": steps_total, "failed": failed, "agents": agents, "by_tool": top},
            "human": {"approved": approved, "denied": denied, "unanswered": unanswered_perm + unanswered_q,
                      "answered_questions": answered_q},
        },
        "pipeline": pipeline,
        "dropped": d.n,
    }


# ── the kept record ───────────────────────────────────────────────────────────

def _dir() -> Path:
    from core.swarm_out import _state_dir
    p = _state_dir() / "results"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _path(dispatch_id: str) -> Optional[Path]:
    return _dir() / f"{dispatch_id}.json" if DISPATCH_ID_RE.fullmatch(dispatch_id or "") else None


def load(dispatch_id: str) -> Optional[dict]:
    p = _path(dispatch_id)
    if p is None or not p.exists():
        return None
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("swarm_out_results: unreadable record %s", dispatch_id)
        return None
    return rec if isinstance(rec, dict) else None


def save(record: dict) -> None:
    """Write the record atomically (a crash leaves the old file or the new one)."""
    p = _path(record.get("dispatch_id") or "")
    if p is None:
        raise ValueError("record without a valid dispatch_id")
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── fetch ─────────────────────────────────────────────────────────────────────

_locks: dict[tuple[int, str], asyncio.Lock] = {}   # one check per run at a time, per loop


async def fetch(row: dict) -> dict:
    """Check the run's session on its Hive once and keep what came back; returns the
    kept record. Never raises: a failure is written as ``fetch_error`` and the
    previous result stays. Checks of one run (the background check, the card's
    refresh) run one after the other, so each starts from what the last one kept and
    an older answer never replaces a newer one."""
    did = row.get("dispatch_id") or ""
    loop_id = id(asyncio.get_running_loop())
    if any(k[0] != loop_id for k in _locks):   # a lock binds to one loop (as _sems)
        _locks.clear()
    key = (loop_id, did)
    lock = _locks.get(key)
    if lock is None:
        lock = _locks[key] = asyncio.Lock()
    async with lock:      # kept for the process (one small lock per dispatched run)
        return await _fetch(row)


async def _fetch(row: dict) -> dict:
    from core import swarm_out
    did, sid = row.get("dispatch_id") or "", row.get("session_id")
    prev = await asyncio.to_thread(load, did) or {}
    rec = {"dispatch_id": did, "hive_id": row.get("hive_id"), "session_id": sid,
           "etag": prev.get("etag"), "transcript_rev": prev.get("transcript_rev"),
           "fetched_at": prev.get("fetched_at"), "checked_at": _now(),
           "fetch_error": None, "result": prev.get("result"),
           "summary_version": prev.get("summary_version")}
    if not isinstance(sid, str) or not swarm_out.KEY_RE.fullmatch(sid):
        rec["fetch_error"] = "no session"
        return rec
    try:
        inst = await swarm_out._instance(row.get("hive_id") or "")
        current = prev.get("summary_version") == SUMMARY_VERSION
        headers = {"If-None-Match": rec["etag"]} if rec["etag"] and rec["result"] is not None and current else {}
        async with swarm_out._client(inst) as client:
            r = await client.get(f"/api/chat/sessions/{sid}/messages",
                                 params={"turns": FETCH_TURNS}, headers=headers)
        if r.status_code == 304:
            pass
        elif r.status_code == 404:
            rec["fetch_error"] = "session_gone"
        elif r.status_code != 200:
            rec["fetch_error"] = f"http_{r.status_code}"
        else:
            body = r.json()
            if not isinstance(body, list):
                rec["fetch_error"] = "unexpected_body"
            else:
                rec["result"] = summarize(body, r.headers.get("x-has-more") == "1")
                rec["summary_version"] = SUMMARY_VERSION
                etag = r.headers.get("etag")
                rec["etag"] = etag[:200] if isinstance(etag, str) else None
                rev = r.headers.get("x-transcript-rev")
                rec["transcript_rev"] = int(rev) if isinstance(rev, str) and rev.isdigit() else None
                rec["fetched_at"] = rec["checked_at"]
    except swarm_out.SwarmOutError as e:
        rec["fetch_error"] = "hive_not_running" if "not running" in str(e) else "hive_unusable"
    except Exception as e:  # noqa: BLE001 — Hive slow or unreachable: keep the old result
        logger.warning("swarm_out_results: checking %s failed (%s)", did, type(e).__name__)
        rec["fetch_error"] = "hive_silent"
    try:
        await asyncio.to_thread(save, rec)
    except Exception as e:  # noqa: BLE001 — the next check writes again
        logger.error("swarm_out_results: keeping %s failed: %s", did, e)
    return rec


# ── checks started from /runs ─────────────────────────────────────────────────

_inflight: set[str] = set()
_tasks: set[asyncio.Task] = set()
_sems: dict[int, asyncio.Semaphore] = {}     # per event loop (a semaphore binds to one)


async def _check(row: dict) -> None:
    loop_id = id(asyncio.get_running_loop())
    sem = _sems.get(loop_id)
    if sem is None:
        _sems.clear()
        sem = _sems[loop_id] = asyncio.Semaphore(MAX_CONCURRENT_CHECKS)
    async with sem:
        await fetch(row)


def _start_check(row: dict) -> bool:
    did = row.get("dispatch_id") or ""
    if did in _inflight or not DISPATCH_ID_RE.fullmatch(did):
        return False
    _inflight.add(did)
    task = asyncio.get_running_loop().create_task(_check(dict(row)))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    # in the done callback, not the coroutine: a task cancelled before it first runs
    # never runs its own finally
    task.add_done_callback(lambda _t, d=did: _inflight.discard(d))
    return True


def _age_s(sent_at: Any) -> Optional[float]:
    try:
        t = datetime.strptime(str(sent_at), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return time.time() - t.timestamp()


def check_due(row: dict, hive_status: Optional[str], kept: Optional[dict]) -> bool:
    """A stopped run on a running Hive is checked on every poll while it is recent,
    and once when it has never been checked."""
    if hive_status != "running" or not row.get("session_id"):
        return False
    if row.get("state") != "stopped" or row.get("state_reason") not in ("idle", "not_held"):
        return False
    if kept is None:
        return True
    age = _age_s(row.get("sent_at"))
    return age is not None and age <= RECENT_S


_TIME_RE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:?\d{2})?")
_KINDS = ("pipeline", "code", "files", "answer")
_FILE_KINDS = ("content", "knowledge", "external-diff", "external-nodiff", "other")
_DIMS = ("Run Isolation", "Quality", "Autonomy", "Speed", "Token Cost")
#: the error codes the product emits (its error events, turn_outcome's ``crashed`` and
#: the CLI's result subtypes); any other code is ``other`` in /runs, as it is Hive text
ERROR_CODES = frozenset({
    "crashed", "AGENT_EXECUTION_ERROR", "AGENT_TIMEOUT", "ALL_RETRIES_EXHAUSTED",
    "CONVERSATION_ERROR", "CREDENTIALS_EXPIRED", "EMPTY_ANSWER", "EMPTY_MESSAGE",
    "MODEL_ERROR", "NOT_INITIALIZED", "OOM_DETECTED", "OOM_LIMIT_REACHED",
    "PERMISSION_EXPIRED", "POLICY_VIOLATION", "QUEUE_TIMEOUT", "RESOURCE_EXHAUSTED",
    "RETRY_SPAWN", "SDK_ERROR", "SERIALIZATION_ERROR", "SERVICE_UNAVAILABLE",
    "SESSION_BUSY", "SESSION_NOT_FOUND", "SPAWN_FAILED", "TURN_ABANDONED",
    "error_max_turns", "error_during_execution", "turn_limit_reached",
})
_LANDINGS = ("landed", "not_landed", "no_code", "human_override")


def _int(v: Any) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else 0


def _opt_int(v: Any) -> Optional[int]:
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


def _match(rx: re.Pattern, v: Any) -> Optional[str]:
    return v if isinstance(v, str) and rx.fullmatch(v) else None


def _one_of(v: Any, allowed: Iterable[str]) -> Optional[str]:
    return v if isinstance(v, str) and v in allowed else None


def _declared(declared: Any) -> dict[str, dict]:
    """The dispatch's own repositories (this Host's log row), by directory."""
    from core import task_repos
    try:
        return {r["dir"]: r for r in task_repos.parse(declared or [])}
    except task_repos.TaskRepoError:
        return {}


def _public_repo(r: dict, host: dict) -> dict:
    """A declared repository's numbers under the name this Host sent (``host``); its
    error only when it is one task_repos writes."""
    from core import task_repos
    err = r.get("error")
    if err is not None:
        err = err if err in task_repos.ERRORS else task_repos.ERROR_UNREADABLE
    return {"url": host["url"], "ref": host["ref"], "dir": host["dir"],
            "present": r.get("present") if isinstance(r.get("present"), bool) else None,
            "origin_matches": r.get("origin_matches") if isinstance(r.get("origin_matches"), bool) else None,
            "base": _match(_SHA_RE, r.get("base")), "head": _match(_SHA_RE, r.get("head")),
            "commits": [], "commits_total": _int(r.get("commits_total")),
            "files": [], "files_total": _int(r.get("files_total")),
            "added": _int(r.get("added")), "deleted": _int(r.get("deleted")),
            "untracked": _int(r.get("untracked")),
            "pushed": r.get("pushed") if r.get("pushed") in ("yes", "no", "nothing_to_push", "unknown") else "unknown",
            "error": err}


def public(record: Optional[dict], declared: Any = None) -> Optional[dict]:
    """The result as ``/runs`` carries it, which the CLI prints and the local agent
    reads: built field by field from enums, fixed value lists, numbers and shas. No
    string the Hive chose gets in: a repository appears under the URL, ref and
    directory this Host sent (``declared``, the dispatch log row's ``repos``; a
    repository the Hive reports that was not declared is left out), and a change card
    of another repository has no name. The reply, error messages, audit details,
    file paths and tool names are served to the panel only (``/runs/{id}/result``)."""
    from core.run_protocol.lifecycle import PROFILE_STAGES
    from core.pipeline_stage_events import RUN_END_STATUSES
    res = (record or {}).get("result")
    if not isinstance(res, dict):
        return None
    hosts = _declared(declared)
    work = res.get("work") if isinstance(res.get("work"), dict) else {}
    steps = work.get("steps") if isinstance(work.get("steps"), dict) else {}
    human = work.get("human") if isinstance(work.get("human"), dict) else {}
    err = res.get("error")
    error = None
    if isinstance(err, dict):
        error = {"code": err.get("code") if err.get("code") in ERROR_CODES else "other"}
    tokens = res.get("tokens")
    cost = res.get("cost_usd")
    p = res.get("pipeline")
    pipeline = None
    if isinstance(p, dict):
        audit = [{"dim": a["dim"], "verdict": a.get("verdict") if a.get("verdict") in _VERDICTS else None,
                  "detail": None}
                 for a in (p.get("audit") or []) if isinstance(a, dict) and a.get("dim") in _DIMS]
        pipeline = {"run_id": None, "profile": _one_of(p.get("profile"), PROFILE_STAGES),
                    "status": _one_of(p.get("status"), RUN_END_STATUSES),
                    "landing": _one_of(p.get("landing"), _LANDINGS),
                    "landed_sha": _match(_SHA_RE, p.get("landed_sha")), "audit": audit[:5]}
    repos = work.get("repos")
    names = {d.rsplit("/", 1)[-1] for d in hosts}
    return {
        "ended": res.get("ended") if res.get("ended") in ENDED else "no_record",
        "provisional": res.get("provisional") is True,
        "outcome_recorded": res.get("outcome_recorded") is True,
        "hive_records_outcomes": res.get("hive_records_outcomes") is True,
        "error": error, "ended_at": _match(_TIME_RE, res.get("ended_at")),
        "turns": _int(res.get("turns")), "older_turns_not_read": res.get("older_turns_not_read") is True,
        "active_s": _opt_int(res.get("active_s")),
        "tokens": {"input": _int(tokens.get("input")), "output": _int(tokens.get("output"))}
        if isinstance(tokens, dict) else None,
        "cost_usd": cost if isinstance(cost, (int, float)) and not isinstance(cost, bool)
        and math.isfinite(cost) and cost >= 0 else None,
        "cost_partial": res.get("cost_partial") is not False,
        "report_chars": _int(res.get("report_chars")),
        "kinds": [k for k in (res.get("kinds") or []) if k in _KINDS],
        "work": {
            "repos": [_public_repo(r, hosts[r["dir"]]) for r in repos
                      if isinstance(r, dict) and isinstance(r.get("dir"), str) and r["dir"] in hosts]
            if isinstance(repos, list) else None,
            "changes": [{"root": None, "repo": _one_of(c.get("repo"), names),
                         "files": _int(c.get("files")), "added": _int(c.get("added")),
                         "deleted": _int(c.get("deleted")),
                         "landed": [x for x in (c.get("landed") or []) if _match(_SHA_RE, x)][:20]}
                        for c in (work.get("changes") or []) if isinstance(c, dict)],
            "files": [{"kind": f.get("kind") if f.get("kind") in _FILE_KINDS else "other"}
                      for f in (work.get("files") or []) if isinstance(f, dict)],
            "files_omitted": _int(work.get("files_omitted")),
            "steps": {"total": _int(steps.get("total")), "failed": _int(steps.get("failed")),
                      "agents": _int(steps.get("agents")), "by_tool": {}},
            "human": {k: _int(human.get(k)) for k in ("approved", "denied", "unanswered", "answered_questions")},
        },
        "pipeline": pipeline,
        "dropped": _int(res.get("dropped")),
    }


def _status(row: dict, hive_status: Optional[str], kept: Optional[dict]) -> str:
    if not row.get("session_id"):
        return "not_applicable"
    if kept and kept.get("result") is not None:
        return "fetched"
    if hive_status == "gone":
        return "not_applicable"
    if kept and kept.get("fetch_error"):
        return "failed"
    return "pending"


def _read_kept(ids: list[str]) -> dict[str, Optional[dict]]:
    return {i: load(i) for i in ids}


async def attach(rows: list[dict], status_of: dict[str, Optional[str]]) -> None:
    """Add ``result`` / ``result_status`` / ``result_error`` / ``result_checked_at`` to
    each row from its kept record, and start the checks that are due (in the
    background: this poll returns what is kept, the next one sees the new result)."""
    kept = await asyncio.to_thread(_read_kept, [r.get("dispatch_id") or "" for r in rows])
    for r in rows:
        k = kept.get(r.get("dispatch_id") or "")
        st = status_of.get(r.get("hive_id"))
        r["result"] = public(k, r.get("repos"))
        r["result_status"] = _status(r, st, k)
        r["result_error"] = (k or {}).get("fetch_error")
        r["result_checked_at"] = (k or {}).get("checked_at")
        if check_due(r, st, k):
            _start_check(r)
