"""What the agent does next (r6 §2.3, NE-2 pulled forward): one answer in every CLI
response, so a run never needs a separate ``status`` between stages.

- ``next_step(engine, *, previous_stage=None)`` — a dict ``{stage, status, action,
  command, why, blocking, card, card_text?}``. It reads only the logged events (the
  lifecycle fold), the code facts and checks the engine computes, the run's own files
  and the stage cards: nothing the agent reported about itself.

``action`` is one of ``run`` (call ``command``), ``write_artifact`` (write the stage
artifact, then ``command`` with ``--artifact -``), ``fix`` (``blocking`` names the
failed checks; fix, then ``command``), ``dispatch`` (spawn the review handouts),
``wait`` (an expert, the daemon or a person acts next), ``ask_user`` (ask the pending
decision in the response) or ``none`` (the run has ended). ``card_text`` is included
only when the stage changed in this call (the card replaces ``stage-start``).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from core.run_protocol import checks, lifecycle

_CARDS = Path(__file__).resolve().parents[2] / "skills" / "s_autonomous-pipeline" / "stages"
_ARTIFACT_STAGES = {"evaluate", "plan"}
_CODE_STAGES = {"build"}     # the agent writes and tests the change its own way
# a person stopped the run, or its session went away: only a person resumes it
_HUMAN_PAUSES = {"user_stop", "session_lost"}


def _cmd(engine, rest: str) -> str:
    """The command the agent runs next, with this backend's script by ABSOLUTE path:
    the same command packaged and in dev, wherever the agent's cwd is."""
    from core.worktree_write_gate import v1_command
    args = f"{rest} --project {engine.project} --run-id {engine.run_id}".strip()
    command = v1_command(args).replace("  ", " ")
    if (getattr(engine, "contract", None) or {}).get("owner_source") == "run":
        # a run-level owner (standalone T6): the command carries it, nothing else does
        command = f"SWARM_APP_SESSION_ID={engine.owner_session()} {command}"
    return command


def blocking_checks(engine, state: dict[str, Any]) -> list[dict[str, str]]:
    """The checks of the current stage that would fail now, evaluated without
    recording anything (``checks.evaluate`` is pure)."""
    stage, profile = state["stage"], state["profile"]
    variant = lifecycle.document_variant(profile, stage)
    context = engine.check_context()
    out = []
    for spec in checks.stage_checks(profile, stage, variant):
        passed, detail = checks.evaluate(spec.check_id, context)
        if not passed:
            out.append({"check": spec.check_id, "detail": str(detail)[:300]})
    return out


def _reflect_prepared(events: list[dict[str, Any]], state: dict[str, Any]) -> bool:
    """From logged events only: a DELIVER attempt of exactly the RF checks all passed,
    or REFLECT is degraded (the lessons dropped to zero)."""
    if state.get("reflect") == "degraded":
        return True
    rf = sorted(lifecycle.RF_CHECKS)
    for attempt in events:
        if attempt.get("kind") == "stage_attempt" and attempt.get("stage") == "deliver" \
                and sorted(attempt["payload"].get("check_ids") or []) == rf:
            rows = [e for e in events if e.get("kind") == "check" and e["payload"].get(
                "stage_attempt_event_id") == attempt["event_id"]]
            if len(rows) == len(rf) and all(e["payload"].get("passed") for e in rows):
                return True
    return False


def waiting_summary(events: list[dict[str, Any]], waiting: list[dict[str, Any]]) -> str:
    """One line per unanswered handout: its expert, state and reason, and what to do
    (handout-by-file design §3.5). The spawn prompt itself is in ``handouts``."""
    from core.run_protocol import review
    parts = []
    for handout in waiting:
        s = review.handout_status(events, handout)
        part = f"{s['expert_id']}: {s['state']}" + (f" ({s['reason']})" if s["reason"] else "")
        if s["state"] in ("not_spawned", "spawn_rejected", "answer_rejected"):
            part += " - dispatch its spawn_prompt (in handouts) unchanged"
        parts.append(part)
    return "; ".join(parts)


def waiting_handouts(events: list[dict[str, Any]], stage: str) -> list[dict[str, Any]]:
    """handout_status of each latest unanswered handout of *stage* (per expert and round)."""
    from core.run_protocol import review
    latest: dict[tuple[Any, Any], dict[str, Any]] = {}
    for event in events:
        if event.get("kind") == "handout" and event.get("stage") == stage:
            latest[(event["payload"].get("expert_id"), event.get("round"))] = event
    answered = {e["payload"].get("handout_event_id") for e in events if e.get("kind") == "answer"}
    return [review.handout_status(events, h) for h in latest.values()
            if h["event_id"] not in answered]


