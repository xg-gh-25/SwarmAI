"""Swarm Out, Host side: dispatch a task to a Hive and answer what it asks.

v0 scope (docs/design/2026-10-27-swarm-out-v0-dispatch.md): send a task to a Hive,
let it run to the end there, and carry a person's answers back through the S3
bridge (``core/swarm_out_bridge.py`` is the Hive side). No result import, no
monitoring beyond what the dashboard needs to list a run.

- ``dispatch`` — start a session on the Hive with the task and return as soon as the
  Hive names the session. The turn keeps running on the Hive after we disconnect
  (``routers/chat.py`` detaches a turn whose client went away; measured 2026-10-27).
- ``list_runs`` — the local dispatch log, each row with its Hive's open questions and
  whether its session is streaming on the Hive right now.
- ``answer`` — validate an answer against the question the Hive wrote, then write it
  to ``bridge/<hive>/answer/…``. Only the Host can write there.

Hive credentials are read from ``hive_instances`` inside the daemon and never leave
this module: no return value, log line or dispatch record carries them. Outbound
URLs are built only from a stored CloudFront domain that matches ``DOMAIN_RE``.
"""
from __future__ import annotations

import asyncio
import base64
import calendar
import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

DOMAIN_RE = re.compile(r"^[a-z0-9-]{1,63}\.cloudfront\.net$")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$")
KEY_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
BUCKET_RE = re.compile(r"^[a-z0-9.-]{3,63}$")
MAX_TASK_CHARS = 20000
MAX_TITLE_CHARS = 120
MAX_ANSWER_CHARS = 4000
SESSION_ID_WAIT_S = 60       # hang-guard: the Hive names the session within seconds
# A prompt waits at most 4 h on the Hive (permission_manager / ask_question_manager,
# both below the WAITING_INPUT watchdog); an older ask has no waiter left, whatever
# its markers say (e.g. the Hive was destroyed before it could write closed/).
ASK_MAX_AGE_S = 4 * 3600 + 5 * 60
HTTP_TIMEOUT_S = 20

UNATTENDED_PREAMBLE = (
    "[Swarm Out] This task was dispatched to you and runs unattended: nobody is "
    "watching this session. Decide what you can decide yourself and state your "
    "assumptions in your final reply. Use AskUserQuestion only for a decision you "
    "genuinely cannot make; write each question so it is understandable on its own, "
    "because the person reads it elsewhere, possibly hours later. Finish with a reply "
    "that says what you did and where the results are.\n\n"
    "Task:\n"
)


class SwarmOutError(Exception):
    """A dispatch or answer that cannot be done; the message is safe to show."""


def _state_dir() -> Path:
    from config import get_app_data_dir
    d = Path(get_app_data_dir()) / "state" / "swarm-out"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _log_path() -> Path:
    return _state_dir() / "dispatches.jsonl"


def _db_path() -> str:
    from database import db
    return str(db.db_path)


async def hives() -> dict[str, Any]:
    """The Hives this Host knows, by name and status: what the Swarm Out panel needs to
    say whether there is anywhere to dispatch to, and what an agent needs to pick one.
    Never addresses or credentials."""
    import aiosqlite
    async with aiosqlite.connect(_db_path()) as conn:
        await conn.execute("PRAGMA busy_timeout = 3000")
        cur = await conn.execute(
            "SELECT name, status, region FROM hive_instances ORDER BY name")
        rows = [{"name": r[0], "status": r[1], "region": r[2]} for r in await cur.fetchall()]
    return {"hives": rows, "running": sum(1 for r in rows if r["status"] == "running")}


async def _instance(hive_ref: str) -> dict[str, Any]:
    """A running Hive by id or name, with its account row; validated fields only."""
    import aiosqlite
    async with aiosqlite.connect(_db_path()) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA busy_timeout = 3000")
        cur = await conn.execute(
            "SELECT * FROM hive_instances WHERE id = ? OR name = ?", (hive_ref, hive_ref))
        rows = [dict(r) for r in await cur.fetchall()]
        if len(rows) != 1:
            raise SwarmOutError(f"no single Hive named {hive_ref!r}")
        inst = rows[0]
        cur = await conn.execute("SELECT * FROM hive_accounts WHERE id = ?", (inst["account_ref"],))
        acct = await cur.fetchone()
    return _validate_instance(inst, dict(acct) if acct is not None else None)


