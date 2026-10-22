"""Authorized Pipeline v1 landing and manual-run recovery protocol.

Public symbols:
- ``prepare_request`` — land_intent → REPORT → tree guards → land_request.
- ``approve_request`` — hook-side auth event plus daemon oracle registration.
- ``execute_request`` — exact tree recheck, atomic auth consume, commit-tree and land event.
- ``LandingError`` — a deterministic guard, authorization, or git failure.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping

from core.run_protocol import auth, checks, git_tree, tree
from core.run_protocol.store import AuditStore


class LandingError(RuntimeError):
    """Landing evidence, authorization, tree identity, or integration is invalid."""


def _evaluate(ids: list[str], context: checks.CheckContext) -> dict[str, tuple[bool, str]]:
    return {check_id: checks.evaluate(check_id, context) for check_id in ids}


def _require(results: Mapping[str, tuple[bool, str]]) -> None:
    failed = {check_id: detail for check_id, (passed, detail) in results.items() if not passed}
    if failed:
        raise LandingError(f"landing guards failed: {failed}")


def prepare_request(
    store: AuditStore, *, run_id: str, session_id: str, action: str,
    repos: Mapping[str, str], context: checks.CheckContext, at: str,
    render_report: Callable[[str, Mapping[str, tuple[bool, str]]], bytes],
    note: str | None = None, request_id: str,
) -> dict[str, Any]:
    """Prepare a one-use manual landing request without committing anything."""
    if action not in ("land", "land_skip_tests"):
        raise LandingError("manual landing action must be land or land_skip_tests")
    guard_ids = (["LG-3", "LG-4"] if action == "land_skip_tests"
                 else ["LG-1", "LG-2", "LG-3", "LG-4", "LG-5"])
    prior_attempts = [event for event in store.read_events()
                      if event.get("kind") == "stage_attempt" and event.get("stage") == "land"]
    attempt_number = len(prior_attempts) + 1
    attempt_event, _ = store.append_new(
        writer=store.authority.writer("cli"), kind="stage_attempt", at=at,
        stage="land", attempt=attempt_number, payload={"check_ids": guard_ids})
    code_trees = tree.build_code_trees(repos, run_id, git_tree.write_tree, git_tree.list_reserved)
    run_facts = {**dict(context.run), "code_trees": code_trees,
                 "landing_bases": {name: context.final["repos"][name]["base"]
                                   for name in repos if name in context.final.get("repos", {})}}
    current = dataclasses.replace(context, events=tuple(store.read_events()), run=run_facts)
    pre_ids = [] if action == "land_skip_tests" else ["LG-1", "LG-2", "LG-5"]
    pre = _evaluate(pre_ids, current)
    for check_id, (passed, detail) in pre.items():
        store.append_new(
            writer=store.authority.writer("cli"), kind="check", at=at,
            stage="land", attempt=attempt_number,
            payload={"stage_attempt_event_id": attempt_event["event_id"],
                     "check_id": check_id, "passed": passed, "detail": detail})
    _require(pre)
    intent, _ = store.append_new(
        writer=store.authority.writer("cli"), kind="land_intent", at=at,
        stage="deliver", attempt=attempt_number, payload={"request_id": request_id},
    )
    report = render_report(request_id, pre)
    if not isinstance(report, bytes):
        raise LandingError("REPORT renderer must return bytes")
    report_sha = store.put_blob(report)
    render, _ = store.append_new(
        writer=store.authority.writer("cli"), kind="render", at=at,
        stage="deliver", attempt=attempt_number,
        payload={"document": "REPORT", "blob_sha": report_sha},
    )
    bindings = tree.build_delivery_trees(
        repos, run_id, code_trees, report, report_sha,
        git_tree.write_tree, git_tree.list_reserved,
    )
    run_facts["lg3_args"] = {
        "repos": repos, "run_id": run_id, "bindings": bindings,
        "rendered_report_sha": report_sha, "diff_trees": git_tree.diff_trees,
    }
    post_context = dataclasses.replace(
        context, events=tuple(store.read_events()), run=run_facts)
    post_ids = ["LG-3", "LG-4"]
    post = _evaluate(post_ids, post_context)
    for check_id, (passed, detail) in post.items():
        store.append_new(
            writer=store.authority.writer("cli"), kind="check", at=at,
            stage="land", attempt=attempt_number,
            payload={"stage_attempt_event_id": attempt_event["event_id"],
                     "check_id": check_id, "passed": passed, "detail": detail})
    dv1 = _evaluate(["DV-1"], post_context)
    _require({**post, **dv1})
    normalized = {name: {key: row[key] for key in ("code_tree", "delivery_tree", "report_sha")}
                  for name, row in bindings.items()}
    request = auth.make_request(request_id=request_id, session_id=session_id,
                                run_id=run_id, action=action, bindings=normalized, note=note)
    guard_ids = [*pre_ids, *post_ids]
    event, _ = store.append_new(
        writer=store.authority.writer("cli"), kind="land_request", at=at,
        stage="deliver", attempt=attempt_number,
        payload={"request_id": request_id, "action": action,
                 "binding_digest": request["binding_digest"],
                 "guard_attempt_event_id": attempt_event["event_id"],
                 "guard_check_ids": guard_ids},
    )
    return {"request": request, "event": event, "bindings": normalized,
            "bases": {name: context.final["repos"][name]["base"] for name in repos},
            "report_sha": report_sha, "render_event_id": render["event_id"]}


def approve_request(store: AuditStore, oracle: auth.AuthorizationOracle,
                    request: Mapping[str, Any], *, decided_at: float,
                    expires_at: float, at: str) -> dict[str, Any]:
    """Hook-side approval: persist auth and register the daemon-memory grant."""
    event, _ = store.append_new(
        writer=store.authority.writer("hook"), kind="auth", at=at,
        payload={"request_id": request["request_id"], "session_id": request["session_id"],
                 "run_id": request["run_id"], "action": request["action"],
                 "binding_digest": request["binding_digest"], "expires_at": expires_at},
    )
    oracle.issue(request, auth_event_id=event["event_id"],
                 decided_at=decided_at, expires_at=expires_at)
    return event


TRAILER = "Co-Authored-By: Swarm <swarm@swarmai.dev>"


def with_trailer(message: str) -> str:
    """The project commit trailer (AGENTS.md "Git Commits"), added once if missing:
    a landing is the only commit path a run has, so it owns the trailer."""
    text = str(message or "").rstrip()
    if not text:
        raise LandingError("landing commit message is empty")
    if TRAILER in text.splitlines():
        return text + "\n"
    return f"{text}\n\n{TRAILER}\n"


def _git(root: str, *args: str, env: Mapping[str, str] | None = None) -> str:
    try:
        result = subprocess.run(["git", "-C", root, *args], check=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, timeout=120, env=dict(env) if env else None)
    except (OSError, subprocess.SubprocessError) as exc:
        raise LandingError(f"git {' '.join(args[:2])} failed: {exc}") from exc
    return result.stdout.strip()


def _commit(root: str, base: str, delivery_tree: str, message: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "Swarm", "GIT_AUTHOR_EMAIL": "swarm@swarmai.dev",
           "GIT_COMMITTER_NAME": "Swarm", "GIT_COMMITTER_EMAIL": "swarm@swarmai.dev"}
    if _git(root, "rev-parse", "HEAD") != base:
        raise LandingError("integration branch moved before commit")
    commit = _git(root, "commit-tree", delivery_tree, "-p", base, "-m", message, env=env)
    if len(commit) != 40:
        raise LandingError("commit-tree returned invalid sha")
    _git(root, "merge", "--ff-only", commit)
    return commit


def _atomic_progress(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with open(temp, "x", encoding="utf-8") as handle:
            json.dump(dict(value), handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _commit_identity(root: str, commit: str) -> tuple[str, str]:
    return (_git(root, "rev-parse", f"{commit}^"),
            _git(root, "rev-parse", f"{commit}^{{tree}}"))


def commit_repos(
    *, run_id: str, integration_roots: Mapping[str, str], bases: Mapping[str, str],
    bindings: Mapping[str, Mapping[str, str]], message: str, progress_path: str | Path,
) -> dict[str, str]:
    """Land repos one at a time and durably preserve every completed commit.

    A retry verifies recorded facts against git.  It also recovers the narrow crash
    window where ``merge --ff-only`` succeeded but the progress file was not replaced.
    """
    expected = set(integration_roots)
    if not expected or set(bases) != expected or set(bindings) != expected:
        raise LandingError("landing plan must contain the exact non-empty repo set")
    plan = {name: {"base": bases[name],
                   "delivery_tree": bindings[name]["delivery_tree"]}
            for name in sorted(expected)}
    path = Path(progress_path)
    if path.exists():
        try:
            progress = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LandingError(f"landing progress unreadable: {exc}") from exc
        if not isinstance(progress, dict) or progress.get("run_id") != run_id \
                or progress.get("plan") != plan or not isinstance(progress.get("commits"), dict):
            raise LandingError("landing progress belongs to another immutable plan")
    else:
        progress = {"schema": 1, "run_id": run_id, "plan": plan, "commits": {}}
        _atomic_progress(path, progress)
    commits = dict(progress["commits"])
    for name in sorted(expected):
        root, base, delivery_tree = integration_roots[name], bases[name], bindings[name]["delivery_tree"]
        recorded = commits.get(name)
        if recorded is not None:
            if not isinstance(recorded, str) or len(recorded) != 40 \
                    or _commit_identity(root, recorded) != (base, delivery_tree) \
                    or subprocess.run(["git", "-C", root, "merge-base", "--is-ancestor",
                                       recorded, "HEAD"], stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL, timeout=30).returncode != 0:
                raise LandingError(f"{name}: persisted landing fact no longer matches git")
            continue
        head = _git(root, "rev-parse", "HEAD")
        if head != base:
            try:
                parent, tree_id = _commit_identity(root, head)
            except LandingError:
                parent, tree_id = "", ""
            if (parent, tree_id) != (base, delivery_tree):
                raise LandingError(f"{name}: integration branch moved")
            commit = head
        else:
            commit = _commit(root, base, delivery_tree, message)
        commits[name] = commit
        progress["commits"] = dict(commits)
        _atomic_progress(path, progress)
    return {name: commits[name] for name in sorted(expected)}


def execute_request(
    store: AuditStore, oracle: auth.AuthorizationOracle, prepared: Mapping[str, Any], *,
    session_id: str, repos: Mapping[str, str], integration_roots: Mapping[str, str],
    message: str, at: str, now: float | None = None,
    progress_path: str | Path | None = None,
) -> dict[str, str]:
    """Consume an exact authorization, then durably land every prepared repo."""
    request = prepared["request"]
    if set(repos) != set(prepared["bindings"]) or set(integration_roots) != set(repos):
        raise LandingError("repo set changed since prepare")
    report_path = store.paths.blobs / prepared["report_sha"]
    try:
        report = report_path.read_bytes()
    except OSError as exc:
        raise LandingError(f"prepared REPORT blob missing: {exc}") from exc
    code_trees = tree.build_code_trees(repos, request["run_id"], git_tree.write_tree,
                                       git_tree.list_reserved)
    current = tree.build_delivery_trees(
        repos, request["run_id"], code_trees, report, prepared["report_sha"],
        git_tree.write_tree, git_tree.list_reserved)
    current_bindings = {name: {key: row[key] for key in ("code_tree", "delivery_tree", "report_sha")}
                        for name, row in current.items()}
    if current_bindings != prepared["bindings"]:
        raise LandingError("trees changed since authorization request")
    prior_consumed = next((event for event in store.read_events()
                           if event.get("kind") == "auth_consumed"
                           and event.get("payload", {}).get("request_id") == request["request_id"]
                           and event.get("payload", {}).get("session_id") == session_id
                           and event.get("payload", {}).get("action") == request["action"]
                           and event.get("payload", {}).get("binding_digest") == request["binding_digest"]), None)
    if prior_consumed is not None:
        raise LandingError("authorization already consumed; prepare a new landing request")
    result = oracle.consume(request_id=request["request_id"], session_id=session_id,
                            run_id=request["run_id"], action=request["action"],
                            bindings=current_bindings, now=time.time() if now is None else now)
    if not result.ok or result.consumption is None:
        raise LandingError(result.reason)
    consumed, _ = store.append_new(
        writer=store.authority.writer("cli"), kind="auth_consumed", at=at,
        stage="deliver", attempt=1, payload=result.consumption,
    )
    path = Path(progress_path) if progress_path is not None else (
        store.paths.run / f"landing-progress-{request['request_id']}.json")
    commits = commit_repos(
        run_id=request["run_id"], integration_roots=integration_roots,
        bases=prepared["bases"], bindings=current, message=message, progress_path=path)
    if not any(event.get("kind") == "land" and
               event.get("payload", {}).get("auth_consumed_event_id") == consumed["event_id"]
               for event in store.read_events()):
        store.append_new(
            writer=store.authority.writer("cli"), kind="land", at=at,
            stage="deliver", attempt=1, ordinal=len(store.read_events()) + 1,
            payload={"landing": "human_override", "commit": next(iter(commits.values())),
                     "auth_consumed_event_id": consumed["event_id"]},
        )
    return commits
