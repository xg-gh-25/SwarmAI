"""Pipeline v1 human loop, daemon side (docs/design/pipeline-v1-human-loop.md).

Everything a PERSON does to a v1 run lands here, in the daemon process, never in
the agent's CLI: answering a decision, stopping, closing the owner's tab, taking a
run over in another tab.

Public symbols:
- ``load_engine`` — a v1 RunEngine by run id, or None for a missing / pre-v1 run.
- ``owned_runs`` — non-terminal v1 runs a session currently owns.
- ``owner_live`` — whether a session is streaming / waiting for input right now.
- ``record_card_answer`` — record a person's AskUserQuestion pick as ``human_answer``.
- ``answer_from_dashboard`` — record a dashboard decision click.
- ``continue_prompt`` — the message that hands a run back to its owner session.
- ``on_user_stop`` / ``on_session_lost`` — pause the session's active runs.
- ``record_owner`` — sign a takeover as an ``owner_transfer`` event.

Every function is fail-safe for the chat path: an error is logged and reported as
"nothing recorded", never raised into a streaming turn.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_TERMINAL = frozenset({"completed", "abandoned", "failed"})


def _workspace() -> Path:
    from jobs.paths import SWARMWS
    return Path(SWARMWS)


def load_engine(run_id: str, workspace: Optional[Path] = None):
    """The run's engine from its protected contract, or None (missing / pre-v1).
    *workspace* defaults to SwarmWS (the dashboard router passes its own)."""
    from config import get_app_data_dir
    from core.run_protocol.engine import RunEngine, find_run_dir
    from core.run_protocol.contract import is_v1_run

    data_dir = get_app_data_dir()
    if not is_v1_run(data_dir, run_id):
        return None
    workspace = Path(workspace) if workspace is not None else _workspace()
    run_dir = find_run_dir(workspace, run_id)
    engine = RunEngine.load(data_dir=data_dir, workspace=workspace,
                            project=run_dir.parents[2].name, run_id=run_id)
    if engine.run_dir.resolve() != run_dir.resolve():
        raise ValueError("v1 run workspace identity mismatch")
    return engine


def owned_runs(session_id: str, status: Optional[str] = None) -> list[str]:
    """Run ids of non-terminal v1 runs whose projected owner is *session_id* (and,
    with *status*, whose projected status is that one).

    run.json is only the projection; callers re-check the owner on the engine.
    Reads through core.run_index (a file is parsed again only when it changed);
    blocking: call it off the event loop."""
    if not session_id:
        return []
    from core import run_index
    out = []
    for rf in run_index.runs(_workspace()):
        data = rf.data
        if rf.path.parent.name.startswith("run_") and data.get("session_id") == session_id \
                and data.get("status") not in _TERMINAL and "decision_cap" in data \
                and (status is None or data.get("status") == status):
            out.append(rf.path.parent.name)
    return sorted(out)


def owner_live(session_id: str) -> Optional[bool]:
    """True while the session streams or waits for input; None when unknown."""
    try:
        from core import session_registry
        from core.session_unit import SessionState
        router = session_registry.session_router
        if router is None:
            return None
        unit = router.get_unit(session_id)
        return unit is not None and unit.state in (SessionState.STREAMING,
                                                   SessionState.WAITING_INPUT)
    except Exception:  # noqa: BLE001 — liveness is advisory
        logger.debug("pipeline_human: owner liveness unknown", exc_info=True)
        return None


def _owned_engines(session_id: str):
    for run_id in owned_runs(session_id):
        try:
            engine = load_engine(run_id)
        except Exception:  # noqa: BLE001
            logger.warning("pipeline_human: cannot load run %s", run_id, exc_info=True)
            continue
        if engine is not None and engine.owner_session() == session_id:
            yield engine


def record_required_approval(session_id: str) -> list[str]:
    """A person approved a dangerous command or an external send in this session: each
    v1 run it owns counts one required approval (design §E9.3, D4). A system boundary,
    not an autonomy failure. Never raises: the approval itself already happened."""
    counted = []
    for engine in _owned_engines(session_id):
        try:
            state = engine.projection()
            events = engine.store.read_events()
            attempt = 1 + sum(e.get("kind") == "metric"
                              and e.get("payload", {}).get("name") == "required_approval"
                              for e in events)
            engine.store.append_new(
                writer=engine.authority.writer("hook"), kind="metric",
                at=__import__("core.run_protocol.engine", fromlist=["_now"])._now(),
                stage=str(state.get("stage") or "deliver"), attempt=attempt,
                payload={"name": "required_approval", "value": 1})
            counted.append(engine.run_id)
        except Exception:  # noqa: BLE001 — telemetry never undoes an approval
            logger.warning("pipeline_human: approval of %s not counted", engine.run_id,
                           exc_info=True)
    return counted


def record_card_answer(session_id: str, answers: Any) -> list[dict[str, str]]:
    """A person answered an AskUserQuestion card in *session_id*: record the pick
    for each run this session owns whose pending decision that card asked."""
    from core.run_protocol import decision as decision_protocol
    recorded = []
    try:
        for engine in _owned_engines(session_id):
            pending = engine.pending_decision()
            if pending is None:
                continue
            value = engine.decision_value(pending["request_id"])
            option_id = decision_protocol.option_from_answer(value or {}, answers)
            if option_id is None:
                continue
            engine.answer_decision(request_id=pending["request_id"], option_id=option_id)
            recorded.append({"run_id": engine.run_id, "request_id": pending["request_id"],
                             "option_id": option_id})
            logger.info("pipeline_human: decision %s of %s answered on the chat card: %s",
                        pending["request_id"], engine.run_id, option_id)
    except Exception:  # noqa: BLE001 — never break the answer path
        logger.warning("pipeline_human: recording a card answer failed", exc_info=True)
    return recorded


def answer_from_dashboard(run_id: str, request_id: str, option_id: str,
                          workspace: Optional[Path] = None) -> dict[str, Any]:
    """Record a dashboard decision click. Raises on a stale or unknown request."""
    engine = load_engine(run_id, workspace)
    if engine is None:
        raise LookupError(run_id)
    result = engine.answer_decision(request_id=request_id, option_id=option_id)
    logger.info("pipeline_human: decision %s of %s answered on the dashboard: %s",
                request_id, run_id, option_id)
    return {"state": result["state"], "answer": result["answer"],
            "owner_session_id": engine.owner_session()}


def continue_prompt(run_id: str, project: str) -> str:
    """The prompt put in the owner session to continue a run: it names the real status
    command (core.pipeline_command via worktree_write_gate.v1_command)."""
    from core.worktree_write_gate import v1_command
    status = v1_command(f"status --project {project} --run-id {run_id}")
    return (f"Continue pipeline run {run_id} (project {project}): run `{status}` first, "
            "then carry on from its current stage.")


def _stop(session_id: str, kind: str, reason: str) -> list[str]:
    paused = []
    try:
        for engine in _owned_engines(session_id):
            if engine.record_stop(kind, reason=reason):
                paused.append(engine.run_id)
                logger.info("pipeline_human: %s paused %s", kind, engine.run_id)
    except Exception:  # noqa: BLE001
        logger.warning("pipeline_human: recording %s failed", kind, exc_info=True)
    return paused


def on_user_stop(session_id: str) -> list[str]:
    """The user pressed Stop in *session_id*: pause its active runs."""
    return _stop(session_id, "user_stop", "user pressed Stop")


def on_session_lost(session_id: str) -> list[str]:
    """The owner's tab was closed: pause its active runs."""
    return _stop(session_id, "session_lost", "owner session closed")


def record_owner(run_id: str, session_id: str) -> bool:
    """Sign *session_id* as the run's owner (``owner_transfer``) after run_owner_gate
    decided the takeover (the old owner is not streaming). Without this event the
    engine would project the old owner again and keep every human authorization
    bound to it. False when nothing changed or the run has no v1 contract."""
    try:
        engine = load_engine(run_id)
        if engine is None:
            return False
        moved = engine.transfer_owner(session_id, reason="takeover")
        if moved:
            logger.info("pipeline_human: %s now owned by %s", run_id, session_id)
        return moved
    except Exception:  # noqa: BLE001 — the gate re-checks the owner afterwards
        logger.warning("pipeline_human: owner transfer of %s failed", run_id, exc_info=True)
        return False
