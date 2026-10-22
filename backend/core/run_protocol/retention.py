"""Permanent unintegrated-run retention and authorized snapshot disposal.

Public symbols:
- ``snapshot_id`` — immutable identity bound into discard_snapshot authorization.
- ``discard_snapshot`` — remove one preserved branch+ledger only with oracle Consumption.
- ``restore_snapshot`` — rebuild the run worktree from its retained ledger.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

from core import run_worktree
from core.run_protocol.auth import Consumption
from core.run_protocol.identifiers import validate_run_id


class RetentionError(RuntimeError):
    """Snapshot identity, authorization, path safety, or git state is invalid."""


def snapshot_id(ledger: str | Path) -> str:
    path = Path(ledger) / "meta.json"
    try:
        data = path.read_bytes()
        value = json.loads(data)
    except (OSError, json.JSONDecodeError) as exc:
        raise RetentionError(f"snapshot metadata unreadable: {exc}") from exc
    if not isinstance(value, dict) or not value.get("snapshot"):
        raise RetentionError("snapshot metadata is malformed")
    return hashlib.sha256(data).hexdigest()


def discard_snapshot(*, consumption: Consumption, run_id: str, repo_name: str,
                     main: str | Path, ledger: str | Path) -> None:
    """Permanently remove exactly one authorized preserved snapshot and run branch."""
    validate_run_id(run_id)
    if not isinstance(consumption, Consumption):
        raise RetentionError("discard_snapshot requires oracle Consumption")
    payload = consumption._event_payload()
    expected = snapshot_id(ledger)
    if payload.get("action") != "discard_snapshot" or payload.get("run_id") != run_id \
            or consumption.state_hash is not None or consumption.canonical_id is not None:
        raise RetentionError("authorization is not for snapshot disposal")
    bound = consumption.bindings.get("snapshot_id")
    if bound != expected:
        raise RetentionError("authorization is bound to another snapshot")
    root = Path(ledger).resolve()
    expected_root = (run_worktree.ledger_root() / run_id / repo_name).resolve()
    if root != expected_root or root.is_symlink():
        raise RetentionError("ledger path is not this run/repo snapshot")
    branch = run_worktree.branch_name(run_id)
    result = subprocess.run(["git", "-C", str(main), "branch", "-D", branch],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    if result.returncode != 0:
        raise RetentionError("preserved run branch could not be deleted")
    shutil.rmtree(root)
    parent = root.parent
    try:
        parent.rmdir()
    except OSError:
        pass


def restore_snapshot(run_id: str, entry: dict, main: str | Path,
                     workspace: str | Path | None = None) -> dict:
    return run_worktree.restore_from_ledger(run_id, entry, main, workspace=workspace)
