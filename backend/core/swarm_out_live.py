"""The stage a Hive pipeline run is in while it runs, read on this host.

The result checks (core/swarm_out_results) read a run's session once it has stopped. A
pipeline task works in one long turn, so while it runs the host would know nothing of
where it stands. The Hive persists the turn as it goes, including the backend-written
``pipeline_run`` and ``pipeline_stage`` blocks (core/pipeline_stage_events), and its
messages API returns that in-progress turn. This module reads the newest turn of a
running pipeline task, at most once per ``LIVE_STAGE_S`` and at most ``MAX_BODY_BYTES``
(streamed, parsed off the event loop), and keeps the newest stage of
the task's run in memory; ``attach`` gives it to the /swarm-out/runs rows that run.

Why memory, not the kept result record: the result check loads, edits and saves that
file, and a second writer would lose one of the two updates; a live stage is stale
within minutes anyway. A restarted daemon reads it again on the next poll.

Everything read from the Hive is data: only a block's ``runId``, ``stage``, ``status``,
``next`` and ``at`` (and a ``pipeline_run`` block's ``stages``) are taken, each checked
for type and length; a stage outside the pipeline's stages is dropped. A Hive that does
not answer, or answers with an error, leaves nothing from that check. Nothing here
raises into the poll.

- ``live_stage_of(messages, run_ids)`` — pure: the newest stage of one of the runs.
- ``due(row, hive_status)`` — a running pipeline task on a running Hive, not read lately.
- ``check(row)`` — read the Hive once (async; never raises).
- ``attach(rows, status_of)`` — add ``live_stage`` to the running rows, start the checks due.
"""
from __future__ import annotations

import asyncio
import json
import logging
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

_live: dict[str, dict[str, Any]] = {}       # dispatch_id -> what the last check found
_checked: dict[str, float] = {}             # dispatch_id -> monotonic time of the last check
_inflight: set[str] = set()
_tasks: set[asyncio.Task] = set()
_sems: dict[int, asyncio.Semaphore] = {}


def _s(v: Any, n: int) -> Optional[str]:
    return v[:n] if isinstance(v, str) and v else None


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
    """A pipeline task that runs now on a running Hive, with a session, not read in the
    last ``LIVE_STAGE_S``."""
    agent = row.get("project_agent")
    if hive_status != "running" or row.get("state") != "running" or not isinstance(row.get("session_id"), str):
        return False
    if not (isinstance(agent, dict) and agent.get("execution") == "pipeline"):
        return False
    last = _checked.get(row.get("dispatch_id") or "")
    return last is None or (now if now is not None else time.monotonic()) - last >= LIVE_STAGE_S


async def check(row: dict) -> Optional[dict[str, Any]]:
    """Read the run's newest turn from its Hive once; keep and return the stage found
    (None when the Hive did not answer properly or holds no stage of the run)."""
    from core import swarm_out
    did, sid = row.get("dispatch_id") or "", row.get("session_id")
    _checked[did] = time.monotonic()
    if not isinstance(sid, str) or not swarm_out.KEY_RE.fullmatch(sid):
        return None
    try:
        from core import swarm_out_remote
        inst = await swarm_out._instance(row.get("hive_id") or "")
        async with swarm_out._client(inst) as client:
            # streamed and capped: a long pipeline turn is never buffered past the cap
            async with client.stream("GET", f"/api/chat/sessions/{sid}/messages", params={"turns": 1}) as r:
                if r.status_code != 200:
                    return None
                raw = await swarm_out_remote.read_capped(r, MAX_BODY_BYTES)
        ids = _run_ids(row)
        # parsing a whole turn is CPU work: off the event loop, with the scan
        found = await asyncio.to_thread(lambda: live_stage_of(json.loads(raw), ids))
    except Exception as e:  # noqa: BLE001 -- a live stage is a hint: the poll never fails on it
        logger.info("swarm_out_live: %s not read (%s)", did, type(e).__name__)
        return None
    if found:
        _live[did] = {**found, "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
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
    """Give each running row the stage last read (``live_stage``, None for any other
    row) and start the reads that are due, in the background: this poll returns what
    is kept, the next one sees the new stage."""
    for r in rows:
        did = r.get("dispatch_id") or ""
        r["live_stage"] = _live.get(did) if r.get("state") == "running" else None
        if r.get("state") != "running":
            _live.pop(did, None)
        if due(r, status_of.get(r.get("hive_id"))):
            _start(r)