def _validate_instance(inst: dict[str, Any], acct: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Refuse a Hive whose stored address, name or bucket is not the expected shape:
    every outbound URL and S3 key is built from these fields."""
    if inst.get("status") != "running":
        raise SwarmOutError(f"Hive {inst.get('name')} is {inst.get('status')}, not running")
    domain = (inst.get("cloudfront_domain") or "").strip().lower()
    if not DOMAIN_RE.match(domain) or not NAME_RE.match(inst.get("name") or "") \
            or not BUCKET_RE.match(inst.get("s3_bucket") or ""):
        raise SwarmOutError(f"Hive {inst.get('name')} has an unexpected address or bucket")
    if not inst.get("auth_user") or not inst.get("auth_password") or acct is None:
        raise SwarmOutError(f"Hive {inst.get('name')} has no credentials or account")
    return {**inst, "cloudfront_domain": domain, "_account": acct}


def _client(inst: dict[str, Any]) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=_base(inst), headers=_auth(inst),
                             timeout=HTTP_TIMEOUT_S, follow_redirects=False)


def _auth(inst: dict[str, Any]) -> dict[str, str]:
    token = base64.b64encode(f"{inst['auth_user']}:{inst['auth_password']}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _base(inst: dict[str, Any]) -> str:
    return f"https://{inst['cloudfront_domain']}"


def _s3(inst: dict[str, Any]):
    from hive.provisioner import HiveProvisioner
    from database import db
    session = HiveProvisioner(Path(db.db_path))._get_session(inst["_account"], inst["region"])
    return session.client("s3", region_name=inst["region"])


async def dispatch(hive_ref: str, title: str, task: str, origin: str = "swarm-out") -> dict[str, Any]:
    """Start the task on the Hive; return {dispatch_id, hive, session_id, title, sent_at}."""
    title = " ".join((title or "").split())[:MAX_TITLE_CHARS]
    task = (task or "").strip()
    if not title or not task:
        raise SwarmOutError("a dispatch needs a title and a task")
    if len(task) > MAX_TASK_CHARS:
        raise SwarmOutError(f"the task is longer than {MAX_TASK_CHARS} characters")
    inst = await _instance(hive_ref)
    async with _client(inst) as client:
        r = await client.get("/api/chat/sessions/admission-state")
        if r.status_code != 200:
            raise SwarmOutError(f"Hive {inst['name']} did not answer (HTTP {r.status_code})")
        if r.json().get("saturated"):
            raise SwarmOutError(f"Hive {inst['name']} is busy; try again later")
        session_id = await asyncio.wait_for(
            _start_session(client, UNATTENDED_PREAMBLE + task), SESSION_ID_WAIT_S)
    if not session_id or not KEY_RE.match(session_id):
        raise SwarmOutError("the Hive did not name a session")
    row = {"dispatch_id": f"so_{uuid.uuid4().hex[:12]}", "hive_id": inst["id"],
           "hive": inst["name"], "session_id": session_id, "title": title,
           "origin": origin, "sent_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    line = json.dumps(row, ensure_ascii=False) + "\n"
    await asyncio.to_thread(_append, line)
    logger.info("swarm_out: dispatched %s to %s (session %s)", row["dispatch_id"], inst["name"], session_id)
    return row


def _append(line: str) -> None:
    with open(_log_path(), "a", encoding="utf-8") as f:
        f.write(line)


async def _start_session(client: httpx.AsyncClient, message: str) -> Optional[str]:
    """POST the message and read SSE only until the Hive names the session, then drop
    the connection: the Hive keeps running the turn (it detaches a gone client)."""
    async with client.stream("POST", "/api/chat/stream",
                             json={"agent_id": "default", "message": message},
                             timeout=httpx.Timeout(HTTP_TIMEOUT_S, read=SESSION_ID_WAIT_S)) as r:
        if r.status_code != 200:
            raise SwarmOutError(f"the Hive refused the task (HTTP {r.status_code})")
        async for line in r.aiter_lines():
            if not line.startswith("data:"):
                continue
            try:
                ev = json.loads(line[5:].strip())
            except ValueError:
                continue
            if isinstance(ev, dict):
                sid = ev.get("session_id") or ev.get("sessionId")
                if isinstance(sid, str) and sid:
                    return sid
    return None


def _read_log() -> list[dict[str, Any]]:
    p = _log_path()
    if not p.exists():
        return []
    out = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(ln)
        except ValueError:
            continue                      # a torn last line from a crash
        if isinstance(row, dict) and row.get("dispatch_id"):
            out.append(row)
    return out


def _clean_ask(rec: Any, hive: str) -> Optional[dict[str, Any]]:
    """Rebuild a Hive-written ask from known fields only, each typed and capped. The
    Hive's own ``hive`` field is ignored: the caller sets it from the Host's record."""
    if not isinstance(rec, dict) or rec.get("kind") not in ("question", "approval"):
        return None
    sid, rid = str(rec.get("session_id", "")), str(rec.get("request_id", ""))
    if not KEY_RE.match(sid) or not KEY_RE.match(rid):
        return None
    out: dict[str, Any] = {"kind": rec["kind"], "hive": hive, "session_id": sid,
                           "request_id": rid, "asked_at": str(rec.get("asked_at", ""))[:40]}
    if rec["kind"] == "approval":
        out.update(tool_name=str(rec.get("tool_name", ""))[:100],
                   command=str(rec.get("command", ""))[:2000],
                   reason=str(rec.get("reason", ""))[:1000])
        return out
    qs = []
    for q in (rec.get("questions") or [])[:20] if isinstance(rec.get("questions"), list) else []:
        if not isinstance(q, dict) or not str(q.get("question", "")).strip():
            continue
        opts = q.get("options") if isinstance(q.get("options"), list) else []
        qs.append({"question": str(q["question"])[:MAX_ANSWER_CHARS], "header": str(q.get("header", ""))[:200],
                   "multiSelect": bool(q.get("multiSelect")),
                   "options": [{"label": str(o.get("label", ""))[:500], "description": str(o.get("description", ""))[:1000]}
                               for o in opts[:10] if isinstance(o, dict) and str(o.get("label", "")).strip()]})
    if not qs:
        return None
    out["questions"] = qs
    return out


def _too_old(asked_at: str, now: float) -> bool:
    try:
        t = calendar.timegm(time.strptime(asked_at, "%Y-%m-%dT%H:%M:%SZ"))
    except (ValueError, OverflowError):
        return True
    return now - t > ASK_MAX_AGE_S


def _open_asks(s3, bucket: str, hive: str) -> list[dict[str, Any]]:
    """Questions the Hive wrote that are neither answered, closed nor too old."""
    def keys(prefix: str) -> set[str]:
        found: set[str] = set()
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            found |= {o["Key"][len(prefix):] for o in page.get("Contents", [])}
        return found
    base = f"bridge/{hive}/"
    asks = keys(base + "ask/")
    done = keys(base + "answer/") | keys(base + "closed/")
    out = []
    for rel in sorted(asks - done):
        try:
            body = s3.get_object(Bucket=bucket, Key=base + "ask/" + rel)["Body"].read(64 * 1024)
            rec = json.loads(body)
        except Exception as e:   # one unreadable ask must not hide the others
            logger.warning("swarm_out: skipped an unreadable ask from %s (%s)", hive, type(e).__name__)
            continue
        clean = _clean_ask(rec, hive)
        if clean is not None and not _too_old(clean["asked_at"], time.time()):
            out.append(clean)
    return out


async def _streaming_sessions(inst: dict[str, Any]) -> Optional[set[str]]:
    """The session ids streaming on the Hive now (its own streaming-state endpoint, the
    same one its tabs read), or None when the Hive did not say."""
    async with _client(inst) as client:
        r = await client.get("/api/chat/sessions/streaming-state")
    if r.status_code != 200:
        return None
    sessions = r.json().get("sessions")
    if not isinstance(sessions, dict):
        return None
    return {sid for sid, e in sessions.items() if isinstance(e, dict) and e.get("streaming") is True}


async def list_runs() -> dict[str, Any]:
    """Dispatch log rows (newest first) and every Hive's open questions.

    Each row says ``waiting`` (its session has an open question) and ``running`` (its
    session is streaming on the Hive now): True / False, or None when that Hive could
    not be asked, never a guess. A session the Hive no longer holds is not running."""
    rows = list(reversed(await asyncio.to_thread(_read_log)))
    asks: list[dict[str, Any]] = []
    errors: list[str] = []
    streaming: dict[str, Optional[set[str]]] = {}
    for hive_id in sorted({r["hive_id"] for r in rows}):
        try:
            inst = await _instance(hive_id)
        except SwarmOutError as e:
            errors.append(str(e))
            continue
        except Exception as e:                      # store unreadable: show it, keep the list
            logger.warning("swarm_out: reading the Hive record failed (%s)", type(e).__name__)
            errors.append("could not read questions from one Hive")
            continue
        try:
            s3 = await asyncio.to_thread(_s3, inst)
            for rec in await asyncio.to_thread(_open_asks, s3, inst["s3_bucket"], inst["name"]):  # cleaned
                rec["hive_id"] = inst["id"]
                asks.append(rec)
        except SwarmOutError as e:
            errors.append(str(e))
        except Exception as e:                      # S3 unreachable: show it, keep the list
            logger.warning("swarm_out: listing questions failed (%s)", type(e).__name__)
            errors.append("could not read questions from one Hive")
        try:
            streaming[hive_id] = await _streaming_sessions(inst)
        except Exception as e:                      # Hive unreachable: running unknown
            logger.warning("swarm_out: reading session state failed (%s)", type(e).__name__)
            streaming[hive_id] = None
    by_session = {(a["hive_id"], a.get("session_id")): a for a in asks}
    for r in rows:
        r["waiting"] = (r["hive_id"], r["session_id"]) in by_session
        live = streaming.get(r["hive_id"])
        r["running"] = None if live is None else r["session_id"] in live
    return {"runs": rows, "asks": asks, "errors": errors}


def _check_answer(rec: dict[str, Any], answers: Any, decision: Any) -> dict[str, Any]:
    if rec.get("kind") == "approval":
        if decision not in ("approve", "deny"):
            raise SwarmOutError("an approval takes approve or deny")
        return {"decision": decision}
    asked = {q.get("question") for q in rec.get("questions") or [] if isinstance(q, dict)}
    if not isinstance(answers, dict) or not answers:
        raise SwarmOutError("answer every question")
    clean: dict[str, str] = {}
    for q, a in answers.items():
        if q not in asked or not isinstance(a, str) or not a.strip() or len(a) > MAX_ANSWER_CHARS:
            raise SwarmOutError("an answer does not match the question")
        clean[q] = a.strip()
    if set(clean) != asked:
        raise SwarmOutError("answer every question")
    return {"answers": clean}


async def answer(hive_ref: str, session_id: str, request_id: str,
                 answers: Any = None, decision: Any = None) -> dict[str, Any]:
    """Write the person's answer for an open question to the bridge."""
    if not KEY_RE.match(session_id or "") or not KEY_RE.match(request_id or ""):
        raise SwarmOutError("unknown question")
    inst = await _instance(hive_ref)
    s3 = await asyncio.to_thread(_s3, inst)
    base = f"bridge/{inst['name']}/"
    rel = f"{session_id}/{request_id}.json"

    def _load(prefix: str) -> Optional[bytes]:
        try:
            return s3.get_object(Bucket=inst["s3_bucket"], Key=base + prefix + rel)["Body"].read(64 * 1024)
        except s3.exceptions.NoSuchKey:
            return None

    raw = await asyncio.to_thread(_load, "ask/")
    if raw is None:
        raise SwarmOutError("unknown question")
    if await asyncio.to_thread(_load, "closed/") is not None:
        raise SwarmOutError("this question is no longer open on the Hive")
    if await asyncio.to_thread(_load, "answer/") is not None:
        raise SwarmOutError("this question was already answered")
    rec = _clean_ask(json.loads(raw), inst["name"])
    if rec is None or _too_old(rec["asked_at"], time.time()):
        raise SwarmOutError("this question is no longer open on the Hive")
    body = {"request_id": request_id, **_check_answer(rec, answers, decision),
            "answered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

    def _put() -> None:
        try:   # IfNoneMatch: two answers at once — the second is refused, not overwritten
            s3.put_object(Bucket=inst["s3_bucket"], Key=base + "answer/" + rel,
                          Body=json.dumps(body, ensure_ascii=False).encode(), IfNoneMatch="*",
                          ContentType="application/json", ServerSideEncryption="AES256")
        except Exception as e:
            code = getattr(e, "response", {}).get("Error", {}).get("Code", "")
            if code in ("PreconditionFailed", "ConditionalRequestConflict"):
                raise SwarmOutError("this question was already answered") from None
            raise
    await asyncio.to_thread(_put)
    return {"ok": True, "request_id": request_id}
