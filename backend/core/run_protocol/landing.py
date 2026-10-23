"""Authorized Pipeline v1 landing and manual-run recovery protocol.

Public symbols:
- ``prepare_request`` — land_intent → REPORT → tree guards → land_request.
- ``request_event`` — the signed land_request payload: the only source execution reads.
- ``approve_from_event`` — the daemon's ``auth`` fact after the person approved (C1, C3).
- ``consume_authorization`` — one-use consume under the run lock; a second is integrity (C6).
- ``execute_request`` — exact tree recheck against the event, consume, commit-tree, land.
- ``LandingError`` — a deterministic guard, authorization, or git failure.
"""
from __future__ import annotations

import copy
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


def _rev(root: str, ref: str, other: str | None = None) -> str:
    """``rev-parse <ref>`` in *root*, or the merge-base of *ref* and *other*'s HEAD."""
    argv = (["git", "-C", str(root), "rev-parse", ref] if other is None else
            ["git", "-C", str(root), "merge-base", ref, _rev(other, "HEAD")])
    try:
        return subprocess.run(argv, check=True, capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise LandingError(f"git {' '.join(argv[3:])} failed in {root}: {exc}") from exc


def _require(results: Mapping[str, tuple[bool, str]]) -> None:
    failed = {check_id: detail for check_id, (passed, detail) in results.items() if not passed}
    if failed:
        raise LandingError(f"landing guards failed: {failed}")


def prepare_request(
    store: AuditStore, *, run_id: str, session_id: str, action: str,
    repos: Mapping[str, str], context: checks.CheckContext, at: str,
    render_report: Callable[[str, Mapping[str, tuple[bool, str]]], bytes],
    note: str | None = None, request_id: str, push_reasons: int = 0,
    integration_roots: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Prepare a one-use manual landing request without committing anything. Every
    fact execution and the approval card need is in the signed land_request event
    (C1); the JSON the CLI also writes is only for display."""
    if action not in ("land", "land_skip_tests"):
        raise LandingError("manual landing action must be land or land_skip_tests")
    if note is not None and (not isinstance(note, str) or len(note) > auth.NOTE_MAX):
        raise LandingError(f"--note is limited to {auth.NOTE_MAX} characters")
    guard_ids = (["LG-3", "LG-4"] if action == "land_skip_tests"
                 else ["LG-1", "LG-2", "LG-3", "LG-4", "LG-5"])
    prior_attempts = [event for event in store.read_events()
                      if event.get("kind") == "stage_attempt" and event.get("stage") == "land"]
    attempt_number = len(prior_attempts) + 1
    attempt_event, _ = store.append_new(
        writer=store.authority.writer("cli"), kind="stage_attempt", at=at,
        stage="land", attempt=attempt_number, payload={"check_ids": guard_ids})
    code_trees = tree.build_code_trees(repos, run_id, git_tree.write_tree, git_tree.list_reserved)
    roots = dict(integration_roots or {})
    if set(roots) != set(repos):
        raise LandingError("manual landing needs every repo's integration root")
    heads = {name: _rev(roots[name], "HEAD") for name in repos}
    if action == "land_skip_tests":
        # no final is needed (C5): the run's own fork point is its base, and LG-4 holds
        # when main has not moved past it
        run_bases = dict(context.run.get("run_bases") or {})
    else:
        run_bases = {name: context.final["repos"][name]["base"]
                     for name in repos if name in (context.final.get("repos") or {})}
    run_facts = {**dict(context.run), "code_trees": code_trees, "landing_bases": heads,
                 "run_bases": run_bases, "skip_tests": action == "land_skip_tests"}
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
    bases = dict(run_bases)
    request = auth.make_request(request_id=request_id, session_id=session_id,
                                run_id=run_id, action=action, bindings=normalized, note=note)
    guard_ids = [*pre_ids, *post_ids]
    from core.run_protocol.store import StoreError
    try:
        event, _ = store.append_new(
            writer=store.authority.writer("cli"), kind="land_request", at=at,
            stage="deliver", attempt=attempt_number,
            payload={"request_id": request_id, "action": action,
                     "binding_digest": request["binding_digest"], "request": request,
                     "report_sha": report_sha, "bases": bases,
                     "push_reasons": int(push_reasons),
                     "guard_attempt_event_id": attempt_event["event_id"],
                     "guard_check_ids": guard_ids},
        )
    except StoreError as exc:   # e.g. too many repos for one signed event line
        raise LandingError(f"land_request could not be recorded: {exc}") from exc
    return {"request": request, "event": event, "bindings": normalized,
            "bases": bases, "report_sha": report_sha, "render_event_id": render["event_id"]}


AUTH_TTL_SECONDS = 30 * 60


def request_event(store: AuditStore, request_id: str) -> dict[str, Any]:
    """The land_request payload for *request_id* (C1: the only source). read_events
    verifies every signature and the hash chain; the lifecycle validated the payload
    when it was appended."""
    found = [event for event in store.read_events() if event.get("kind") == "land_request"
             and event.get("payload", {}).get("request_id") == request_id]
    if len(found) != 1 or not isinstance(found[0]["payload"].get("request"), Mapping):
        raise LandingError(f"no land_request {request_id!r} in this run's audit log")
    return copy.deepcopy(found[0]["payload"])


def approve_from_event(store: AuditStore, request_id: str, *, now: float,
                       at: str) -> dict[str, Any]:
    """Hook-side approval, written by the DAEMON after the person approved the command:
    the ``auth`` fact for exactly the request the signed land_request names (C1, C3)."""
    request = request_event(store, request_id)["request"]
    event, _ = store.append_new(
        writer=store.authority.writer("hook"), kind="auth", at=at,
        payload={"request_id": request["request_id"], "session_id": request["session_id"],
                 "run_id": request["run_id"], "action": request["action"],
                 "binding_digest": request["binding_digest"],
                 "expires_at": now + AUTH_TTL_SECONDS},
    )
    return event


def consume_authorization(store: AuditStore, request_id: str, *, session_id: str,
                          bindings: Mapping[str, Any], at: str, now: float) -> dict[str, Any]:
    """Consume the hook's ``auth`` for *request_id* once: it must exist, be unexpired,
    unconsumed, and bind exactly *bindings*. The check and the append happen under the
    run lock (the store's validator folds the chain there); a second consumption is
    refused and recorded as integrity (C6)."""
    from core.run_protocol import lifecycle
    from core.run_protocol.store import StoreError
    request = request_event(store, request_id)["request"]
    events = store.read_events()
    grants = [event for event in events if event.get("kind") == "auth"
              and event.get("producer") == "hook"
              and event["payload"].get("request_id") == request_id
              and event["payload"].get("binding_digest") == request["binding_digest"]]
    if not grants:
        raise LandingError("no approval recorded for this request (the hook writes it "
                           "when the person approves the command)")
    auth_event_id, grant = grants[-1]["event_id"], grants[-1]["payload"]
    if any(event.get("kind") == "auth_consumed"
           and event["payload"].get("auth_event_id") == auth_event_id for event in events):
        _record_double_consume(store, request_id, auth_event_id, at)
        raise LandingError("authorization already consumed; prepare a new landing request")
    oracle = auth.AuthorizationOracle()
    expires_at = float(grant["expires_at"])
    oracle.issue(request, auth_event_id=auth_event_id,
                 decided_at=expires_at - AUTH_TTL_SECONDS, expires_at=expires_at)
    result = oracle.consume(request_id=request_id, session_id=session_id,
                            run_id=request["run_id"], action=request["action"],
                            bindings=bindings, now=now)
    if not result.ok or result.consumption is None:
        raise LandingError(result.reason)
    try:
        consumed, _ = store.append_new(
            writer=store.authority.writer("cli"), kind="auth_consumed", at=at,
            stage="deliver", attempt=1, payload=result.consumption)
    except (StoreError, lifecycle.LifecycleError, ValueError) as exc:
        if "already consumed" in str(exc) or "already exists" in str(exc):
            _record_double_consume(store, request_id, auth_event_id, at)
            raise LandingError("authorization already consumed; prepare a new landing "
                               "request") from exc
        raise
    return {"request": request, "consumed": consumed, "consumption": result.consumption}


def _record_double_consume(store: AuditStore, request_id: str, auth_event_id: str,
                           at: str) -> None:
    from core.run_protocol import events as ev
    from core.run_protocol.store import StoreError
    try:
        store.append_new(
            writer=store.authority.writer("cli"), kind="integrity", at=at,
            ordinal=len(store.read_events()) + 1,
            payload={"conflict_id": f"auth_consumed:{auth_event_id}:{len(store.read_events())}",
                     "conflict_type": "auth_consumed_twice",
                     "first_hash": auth_event_id, "new_hash": request_id})
    except (StoreError, ev.EventError, ValueError) as exc:
        # the refusal still reaches the caller; only the integrity row is lost
        import logging
        logging.getLogger(__name__).warning("integrity row for %s not written: %s",
                                            request_id, exc)


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


_IDENTITY = {"GIT_AUTHOR_NAME": "Swarm", "GIT_AUTHOR_EMAIL": "swarm@swarmai.dev",
             "GIT_COMMITTER_NAME": "Swarm", "GIT_COMMITTER_EMAIL": "swarm@swarmai.dev"}


class PartialLanding(LandingError):
    """Every repo passed the pre-check, yet a fast-forward failed after at least one
    repo had moved (another session raced the window). *landed* is repo -> commit."""

    def __init__(self, message: str, *, landed: Mapping[str, str], failed: str):
        super().__init__(message)
        self.landed, self.failed = dict(landed), failed


def _prepare_commit(root: str, base: str, delivery_tree: str, message: str) -> str:
    """Phase 1 for one repo: everything that can fail, and no ref moves (§A3.7).
    The integration branch is still at *base*; the commit object is written; and git
    itself, in a dry run of the same two-tree checkout ``merge --ff-only`` performs,
    confirms the main checkout's index and files (tracked, untracked, case aliases,
    directory/file swaps, symlinks, gitlinks) let it through. Predicting this in
    Python instead was the round-1 review's HIGH finding."""
    if _git(root, "rev-parse", "HEAD") != base:
        raise LandingError("integration branch moved since the run's base")
    commit = _git(root, "commit-tree", delivery_tree, "-p", base, "-m", message,
                  env={**os.environ, **_IDENTITY})
    if len(commit) != 40:
        raise LandingError("commit-tree returned invalid sha")
    try:
        _git(root, "read-tree", "-n", "-m", "-u", "HEAD", commit)
    except LandingError as exc:
        raise LandingError(f"the main checkout would refuse the fast-forward "
                           f"(an uncommitted change is in the way): {exc}") from exc
    return commit


def _ours(root: str, commit: str, base: str, delivery_tree: str, message: str) -> bool:
    """*commit* is the one this landing makes: same parent and tree, the exact message
    bytes commit-tree stores, and the exact Swarm author and committer (name and
    email) — not merely a commit that happens to have the same tree."""
    try:
        parent, tree_id = _commit_identity(root, commit)
        raw = subprocess.run(["git", "-C", root, "cat-file", "commit", commit], check=True,
                             capture_output=True, timeout=30).stdout.decode("utf-8", "replace")
    except (LandingError, OSError, subprocess.SubprocessError):
        return False
    header, _, body = raw.partition("\n\n")
    people = {line.split(" ", 1)[0]: line.split(" ", 1)[1].rsplit(" ", 2)[0]
              for line in header.splitlines() if line.startswith(("author ", "committer "))}
    want = f"{_IDENTITY['GIT_AUTHOR_NAME']} <{_IDENTITY['GIT_AUTHOR_EMAIL']}>"
    return (parent, tree_id) == (base, delivery_tree) \
        and body == (message if message.endswith("\n") else message + "\n") \
        and people.get("author") == want and people.get("committer") == want


def _fast_forward(root: str, commit: str) -> None:
    """Phase 2 for one repo: the only step that moves a ref."""
    _git(root, "merge", "--ff-only", "-q", commit)


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


def _branch(root: str, expected: str | None) -> str:
    """The integration branch of *root*: *expected* when the run recorded one, else
    the branch checked out now. A detached main checkout has none: refused."""
    try:
        current = _git(root, "symbolic-ref", "-q", "HEAD")
    except LandingError as exc:
        raise LandingError("the main checkout has no branch checked out") from exc
    return expected if expected else current


def _same_plan(old: Any, new: Mapping[str, Mapping[str, Any]]) -> bool:
    """*old* is *new*, or *new* without fields a plan written before them lacked."""
    if not isinstance(old, Mapping) or set(old) != set(new):
        return False
    return all(isinstance(old[name], Mapping) and all(
        old[name].get(key, value) == value for key, value in new[name].items())
        for name in new)


def adoptable(integration_roots: Mapping[str, str], plan: Mapping[str, Any],
              message: str) -> bool:
    """Some repo's HEAD is the exact commit this plan makes (a crash between its
    fast-forward and every record of it): the landing must resume, not restart."""
    for name, row in (plan or {}).items():
        root = integration_roots.get(name)
        if not root or not isinstance(row, Mapping):
            continue
        try:
            head = _git(root, "rev-parse", "HEAD")
        except LandingError:
            continue
        if head != row.get("base") and _ours(root, head, row.get("base"),
                                             row.get("delivery_tree"), message):
            return True
    return False


def commit_repos(
    *, run_id: str, integration_roots: Mapping[str, str], bases: Mapping[str, str],
    bindings: Mapping[str, Mapping[str, str]], message: str, progress_path: str | Path,
    branches: Mapping[str, str | None] | None = None,
) -> dict[str, str]:
    """Land every repo or none (design §A3.7, conformance K2).

    Phase 1 checks and prepares EVERY repo while no ref has moved; phase 2 only
    fast-forwards. A phase-2 failure after a repo moved raises ``PartialLanding``
    (the caller ends the run not_landed(partial_land)); one before any moved raises
    ``LandingError`` and nothing landed. Each repo lands on ITS integration branch:
    *branches* (the run's recorded ones) or, when none was recorded (a shared run), the
    branch checked out at phase 1; the checkout must still be on it at phase 2. A
    retry verifies recorded commits against git, and adopts a fast-forward that
    happened just before a crash (``adoptable``).
    """
    expected = set(integration_roots)
    if not expected or set(bases) != expected or set(bindings) != expected:
        raise LandingError("landing plan must contain the exact non-empty repo set")
    plan = {name: {"base": bases[name],
                   "delivery_tree": bindings[name]["delivery_tree"],
                   "message_sha": hashlib.sha256(message.encode()).hexdigest(),
                   "branch": _branch(integration_roots[name], (branches or {}).get(name))}
            for name in sorted(expected)}
    path = Path(progress_path)
    if path.exists():
        try:
            progress = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LandingError(f"landing progress unreadable: {exc}") from exc
        if not isinstance(progress, dict) or progress.get("run_id") != run_id \
                or not isinstance(progress.get("commits"), dict) \
                or not _same_plan(progress.get("plan"), plan):
            raise LandingError("landing progress belongs to another immutable plan")
        progress["plan"] = plan     # a plan written before message/branch were bound
    else:
        progress = {"schema": 1, "run_id": run_id, "plan": plan, "commits": {}}
        _atomic_progress(path, progress)
    commits = dict(progress["commits"])
    for name, recorded in commits.items():
        root, base = integration_roots[name], bases[name]
        if not isinstance(recorded, str) or len(recorded) != 40 \
                or not _ours(root, recorded, base, plan[name]["delivery_tree"], message) \
                or subprocess.run(["git", "-C", root, "merge-base", "--is-ancestor",
                                   recorded, "HEAD"], stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, timeout=30).returncode != 0:
            raise LandingError(f"{name}: persisted landing fact no longer matches git")
    # phase 1: every remaining repo, before any of them moves (design §A3.7)
    prepared: dict[str, str] = {}
    for name in sorted(expected - set(commits)):
        if _branch(integration_roots[name], None) != plan[name]["branch"]:
            raise LandingError(f"{name}: the main checkout is not on {plan[name]['branch']}")
        root, base, delivery_tree = integration_roots[name], bases[name], bindings[name]["delivery_tree"]
        head = _git(root, "rev-parse", "HEAD")
        if head != base and _ours(root, head, base, delivery_tree, message):
            commits[name] = head        # a crash after the ff, before the progress write
            continue
        try:
            prepared[name] = _prepare_commit(root, base, delivery_tree, message)
        except LandingError as exc:
            raise LandingError(f"{name}: {exc}") from exc
    if len(commits) != len(progress["commits"]):
        progress["commits"] = dict(commits)
        _atomic_progress(path, progress)
    # phase 2: only fast-forwards remain
    for name in sorted(prepared):
        try:
            if _branch(integration_roots[name], None) != plan[name]["branch"]:
                raise LandingError(f"the main checkout left {plan[name]['branch']}")
            _fast_forward(integration_roots[name], prepared[name])
        except LandingError as exc:
            try:
                moved = _git(integration_roots[name], "rev-parse", "HEAD") == prepared[name]
            except LandingError:
                moved = False
            if moved:       # the ref moved, a later step of merge failed: it landed
                commits[name] = prepared[name]
                continue
            if commits:
                raise PartialLanding(f"{name}: {exc}", landed=commits, failed=name) from exc
            raise LandingError(f"{name}: {exc}") from exc
        commits[name] = prepared[name]
        try:
            progress["commits"] = dict(commits)
            _atomic_progress(path, progress)
        except OSError:
            # the progress file is crash recovery only: the ref moved, the commit is
            # returned (and signed by the caller); a failed write never stops the rest
            # of the landing half way (review round 1 #2)
            import logging
            logging.getLogger(__name__).warning(
                "run %s: landing progress not recorded after %s", run_id, name, exc_info=True)
    return {name: commits[name] for name in sorted(expected)}


def execute_request(
    store: AuditStore, request_id: str, *,
    session_id: str, repos: Mapping[str, str], integration_roots: Mapping[str, str],
    message: str, at: str, now: float | None = None,
    progress_path: str | Path | None = None,
    branches: Mapping[str, str | None] | None = None,
) -> dict[str, str]:
    """Consume the hook's approval of *request_id*, then durably land every repo.
    Everything bound — trees, REPORT, bases — is read from the signed land_request
    event (C1); the trees are rebuilt now and must equal it exactly."""
    payload = request_event(store, request_id)
    request = payload["request"]
    if request["action"] not in ("land", "land_skip_tests"):
        raise LandingError("this request is not a landing")
    bound = request["bindings"]["repos"]
    if set(repos) != set(bound) or set(integration_roots) != set(repos):
        raise LandingError("repo set changed since prepare")
    report_sha = payload.get("report_sha")
    bases = payload.get("bases")
    if not isinstance(report_sha, str) or not isinstance(bases, Mapping) or set(bases) != set(repos):
        raise LandingError("land_request lacks its REPORT or bases")
    try:
        report = (store.paths.blobs / report_sha).read_bytes()
    except OSError as exc:
        raise LandingError(f"prepared REPORT blob missing: {exc}") from exc
    code_trees = tree.build_code_trees(repos, request["run_id"], git_tree.write_tree,
                                       git_tree.list_reserved)
    current = tree.build_delivery_trees(
        repos, request["run_id"], code_trees, report, report_sha,
        git_tree.write_tree, git_tree.list_reserved)
    current_bindings = {name: {key: row[key] for key in ("code_tree", "delivery_tree", "report_sha")}
                        for name, row in current.items()}
    if current_bindings != bound:
        raise LandingError("trees changed since authorization request")
    consumed = consume_authorization(
        store, request_id, session_id=session_id, bindings=current_bindings, at=at,
        now=time.time() if now is None else now)["consumed"]
    path = Path(progress_path) if progress_path is not None else (
        store.paths.run / f"landing-progress-{request_id}.json")
    try:
        commits = commit_repos(
            run_id=request["run_id"], integration_roots=integration_roots,
            bases=dict(bases), bindings=current, message=message, progress_path=path,
            branches=branches)
    except PartialLanding as exc:
        # signed evidence of what moved, before the person sees the error (K2)
        store.append_new(
            writer=store.authority.writer("cli"), kind="partial_land", at=at,
            stage="deliver", attempt=1, ordinal=len(store.read_events()) + 1,
            payload={"landed": dict(sorted(exc.landed.items())), "failed": exc.failed,
                     "detail": str(exc)[:300], "auth_consumed_event_id": consumed["event_id"]})
        raise
    if not any(event.get("kind") == "land" and
               event.get("payload", {}).get("auth_consumed_event_id") == consumed["event_id"]
               for event in store.read_events()):
        store.append_new(
            writer=store.authority.writer("cli"), kind="land", at=at,
            stage="deliver", attempt=1, ordinal=len(store.read_events()) + 1,
            payload={"landing": "human_override", "commit": next(iter(commits.values())),
                     "commits": dict(sorted(commits.items())),
                     "auth_consumed_event_id": consumed["event_id"]},
        )
    return commits
