"""Pipeline v1 commands (design usable-by-anyone §3.3.5, S6-3).

``pipeline-v1`` (``cli_main``) runs them in the agent's own process, daemon up or not.
Every response is one JSON object; no command pushes or deploys. There is no answer
command for a person: a human decision is recorded by the daemon (chat question card or
dashboard), never by the agent. ``review-answer`` hands over an EXPERT's reply, only on
a run created where no host hook reads experts (standalone T6).

Who runs a command is the request's session (``request_ctx``: the CLI builds it from
``SWARM_APP_SESSION_ID``, else Claude Code's session id). Without either, create makes a
run-level owner id and every ``next.command`` carries it.

- ``parser()`` — the CLI's argparse definition.
- ``run(args)`` — (result, exit code) of one command, as the CLI prints it.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import os
from pathlib import Path
from typing import Any

from config import get_app_data_dir, get_swarmws
from core.run_protocol import lifecycle, request_ctx
from core.run_protocol.engine import EngineError, RunEngine
from core.run_protocol.landing import LandingError


def _workspace() -> Path:
    return get_swarmws()


def _repos(values: list[str]) -> dict[str, str]:
    out = {}
    for value in values:
        name, sep, path = value.partition("=")
        if not sep or not name or not path or not Path(path).is_absolute():
            # relative = the daemon's cwd here; the CLI makes it absolute (B-e MED)
            raise ValueError("--repo must be name=/absolute/path")
        resolved = Path(path).resolve()
        if not (resolved / ".git").exists():
            raise ValueError(f"--repo is not a git worktree: {path}")
        # a run needs a base commit (its worktree, landing checks, ff-only landing):
        # an unborn repo is not supported (spec usable-by-anyone §5.2, r29)
        head = subprocess.run(["git", "-C", str(resolved), "rev-parse", "--verify", "-q",
                               "HEAD^{commit}"], capture_output=True, text=True, timeout=30)
        if head.returncode != 0:
            raise ValueError(
                f"--repo {name} has no commit yet, and a pipeline run needs one to start "
                f"from. Make an initial commit first (for example `git -C {resolved} commit "
                "--allow-empty -m init`), then run create again.")
        out[name] = str(resolved)
    return out


def _json_arg(value: str | None) -> dict[str, Any]:
    """``--artifact``: ``-`` is the request's stdin, anything else the JSON itself. A
    file name never reaches here: the CLI (agent cwd) reads the file and sends it as
    stdin; the daemon opening a path would resolve it in ITS cwd (B-e re-audit MED).
    The command that consumes it keeps it: a document stores its JSON source in the
    run, an event its payload."""
    if value is None:
        return {}
    try:
        raw = _request_stdin() if value == "-" else value
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"artifact is not JSON (a file must exist where the command "
                         f"runs): {exc}") from exc
    if not isinstance(result, dict):
        raise ValueError("artifact must be a JSON object")
    return result


def _request() -> request_ctx.Request:
    req = request_ctx.current()
    if req is None:
        raise ValueError('no pipeline request in this process (run "$SWARM_BACKEND_BIN" pipeline-v1)')
    return req


def _request_stdin() -> str:
    stdin = _request().stdin
    if stdin is None:
        raise ValueError("--artifact - needs the JSON on stdin")
    return stdin


def _this_transcript() -> list[str]:
    """The sending CLI process's SDK transcript id (CLAUDE_CODE_SESSION_ID): the main
    agent's usage is measured from it (D3). Not an SDK id -> nothing."""
    from core.session_ids import valid_session_id
    return [sid for sid in _request().transcript_ids if valid_session_id(sid)]


def _load(args, *, note: bool = True) -> RunEngine:
    engine = RunEngine.load(data_dir=get_app_data_dir(), workspace=_workspace(),
                            project=args.project, run_id=args.run_id)
    if note:   # a write: only under the operation lock (r6 §2.2)
        _note_transcripts(engine)
    return engine


def _note_transcripts(engine: RunEngine) -> None:
    for sid in _this_transcript():     # a resumed / taken-over session adds its own
        engine.note_transcript(sid)