def _review_action(engine, events: list[dict[str, Any]], state: dict[str, Any],
                   blocking: list[dict[str, str]], dispatched: int | None) -> tuple[str, str, str]:
    # per expert and round, the latest handout (a re-dispatch replaces an earlier one):
    # handout attempts number dispatches, not stage attempts
    latest: dict[tuple[Any, Any], dict[str, Any]] = {}
    for event in events:
        if event.get("kind") == "handout" and event.get("stage") == "review":
            latest[(event["payload"].get("expert_id"), event.get("round"))] = event
    answered = {e["payload"].get("handout_event_id") for e in events if e.get("kind") == "answer"}
    waiting = [h for h in latest.values() if h["event_id"] not in answered]
    if waiting:
        if dispatched:
            return "dispatch", "", \
                f"spawn the {dispatched} handout(s) in this response in one parallel message"
        return "wait", "", (f"{len(waiting)} dispatched expert(s) have not answered yet: "
                            + waiting_summary(events, waiting))
    # an address no R2 will answer, then an R2 line that used a word its finding does
    # not take (design §2.3 points 3 and 7). RV-5 holds the stage for the first; the
    # second is advice (a HIGH it leaves unconfirmed still blocks landing through RV-2)
    from core.run_protocol import review as review_protocol
    uncovered = review_protocol.uncovered_addresses(events)
    if uncovered:
        return "run", _cmd(engine, "review-address --artifact -"), \
            ("addressed without an R2 handout: " + ", ".join(uncovered) + "; review-address "
             "with {\"pass\": <this pass>, \"items\": []} (or your next pass) dispatches "
             "their R2, then spawn it")
    redo = review_protocol.r2_redispatch_targets(events)
    if redo:
        named = "; ".join(f"{expert}: " + ", ".join(f"{cid} said {word}" for cid, word
                                                    in sorted(wrong.items()))
                          for expert, (_answer, wrong) in sorted(redo.items()))
        return "run", _cmd(engine, "review-address --artifact -"), \
            ("re-dispatch R2 once for the lines that used a word not allowed (" + named
             + "): review-address with {\"pass\": <this pass>, \"items\": []} writes the "
             "new R2 handout, then spawn it")
    if any(row.get("check") == "RV-4" for row in blocking):
        # S6-2: the code changed since a review saw it (TEST sent the run back, or an
        # edit after R2): only the change since then is reviewed
        return "run", _cmd(engine, "review-start"), \
            "the code changed since the last review: review-start hands out an " \
            "incremental review of that change only"
    if not blocking and (latest or dispatched == 0):
        # every answer is in (or this change needs no expert) and the checks pass
        return "run", _cmd(engine, "stage-complete"), \
            "every review check passes"
    if not latest:
        return "run", _cmd(engine, "review-start"), \
            "start the review: deterministic checks, then the expert handouts (if it " \
            "dispatches none, complete the stage)"
    # an expert whose latest answer was not parsed produced no findings: one redispatch
    # (review-start), then the RV checks decide (re-audit 99f96949)
    states = {e["payload"].get("handout_event_id"): e["payload"].get("state")
              for e in events if e.get("kind") == "answer"}
    per_expert: dict[Any, list[dict[str, Any]]] = {}
    for event in events:
        if event.get("kind") == "handout" and event.get("stage") == "review" \
                and event.get("round") == 1:
            per_expert.setdefault(event["payload"].get("expert_id"), []).append(event)
    unusable = [hs for hs in per_expert.values()
                if states.get(hs[-1]["event_id"]) not in ("parsed", "partial")]
    if unusable:
        if any(len(hs) < 2 for hs in unusable):
            return "run", _cmd(engine, "review-start"), \
                "an expert answer could not be parsed: review-start dispatches it once more"
        return "run", _cmd(engine, "stage-complete"), \
            "an expert gave no usable answer twice: the review checks decide the stage"
    return "fix", _cmd(engine, "review-address --artifact -"), \
        ("address the open findings once (cluster by file ±10 lines), prove each "
         "(review-proof, or the test that shows the fix in your next report), then "
         "review-start for R2")


