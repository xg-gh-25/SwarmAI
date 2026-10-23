"""Pipeline-test adapter for v1 call evidence and final reports.

Public symbols:
- ``V1Runner`` — execute/record runner calls and rebuild one repo's final row:
  ``node_results`` (each node's last outcome on the tree), ``trace`` (exits matched on
  their own key), ``run_probe`` (the §A2.5 probe executor).
- ``contract_path`` — locate protected v1 contract metadata for a run.
- ``is_v1_run`` — authoritative contract-based protocol detection.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from core import evidence_seal
from core.run_protocol import events as protocol_events
from core.run_protocol.final import build_repo_final, call_hash
from core.run_protocol.store import NEXT
from core.run_protocol.validations import load_manifest, manifest_digest, required_for
from utils.file_lock import flock_exclusive, flock_unlock


class RunnerError(RuntimeError):
    """V1 runner evidence cannot be safely produced or finalized."""


def contract_path(data_dir: str | Path, run_id: str) -> Path:
    if not re.fullmatch(r"run_[A-Za-z0-9_-]+", run_id or ""):
        raise RunnerError("unsafe run id")
    return Path(data_dir) / "state" / "pipeline_agent_audit" / "runs" / run_id / "contract.json"


def read_contract(data_dir: str | Path, run_id: str) -> dict[str, Any] | None:
    path = contract_path(data_dir, run_id)
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RunnerError(f"v1 contract is unreadable: {exc}") from exc
    if not isinstance(value, dict) or value.get("run_id") != run_id \
            or value.get("protocol_version") != 1:
        raise RunnerError("v1 contract identity/version mismatch")
    return value


def integration_branches_path(data_dir: str | Path, run_id: str) -> Path:
    """Where the engine records each repo's integration branch at worktree creation
    (the audit area: agents cannot write it)."""
    return contract_path(data_dir, run_id).parent / "integration_branches.json"


def read_integration_branches(data_dir: str | Path, run_id: str) -> dict[str, str]:
    try:
        value = json.loads(integration_branches_path(data_dir, run_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in value.items()} if isinstance(value, dict) else {}


def carry_plan_path(data_dir: str | Path, run_id: str) -> Path:
    """The tests a moved main reaches, per repo (§C5.3, conformance F15): written by the
    engine when the integration base moved into the final's dependency scope, read by
    the runner's recheck. In the audit area: agents cannot write it."""
    return contract_path(data_dir, run_id).parent / "carry_plan.json"