def _not_owner(engine: RunEngine, run_id: str) -> "dict | None":
    owner = engine.owner_session()
    if _request().session_id == owner:
        return None
    return {"ok": False, "code": "not_owner", "error":
            f"run {run_id} now belongs to session {owner}; this command changed nothing"}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="pipeline-v1")
    sub = root.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--project", required=True,
                        help="the run's project; it must be the one a bound --repo names")
    create.add_argument("--profile", default="direct")
    create.add_argument("--session-id", default=None)
    create.add_argument("--repo", action="append", default=[])
    create.add_argument("--run-id")
    create.add_argument("--transcript-id", action="append", default=[])
    create.add_argument("--requirement",
                        help="the user's request, verbatim (required for --profile direct)")
    for command in ("status", "review-start", "land",
                    "events", "findings", "resume", "restore"):
        item = sub.add_parser(command)
        item.add_argument("--project", required=True)
        item.add_argument("--run-id", required=True)
        if command == "land":
            item.add_argument("--message", required=True)
    answer = sub.add_parser("review-answer")
    answer.add_argument("--project", required=True)
    answer.add_argument("--run-id", required=True)
    answer.add_argument("--handout", required=True, help="the handout's event id")
    answer.add_argument("--answer", required=True,
                        help="the expert's final reply, unchanged: - for stdin, or a file")
    gate0 = sub.add_parser("gate0-start")
    gate0.add_argument("--project", required=True)
    gate0.add_argument("--run-id", required=True)
    gate0.add_argument("--artifact")
    review_address = sub.add_parser("review-address")
    review_address.add_argument("--project", required=True)
    review_address.add_argument("--run-id", required=True)
    review_address.add_argument("--artifact", required=True)
    review_proof = sub.add_parser("review-proof")
    review_proof.add_argument("--project", required=True)
    review_proof.add_argument("--run-id", required=True)
    review_proof.add_argument("--artifact", required=True)
    report_tests = sub.add_parser("report-tests")      # a TEST report in DELIVER (S6-1)
    report_tests.add_argument("--project", required=True)
    report_tests.add_argument("--run-id", required=True)
    report_tests.add_argument("--artifact", required=True)
    abandon = sub.add_parser("abandon")
    abandon.add_argument("--project", required=True)
    abandon.add_argument("--run-id", required=True)
    abandon.add_argument("--reason", required=True)
    decision_request = sub.add_parser("decision-request")
    decision_request.add_argument("--project", required=True)
    decision_request.add_argument("--run-id", required=True)
    decision_request.add_argument("--artifact", required=True)
    decision_request.add_argument("--document", choices=("REQUIREMENTS", "DESIGN_SPEC"), required=True)
    run_land = sub.add_parser("run-land")
    run_land.add_argument("--project", required=True)
    run_land.add_argument("--run-id", required=True)
    mode = run_land.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--request")
    run_land.add_argument("--skip-tests", action="store_true")
    run_land.add_argument("--note")
    run_land.add_argument("--request-id")
    run_land.add_argument("--message")
    complete = sub.add_parser("stage-complete")
    complete.add_argument("--project", required=True)

    accept = sub.add_parser("accept-finding")
    accept.add_argument("--project", required=True)
    accept.add_argument("--run-id", required=True)
    accept_mode = accept.add_mutually_exclusive_group(required=True)
    accept_mode.add_argument("--prepare", action="store_true")
    accept_mode.add_argument("--request")
    accept.add_argument("--canonical-id")
    accept.add_argument("--note")

    discard = sub.add_parser("discard-snapshot")
    discard.add_argument("--project", required=True)
    discard.add_argument("--run-id", required=True)
    discard_mode = discard.add_mutually_exclusive_group(required=True)
    discard_mode.add_argument("--prepare", action="store_true")
    discard_mode.add_argument("--request")
    discard.add_argument("--repo")
    discard.add_argument("--note")
    complete.add_argument("--run-id", required=True)
    complete.add_argument("--artifact")
    complete.add_argument("--expect-stage",
                          help="complete only if the run is still in this stage")

    reflect_prepare = sub.add_parser("reflect-prepare")
    reflect_prepare.add_argument("--project", required=True)
    reflect_prepare.add_argument("--run-id", required=True)
    reflect_prepare.add_argument("--artifact", required=True)
    return root


