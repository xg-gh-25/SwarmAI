"""Following a dispatched run from the session that dispatched it.

Design docs/design/2026-10-31-swarm-out-agent-owns-its-runs.md §2.3. Two readers of one
fact, a run's **fingerprint** (what about it can change and matter: its state and why,
its stage, whether its result is in, the phase of each of its waits, whether it is
settled):

- ``watch`` (the route ``POST /api/swarm-out/watch``, the CLI's ``watch``): returns as
  soon as one of the runs asked about changes from the fingerprint the caller last saw,
  or after ``wait_s`` with ``changed: false``. A run read as ``unknown`` (its Hive did
  not answer the probe) counts as changed only when two reads in a row say so: one slow
  probe is not news.
- ``digest(session)``: the block put before the next message of the session that
  dispatched the runs, listing those that changed since that session last saw them.
  It reads the background snapshot only (no probe), and only Host fields.

Both record what the session has now seen (``state/swarm-out/seen/<session>.json``), so
a change the agent reported while following is not reported again at the next message.

Nothing here carries text a Hive wrote: titles are this machine's agent's, every other
field is an enum, a code or a time (``public`` already built the rows that way).

Public: ``fingerprint``, ``summary``, ``watch``, ``digest``, ``mark_seen``,
``DIGEST_HEADER``.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Optional

from core import swarm_run
from core.injected_blocks import DISPATCH_DIGEST_HEADER as DIGEST_HEADER

logger = logging.getLogger(__name__)

#: under the agent's Bash ceiling (10 minutes), like DISPATCH_TIMEOUT_S: a hang guard
#: for the call, not for the run (the agent calls again)
WATCH_MAX_S = 540
WATCH_EVERY_S = 30
MAX_WATCHED = 20
#: at most this many runs in one digest (the newest dispatches first); the rest are
#: counted, and left unseen for the next digest
MAX_DIGEST = 10


# ── the fingerprint ──────────────────────────────────────────────────────────

def _card(row: dict[str, Any], request_id: Any) -> Optional[dict[str, Any]]:
    """The chat card's forward outcome for this wait (core/swarm_out_wait_card), or None."""
    from core import swarm_out_wait_card
    return swarm_out_wait_card.status(str(row.get("dispatch_id") or ""), str(request_id or ""))


def _card_mark(card: Optional[dict[str, Any]]) -> str:
    """What of a card's outcome is news: forwarded, or not with why (a card only built
    or asked is not: the agent itself just asked it)."""
    if not card or card.get("status") not in ("forwarded", "not_forwarded"):
        return ""
    why = str(card.get("reason") or "")
    return card["status"] + (":" + hashlib.sha256(why.encode()).hexdigest()[:8] if why else "")


def _waits(row: dict[str, Any], data: dict[str, Any]) -> list[tuple[str, str, str, str]]:
    sid = row.get("session_id")
    out = []
    for w in list(data.get("asks") or []) + list(data.get("answered") or []):
        if isinstance(w, dict) and sid and w.get("session_id") == sid:
            out.append((str(w.get("request_id") or ""), str(w.get("kind") or ""), str(w.get("phase") or ""),
                        _card_mark(_card(row, w.get("request_id")))))
    return sorted(set(out))


#: a running run's state_reason is the Hive's activity name, which changes all the time:
#: only these reasons of a running run are news
_RUNNING_REASONS = ("answer_sent", "background")


