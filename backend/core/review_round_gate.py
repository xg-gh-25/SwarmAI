"""review_round_gate — PreToolUse (Agent|Task) gate: 再一再二不再三.

A pipeline stage gives each review expert at most ``MAX_ROUNDS`` (3) completed
rounds: round 1 (full review), round 2 (re-check of fix 1), round 3 (re-check of
fix 2). A spawn of an expert that has already completed 3 rounds in the CURRENT
stage of THIS session's active run is denied, with the instruction to stop and
hand the decision to the user (docs/design/pipeline-review-convergence.md §3,
「N3 规则定稿」). It used to be an allowance that stage-complete checked after the
rounds had run, waived by any written ``over_budget_reasons`` entry: run_d8762bc8
ran 7 Gate-2 rounds, run_5d894e22 9 red-team rounds.

Counting matches artifact_cli._completed_spawns: SubagentStop markers in
``<app data>/state/pipeline_agent_audit`` with this run's id and this EXPERT-ID,
inside the stage's own time windows (a re-opened stage keeps its count). The
expert comes from the spawn prompt's first ``EXPERT-ID:`` line
(runtime_hooks._extract_expert_id). goal_cycle is exempt (its allowance scales
with cycles). Operator bypass: SWARM_REVIEW_ROUND_GATE_FORCE=1 in the backend env.
Fail-open on any error of its own.

Public: ``create_review_round_gate``, ``rounds_used``, ``MAX_ROUNDS``.
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
        if isinstance(ts, (int, float)) and any(lo <= ts <= hi for lo, hi in windows):
            used += 1
    return {"run_id": own["id"], "project": own["project"], "stage": stage, "used": used}


def _deny_reason(expert_id: str, info: dict) -> str:
    return (
        f"DENY (再一再二不再三): '{expert_id}' has already completed {info['used']} review "
        f"rounds in the {info['stage']} stage of pipeline run {info['run_id']} (round 1 "
        f"review, round 2 and round 3 re-checks). There is no round {MAX_ROUNDS + 1}, and no "
        "reason, --force or new prompt opens one. STOP the fix loop and hand the decision to "
        "the user in this conversation, with: (1) the blocking findings still open, each "
        "with file:line; (2) what each fix round introduced; (3) three options — accept the "
        "remaining risk and commit (recorded as an intervention), split the open items into "
        "a follow-up run, or redesign (back to PLAN). Then wait for the answer. Non-blocking "
        "findings go to REPORT §10, not into another round."
    )


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
        if not info or info["stage"] in _EXEMPT_STAGES or info["used"] < MAX_ROUNDS:
            return {"decision": "approve"}
        if os.environ.get("SWARM_REVIEW_ROUND_GATE_FORCE") == "1":
            logger.warning("[review-round-gate] FORCE override: %s round %d in %s/%s",
                           expert_id, info["used"] + 1, info["run_id"], info["stage"])
            return {"decision": "approve"}
        logger.warning("[BLOCKED] review_round_gate: %s round %d in %s/%s", expert_id,
                       info["used"] + 1, info["run_id"], info["stage"])
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": _deny_reason(expert_id, info),
        }}

    return _gate