def create_run(*, project: str, profile: str, session_id: str, repos: dict[str, str],
               run_id: str | None, transcript_ids: list[str], requirement: str | None,
               owner_source: str, review_answers: str):
    """Create a run as ``pipeline-v1 create`` does once its owner is known: resolve the
    project's binding (run-knowledge §3.2), build the run's knowledge (§3.3), create it
    and write the knowledge file. ``(engine, binding, knowledge, knowledge_file)``."""
    from core.run_protocol import project_binding
    binding = project_binding.resolve(project, repos)          # run-knowledge §3.2
    from core.run_protocol import run_knowledge                # run-knowledge §3.3
    from core.run_protocol.lifecycle import PROFILE_STAGES
    # a profile without EVALUATE goes straight to code: give it the design recall
    purpose = "requirement" if "evaluate" in PROFILE_STAGES[profile] else "design"
    knowledge = run_knowledge.build(
        requirement, project=binding.project, has_ddd=binding.has_ddd,
        workspace=_workspace(), repo_roots=repos, purpose=purpose)
    engine = RunEngine.create(
        data_dir=get_app_data_dir(), workspace=_workspace(), project=binding.project,
        profile=profile, session_id=session_id, repos=repos,
        run_id=run_id, transcript_ids=transcript_ids,
        requirement=requirement, has_ddd=binding.has_ddd,
        bound_repos=binding.bound_repos,
        knowledge=run_knowledge.contract_record(knowledge),
        owner_source=owner_source, review_answers=review_answers)
    knowledge_file = engine.write_knowledge(knowledge.markdown, purpose)
    return engine, binding, knowledge, knowledge_file


def execute(args) -> dict[str, Any]:
    if args.command == "create":
        # the owner is the calling session: a different --session-id is refused
        owner, owner_source = _request().session_id, _request().session_source
        if not owner:
            # no host session at all (standalone T6 §4.2): the run owns itself; the id
            # only keeps two sessions from driving one run, it is no credential
            import uuid
            owner, owner_source = str(uuid.uuid4()), "run"
        if args.session_id and args.session_id != owner:
            raise ValueError("create --session-id must be this session")
        args.session_id = owner
        # SWARM_APP_SESSION_ID comes only from a host hook, and that hook reads the
        # experts; without it the agent relays their replies (standalone §4.3)
        review_answers = "host" if owner_source == "host" else "relayed"
        engine, binding, knowledge, knowledge_file = create_run(
            project=args.project, profile=args.profile, session_id=args.session_id,
            repos=_repos(args.repo), run_id=args.run_id,
            transcript_ids=[*args.transcript_id, *_this_transcript()],
            requirement=args.requirement, owner_source=owner_source, review_answers=review_answers)
        result = {"ok": True, "pipeline_id": engine.run_id, "project": binding.project,
                  "has_ddd": binding.has_ddd, "run_file": str(engine.run_dir / "run.json"),
                  "knowledge": {"file": str(knowledge_file), "status": knowledge.status,
                                "entries": len(knowledge.items)},
                  "owner": owner, "review_answers": review_answers}
        notes = list(binding.notes)
        if owner_source == "run":
            notes.append(f"no host session id: this run's owner is {owner}; every command "
                         "in `next` carries it as SWARM_APP_SESSION_ID")
        if review_answers == "relayed":
            notes.append("no host hook reads the review experts: after each expert returns, "
                         "pass its final reply unchanged to the handout's `answer_command`; "
                         "REPORT says the answers were relayed")
        if notes:
            result["notes"] = notes
        return _with_next(engine, result, previous_stage="", mutating=True)
    if args.command not in _READ_ONLY:
        # one read-decide-write operation at a time on this run (r6 §2.2): a parallel
        # call waits, then is judged on the state the first one left
        from core.run_protocol.oplock import operation
        audit_root = get_app_data_dir() / "state" / "pipeline_agent_audit"
        with operation(audit_root, args.run_id):
            engine = _load(args, note=False)
            # judged under the lock, before ANY write (the transcript note included):
            # a command decided before an owner transfer changes nothing after it
            refused = _not_owner(engine, args.run_id)
            if refused:
                return refused
            _note_transcripts(engine)
            from core.run_protocol import reflect_job
            # a run left reflecting (its CLI stopped after the landing): finish it first;
            # `resume` exists for exactly this (next_step names it)
            earlier = reflect_job.finish_in_cli(engine)
            if earlier is not None and args.command == "resume":
                return _with_next(engine, {"ok": True, "reflect": earlier},
                                  previous_stage="reflect", mutating=True)
            before = engine.projection()["stage"]
            result = _execute_loaded(args, engine)
            # the landing (or the not-landed path) moved it to REFLECT: end the run here,
            # no daemon needed (standalone §4.6)
            finished = reflect_job.finish_in_cli(engine)
            if finished is not None:
                result["reflect"] = finished
            return _with_next(engine, result, previous_stage=before, mutating=True)
    engine = _load(args, note=False)
    if args.command == "status" and _missing_worktrees(engine) \
            and _request().session_id == engine.owner_session():
        # status is read-only, except that the run's OWN session makes a code stage's
        # missing worktree (a crash between entering the stage and making it): one
        # operation, under the lock
        from core.run_protocol.oplock import operation
        with operation(get_app_data_dir() / "state" / "pipeline_agent_audit", args.run_id):
            engine = _load(args, note=False)
            if not _not_owner(engine, args.run_id):     # re-judged under the lock
                _note_transcripts(engine)
                engine.ensure_stage_worktrees()
    return _with_next(engine, _execute_loaded(args, engine), previous_stage=None,
                      mutating=False)


