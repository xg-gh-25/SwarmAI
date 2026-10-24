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

import copy
import hashlib
import json
import re
import logging
import os
import secrets
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from core import evidence_seal, run_worktree
from core.run_protocol import checks, git_tree, lifecycle, review, validation_report
from core.run_protocol.infra import InfrastructureError
from core.run_protocol.documents import DocumentStore, complete_stage
from core.run_protocol.events import EventAuthority
from core.run_protocol.final import carry_repo_final, merge_final
from core.run_protocol.store import NEXT, AuditStore, StoreError

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
_LINE_COMMENT = {".sh": _HASH_COMMENT, ".ts": _SLASH_COMMENT,
                 ".tsx": _SLASH_COMMENT, ".js": _SLASH_COMMENT, ".jsx": _SLASH_COMMENT}


def _blob(root: str, tree: str, file: str) -> tuple[str, bytes | None] | None:
    """*file* at *tree* as ``(mode, bytes)``: ``("absent", None)`` when the tree has no
    such path, ``(mode, None)`` when it is not a regular blob, and None when it cannot be
    read (review 950a628c: a read error is never mistaken for a missing file)."""
    try:
        listed = subprocess.run(["git", "-C", str(root), "ls-tree", "-z", tree, "--",
                                 f":(literal){file}"], capture_output=True, timeout=60)
        if listed.returncode != 0:
            return None
        if not listed.stdout:
            return "absent", None
        meta, _, _path = listed.stdout.rstrip(b"\0").partition(b"\t")
        mode, kind, sha = meta.decode("ascii").split()
        if kind != "blob":
            return mode, None
        data = subprocess.run(["git", "-C", str(root), "cat-file", "blob", sha],
                              capture_output=True, timeout=60)
        return (mode, data.stdout) if data.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _python_ast(source: bytes) -> str | None:
    """What a Python file executes, as a canonical AST dump: comments and layout are
    gone. Every string stays, docstrings included: a docstring is data another file
    can read (``__doc__``, ``inspect``, FastAPI descriptions), which no diff shows
    (XG 2026-10-23, tightening conformance L4). None when it does not decode or parse."""
    import ast
    import io
    import tokenize
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(source).readline)
        tree = ast.parse(source.decode(encoding))
    except (SyntaxError, UnicodeDecodeError, LookupError, ValueError):
        return None
    return ast.dump(tree, include_attributes=False)


def _comment_only_diff(file: str, diff: str, *,
                       old: "tuple[str, bytes | None] | None | bool" = False,
                       new: "tuple[str, bytes | None] | None | bool" = False) -> bool:
    """True when nothing executable changed (F-8). A document; a ``.py`` file present
    on both sides with the same regular-file mode whose AST is the same: only comments
    and layout may change (``_python_ast``; L4); any other source file whose every
    added / removed line is blank or a whole-line comment (a trailing comment on a code
    line is code). For ``.py``, *old* / *new* are ``_blob`` results: a file added or
    deleted, a mode or type change, an unreadable side, or one that does not parse is
    executable (fails closed). Without them only the hunk's two sides are compared."""
    suffix = Path(file).suffix.lower()
    if suffix in {".md", ".txt", ".rst"}:
        return True
    if suffix == ".py":
        if old is False or new is False:      # only the hunk: compare its two sides
            import textwrap
            rows = [line for line in diff.splitlines() if not line.startswith(("+++", "---"))]
            old = ("100644", (textwrap.dedent("\n".join(r[1:] for r in rows if r[:1] == "-"))
                              + "\n").encode())
            new = ("100644", (textwrap.dedent("\n".join(r[1:] for r in rows if r[:1] == "+"))
                              + "\n").encode())
        if not old or not new or old[1] is None or new[1] is None or old[0] != new[0] \
                or old[0] not in ("100644", "100755"):
            return False
        before, after = _python_ast(old[1]), _python_ast(new[1])
        return before is not None and before == after
    markers = _LINE_COMMENT.get(suffix)
    if markers is None:
        return False
    changed = [line[1:] for line in diff.splitlines()
               if line[:1] in "+-" and not line.startswith(("+++", "---"))]
    return bool(changed) and all(not text.strip() or markers.match(text.lstrip())
                                 for text in changed)


def _budgets(profile: str) -> dict[str, Any]:
    """The profile's speed / token reference lines from baselines.json (design §E9.3,
    built by scripts/pipeline_baseline.py). A missing file or profile gives null, and
    the audit says "no baseline" instead of inventing a number."""
    try:
        rows = json.loads((Path(__file__).parent / "baselines.json").read_text(encoding="utf-8"))
        row = rows["profiles"][profile]
    except (OSError, ValueError, KeyError, TypeError):
        row = {}
    out = {}
    for key in ("budget_seconds", "budget_tokens"):
        value = row.get(key) if isinstance(row, Mapping) else None
        out[key] = value if isinstance(value, (int, float)) and value > 0 else None
    return out


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
    return isinstance(chosen, Mapping) and (
        chosen.get("decided_by") == "agent_after_cap"
        or any(isinstance(row, Mapping) and row.get("decided_by") == "agent_after_cap"
               for row in chosen.get("decisions") or []))


def _store(root: Path, run_id: str, authority: EventAuthority,
           producer: str = "cli") -> AuditStore:
    """The run's store, refusing any event after which the log would not fold.

    One rule for every writer (fixing spec A), the CLI's and the runner's alike (K3
    review r2): an event the lifecycle or the finding ledger cannot apply is never
    written, and the transition an event triggers is appended in the same lock, signed
    by the *producer* that wrote the trigger."""
    from core.run_protocol import findings as finding_protocol

    def validate(chain: list[dict[str, Any]]) -> Any:
        state = lifecycle.fold(chain, authority, allow_pending_transition=True)
        if chain[-1].get("kind") in _FINDING_KINDS:
            finding_protocol.fold(chain, authority)
        return state.get("expected_transition")
    return AuditStore(root, run_id, authority, validator=validate,
                      transition_writer=authority.writer(producer))


_FINDING_KINDS = frozenset({"answer", "finding_occ", "finding_status",
                            "reclassify", "address", "proof"})


REQUIREMENT_MAX = 4000


