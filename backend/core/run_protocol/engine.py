"""Direct-first execution engine for the Pipeline v1 protocol.

Public symbols:
- ``RunEngine.create`` / ``RunEngine.load`` — durable v1 run lifecycle.
- ``stage_start`` / ``stage_complete`` — cards, documents, checks and transitions.
- ``normal_land`` / ``finish_not_landed`` — landing; every not-landed outcome's path.
- ``_store`` — the run's store: an event after which the log would not fold is refused.
- ``review_start`` — lightweight R1 handouts bound to the current code trees.
- ``find_run_dir`` — resolve one v1 run without trusting caller-supplied project state.
"""
from __future__ import annotations

import hashlib
import json
import re
import logging
import os
import secrets
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from core import evidence_seal, run_worktree
from core.run_protocol import checks, git_tree, lifecycle, review
from core.run_protocol.documents import DocumentStore, complete_stage
from core.run_protocol.events import EventAuthority
from core.run_protocol.final import carry_repo_final, merge_final
from core.run_protocol.store import AuditStore, StoreError

logger = logging.getLogger(__name__)


class EngineError(RuntimeError):
    """The run cannot proceed safely or its durable protocol state is invalid."""

    code = "engine_error"


class IntegrationRequired(EngineError):
    """A safe operation requires wiring owned outside the v1 CLI process."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with open(temp, "x", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.flush(); os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        try: temp.unlink()
        except FileNotFoundError: pass


_HASH_COMMENT = re.compile(r"#(?!!)")             # a shebang (#!) is executable
_SLASH_COMMENT = re.compile(r"(//|/\*|\*/|\*(\s|$))")   # not `*gen()` / `*[Symbol…]`
_LINE_COMMENT = {".py": _HASH_COMMENT, ".sh": _HASH_COMMENT, ".ts": _SLASH_COMMENT,
                 ".tsx": _SLASH_COMMENT, ".js": _SLASH_COMMENT, ".jsx": _SLASH_COMMENT}


def _comment_only_diff(file: str, diff: str) -> bool:
    """True when nothing executable changed (F-8): a document, or a source file whose
    every added / removed line is blank or a whole-line comment. A docstring is a string
    literal and counts as code; so does a trailing comment on a code line."""
    suffix = Path(file).suffix.lower()
    if suffix in {".md", ".txt", ".rst"}:
        return True
    markers = _LINE_COMMENT.get(suffix)
    if markers is None:
        return False
    changed = [line[1:] for line in diff.splitlines()
               if line[:1] in "+-" and not line.startswith(("+++", "---"))]
    return bool(changed) and all(not text.strip() or markers.match(text.lstrip())
                                 for text in changed)


def _git(root: str | Path, *args: str) -> str:
    try:
        return subprocess.run(["git", "-C", str(root), *args], check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, timeout=60).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise EngineError(f"git {' '.join(args[:2])} failed: {exc}") from exc


def find_run_dir(workspace: str | Path, run_id: str) -> Path:
    matches = list((Path(workspace) / "Projects").glob(f"*/.artifacts/runs/{run_id}"))
    matches = [path for path in matches if path.is_dir()]
    if len(matches) != 1:
        raise EngineError(f"run {run_id} resolved to {len(matches)} directories")
    return matches[0]


def _agent_after_cap(requirements: Any, design: Any) -> bool:
    """DC-2's fact (design §A1.2): after the decision cap the agent's own choice is
    written into the documents as ``decided_by: agent_after_cap`` (a clarification of
    REQUIREMENTS, or DESIGN_SPEC ``chosen``). Read from there — no flag to trust."""
    rows = requirements.get("clarifications") if isinstance(requirements, Mapping) else None
    if any(isinstance(row, Mapping) and row.get("decided_by") == "agent_after_cap"
           for row in rows or []):
        return True
    chosen = design.get("chosen") if isinstance(design, Mapping) else None
    return isinstance(chosen, Mapping) and chosen.get("decided_by") == "agent_after_cap"


def _store(root: Path, run_id: str, authority: EventAuthority) -> AuditStore:
    """The run's store, refusing any event after which the log would not fold.

    One rule for every writer (fixing spec A): an event the lifecycle or the finding
    ledger cannot apply is never written, and the transition an event triggers is
    appended in the same lock."""
    from core.run_protocol import findings as finding_protocol

    def validate(chain: list[dict[str, Any]]) -> Any:
        state = lifecycle.fold(chain, authority, allow_pending_transition=True)
        if chain[-1].get("kind") in _FINDING_KINDS:
            finding_protocol.fold(chain, authority)
        return state.get("expected_transition")
    return AuditStore(root, run_id, authority, validator=validate,
                      transition_writer=authority.writer("cli"))


_FINDING_KINDS = frozenset({"answer", "answer_reuse", "finding_occ", "finding_status",
                            "reclassify", "address", "proof"})


REQUIREMENT_MAX = 4000


class RunEngine:
    """One v1 run, projected from signed events and immutable contract metadata."""

    def __init__(self, *, data_dir: Path, workspace: Path, project: str,
                 run_id: str, authority: EventAuthority):
        self.data_dir, self.workspace = data_dir, workspace
        self.project, self.run_id = project, run_id
        self.run_dir = workspace / "Projects" / project / ".artifacts" / "runs" / run_id
        self.audit_root = data_dir / "state" / "pipeline_agent_audit"
        self.store = _store(self.audit_root, run_id, authority)
        self.authority = authority
        try:
            self.contract = json.loads(self.store.paths.contract.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise EngineError(f"contract unreadable: {exc}") from exc

    @classmethod
    def create(cls, *, data_dir: str | Path, workspace: str | Path, project: str,
               profile: str, session_id: str, repos: Mapping[str, str],
               mode: str = "isolated", decision_cap: int = 2,
               risk_test_file_cap: int = 200, run_id: str | None = None,
               transcript_ids: Sequence[str] = (),
               requirement: str | None = None) -> "RunEngine":
        if profile not in lifecycle.PROFILE_STAGES or mode not in {"isolated", "shared"}:
            raise EngineError("unknown profile or mode")
        if requirement is not None and (not isinstance(requirement, str)
                                        or len(requirement) > REQUIREMENT_MAX):
            raise EngineError(f"requirement must be text of at most {REQUIREMENT_MAX} chars")
        requirement = requirement.strip() if requirement else ""
        if profile == "direct" and not requirement:
            # direct has no REQUIREMENTS document: the user's own words ARE the requirement
            raise EngineError("direct needs --requirement: the user's request, verbatim")
        if mode == "isolated" and profile != "research" and not repos:
            raise EngineError("code profile requires at least one repo")
        run_id = run_id or "run_" + secrets.token_hex(4)
        from core.session_ids import valid_session_id
        if any(not valid_session_id(value) for value in transcript_ids):
            raise EngineError("invalid transcript id")
        workspace_path, data_path = Path(workspace), Path(data_dir)
        run_dir = workspace_path / "Projects" / project / ".artifacts" / "runs" / run_id
        try:
            run_dir.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise EngineError("run already exists") from exc
        audit = data_path / "state" / "pipeline_agent_audit" / "runs" / run_id
        audit.mkdir(parents=True, exist_ok=False, mode=0o700)
        key = secrets.token_bytes(32)
        key_path = audit / "authority.key"
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(key); handle.flush(); os.fsync(handle.fileno())
        authority = EventAuthority(key)
        engine = cls.__new__(cls)
        engine.data_dir, engine.workspace = data_path, workspace_path
        engine.project, engine.run_id, engine.run_dir = project, run_id, run_dir
        engine.audit_root, engine.authority = data_path / "state" / "pipeline_agent_audit", authority
        engine.store = _store(engine.audit_root, run_id, authority)
        from core.run_protocol.cache import pipeline_code_hash
        pipeline_hash = pipeline_code_hash()   # the whole protocol + both CLIs (B7)
        contract = {"run_id": run_id, "project": project,
                    "protocol_version": 1, "profile": profile,
                    "session_id": session_id, "mode": mode,
                    "expected_repos": sorted(repos),
                    "repo_roots": {name: str(Path(root).resolve()) for name, root in repos.items()},
                    "decision_cap": decision_cap, "risk_test_file_cap": risk_test_file_cap,
                    "transcript_ids": list(dict.fromkeys(transcript_ids)),
                    "pipeline_code_hash": pipeline_hash,
                    "event_authority_id": authority.authority_id,
                    "created_at": _now(), "stage_attempt_cap": 3,
                    "regression_round_cap": 2, "recheck_cap": 3}
        if requirement:
            contract["requirement"] = requirement
        engine.contract = contract
        engine.store.write_contract_once(contract)
        engine.store.append_new(
            writer=authority.writer("cli"), kind="run_create", at=contract["created_at"],
            payload={"run_id": run_id, "profile": profile, "decision_cap": decision_cap})
        evidence_seal.enable(run_id)
        engine.write_projection()
        return engine

    @classmethod
    def load(cls, *, data_dir: str | Path, workspace: str | Path,
             project: str, run_id: str) -> "RunEngine":
        workspace_path = Path(workspace)
        resolved_run = find_run_dir(workspace_path, run_id)
        actual_project = resolved_run.parents[2].name
        if project != actual_project:
            raise EngineError(f"run {run_id} belongs to project {actual_project}, not {project}")
        key_path = Path(data_dir) / "state" / "pipeline_agent_audit" / "runs" / run_id / "authority.key"
        try:
            key = key_path.read_bytes()
        except OSError as exc:
            raise EngineError(f"authority unavailable: {exc}") from exc
        engine = cls(data_dir=Path(data_dir), workspace=workspace_path,
                     project=actual_project, run_id=run_id, authority=EventAuthority(key))
        contract_project = engine.contract.get("project")
        if contract_project is not None and contract_project != actual_project:
            raise EngineError("contract project identity mismatch")
        return engine

    def owner_session(self) -> str:
        """The session that drives this run now: the creator, then each signed
        ``owner_transfer`` in order (a transfer that does not start from the current
        owner is ignored). Every human authorization binds to this session."""
        owner = self.contract["session_id"]
        for event in self.store.read_events():
            if event.get("kind") != "owner_transfer":
                continue
            payload = event.get("payload") or {}
            if payload.get("from_session") == owner and payload.get("to_session"):
                owner = payload["to_session"]
        return owner

    def transfer_owner(self, to_session: str, *, reason: str) -> bool:
        """Record that *to_session* now drives the run (daemon-written). False when it
        already owns it or the run is terminal; the caller decides liveness first."""
        from core.session_ids import valid_session_id
        if not valid_session_id(to_session):
            raise EngineError("owner transfer needs a valid session id")
        if self.projection()["status"] in lifecycle.TERMINAL:
            return False
        current = self.owner_session()
        if current == to_session:
            return False
        self.store.append_new(
            writer=self.authority.writer("hook"), kind="owner_transfer", at=_now(),
            ordinal=len(self.store.read_events()) + 1,
            payload={"from_session": current, "to_session": to_session,
                     "reason": reason})
        self.write_projection()
        return True

    def record_stop(self, kind: str, *, reason: str) -> bool:
        """Record ``user_stop`` / ``session_lost``: an ACTIVE run becomes paused; a run
        waiting for a human keeps waiting (no event). Returns whether it paused."""
        if kind not in ("user_stop", "session_lost"):
            raise EngineError(f"unknown stop kind {kind!r}")
        if self.projection()["status"] != "active":
            return False
        self.store.append_new(
            writer=self.authority.writer("cli"), kind=kind, at=_now(),
            ordinal=len(self.store.read_events()) + 1, payload={"reason": reason})
        self._settle(); self.write_projection()
        return True

    def pending_decision(self) -> dict[str, Any] | None:
        """The open decision as the human sees it, with the exact AskUserQuestion
        payload the agent must ask (``ask_user_question``), or None."""
        state = self.projection()
        request_id = state.get("pending_decision")
        if state["status"] != "awaiting_human" or not request_id:
            return None
        value = self._read_json(self.run_dir / "artifacts" / f"decision-{request_id}.json", None)
        if not isinstance(value, Mapping):
            return None
        from core.run_protocol import decision as decision_protocol
        return {"request_id": request_id, "kind": value.get("kind"),
                "question": value.get("question"),
                "why_evidence_insufficient": value.get("why_evidence_insufficient"),
                "options": [{key: row.get(key) for key in
                             ("id", "choice", "consequence", "reversible", "risk")}
                            for row in value.get("options") or [] if isinstance(row, Mapping)],
                "ask_user_question": decision_protocol.ask_payload(value)}

    def projection(self, *, allow_pending: bool = False) -> dict[str, Any]:
        return lifecycle.fold(self.store.read_events(), self.authority,
                              allow_pending_transition=allow_pending)

    def _settle(self) -> None:
        state = self.projection(allow_pending=True)
        expected = state.get("expected_transition")
        if expected:
            self.store.append_new(
                writer=self.authority.writer("cli"), kind="transition", at=_now(),
                payload=expected)

    def effective_repos(self) -> dict[str, str]:
        roots = self.contract["repo_roots"]
        if self.contract["mode"] == "shared":
            return dict(roots)
        out = {}
        for name in self.contract["expected_repos"]:
            path = run_worktree.wt_path(self.run_id, name)
            if path.is_dir():
                out[name] = str(path)
        return out

    def ensure_worktrees(self) -> dict[str, str]:
        if self.contract["mode"] == "shared" or self.contract["profile"] == "research":
            return self.effective_repos()
        current = self.effective_repos()
        if set(current) == set(self.contract["expected_repos"]):
            return current
        created: list[tuple[str, dict]] = []
        try:
            for name in self.contract["expected_repos"]:
                if name in current:
                    continue
                entry = run_worktree.create(
                    self.run_id, name, self.contract["repo_roots"][name], workspace=self.workspace)
                created.append((name, entry))
                self._record_integration_branch(name, entry.get("integration_branch"))
        except run_worktree.WorktreeError as exc:
            for name, entry in created:
                try:
                    run_worktree._remove(self.run_id, entry,
                                         Path(self.contract["repo_roots"][name]), merged=False)
                except run_worktree.WorktreeError:
                    pass
            self.store.append_new(
                writer=self.authority.writer("cli"), kind="worktree_unavailable", at=_now(),
                ordinal=len(self.store.read_events()) + 1, payload={"reason": exc.message})
            self._settle(); self.write_projection()
            raise EngineError(f"worktree unavailable: {exc.message}") from exc
        return self.effective_repos()

    def _read_json(self, path: Path, default: Any) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return default
        except json.JSONDecodeError as exc:
            raise EngineError(f"malformed {path.name}: {exc}") from exc

    def final(self) -> dict[str, Any]:
        if self.contract["profile"] == "research":
            return {}
        rows = {}
        for name in self.contract["expected_repos"]:
            path = self.run_dir / "evidence" / "tests" / f"final-{name}.json"
            raw, ok, why = evidence_seal.read_checked(path) if path.exists() else (None, False, "missing")
            if not ok or raw is None:
                return {}
            try:
                rows[name] = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise EngineError(f"malformed final for {name}: {exc}") from exc
        return merge_final(self.run_id, self.contract["expected_repos"], rows)

    def _record_integration_branch(self, name: str, branch: Any) -> None:
        """The branch the worktree forked from, kept in the audit area (B6 review): the
        base must not follow whatever the main checkout has checked out later."""
        if not isinstance(branch, str) or not branch.startswith("refs/heads/"):
            return
        from core.run_protocol.runner import integration_branches_path
        path = self.store.paths.run / integration_branches_path(".", self.run_id).name
        current = self._read_json(path, {}) or {}
        if current.get(name) != branch:
            _atomic_json(path, {**current, name: branch})

    def _fork_base(self, name: str, root: str) -> str:
        """Where *root* forked from its main checkout's recorded integration branch (the
        run's base, B6); HEAD for a shared run whose root IS the main checkout."""
        from core.run_protocol.runner import run_base
        main = self.contract["repo_roots"].get(name)
        if not main or Path(main).resolve() == Path(root).resolve():
            return _git(root, "rev-parse", "HEAD")
        branches = self._read_json(self.store.paths.run / "integration_branches.json", {}) or {}
        base = run_base(root, main, branches.get(name))
        if not base:
            raise EngineError(f"{name}: cannot find where the run forked from main")
        return base

    def _changed_paths(self, repos: Mapping[str, str]) -> list[str]:
        """Every path the run changed since its base: committed in the worktree (B6),
        staged, unstaged and untracked. The base is the fork point from main."""
        out = []
        for name, root in repos.items():
            base = self._fork_base(name, root)
            out += _git(root, "-c", "core.quotepath=off", "diff", "--name-only",
                        "--no-renames", base, "HEAD").splitlines()
            # name-only listings: _git strips its output, so a porcelain status line's
            # leading space would cost the first path a character
            out += _git(root, "-c", "core.quotepath=off", "diff", "--name-only",
                        "--no-renames", "HEAD").splitlines()
            out += _git(root, "-c", "core.quotepath=off", "ls-files", "--others",
                        "--exclude-standard").splitlines()
        return sorted({p for p in out if p})

    def check_context(self) -> checks.CheckContext:
        requirements = self._read_json(self.run_dir / "artifacts" / "requirements.json", {})
        design = self._read_json(self.run_dir / "artifacts" / "design_spec.json", None)
        final = self.final()
        events = self.store.read_events()
        repos = self.effective_repos() or self.contract["repo_roots"]
        code_trees = {}
        existing: set[str] = set()
        for name, root in repos.items():
            try:
                code_trees[name] = git_tree.write_tree(
                    root, (f".aidlc/deliverables/{self.run_id}/",), {})
                existing |= set(_git(root, "ls-files").splitlines())
            except Exception as exc:  # a missing tree fails its checks below, not here
                logging.getLogger(__name__).warning(
                    "run %s: code tree of %s unavailable for checks: %s", self.run_id, name, exc)
        results: dict[str, str] = {}
        covers: set[str] = set()
        collected: set[str] = set()
        validation_manifest: dict[str, set[str]] = {}
        for name, row in final.get("repos", {}).items():
            # what pytest collected (B8) and every node that actually ran on this tree
            tests_row = row.get("tests") or {}
            collected |= set(tests_row.get("collected") or [])
            collected |= {n for n in (tests_row.get("nodes") or {}) if "::" in n
                          and not n.endswith("::*")}
            validation_manifest[name] = {item["id"] for item in row.get("validations", [])}
            for trace in row.get("trace", []):
                results[str(trace.get("ref") or trace.get("exit"))] = trace.get("result")
                covers |= set(trace.get("covers") or [])
        profile = self.contract["profile"]
        req_variant = "lite" if profile == "direct" else "full"
        design_variant = None if profile in {"research", "direct"} else (
            "lite" if profile == "trivial" else "full")
        run = {**self.projection(), "mode": self.contract["mode"],
               "code_trees": code_trees, "changed_paths": self._changed_paths(repos),
               "validation_rule_paths": ["validations.json", "backend/core/run_protocol/risk_selection.py"],
               "lessons": self._read_json(self.run_dir / "artifacts" / "reflect.json", {}).get("lessons", []),
               "agent_after_cap": _agent_after_cap(requirements, design)}
        return checks.make_context(
            profile=profile, requirement_variant=req_variant, design_variant=design_variant,
            requirements=requirements, design=design, events=events, authority=self.authority,
            final=final, run=run, test_results=results, trace_covers=covers,
            expected_repos=set(self.contract["expected_repos"]),
            validation_manifest=validation_manifest, existing_files=existing,
            collected_tests=collected, decision_documents={}, known_refs={})

    def write_projection(self) -> Path:
        state = self.projection()
        value = {key: val for key, val in state.items()
                 if key not in {"expected_transition", "open_attempt", "auth_grants"}}
        value.update({"id": self.run_id, "project": self.project,
                      "session_id": self.owner_session(),
                      "worktree_mode": self.contract["mode"],
                      "worktrees": [{"repo_name": name, "main": self.contract["repo_roots"][name],
                                     "path": root, "state": "active"}
                                    for name, root in self.effective_repos().items()],
                      "updated_at": _now(), "created_at": self.contract["created_at"]})
        audit_value = self._read_json(self.run_dir / "run_audit.json", {})
        if isinstance(audit_value, Mapping):
            value["audit_card"] = audit_value.get("card")
            value["audit_summary"] = audit_value.get("summary")
            value["push_ready"] = (audit_value.get("outcome") or {}).get("push_ready")
        retention = self._read_json(self._retention_path(), {})
        if isinstance(retention, Mapping) and retention.get("worktrees"):
            value["retention"] = retention["worktrees"]
        projected_commits: dict[str, str] = {}
        for progress_path in sorted((self.run_dir / "artifacts").glob("landing-progress-*.json")):
            progress = self._read_json(progress_path, {})
            if not isinstance(progress, Mapping):
                continue
            for name, commit in (progress.get("commits") or {}).items():
                if isinstance(name, str) and isinstance(commit, str):
                    projected_commits[name] = commit
        if projected_commits:
            value["commits"] = [
                {"repo": name, "sha": commit, "landed": True}
                for name, commit in sorted(projected_commits.items())
            ]
        requirements = self._read_json(self.run_dir / "artifacts" / "requirements.json", {})
        rows = requirements.get("requirements") if isinstance(requirements, Mapping) else []
        if self.contract.get("requirement"):
            value["requirement"] = str(self.contract["requirement"])
        elif isinstance(rows, list) and rows:
            value["requirement"] = str(rows[0].get("story") or rows[0].get("id") or "")
        path = self.run_dir / "run.json"
        _atomic_json(path, value)
        return path

    def _retention_path(self) -> Path:
        return self.run_dir / "artifacts" / "retention.json"

    def _retention_entries(self) -> list[dict[str, Any]]:
        value = self._read_json(self._retention_path(), {"worktrees": []})
        rows = value.get("worktrees") if isinstance(value, Mapping) else None
        if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
            raise EngineError("retention metadata is malformed")
        return [dict(row) for row in rows]

    def _save_retention(self, entries: Sequence[Mapping[str, Any]]) -> None:
        _atomic_json(self._retention_path(), {"schema": 1, "run_id": self.run_id,
                                             "worktrees": [dict(row) for row in entries]})

    def _active_entries(self) -> list[dict[str, Any]]:
        entries = []
        for name, root in self.effective_repos().items():
            main = self.contract["repo_roots"][name]
            common = _git(root, "rev-parse", "--git-common-dir")
            common_path = Path(common)
            if not common_path.is_absolute():
                common_path = Path(root) / common_path
            entries.append({"repo_name": name, "main": main,
                            "git_common_dir": str(common_path.resolve()),
                            "integration_branch": _git(main, "symbolic-ref", "HEAD"),
                            "base": _git(root, "rev-parse", "HEAD"),
                            "pre_sync_tip": None, "state": "active",
                            "created_at": self.contract["created_at"], "dep_links": []})
        return entries

    def gate0_start(self, requirements: Mapping[str, Any] | None = None) -> dict[str, Any]:
        state = self.projection()
        if state["status"] != "active" or state["stage"] != "evaluate" \
                or state["profile"] not in {"full", "bugfix", "goal"}:
            raise EngineError("Gate-0 requires active EVALUATE on full, bugfix, or goal")
        docs = DocumentStore(self.run_dir)
        if requirements is not None:
            docs.write_requirements(requirements, variant="full", profile=state["profile"])
        value = self._read_json(self.run_dir / "artifacts" / "requirements.json", {})
        readings = ((value.get("understanding") or {}).get("readings")
                    if isinstance(value, Mapping) else None)
        required = state["profile"] == "bugfix" or (isinstance(readings, list) and len(readings) >= 2)
        if not required:
            return {"required": False, "handout": None}
        prior = [event for event in self._since_last_answer()
                 if event.get("kind") == "handout" and event.get("stage") == "evaluate"
                 and event.get("payload", {}).get("expert_id") == "gate0-skeptic"]
        if len(prior) >= 2:
            raise EngineError("Gate-0 has reached its two-round cap")
        round_number = len(prior) + 1
        prompt_path = (Path(__file__).resolve().parents[2] / "skills" /
                       "s_autonomous-pipeline" / "data" / "gate-prompts" /
                       "gate0-skeptic.md")
        handout = review.ReviewOrchestrator(self.store, self.run_dir).handout(
            expert_id="gate0-skeptic", stage="evaluate", round=1,
            attempt=round_number, at=_now(),
            expert_instructions=prompt_path.read_text(encoding="utf-8"),
            review_input=(f"GATE-0 ATTEMPT {round_number} OF 2\n" +
                          json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)))
        return {"required": True, "round": round_number, "handout": handout}

    def _since_last_answer(self) -> list[dict[str, Any]]:
        """Events after the latest human answer: a person's answer restarts the
        Gate-0 rounds (design §A1.2: "审查轮次重新计数"), so an earlier rejection no
        longer decides the run."""
        events = self.store.read_events()
        last = max((index for index, event in enumerate(events)
                    if event.get("kind") == "human_answer"), default=-1)
        return events[last + 1:]

    def _gate0_disposition(self) -> str:
        events = self._since_last_answer()
        answered = len(events) != len(self.store.read_events())
        handouts = [event for event in events
                    if event.get("kind") == "handout" and event.get("stage") == "evaluate"
                    and event.get("payload", {}).get("expert_id") == "gate0-skeptic"]
        if answered and not handouts:
            return "continue"      # the person decided after the gate; nothing newer to wait for
        answers = [event for event in events
                   if event.get("kind") in {"answer", "answer_reuse"}
                   and event.get("payload", {}).get("expert_id") == "gate0-skeptic"]
        if not handouts or len(answers) < len(handouts):
            return "awaiting_answer"
        verdicts = [event.get("payload", {}).get("verdict") for event in answers
                    if event.get("payload", {}).get("state") == "parsed"]
        if "ALREADY-SATISFIED" in verdicts:
            return "already_satisfied"
        negatives = [value for value in verdicts if value in {"WRONG-FRAME", "UNSUPPORTED"}]
        if len(negatives) >= 2:
            return "framing_rejected"
        latest_parsed = answers[-1].get("payload", {}).get("state") == "parsed"
        if (negatives or not latest_parsed) and len(handouts) < 2:
            return "needs_round_2"
        return "continue"

    def review_address(self, artifact: Mapping[str, Any]) -> dict[str, Any]:
        state = self.projection()
        if state["status"] != "active" or state["stage"] != "review":
            raise EngineError("review-address requires active REVIEW")
        orchestrator = review.ReviewOrchestrator(self.store, self.run_dir)
        prior = [event for event in self.store.read_events() if event.get("kind") == "address"]
        handoff = orchestrator.record_fix_handoff(artifact, at=_now(), attempt=len(prior) + 1)
        roster = orchestrator.r2_roster(set(handoff["addressed"]))
        expert_root = Path(__file__).resolve().parents[2] / "experts" / "code"
        handouts = []
        for expert, ids in roster.items():
            review_input = orchestrator.r2_input(
                expert, ids, hunks=handoff["hunks"], proofs=handoff["proofs"],
                reclassifications=handoff["reclassifications"])
            handouts.append(orchestrator.handout(
                expert_id=expert, stage="review", round=2, attempt=2, at=_now(),
                expert_instructions=(expert_root / f"{expert}.md").read_text(encoding="utf-8"),
                review_input=review_input, finding_ids=ids))
        orchestrator.write_documents()
        return {"handoff": handoff, "reviewers": sorted(roster), "handouts": handouts}

    def review_proof(self, artifact: Mapping[str, Any]) -> dict[str, Any]:
        state = self.projection()
        if state["status"] != "active" or state["stage"] != "review":
            raise EngineError("review-proof requires active REVIEW")
        if not isinstance(artifact, Mapping) or set(artifact) != {"pass", "proofs"} \
                or not isinstance(artifact.get("proofs"), list):
            raise EngineError("review-proof artifact must contain pass and proofs")
        orchestrator = review.ReviewOrchestrator(self.store, self.run_dir)
        findings = {row["canonical_id"]: row for row in orchestrator.findings()}
        repos = self.effective_repos()
        import re
        hunk_re = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)
        derived = []
        for index, supplied in enumerate(artifact["proofs"], 1):
            if not isinstance(supplied, Mapping):
                raise EngineError(f"review proof {index} must be an object")
            finding_id = supplied.get("finding_id")
            evidence_type = supplied.get("evidence_type")
            row = findings.get(finding_id)
            if row is None or evidence_type not in {"diff_assertion", "refuted"}:
                raise EngineError(f"review proof {index} has unknown finding/evidence type")
            allowed = ({"finding_id", "evidence_type"} if evidence_type == "diff_assertion"
                       else {"finding_id", "evidence_type", "refs"})
            if set(supplied) != allowed:
                raise EngineError(f"review proof {index} contains caller-derived fields")
            file = row["file"]
            candidates = []
            for name, root in repos.items():
                try:
                    exists = (Path(root) / file).exists()
                    tracked = _git(root, "ls-files", "--error-unmatch", "--", file) == file
                except EngineError:
                    tracked = False
                if exists or tracked:
                    candidates.append((name, root))
            if len(candidates) != 1:
                raise EngineError(f"{finding_id}: finding file resolves to {len(candidates)} repos")
            _name, root = candidates[0]
            code_tree = git_tree.write_tree(
                root, (f".aidlc/deliverables/{self.run_id}/",), {})
            if evidence_type == "refuted":
                refs = supplied.get("refs")
                if not isinstance(refs, list) or not refs or any(
                        not isinstance(ref, str) or not ref for ref in refs):
                    raise EngineError(f"{finding_id}: refutation requires concrete refs")
                derived.append({"finding_id": finding_id, "evidence_type": "refuted",
                                "code_tree": code_tree, "result": "refuted",
                                "refs": list(refs)})
                continue
            # base -> the code tree that lands, as REVIEW saw it (a new file included)
            diff = _git(root, "diff", "--unified=0", "--no-renames",
                        self._fork_base(_name, root), code_tree, "--", file)
            line = int(row["line"])
            touches = False
            for match in hunk_re.finditer(diff):
                old_start, old_count = int(match.group(1)), int(match.group(2) or 1)
                new_start, new_count = int(match.group(3)), int(match.group(4) or 1)
                if (old_count and old_start <= line < old_start + old_count) \
                        or (new_count and new_start <= line < new_start + new_count):
                    touches = True
                    break
            non_executable = _comment_only_diff(file, diff)
            derived.append({"finding_id": finding_id,
                            "evidence_type": "diff_assertion",
                            "code_tree": code_tree, "result": "asserted",
                            "touches_finding": touches,
                            "touches_executable": not non_executable})
        events = orchestrator.record_cli_proofs(
            {"pass": artifact["pass"], "proofs": derived}, at=_now(),
            attempt=1 + sum(event.get("kind") == "proof"
                            for event in self.store.read_events()))
        orchestrator.write_documents()
        return {"proofs": events, "findings": orchestrator.findings()}

    def request_decision(self, value: Mapping[str, Any], *, document: str) -> dict[str, Any]:
        from core.run_protocol.documents import record_decision
        if self.contract.get("profile") == "direct":
            raise EngineError("a direct run makes no decisions: abandon it and re-create the "
                              "request as --profile trivial, then ask there")
        refs: dict[str, Mapping[str, Any]] = {
            event["event_id"]: event for event in self.store.read_events()}
        requirements = self._read_json(self.run_dir / "artifacts" / "requirements.json", {})
        for index, row in enumerate(requirements.get("analysis", []) if isinstance(requirements, Mapping) else [], 1):
            if isinstance(row, Mapping):
                refs[f"A{index}"] = {**dict(row), "analysis_kind": row.get("kind"),
                                     "kind": "analysis"}
        for row in review.ReviewOrchestrator(self.store, self.run_dir).findings():
            refs[row["canonical_id"]] = row
        state = self.projection()
        event = record_decision(self.store, value, known_refs=refs, document=document,
                                at=_now(), decisions_answered=state["human_decisions"],
                                cap=state["decision_cap"])
        _atomic_json(self.run_dir / "artifacts" / f"decision-{value['request_id']}.json", dict(value))
        self._settle(); self.write_projection()
        return {"event": event, "decision": dict(value), "state": self.projection(),
                "ask_user_question": __import__("core.run_protocol.decision",
                                                fromlist=["ask_payload"]).ask_payload(value)}

    def answer_decision(self, *, request_id: str, option_id: str) -> dict[str, Any]:
        """Record a HUMAN's answer. Called only from the daemon (the chat question card
        or the dashboard decision card); the agent's CLI has no answer command."""
        from core.run_protocol import decision
        state = self.projection()
        if state["status"] != "awaiting_human" or state.get("pending_decision") != request_id:
            raise EngineError("decision answer is not for the current pending decision")
        value = self._read_json(self.run_dir / "artifacts" / f"decision-{request_id}.json", None)
        if not isinstance(value, Mapping):
            raise EngineError("pending decision document is missing")
        answer = decision.answer(value, request_id=request_id, option_id=option_id)
        event, _ = self.store.append_new(
            writer=self.authority.writer("hook"), kind="human_answer", at=_now(),
            payload={"request_id": request_id, "option_id": option_id})
        self._settle()
        answers = self._read_json(self.run_dir / "artifacts" / "decision-answers.json", [])
        if not isinstance(answers, list):
            raise EngineError("decision answer projection is malformed")
        _atomic_json(self.run_dir / "artifacts" / "decision-answers.json", [*answers, answer])
        self.write_projection()
        return {"event": event, "answer": answer, "state": self.projection()}

    def abandon(self, *, reason: str) -> dict[str, Any]:
        if not isinstance(reason, str) or not reason.strip():
            raise EngineError("abandon requires a reason")
        state = self.projection()
        if state["status"] in lifecycle.TERMINAL:
            raise EngineError(f"cannot abandon {state['status']} run")
        entries = self._active_entries()
        if entries:
            results = run_worktree.release(
                self.run_id, {"worktrees": entries},
                {name: Path(root) for name, root in self.contract["repo_roots"].items()},
                reason.strip())
            failed = [row for row in results if not row.get("ok")]
            if failed:
                raise EngineError(f"snapshot preservation failed: {failed}")
            self._save_retention(entries)
        event, _ = self.store.append_new(
            writer=self.authority.writer("cli"), kind="abandon", at=_now(),
            ordinal=len(self.store.read_events()) + 1, payload={"reason": reason.strip()})
        self._settle(); self.write_projection()
        return {"event": event, "retention": entries, "state": self.projection()}

    def restore(self) -> dict[str, Any]:
        state = self.projection()
        if not (state["status"] == "abandoned" or
                (state["status"] == "completed" and state.get("landing") == "not_landed")):
            raise EngineError("restore requires abandoned or completed(not_landed)")
        entries = self._retention_entries()
        by_name = {row.get("repo_name"): row for row in entries}
        restored = []
        for name in self.contract["expected_repos"]:
            if name in self.effective_repos():
                continue
            entry = by_name.get(name)
            if entry is None:
                raise EngineError(f"{name}: no retained snapshot")
            try:
                new = __import__("core.run_protocol.retention", fromlist=["restore_snapshot"]).restore_snapshot(
                    self.run_id, entry, self.contract["repo_roots"][name], workspace=self.workspace)
            except Exception as exc:
                raise EngineError(f"{name}: restore failed: {exc}") from exc
            by_name[name] = new; restored.append(new)
        self._save_retention([by_name[name] for name in self.contract["expected_repos"]])
        event, _ = self.store.append_new(
            writer=self.authority.writer("cli"), kind="restore", at=_now(),
            ordinal=len(self.store.read_events()) + 1, payload={"snapshot_exists": True})
        self._settle(); self.write_projection()
        return {"event": event, "restored": restored, "state": self.projection()}

    def resume(self) -> dict[str, Any]:
        state = self.projection()
        if state["status"] != "paused":
            raise EngineError("resume requires paused")
        ready = True
        if state.get("reason") == "worktree_unavailable":
            try:
                for name in self.contract["expected_repos"]:
                    if name not in self.effective_repos():
                        run_worktree.create(self.run_id, name, self.contract["repo_roots"][name],
                                            workspace=self.workspace)
            except run_worktree.WorktreeError:
                ready = False
        event, _ = self.store.append_new(
            writer=self.authority.writer("cli"), kind="resume", at=_now(),
            ordinal=len(self.store.read_events()) + 1, payload={"worktree_ready": ready})
        self._settle(); self.write_projection()
        return {"ok": ready, "event": event, "state": self.projection()}

    def _commit_report(self, audit_value: Mapping[str, Any], *, guards: Sequence[str],
                       request_id: str | None = None, note: str | None = None) -> bytes:
        from core.run_protocol import audit as audit_protocol
        text = audit_protocol.render(audit_value, fmt="report")
        marker = "Landing guards: PASS (" + ", ".join(guards) + ")."
        lines = text.splitlines()
        lines.insert(3 if len(lines) >= 3 else len(lines), marker)
        if request_id:
            lines.insert(4 if len(lines) >= 4 else len(lines),
                         f"Manual landing request: {request_id}" + (f" — {note}" if note else ""))
        return ("\n".join(lines).rstrip() + "\n").encode()

    def prepare_land_request(self, *, action: str, note: str | None = None,
                             request_id: str | None = None) -> dict[str, Any]:
        with self._landing_lock():
            return self._prepare_land_request(action=action, note=note, request_id=request_id)

    def _prepare_land_request(self, *, action: str, note: str | None = None,
                              request_id: str | None = None) -> dict[str, Any]:
        from core.run_protocol import audit as audit_protocol, findings as finding_protocol, landing, tree
        state = self.projection()
        if self.contract["mode"] == "isolated" and set(self.effective_repos()) != set(self.contract["expected_repos"]):
            if state["status"] == "abandoned" or (
                    state["status"] == "completed" and state.get("landing") == "not_landed"):
                self.restore()
            elif state["status"] == "paused" and state.get("reason") == "worktree_unavailable":
                resumed = self.resume()
                if not resumed["ok"]:
                    raise EngineError("run-land prepare could not recreate worktrees")
            else:
                self.ensure_worktrees()
        request_id = request_id or "land_" + secrets.token_hex(8)
        if action == "land":
            # design §A4.3: a tested landing needs a final on the CURRENT tree. The run's
            # tests go through the runner's own bounded calls, not a hidden subprocess
            current = tree.build_code_trees(self.effective_repos(), self.run_id,
                                            git_tree.write_tree, git_tree.list_reserved)
            final = self.final().get("repos") or {}
            stale = sorted(name for name, code in current.items()
                           if (final.get(name) or {}).get("code_tree") != code)
            if stale:
                raise EngineError(
                    "the tests have not run on the current tree of " + ", ".join(stale)
                    + ": run `pipeline_test.py --project " + self.project + " --run-id "
                    + self.run_id + " --mode regression` (again while it says continue), "
                    "then --prepare again; or --skip-tests to land untested")
        context = self.check_context()
        push = self.push_status(manual_landing=True)
        def render(_request_id, _results):
            events = self.store.read_events()
            cutoff = next(index for index, event in enumerate(events)
                          if event.get("kind") == "land_intent"
                          and event.get("payload", {}).get("request_id") == request_id)
            prior = events[:cutoff]
            audit_value = audit_protocol.build(
                run_id=self.run_id,
                projection=lifecycle.fold(prior, self.authority), events=prior,
                final=self.final(), findings=finding_protocol.fold(prior, self.authority),
                contract=self.contract, push=push, audit_through="DELIVER")
            guards = ("LG-3", "LG-4") if action == "land_skip_tests" else (
                "LG-1", "LG-2", "LG-3", "LG-4", "LG-5")
            return self._commit_report(audit_value, guards=guards,
                                       request_id=request_id, note=note)
        prepared = landing.prepare_request(
            self.store, run_id=self.run_id, session_id=self.owner_session(),
            action=action, repos=self.effective_repos(), context=context, at=_now(),
            render_report=render, note=note, request_id=request_id,
            push_reasons=len(push.get("reasons") or []),
            integration_roots=self.contract["repo_roots"])
        # display copy only: execution reads the signed land_request event (C1)
        path = self.run_dir / "artifacts" / "land-requests" / f"{request_id}.json"
        _atomic_json(path, prepared)
        self.write_projection()
        return {"prepared": prepared, "path": str(path), "push": push}

    def execute_land_request(self, request_id: str, *, message: str) -> dict[str, Any]:
        """Consume one daemon-issued approval token and finish a prepared landing."""
        with self._landing_lock():
            return self._execute_land_request(request_id, message=message)

    def _execute_land_request(self, request_id: str, *, message: str) -> dict[str, Any]:
        from core.run_protocol.landing import with_trailer
        message = with_trailer(message)
        from core import hook_token
        from core.run_protocol import auth, landing

        try:
            payload = landing.request_event(self.store, request_id)
        except landing.LandingError as exc:
            raise EngineError(str(exc)) from exc
        request = payload["request"]
        if request.get("session_id") != self.owner_session():
            raise EngineError("land request was prepared by another owner session")
        token = os.environ.get(hook_token.TOKEN_ENV)
        token_scope = f"run-land:{request_id}"
        ok, reason = hook_token.consume(
            self.run_dir, token, self.owner_session(), self.run_id, token_scope)
        if not ok:

            raise IntegrationRequired("approval_required", reason)

        now = __import__("time").time()
        commits = landing.execute_request(
            self.store, request_id,
            session_id=self.owner_session(), repos=self.effective_repos(),
            integration_roots=self.contract["repo_roots"], message=message,
            at=_now(), now=now,
            progress_path=self.run_dir / "artifacts" / f"landing-progress-{request_id}.json")
        try:
            report = (self.store.paths.blobs / payload["report_sha"]).read_text(encoding="utf-8")
        except OSError as exc:
            raise EngineError(f"prepared REPORT blob is missing after landing: {exc}") from exc
        DocumentStore._atomic(self.run_dir / "REPORT.md", report)
        self._settle()
        self.write_projection()
        auth_event_id = next(event_id for event_id, row in lifecycle.fold(
            self.store.read_events(), self.authority)["auth_grants"].items()
            if row["request_id"] == request_id)
        return {"auth_event_id": auth_event_id, "commits": commits,
                "state": self.projection(), "push": self.push_status(manual_landing=True)}

    def _ledger_of(self, repo_name: str) -> Path:
        """Where run_worktree keeps this run's snapshot of *repo_name* (never from JSON)."""
        return run_worktree.ledger_root() / self.run_id / repo_name

    def prepare_authorized_action(
        self, *, action: str, target: str, note: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """Prepare a non-landing human authorization without performing the action."""
        from core.run_protocol import auth, findings, retention

        request_id = request_id or "auth_" + secrets.token_hex(8)
        metadata: dict[str, Any] = {}
        if action == "accept_finding":
            rows = {row["canonical_id"]: row for row in
                    findings.fold(self.store.read_events(), self.authority)}
            row = rows.get(target)
            if row is None:
                raise EngineError("accept-finding references an unknown canonical finding")
            bindings = {"canonical_id": target, "state_hash": findings.state_hash(row)}
        elif action == "discard_snapshot":
            if target not in self.contract["expected_repos"]:
                raise EngineError("discard-snapshot names a repo this run does not have")
            ledger = self._ledger_of(target)
            if not (ledger / "meta.json").is_file():
                raise EngineError("discard-snapshot references no retained repo ledger")
            bindings = {"snapshot_id": retention.snapshot_id(ledger), "repo_name": target}
        else:
            raise EngineError("unsupported authorized action")
        request = auth.make_request(
            request_id=request_id, session_id=self.owner_session(),
            run_id=self.run_id, action=action, bindings=bindings, note=note)
        self.store.append_new(
            writer=self.authority.writer("cli"), kind="land_request", at=_now(),
            payload={"request_id": request_id, "action": action,
                     "binding_digest": request["binding_digest"], "request": request})
        value = {"request": request, **metadata}
        path = self.run_dir / "artifacts" / "authorized-requests" / f"{request_id}.json"
        _atomic_json(path, value)
        self.write_projection()
        return {"request": request, "path": str(path)}

    def execute_authorized_action(self, request_id: str) -> dict[str, Any]:
        """Consume a daemon-issued token, append auth facts, then perform one action."""
        from core import hook_token
        from core.run_protocol import auth, findings, landing, retention

        path = self.run_dir / "artifacts" / "authorized-requests" / f"{request_id}.json"
        try:   # everything comes from the signed event; the JSON is display only (C1)
            request = landing.request_event(self.store, request_id)["request"]
        except landing.LandingError as exc:
            raise EngineError(str(exc)) from exc
        if request.get("session_id") != self.owner_session():
            raise EngineError("authorization request was prepared by another owner session")
        action = request.get("action")
        bindings = request.get("bindings")
        if action == "accept_finding":
            normalized = bindings if isinstance(bindings, Mapping) else {}
            rows = {row["canonical_id"]: row for row in
                    findings.fold(self.store.read_events(), self.authority)}
            canonical_id = normalized.get("canonical_id")
            row = rows.get(canonical_id)
            if row is None or findings.state_hash(row) != normalized.get("state_hash"):
                raise EngineError("accept-finding request is stale")
        elif action == "discard_snapshot":
            normalized = bindings if isinstance(bindings, Mapping) else {}
            repo_name = normalized.get("repo_name")
            if repo_name not in self.contract["expected_repos"]:
                raise EngineError("discard-snapshot request names an unknown repo")
            ledger = self._ledger_of(repo_name)
            try:
                current = retention.snapshot_id(ledger)
            except retention.RetentionError as exc:
                raise EngineError(f"discard-snapshot request is stale: {exc}") from exc
            if current != normalized.get("snapshot_id"):
                raise EngineError("discard-snapshot request is stale")
        else:
            raise EngineError("prepared authorization action is unsupported")

        token_scope = f"{str(action).replace('_', '-')}:{request_id}"
        ok, reason = hook_token.consume(
            self.run_dir, os.environ.get(hook_token.TOKEN_ENV),
            self.owner_session(), self.run_id, token_scope)
        if not ok:
            raise IntegrationRequired("approval_required", reason)
        now = __import__("time").time()
        try:
            done = landing.consume_authorization(
                self.store, request_id, session_id=self.owner_session(),
                bindings=bindings, at=_now(), now=now)
        except landing.LandingError as exc:
            raise EngineError(str(exc)) from exc
        consumed = done["consumed"]
        if action == "discard_snapshot":
            retention.discard_snapshot(
                consumption=done["consumption"], run_id=self.run_id,
                repo_name=repo_name, main=self.contract["repo_roots"][repo_name],
                ledger=str(ledger))
            self._save_retention([
                row for row in self._retention_entries()
                if row.get("repo_name") != repo_name])
        review.ReviewOrchestrator(self.store, self.run_dir).write_documents()
        self.write_projection()
        return {"auth_event_id": consumed["payload"]["auth_event_id"],
                "consumed_event_id": consumed["event_id"],
                "action": action, "state": self.projection()}

    def stage_start(self) -> dict[str, Any]:
        state = self.projection()
        if state["status"] != "active":
            raise EngineError(f"run is {state['status']}")
        if state["stage"] in {"plan", "build", "goal_cycle", "review", "test", "deliver"}:
            self.ensure_worktrees()
            # run.json is what the live hooks read (worktree_write_gate, main_dirty_scan):
            # it must name the worktrees the moment they exist
            self.write_projection()
        card = Path(__file__).resolve().parents[2] / "skills" / "s_autonomous-pipeline" / "stages" / f"{state['stage']}.md"
        return {"run_id": self.run_id, "stage": state["stage"], "card": str(card),
                "card_text": card.read_text(encoding="utf-8"), "repos": self.effective_repos()}

    def _next_attempt(self, stage: str) -> int:
        """The lifecycle numbers attempts (a human answer restarts the count)."""
        return int(self.projection()["stage_attempts"].get(stage, 0)) + 1

    def stage_complete(self, artifact: Mapping[str, Any] | None = None) -> dict[str, Any]:
        from core.run_protocol.documents import DocumentError
        state = self.projection()
        if state["status"] != "active":
            raise EngineError(f"run is {state['status']}")
        stage, profile = state["stage"], state["profile"]
        if stage == "deliver":
            if state.get("pending_landing") is None and profile != "research":
                raise EngineError("DELIVER finishes with `land`; stage-complete is only for "
                                  "the not-landed path")
            return self.finish_not_landed()
        docs = DocumentStore(self.run_dir)
        artifact = dict(artifact or {})
        variant = ("lite" if stage == "plan" and profile == "trivial"
                   else "full" if stage in {"evaluate", "plan"} else None)
        roster = {spec.check_id for spec in checks.stage_checks(profile, stage, variant)}
        try:
            if stage == "evaluate":
                docs.write_requirements(artifact, variant="full", profile=profile,
                                        blocking=roster)
            elif stage == "plan":
                requirements = self._read_json(
                    self.run_dir / "artifacts" / "requirements.json", {})
                docs.write_design(artifact, variant="lite" if profile == "trivial" else "full",
                                  profile=profile, requirements=requirements,
                                  requirement_variant="full",
                                  existing_files=self.check_context().existing_files,
                                  blocking=roster)
        except DocumentError as exc:
            forced = exc.issues or {next(iter(sorted(roster))): str(exc)}
            result = complete_stage(self.store, profile=profile, stage=stage, variant=variant,
                                    context=self.check_context(), at=_now(),
                                    attempt=self._next_attempt(stage), forced=forced)
            self._settle(); self.write_projection()
            return {"ok": False, "error": str(exc), **result, "state": self.projection()}
        if stage == "evaluate" and profile in {"full", "bugfix", "goal"}:
            readings = ((artifact.get("understanding") or {}).get("readings")
                        if isinstance(artifact, Mapping) else None)
            gate_required = profile == "bugfix" or (isinstance(readings, list) and len(readings) >= 2)
            if gate_required:
                disposition = self._gate0_disposition()
                if disposition in {"awaiting_answer", "needs_round_2"}:
                    self.write_projection()
                    return {"ok": False, "gate0": disposition, "state": self.projection()}
                if disposition in {"framing_rejected", "already_satisfied"}:
                    self.store.append_new(
                        writer=self.authority.writer("cli"), kind="gate0_exhausted", at=_now(),
                        ordinal=len(self.store.read_events()) + 1,
                        payload={"reason": disposition})
                    self._settle(); self.write_projection()
                    return {"ok": False, "gate0": disposition, "state": self.projection()}
        context = self.check_context()
        result = complete_stage(self.store, profile=profile, stage=stage,
                                variant=variant, context=context, at=_now(),
                                attempt=self._next_attempt(stage))
        self._settle()
        if not result["passed"]:
            self.write_projection()
            return {"ok": False, **result, "state": self.projection()}
        payload = {"stage_attempt_event_id": result["stage_attempt_event_id"]}
        if stage == "test":
            final = self.final()
            verdicts = [row["tests"]["verdict"] for row in final.get("repos", {}).values()]
            payload["final_verdict"] = ("incomplete" if not verdicts or "incomplete" in verdicts
                                        else "red" if "red" in verdicts else "green")
        self.store.append_new(writer=self.authority.writer("cli"), kind="stage_complete",
                              at=_now(), stage=stage, attempt=result["attempt"], payload=payload)
        self._settle(); self.write_projection()
        return {"ok": True, **result, "state": self.projection()}

    def finish_not_landed(self) -> dict[str, Any]:
        """The not-landed path (design §A1.2): render the run-directory REPORT, then a
        DELIVER attempt whose only stage check is DV-1, then ``reflecting``. Every
        not_landed outcome (and research's no_code) ends here."""
        from core.run_protocol import audit as audit_protocol
        state = self.projection()
        if state["status"] != "active" or state["stage"] != "deliver" or (
                state.get("pending_landing") is None and state["profile"] != "research"):
            raise EngineError("the not-landed path needs active DELIVER with a not-landed reason")
        value = self.build_audit(through="DELIVER", persist=True)
        report = audit_protocol.render(value, fmt="report")
        report_sha = self.store.put_blob(report.encode())
        self.store.append_new(
            writer=self.authority.writer("cli"), kind="render", at=_now(),
            stage="deliver", attempt=self._next_attempt("deliver"),
            payload={"document": "REPORT", "blob_sha": report_sha})
        DocumentStore._atomic(self.run_dir / "REPORT.md", report)
        attempt = self._next_attempt("deliver")
        result = complete_stage(self.store, profile=state["profile"], stage="deliver",
                                variant=None, context=self.check_context(), at=_now(),
                                attempt=attempt, check_ids=["DV-1"])
        self._settle()
        if not result["passed"]:
            self.write_projection()
            return {"ok": False, **result, "state": self.projection()}
        self.store.append_new(
            writer=self.authority.writer("cli"), kind="stage_complete", at=_now(),
            stage="deliver", attempt=attempt,
            payload={"stage_attempt_event_id": result["stage_attempt_event_id"]})
        self._settle(); self.write_projection()
        return {"ok": True, "landing": self.projection()["landing"], **result,
                "state": self.projection()}

    def review_start(self, *, attempt: int = 1) -> dict[str, Any]:
        state = self.projection()
        if state["stage"] != "review" or state["status"] != "active":
            raise EngineError("review-start requires active REVIEW")
        repos = self.effective_repos()
        changed = []
        diffs = []
        for name, root in repos.items():
            # the run's whole change: fork point -> the code tree that would land (B6),
            # so a commit in the worktree or a new untracked file is reviewed too
            base, tree_sha = self._fork_base(name, root), git_tree.write_tree(
                root, (f".aidlc/deliverables/{self.run_id}/",), {})
            for line in _git(root, "diff", "--numstat", "--no-renames", base, tree_sha).splitlines():
                ins, delete, path = line.split("\t", 2)
                changed.append({"path": path, "insertions": int(ins) if ins.isdigit() else 0,
                                "deletions": int(delete) if delete.isdigit() else 0,
                                "is_test": "/tests/" in path or path.startswith("tests/")})
            diffs.append(f"## {name}\n" + _git(root, "diff", "--no-ext-diff", "--no-renames",
                                                base, tree_sha))
        source_paths = [row["path"] for row in changed if not row["is_test"]
                        and not row["path"].endswith((".md", ".txt", ".rst"))]
        patterns = (review.review_patterns(source_paths, "\n".join(diffs)) if source_paths
                    else {"compact": "", "triggered": [], "error": None})
        roster = review.reviewers(profile=state["profile"], changed=changed,
                                  patterns=patterns["triggered"])
        if patterns["error"] and not any(item["id"] == "correctness" for item in roster):
            # the selection failed: review the source anyway, never fewer checks
            roster.insert(0, {"id": "correctness",
                              "reason": f"review patterns unavailable ({patterns['error']})"})
        orchestrator = review.ReviewOrchestrator(self.store, self.run_dir)
        handouts = []
        expert_root = Path(__file__).resolve().parents[2] / "experts" / "code"
        events = self.store.read_events()
        prior = [e for e in events if e.get("kind") == "handout" and e.get("stage") == "review"
                 and e.get("round") == 1]
        answers = {e["payload"]["handout_event_id"]: e for e in events
                   if e.get("kind") in ("answer", "answer_reuse") and e.get("stage") == "review"}
        for item in roster:
            instructions = (expert_root / f"{item['id']}.md").read_text(encoding="utf-8")
            review_input = "\n\n".join(diffs)
            if item["id"] == "correctness":
                review_input = review.patterns_block(patterns) + "\n\n" + review_input
            mine = [h for h in prior if h["payload"]["expert_id"] == item["id"]]
            expert_attempt = attempt
            if mine:
                # a second review-start (resume, retry): never a third dispatch (§A3.2)
                latest = answers.get(mine[-1]["event_id"])
                if latest is None or latest["payload"].get("state") in ("parsed", "partial"):
                    continue                # still pending, or answered
                if len(mine) >= 2:
                    continue                # no usable output twice: RV-1a/1c decide
                expert_attempt = int(mine[-1]["attempt"]) + 1
            handouts.append(orchestrator.handout(
                expert_id=item["id"], stage="review", round=1, attempt=expert_attempt,
                at=_now(), expert_instructions=instructions,
                review_input=review_input))
        if not handouts:
            orchestrator.write_documents()
        return {"reviewers": roster, "handouts": handouts}

    def build_audit(self, *, through: str, commits: Mapping[str, str] | None = None,
                    persist: bool = True,
                    event_rows: Sequence[Mapping[str, Any]] | None = None,
                    push: Mapping[str, Any] | None = None) -> dict[str, Any]:
        from core.run_protocol import audit as audit_protocol
        from core.run_protocol import findings as finding_protocol
        rows = list(event_rows) if event_rows is not None else self.store.read_events()
        projected_findings = finding_protocol.fold(rows, self.authority)
        value = audit_protocol.build(
            run_id=self.run_id, projection=lifecycle.fold(rows, self.authority),
            events=rows, final=self.final(), findings=projected_findings,
            contract=self.contract, push=dict(push or self.push_status()), audit_through=through,
            commits=commits)
        if persist:
            audit_protocol.write(self.run_dir / "run_audit.json", value)
        return value

    def push_status(self, *, manual_landing: bool = False) -> dict[str, Any]:
        import dataclasses
        context = self.check_context()
        if manual_landing:
            context = dataclasses.replace(
                context, run={**dict(context.run), "landing": "human_override"})
        results = {}
        for spec in checks.push_checks(self.contract["profile"]):
            if spec.check_id == "MD-3" and not manual_landing:
                continue  # normal land evaluates no manual-landing penalty
            try:
                results[spec.check_id] = checks.evaluate(spec.check_id, context)
            except Exception as exc:
                results[spec.check_id] = (False, f"{type(exc).__name__}: {exc}")
        failed = {key: detail for key, (passed, detail) in results.items() if not passed}
        return {"push_ready": not failed, "reasons": failed, "checks": results}

    def prepare_reflect(self, lessons: Sequence[Mapping[str, Any]],
                        candidates: Mapping[str, Sequence[str]]) -> dict[str, Any]:
        """Write reflect.json and evaluate RF-1~4 as a DELIVER attempt (design §A1.2).
        RF never blocks landing: the third failure drops the lessons to zero and
        records ``reflect: degraded``; DELIVER continues."""
        from core.run_protocol import reflect
        state = self.projection()
        if state["status"] != "active" or state["stage"] != "deliver":
            raise EngineError("reflect-prepare requires active DELIVER")
        if state.get("reflect") == "degraded":
            return {"lessons": [], "degraded": True}
        event_ids = {event["event_id"] for event in self.store.read_events()}
        document = reflect.prepare(lessons, event_ids=event_ids, candidates=candidates)
        path = self.run_dir / "artifacts" / "reflect.json"
        DocumentStore._atomic(path, json.dumps(document, ensure_ascii=False, sort_keys=True,
                                               indent=2))
        result = complete_stage(self.store, profile=state["profile"], stage="deliver",
                                variant=None, context=self.check_context(), at=_now(),
                                attempt=self._next_attempt("deliver"),
                                check_ids=sorted(lifecycle.RF_CHECKS))
        self._settle(); self.write_projection()
        if self.projection().get("reflect") == "degraded":
            document = {**document, "lessons": []}
            DocumentStore._atomic(path, json.dumps(document, ensure_ascii=False,
                                                   sort_keys=True, indent=2))
        return {**document, "checks": result["checks"], "passed": result["passed"]}

    def measure_usage(self) -> dict[str, Any]:
        from core.run_protocol import usage
        return usage.measure(self.store,
                             transcript_ids=self.contract.get("transcript_ids", []),
                             created_at=self.contract["created_at"])

    def _resume_normal_landing(self, *, message: str) -> dict[str, Any] | None:
        from core.run_protocol import landing
        path = self.run_dir / "artifacts" / "landing-progress-normal.json"
        progress = self._read_json(path, None)
        if not isinstance(progress, Mapping) or not progress.get("commits"):
            self._discard_empty_progress()   # a failed attempt committed nothing
            return None
        if any(event.get("kind") == "land" and event.get("payload", {}).get("landing") == "landed"
               for event in self.store.read_events()):
            return None
        state = self.projection()
        attempt = state.get("open_attempt")
        intent, rendered = state.get("last_land_intent"), state.get("last_report_render")
        if not isinstance(attempt, Mapping) or attempt.get("stage") != "land" \
                or not isinstance(intent, Mapping) or not isinstance(rendered, Mapping) \
                or intent.get("stage_attempt_event_id") != attempt.get("event_id") \
                or rendered.get("stage_attempt_event_id") != attempt.get("event_id"):
            raise EngineError("normal landing progress has no matching open land attempt")
        plan = progress.get("plan")
        if not isinstance(plan, Mapping) or set(plan) != set(self.contract["expected_repos"]):
            raise EngineError("normal landing progress has an invalid repo plan")
        bindings = {name: {"delivery_tree": row["delivery_tree"]}
                    for name, row in plan.items() if isinstance(row, Mapping)}
        bases = {name: row["base"] for name, row in plan.items() if isinstance(row, Mapping)}
        commits = landing.commit_repos(
            run_id=self.run_id, integration_roots=self.contract["repo_roots"],
            bases=bases, bindings=bindings, message=message, progress_path=path)
        render_event = next((event for event in reversed(self.store.read_events())
                             if event.get("event_id") == rendered.get("event_id")), None)
        if render_event is None:
            raise EngineError("normal landing REPORT render event is missing")
        report_sha = render_event["payload"]["blob_sha"]
        try:
            report = (self.store.paths.blobs / report_sha).read_text(encoding="utf-8")
        except OSError as exc:
            raise EngineError(f"normal landing REPORT blob is missing: {exc}") from exc
        DocumentStore._atomic(self.run_dir / "REPORT.md", report)
        self.store.append_new(
            writer=self.authority.writer("cli"), kind="land", at=_now(),
            stage="deliver", attempt=attempt["attempt"],
            ordinal=len(self.store.read_events()) + 1,
            payload={"landing": "landed", "commit": next(iter(commits.values())),
                     "stage_attempt_event_id": attempt["event_id"]})
        self._settle(); self.write_projection()
        return {"commits": commits, "push": self.push_status(), "state": self.projection(),
                "resumed": True}

    def _carry_moved_finals(self) -> None:
        from core.run_protocol.carry import CarryError, carry_decision, test_dependency_map
        finals = self.final().get("repos", {})
        repos = self.effective_repos()
        for name in self.contract["expected_repos"]:
            row = finals.get(name)
            if not isinstance(row, Mapping):
                raise IntegrationRequired("final_missing", f"{name}: no test final; run "
                                          "pipeline_test.py --mode regression first")
            main, root = self.contract["repo_roots"][name], repos[name]
            old_base, new_base = row["base"], _git(main, "rev-parse", "HEAD")
            if old_base == new_base:
                continue
            selection = ((row.get("provenance") or {}).get("selection") or {})
            try:
                dependencies = test_dependency_map(selection.get("test_dependencies"))
            except CarryError as exc:
                raise IntegrationRequired(
                    "final_recheck_required",
                    f"{name}: integration base moved and the final has no usable "
                    f"test_dependencies ({exc}); rerun the regression") from exc
            main_changed = [path for path in _git(
                main, "diff", "--name-only", "--no-renames", old_base, new_base).splitlines() if path]
            run_changed = [path for path in _git(
                root, "diff-tree", "-r", "--name-only", "--no-renames",
                old_base, row["code_tree"]).splitlines() if path]
            selected = selection.get("files") or []
            entry = next(item for item in self._active_entries() if item["repo_name"] == name)
            entry["base"] = old_base
            try:
                sync = run_worktree.sync(self.run_id, entry, main)
            except run_worktree.WorktreeError as exc:
                code = "merge_conflict" if exc.code == "conflict" else "final_recheck_required"
                raise IntegrationRequired(code, f"{name}: base sync failed: {exc.message}") from exc
            new_tree = git_tree.write_tree(
                root, (f".aidlc/deliverables/{self.run_id}/",), {})
            try:
                decision = carry_decision(
                    old_base=old_base, new_base=new_base,
                    old_tree=row["code_tree"], new_tree=new_tree,
                    main_changed=main_changed, run_changed=run_changed,
                    selected_tests=selected, test_dependencies=dependencies)
            except CarryError as exc:
                raise IntegrationRequired("final_recheck_required", f"{name}: {exc}") from exc
            if sync.get("patch_changed") or not decision.reusable:
                affected = ", ".join(decision.affected_tests) or "the run regression scope"
                raise IntegrationRequired(
                    "final_recheck_required",
                    f"{name}: integration changes intersect final dependencies; rerun {affected}")
            carried = carry_repo_final(row, new_base=new_base, new_code_tree=new_tree)
            path = self.run_dir / "evidence" / "tests" / f"final-{name}.json"
            warning = evidence_seal.write_sealed(path, json.dumps(carried, sort_keys=True))
            if warning:
                raise EngineError(warning)

    def _landing_lock(self):
        """One landing of this run at a time (fixing spec C4): land / run-land hold it
        from the guards to the land event, so two processes cannot interleave a
        guard, a consume and a commit. Non-blocking: the second caller is told."""
        import contextlib
        from utils.file_lock import flock_exclusive_nb, flock_unlock

        @contextlib.contextmanager
        def held():
            path = self.store.paths.run / "landing.lock"
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with open(path, "a+", encoding="utf-8") as handle:
                try:
                    flock_exclusive_nb(handle)
                except OSError as exc:
                    raise EngineError("another landing of this run is in progress") from exc
                try:
                    yield
                finally:
                    flock_unlock(handle)
        return held()

    def normal_land(self, *, message: str) -> dict[str, Any]:
        with self._landing_lock():
            return self._normal_land(message=message)

    def _normal_land(self, *, message: str) -> dict[str, Any]:
        """Land active DELIVER in the v13 order (design §A3.7).

        Every land attempt is closed with a complete check set, success or failure:
        a guard that could not be evaluated is recorded ``not evaluated``, so the log
        never holds an open attempt. A retryable failure (LG-1 / LG-4, a merge
        conflict) returns ``retry``; the lifecycle turns the third one, and any
        non-retryable guard, into the not-landed path, which this call then finishes.
        A run already on the not-landed path never commits."""
        from core.run_protocol.landing import LandingError, with_trailer
        message = with_trailer(message)
        import dataclasses
        from core.run_protocol import landing, tree
        state = self.projection()
        if state["status"] != "active" or state["stage"] != "deliver":
            raise EngineError("normal land requires active DELIVER")
        if state.get("pending_landing") is not None:
            return self.finish_not_landed()
        resumed = self._resume_normal_landing(message=message)
        if resumed is not None:
            return resumed
        from core import hook_token
        ok, why = hook_token.consume(self.run_dir, os.environ.get(hook_token.TOKEN_ENV),
                                     self.owner_session(), self.run_id, "land")
        if not ok:
            # a landing needs the session's hook (fixing spec A7, design §C5.1): a
            # hookless executor's work is kept and the run ends not_landed
            logger.info("v1 land of %s without a hook token: %s", self.run_id, why)
            self.store.append_new(
                writer=self.authority.writer("cli"), kind="land", at=_now(),
                stage="deliver", attempt=self._next_attempt("deliver"),
                ordinal=len(self.store.read_events()) + 1,
                payload={"landing": "not_landed", "commit": None,
                         "reason": "needs_hooked_session"})
            self._settle(); self.write_projection()
            finished = self.finish_not_landed()
            return {**finished, "ok": False, "landing": "not_landed",
                    "reason": "needs_hooked_session", "detail": why}
        attempt = self._next_attempt("land")
        guard_ids = ["DV-1", "LG-1", "LG-2", "LG-3", "LG-4", "LG-5"]
        opened, _ = self.store.append_new(
            writer=self.authority.writer("cli"), kind="stage_attempt", at=_now(),
            stage="land", attempt=attempt, payload={"check_ids": guard_ids})
        results: dict[str, tuple[bool, str]] = {}

        def record(check_id: str, passed: bool, detail: str) -> None:
            results[check_id] = (passed, detail)
            self.store.append_new(
                writer=self.authority.writer("cli"), kind="check", at=_now(),
                stage="land", attempt=attempt,
                payload={"stage_attempt_event_id": opened["event_id"], "check_id": check_id,
                         "passed": passed, "detail": detail})

        def close(why: str) -> None:
            for check_id in guard_ids:
                if check_id not in results:
                    record(check_id, False, f"not evaluated: {why}")

        try:
            carry_error: IntegrationRequired | None = None
            try:
                self._carry_moved_finals()
            except IntegrationRequired as exc:
                carry_error = exc
            repos = self.effective_repos()
            code_trees = tree.build_code_trees(
                repos, self.run_id, git_tree.write_tree, git_tree.list_reserved)
            context = self.check_context()
            run_facts = {**dict(context.run), "code_trees": code_trees,
                         "landing_bases": {name: _git(root, "rev-parse", "HEAD")
                                           for name, root in self.contract["repo_roots"].items()}}
            context = dataclasses.replace(context, run=run_facts,
                                          events=tuple(self.store.read_events()))
            for check_id in ("LG-1", "LG-2", "LG-5"):
                record(check_id, *checks.evaluate(check_id, context))
            if carry_error is not None:
                prefix = "merge_conflict: " if carry_error.code == "merge_conflict" else ""
                record("LG-4", False, prefix + str(carry_error))
            if not all(passed for passed, _ in results.values()):
                close("an earlier landing guard failed")
                return self._land_failed(results)
            push = self.push_status()
            report_events = self.store.read_events()
            self.store.append_new(
                writer=self.authority.writer("cli"), kind="land_intent", at=_now(),
                stage="deliver", attempt=attempt,
                payload={"stage_attempt_event_id": opened["event_id"]})
            audit_value = self.build_audit(
                through="DELIVER", persist=True, event_rows=report_events, push=push)
            report = self._commit_report(
                audit_value, guards=("LG-1", "LG-2", "LG-3", "LG-4", "LG-5", "DV-1"))
            report_sha = self.store.put_blob(report)
            self.store.append_new(
                writer=self.authority.writer("cli"), kind="render", at=_now(),
                stage="deliver", attempt=attempt,
                payload={"document": "REPORT", "blob_sha": report_sha})
            bindings = tree.build_delivery_trees(
                repos, self.run_id, code_trees, report, report_sha,
                git_tree.write_tree, git_tree.list_reserved)
            run_facts["lg3_args"] = {"repos": repos, "run_id": self.run_id,
                                     "bindings": bindings, "rendered_report_sha": report_sha,
                                     "diff_trees": git_tree.diff_trees}
            context = dataclasses.replace(self.check_context(), run=run_facts,
                                          events=tuple(self.store.read_events()))
            for check_id in ("DV-1", "LG-3", "LG-4"):
                record(check_id, *checks.evaluate(check_id, context))
            if not all(passed for passed, _ in results.values()):
                return self._land_failed(results)
            bases = {name: self.final()["repos"][name]["base"]
                     for name in self.contract["expected_repos"]}
            try:
                commits = landing.commit_repos(
                    run_id=self.run_id, integration_roots=self.contract["repo_roots"],
                    bases=bases, bindings=bindings, message=message,
                    progress_path=self.run_dir / "artifacts" / "landing-progress-normal.json")
            except LandingError as exc:
                # the guards passed and the attempt is closed; the next land supersedes it
                self._discard_empty_progress()
                self.write_projection()
                return {"ok": False, "retry": True, "error": f"commit failed: {exc}",
                        "state": self.projection()}
        except Exception as exc:
            close(f"{type(exc).__name__}: {exc}"[:300])
            self._settle(); self.write_projection()
            raise
        DocumentStore._atomic(self.run_dir / "REPORT.md", report.decode("utf-8"))
        self.store.append_new(
            writer=self.authority.writer("cli"), kind="land", at=_now(),
            stage="deliver", attempt=attempt, ordinal=len(self.store.read_events()) + 1,
            payload={"landing": "landed", "commit": next(iter(commits.values())),
                     "stage_attempt_event_id": opened["event_id"]})
        self._settle(); self.write_projection()
        return {"ok": True, "commits": commits, "push": push, "state": self.projection()}

    def _land_failed(self, results: Mapping[str, tuple[bool, str]]) -> dict[str, Any]:
        self._settle(); self.write_projection()
        failed = {cid: detail for cid, (passed, detail) in results.items() if not passed}
        if self.projection().get("pending_landing") is not None:
            finished = self.finish_not_landed()
            return {**finished, "ok": False, "landing": "not_landed",
                    "reason": self.projection().get("reason"), "failed": failed}
        return {"ok": False, "retry": True, "failed": failed, "state": self.projection()}

    def _discard_empty_progress(self) -> None:
        path = self.run_dir / "artifacts" / "landing-progress-normal.json"
        progress = self._read_json(path, None)
        if isinstance(progress, Mapping) and not progress.get("commits"):
            path.unlink(missing_ok=True)

    def reflect_complete(self, *, degraded: bool = False, reason: str = "") -> dict[str, Any]:
        state = self.projection()
        if state["status"] != "reflecting":
            raise EngineError("reflect completion requires reflecting")
        kind = "reflect_exhausted" if degraded else "reflect_complete"
        payload = {"reason": reason or "background reflection exhausted"} if degraded else {}
        self.store.append_new(writer=self.authority.writer("cli"), kind=kind, at=_now(),
                              ordinal=len(self.store.read_events()) + 1, payload=payload)
        self._settle()
        commits: dict[str, str] = {}
        for progress_path in sorted((self.run_dir / "artifacts").glob("landing-progress-*.json")):
            progress = self._read_json(progress_path, {})
            if not isinstance(progress, Mapping):
                continue
            for name, commit in (progress.get("commits") or {}).items():
                if name in commits and commits[name] != commit:
                    raise EngineError(f"conflicting landing commit for {name}")
                if isinstance(name, str) and isinstance(commit, str):
                    commits[name] = commit
        if not commits:
            commits = {"primary": event["payload"]["commit"] for event in self.store.read_events()
                       if event.get("kind") == "land" and event.get("payload", {}).get("commit")}
        value = self.build_audit(through="COMPLETED", commits=commits, persist=True)
        from core.run_protocol import audit as audit_protocol
        DocumentStore._atomic(self.run_dir / "REPORT.md",
                              audit_protocol.render(value, fmt="report"))
        self.write_projection()
        return self.projection()