# read the run, never write it: no operation lock (r6 §2.2)
_READ_ONLY = frozenset({"status", "events", "findings"})


def _missing_worktrees(engine: RunEngine) -> bool:
    state = engine.projection()
    return (engine.contract.get("mode") == "isolated" and state["status"] == "active"
            and lifecycle.caps(engine.contract["profile"]).lands_code
            and state["stage"] in engine.WORKTREE_STAGES
            and set(engine.effective_repos()) != set(engine.contract["expected_repos"]))


def _with_next(engine: RunEngine, result: dict[str, Any], *, previous_stage: str | None,
               mutating: bool) -> dict[str, Any]:
    """Every response ends with ``state`` and ``next`` (r6 §2.3). A mutating call that
    leaves the run in a stage that edits code also makes sure its worktrees exist
    (idempotent): there is no ``stage-start``, the first call that needs them makes them."""
    from core.run_protocol.next_step import next_step
    if mutating:
        try:
            engine.ensure_stage_worktrees()
        except EngineError as exc:
            result["worktree_error"] = str(exc)
    result["state"] = engine.projection()
    result["repos"] = engine.effective_repos()       # where the run's code is edited
    handouts = result.get("handouts")
    result["next"] = next_step(engine, previous_stage=previous_stage,
                               dispatched=len(handouts) if isinstance(handouts, list) else None)
    if engine.review_answers() == "relayed":
        for listed in (handouts, result["next"].get("handouts")):
            for item in listed if isinstance(listed, list) else ():
                _answer_command(engine, item)
    return result


def _answer_command(engine: RunEngine, handout: Any) -> None:
    """A relayed run's handout names the command its expert's reply goes to (T6)."""
    from core.run_protocol import hook_bridge
    from core.run_protocol.next_step import _cmd
    if not isinstance(handout, dict):
        return
    hid = handout.get("handout_event_id")
    if not hid:
        bound = hook_bridge.parse_binding(str(handout.get("prompt") or ""))
        hid = next((e["event_id"] for e in engine.store.read_events()
                    if bound and e.get("kind") == "handout"
                    and e["payload"].get("nonce") == bound[1]), None)
    if hid:
        handout["answer_command"] = _cmd(engine, "review-answer") + f" --handout {hid} --answer -"


