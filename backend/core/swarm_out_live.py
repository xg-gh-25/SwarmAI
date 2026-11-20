"""Where a running Hive task stands and what its agent is doing, read on this host.

The result checks (core/swarm_out_results) read a run's session once it has stopped. A
task works in one long turn, so while it runs the host would know nothing of where it
stands. The Hive persists the turn as it goes, including the backend-written
``pipeline_run`` and ``pipeline_stage`` blocks (core/pipeline_stage_events), and its
messages API returns that in-progress turn. This module reads the newest turn of a
running task (native or pipeline), at most once per ``LIVE_STAGE_S`` and at most
``MAX_BODY_BYTES`` (streamed, parsed off the event loop), and keeps in memory the newest
stage of the task's pipeline run (``live_stage``) and what the agent is doing now
(``live_now``: its newest line of text and its newest tool call, as the chat shows
them); ``attach`` gives both to the /swarm-out/runs rows that run.

Why memory, not the kept result record: the result check loads, edits and saves that
file, and a second writer would lose one of the two updates; a live stage is stale
within minutes anyway. A restarted daemon reads it again on the next poll.

Everything read from the Hive is data: only a block's ``runId``, ``stage``, ``status``,
``next`` and ``at`` (and a ``pipeline_run`` block's ``stages``) are taken, each checked
for type and length; a stage outside the pipeline's stages is dropped. For ``live_now``
only an assistant ``text`` block's text and a ``tool_use`` block's ``display.tool`` and
``display.object`` (the redacted copy the chat shows) are taken, as strings, control
characters removed and cut to a length. A Hive that does not answer, or answers with an
error, leaves nothing from that check. Nothing here raises into the poll.

- ``live_stage_of(messages, run_ids)`` — pure: the newest stage of one of the runs.
- ``live_now_of(messages)`` — pure: the newest text line and tool call of the turn.
- ``due(row, hive_status)`` — a running task on a running Hive, not read lately.
- ``check(row)`` — read the Hive once (async; never raises).
- ``attach(rows, status_of)`` — add ``live_stage`` and ``live_now`` to the running rows,
  start the checks due.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: at most one read of a run's Hive in this many seconds
LIVE_STAGE_S = 90
#: reads at the same time, over every Hive
MAX_CONCURRENT = 2
#: the most of a Hive's answer read for one live stage (a turn past it is skipped)
MAX_BODY_BYTES = 4 * 1024 * 1024
#: the pipeline's stages (core.run_protocol.lifecycle.PROFILE_STAGES, every profile)
STAGES = ("evaluate", "think", "plan", "build", "review", "test", "deliver", "reflect")
_STATUSES = ("in_progress", "completed", "skipped")

_live: dict[str, dict[str, Any]] = {}       # dispatch_id -> the stage the last check found
_now: dict[str, dict[str, Any]] = {}        # dispatch_id -> what the agent did, as last read
_checked: dict[str, float] = {}             # dispatch_id -> monotonic time of the last check
_inflight: set[str] = set()
_tasks: set[asyncio.Task] = set()
_sems: dict[int, asyncio.Semaphore] = {}


def _s(v: Any, n: int) -> Optional[str]:
    return v[:n] if isinstance(v, str) and v else None


#: the most of each live_now field kept
NOW_TEXT, NOW_TOOL, NOW_OBJECT = 240, 40, 160
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u200b-\u200f\u202a-\u202e\u2066-\u2069]")


def _line(v: Any, n: int, last: bool) -> Optional[str]:
    """One line of *v* (its last non-empty one, or its first), control characters
    removed, at most *n* characters; None for anything but a non-empty string."""
    if not isinstance(v, str):
        return None
    lines = [_CONTROL.sub("", x).strip() for x in v.splitlines()]
    lines = [x for x in lines if x]
    if not lines:
        return None
    return (lines[-1] if last else lines[0])[:n]


def live_now_of(messages: Any) -> Optional[dict[str, Any]]:
    """What the agent is doing in the newest assistant message of *messages* (the Hive's
    messages API body): ``{"text", "tool", "object"}``, each None when not read, or None
    when there is neither a text line nor a tool call. ``text`` is the last non-empty line
    of its newest text block; ``tool`` / ``object`` are its newest tool call as the chat
    shows it (``display``, the redacted copy), the object's first line."""
    msgs = messages if isinstance(messages, list) else []
    last = next((m for m in reversed(msgs) if isinstance(m, dict) and m.get("role") == "assistant"), None)
    content = last.get("content") if isinstance(last, dict) else None
    text = tool = obj = None
    for b in reversed(content if isinstance(content, list) else []):
        if not isinstance(b, dict):
            continue
        if text is None and b.get("type") == "text":
            text = _line(b.get("text"), NOW_TEXT, last=True)
        if tool is None and b.get("type") == "tool_use" and isinstance(b.get("display"), dict):
            tool = _line(b["display"].get("tool"), NOW_TOOL, last=False)
            obj = _line(b["display"].get("object"), NOW_OBJECT, last=False) if tool else None
        if text is not None and tool is not None:
            break
    if text is None and tool is None:
        return None
    return {"text": text, "tool": tool, "object": obj}