def _log_tail(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    from core.run_protocol import events as ev
    return {"event_count": len(events),
            "tail": ev.event_digest(dict(events[-1])) if events else None}


def signed_terminal(data_dir: str | Path, run_id: str) -> bool:
    """Whether the run's SIGNED event log folds to a terminal status. Needs only the
    authority key and the log, never the contract, so it answers for a run whose
    contract is unreadable; anything that cannot be verified answers False (not
    finished: callers fail closed). Never reads the agent-writable run.json
    (re-audit c772dd92)."""
    from core.run_protocol.store import AuditStore
    root = Path(data_dir) / "state" / "pipeline_agent_audit"
    try:
        from core.run_protocol import keys
        authority = keys.load(data_dir, run_id)
        events = AuditStore(root, run_id, authority).read_events()
        return bool(events) and lifecycle.fold(
            events, authority, allow_pending_transition=True)["status"] in lifecycle.TERMINAL
    except Exception as exc:  # noqa: BLE001 — unverifiable: not finished
        logger.debug("run %s: signed state unverifiable, not finished: %s", run_id, exc)
        return False


_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


def _safe_project(project: str) -> bool:
    """One directory name under Projects/ (project names may hold spaces)."""
    return (isinstance(project, str) and 0 < len(project) <= 128
            and project not in (".", "..") and not any(c in project for c in "/\\\0"))


_CREATING = ".creating"


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


# identifies this process's lifetime: a staging directory of another lifetime (a daemon
# that died mid-create) is never one a create in THIS process is still building
_PROCESS_BOOT = f"{os.getpid()}-{time.time_ns()}"


def recover_incomplete_creates(data_dir: str | Path, workspace: str | Path) -> list[str]:
    """Remove the staging directories of creates whose process died before the rename
    that makes a run exist (``RunEngine._create``). They live only under the audit
    area's ``.creating/`` and are never a run, so nothing else is touched (no path is
    read from a file). Returns the removed directory names."""
    import shutil
    folder = Path(data_dir) / "state" / "pipeline_agent_audit" / _CREATING
    removed: list[str] = []
    for entry in sorted(folder.iterdir()) if folder.is_dir() else []:
        if entry.name.startswith(_PROCESS_BOOT + "-") or entry.is_symlink():
            continue
        shutil.rmtree(entry, ignore_errors=True)
        removed.append(entry.name)
    del workspace       # an empty run directory left behind is reused by the next create
    return removed


def repair_stale_projections(data_dir: str | Path, workspace: str | Path) -> list[str]:
    """Rewrite the run.json of every v1 run whose ``projection_of`` does not name the
    signed log's current tail (a crash between an event and the projection write).
    Returns the repaired run ids; a run that cannot be loaded is skipped (logged)."""
    base = Path(data_dir) / "state" / "pipeline_agent_audit" / "runs"
    repaired: list[str] = []
    for entry in sorted(base.iterdir()) if base.is_dir() else []:
        try:
            contract = json.loads((entry / "contract.json").read_text(encoding="utf-8"))
            engine = RunEngine.load(data_dir=data_dir, workspace=workspace,
                                    project=str(contract.get("project")), run_id=entry.name)
            try:
                seen = json.loads((engine.run_dir / "run.json").read_text(
                    encoding="utf-8")).get("projection_of")
            except (OSError, ValueError, AttributeError):
                seen = None
            if seen != _log_tail(engine.store.read_events()):
                # under the run's operation lock, judged again there: a command that
                # appended meanwhile wrote a newer projection, which must not be
                # overwritten (re-audit 84b72ec1 MED). Busy: that command projects.
                from core.run_protocol.oplock import RunBusy, operation
                try:
                    with operation(engine.audit_root, entry.name, timeout=0.5):
                        try:
                            seen = json.loads((engine.run_dir / "run.json").read_text(
                                encoding="utf-8")).get("projection_of")
                        except (OSError, ValueError, AttributeError):
                            seen = None
                        if seen != _log_tail(engine.store.read_events()):
                            engine.write_projection()
                            repaired.append(entry.name)
                except RunBusy:
                    continue
        except FileNotFoundError:
            continue                      # not a v1 run directory
        except Exception:  # noqa: BLE001 — one unreadable run never stops the others
            logging.getLogger(__name__).warning("projection repair skipped %s", entry.name,
                                                exc_info=True)
    return repaired


#: how long a mutation waits for another operation on the same run (seconds)
ENGINE_LOCK_TIMEOUT = 60.0


def _one_operation(method):
    """Run an engine mutation under the run's operation lock (``oplock``): the read,
    decide and write of the daemon's mutations (Cancel, Resume, a decision answer,
    session stop, owner transfer, REFLECT) never interleave with a CLI command's
    (re-audit ca91762f). Re-entrant in one thread, so the CLI, which already holds
    the lock for its whole command, is unaffected."""
    import functools

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        from core.run_protocol.oplock import operation
        with operation(self.audit_root, self.run_id, timeout=ENGINE_LOCK_TIMEOUT):
            return method(self, *args, **kwargs)
    return wrapper


class RunEngine:
    """One v1 run, projected from signed events and immutable contract metadata."""

    def __init__(self, *, data_dir: Path, workspace: Path, project: str,
                 run_id: str, authority: EventAuthority, producer: str = "cli"):
        self.data_dir, self.workspace = data_dir, workspace
        self.project, self.run_id = project, run_id
        self.run_dir = workspace / "Projects" / project / ".artifacts" / "runs" / run_id
        self.audit_root = data_dir / "state" / "pipeline_agent_audit"
        # *producer*: who this process signs as. The test runner loads a run to read
        # what comes next and signs nothing but its own facts (C3)
        self.store = _store(self.audit_root, run_id, authority, producer=producer)
        self.authority = authority
        try:
            self.contract = json.loads(self.store.paths.contract.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise EngineError(f"contract unreadable: {exc}") from exc

    @classmethod
    def create(cls, *, data_dir: str | Path, workspace: str | Path, project: str,
               repos: Mapping[str, str] | None = None, mode: str = "isolated",
               **kwargs: Any) -> "RunEngine":
        """Create a run. A shared-mode run reserves its repo roots: while another
        shared run of one of them is not terminal, it is refused (re-audit ca91762f:
        two shared runs computed finals and delivery trees from the same files). The
        check and the create hold a per-repo lock, so two creates cannot both pass."""
        if mode != "shared" or not repos:
            return cls._create(data_dir=data_dir, workspace=workspace, project=project,
                               repos=repos or {}, mode=mode, **kwargs)
        import contextlib
        import hashlib
        from utils.file_lock import flock_exclusive, flock_unlock
        roots = sorted({str(Path(root).resolve()) for root in repos.values()})
        lock_dir = Path(data_dir) / "state" / "pipeline_agent_audit" / "shared_repos"
        lock_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        with contextlib.ExitStack() as stack:
            for root in roots:
                handle = stack.enter_context(open(
                    lock_dir / (hashlib.sha256(root.encode()).hexdigest()[:32] + ".lock"),
                    "a+", encoding="utf-8"))
                flock_exclusive(handle)
                stack.callback(flock_unlock, handle)
            holder = cls._live_shared_holder(Path(data_dir), Path(workspace), set(roots))
            if holder:
                raise EngineError(f"run {holder} is a live shared run of the same repo: a "
                                  "second shared run would test and land its edits. Finish "
                                  "or abandon it first, or use worktree mode")
            return cls._create(data_dir=data_dir, workspace=workspace, project=project,
                               repos=repos, mode=mode, **kwargs)

    @classmethod
    def _live_shared_holder(cls, data_dir: Path, workspace: Path,
                            roots: "set[str]") -> "str | None":
        """The id of a non-terminal shared run whose contract names one of *roots*; a
        run whose signed state cannot be read counts as live (fail closed)."""
        from core.run_protocol.runner import read_contract
        base = data_dir / "state" / "pipeline_agent_audit" / "runs"
        for entry in sorted(base.iterdir()) if base.is_dir() else []:
            try:
                contract = read_contract(data_dir, entry.name)
            except Exception as exc:  # noqa: BLE001 — an unreadable contract: its repos unknown
                logger.warning("run %s: contract unreadable while checking shared "
                               "holders: %s", entry.name, exc)
                # a run its projection calls finished holds nothing; any other might
                # hold these repos: fail closed (re-audit 84b72ec1 MED)
                if not signed_terminal(data_dir, entry.name):
                    return entry.name
                continue
            if not isinstance(contract, dict) or contract.get("mode") != "shared":
                continue
            theirs = {str(Path(r).resolve()) for r in (contract.get("repo_roots") or {}).values()}
            if not theirs & roots:
                continue
            try:
                other = cls.load(data_dir=data_dir, workspace=workspace,
                                 project=str(contract.get("project")), run_id=entry.name)
                if other.projection(allow_pending=True)["status"] in lifecycle.TERMINAL:
                    continue
            except Exception as exc:  # noqa: BLE001 — unknown state: treated as live
                logger.warning("run %s: state unreadable, treated as live shared "
                               "holder: %s", entry.name, exc)
            return entry.name
        return None

    @classmethod
    def _create(cls, *, data_dir: str | Path, workspace: str | Path, project: str,
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
        # every run has an owner from the start (R1 review r1): the contract is where
        # that invariant lives, whoever calls create
        if not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}",
                                                                session_id):
            raise EngineError("create requires the owning app session id")
        if any(not valid_session_id(value) for value in transcript_ids):
            raise EngineError("invalid transcript id")
        workspace_path, data_path = Path(workspace), Path(data_dir)
        run_dir = workspace_path / "Projects" / project / ".artifacts" / "runs" / run_id
        # The run comes into existence in ONE step (B-e review HIGH): its audit
        # directory (key, contract, signed run_create) is built in a staging directory
        # of this process and renamed into runs/<id> only when complete. A process that
        # dies before the rename leaves only a staging directory, which startup removes
        # (recover_incomplete_creates); the id is never left half-taken.
        if not _SAFE_ID.fullmatch(run_id) or not _safe_project(project):
            raise EngineError("invalid run id or project")
        audit_root = data_path / "state" / "pipeline_agent_audit"
        audit = audit_root / "runs" / run_id
        if audit.exists():
            raise EngineError("run already exists")
        try:
            run_dir.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            # an EMPTY directory is what a create that died before its rename left: reuse
            if not run_dir.is_dir() or run_dir.is_symlink() or any(run_dir.iterdir()):
                raise EngineError("run already exists") from exc
        stage_data = audit_root / _CREATING / f"{_PROCESS_BOOT}-{secrets.token_hex(4)}"
        stage_audit_root = stage_data / "state" / "pipeline_agent_audit"
        try:
            (stage_audit_root / "runs" / run_id).mkdir(parents=True, mode=0o700)
            from core.run_protocol import keys
            authority = keys.create(stage_data, run_id)
            engine = cls.__new__(cls)
            engine.data_dir, engine.workspace = data_path, workspace_path
            engine.project, engine.run_id, engine.run_dir = project, run_id, run_dir
            engine.audit_root, engine.authority = stage_audit_root, authority
            engine.store = _store(stage_audit_root, run_id, authority)
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
                        "regression_round_cap": 2, "recheck_cap": 3,
                        # reference lines, never limits: the profile's p95 (D2); null = none
                        **_budgets(profile)}
            if requirement:
                contract["requirement"] = requirement
            engine.contract = contract
            engine.store.write_contract_once(contract)
            engine.store.append_new(
                writer=authority.writer("cli"), kind="run_create", at=contract["created_at"],
                payload={"run_id": run_id, "profile": profile, "decision_cap": decision_cap})
            audit.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            _fsync_dir(stage_audit_root / "runs" / run_id)
            try:
                # atomic, and refused when runs/<id> exists and is not empty (a run)
                os.rename(stage_audit_root / "runs" / run_id, audit)
            except OSError as exc:
                raise EngineError("run already exists") from exc
            _fsync_dir(audit.parent)
            engine.audit_root = audit_root
            engine.store = _store(audit_root, run_id, authority)
        except BaseException as exc:
            import shutil
            shutil.rmtree(stage_data, ignore_errors=True)   # only this call's staging
            if not audit.exists():
                try:
                    run_dir.rmdir()                  # only when still empty
                except OSError:
                    pass
            if isinstance(exc, OSError) and not isinstance(exc, StoreError):
                from core.run_protocol.store import AuditUnwritable
                raise AuditUnwritable(f"audit area not writable: {exc}") from exc
            raise
        import shutil
        shutil.rmtree(stage_data, ignore_errors=True)
        evidence_seal.enable(run_id)
        engine.write_projection()
        return engine

    @classmethod
    def load(cls, *, data_dir: str | Path, workspace: str | Path,
             project: str, run_id: str, producer: str = "cli") -> "RunEngine":
        workspace_path = Path(workspace)
        resolved_run = find_run_dir(workspace_path, run_id)
        actual_project = resolved_run.parents[2].name
        if project != actual_project:
            raise EngineError(f"run {run_id} belongs to project {actual_project}, not {project}")
        from core.run_protocol import keys
        try:
            authority = keys.load(data_dir, run_id)
        except keys.KeyUnavailable as exc:
            raise EngineError(str(exc)) from exc
        engine = cls(data_dir=Path(data_dir), workspace=workspace_path,
                     project=actual_project, run_id=run_id, authority=authority,
                     producer=producer)
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

    @_one_operation
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
            ordinal=NEXT,
            payload={"from_session": current, "to_session": to_session,
                     "reason": reason})
        self.write_projection()
        return True

    @_one_operation
    def record_stop(self, kind: str, *, reason: str) -> bool:
        """Record ``user_stop`` / ``session_lost``: an ACTIVE run becomes paused; a run
        waiting for a human keeps waiting (no event). Returns whether it paused."""
        if kind not in ("user_stop", "session_lost"):
            raise EngineError(f"unknown stop kind {kind!r}")
        if self.projection()["status"] != "active":
            return False
        self.store.append_new(
            writer=self.authority.writer("cli"), kind=kind, at=_now(),
            ordinal=NEXT, payload={"reason": reason})
        self._settle(); self.write_projection()
        return True

    def decision_value(self, request_id: str) -> dict[str, Any] | None:
        """The decision exactly as its validated event recorded it (conformance K1)."""
        from core.run_protocol import facts
        try:
            return facts.decision_values(self.store.read_events(),
                                         self.store.paths.blobs).get(request_id)
        except facts.FactError as exc:
            raise EngineError(str(exc)) from exc

    def pending_decision(self) -> dict[str, Any] | None:
        """The open decision as the human sees it, with the exact AskUserQuestion
        payload the agent must ask (``ask_user_question``), or None."""
        state = self.projection()
        request_id = state.get("pending_decision")
        if state["status"] != "awaiting_human" or not request_id:
            return None
        value = self.decision_value(request_id)
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

    @_one_operation
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
                created.append((name, self._create_worktree(name)))
        except run_worktree.WorktreeError as exc:
            for name, entry in created:
                try:
                    run_worktree._remove(self.run_id, entry,
                                         Path(self.contract["repo_roots"][name]), merged=False)
                except run_worktree.WorktreeError:
                    pass
            self.store.append_new(
                writer=self.authority.writer("cli"), kind="worktree_unavailable", at=_now(),
                ordinal=NEXT, payload={"reason": exc.message})
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

    def _create_worktree(self, name: str) -> dict:
        """The one place a run worktree is created: it also records the integration
        branch it forked from, so the run's base is always nameable (conformance F14)."""
        main = self.contract["repo_roots"][name]
        try:
            branch = _git(main, "symbolic-ref", "-q", "HEAD")
        except EngineError as exc:
            raise run_worktree.WorktreeError(
                "detached_main", f"{name}: main checkout has no branch checked out") from exc
        # recorded first: a crash between the two steps leaves a record and no worktree
        # (the next start creates it), never a worktree whose base cannot be named
        self._record_integration_branch(name, branch)
        entry = run_worktree.create(self.run_id, name, main, workspace=self.workspace)
        if entry.get("integration_branch") != branch:
            try:
                run_worktree._remove(self.run_id, entry, Path(main), merged=False)
            finally:
                raise run_worktree.WorktreeError(
                    "branch_moved", f"{name}: main switched branches while the worktree was made")
        return entry

    def _fork_base(self, name: str, root: str) -> str:
        """The run's base for *root* (facts.resolve_base): never a guess. Resolved once
        per engine for the same worktrees (3 git processes each time)."""
        from core.run_protocol import facts
        state = tuple(sorted(self.effective_repos().items()))
        if getattr(self, "_bases", (None,))[0] != state:
            self._bases = (state, {})
        memo = self._bases[1]
        if (name, root) in memo:
            return memo[(name, root)]
        branches = self._read_json(self.store.paths.run / "integration_branches.json", {}) or {}
        try:
            base = facts.resolve_base(root, self.contract["repo_roots"].get(name),
                                      branches.get(name))
        except facts.FactError as exc:
            raise EngineError(str(exc)) from exc
        memo[(name, root)] = base
        return base

    def _current_code(self, expected: Sequence[str]):
        """CodeFacts (commit 2, efficiency): computed once per engine (one command) for
        a given place of the code — the repos' worktrees and, after landing, the landed
        commits — not once per check: a command recomputed them up to 3 times, 11 git
        processes each. Within one command only the command itself moves the code, and
        each such move (a worktree made or restored, a landing) changes this key. Code
        an agent edits while a command runs is the next command's input."""
        state = self.projection(allow_pending=True)
        key = (tuple(expected), tuple(sorted(self.effective_repos().items())),
               state.get("landing"),
               tuple(sorted(self.landed_commits().items()))
               if state.get("landing") in ("landed", "human_override") else ())
        cached = getattr(self, "_code_facts", None)
        if cached is not None and cached[0] == key:
            return copy.deepcopy(cached[1])
        value = self._compute_current_code(expected)
        self._code_facts = (key, copy.deepcopy(value))
        return value

    def _compute_current_code(self, expected: Sequence[str]):
        """Where each repo's current code is (conformance K1, review round 1 #2):

        - a repo this run LANDED: the landed commit (its tree minus the run's REPORT,
          its parent as base) — never today's main or a released worktree;
        - an isolated repo: its own worktree only; a missing worktree is unknown,
          never replaced by the main checkout another session shares;
        - a shared run: the main checkout.
        Returns (roots, code_trees, bases, existing_files, changed_by_repo)."""
        reserved = (f".aidlc/deliverables/{self.run_id}/",)
        landed = self.landed_commits() if self.projection().get("landing") in (
            "landed", "human_override") else {}
        if set(landed) == {"primary"} and len(expected) == 1:
            landed = {expected[0]: landed["primary"]}
        live = self.effective_repos()
        roots: dict[str, str] = {}
        code_trees: dict[str, str | None] = {}
        bases: dict[str, str | None] = {}
        existing: set[str] = set()
        changed: dict[str, list[str]] = {}
        log = logging.getLogger(__name__)
        for name in expected:
            main = self.contract["repo_roots"].get(name)
            commit = landed.get(name)
            try:
                if commit and main:
                    roots[name] = main
                    code_trees[name] = git_tree.commit_code_tree(main, commit, reserved)
                    bases[name] = _git(main, "rev-parse", f"{commit}^")
                    existing |= set(_git(main, "ls-tree", "-r", "--name-only", commit).splitlines())
                    changed[name] = sorted(p for p in _git(
                        main, "-c", "core.quotepath=off", "diff", "--name-only", "--no-renames",
                        bases[name], commit).splitlines() if p and not p.startswith(reserved[0]))
                    continue
                root = live.get(name)
                if root is None:
                    code_trees[name] = bases[name] = None
                    log.warning("run %s: %s has no worktree; its facts are unknown",
                                self.run_id, name)
                    continue
                roots[name] = root
                code_trees[name] = git_tree.write_tree(root, reserved, {})
                existing |= set(_git(root, "ls-files").splitlines())
                bases[name] = self._fork_base(name, root)
                # the tree already holds committed, staged, unstaged and untracked
                # changes (git_tree adds -A on a private index): one diff against it
                changed[name] = sorted(p for p in _git(
                    root, "-c", "core.quotepath=off", "diff", "--name-only", "--no-renames",
                    bases[name], code_trees[name]).splitlines() if p)
            except InfrastructureError:
                raise                   # the run's infrastructure, not an unknown fact (F11)
            except (EngineError, git_tree.GitTreeError) as exc:
                code_trees.setdefault(name, None)
                bases[name] = bases.get(name) if code_trees.get(name) else None
                log.warning("run %s: facts of %s unavailable: %s", self.run_id, name, exc)
        return roots, code_trees, bases, existing, changed

    def check_context(self) -> checks.CheckContext:
        """The facts every check reads (conformance K1): the final only when it binds
        the current code trees and bases; the required validations from the BASE
        manifest; the decisions as their events recorded them."""
        from core.run_protocol import facts
        from core.run_protocol import findings as finding_protocol
        requirements = self._read_json(self.run_dir / "artifacts" / "requirements.json", {})
        design = self._read_json(self.run_dir / "artifacts" / "design_spec.json", None)
        # the decided decisions are facts of the signed log; the documents only
        # project them, so the checks read them overlaid (K3 review r2)
        if isinstance(requirements, Mapping) and requirements:
            requirements = self._with_decisions("REQUIREMENTS", requirements)
        if isinstance(design, Mapping):
            design = self._with_decisions("DESIGN_SPEC", design)
        events = self.store.read_events()
        expected = list(self.contract["expected_repos"])
        repos, code_trees, bases, existing, changed = self._current_code(expected)
        profile = self.contract["profile"]
        req_variant = "lite" if profile == "direct" else "full"
        design_variant = None if profile in {"research", "direct"} else (
            "lite" if profile == "trivial" else "full")
        flat_changed = sorted({p for paths in changed.values() for p in paths})
        run = {**self.projection(), "mode": self.contract["mode"],
               "code_trees": code_trees, "run_bases": bases, "changed_paths": flat_changed,
               "stale_closures": sorted(facts.stale_closures(
                   finding_protocol.fold(list(events), self.authority), events, code_trees)),
               "lessons": self._read_json(self.run_dir / "artifacts" / "reflect.json", {}).get("lessons", []),
               "agent_after_cap": _agent_after_cap(requirements, design)}
        return checks.make_context(
            profile=profile, requirement_variant=req_variant, design_variant=design_variant,
            requirements=requirements, design=design, events=events, authority=self.authority,
            final={}, run=run, expected_repos=set(expected),
            existing_files=existing,
            decision_documents=facts.decision_values(events, self.store.paths.blobs),
            known_refs=self.decision_refs(events))

    def decision_refs(self, events: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
        """What a decision may cite (DC-1): this run's events, REQUIREMENTS analysis
        rows (A<n>) and review findings. One builder for asking and for checking."""
        refs: dict[str, Mapping[str, Any]] = {event["event_id"]: event for event in events}
        requirements = self._read_json(self.run_dir / "artifacts" / "requirements.json", {})
        for index, row in enumerate(requirements.get("analysis", []) if isinstance(requirements, Mapping) else [], 1):
            if isinstance(row, Mapping):
                refs[f"A{index}"] = {**dict(row), "analysis_kind": row.get("kind"),
                                     "kind": "analysis"}
        for row in review.ReviewOrchestrator(self.store, self.run_dir).findings():
            refs[row["canonical_id"]] = row
        return refs

    def owned_paths(self) -> set[str]:
        """The real paths this run's code lives at (R1, active_run_commit_gate): every
        path it changed against its fork base, in its own checkout and, for an isolated
        run, at the same place in the main checkout. Read from the run's code, never
        from a field an agent wrote. Nothing once the run is terminal."""
        if self.projection()["status"] in lifecycle.TERMINAL:
            return set()
        expected = list(self.contract["expected_repos"])
        roots, trees, _bases, _existing, changed = self._current_code(expected)
        # every repo of the contract, not only those with a root: an isolated repo whose
        # worktree is gone has none, and its paths are just as unknown (re-audit 99f96949)
        missing = [name for name in expected
                   if name not in roots or not trees.get(name) or name not in changed]
        if missing:
            # a repo whose code facts cannot be read owns an unknown set of paths: the
            # caller refuses rather than treat it as owning none (R1 review r2)
            raise EngineError(f"code facts unavailable for {', '.join(missing)}")
        from core.security_hooks import _commit_identity
        out: set[str] = set()
        for name, paths in changed.items():
            homes = {roots.get(name), self.contract["repo_roots"].get(name)}
            for home in filter(None, homes):
                out.update(_commit_identity(os.path.join(home, p)) for p in paths)
        return out

    @staticmethod
    def _read_model(events: list[Mapping[str, Any]]) -> dict[str, Any]:
        """Dashboard facts the fold does not keep (R1b): when the run finished (the
        terminal transition's time) and its token usage, by the audit's one rule
        (``audit.token_totals``). Absent when never recorded."""
        from core.run_protocol.audit import token_totals
        out: dict[str, Any] = {}
        for event in events:
            if event.get("kind") == "transition" \
                    and (event.get("payload") or {}).get("to") in lifecycle.TERMINAL:
                out["finished_at"] = event.get("at")
        tokens_in, tokens_out = token_totals(events)
        if isinstance(tokens_in, (int, float)) and isinstance(tokens_out, (int, float)):
            out["usage"] = {"input_tokens": int(tokens_in), "output_tokens": int(tokens_out)}
        return out

    def write_projection(self) -> Path:
        events = self.store.read_events()          # read and verified once
        state = lifecycle.fold(events, self.authority)
        value = {key: val for key, val in state.items()
                 if key not in {"expected_transition", "open_attempt", "auth_grants"}}
        value.update(self._read_model(events))
        value.update({"id": self.run_id, "project": self.project,
                      "session_id": self.owner_session(),
                      "worktree_mode": self.contract["mode"],
                      "worktrees": [{"repo_name": name, "main": self.contract["repo_roots"][name],
                                     "path": root, "state": "active"}
                                    for name, root in self.effective_repos().items()],
                      "updated_at": _now(), "created_at": self.contract["created_at"],
                      # what this projection was built from: a reader (and the startup
                      # repair) can tell a projection the log has moved past (re-audit
                      # ca91762f)
                      "projection_of": _log_tail(events)})
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

    @_one_operation
    def gate0_start(self, requirements: Mapping[str, Any] | None = None) -> dict[str, Any]:
        state = self.projection()
        if state["status"] != "active" or state["stage"] != "evaluate" \
                or state["profile"] not in {"full", "bugfix", "goal"}:
            raise EngineError("Gate-0 requires active EVALUATE on full, bugfix, or goal")
        docs = DocumentStore(self.run_dir, on_render_failed=self._render_failed)
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
            self.record_cap_exceeded("gate0_round", stage="evaluate")
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

    def _gate0_ended_at_cap(self) -> bool:
        """Two Gate-0 rounds answered and the latest one unparsed or negative."""
        events = self._since_last_answer()
        rounds = [event for event in events
                  if event.get("kind") == "answer"
                  and event.get("payload", {}).get("expert_id") == "gate0-skeptic"]
        if len(rounds) < 2:
            return False
        latest = rounds[-1].get("payload", {})
        return latest.get("state") != "parsed" or \
            latest.get("verdict") in {"WRONG-FRAME", "UNSUPPORTED"}

    def _gate0_disposition(self) -> str:
        events = self._since_last_answer()
        answered = len(events) != len(self.store.read_events())
        handouts = [event for event in events
                    if event.get("kind") == "handout" and event.get("stage") == "evaluate"
                    and event.get("payload", {}).get("expert_id") == "gate0-skeptic"]
        if answered and not handouts:
            return "continue"      # the person decided after the gate; nothing newer to wait for
        answers = [event for event in events
                   if event.get("kind") == "answer"
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

    @_one_operation
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
        self._write_review_documents()
        return {"handoff": handoff, "reviewers": sorted(roster), "handouts": handouts}

    @_one_operation
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
        bound_nodes: dict[str, Any] | None = None
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
                # §A3.5: a refutation cites evidence that exists — a file:line of the
                # tree that lands, an event of this run, or a test result on that tree
                from core.run_protocol import facts
                if bound_nodes is None:
                    bound_nodes = {}
                try:
                    resolved = facts.resolve_refs(
                        supplied.get("refs"), root=root, code_tree=code_tree,
                        event_ids={e["event_id"] for e in self.store.read_events()},
                        final_nodes=bound_nodes.get(_name, {}))
                except facts.FactError as exc:
                    raise EngineError(f"{finding_id}: {exc}") from exc
                derived.append({"finding_id": finding_id, "evidence_type": "refuted",
                                "code_tree": code_tree, "result": "refuted",
                                "refs": resolved, "repo": _name})
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
            non_executable = _comment_only_diff(
                file, diff, old=_blob(root, self._fork_base(_name, root), file),
                new=_blob(root, code_tree, file))
            derived.append({"finding_id": finding_id,
                            "evidence_type": "diff_assertion", "repo": _name,
                            "code_tree": code_tree, "result": "asserted",
                            "touches_finding": touches,
                            "touches_executable": not non_executable})
        events = orchestrator.record_cli_proofs(
            {"pass": artifact["pass"], "proofs": derived}, at=_now(),
            attempt=1 + sum(event.get("kind") == "proof"
                            for event in self.store.read_events()))
        self._write_review_documents()
        return {"proofs": events, "findings": orchestrator.findings()}

    @_one_operation
    def request_decision(self, value: Mapping[str, Any], *, document: str) -> dict[str, Any]:
        from core.run_protocol.documents import record_decision
        if self.contract.get("profile") == "direct":
            raise EngineError("a direct run makes no decisions: abandon it and re-create the "
                              "request as --profile trivial, then ask there")
        refs = self.decision_refs(self.store.read_events())
        state = self.projection()
        value = dict(value)
        after_cap = value.pop("after_cap", None)
        event = record_decision(self.store, value, known_refs=refs, document=document,
                                at=_now(), decisions_answered=state["human_decisions"],
                                cap=state["decision_cap"], after_cap=after_cap)
        self._settle()
        if after_cap is not None:
            self._sync_decision_document(document)     # DC-2: the choice is in the document
            self.write_projection()
            return {"event": event, "decision": value, "decided_by": "agent_after_cap",
                    "state": self.projection()}
        self.write_projection()
        return {"event": event, "decision": value, "state": self.projection(),
                "ask_user_question": __import__("core.run_protocol.decision",
                                                fromlist=["ask_payload"]).ask_payload(value)}

    def _answered_decisions(self, document: str) -> list[dict[str, Any]]:
        """Every decided decision of *document*, from the signed log alone: a person's
        answer (``human_answer``) or the agent's after-cap choice (a ``decision`` with
        ``decided_by``). The documents carry them as a projection (conformance F13)."""
        events = self.store.read_events()
        asked = {e["payload"]["request_id"]: e for e in events if e.get("kind") == "decision"}
        picks: list[tuple[str, str, Mapping[str, Any]]] = []
        for event in events:
            payload = event.get("payload") or {}
            if event.get("kind") == "human_answer":
                picks.append((payload["request_id"], payload["option_id"], {"answered_by": "user"}))
            elif event.get("kind") == "decision" and payload.get("decided_by"):
                picks.append((payload["request_id"], payload["option_id"],
                              {"answered_by": "evidence", "decided_by": payload["decided_by"],
                               "evidence_refs": list(payload.get("evidence_refs") or [])}))
        out = []
        for request_id, option_id, how in picks:
            source = asked.get(request_id)
            value = self.decision_value(request_id) or {}
            if source is None or source["payload"].get("document") != document:
                continue
            option = next((o for o in value.get("options") or []
                           if isinstance(o, Mapping) and o.get("id") == option_id), {})
            out.append({"request_id": request_id, "question": value.get("question"),
                        "option_id": option_id, "choice": option.get("choice"), **how})
        return out

    def _with_decisions(self, document: str, value: Mapping[str, Any]) -> dict[str, Any]:
        """*value* with every decided decision of *document* written in: REQUIREMENTS
        clarifications (qid = request id), DESIGN_SPEC ``chosen.decisions``."""
        import copy
        out = copy.deepcopy(dict(value))
        rows = self._answered_decisions(document)
        if not rows or (document == "DESIGN_SPEC" and self.contract["profile"] == "trivial"):
            # the lite DESIGN_SPEC has no ``chosen``: its decision stays in the signed
            # log and the REPORT (a registered deviation, conformance F13)
            return out
        if document == "REQUIREMENTS":
            ids = {row["request_id"] for row in rows}
            kept = [row for row in out.get("clarifications") or []
                    if not (isinstance(row, Mapping) and row.get("qid") in ids)]
            out["clarifications"] = kept + [
                {"qid": row["request_id"], "question": row["question"],
                 "answer": row["choice"], "answered_by": row["answered_by"],
                 **({"decided_by": row["decided_by"]} if row.get("decided_by") else {})}
                for row in rows]
        else:
            chosen = out.get("chosen")
            base = dict(chosen) if isinstance(chosen, Mapping) else (
                {"alternative": chosen} if chosen else {})
            base["decisions"] = [
                {key: row[key] for key in ("request_id", "option_id", "choice", "answered_by",
                                           "decided_by", "evidence_refs") if key in row}
                for row in rows]
            out["chosen"] = base
        return out

    def _sync_decision_document(self, document: str) -> None:
        """Project the decided decisions into *document* now; when the write fails the
        signed event stands, the checks read it overlaid, and the next stage-complete
        projects it again (K3 review r2)."""
        try:
            self._write_back_decisions(document)
        except Exception as exc:   # noqa: BLE001 — a projection, never the fact
            if isinstance(exc, InfrastructureError):
                raise
            logger.warning("run %s: %s not re-rendered with its decisions: %s",
                           self.run_id, document, exc)

    def _write_back_decisions(self, document: str) -> None:
        """Rewrite *document* (if it exists yet) with its decided decisions and render
        it again; a document written later gets them at its stage-complete."""
        docs = DocumentStore(self.run_dir, on_render_failed=self._render_failed)
        if document == "REQUIREMENTS":
            path = self.run_dir / "artifacts" / "requirements.json"
            if path.is_file():
                docs.write_requirements(self._with_decisions(document, self._read_json(path, {})),
                                        variant="full", profile=self.contract["profile"],
                                        blocking=set())
        else:
            path = self.run_dir / "artifacts" / "design_spec.json"
            if path.is_file():
                docs.write_design(
                    self._with_decisions(document, self._read_json(path, {})),
                    variant="lite" if self.contract["profile"] == "trivial" else "full",
                    profile=self.contract["profile"],
                    requirements=self._read_json(self.run_dir / "artifacts" / "requirements.json", {}),
                    requirement_variant="full", existing_files=self.check_context().existing_files,
                    blocking=set())

    @_one_operation
    def answer_decision(self, *, request_id: str, option_id: str) -> dict[str, Any]:
        """Record a HUMAN's answer. Called only from the daemon (the chat question card
        or the dashboard decision card); the agent's CLI has no answer command."""
        from core.run_protocol import decision
        state = self.projection()
        if state["status"] != "awaiting_human" or state.get("pending_decision") != request_id:
            raise EngineError("decision answer is not for the current pending decision")
        value = self.decision_value(request_id)
        if not isinstance(value, Mapping):
            raise EngineError("pending decision document is missing")
        answer = decision.answer(value, request_id=request_id, option_id=option_id)
        event, _ = self.store.append_new(
            writer=self.authority.writer("hook"), kind="human_answer", at=_now(),
            payload={"request_id": request_id, "option_id": option_id})
        self._settle()
        # the answer goes into its source document at the same place (conformance F13)
        document = next((e["payload"].get("document") for e in self.store.read_events()
                         if e.get("kind") == "decision"
                         and e["payload"].get("request_id") == request_id), None)
        if document:
            self._sync_decision_document(document)
        self.write_projection()
        return {"event": event, "answer": answer, "state": self.projection()}

    @_one_operation
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
            ordinal=NEXT, payload={"reason": reason.strip()})
        self._settle(); self.write_projection()
        return {"event": event, "retention": entries, "state": self.projection()}

    @_one_operation
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
            ordinal=NEXT, payload={"snapshot_exists": True})
        self._settle(); self.write_projection()
        return {"event": event, "restored": restored, "state": self.projection()}

    @_one_operation
    def resume(self) -> dict[str, Any]:
        state = self.projection()
        if state["status"] != "paused":
            raise EngineError("resume requires paused")
        ready = True
        if state.get("reason") == "worktree_unavailable":
            try:
                for name in self.contract["expected_repos"]:
                    if name not in self.effective_repos():
                        self._create_worktree(name)
            except run_worktree.WorktreeError:
                ready = False
            # ready = every worktree exists AND its base is nameable (recorded branch)
            branches = self._read_json(self.store.paths.run / "integration_branches.json", {}) or {}
            ready = ready and set(self.effective_repos()) == set(self.contract["expected_repos"]) \
                and all(isinstance(branches.get(n), str) for n in self.contract["expected_repos"])
        event, _ = self.store.append_new(
            writer=self.authority.writer("cli"), kind="resume", at=_now(),
            ordinal=NEXT, payload={"worktree_ready": ready})
        self._settle(); self.write_projection()
        return {"ok": ready, "event": event, "state": self.projection()}

    def _commit_report(self, audit_value: Mapping[str, Any], *, guards: Sequence[str],
                       request_id: str | None = None, note: str | None = None) -> bytes:
        """The commit-version REPORT (§B8): the renderer's text only. The guards are in
        its one-line conclusion; a manual landing's request and words come in through
        the audit's ``manual`` detail (no extra lines outside the allowed set)."""
        from core.run_protocol import audit as audit_protocol
        self._write_review_documents()   # DELIVER ends here: REVIEW.md as landed (F20)
        value = dict(audit_value)
        if request_id:
            value["details"] = {**dict(audit_value.get("details") or {}),
                                "manual": {"request_id": request_id, "note": note}}
        return audit_protocol.render(value, fmt="report").encode()

    @_one_operation
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
            # a tested landing needs the TEST report on the CURRENT tree (S6-1)
            current = tree.build_code_trees(self.effective_repos(), self.run_id,
                                            git_tree.write_tree, git_tree.list_reserved)
            reports = validation_report.latest(self.store.read_events(), "test")
            stale = sorted(name for name, code in current.items()
                           if (reports.get(name) or {}).get("payload", {}).get("code_tree")
                           != code)
            if stale:
                raise EngineError(
                    "no TEST report on the current tree of " + ", ".join(stale)
                    + ": run the tests and submit them with `report-tests --artifact -`, "
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
                **self._audit_facts(),
                run_id=self.run_id,
                projection=lifecycle.fold(prior, self.authority), events=prior,
                final={}, findings=finding_protocol.fold(prior, self.authority),
                contract=self.contract, push=push, audit_through="DELIVER")
            guards = ("LG-3", "LG-4") if action == "land_skip_tests" else (
                "LG-1", "LG-3", "LG-4")
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

    @_one_operation
    def execute_land_request(self, request_id: str, *, message: str) -> dict[str, Any]:
        """Consume one daemon-issued approval token and finish a prepared landing."""
        with self._landing_lock():
            return self._execute_land_request(request_id, message=message)

    def _execute_land_request(self, request_id: str, *, message: str) -> dict[str, Any]:
        from core.run_protocol.landing import check_message
        message = check_message(message)    # each repo's trailer is added by the landing
        from core.run_protocol import auth, landing

        try:
            payload = landing.request_event(self.store, request_id)
        except landing.LandingError as exc:
            raise EngineError(str(exc)) from exc
        request = payload["request"]
        if request.get("session_id") != self.owner_session():
            raise EngineError("land request was prepared by another owner session")
        ok, reason = self._consume_token(f"run-land:{request_id}")
        if not ok:

            raise IntegrationRequired("approval_required", reason)

        now = __import__("time").time()
        try:
            commits = landing.execute_request(
                self.store, request_id,
                session_id=self.owner_session(), repos=self.effective_repos(),
                integration_roots=self.contract["repo_roots"], message=message,
                at=_now(), now=now, branches=self._recorded_branches(),
                in_place=self.contract.get("mode") == "shared",
                adopted=self._partially_landed(),
                progress_path=self.run_dir / "artifacts" / f"landing-progress-{request_id}.json")
        except landing.PartialLanding as exc:
            # the signed partial_land is written; the person sees what moved (K2)
            self._settle()
            value = self.build_audit(through="COMPLETED" if self.projection()["status"] ==
                                     "completed" else "DELIVER", persist=True,
                                     commits=self.landed_commits() or None)
            from core.run_protocol import audit as audit_protocol
            DocumentStore._atomic(self.run_dir / "REPORT.md",
                                  audit_protocol.render(value, fmt="report"))
            self.write_projection()
            return {"ok": False, "reason": "partial_land", "landed": exc.landed,
                    "failed": exc.failed, "error": str(exc), "state": self.projection()}
        try:
            report = self.store.read_blob(payload["report_sha"]).decode("utf-8")
        except OSError as exc:
            raise EngineError(f"prepared REPORT blob is missing after landing: {exc}") from exc
        DocumentStore._atomic(self.run_dir / "REPORT.md", report)
        self._settle()
        self.write_projection()
        if self.projection()["status"] == "completed":
            self._refresh_completed_audit()     # the card must say it landed now
        auth_event_id = next(event_id for event_id, row in lifecycle.fold(
            self.store.read_events(), self.authority)["auth_grants"].items()
            if row["request_id"] == request_id)
        return {"auth_event_id": auth_event_id, "commits": commits,
                "state": self.projection(), "push": self.push_status(manual_landing=True)}

    def _ledger_of(self, repo_name: str) -> Path:
        """Where run_worktree keeps this run's snapshot of *repo_name* (never from JSON)."""
        return run_worktree.ledger_root() / self.run_id / repo_name

    @_one_operation
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

    @_one_operation
    def execute_authorized_action(self, request_id: str) -> dict[str, Any]:
        """Consume a daemon-issued token, append auth facts, then perform one action."""
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
        ok, reason = self._consume_token(token_scope)
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
        self._write_review_documents()
        self.write_projection()
        return {"auth_event_id": consumed["payload"]["auth_event_id"],
                "consumed_event_id": consumed["event_id"],
                "action": action, "state": self.projection()}

    WORKTREE_STAGES = frozenset({"plan", "build", "goal_cycle", "review", "test", "deliver"})

    @_one_operation
    def ensure_stage_worktrees(self) -> dict[str, str]:
        """The run's worktrees exist whenever it is active in a stage that edits code
        (r6 §2.3: no ``stage-start``; every mutating command ends by calling this, so
        the call that enters such a stage makes them). Idempotent. run.json is rewritten
        only when a worktree was made: the live hooks read the worktrees from it."""
        state = self.projection()
        before = self.effective_repos()
        if state["status"] != "active" or state["stage"] not in self.WORKTREE_STAGES:
            return before
        after = self.ensure_worktrees()
        if after != before:
            self.write_projection()
        return after

    def _next_attempt(self, stage: str) -> int:
        """The lifecycle numbers attempts (a human answer restarts the count)."""
        return int(self.projection()["stage_attempts"].get(stage, 0)) + 1

    @_one_operation
    def stage_complete(self, artifact: Mapping[str, Any] | None = None, *,
                       expected_stage: str | None = None) -> dict[str, Any]:
        from core.run_protocol.documents import DocumentError
        state = self.projection()
        if state["status"] != "active":
            raise EngineError(f"run is {state['status']}")
        stage, profile = state["stage"], state["profile"]
        if expected_stage is not None and stage != expected_stage:
            # a completion decided on an earlier state never completes a later stage
            # (r6 §2.3, review 4cfba9d6): checked under the operation lock
            raise EngineError(f"stale: the run is in {stage}, not {expected_stage}")
        for document in ("REQUIREMENTS", "DESIGN_SPEC"):   # the decisions' projection
            self._sync_decision_document(document)
        if stage in validation_report.STAGES and artifact and "reports" in artifact:
            self._record_validation_reports(stage, artifact)
        if stage == "deliver":
            if state.get("pending_landing") is None and profile != "research":
                raise EngineError("DELIVER finishes with `land`; stage-complete is only for "
                                  "the not-landed path")
            return self.finish_not_landed()
        docs = DocumentStore(self.run_dir, on_render_failed=self._render_failed)
        artifact = dict(artifact or {})
        variant = ("lite" if stage == "plan" and profile == "trivial"
                   else "full" if stage in {"evaluate", "plan"} else None)
        roster = {spec.check_id for spec in checks.stage_checks(profile, stage, variant)}
        try:
            if stage == "evaluate":
                artifact = self._with_decisions("REQUIREMENTS", artifact)
                docs.write_requirements(artifact, variant="full", profile=profile,
                                        blocking=roster)
            elif stage == "plan":
                requirements = self._read_json(
                    self.run_dir / "artifacts" / "requirements.json", {})
                artifact = self._with_decisions("DESIGN_SPEC", artifact)
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
                if disposition == "continue" and self._gate0_ended_at_cap():
                    # both rounds ran and the second still did not clear the frame:
                    # the gate stops at its cap rather than passing (K3 review r3)
                    self.record_cap_exceeded("gate0_round", stage="evaluate")
                if disposition in {"awaiting_answer", "needs_round_2"}:
                    self.write_projection()
                    return {"ok": False, "gate0": disposition, "state": self.projection()}
                if disposition in {"framing_rejected", "already_satisfied"}:
                    self.store.append_new(
                        writer=self.authority.writer("cli"), kind="gate0_exhausted", at=_now(),
                        ordinal=NEXT,
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
        self.store.append_new(writer=self.authority.writer("cli"), kind="stage_complete",
                              at=_now(), stage=stage, attempt=result["attempt"], payload=payload)
        self._settle()
        if stage in {"review", "test", "deliver"} and "review" in lifecycle.PROFILE_STAGES[profile]:
            self._write_review_documents()          # its checks and evidence moved on (F20)
        self.write_projection()
        return {"ok": True, **result, "state": self.projection()}

    @_one_operation
    def report_tests(self, artifact: Mapping[str, Any]) -> dict[str, Any]:
        """A TEST validation report outside TEST (S6-1): in DELIVER, after main moved and
        the worktree was synced, the tests are run again on the new tree and reported
        here; LG-1 reads the latest TEST report."""
        state = self.projection()
        if state["status"] != "active" or state["stage"] not in ("test", "deliver"):
            raise EngineError("report-tests is for TEST or DELIVER of an active run")
        self._record_validation_reports("test", artifact)
        self.write_projection()
        return {"ok": True, "state": self.projection()}

    def _record_validation_reports(self, stage: str, artifact: Mapping[str, Any]) -> None:
        """One ``validation_report`` event per expected repo (S6-1): the agent's report,
        bound to the tree the engine computes now for that repo's worktree. Nothing is
        written unless every report is valid and every repo's tree is known."""
        expected = list(self.contract["expected_repos"])
        try:
            reports = validation_report.parse(artifact, expected)
        except validation_report.ReportError as exc:
            raise EngineError(f"validation report: {exc}") from exc
        _roots, code_trees, bases, _existing, _changed = self._current_code(expected)
        unknown = sorted(name for name in expected
                         if not code_trees.get(name) or not bases.get(name))
        if unknown:
            raise EngineError(f"validation report: the code or base of {unknown} is unknown "
                              "(no worktree, or its base cannot be named); nothing recorded")
        for report in reports:
            self.store.append_new(
                writer=self.authority.writer("cli"), kind="validation_report", at=_now(),
                ordinal=NEXT, payload=validation_report.event_payload(
                    stage, report, code_trees[report["repo"]]))

    def _render_failed(self, document: str, exc: Exception) -> None:
        """A Markdown projection failed (K4, F23): one ``render_failed`` event; the
        stage decision is untouched. REPORT is not routed here: DV-1 checks it."""
        from core.run_protocol.store import StoreError
        logger.warning("run %s: %s not rendered: %s", self.run_id, document, exc)
        events = self.store.read_events()
        # one record per document per snapshot (K4 r1): a retry on the same events is
        # the same failure, not a new one
        # the snapshot rendered: its last event other than a render failure itself
        facts = [e for e in events if e.get("kind") != "render_failed"]
        tail = facts[-1]["event_id"] if facts else "none"

        def recorded(chain: list[dict[str, Any]]) -> str | None:
            return "already recorded" if any(
                e.get("kind") == "render_failed"
                and e["payload"].get("document") == document
                and e["payload"].get("source_tail_event_id") == tail for e in chain) else None
        try:
            self.store.append_new(
                writer=self.authority.writer("cli"), kind="render_failed", at=_now(),
                payload={"document": document, "source_tail_event_id": tail,
                         "error": f"{type(exc).__name__}: {exc}"[:300]},
                refuse=recorded)
        except StoreError as again:
            if "already recorded" not in str(again):
                raise

    def _write_review_documents(self) -> None:
        """REVIEW.md and FIXING_SPEC.md from the events, at every point their facts can
        change: each review step, an accepted finding, and TEST / DELIVER completion."""
        from core.run_protocol.infra import find
        orchestrator = review.ReviewOrchestrator(self.store, self.run_dir)
        for document, write in (("REVIEW", orchestrator.write_review),
                                ("FIXING_SPEC", orchestrator.write_fixing_spec)):
            try:
                write()
            except Exception as exc:  # noqa: BLE001 — a projection, never the decision
                if find(exc) is not None:
                    raise
                self._render_failed(document, exc)
        if orchestrator.missing_blobs:      # written, but a fixer's hunk is unreadable
            self._render_failed("FIXING_SPEC", FileNotFoundError(
                "address hunk blob missing: " + ", ".join(orchestrator.missing_blobs)))

    def _consume_token(self, scope: str) -> tuple[bool, str]:
        """Whether this daemon request's hook token covers *scope* on this run and was
        issued to the run's owner (B-class B-b: the daemon redeemed it before running
        the command; nothing in the agent's process or environment counts)."""
        from core.run_protocol import request_ctx
        ok, reason = request_ctx.take_scope(scope, self.run_id)
        if ok and request_ctx.current().session_id != self.owner_session():
            return False, "hook token belongs to another session"
        return ok, reason

    @_one_operation
    def fail_infrastructure(self, error: "InfrastructureError") -> dict[str, Any]:
        """Record the run's infrastructure failure (v13 §A1.2): it ends ``failed``. The
        CLI and the runner call this for the three ``infra`` kinds only."""
        from core.run_protocol.store import StoreError
        try:
            self.store.append_new(
                writer=self.authority.writer("cli"), kind="infrastructure_failed", at=_now(),
                ordinal=NEXT,
                payload={"reason": f"{error.component}: {error}"[:300]},
                refuse=lambda chain: lifecycle.infrastructure_refusal(chain, self.authority))
            recorded = True
        except StoreError as exc:
            if not str(exc).startswith(lifecycle.ALREADY_ENDED):
                raise
            recorded = False
        self._settle(); self.write_projection()
        return {"recorded": recorded, "state": self.projection()}

    @_one_operation
    def record_cap_exceeded(self, cap: str, *, stage: str | None = None,
                            attempt: int | None = None) -> None:
        """metric(cap_exceeded.<cap>): one of the run's count caps was hit (v13 E4/E5,
        conformance F18). The audit reads it: Speed BLOCK, and Token BLOCK when the cap
        bounds model dispatches. Idempotent per (stage, cap, attempt)."""
        from core.run_protocol.store import StoreError
        if cap not in lifecycle.CAPS:
            raise EngineError(f"unknown cap {cap!r}")
        stage, attempt = stage or "run", attempt or 1
        try:
            self.store.append_new(
                writer=self.authority.writer("cli"), kind="metric", at=_now(),
                stage=stage, attempt=attempt,
                payload={"name": f"cap_exceeded.{cap}", "value": 1, "cap": cap},
                refuse=lambda chain: lifecycle.cap_already_recorded(chain, cap, stage, attempt))
        except StoreError as exc:
            if str(exc) != "cap already recorded":
                raise

    @_one_operation
    def finish_not_landed(self) -> dict[str, Any]:
        """The not-landed path (design §A1.2): render the run-directory REPORT, then a
        DELIVER attempt whose only stage check is DV-1, then ``reflecting``. Every
        not_landed outcome (and research's no_code) ends here."""
        from core.run_protocol import audit as audit_protocol
        state = self.projection()
        if state["status"] != "active" or state["stage"] != "deliver" or (
                state.get("pending_landing") is None and state["profile"] != "research"):
            raise EngineError("the not-landed path needs active DELIVER with a not-landed reason")
        cap = lifecycle.REASON_CAPS.get((state.get("pending_landing") or {}).get("reason"))
        if cap is not None:
            self.record_cap_exceeded(cap, stage="deliver")
        value = self.build_audit(through="DELIVER", persist=True,
                                 commits=self.landed_commits() or None)
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

    @_one_operation
    def review_start(self, *, attempt: int = 1) -> dict[str, Any]:
        state = self.projection()
        if state["stage"] != "review" or state["status"] != "active":
            raise EngineError("review-start requires active REVIEW")
        # deterministic checks before any model review (v13 E4, conformance F16): the
        # REVIEW stage checks that read no expert answer must pass first, or no expert
        # is spawned for a change that cannot complete REVIEW anyway
        context = self.check_context()
        failed = {}
        for spec in checks.stage_checks(state["profile"], "review"):
            if spec.document == "review":
                continue                    # judged from the experts' answers
            ok, detail = checks.evaluate(spec.check_id, context)
            if not ok:
                failed[spec.check_id] = detail
        if failed:
            return {"ok": False, "reviewers": [], "handouts": [], "failed": failed,
                    "error": "deterministic REVIEW checks fail; no expert is spawned until "
                             f"they pass: {failed}"}
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
                   if e.get("kind") == "answer" and e.get("stage") == "review"}
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
                    # no usable output twice: RV-1a/1c decide; a third dispatch is the
                    # cap (§A3.2), and hitting it is recorded (F18)
                    self.record_cap_exceeded("dispatch", stage="review",
                                             attempt=int(mine[-1]["attempt"]))
                    continue
                expert_attempt = int(mine[-1]["attempt"]) + 1
            handouts.append(orchestrator.handout(
                expert_id=item["id"], stage="review", round=1, attempt=expert_attempt,
                at=_now(), expert_instructions=instructions,
                review_input=review_input))
        if not handouts:
            self._write_review_documents()
        return {"reviewers": roster, "handouts": handouts}

    def _audit_facts(self) -> dict[str, Any]:
        """Measured inputs the audit cannot read from events (§E9.3, D5): how many repos
        really got a run worktree (recorded at creation, in the audit area) and the
        retained, unintegrated work."""
        made = self._read_json(self.store.paths.run / "integration_branches.json", {}) or {}
        isolated = (len([n for n in self.contract["expected_repos"] if n in made])
                    if self.contract["mode"] == "isolated" else 0)
        manual = None          # a person's landing: from the signed chain, every rebuild (D5)
        rows = self.store.read_events()
        consumed = [e["payload"] for e in rows if e.get("kind") == "auth_consumed"
                    and str(e.get("payload", {}).get("action", "")).startswith("land")]
        if consumed:
            request_id = consumed[-1].get("request_id")
            requests = [e["payload"] for e in rows if e.get("kind") == "land_request"
                        and e.get("payload", {}).get("request_id") == request_id]
            note = ((requests[-1].get("request") or {}).get("note") if requests else None)
            manual = {"request_id": request_id, "note": note}
        design = self._read_json(self.run_dir / "artifacts" / "design_spec.json", None)
        if isinstance(design, Mapping):     # the decisions are part of the design (K3)
            design = self._with_decisions("DESIGN_SPEC", design)
        return {"repos_isolated": isolated, "manual": manual, "title": self._run_title(),
                "design": design if isinstance(design, Mapping) else None,
                "retained": [{"repo_name": row.get("repo_name"), "ledger": row.get("ledger")}
                             for row in self._retention_entries()
                             if isinstance(row, Mapping) and row.get("ledger")]}

    def _run_title(self) -> str:
        """The run's title (§B8, L6): the first line of what was asked — the user's own
        words for direct, else the requirements' understanding — or the run id."""
        requirements = self._read_json(self.run_dir / "artifacts" / "requirements.json", {})
        claim = ((requirements.get("understanding") or {}).get("claim")
                 if isinstance(requirements, Mapping) else None)
        for text in (self.contract.get("requirement"), claim):
            if isinstance(text, str) and text.strip():
                line = text.strip().splitlines()[0].strip()
                return line if len(line) <= 80 else line[:79] + "…"
        return self.run_id

    def _delivered_summary(self) -> str | None:
        """What the run delivered, for the report's conclusion (D5): the user's words for
        direct, else the validated REQUIREMENTS understanding (or its first story)."""
        if self.contract.get("requirement"):
            return self.contract["requirement"]
        value = self._read_json(self.run_dir / "artifacts" / "requirements.json", {}) or {}
        claim = (value.get("understanding") or {}).get("claim") if isinstance(value, Mapping) else None
        if isinstance(claim, str) and claim.strip():
            return claim
        stories = [r.get("story") for r in (value.get("requirements") or [])
                   if isinstance(r, Mapping)] if isinstance(value, Mapping) else []
        return stories[0] if stories and isinstance(stories[0], str) else None

    def build_audit(self, *, through: str, commits: Mapping[str, str] | None = None,
                    persist: bool = True,
                    event_rows: Sequence[Mapping[str, Any]] | None = None,
                    push: Mapping[str, Any] | None = None) -> dict[str, Any]:
        from core.run_protocol import audit as audit_protocol
        from core.run_protocol import findings as finding_protocol
        rows = list(event_rows) if event_rows is not None else self.store.read_events()
        projected_findings = finding_protocol.fold(rows, self.authority)
        value = audit_protocol.build(
            **self._audit_facts(),
            run_id=self.run_id, projection=lifecycle.fold(rows, self.authority),
            events=rows, final={}, findings=projected_findings,
            contract={**self.contract, "requirement": self._delivered_summary()},
            push=dict(push or self.push_status()), audit_through=through,
            commits=commits)
        if persist:
            audit_protocol.write(self.run_dir / "run_audit.json", value)
        return value

    def push_status(self, *, manual_landing: bool = False) -> dict[str, Any]:
        import dataclasses
        # a run a person landed stays a manual landing on every later recompute
        manual_landing = manual_landing or self.projection().get("landing") == "human_override"
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
            except InfrastructureError:
                raise
            except Exception as exc:
                results[spec.check_id] = (False, f"{type(exc).__name__}: {exc}")
        failed = {key: detail for key, (passed, detail) in results.items() if not passed}
        return {"push_ready": not failed, "reasons": failed, "checks": results}

    @_one_operation
    def prepare_reflect(self, lessons: Sequence[Mapping[str, Any]],
                        candidates: Mapping[str, Sequence[str]] | None = None) -> dict[str, Any]:
        """Write reflect.json and evaluate RF-1~4 as a DELIVER attempt (design §A1.2).
        RF never blocks landing: the third failure drops the lessons to zero and
        records ``reflect: degraded``; DELIVER continues."""
        from core.run_protocol import reflect
        state = self.projection()
        if state["status"] != "active" or state["stage"] != "deliver":
            raise EngineError("reflect-prepare requires active DELIVER")
        if state.get("reflect") == "degraded":
            return {"lessons": [], "degraded": True}
        from core.run_protocol import reflect_job
        event_ids = {event["event_id"] for event in self.store.read_events()}
        if candidates is None:      # the CLI's own recall (RF-4): the agent cannot pick them
            candidates = reflect_job.candidates_for(list(lessons), self.project)
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

    @_one_operation
    def note_transcript(self, sid: str) -> None:
        """Remember an SDK transcript that worked on this run (audit area; D3)."""
        from core.session_ids import MAX_TRANSCRIPT_IDS, valid_session_id
        if not valid_session_id(sid) or sid in self.contract.get("transcript_ids", []):
            return
        path = self.store.paths.run / "transcripts.json"
        known = self._read_json(path, []) or []
        if sid not in known and len(known) < MAX_TRANSCRIPT_IDS:
            _atomic_json(path, [*known, sid])

    def transcript_ids(self) -> list[str]:
        extra = self._read_json(self.store.paths.run / "transcripts.json", []) or []
        return list(dict.fromkeys([*self.contract.get("transcript_ids", []), *extra]))

    def _record_run_usage(self) -> None:
        """The run's token usage, measured by the daemon when REFLECT ends (r6 §2.3):
        the agent never calls a measuring command. No transcript, or none in the run's
        window, is recorded as ``metric(usage_not_available)`` with the reason, and
        Token Cost reads WARN for it instead of BLOCK."""
        from core.run_protocol import usage
        try:
            usage.measure(self.store, transcript_ids=self.transcript_ids(),
                          created_at=self.contract["created_at"])
        except usage.UsageError as exc:
            events = self.store.read_events()
            attempt = 1 + sum(e.get("kind") == "metric"
                              and e["payload"].get("name") == "usage_not_available"
                              for e in events)
            self.store.append_new(
                writer=self.authority.writer("cli"), kind="metric", at=_now(),
                stage="reflect", attempt=attempt,
                payload={"name": "usage_not_available", "value": 1,
                         "reason": str(exc)[:300]})

    @_one_operation
    def measure_usage(self) -> dict[str, Any]:
        from core.run_protocol import usage
        value = usage.measure(self.store, transcript_ids=self.transcript_ids(),
                              created_at=self.contract["created_at"])
        if self.projection().get("status") == "completed":
            self._refresh_completed_audit()      # REFLECT may have finished first (D3/D5)
        return value

    def _resume_normal_landing(self, *, message: str) -> dict[str, Any] | None:
        from core.run_protocol import landing
        path = self.run_dir / "artifacts" / "landing-progress-normal.json"
        progress = self._read_json(path, None)
        if not isinstance(progress, Mapping):
            return None
        if not progress.get("commits") and not landing.adoptable(
                self.contract["repo_roots"], progress.get("plan") or {}, message):
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
        commits, partial = self._commit_normal(bases=bases, bindings=bindings, message=message,
                                               attempt=attempt["attempt"],
                                               attempt_event_id=attempt["event_id"])
        if partial is not None:
            return partial          # partial_land: the not-landed path's result
        render_event = next((event for event in reversed(self.store.read_events())
                             if event.get("event_id") == rendered.get("event_id")), None)
        if render_event is not None:
            self._copy_report(render_event["payload"]["blob_sha"])
        self._settle(); self.write_projection()
        return {"commits": commits, "push": self.push_status(), "state": self.projection(),
                "resumed": True}

    def _sync_moved_bases(self) -> None:
        """Main moved past a repo's run base: rebase that worktree onto it (S6-1). The
        code tree then changes, so LG-1 asks for a TEST report on the new tree
        (``report-tests``). A sync that changed the run's own patch (a conflict
        resolution) is new code no review saw: LG-4 fails with ``patch_changed``."""
        if self.contract["mode"] == "shared":
            return                  # a shared run's code is the main checkout itself
        for entry in self._active_entries():
            name = entry["repo_name"]
            main = self.contract["repo_roots"][name]
            root = self.effective_repos().get(name)
            if root is None:
                continue
            if run_worktree._rebase_onto(Path(root)):
                # a sync stopped on a conflict earlier: resolve it in the worktree, then
                # land again (sync continues the rebase); the code is mid-rebase till then
                entry = {**entry, "base": self._fork_base(name, root)}
            else:
                base, head = self._fork_base(name, root), _git(main, "rev-parse", "HEAD")
                if base == head:
                    continue
                entry = {**entry, "base": base}
            try:
                sync = run_worktree.sync(self.run_id, entry, main)
            except run_worktree.WorktreeError as exc:
                code = "merge_conflict" if exc.code == "conflict" else "sync_failed"
                raise IntegrationRequired(code, f"{name}: base sync failed: {exc.message}") from exc
            self._code_facts, self._bases = None, (None,)   # the tree and base moved
            if sync.get("patch_changed"):
                raise IntegrationRequired(
                    "patch_changed", f"{name}: rebasing onto the moved main changed the run's "
                    "own patch; that code was not reviewed")

    def _write_carry_plan(self, name: str, *, old_base: str, new_base: str, code_tree: str,
                          affected: Sequence[str]) -> None:
        from core.run_protocol.runner import carry_plan_path
        path = carry_plan_path(self.data_dir, self.run_id)
        current = self._read_json(path, {}) or {}
        _atomic_json(path, {**current, name: {"old_base": old_base, "new_base": new_base,
                                              "code_tree": code_tree,
                                              "affected_tests": sorted(affected)}})

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

    @_one_operation
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
        from core.run_protocol.landing import LandingError, check_message
        message = check_message(message)    # each repo's trailer is added by the landing
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
        ok, why = self._consume_token("land")
        if not ok:
            # a landing needs the session's hook (fixing spec A7, design §C5.1): a
            # hookless executor's work is kept and the run ends not_landed
            logger.info("v1 land of %s without a hook token: %s", self.run_id, why)
            self.store.append_new(
                writer=self.authority.writer("cli"), kind="land", at=_now(),
                stage="deliver", attempt=self._next_attempt("deliver"),
                ordinal=NEXT,
                payload={"landing": "not_landed", "commit": None,
                         "reason": "needs_hooked_session"})
            self._settle(); self.write_projection()
            finished = self.finish_not_landed()
            return {**finished, "ok": False, "landing": "not_landed",
                    "reason": "needs_hooked_session", "detail": why}
        attempt = self._next_attempt("land")
        guard_ids = ["DV-1", "LG-1", "LG-3", "LG-4"]
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
                self._sync_moved_bases()
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
            record("LG-1", *checks.evaluate("LG-1", context))
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
                audit_value, guards=("LG-1", "LG-3", "LG-4", "DV-1"))
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
            bases = {name: context.run["run_bases"][name]
                     for name in self.contract["expected_repos"]}
            try:
                commits, partial = self._commit_normal(bases=bases, bindings=bindings,
                                                       message=message, attempt=attempt,
                                                       attempt_event_id=opened["event_id"])
                if partial is not None:
                    return partial      # partial_land: the not-landed path's result
            except LandingError as exc:
                # nothing moved (phase 1 refused); the attempt is closed, the next land
                # supersedes it
                self._discard_empty_progress()
                self.write_projection()
                return {"ok": False, "retry": True, "error": f"commit failed: {exc}",
                        "state": self.projection()}
        except Exception as exc:
            close(f"{type(exc).__name__}: {exc}"[:300])
            self._settle(); self.write_projection()
            raise
        self._copy_report(text=report.decode("utf-8"))
        self._settle(); self.write_projection()
        return {"ok": True, "commits": commits, "push": push, "state": self.projection()}

    def _commit_normal(self, *, bases: Mapping[str, str], bindings: Mapping[str, Any],
                       message: str, attempt: int, attempt_event_id: str
                       ) -> tuple[dict[str, str], dict[str, Any] | None]:
        """The one place a normal landing writes main (fresh and resumed): (repo ->
        commit, None), or — when the fast-forwards raced another session after every repo
        passed phase 1 — a signed ``partial_land`` with the commits that DID land and
        the not-landed path's result as the second item (§A3.7, conformance K2).
        Never a silent retry."""
        from core.run_protocol import landing
        try:
            commits = landing.commit_repos(
                run_id=self.run_id, integration_roots=self.contract["repo_roots"],
                bases=bases, bindings=bindings, message=message,
                branches=self._recorded_branches(),
                in_place=self.contract.get("mode") == "shared",
                progress_path=self.run_dir / "artifacts" / "landing-progress-normal.json")
        except landing.PartialLanding as exc:
            self.store.append_new(
                writer=self.authority.writer("cli"), kind="partial_land", at=_now(),
                stage="deliver", attempt=attempt, ordinal=NEXT,
                payload={"landed": dict(sorted(exc.landed.items())), "failed": exc.failed,
                         "detail": str(exc)[:300], "stage_attempt_event_id": attempt_event_id,
                         **({"unsettled": list(exc.unsettled)} if exc.unsettled else {})})
            self._settle()
            finished = self.finish_not_landed()
            return dict(exc.landed), {**finished, "ok": False, "landing": "not_landed",
                                      "reason": "partial_land", "landed": exc.landed}
        # the signed land event comes RIGHT after the refs moved, before any copy or
        # projection that could fail (K5 review r3); a failure of this append itself is
        # recovered by the resume path, which adopts the recorded commits
        self.store.append_new(
            writer=self.authority.writer("cli"), kind="land", at=_now(),
            stage="deliver", attempt=attempt, ordinal=NEXT,
            payload={"landing": "landed", "commit": next(iter(commits.values())),
                     "commits": dict(sorted(commits.items())),
                     **({"unsettled": list(commits.unsettled)} if commits.unsettled else {}),
                     "stage_attempt_event_id": attempt_event_id})
        return commits, None

    def _copy_report(self, blob_sha: str | None = None, *, text: str | None = None) -> None:
        """The run directory's REPORT.md: a projection of the signed render blob,
        written after the land event; a failure here never undoes a landing."""
        try:
            if text is None:
                text = self.store.read_blob(str(blob_sha)).decode("utf-8")
            DocumentStore._atomic(self.run_dir / "REPORT.md", text)
        except OSError:
            import logging
            logging.getLogger(__name__).warning("run %s: REPORT.md copy not written",
                                                self.run_id, exc_info=True)

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

    @_one_operation
    def reflect_complete(self, *, degraded: bool = False, reason: str = "") -> dict[str, Any]:
        state = self.projection()
        if state["status"] != "reflecting":
            raise EngineError("reflect completion requires reflecting")
        kind = "reflect_exhausted" if degraded else "reflect_complete"
        payload = {"reason": reason or "background reflection exhausted"} if degraded else {}
        self._record_run_usage()
        self.store.append_new(writer=self.authority.writer("hook"), kind=kind, at=_now(),
                              ordinal=NEXT, payload=payload)
        self._settle()
        self._refresh_completed_audit()
        return self.projection()

    def landed_commits(self) -> dict[str, str]:
        """repo -> the commit on main, from SIGNED events (review r3): the latest
        landing that finished (``landed`` / ``human_override``) when it is later than
        any partial_land, else the latest partial_land. A finished land event without
        ``commits`` yields its signed ``commit``."""
        events = self.store.read_events()
        finished = [(i, e) for i, e in enumerate(events) if e.get("kind") == "land"
                    and e["payload"].get("landing") in ("landed", "human_override")]
        partials = [(i, e) for i, e in enumerate(events) if e.get("kind") == "partial_land"]
        if partials and (not finished or partials[-1][0] > finished[-1][0]):
            return dict(partials[-1][1]["payload"]["landed"])
        if not finished:
            return {}
        payload = finished[-1][1]["payload"]
        if isinstance(payload.get("commits"), dict) and payload["commits"]:
            return dict(payload["commits"])
        # signed fields only: the agent-writable landing-progress files are never read
        # here (re-audit c772dd92 HIGH; old runs without ``commits`` are not supported)
        return {"primary": payload["commit"]} if payload.get("commit") else {}

    def _partially_landed(self) -> dict[str, str]:
        """repo -> commit of a signed partial_land later than every finished landing:
        a person's run-land keeps those and lands the rest (review K5 r1)."""
        events = self.store.read_events()
        last_finished = max((i for i, e in enumerate(events) if e.get("kind") == "land"
                             and e["payload"].get("landing") in ("landed", "human_override")),
                            default=-1)
        partial = next((e for i, e in reversed(list(enumerate(events)))
                        if e.get("kind") == "partial_land" and i > last_finished), None)
        return dict(partial["payload"]["landed"]) if partial else {}

    def _recorded_branches(self) -> dict[str, str]:
        if self.contract.get("mode") == "shared":
            return {}               # the shared checkout's own branch, read at landing
        value = self._read_json(self.store.paths.run / "integration_branches.json", {}) or {}
        return {str(k): str(v) for k, v in value.items()}

    def _refresh_completed_audit(self) -> None:
        """The run-directory audit, REPORT and card through COMPLETED (§B8, §E9.5): at
        REFLECT's end, and again when a person lands a completed(not_landed) run (D5)."""
        commits = self.landed_commits()
        value = self.build_audit(through="COMPLETED", commits=commits, persist=True)
        from core.run_protocol import audit as audit_protocol
        DocumentStore._atomic(self.run_dir / "REPORT.md",
                              audit_protocol.render(value, fmt="report"))
        self.write_projection()