def _execute_loaded(args, engine: RunEngine) -> dict[str, Any]:
    if args.command == "status":
        return {"ok": True, "state": engine.projection(), "owner": engine.owner_session(),
                "decision": engine.pending_decision(),
                "run_file": str(engine.run_dir / "run.json")}
    if args.command == "gate0-start":
        return {"ok": True, **engine.gate0_start(
            _json_arg(args.artifact) if args.artifact else None)}
    if args.command == "stage-complete":
        return engine.stage_complete(_json_arg(args.artifact),
                                     expected_stage=args.expect_stage)
    if args.command == "review-start":
        return {"ok": True, **engine.review_start()}
    if args.command == "review-answer":
        return {"ok": True, **engine.relayed_answer(args.handout, _request_stdin())}
    if args.command == "review-address":
        return {"ok": True, **engine.review_address(_json_arg(args.artifact))}
    if args.command == "report-tests":
        return engine.report_tests(_json_arg(args.artifact))
    if args.command == "review-proof":
        return {"ok": True, **engine.review_proof(_json_arg(args.artifact))}
    if args.command == "land":
        return {"ok": True, **engine.normal_land(message=args.message)}
    if args.command == "resume":
        return engine.resume()
    if args.command == "restore":
        return {"ok": True, **engine.restore()}
    if args.command == "abandon":
        return {"ok": True, **engine.abandon(reason=args.reason)}
    if args.command == "decision-request":
        return {"ok": True, **engine.request_decision(
            _json_arg(args.artifact), document=args.document)}
    if args.command == "run-land":
        if args.prepare:
            if args.message:
                raise ValueError("--message is only valid with --request")
            action = "land_skip_tests" if args.skip_tests else "land"
            return {"ok": True, **engine.prepare_land_request(
                action=action, note=args.note, request_id=args.request_id)}
        if args.skip_tests or args.note or args.request_id:
            raise ValueError("--request cannot be combined with prepare-only options")
        if not args.message:
            raise ValueError("--request requires --message")
        return {"ok": True, **engine.execute_land_request(
            args.request, message=args.message)}
    if args.command == "reflect-prepare":
        return {"ok": True, "reflect": engine.prepare_reflect(_json_arg(args.artifact))}
    if args.command == "findings":    # read-only: every finding and what closes it (§3.2)
        from core.run_protocol import review as review_protocol
        events = engine.store.read_events()
        rows = review_protocol.ReviewOrchestrator(engine.store, engine.run_dir).findings(events)
        return {"ok": True, "findings": review_protocol.closure_view(rows, events)}
    if args.command == "events":      # read-only: the ids a lesson may cite (RF-2, D1)
        from core.ingestion import gaps    # and the open gaps a delivered event may close
        return {"ok": True, "events": [
            {"event_id": e["event_id"], "kind": e["kind"], "stage": e.get("stage"),
             "at": e.get("at")} for e in engine.store.read_events()],
            "open_gaps": gaps.open_gaps(engine.project, gaps.run_query(engine.run_dir))}
    if args.command in {"accept-finding", "discard-snapshot"}:
        action = args.command.replace("-", "_")
        target = args.canonical_id if args.command == "accept-finding" else args.repo
        if args.prepare:
            if not target:
                raise ValueError(f"{args.command} --prepare requires its target")
            return {"ok": True, **engine.prepare_authorized_action(
                action=action, target=target, note=args.note)}
        if target or args.note:
            raise ValueError("--request cannot be combined with prepare-only options")
        return {"ok": True, **engine.execute_authorized_action(args.request)}
    raise ValueError(f"unknown command {args.command}")


def _infrastructure_failed(args, failure) -> dict[str, Any]:
    """The run's infrastructure failed (conformance F11): record it on the run, which
    ends ``failed``; when the audit area itself cannot be written, say so instead."""
    payload: dict[str, Any] = {"ok": False, "code": "infrastructure_failed",
                               "component": failure.component, "error": str(failure)[:500]}
    if getattr(args, "run_id", None) and getattr(args, "project", None):
        try:
            engine = _load(args, note=False)
            if _not_owner(engine, args.run_id):
                # only the run's logged owner can fail it (B-e review)
                payload.update(recorded=False, record_error="not the run's owner")
            else:
                payload.update(engine.fail_infrastructure(failure))
        except Exception as exc:  # noqa: BLE001 — the failure itself may forbid recording
            payload.update(recorded=False, record_error=f"{type(exc).__name__}: {exc}"[:300])
    return payload


def run(args) -> "tuple[dict[str, Any], int]":
    """(the JSON the CLI prints, its exit code) for one parsed command."""
    from core.run_protocol import infra
    try:
        result = execute(args)
        return result, (0 if result.get("ok") is not False else 1)
    except Exception as exc:
        failure = infra.find(exc)
        if failure is None:
            from core.run_protocol.oplock import RunBusy
            from core.run_protocol.store import LogDamaged
            if isinstance(exc, LogDamaged):
                # a damaged record (S6-3, §3.3.5): the run stops for a person; nothing
                # more is written to its log
                return {"ok": False, "code": "log_damaged", "error":
                        f"{exc}. The run's event log is damaged, so the run stops here; "
                        "tell the user (a person has to look at it)."}, 2
            if isinstance(exc, (ValueError, EngineError, LandingError, RunBusy)):
                return _refused(exc), 2
            raise
        return _infrastructure_failed(args, failure), 2


def _refused(exc: Exception) -> dict[str, Any]:
    """A refused command, always as one JSON object (C5)."""
    payload: dict[str, Any] = {"ok": False, "error": str(exc)}
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        payload["code"] = code
    return payload


__all__ = ["parser", "run"]