def read_carry_plan(data_dir: str | Path, run_id: str, repo_name: str,
                    code_tree: str) -> list[str] | None:
    """The affected tests of *repo_name* for exactly *code_tree* (the synced tree the
    engine computed them for), or None when there is no such plan. An EMPTY list is a
    plan too: no test could be named, so the whole regression scope must run."""
    try:
        value = json.loads(carry_plan_path(data_dir, run_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    row = value.get(repo_name) if isinstance(value, dict) else None
    if not isinstance(row, dict) or row.get("code_tree") != code_tree \
            or not isinstance(row.get("affected_tests"), list):
        return None
    return [str(test) for test in row["affected_tests"]]


def is_v1_run(data_dir: str | Path, run_id: str) -> bool:
    return read_contract(data_dir, run_id) is not None


def event_channel(run_id: str, contract: Mapping[str, Any]):
    """(store, runner writer) of *run_id*. In the test runner's process the store is the
    daemon's (``runner_client``: the key never leaves the daemon, B-class B-b); in the
    daemon itself it is the local store, checked against the contract's authority."""
    from core.run_protocol import runner_client
    if runner_client.active():
        return runner_client.RemoteStore(run_id), None
    data_dir = os.environ.get("SWARM_DATA_DIR")
    if not data_dir:
        from config import get_app_data_dir
        data_dir = str(get_app_data_dir())
    audit_root = Path(data_dir) / "state" / "pipeline_agent_audit"
    from core.run_protocol import keys
    try:
        authority = keys.load(data_dir, run_id)
    except keys.KeyUnavailable as exc:
        raise RunnerError(f"runner event authority unavailable: {exc}") from exc
    expected = contract.get("event_authority_id")
    if not isinstance(expected, str) or expected != authority.authority_id:
        raise RunnerError("contract event authority does not match runner authority")
    # the same lifecycle-validated store as the CLI's (K3 review r2): no runner event
    # can land after a trigger whose transition is still due
    from core.run_protocol.engine import _store
    return _store(audit_root, run_id, authority, producer="runner"), authority.writer("runner")


def _named(spec: tuple, store):
    """A store refusal by name: sent as-is to the daemon's store, made into the check
    itself for a local one (the two cannot drift: runner_service._refusal)."""
    from core.run_protocol import runner_client
    if isinstance(store, runner_client.RemoteStore):
        return spec
    from core.run_protocol.runner_service import _refusal
    return _refusal(list(spec), store.authority)


def _stage_attempt(store) -> "tuple[str, int]":
    """The run's current stage and its attempt, from the signed log."""
    from core.run_protocol import runner_client
    if isinstance(store, runner_client.RemoteStore):
        value = runner_client.call("stage_attempt")
        return str(value["stage"]), int(value["attempt"])
    from core.run_protocol import lifecycle
    state = lifecycle.fold(store.read_events(), store.authority)
    stage = str(state.get("stage") or "run")
    return stage, int((state.get("stage_attempts") or {}).get(stage) or 1)


def record_infrastructure_failed(run_id: str, contract: Mapping[str, Any],
                                 failure: Any) -> dict[str, Any] | None:
    """The run's infrastructure failed under a test call (conformance F11): the run
    ends ``failed``. None when it already ended. Needs no V1Runner: the failure may
    have come before one could be built (the code tree's own git objects)."""
    from core.run_protocol import lifecycle
    from core.run_protocol.store import StoreError
    store, writer = event_channel(run_id, contract)
    try:
        event, _ = store.append_new(
            writer=writer, kind="infrastructure_failed", at=_now(), ordinal=time.time_ns(),
            payload={"reason": f"{failure.component}: {failure}"[:300]},
            # one per run, checked under the run lock: a second reporter (another
            # repo's runner) records nothing (K3 review r1)
            refuse=_named(("infrastructure",), store))
    except StoreError as exc:
        if str(exc).startswith(lifecycle.ALREADY_ENDED):
            return None
        raise
    return event


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha_json(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class V1Runner:
    """Append signed call events plus sealed receipts for one repository.

    A caller cannot submit a pass/fail label. ``record_call`` executes the process and
    derives the result from its return code. ``record_pipeline_call`` derives the result
    from pipeline_test's sealed evidence file.
    """

    def __init__(self, *, run_dir: str | Path, repo_root: str | Path, repo_name: str,
                 run_id: str, base: str, code_tree: str, contract: Mapping[str, Any],
                 python: str):
        self.run_dir = Path(run_dir)
        self.repo_root = Path(repo_root).resolve()
        self.repo_name = repo_name
        self.run_id = run_id
        self.base = base
        self.code_tree = code_tree
        self.contract = dict(contract)
        self.python = python
        if self.contract.get("run_id") != run_id or self.contract.get("protocol_version") != 1:
            raise RunnerError("contract does not identify this v1 run")
        expected = self.contract.get("expected_repos")
        if not isinstance(expected, list) or repo_name not in expected:
            raise RunnerError("repo is not in the contract expected_repos")
        if not re.fullmatch(r"[0-9a-f]{40}", base or "") or not re.fullmatch(r"[0-9a-f]{40}", code_tree or ""):
            raise RunnerError("base/code_tree must be git object ids")
        self.calls_dir = self.run_dir / "evidence" / "tests" / "calls"
        self.lock_path = self.calls_dir / ".index.lock"
        self._store, self._writer = event_channel(self.run_id, self.contract)

    def _locked(self):
        self.calls_dir.mkdir(parents=True, exist_ok=True)
        handle = open(self.lock_path, "a+", encoding="utf-8")
        flock_exclusive(handle.fileno())
        return handle

    def _all_calls(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for path in sorted(self.calls_dir.glob("*.json")):
            try:
                raw, ok, why = evidence_seal.read_checked(path)
                value = json.loads(raw) if ok and raw is not None else None
            except (OSError, json.JSONDecodeError) as exc:
                raise RunnerError(f"call evidence unreadable: {path.name}: {exc}") from exc
            if not isinstance(value, dict):
                raise RunnerError(f"call evidence rejected: {path.name}: {why}")
            rows.append(value)
        return rows

    def _calls(self, *, current_tree_only: bool = False) -> list[dict[str, Any]]:
        events = {event["event_id"]: event for event in self._store.read_events()
                  if event.get("kind") == "test_call"}
        rows: list[dict[str, Any]] = []
        for value in self._all_calls():
            if value.get("repo") != self.repo_name:
                continue
            if current_tree_only and value.get("code_tree") != self.code_tree:
                continue
            record_hash = value.get("record_hash")
            event = events.get(value.get("event_id"))
            if not isinstance(record_hash, str) or record_hash != call_hash(value):
                raise RunnerError("call record hash mismatch")
            if not isinstance(event, Mapping) or protocol_events.event_digest(dict(event)) != value.get("event_hash"):
                raise RunnerError("call event binding is missing or stale")
            payload = event.get("payload")
            details = payload.get("details") if isinstance(payload, Mapping) else None
            if event.get("producer") != "runner" or not isinstance(payload, Mapping) \
                    or payload.get("seq") != value.get("seq") \
                    or payload.get("call_type") != value.get("call_type") \
                    or payload.get("code_tree") != value.get("code_tree") \
                    or payload.get("result") != value.get("result") \
                    or not isinstance(details, list) or f"record_hash:{record_hash}" not in details:
                raise RunnerError("call does not match its signed runner event")
            row = dict(value)
            row["event"] = dict(event)
            rows.append(row)
        return sorted(rows, key=lambda row: row["seq"])

    def _record_result(self, *, call_type: str, argv: Sequence[str], tool_version: str,
                       result: str, validation_id: str | None = None,
                       target: str | None = None, details: Any = None,
                       pre_existing: bool = False, started_at: float,
                       finished_at: float, code_tree: str | None = None,
                       snapshot: str | None = None) -> dict[str, Any]:
        lock = self._locked()
        try:
            calls = self._all_calls()
            event_seqs = [event.get("payload", {}).get("seq")
                          for event in self._store.read_events()
                          if event.get("kind") == "test_call"]
            seq = max([int(row["seq"]) for row in calls]
                      + [value for value in event_seqs if isinstance(value, int)],
                      default=0) + 1
            bound_tree = code_tree or self.code_tree
            if not re.fullmatch(r"[0-9a-f]{40}", bound_tree):
                raise RunnerError("call code_tree must be a git tree id")
            row = {"seq": seq, "repo": self.repo_name, "call_type": call_type,
                   "code_tree": bound_tree, "argv": list(argv),
                   "tool_version": tool_version, "result": result,
                   "started_at": started_at, "finished_at": finished_at}
            if validation_id is not None:
                row["validation_id"] = validation_id
            if target is not None:
                row["target"] = target
            if details is not None:
                row["details"] = details
            if pre_existing:
                row["pre_existing"] = True
            if snapshot is not None:
                if snapshot not in {"old", "new", "current"}:
                    raise RunnerError("call snapshot is invalid")
                row["snapshot"] = snapshot
            record_hash = call_hash(row)
            event_payload = {"seq": seq, "call_type": call_type,
                             "code_tree": bound_tree, "result": result,
                             "details": [f"record_hash:{record_hash}",
                                         f"argv_sha256:{_sha_json(list(argv))}",
                                         f"tool_version:{tool_version}"]}
            if target is not None:
                event_payload["target"] = target
            if snapshot is not None:
                event_payload["snapshot"] = snapshot
            event, _ = self._store.append_new(
                writer=self._writer, kind="test_call", at=_now(), payload=event_payload)
            row.update(record_hash=record_hash, event_id=event["event_id"],
                       event_hash=protocol_events.event_digest(event))
            path = self.calls_dir / f"{seq:06d}-{call_type}.json"
            warning = evidence_seal.write_sealed(path, json.dumps(row, sort_keys=True))
            if warning:
                raise RunnerError(warning)
            return {**row, "event": event}
        finally:
            flock_unlock(lock.fileno())
            lock.close()

    def record_call(self, *, call_type: str, argv: Sequence[str], tool_version: str,
                    validation_id: str | None = None, target: str | None = None,
                    timeout: float = 90.0, cwd: str | Path | None = None,
                    env: Mapping[str, str] | None = None) -> dict[str, Any]:
        """Execute one subprocess and record its result; callers cannot supply a verdict."""
        started = time.time()
        detail: dict[str, Any]
        try:
            completed = subprocess.run(
                list(argv), cwd=Path(cwd).resolve() if cwd is not None else self.repo_root,
                env=dict(env) if env is not None else None, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
            )
            verdict = "pass" if completed.returncode == 0 else "fail"
            detail = {"returncode": completed.returncode,
                      "stdout": completed.stdout[-8192:].decode("utf-8", "replace"),
                      "stderr": completed.stderr[-8192:].decode("utf-8", "replace")}
        except subprocess.TimeoutExpired as exc:
            verdict = "error"
            detail = {"timeout": timeout,
                      "stdout": (exc.stdout or b"")[-8192:].decode("utf-8", "replace"),
                      "stderr": (exc.stderr or b"")[-8192:].decode("utf-8", "replace")}
        return self._record_result(
            call_type=call_type, argv=argv, tool_version=tool_version, result=verdict,
            validation_id=validation_id, target=target, details=detail,
            started_at=started, finished_at=time.time())

    def record_pipeline_call(self, *, argv: Sequence[str], tool_version: str,
                             target: str, output: Mapping[str, Any],
                             exit_code: int) -> dict[str, Any]:
        """Record pipeline_test output, deriving its verdict from sealed runner evidence."""
        evidence_path = output.get("evidence")
        evidence: dict[str, Any] | None = None
        if isinstance(evidence_path, str):
            path = Path(evidence_path).resolve()
            root = (self.run_dir / "evidence").resolve()
            if root not in path.parents:
                raise RunnerError("pipeline receipt is outside this run's evidence")
            raw, ok, why = evidence_seal.read_checked(path)
            if not ok or raw is None:
                raise RunnerError(f"pipeline receipt seal rejected: {why}")
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise RunnerError(f"pipeline receipt is malformed: {exc}") from exc
            if not isinstance(value, dict) or value.get("run_id") != self.run_id:
                raise RunnerError("pipeline receipt belongs to another run")
            evidence = value
        if evidence is None:
            verdict = "not_run" if output.get("continue") or output.get("skipped_for_budget_total") else "error"
            details = {"error": output.get("error"), "receipt": None}
        else:
            blocking = [row for row in evidence.get("failures") or []
                        if not (isinstance(row, Mapping)
                                and row.get("classification") in {"pre-existing", "flaky"}
                                and row.get("outcome") != "timeout")]
            derived_exit = 1 if evidence.get("runner_errors") or blocking else 0
            if exit_code not in {0, 1} or exit_code != derived_exit:
                verdict = "error"
            elif output.get("continue") or output.get("skipped_for_budget_total") \
                    or evidence.get("risk_overflow"):
                verdict = "not_run"
            else:
                verdict = "pass" if derived_exit == 0 else "fail"
            details = {"evidence": evidence_path, "evidence_sha256": _sha_json(evidence),
                       "counts": evidence.get("counts"),
                       "failures": len(evidence.get("failures") or [])}
        now = time.time()
        return self._record_result(
            call_type="test", argv=argv, tool_version=tool_version, result=verdict,
            target=target, details=details,
            pre_existing=verdict == "pass" and bool(evidence and evidence.get("failures")),
            started_at=now, finished_at=now)

    def record_cap_exceeded(self, cap: str) -> dict[str, Any] | None:
        """metric(cap_exceeded.<cap>) for a runner-side count cap (full-scope rounds,
        fix rechecks, the circuit breaker's continuations): conformance F18. One per
        cap per stage attempt of the run (idempotent)."""
        from core.run_protocol import lifecycle
        from core.run_protocol.store import StoreError
        if cap not in lifecycle.CAPS or cap in ("stage_attempt", "land_attempt",
                                                "gate0_round", "dispatch"):
            raise RunnerError(f"unknown runner cap {cap!r}")
        stage, attempt = _stage_attempt(self._store)
        try:
            event, _ = self._store.append_new(
                writer=self._writer, kind="metric", at=_now(), stage=stage, attempt=attempt,
                payload={"name": f"cap_exceeded.{cap}", "value": 1, "cap": cap},
                refuse=_named(("cap", cap, stage, attempt), self._store))
        except StoreError as exc:
            if str(exc) != "cap already recorded":
                raise
            return None
        return event

    def record_cache_conflict(self, *, key: str, first_hash: str, new_hash: str,
                              kind: str = "test") -> dict[str, Any]:
        """Write a signed cross-run integrity event for a poisoned v1 cache key of
        *kind* (``test`` results or ``baseline`` outcomes)."""
        if kind not in ("test", "baseline"):
            raise RunnerError(f"unknown cache kind {kind!r}")
        event, _ = self._store.append_new(
            writer=self._writer, kind="integrity", at=_now(), ordinal=time.time_ns(),
            payload={"conflict_id": key, "conflict_type": f"cross_run_{kind}_cache",
                     "first_hash": first_hash, "new_hash": new_hash})
        return event

    def run_validation(self, entry: Mapping[str, Any], *, timeout: float = 90.0) -> dict[str, Any]:
        """Run one human-reviewed base-manifest command with a confined cwd and env."""
        argv = [self.python if arg == "python" else arg for arg in entry["argv"]]
        cwd = (self.repo_root / entry["cwd"]).resolve()
        if cwd != self.repo_root and self.repo_root not in cwd.parents:
            raise RunnerError("validation cwd leaves repo")
        env = {key: value for key, value in os.environ.items()
               if key in {"PATH", "HOME", "LANG", "LC_ALL", "SWARM_DATA_DIR"}}
        return self.record_call(
            call_type=entry["call_type"], argv=argv, tool_version="manifest-v1",
            validation_id=entry["id"], timeout=timeout, cwd=cwd, env=env)

    def manifest_identity(self, changed_paths: Sequence[str]) -> dict[str, Any]:
        manifest = load_manifest(str(self.repo_root), self.base)
        required = required_for(manifest, changed_paths)
        return {"sha256": manifest_digest(manifest), "base": self.base,
                "required_validation_ids": [entry["id"] for entry in required]}

    def record_proof_result(self, *, output: Mapping[str, Any], exit_code: int,
                            bindings: Mapping[str, str]) -> dict[str, Any]:
        """Seal one prove receipt and append correlated runner proof events.

        ``bindings`` maps each canonical finding ID to the exact break-test nodeid.
        Only valid old-old-new proofs emit a closing proof event; pending/invalid rows
        remain visible in the receipt without changing finding state.
        """
        evidence_path = output.get("evidence")
        if not isinstance(evidence_path, str):
            raise RunnerError("prove output lacks evidence")
        path = Path(evidence_path).resolve()
        root = (self.run_dir / "evidence").resolve()
        if root not in path.parents:
            raise RunnerError("prove receipt is outside this run's evidence")
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise RunnerError(f"prove receipt unreadable: {exc}") from exc
        warning = evidence_seal.write_sealed(path, raw)
        if warning:
            raise RunnerError(warning)
        checked, ok, why = evidence_seal.read_checked(path)
        if not ok or checked is None:
            raise RunnerError(f"prove receipt seal rejected: {why}")
        try:
            evidence = json.loads(checked)
        except json.JSONDecodeError as exc:
            raise RunnerError(f"prove receipt is malformed: {exc}") from exc
        proofs = evidence.get("proofs") if isinstance(evidence, Mapping) else None
        old_tree = evidence.get("overlay_tree") if isinstance(evidence, Mapping) else None
        round_name = evidence.get("round") if isinstance(evidence, Mapping) else None
        if evidence.get("run_id") != self.run_id or not isinstance(proofs, list) \
                or not re.fullmatch(r"[0-9a-f]{40}", str(old_tree or "")) \
                or not isinstance(round_name, str) or not re.fullmatch(r"R[12]", round_name):
            raise RunnerError("prove receipt identity is invalid")
        if not isinstance(bindings, Mapping) or not bindings:
            raise RunnerError("v1 prove requires canonical finding bindings")
        if len(set(bindings.values())) != len(bindings) \
                or any(not re.fullmatch(r"CF-[0-9a-f]{8}", str(key))
                       or not isinstance(value, str) or "::" not in value
                       for key, value in bindings.items()):
            raise RunnerError("prove bindings must uniquely map CF ids to nodeids")
        by_node = {row.get("nodeid"): row for row in proofs if isinstance(row, Mapping)}
        if set(bindings.values()) != set(by_node):
            raise RunnerError("prove bindings do not exactly match receipt nodeids")
        if exit_code not in {0, 1}:
            raise RunnerError("prove runner exit code is invalid")
        argv_base = ["pipeline_test.py", "--mode", "prove", "--round", round_name]
        emitted: list[dict[str, Any]] = []
        for canonical_id, nodeid in sorted(bindings.items()):
            row = by_node[nodeid]
            if row.get("verdict") != "valid":
                continue
            old = row.get("old")
            new = row.get("new")
            if old != ["assert_fail", "assert_fail"] or new != "pass":
                raise RunnerError("valid prove row lacks old-old-new outcomes")
            call_ids = []
            for snapshot, tree_id, result in (
                    ("old", old_tree, old[0]), ("old", old_tree, old[1]),
                    ("new", self.code_tree, new)):
                now = time.time()
                call = self._record_result(
                    call_type="test", argv=[*argv_base, "--files", nodeid],
                    tool_version="pipeline-prove-v1", result=result,
                    target=nodeid, details={"receipt": str(path)},
                    started_at=now, finished_at=now,
                    code_tree=tree_id, snapshot=snapshot)
                call_ids.append(call["event_id"])
            pass_number = int(round_name[1:])
            attempt = 1 + sum(
                event.get("kind") == "proof"
                and event.get("payload", {}).get("canonical_id") == canonical_id
                and event.get("payload", {}).get("pass") == pass_number
                for event in self._store.read_events())
            proof, _ = self._store.append_new(
                writer=self._writer, kind="proof", at=_now(), stage="review",
                attempt=attempt,
                payload={"canonical_id": canonical_id, "pass": pass_number,
                         "evidence_type": "test", "code_tree": self.code_tree,
                         "result": "proven", "old_runs": list(old),
                         "new_run": new, "old_code_tree": old_tree,
                         "test_node": nodeid,
                         "test_call_event_ids": call_ids})
            emitted.append(proof)
        return {"proof_events": emitted, "evidence": str(path)}

    def finalize(self, *, changed_paths: Sequence[str], runner_code_hash: str,
                 pipeline_code_hash: str, runner_argv: Sequence[str],
                 selection: Mapping[str, Any], cache_keys: Sequence[str],
                 main_red: Mapping[str, Any],
                 trace: Sequence[Mapping[str, Any]],
                 node_results: Mapping[str, Mapping[str, Any]] | None = None,
                 tdd_required: Sequence[str] | None = None,
                 whole_files: Sequence[str] | None = None,
                 collected: Sequence[str] | None = None) -> dict[str, Any]:
        manifest = load_manifest(str(self.repo_root), self.base)
        required = required_for(manifest, changed_paths)
        final = build_repo_final(
            repo=self.repo_name, code_tree=self.code_tree, base=self.base,
            calls=self._calls(current_tree_only=True),
            required_validation_ids=[entry["id"] for entry in required],
            validation_manifest=manifest, runner_code_hash=runner_code_hash,
            pipeline_code_hash=pipeline_code_hash,
            contract_pipeline_code_hash=str(self.contract.get("pipeline_code_hash") or ""),
            runner_argv=runner_argv, selection=selection, cache_keys=cache_keys,
            main_red=main_red, trace=trace,
            tests_required=any(not path.endswith((".md", ".txt", ".rst"))
                               for path in changed_paths),
            node_results=node_results, tdd_required=tdd_required,
            whole_files=whole_files, collected=collected,
            # the final is a file, not an event: its own time is on it (L5, §A3.6)
            generated_at=datetime.now(timezone.utc).isoformat(),
        )
        path = self.run_dir / "evidence" / "tests" / f"final-{self.repo_name}.json"
        warning = evidence_seal.write_sealed(path, json.dumps(final, sort_keys=True))
        if warning:
            raise RunnerError(warning)
        return final

    def node_results(self) -> dict[str, dict[str, Any]]:
        """Each test node's LAST outcome on the current code tree (fixing spec B2).

        Read from the sealed receipts of this tree's pipeline calls, in call order: a
        later run of a node replaces an earlier one, so a continuation's ``not_run``
        is cleared by the call that finishes it. Keys are nodeids; a file skipped for
        budget is ``<file>::*`` until a later call runs that file; a call that left no
        receipt is ``call:<seq>`` until a later call with a receipt. Values:
        ``{"result": pass | fail | incomplete, "seq": n}`` — a failure whose base
        classification is unknown is ``incomplete``, not red."""
        out: dict[str, dict[str, Any]] = {}
        for call in self._calls(current_tree_only=True):
            if call.get("call_type") != "test":
                continue
            seq = int(call["seq"])
            details = call.get("details") if isinstance(call.get("details"), Mapping) else {}
            evidence = (self._receipt(str(details["evidence"]), str(details.get("evidence_sha256")))
                        if details.get("evidence") else None)
            if evidence is None:
                if call.get("result") in {"error", "not_run"}:
                    out[f"call:{seq}"] = {"result": "incomplete", "seq": seq}
                continue
            for key in [k for k in out if k.startswith("call:")]:
                del out[key]
            classes = {row.get("nodeid"): row.get("classification")
                       for row in evidence.get("failures") or [] if isinstance(row, Mapping)}
            for row in evidence.get("results") or []:
                if not isinstance(row, Mapping) or not isinstance(row.get("nodeid"), str):
                    continue
                node, outcome = row["nodeid"], row.get("outcome")
                if outcome in {"passed", "skipped"}:
                    result = "pass"
                elif classes.get(node) in {"pre-existing", "flaky"} and outcome != "timeout":
                    result = "pass"
                elif classes.get(node) == "unknown":
                    result = "incomplete"
                elif outcome == "timeout":
                    result = "incomplete"
                else:
                    result = "fail"
                out[node] = {"result": result, "seq": seq}
                out.pop(f"{row.get('file') or node.split('::')[0]}::*", None)
            for path in evidence.get("skipped_for_budget") or []:
                if isinstance(path, str) and not any(
                        key.startswith(path + "::") for key in out if not key.endswith("::*")):
                    out[f"{path}::*"] = {"result": "incomplete", "seq": seq}
        return out

    def whole_file_runs(self) -> list[str]:
        """Test files a sealed call on the current code tree ran WHOLE — named without a
        ``::node`` and not skipped for budget. A file run only as picked nodes is not
        here, so one passing node cannot stand for its file (direct's BX-2)."""
        out: set[str] = set()
        for call in self._calls(current_tree_only=True):
            details = call.get("details") if isinstance(call.get("details"), Mapping) else {}
            if call.get("call_type") != "test" or not details.get("evidence"):
                continue
            evidence = self._receipt(str(details["evidence"]), str(details.get("evidence_sha256")))
            if evidence is None:
                continue
            skipped = {p for p in evidence.get("skipped_for_budget") or [] if isinstance(p, str)}
            out |= {f for f in evidence.get("whole_files") or []
                    if isinstance(f, str) and f not in skipped}
        return sorted(out)

    def _receipt(self, path_text: str, digest: str) -> dict[str, Any] | None:
        """The sealed receipt at *path_text* inside this run's evidence, only if its
        content still hashes to the *digest* the signed call recorded."""
        try:
            path = Path(path_text).resolve()
            root = (self.run_dir / "evidence").resolve()
            if root not in path.parents:
                return None
            raw, ok, _why = evidence_seal.read_checked(path)
            evidence = json.loads(raw) if ok and raw is not None else None
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(evidence, dict) or _sha_json(evidence) != digest:
            return None
        return evidence

    def trace(self, exits: Sequence[Mapping[str, Any]],
              nodes: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
        """One row per test / probe exit of DESIGN_SPEC, matched ONLY on the exit's own
        key (fixing spec B1): a test exit on its nodeid, a probe exit on its row id (the
        target of the probe call). An exit with no result on this tree gets no row."""
        probes = {}
        for call in self._calls(current_tree_only=True):
            if call.get("call_type") in ("probe", "mutation") \
                    and isinstance(call.get("target"), str):
                probes[(call["call_type"], call["target"])] = call
        rows = []
        for exit_row in exits:
            if not isinstance(exit_row, Mapping):
                continue
            if exit_row.get("kind") in ("probe", "mutation"):
                call = probes.get((exit_row["kind"], str(exit_row.get("id") or "")))
                if call is not None:
                    rows.append({"exit": str(exit_row["id"]), "kind": exit_row["kind"],
                                 "covers": list(exit_row.get("covers") or []),
                                 "ref": str(exit_row["id"]), "result": call["result"],
                                 "seq": int(call["seq"])})
                continue
            if exit_row.get("kind") != "test":
                continue
            node = nodes.get(str(exit_row.get("ref") or ""))
            if node is None:
                continue
            rows.append({"exit": str(exit_row.get("id") or ""), "kind": "test",
                         "covers": list(exit_row.get("covers") or []),
                         "ref": str(exit_row.get("ref")),
                         "result": {"pass": "pass", "fail": "fail"}.get(node["result"], "not_run"),
                         "seq": int(node["seq"])})
        return rows

    def run_probe(self, exit_row: Mapping[str, Any]) -> dict[str, Any]:
        """Execute one DESIGN_SPEC probe exit under the frozen §A2.5 protocol.

        Validated again right before execution; argv list, no shell; only the fixed
        environment; stdin /dev/null; 30 s; stdout capped at 256 KB (more is error);
        code_tree computed before and after — a change is error plus an integrity
        event. The result is recorded as a runner-signed ``probe`` call whose target is
        the exit's row id, so the trace matches it by that id (fixing spec B1/B4)."""
        import tempfile
        from core.run_protocol import git_tree, probe as probe_protocol
        exit_id = str(exit_row.get("id") or "")
        if not exit_id or exit_row.get("kind") != "probe":
            raise RunnerError("run_probe needs a probe exit with an id")
        probe = probe_protocol.validate(exit_row.get("argv"), exit_row.get("expect") or {})
        excludes = (f".aidlc/deliverables/{self.run_id}/",)
        before = git_tree.write_tree(str(self.repo_root), excludes, {})
        if before != self.code_tree:
            raise RunnerError("the worktree moved since this runner was built")
        started = time.time()
        with tempfile.TemporaryDirectory(prefix="v1-probe-") as temp_root:
            home = os.path.join(temp_root, "home")
            os.mkdir(home, 0o700)
            spec = probe_protocol.execution_spec(
                probe, repo_root=str(Path(self.repo_root).resolve()),
                allowed_repo_roots=[str(Path(self.repo_root).resolve())],
                temp_home=home, temp_root=temp_root)
            rc, stdout, overflow = None, b"", False
            timed_out = threading.Event()
            proc = subprocess.Popen(spec["argv"], cwd=spec["cwd"], env=spec["env"],
                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, shell=False)

            def _expire() -> None:
                timed_out.set()
                proc.kill()
            timer = threading.Timer(spec["timeout_seconds"], _expire)
            timer.start()
            try:
                chunks: list[bytes] = []
                size = 0
                assert proc.stdout is not None
                while True:
                    chunk = proc.stdout.read(65536)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > spec["stdout_limit_bytes"]:
                        overflow = True
                        proc.kill()
                        break
                    chunks.append(chunk)
                stdout = b"".join(chunks)
                rc = proc.wait()
            finally:
                timer.cancel()
                proc.stdout.close()
        timed_out = timed_out.is_set()
        after = git_tree.write_tree(str(self.repo_root), excludes, {})
        if after != before:
            self._store.append_new(
                writer=self._writer, kind="integrity", at=_now(),
                payload={"conflict_id": hashlib.sha256(f"probe:{exit_id}:{after}".encode()).hexdigest(),
                         "conflict_type": "probe_changed_tree",
                         "first_hash": hashlib.sha256(before.encode()).hexdigest(),
                         "new_hash": hashlib.sha256(after.encode()).hexdigest()},
                ordinal=NEXT)
        verdict = probe_protocol.evaluate_result(
            probe, rc=None if timed_out else rc, stdout=stdout, timed_out=timed_out,
            overflow=overflow, tree_changed=after != before)
        return self._record_result(
            call_type="probe", argv=spec["argv"], tool_version="git", result=verdict["result"],
            target=exit_id, details={"reason": verdict["reason"], "rc": rc,
                                     "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
                                     "stdout_bytes": len(stdout)},
            started_at=started, finished_at=time.time())

    def required_validations(self, changed_paths: Sequence[str]) -> list[dict[str, Any]]:
        return required_for(load_manifest(str(self.repo_root), self.base), changed_paths)

    def prior_selection(self) -> dict[str, Any]:
        path = self.run_dir / "evidence" / "tests" / f"final-{self.repo_name}.json"
        if not path.exists():
            return {}
        try:
            raw, ok, _why = evidence_seal.read_checked(path)
            value = json.loads(raw) if ok and raw is not None else None
        except (OSError, json.JSONDecodeError):
            return {}
        provenance = value.get("provenance") if isinstance(value, Mapping) else None
        selection = provenance.get("selection") if isinstance(provenance, Mapping) else None
        return dict(selection) if isinstance(selection, Mapping) else {}