def live_stage_of(messages: Any, run_ids: set[str]) -> Optional[dict[str, Any]]:
    """The newest stage of one of *run_ids* in *messages* (the Hive's messages API
    body): ``{"run_id", "stage", "stages", "at"}``, or None. An in-progress block names
    its stage; a completed one the stage after it (``next``). ``stages`` are the run's
    planned stages from its newest ``pipeline_run`` block (empty when none was read)."""
    blocks: list[dict] = []
    for m in messages if isinstance(messages, list) else []:
        content = m.get("content") if isinstance(m, dict) else None
        for b in content if isinstance(content, list) else []:
            if isinstance(b, dict) and b.get("type") in ("pipeline_stage", "pipeline_run") \
                    and _s(b.get("runId"), 64) in run_ids:
                blocks.append(b)
    stage = None
    for b in reversed(blocks):
        if b.get("type") != "pipeline_stage" or b.get("status") not in _STATUSES:
            continue
        name = b.get("stage") if b.get("status") == "in_progress" else b.get("next")
        if isinstance(name, str) and name in STAGES:
            stage = {"run_id": _s(b.get("runId"), 64), "stage": name, "at": _s(b.get("at"), 40)}
        break
    if stage is None:
        return None
    planned: list[str] = []
    for b in reversed(blocks):
        if b.get("type") == "pipeline_run" and b.get("runId") == stage["run_id"] and isinstance(b.get("stages"), list):
            planned = [x for x in b["stages"][:10] if isinstance(x, str) and x in STAGES]
            break
    return {**stage, "stages": planned}


def _run_ids(row: dict) -> set[str]:
    from core import swarm_out
    did = row.get("dispatch_id") or ""
    agent = row.get("project_agent") if isinstance(row.get("project_agent"), dict) else {}
    ids = set(swarm_out.pipeline_run_ids(did)) if did else set()
    if isinstance(agent.get("run_id"), str):
        ids.add(agent["run_id"])
    return ids


def due(row: dict, hive_status: Optional[str], now: Optional[float] = None) -> bool:
    """A task (native or pipeline) that runs now on a running Hive, with a session, not
    read in the last ``LIVE_STAGE_S``."""
    if hive_status != "running" or row.get("state") != "running" or not isinstance(row.get("session_id"), str):
        return False
    last = _checked.get(row.get("dispatch_id") or "")
    return last is None or (now if now is not None else time.monotonic()) - last >= LIVE_STAGE_S


async def check(row: dict) -> Optional[dict[str, Any]]:
    """Read the run's newest turn from its Hive once; keep what the agent is doing, and
    keep and return the stage found (None when the Hive did not answer properly or holds
    no stage of the run)."""
    from core import swarm_out
    did, sid = row.get("dispatch_id") or "", row.get("session_id")
    _checked[did] = time.monotonic()
    if not isinstance(sid, str) or not swarm_out.KEY_RE.fullmatch(sid):
        return None
    try:
        from core import swarm_out_remote
        from core.carriers import registry
        async with registry.client_for(registry.handle_of(row)) as client:
            # streamed and capped: a long pipeline turn is never buffered past the cap
            async with client.stream("GET", f"/api/chat/sessions/{sid}/messages", params={"turns": 1}) as r:
                if r.status_code != 200:
                    return None
                raw = await swarm_out_remote.read_capped(r, MAX_BODY_BYTES)
        ids = _run_ids(row)
        # parsing a whole turn is CPU work: off the event loop, with the scans

        def scan() -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
            body = json.loads(raw)
            return live_stage_of(body, ids), live_now_of(body)
        found, now = await asyncio.to_thread(scan)
    except Exception as e:  # noqa: BLE001 -- a live stage is a hint: the poll never fails on it
        logger.info("swarm_out_live: %s not read (%s)", did, type(e).__name__)
        return None
    checked = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if found:
        _live[did] = {**found, "checked_at": checked}
    if now:
        _now[did] = {**now, "checked_at": checked}
    return found


async def _run(row: dict) -> None:
    loop_id = id(asyncio.get_running_loop())
    sem = _sems.get(loop_id)
    if sem is None:
        _sems.clear()
        sem = _sems[loop_id] = asyncio.Semaphore(MAX_CONCURRENT)
    async with sem:
        await check(row)


def _start(row: dict) -> None:
    did = row.get("dispatch_id") or ""
    if not did or did in _inflight:
        return
    _inflight.add(did)
    task = asyncio.get_running_loop().create_task(_run(dict(row)))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    task.add_done_callback(lambda _t, d=did: _inflight.discard(d))


def attach(rows: list[dict], status_of: dict[str, Optional[str]]) -> None:
    """Give each running row the stage and the doing last read (``live_stage``,
    ``live_now``; None for any other row) and start the reads that are due, in the
    background: this poll returns what is kept, the next one sees the new read."""
    for r in rows:
        did = r.get("dispatch_id") or ""
        running = r.get("state") == "running"
        r["live_stage"] = _live.get(did) if running else None
        r["live_now"] = _now.get(did) if running else None
        if not running:
            _live.pop(did, None)
            _now.pop(did, None)
        if due(r, status_of.get(r.get("hive_id"))):
            _start(r)
