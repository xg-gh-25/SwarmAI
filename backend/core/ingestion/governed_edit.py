"""Journaled user editing for governed corpus files and project trees.

- ``revision_of`` provides optimistic-concurrency revisions.
- ``apply_user_edit`` archives and applies one baseline-revising file edit.
- ``apply_user_tree_move`` journals a governed project rename or trash move.
- ``forbid_dir_op`` rejects unsafe directory-level corpus mutations.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from config import get_app_data_dir, get_swarmws
from core.memory_guard import MemoryGuard
from utils.file_lock import md_lock

from .api import StaleRevision
from .materializer import get_materializer, is_governed
from .writer import (
    _advance,
    _claim_step,
    _execute_plan,
    _hash,
    _iso,
    _load_plan,
    _read_baseline,
    _relative,
    _write_baseline,
    baseline_path,
    ensure_baseline,
)

logger = logging.getLogger(__name__)


def revision_of(path: Path | str) -> str:
    """Return the SHA-256 revision of current bytes, or ``""`` when absent."""
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except FileNotFoundError:
        return ""


def _connection() -> sqlite3.Connection:
    from .api import connect_rw

    connection = connect_rw()
    connection.row_factory = sqlite3.Row
    return connection


def _archive_path(path: Path, op_id: str) -> Path:
    digest = revision_of(path)[:12] or "empty"
    return path.parent / ".archive" / f"{path.name}.{op_id.split(':')[-1]}.{digest}"


def _sanitize(path: Path, data: bytes | None) -> bytes | None:
    if data is None:
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        if is_governed(path):
            raise ValueError("governed corpus files must be UTF-8")
        return data
    return MemoryGuard().sanitize(text).encode("utf-8")


def _baseline_anchors(path: Path) -> list[str]:
    baseline = ensure_baseline()
    relative = _relative(path)
    entry = baseline.get("files", {}).get(relative, {})
    return [f"{relative}#{unit.get('section', '')}#{unit.get('identity', unit.get('byte_hash', ''))}"
            for unit in entry.get("units", [])]


def _insert_user_plan(conn: sqlite3.Connection, *, op_id: str, path: Path,
                      before_hash: str, new_bytes: bytes | None, archive: Path | None,
                      baseline_patch: dict[str, Any], actor: str) -> dict[str, Any]:
    now = _iso()
    intended_hash = _hash(new_bytes or b"")
    owner = conn.execute("SELECT epoch FROM ingestion_owner WHERE id=1").fetchone()
    epoch = int(owner[0]) if owner else 0
    conn.execute(
        """INSERT INTO ingestion_writes
        (op_id,op_kind,unit_id,target_path,before_hash,intended_bytes,after_hash,
         archive_path,archive_hash,anchors_present,anchors_absent,relations,
         baseline_patch,tree_manifest,step,step_owner,owner_epoch,created_at,updated_at)
        VALUES (?,'user_edit',NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (op_id, _relative(path), before_hash, new_bytes, intended_hash,
         _relative(archive) if archive else None, before_hash if archive else None,
         "[]", "[]", "[]", json.dumps(baseline_patch, ensure_ascii=False, sort_keys=True),
         None, "planned", f"user:{actor}", epoch, now, now),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM ingestion_writes WHERE op_id=?", (op_id,)).fetchone()
    return _load_plan(row)


def apply_user_edit(path: Path, new_bytes: bytes | None, *, expected_revision: str,
                    actor: str = "user") -> str:
    """Apply one optimistic, archive-first governed edit and revise the baseline."""
    target = Path(path).expanduser().resolve(strict=False)
    if not is_governed(target):
        raise PermissionError("path is not a governed corpus file")
    target.parent.mkdir(parents=True, exist_ok=True)
    new_bytes = _sanitize(target, new_bytes)
    conn = _connection()
    try:
        ensure_baseline(conn=conn)
        with md_lock(target, blocking=True):
            before = revision_of(target)
            if before != expected_revision:
                raise StaleRevision(f"expected {expected_revision}, found {before}")
            op_id = f"user-edit:{uuid.uuid4().hex}"
            archive = _archive_path(target, op_id) if target.is_file() else None
            patch = {
                "target_path": _relative(target),
                "delete": new_bytes is None,
                "invalidate_anchors": _baseline_anchors(target),
                "actor": actor,
            }
            plan = _insert_user_plan(
                conn, op_id=op_id, path=target, before_hash=before,
                new_bytes=new_bytes, archive=archive, baseline_patch=patch, actor=actor,
            )
        result = _execute_plan(conn, plan)
        if result != "done":
            if result == "deferred":
                raise StaleRevision("file changed while the edit was being applied")
            raise RuntimeError(f"user edit did not complete: {result}")
        # The edit is committed; re-queueing parked units is best effort and must never
        # turn a successful save into an error (the maintenance scan retries it).
        if new_bytes is not None:
            try:
                if "Knowledge/Learned/domains" in target.as_posix():
                    from .api_v2 import requeue_parked
                    from .contracts_v2 import slugify
                    requeue_parked("unknown_domain", slugify(target.stem))
                from .pipeline_v2 import requeue_missing_sections
                requeue_missing_sections(get_swarmws(), target)
            except Exception as exc:
                logger.warning("CDF requeue after user edit failed (maintenance will retry): %s", exc)
        return revision_of(target)
    finally:
        conn.close()


def _tree_files(root: Path) -> list[dict[str, str]]:
    files: list[dict[str, str]] = []
    if not root.is_dir():
        return files
    for item in sorted(root.rglob("*")):
        if item.is_file() and is_governed(item):
            files.append({"path": item.relative_to(root).as_posix(), "sha256": revision_of(item)})
    return files


def _manifest_digest(files: list[dict[str, str]]) -> str:
    encoded = json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def tree_revision_of(root: Path | str) -> str:
    """Manifest digest of every governed descendant of *root* (the tree's revision)."""
    return _manifest_digest(_tree_files(Path(root).expanduser().resolve(strict=False)))


def move_tree(src: Path, dst: Path, *, actor: str = "user") -> None:
    """Project rename / trash move. Journaled when the tree holds governed files,
    a plain rename otherwise (nothing governed to protect)."""
    source = Path(src).expanduser().resolve(strict=False)
    if _tree_files(source):
        apply_user_tree_move(source, dst, expected_manifest=tree_revision_of(source), actor=actor)
    else:
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        source.rename(dst)


def _tree_destination(src: Path, dst: Path | None, op_id: str) -> Path:
    if dst is not None:
        return Path(dst).expanduser().resolve(strict=False)
    trash = Path(get_app_data_dir()) / "state" / "governed-trash"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return trash / f"{src.name}.{stamp}.{op_id[-12:]}"


def _insert_tree_plan(conn: sqlite3.Connection, *, op_id: str, src: Path, dst: Path,
                      files: list[dict[str, str]], actor: str) -> dict[str, Any]:
    now = _iso()
    owner = conn.execute("SELECT epoch FROM ingestion_owner WHERE id=1").fetchone()
    epoch = int(owner[0]) if owner else 0
    digest = _manifest_digest(files)
    manifest = {"src": str(src), "dst": str(dst), "files": files, "actor": actor}
    conn.execute(
        """INSERT INTO ingestion_writes
        (op_id,op_kind,unit_id,target_path,before_hash,intended_bytes,after_hash,
         archive_path,archive_hash,anchors_present,anchors_absent,relations,
         baseline_patch,tree_manifest,step,step_owner,owner_epoch,created_at,updated_at)
        VALUES (?,'user_tree_move',NULL,?,?,NULL,?,NULL,NULL,'[]','[]','[]',NULL,?,'planned',?,?,?,?)""",
        (op_id, _relative(src), digest, digest, json.dumps(manifest, sort_keys=True),
         f"user:{actor}", epoch, now, now),
    )
    conn.commit()
    return _load_plan(conn.execute("SELECT * FROM ingestion_writes WHERE op_id=?", (op_id,)).fetchone())


def _tree_postcondition(manifest: Mapping[str, Any]) -> bool:
    src, dst = Path(str(manifest["src"])), Path(str(manifest["dst"]))
    # Compare the manifest's own files, not is_governed(dst): a trash destination is
    # deliberately outside the governed tree.
    return not src.exists() and dst.is_dir() and all(
        revision_of(dst / item["path"]) == item["sha256"] for item in manifest["files"])


def _tree_precondition(manifest: Mapping[str, Any]) -> bool:
    src = Path(str(manifest["src"]))
    return src.is_dir() and _tree_files(src) == list(manifest["files"])


def _move_baseline(manifest: Mapping[str, Any], conn: sqlite3.Connection) -> None:
    path = baseline_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with md_lock(path, blocking=True):
        baseline = _read_baseline(conn=conn)
        src, dst = Path(str(manifest["src"])), Path(str(manifest["dst"]))
        source_prefix = _relative(src).rstrip("/") + "/"
        destination_prefix = _relative(dst).rstrip("/") + "/"
        for old in [key for key in baseline["files"] if key.startswith(source_prefix)]:
            suffix = old[len(source_prefix):]
            entry = baseline["files"].pop(old)
            if str(manifest.get("actor", "")).startswith("sync:"):
                for unit in entry.get("units", []):
                    unit["trust"] = "external"
            baseline["files"][destination_prefix + suffix] = entry
        _write_baseline(baseline)


def recover_tree_plan(conn: sqlite3.Connection, plan: dict[str, Any]) -> str:
    """Recover or execute one journaled project tree move from its manifest."""
    manifest = plan.get("tree_manifest") or {}
    src, dst = Path(str(manifest["src"])), Path(str(manifest["dst"]))
    epoch = int(plan.get("owner_epoch") or 0)
    lock_path = src.parent / f".{src.name}.cdf-tree"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with md_lock(lock_path, blocking=True):
        if plan["step"] == "planned":
            if not _claim_step(conn, plan["op_id"], "planned", epoch):
                return "fenced"
            _advance(conn, plan["op_id"], "planned", "archive_applied", epoch)
            plan["step"] = "archive_applied"
        if plan["step"] == "archive_applied":
            if not _claim_step(conn, plan["op_id"], "archive_applied", epoch):
                return "fenced"
            if not _tree_postcondition(manifest):
                if not _tree_precondition(manifest):
                    conn.execute("UPDATE ingestion_writes SET step='abandoned',updated_at=? WHERE op_id=?",
                                 (_iso(), plan["op_id"]))
                    conn.commit()
                    return "abandoned"
                get_materializer().move(src, dst, op_id=plan["op_id"])
            if not _tree_postcondition(manifest):
                raise OSError("tree move postcondition failed")
            _advance(conn, plan["op_id"], "archive_applied", "target_applied", epoch)
            plan["step"] = "target_applied"
        if plan["step"] == "target_applied":
            if not _claim_step(conn, plan["op_id"], "target_applied", epoch):
                return "fenced"
            _advance(conn, plan["op_id"], "target_applied", "relations_applied", epoch)
            plan["step"] = "relations_applied"
        if plan["step"] == "relations_applied":
            if not _claim_step(conn, plan["op_id"], "relations_applied", epoch):
                return "fenced"
            _move_baseline(manifest, conn)
            _advance(conn, plan["op_id"], "relations_applied", "baseline_applied", epoch)
            plan["step"] = "baseline_applied"
        if plan["step"] == "baseline_applied":
            if not _claim_step(conn, plan["op_id"], "baseline_applied", epoch):
                return "fenced"
            _advance(conn, plan["op_id"], "baseline_applied", "done", epoch)
            plan["step"] = "done"
    return str(plan["step"])


def apply_user_tree_move(src: Path, dst: Path | None, *, expected_manifest: str,
                         actor: str = "user") -> None:
    """Journal and apply a project rename or recoverable trash move."""
    source = Path(src).expanduser().resolve(strict=False)
    files = _tree_files(source)
    if not files:
        raise PermissionError("project tree contains no governed descendants")
    actual = _manifest_digest(files)
    if actual != expected_manifest:
        raise StaleRevision(f"expected manifest {expected_manifest}, found {actual}")
    op_id = f"user-tree:{uuid.uuid4().hex}"
    destination = _tree_destination(source, dst, op_id)
    conn = _connection()
    try:
        ensure_baseline(conn=conn)
        plan = _insert_tree_plan(conn, op_id=op_id, src=source, dst=destination,
                                 files=files, actor=actor)
        result = recover_tree_plan(conn, plan)
        if result != "done":
            raise StaleRevision(f"project tree changed while moving: {result}")
    finally:
        conn.close()


def forbid_dir_op(path: Path) -> None:
    """Raise when a directory-level operation would include governed descendants."""
    directory = Path(path).expanduser().resolve(strict=False)
    if not directory.is_dir():
        return
    if any(item.is_file() and is_governed(item) for item in directory.rglob("*")):
        raise PermissionError(
            "directory contains governed corpus files; edit files individually or use a project tree move"
        )


def _redacted_path(anchor: str) -> Path:
    digest = hashlib.sha256(anchor.encode("utf-8")).hexdigest()
    return Path(get_app_data_dir()) / "state" / "redacted" / f"{digest}.bin"


def _redaction_span(target: Path, anchor: str) -> tuple[int, int, str]:
    from .units import parse_units

    unit = next((item for item in parse_units(target, root=Path(target).parents[0])
                 if item.identity == anchor.rsplit("#", 1)[-1]), None)
    if unit is None:
        # Normal workspace-relative anchors compare directly when a root is supplied.
        from config import get_swarmws
        unit = next((item for item in parse_units(target, root=get_swarmws())
                     if item.anchor == anchor), None)
    if unit is None:
        raise ValueError("redaction anchor does not resolve")
    data = target.read_bytes()
    end = unit.byte_end
    metadata = __import__("re").match(rb"\r?\n[ \t]*<!-- ref:[^\r\n]*-->[ \t]*(?:\r?\n)?", data[end:])
    if metadata:
        end += metadata.end()
    return unit.byte_start, end, unit.identity


def _insert_redact_plan(conn: sqlite3.Connection, *, anchor: str, reason: str,
                        target: Path, vault: Path, original: bytes,
                        intended: bytes, identity: str) -> dict[str, Any]:
    op_id = f"user-redact:{uuid.uuid4().hex}"
    now = _iso()
    owner = conn.execute("SELECT epoch FROM ingestion_owner WHERE id=1").fetchone()
    epoch = int(owner[0]) if owner else 0
    marker = f"redacted:{hashlib.sha256(anchor.encode()).hexdigest()}"
    patch = {"target_path": _relative(target), "delete": False,
             "invalidate_anchors": [anchor], "actor": "user"}
    relation_payload = {"redact_anchor": anchor, "reason": reason,
                        "vault_path": str(vault)}
    conn.execute(
        """INSERT INTO ingestion_writes
        (op_id,op_kind,unit_id,target_path,before_hash,intended_bytes,after_hash,
         archive_path,archive_hash,anchors_present,anchors_absent,relations,
         baseline_patch,step,step_owner,owner_epoch,created_at,updated_at)
        VALUES (?,'user_redact',NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (op_id, _relative(target), revision_of(target), intended, _hash(intended),
         str(vault), _hash(original), json.dumps([marker]),
         json.dumps([original.decode("utf-8")]), json.dumps([relation_payload]),
         json.dumps(patch), "planned", "user:redact", epoch, now, now),
    )
    conn.commit()
    return _load_plan(conn.execute("SELECT * FROM ingestion_writes WHERE op_id=?", (op_id,)).fetchone())


def recover_redact_plan(conn: sqlite3.Connection, plan: dict[str, Any]) -> str:
    """Idempotently finish one user-redact journal plan."""
    from .writer import _apply_baseline_patch, _atomic_state_write, _path_hash

    target = Path(get_swarmws()) / str(plan["target_path"])
    vault = Path(str(plan["archive_path"]))
    epoch = int(plan.get("owner_epoch") or 0)
    payload = (plan.get("relations") or [{}])[0]
    anchor = str(payload.get("redact_anchor") or "")
    with md_lock(target, blocking=True):
        if plan["step"] == "planned":
            if not vault.is_file() or _path_hash(vault) != plan["archive_hash"]:
                original = str((plan.get("anchors_absent") or [""])[0]).encode("utf-8")
                _atomic_state_write(vault, original)
                vault.chmod(0o600)
            _advance(conn, plan["op_id"], "planned", "archive_applied", epoch)
            plan["step"] = "archive_applied"
        if plan["step"] == "archive_applied":
            if revision_of(target) != plan["after_hash"]:
                if revision_of(target) != plan["before_hash"]:
                    raise StaleRevision("file changed while redaction was being applied")
                get_materializer().write_bytes(target, bytes(plan["intended_bytes"]), op_id=plan["op_id"])
            _advance(conn, plan["op_id"], "archive_applied", "target_applied", epoch)
            plan["step"] = "target_applied"
        if plan["step"] == "target_applied":
            if _table_exists_for_redaction(conn, "claim_support"):
                conn.execute("UPDATE claim_support SET inactive=1 WHERE claim_anchor=?", (anchor,))
            conn.execute("UPDATE ingestion_relations SET inactive=1 WHERE subject_anchor=? OR object_anchor=?",
                         (anchor, anchor))
            if _table_exists_for_redaction(conn, "redactions"):
                conn.execute(
                    "INSERT OR REPLACE INTO redactions(anchor,redacted_at,reason,vault_path) VALUES(?,?,?,?)",
                    (anchor, _iso(), str(payload.get("reason") or ""), str(vault)),
                )
            conn.commit()
            _advance(conn, plan["op_id"], "target_applied", "relations_applied", epoch)
            plan["step"] = "relations_applied"
        if plan["step"] == "relations_applied":
            _apply_baseline_patch(plan["baseline_patch"], conn=conn)
            _advance(conn, plan["op_id"], "relations_applied", "baseline_applied", epoch)
            plan["step"] = "baseline_applied"
        if plan["step"] == "baseline_applied":
            _advance(conn, plan["op_id"], "baseline_applied", "done", epoch)
            plan["step"] = "done"
    if str(plan["target_path"]).startswith("Knowledge/"):
        from core.knowledge_store import sync_knowledge_file
        sync_knowledge_file(target)
    return str(plan["step"])


def _table_exists_for_redaction(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (table,)).fetchone() is not None


def redact(anchor: str, reason: str, *, actor: str) -> str:
    """Replace one resolved unit with a tombstone; callable only as actor ``user``."""
    if actor != "user":
        raise PermissionError("redaction is user-only")
    reason = reason.strip()
    if not reason or len(reason) > 500 or any(ord(char) < 32 and char not in "\t" for char in reason):
        raise ValueError("redaction reason must be 1-500 printable characters")
    try:
        path_value, _section, identity = anchor.rsplit("#", 2)
    except ValueError as exc:
        raise ValueError("invalid redaction anchor") from exc
    target = (Path(get_swarmws()) / path_value).resolve(strict=False)
    workspace = Path(get_swarmws()).resolve(strict=False)
    if not target.is_relative_to(workspace) or not is_governed(target):
        raise PermissionError("redaction target is outside governed corpus")
    start, end, parsed_identity = _redaction_span(target, anchor)
    data = target.read_bytes()
    original = data[start:end]
    date = datetime.now(timezone.utc).date().isoformat()
    marker = f"redacted:{hashlib.sha256(anchor.encode()).hexdigest()}"
    tombstone = (f"- [redacted] **Withdrawn** [已撤回 {date}，原因：{reason}，原锚点：{anchor}] "
                 f"<!-- {parsed_identity} | {marker} -->").encode("utf-8")
    intended = data[:start].rstrip(b"\r\n") + b"\n\n" + tombstone + b"\n" + data[end:].lstrip(b"\r\n")
    conn = _connection()
    try:
        plan = _insert_redact_plan(conn, anchor=anchor, reason=reason, target=target,
                                   vault=_redacted_path(anchor), original=original,
                                   intended=intended, identity=identity)
        result = recover_redact_plan(conn, plan)
        if result != "done":
            raise RuntimeError(f"redaction did not complete: {result}")
        return revision_of(target)
    finally:
        conn.close()


def _sync_hash(mapping: Mapping[str, str], path: Path) -> str | None:
    """Resolve a caller hash by absolute or workspace-relative path spelling."""
    relative = _relative(path)
    for key in (str(path), path.as_posix(), relative):
        if key in mapping:
            return str(mapping[key])
    return None


def sync_revision_of(path: Path | str) -> str:
    """Return a file hash, governed-tree manifest hash, or empty missing revision."""
    target = Path(path).expanduser().resolve(strict=False)
    if target.is_dir():
        return tree_revision_of(target)
    return revision_of(target)


def _baseline_tree_files(path: Path, baseline: Mapping[str, Any]) -> list[dict[str, str]]:
    prefix = _relative(path).rstrip("/") + "/"
    return [
        {"path": name[len(prefix):], "sha256": str(entry.get("file_hash", ""))}
        for name, entry in sorted(baseline.get("files", {}).items())
        if name.startswith(prefix)
    ]


def sync_baseline_revision(path: Path | str, baseline: Mapping[str, Any]) -> str:
    """Return the baseline revision for one file or governed project subtree."""
    target = Path(path).expanduser().resolve(strict=False)
    entry = baseline.get("files", {}).get(_relative(target))
    if entry is not None:
        return str(entry.get("file_hash", ""))
    files = _baseline_tree_files(target, baseline)
    return _manifest_digest(files) if files else ""


def _sync_path_allowed(path: Path, workspace: Path, baseline: Mapping[str, Any]) -> bool:
    if not path.is_relative_to(workspace):
        return False
    if is_governed(path):
        return True
    projects = workspace / "Projects"
    if not path.is_relative_to(projects) or path == projects:
        return False
    return bool(_tree_files(path) or _baseline_tree_files(path, baseline))


def apply_sync(paths: list[Path | str], before_hashes: Mapping[str, str],
               after_hashes: Mapping[str, str], origin: str) -> list[str]:
    """Journal an already-completed repository sync and revise its baseline.

    Every supplied hash is checked before any plan is inserted. The pre-sync hash
    must match the baseline and the post-sync hash must match the current bytes;
    this prevents a stale or fabricated sync record from blessing unknown content.
    """
    source = origin.strip()
    if not source or len(source) > 200 or any(ord(char) < 32 for char in source):
        raise ValueError("sync origin must be 1-200 printable characters")
    targets = [Path(value).expanduser().resolve(strict=False) for value in paths]
    if not targets or len(set(targets)) != len(targets):
        raise ValueError("sync paths must be non-empty and unique")
    workspace = Path(get_swarmws()).resolve(strict=False)

    conn = _connection()
    try:
        baseline = ensure_baseline(conn=conn)
        if any(not _sync_path_allowed(target, workspace, baseline) for target in targets):
            raise PermissionError("sync path is outside governed corpus")
        observed: list[tuple[Path, str, str]] = []
        for target in targets:
            relative = _relative(target)
            expected_before = _sync_hash(before_hashes, target)
            expected_after = _sync_hash(after_hashes, target)
            if expected_before is None or expected_after is None:
                raise ValueError(f"missing sync hashes for {relative}")
            recorded_before = sync_baseline_revision(target, baseline)
            actual_after = sync_revision_of(target)
            if expected_before != recorded_before:
                raise StaleRevision(
                    f"sync before hash for {relative} does not match baseline"
                )
            if expected_after != actual_after:
                raise StaleRevision(
                    f"sync after hash for {relative} does not match current content"
                )
            if expected_before != expected_after:
                observed.append((target, expected_before, expected_after))

        used: set[Path] = set()
        tree_moves: list[tuple[Path, Path, str]] = []
        for old_path, before, after in observed:
            if not before or after or not _baseline_tree_files(old_path, baseline):
                continue
            matches = [
                new_path for new_path, new_before, new_after in observed
                if new_before == "" and new_after == before and new_path.is_dir()
            ]
            if len(matches) != 1:
                raise StaleRevision("sync directory move is missing a unique destination")
            destination = matches[0]
            used.update((old_path, destination))
            tree_moves.append((old_path, destination, before))

        checked: list[tuple[Path, str, str, bytes | None, list[str]]] = []
        for target, before, after in observed:
            if target in used:
                continue
            if target.is_dir() or _baseline_tree_files(target, baseline):
                raise ValueError("changed sync directory is not a complete move pair")
            relative = _relative(target)
            data = target.read_bytes() if target.is_file() else None
            anchors = [
                f"{relative}#{unit.get('section', '')}#"
                f"{unit.get('identity', unit.get('byte_hash', ''))}"
                for unit in baseline.get("files", {}).get(relative, {}).get("units", [])
            ]
            checked.append((target, before, after, data, anchors))

        completed: list[str] = []
        for old_path, destination, digest in tree_moves:
            op_id = f"sync-tree:{uuid.uuid4().hex}"
            plan = _insert_tree_plan(
                conn, op_id=op_id, src=old_path, dst=destination,
                files=_tree_files(destination), actor=f"sync:{source}",
            )
            result = recover_tree_plan(conn, plan)
            if result != "done":
                raise RuntimeError(f"sync tree record did not complete: {result}")
            conn.execute(
                "INSERT INTO ingestion_events(at,kind,detail) VALUES(?,?,?)",
                (_iso(), "sync_applied", json.dumps({
                    "op_id": op_id, "target_path": _relative(destination),
                    "before_hash": digest, "after_hash": digest,
                    "writer": f"sync:{source}", "moved_from": _relative(old_path),
                }, ensure_ascii=False, sort_keys=True)),
            )
            conn.commit()
            completed.append(op_id)

        for target, before, after, data, anchors in checked:
            op_id = f"sync:{uuid.uuid4().hex}"
            patch = {
                "target_path": _relative(target),
                "delete": data is None,
                "invalidate_anchors": anchors,
                "actor": f"sync:{source}",
                "trust": "external",
            }
            plan = _insert_user_plan(
                conn, op_id=op_id, path=target, before_hash=before,
                new_bytes=data, archive=None, baseline_patch=patch,
                actor=f"sync:{source}",
            )
            result = _execute_plan(conn, plan)
            if result != "done":
                raise RuntimeError(f"sync record did not complete: {result}")
            conn.execute(
                "INSERT INTO ingestion_events(at,kind,detail) VALUES(?,?,?)",
                (_iso(), "sync_applied", json.dumps({
                    "op_id": op_id,
                    "target_path": _relative(target),
                    "before_hash": before,
                    "after_hash": after,
                    "writer": f"sync:{source}",
                }, ensure_ascii=False, sort_keys=True)),
            )
            conn.commit()
            completed.append(op_id)
        return completed
    finally:
        conn.close()
