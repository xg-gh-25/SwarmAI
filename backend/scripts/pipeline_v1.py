#!/usr/bin/env python3
"""Pipeline v1 command line entrypoint.

Commands expose the event-driven RunEngine without caller-supplied verdict fields.
Every response is one JSON object; no command pushes or deploys. There is no
answer command: a human decision is recorded by the daemon (chat question card or
dashboard), never by the agent.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from config import get_app_data_dir  # noqa: E402
from core.run_protocol.engine import EngineError, RunEngine  # noqa: E402
from core.run_protocol.landing import LandingError  # noqa: E402
from core.run_worktree import mode_enabled  # noqa: E402


def _workspace() -> Path:
    return Path(os.environ.get("SWARM_WORKSPACE") or get_app_data_dir() / "SwarmWS")


def _repos(values: list[str]) -> dict[str, str]:
    out = {}
    for value in values:
        name, sep, path = value.partition("=")
        if not sep or not name or not path:
            raise ValueError("--repo must be name=/absolute/path")
        resolved = Path(path).resolve()
        if not resolved.is_absolute() or not (resolved / ".git").exists():
            raise ValueError(f"--repo is not a git worktree: {path}")
        out[name] = str(resolved)
    return out


def _json_arg(value: str | None) -> dict[str, Any]:
    if value is None:
        return {}
    path = Path(value)
    try:
        raw = path.read_text(encoding="utf-8") if path.is_file() else value
        result = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"artifact is not JSON: {exc}") from exc
    if not isinstance(result, dict):
        raise ValueError("artifact must be a JSON object")
    return result


def _this_transcript() -> list[str]:
    """This CLI process's SDK transcript id (CLAUDE_CODE_SESSION_ID): the main agent's
    usage is measured from it (D3). Not an SDK id -> nothing."""
    from core.session_ids import valid_session_id
    sid = (os.environ.get("CLAUDE_CODE_SESSION_ID") or "").strip()
    return [sid] if valid_session_id(sid) else []


def _load(args) -> RunEngine:
    engine = RunEngine.load(data_dir=get_app_data_dir(), workspace=_workspace(),
                            project=args.project, run_id=args.run_id)
    for sid in _this_transcript():     # a resumed / taken-over session adds its own
        engine.note_transcript(sid)
    return engine


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="pipeline_v1.py")
    sub = root.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--project", required=True)
    create.add_argument("--profile", default="direct")
    create.add_argument("--session-id", default=os.environ.get("SWARM_APP_SESSION_ID"))
    create.add_argument("--repo", action="append", default=[])
    create.add_argument("--run-id")
    create.add_argument("--risk-test-file-cap", type=int, default=200)
    create.add_argument("--transcript-id", action="append", default=[])
    create.add_argument("--requirement",
                        help="the user's request, verbatim (required for --profile direct)")
    for command in ("status", "stage-start", "review-start", "push-status", "land",
                    "measure-usage", "events", "resume", "restore"):
        item = sub.add_parser(command)
        item.add_argument("--project", required=True)
        item.add_argument("--run-id", required=True)
        if command == "land":
            item.add_argument("--message", required=True)
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

    reflect_prepare = sub.add_parser("reflect-prepare")
    reflect_prepare.add_argument("--project", required=True)
    reflect_prepare.add_argument("--run-id", required=True)
    reflect_prepare.add_argument("--artifact", required=True)
    return root


def execute(args) -> dict[str, Any]:
    if args.command == "create":
        if not args.session_id:
            raise ValueError("create requires --session-id or SWARM_APP_SESSION_ID")
        engine = RunEngine.create(
            data_dir=get_app_data_dir(), workspace=_workspace(), project=args.project,
            profile=args.profile, session_id=args.session_id, repos=_repos(args.repo),
            # the mode is a setting, never the agent's choice (C4, design §C5.1)
            mode="isolated" if mode_enabled() else "shared",
            risk_test_file_cap=args.risk_test_file_cap,
            run_id=args.run_id, transcript_ids=[*args.transcript_id, *_this_transcript()],
            requirement=args.requirement)
        return {"ok": True, "pipeline_id": engine.run_id,
                "state": engine.projection(), "run_file": str(engine.run_dir / "run.json")}
    engine = _load(args)
    if args.command == "status":
        return {"ok": True, "state": engine.projection(), "owner": engine.owner_session(),
                "decision": engine.pending_decision(),
                "push": engine.push_status(), "run_file": str(engine.run_dir / "run.json")}
    if args.command == "stage-start":
        return {"ok": True, **engine.stage_start()}
    if args.command == "gate0-start":
        return {"ok": True, **engine.gate0_start(
            _json_arg(args.artifact) if args.artifact else None)}
    if args.command == "stage-complete":
        return engine.stage_complete(_json_arg(args.artifact))
    if args.command == "review-start":
        return {"ok": True, **engine.review_start()}
    if args.command == "review-address":
        return {"ok": True, **engine.review_address(_json_arg(args.artifact))}
    if args.command == "review-proof":
        return {"ok": True, **engine.review_proof(_json_arg(args.artifact))}
    if args.command == "push-status":
        return {"ok": True, **engine.push_status()}
    if args.command == "measure-usage":
        return {"ok": True, "usage": engine.measure_usage()}
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
        artifact = _json_arg(args.artifact)
        # dedup candidates are computed here, not supplied (RF-4, D1)
        return {"ok": True, "reflect": engine.prepare_reflect(artifact.get("lessons", []))}
    if args.command == "events":      # read-only: the ids a lesson may cite (RF-2, D1)
        return {"ok": True, "events": [
            {"event_id": e["event_id"], "kind": e["kind"], "stage": e.get("stage"),
             "at": e.get("at")} for e in engine.store.read_events()]}
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
            payload.update(_load(args).fail_infrastructure(failure))
        except Exception as exc:  # noqa: BLE001 — the failure itself may forbid recording
            payload.update(recorded=False, record_error=f"{type(exc).__name__}: {exc}"[:300])
    return payload


def main(argv: list[str] | None = None) -> int:
    from core.run_protocol import infra
    args = None
    try:
        args = parser().parse_args(argv)
        result = execute(args)
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0 if result.get("ok") is not False else 1
    except Exception as exc:
        failure = infra.find(exc)
        if failure is None:
            if isinstance(exc, (ValueError, EngineError, LandingError)):
                return _refused(exc)
            raise
        print(json.dumps(_infrastructure_failed(args, failure), ensure_ascii=False,
                         separators=(",", ":")))
        return 2


def _refused(exc: Exception) -> int:
    """A refused command, always as one JSON object (C5)."""
    payload = {"ok": False, "error": str(exc)}
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        payload["code"] = code
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return 2


if __name__ == "__main__":
    from core.run_protocol.events import restrict_process
    restrict_process("cli")      # the agent's CLI signs as cli, never hook / runner (C3)
    raise SystemExit(main())