def next_step(engine, *, previous_stage: str | None = None,
              dispatched: int | None = None) -> dict[str, Any]:
    """*dispatched*: how many handouts the engine just created in this call (review-start
    / review-address), the only fact a review step cannot read back from the events
    (an empty roster records nothing)."""
    state = engine.projection()
    status, stage = state["status"], state["stage"]
    out: dict[str, Any] = {"stage": stage, "status": status, "action": "none",
                           "command": "", "why": "", "blocking": []}
    card = _CARDS / f"{stage}.md"
    if card.is_file():
        out["card"] = str(card)
        if previous_stage is not None and previous_stage != stage and status == "active":
            out["card_text"] = card.read_text(encoding="utf-8")
    if status in lifecycle.TERMINAL:
        out["why"] = f"the run is {status}"
        return out
    if status == "awaiting_human":
        out.update(action="ask_user", why="ask the pending decision exactly as given "
                   "(`decision.ask_user_question`); the answer arrives from the person")
        return out
    if status == "reflecting":
        # a landing's own command ends REFLECT; still reflecting means that command
        # stopped before it finished (standalone §4.6)
        out.update(action="run", command=_cmd(engine, "resume"),
                   why="REFLECT did not finish: resume finishes it")
        return out
    if status == "paused":
        reason = state.get("reason") or "see status"
        if reason in _HUMAN_PAUSES:
            out.update(action="wait", why=f"the run is paused ({reason}): resume it only "
                       "when the person asks you to continue")
        else:
            out.update(action="run", command=_cmd(engine, "resume"),
                       why=f"the run is paused ({reason})")
        return out
    if stage in _ARTIFACT_STAGES:
        if stage == "evaluate":
            gate = engine.gate0_status()
            disposition = gate["disposition"] if gate["required"] else None
            if disposition in {"needs_handout", "needs_round_2"}:
                out.update(action="write_artifact",
                           command=_cmd(engine, "gate0-start --artifact -"),
                           why=("Gate-0 is required for this requirement: pass the same "
                                "requirements artifact on stdin and dispatch the returned "
                                "handout `prompt` unchanged" if disposition == "needs_handout"
                                else "Gate-0's first answer did not clear the frame: start "
                                "its second and last round the same way"))
                return out
            if disposition == "awaiting_answer":
                out.update(action="wait", why="wait for the Gate-0 answer; dispatch its "
                           "`spawn_prompt` again if it was never spawned")
                out["handouts"] = waiting_handouts(engine._since_last_answer(), "evaluate")
                return out
        out.update(action="write_artifact",
                   command=_cmd(engine, "stage-complete --artifact -"),
                   why=f"write the {stage} artifact the card describes and pass it on stdin")
        return out
    if stage == "deliver":
        if not _reflect_prepared(engine.store.read_events(), state):
            out.update(action="write_artifact",
                       command=_cmd(engine, "reflect-prepare --artifact -"),
                       why="record the run's lessons (zero is allowed) before landing")
        elif lifecycle.deliver_route(state) == "finish":
            # nothing lands: research (no code) or the not-landed path (two-ends §5)
            out.update(action="run", command=_cmd(engine, "stage-complete"),
                       why=("a research run lands nothing: finish DELIVER (it ends no_code)"
                            if not lifecycle.caps(state["profile"]).lands_code else
                            "the run does not land (" + str(state.get("reason") or "not landed")
                            + "): finish DELIVER; the REPORT says why"))
        else:
            # LG-1 (S6-1): after main moved and the worktree was synced, the TEST report
            # is for the old tree; test the new one before landing again
            passed, detail = checks.evaluate("LG-1", engine.check_context())
            if not passed:
                out.update(action="write_artifact",
                           command=_cmd(engine, "report-tests --artifact -"),
                           why="the code changed since the TEST report (" + str(detail)[:200]
                           + "): run the tests on the current tree and send the report, "
                           "then land")
            else:
                out.update(action="run", command=_cmd(engine,
                                                      'land --message "<commit message>"'),
                           why="land the run")
        return out
    blocking = blocking_checks(engine, state)
    out["blocking"] = blocking
    if stage == "review":
        events = engine.store.read_events()
        action, command, why = _review_action(engine, events, state, blocking, dispatched)
        out.update(action=action, command=command, why=why)
        if action == "wait":
            out["handouts"] = waiting_handouts(events, "review")
        return out
    if stage in _CODE_STAGES or stage == "test":
        # S6-1: the agent tests natively and ends the stage with its validation report
        out.update(action="write_artifact",
                   command=_cmd(engine, "stage-complete --artifact -"),
                   why=("write and test the change your own way, "
                        "then pass your report on stdin: "
                        '{"reports": [{"repo", "commands": [{"command", "cwd", "result": '
                        '"pass|fail|not_run", "reason"}], "tests_changed", "rationale"}]}'
                        if stage in _CODE_STAGES else
                        "run the final verification on this tree yourself (do not change "
                        "code in TEST), then pass the report on stdin in the same shape"))
        return out
    if not blocking:
        out.update(action="run", command=_cmd(engine, "stage-complete"),
                   why=f"every {stage} check passes")
        return out
    out.update(action="fix", command=_cmd(engine, "stage-complete"),
               why=f"{len(blocking)} {stage} check(s) do not pass yet")
    return out


__all__ = ["blocking_checks", "next_step"]