def fingerprint(row: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    settle = row.get("settle") if isinstance(row.get("settle"), dict) else {}
    settled = settle.get("settled") if isinstance(settle.get("settled"), dict) else None
    res = row.get("result") if isinstance(row.get("result"), dict) else {}
    reason = row.get("state_reason")
    if row.get("state") == "running" and reason not in _RUNNING_REASONS:
        reason = "activity"
    return {"state": row.get("state"), "reason": reason, "stage": row.get("stage"), "why": row.get("why"),
            "result": row.get("result_status"), "ended": res.get("ended"), "ended_at": res.get("ended_at"),
            "why_not": sorted(settle.get("why_not") or []),
            "waits": [list(w) for w in _waits(row, data)],
            "settled": (settled or {}).get("outcome") if settled else None}


def _hash(fp: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(fp, sort_keys=True).encode()).hexdigest()[:16]


def summary(row: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    """What the agent is told about one run: Host fields only."""
    res = row.get("result") if isinstance(row.get("result"), dict) else {}
    waits = []
    for w in list(data.get("asks") or []) + list(data.get("answered") or []):
        if isinstance(w, dict) and row.get("session_id") and w.get("session_id") == row.get("session_id"):
            card = _card(row, w.get("request_id"))
            from core import swarm_out_wait_card
            cls = w.get("approval_class")
            escalated = bool(swarm_out_wait_card.escalation(str(row.get("dispatch_id") or ""),
                                                            str(w.get("request_id") or "")))
            waits.append({"request_id": w.get("request_id"), "kind": w.get("kind"), "phase": w.get("phase"),
                          "refused_reason": w.get("refused_reason"),
                          # who answers it (design 2026-10-31-hive-approvals): "you" (this
                          # agent: `swarm_out answer`) or "person" (ask them, or escalate)
                          "approval_class": cls,
                          "who": swarm_out_wait_card._who(cls, escalated),
                          # the chat card's outcome; its reason is this daemon's own text
                          "card": card})
    agent = row.get("project_agent") if isinstance(row.get("project_agent"), dict) else {}
    return {"dispatch_id": row.get("dispatch_id"), "title": row.get("title"), "hive": row.get("hive"),
            "state": row.get("state"), "state_reason": row.get("state_reason"), "stage": row.get("stage"),
            "result_status": row.get("result_status"),
            "ended": res.get("ended"), "ended_at": res.get("ended_at"), "returns": agent.get("returns"),
            # each offered command with how it works now (accept: land / manual / verdict)
            "actions": [{"key": a.get("key"), "variant": a.get("variant")}
                        for a in (row.get("actions") or []) if isinstance(a, dict)],
            "why_not": (row.get("settle") or {}).get("why_not") if isinstance(row.get("settle"), dict) else None,
            "waits": waits}


# ── what a session has seen ──────────────────────────────────────────────────

def _seen_path(session: str) -> Optional[Path]:
    from core import swarm_out
    from core.app_session_id import valid_app_session_id
    if not valid_app_session_id(session):
        return None
    key = hashlib.sha256(session.encode()).hexdigest()[:24]   # no session id in a file name
    return swarm_out._state_dir() / "seen" / f"{key}.json"


def _load_seen(session: Optional[str]) -> dict[str, str]:
    p = _seen_path(session or "")
    if p is None:
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in d.items() if isinstance(k, str) and isinstance(v, str)} if isinstance(d, dict) else {}


_SEEN_LOCK = threading.Lock()


def mark_seen(session: Optional[str], seen: dict[str, str], keep: Optional[set] = None) -> None:
    """Record the fingerprints *session* has now seen (merged over what it had, under one
    lock: a digest and a watch at once lose neither). ``keep``: the runs still listed;
    the session's entries for any other are dropped, so the file stays small."""
    p = _seen_path(session or "")
    if p is None or not seen:
        return
    try:
        with _SEEN_LOCK:
            _write_seen(p, session, seen, keep)
    except OSError:
        logger.warning("swarm_out_follow: could not record what a session has seen", exc_info=True)


def _write_seen(p: Path, session: Optional[str], seen: dict[str, str], keep: Optional[set]) -> None:
    merged = {**_load_seen(session), **seen}
    if keep is not None:
        merged = {k: v for k, v in merged.items() if k in keep or k in seen}
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.parent / f".{p.name}.{secrets.token_hex(6)}.tmp"
    tmp.write_text(json.dumps(merged), encoding="utf-8")
    os.replace(tmp, p)


# ── watch ────────────────────────────────────────────────────────────────────

def _token(fps: dict[str, str]) -> str:
    return base64.urlsafe_b64encode(json.dumps(fps, sort_keys=True).encode()).decode()


def _untoken(token: Optional[str]) -> dict[str, str]:
    if not token:
        return {}
    try:
        d = json.loads(base64.urlsafe_b64decode(token.encode()).decode())
    except (ValueError, UnicodeDecodeError):
        return {}
    return {k: v for k, v in d.items() if isinstance(k, str) and isinstance(v, str)} if isinstance(d, dict) else {}


async def _list() -> dict[str, Any]:
    from core import swarm_out
    return await swarm_out.list_runs(swarm_out.SNAPSHOT_FRESH_S)


async def watch(ids: list[str], since: Optional[str] = None, session: Optional[str] = None,
                wait_s: float = WATCH_MAX_S, every_s: float = WATCH_EVERY_S) -> dict[str, Any]:
    """Wait until one of *ids* changes from the fingerprint in *since* (none: from
    what is read first), or *wait_s* passes. ``unknown`` counts only when two reads in a
    row say it. Returns ``{changed, token, runs}``: every asked run's summary, the
    changed ones listed in ``changed_ids``."""
    ids = [i for i in dict.fromkeys(ids) if isinstance(i, str)][:MAX_WATCHED]
    if not ids:
        return {"changed": False, "token": _token({}), "runs": [], "changed_ids": []}
    # what the caller last saw: the token, and the session's record (a digest may have
    # shown a change after the token was issued): a run is unchanged while it reads as
    # either; a run in neither starts from now
    record = _load_seen(session)
    seen = {**record, **_untoken(since)}
    unknown_once: set[str] = set()
    deadline = time.monotonic() + max(0.0, wait_s)
    first = True
    while True:
        data = await _list()
        rows = {r.get("dispatch_id"): r for r in (data.get("runs") or []) if isinstance(r, dict)}
        now = {}
        for i in ids:
            r = rows.get(i)
            now[i] = _hash(fingerprint(r, data)) if r else "missing"
        if first:
            seen = {i: seen.get(i, now[i]) for i in ids}
        first = False
        changed = []
        for i in ids:
            if now[i] == seen.get(i) or now[i] == record.get(i):
                unknown_once.discard(i)
                continue
            r = rows.get(i) or {}
            if r.get("state") == "unknown" and i not in unknown_once:
                unknown_once.add(i)              # one slow probe: read it once more
                continue
            unknown_once.discard(i)              # told now: the token carries what was read
            changed.append(i)
        if changed or time.monotonic() >= deadline:
            # what the caller has now been told: the current state, except a run held
            # back by one unknown read (it keeps what was seen before)
            token_fps = {i: (seen[i] if i in unknown_once else now[i]) for i in ids}
            # what the caller is told now it has seen: every run it asked about that this
            # session dispatched (another session's runs are not its to mark)
            own = {i: token_fps[i] for i in ids if swarm_run.owner_of(rows.get(i)) == session}
            await asyncio.to_thread(mark_seen, session, own, set(rows))
            return {"changed": bool(changed), "changed_ids": changed, "token": _token(token_fps),
                    "runs": [summary(rows[i], data) if i in rows else {"dispatch_id": i, "state": "missing"}
                             for i in ids]}
        await asyncio.sleep(min(every_s, max(0.0, deadline - time.monotonic())))


# ── the digest before a session's next message ───────────────────────────────

def listed_ids() -> set:
    """The runs the background snapshot lists (what a session's seen record keeps)."""
    from core import swarm_out
    data = swarm_out.snapshot()
    return {r.get("dispatch_id") for r in (data or {}).get("runs") or [] if isinstance(r, dict)}


#: what the funnel asks of the dispatching agent next (core/swarm_out_settle ``why``;
#: design 2026-11-02-hive-run-decision-record §1)
_NEXT = {"judge": "landed: judge it (`swarm_out accept`, or `swarm_out ask-person` when you cannot)",
         "revert": "the person asked to revert what landed: undo it, then `swarm_out reverted`",
         "ask_person": "with the person (keep or revert)"}


def digest(session: Optional[str]) -> tuple[Optional[str], dict[str, str]]:
    """(block, fingerprints shown) for the next message of *session*: the runs it
    dispatched whose fingerprint changed since it last saw them, from the background
    snapshot. (None, {}) when there is nothing to say. The caller marks the
    fingerprints seen once the block is delivered (``mark_seen``)."""
    from core import swarm_out
    if not session:
        return None, {}
    data = swarm_out.snapshot()
    if not isinstance(data, dict):
        return None, {}
    seen = _load_seen(session)
    lines, shown, more = [], {}, 0
    for r in data.get("runs") or []:                 # newest dispatch first
        if not isinstance(r, dict) or swarm_run.owner_of(r) != session:
            continue
        if r.get("state") == "unknown" and r.get("state_reason") != "no_session_id":
            continue          # one slow probe is not news; told once the Hive answers (not marked)
        h = _hash(fingerprint(r, data))
        if seen.get(r.get("dispatch_id")) == h:
            continue
        if len(lines) >= MAX_DIGEST:
            more += 1                                # named, not shown: not marked seen
            continue
        shown[str(r.get("dispatch_id"))] = h
        s = summary(r, data)
        bits = [str(s["state"] or "?"), str(s["state_reason"] or "")]
        if s.get("stage"):
            bits.append(f"stage {s['stage']}")
        if s.get("result_status") == "fetched":
            bits.append(f"result {s.get('ended') or 'in'}")
        if _NEXT.get(str(r.get("why") or "")):
            bits.append(_NEXT[str(r.get("why"))])
        waits = [f"{w['kind']} {w['phase']}"
                 + (f" ({w['approval_class'] or 'unknown'}, answered by {w['who']})" if w.get("phase") == "waiting" else "")
                 for w in s["waits"] if w.get("kind")]
        if waits:
            bits.append("waits: " + ", ".join(waits))
        title = " ".join(str(s.get("title") or "").split())[:80]
        lines.append(f"- {s['dispatch_id']} 「{title}」: " + " · ".join(b for b in bits if b))
    if not lines:
        return None, {}
    body = "\n".join(lines) + (f"\n- and {more} more that changed: `swarm_out runs`" if more else "")
    block = (f"{DIGEST_HEADER}\nRuns this session dispatched changed since your last turn. Tell the "
             "person where they are in a sentence or two first, handle what you can (the Swarm Out "
             f"skill), then answer the message.\n{body}")
    return block, shown
