"""review_round_gate — PreToolUse (Agent|Task) gate: 再一再二不再三.

A pipeline stage gives each review expert at most ``MAX_ROUNDS`` (3) completed
rounds: round 1 (full review), round 2 (re-check of fix 1), round 3 (re-check of
fix 2). A spawn of an expert that has already completed 3 rounds in the CURRENT
stage of THIS session's active run is denied, with the instruction to stop fixing
and run one fresh-context root-cause review instead; whatever it answers, the run
commits locally with the open findings in REPORT §10 and push_ready false (no redesign
since 2026-10-19; docs/design/pipeline-review-convergence.md §3). The user is not asked.
The root-cause review itself (EXPERT-ID ``root-cause``) is allowed once per cycle, and
a recorded redesign (run.json ``redesigns``) starts a new cycle with a fresh count. It used to be an allowance that stage-complete checked after the
rounds had run, waived by any written ``over_budget_reasons`` entry: run_d8762bc8
ran 7 Gate-2 rounds, run_5d894e22 9 red-team rounds.

Counting matches artifact_cli._completed_spawns: SubagentStop markers in
``<app data>/state/pipeline_agent_audit`` with this run's id and this EXPERT-ID,
inside the stage's own time windows (a re-opened stage keeps its count). The
expert comes from the spawn prompt's first ``EXPERT-ID:`` line
(runtime_hooks._extract_expert_id). goal_cycle is exempt (its allowance scales
with cycles). Operator bypass: SWARM_REVIEW_ROUND_GATE_FORCE=1 in the backend env.
Fail-open on any error of its own.

It also keeps REVIEW / DELIVER rounds parallel (XG 2026-10-19), by prevention rather
than after the fact: when a member of the current wave (REVIEW's ``review_experts``,
DELIVER round 1, or a ``gate2-recheck`` wave in run.json ``gate2_waves``) has already
COMPLETED and another member is now spawned on its own while ≥2 members are still to
run, the spawn is denied ONCE per wave with the list of the remaining members to spawn
together in one message. Calls of one message reach this hook before any of them
completes, so a parallel batch is never denied; at worst a wave runs in two batches.

Public: ``create_review_round_gate``, ``rounds_used``, ``wave_split``, ``MAX_ROUNDS``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MAX_ROUNDS = 3
ROOT_CAUSE_ID = "root-cause"
_ROOT_CAUSE_DENY = (
    "DENY (再一再二不再三): the root-cause review already ran in the {stage} stage of pipeline "
    "run {run_id}; there is one per review cycle. Do not spawn it again: run `stage-complete "
    "--stage deliver`. Whatever it answered (SOUND, WRONG-APPROACH, or no verdict line), "
    "the run commits locally with push_ready false and the open items in REPORT §10. "
    "Do not ask the user.")
_EXEMPT_STAGES = frozenset({"goal_cycle"})
_SCAN_TIMEOUT = 3.0  # under the hook chain's 5 s per-hook cap


def _epoch(value) -> "float | None":
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _open_stage(timing: dict) -> "tuple[str, list[tuple[float, float]]] | None":
    """The stage that is open now (started, not completed; the latest if several)
    and its own windows — the same rule as artifact_cli._stage_windows."""
    now = time.time()
    best = None
    for stage, t in (timing or {}).items():
        if not isinstance(t, dict) or t.get("completed_at"):
            continue
        lo = _epoch(t.get("started_at"))
        if lo and (best is None or lo > best[1]):
            best = (stage, lo, t)
    if best is None:
        return None
    stage, _lo, t = best
    windows: list[tuple[float, float]] = []
    for w in t.get("prior_windows") or []:
        a = _epoch((w or {}).get("started_at")) if isinstance(w, dict) else None
        b = _epoch((w or {}).get("completed_at")) if isinstance(w, dict) else None
        if a and b and b >= a:
            windows.append((a, b))
    start = _epoch(t.get("started_at") if windows else (t.get("first_started_at") or t.get("started_at")))
    if start:
        windows.append((start, now))
    return stage, windows


def _audit_dir() -> Path:
    from config import get_app_data_dir
    return get_app_data_dir() / "state" / "pipeline_agent_audit"


def rounds_used(session_id: str, expert_id: str) -> "dict | None":
    """``{"run_id", "project", "stage", "used"}`` for *expert_id* in the open stage of
    this session's active run, or None (no active run, no open stage, unreadable)."""
    from core.security_hooks import _pipeline_runs_root, _session_run_state
    own = _session_run_state(session_id)
    if not own:
        return None
    rf = (Path(_pipeline_runs_root()) / "Projects" / own["project"] / ".artifacts" / "runs"
          / own["id"] / "run.json")
    try:
        state = json.loads(rf.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    opened = _open_stage(state.get("stage_timing") if isinstance(state, dict) else None)
    if not opened:
        return None
    stage, windows = opened
    if not windows:
        return None
    audit = _audit_dir()
    if not audit.is_dir():
        return None
    earliest = min(lo for lo, _hi in windows)
    # A root-cause redesign starts a new review cycle with a fresh allowance.
    redesign = max((float(r.get("ts") or 0.0) for r in state.get("redesigns") or []
                    if isinstance(r, dict)), default=0.0)
    floor = max(earliest, redesign)
    used = 0
    for p in audit.glob("session_*.marker"):
        if "_adv_" in p.name:
            continue  # the adversarial twin of a marker already counted
        try:
            if p.stat().st_mtime < earliest - 60:
                continue
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or data.get("run_id") != own["id"] \
                or data.get("expert_id") != expert_id:
            continue
        ts = data.get("ts")
        if isinstance(ts, (int, float)) and ts >= floor and any(lo <= ts <= hi for lo, hi in windows):
            used += 1
    return {"run_id": own["id"], "project": own["project"], "stage": stage, "used": used}


_NOT_IN_WAVE = frozenset({"meta-review", ROOT_CAUSE_ID})
_WAVE_DENIED: "dict[tuple[str, float], float]" = {}  # (run_id, wave at) → when; bounded below


def wave_split(session_id: str, expert_id: str) -> "dict | None":
    """``{"run_id", "stage", "remaining", "wave_at"}`` when spawning *expert_id* now would
    split the current REVIEW / DELIVER wave (another member already completed, ≥2 still
    to run, not yet denied for this wave), else None."""
    from core.security_hooks import _pipeline_runs_root, _session_run_state
    own = _session_run_state(session_id)
    if not own:
        return None
    rf = (Path(_pipeline_runs_root()) / "Projects" / own["project"] / ".artifacts" / "runs"
          / own["id"] / "run.json")
    try:
        state = json.loads(rf.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    opened = _open_stage(state.get("stage_timing") if isinstance(state, dict) else None)
    if not opened or opened[0] not in ("review", "deliver") or not opened[1]:
        return None
    stage, windows = opened
    lo, now = windows[-1]
    plan = state.get("dispatch_plan") if isinstance(state.get("dispatch_plan"), dict) else {}
    waves = [{"at": lo, "experts": plan.get(stage) or []}]
    if stage == "deliver":
        waves += sorted((w for w in state.get("gate2_waves") or [] if isinstance(w, dict)
                         and isinstance(w.get("at"), (int, float)) and lo <= w["at"] <= now),
                        key=lambda w: w["at"])
    wave = waves[-1]
    members = {e for e in wave.get("experts") or [] if isinstance(e, str)} - _NOT_IN_WAVE
    if expert_id not in members or len(members) < 3:
        return None
    key = (own["id"], float(wave["at"]))
    if key in _WAVE_DENIED:
        return None
    audit = _audit_dir()
    if not audit.is_dir():
        return None
    done: set = set()
    t_from = float(wave["at"]) - 5
    for p in audit.glob("session_*.marker"):
        if "_adv_" in p.name:
            continue
        try:
            if p.stat().st_mtime < t_from:
                continue
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and d.get("run_id") == own["id"] \
                and isinstance(d.get("ts"), (int, float)) and d["ts"] >= t_from:
            done.add(d.get("expert_id"))
    if expert_id in done or not (members & done):
        return None
    remaining = sorted(members - done)
    if len(remaining) < 2:
        return None
    return {"run_id": own["id"], "stage": stage, "remaining": remaining, "wave_at": key[1]}


def _deny_reason(expert_id: str, info: dict) -> str:
    from core.gate2_fixspec import FIXSPEC_SKEPTIC_ID
    if expert_id == FIXSPEC_SKEPTIC_ID:
        return (
            f"DENY (再一再二不再三): the fixing-spec skeptic has already run {info['used']} times "
            f"in the {info['stage']} stage of pipeline run {info['run_id']}, every spec "
            "submission this cycle allows. Do not ask the user and do not hand-craft a spawn: "
            "run `gate2-fixspec` with the round's spec — it returns next=root_cause, and "
            "gate2-recheck then hands out the ONE root-cause review.")
    if info.get("stage") != "deliver":
        # REVIEW (and any non-DELIVER stage) has no root-cause machinery: record and go on.
        return (
            f"DENY (再一再二不再三): '{expert_id}' has already completed {info['used']} review "
            f"rounds in the {info['stage']} stage of pipeline run {info['run_id']}. There is "
            f"no round {MAX_ROUNDS + 1}. STOP the fix loop and do not ask the user to decide. "
            "Record every finding still open (file:line, what is unmet, what each fix "
            "changed) as open items in this stage's artifact and the DELIVER "
            "attention_flags (REPORT §10, completion summary), then complete this stage and "
            "continue the run; DELIVER commits locally with push_ready false.")
    return (
        f"DENY (再一再二不再三): '{expert_id}' has already completed {info['used']} review "
        f"rounds in the {info['stage']} stage of pipeline run {info['run_id']} (round 1 "
        f"review, round 2 and round 3 re-checks). There is no round {MAX_ROUNDS + 1}, and no "
        "reason, --force or new prompt opens one. STOP the fix loop and do not ask the user "
        "to decide. Run ONE root-cause review instead: spawn a fresh sub-agent whose prompt "
        "starts with `EXPERT-ID: root-cause`, give it the requirement, the PLAN, every "
        "round's findings and what each fix introduced, and have it answer on its first line "
        "`ROOT-CAUSE VERDICT: SOUND` (the approach is right; what is left is edge cases) or "
        "`ROOT-CAUSE VERDICT: WRONG-APPROACH` (fixing on top of this approach will not "
        "converge), with its reasons. Either way: stop fixing, write every open finding with "
        "file:line and the verdict with its reasons into REPORT §10, complete DELIVER with "
        "push_ready false and let run-commit commit locally (no redesign, no round 4)."
    )


def cap_for(expert_id: str) -> int:
    """Completed spawns *expert_id* may have per review cycle of a stage: the root-cause
    review once; the fixing-spec skeptic up to core.gate2_fixspec.MAX_SUBMISSIONS per
    Gate-2 round over MAX_ROUNDS + 1 global rounds (a reviewer that joins in round 2, the
    Red Team, can take its MAX_ROUNDS to global round MAX_ROUNDS + 1); every reviewer
    MAX_ROUNDS."""
    from core.gate2_fixspec import FIXSPEC_SKEPTIC_ID, MAX_SUBMISSIONS
    if expert_id == ROOT_CAUSE_ID:
        return 1
    if expert_id == FIXSPEC_SKEPTIC_ID:
        return MAX_SUBMISSIONS * (MAX_ROUNDS + 1)
    return MAX_ROUNDS


def create_review_round_gate(session_context: dict[str, Any]):
    """Factory: the PreToolUse (Agent|Task) round gate. session_context is the
    mutable dict SessionUnit updates (``sdk_session_id`` read at call time)."""
    from core import executors
    from core.runtime_hooks import _extract_expert_id

    async def _gate(input_data: dict[str, Any], tool_use_id: "str | None",
                    context: Any) -> dict[str, Any]:
        if input_data.get("tool_name") not in ("Agent", "Task"):
            return {"decision": "approve"}
        prompt = str((input_data.get("tool_input") or {}).get("prompt") or "")
        expert_id = _extract_expert_id(prompt[:2000])
        if not expert_id:
            return {"decision": "approve"}  # not a pipeline expert spawn
        session_id = session_context.get("sdk_session_id", "") or ""
        if not session_id:
            return {"decision": "approve"}
        try:
            info = await asyncio.wait_for(
                executors.run_in("subprocess", rounds_used, session_id, expert_id),
                timeout=_SCAN_TIMEOUT)
        except Exception:  # noqa: BLE001 — incl. TimeoutError: the gate never blocks on itself
            logger.warning("review_round_gate: scan failed — failing open", exc_info=True)
            return {"decision": "approve"}
        cap = cap_for(expert_id)
        if not info or info["stage"] in _EXEMPT_STAGES or info["used"] < cap:
            return await _wave_check(session_id, expert_id)
        if os.environ.get("SWARM_REVIEW_ROUND_GATE_FORCE") == "1":
            logger.warning("[review-round-gate] FORCE override: %s round %d in %s/%s",
                           expert_id, info["used"] + 1, info["run_id"], info["stage"])
            return {"decision": "approve"}
        logger.warning("[BLOCKED] review_round_gate: %s round %d in %s/%s", expert_id,
                       info["used"] + 1, info["run_id"], info["stage"])
        reason = (_ROOT_CAUSE_DENY.format(**info) if expert_id == ROOT_CAUSE_ID
                  else _deny_reason(expert_id, info))
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }}

    async def _wave_check(session_id: str, expert_id: str) -> dict[str, Any]:
        if os.environ.get("SWARM_REVIEW_ROUND_GATE_FORCE") == "1":
            return {"decision": "approve"}
        try:
            split = await asyncio.wait_for(
                executors.run_in("subprocess", wave_split, session_id, expert_id),
                timeout=_SCAN_TIMEOUT)
        except Exception:  # noqa: BLE001 — never blocks on itself
            logger.warning("review_round_gate: wave scan failed — failing open", exc_info=True)
            return {"decision": "approve"}
        if not split:
            return {"decision": "approve"}
        _WAVE_DENIED[(split["run_id"], split["wave_at"])] = time.time()
        while len(_WAVE_DENIED) > 256:
            _WAVE_DENIED.pop(next(iter(_WAVE_DENIED)))
        logger.warning("[BLOCKED] review_round_gate: serial spawn of %s in %s/%s",
                       expert_id, split["run_id"], split["stage"])
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"DENY (parallel review): '{expert_id}' is being spawned on its own after "
                f"another expert of this {split['stage']} round already finished. A round runs "
                "in parallel: spawn ALL of these remaining experts NOW in ONE message (one "
                "Agent call each, same reply): " + ", ".join(split["remaining"])
                + ". This is asked once per round; the batch you send next is allowed."),
        }}

    return _gate
