"""Where a run's event-signing key lives: the one place that creates and loads it.

The key signs every event of one run (``EventAuthority``) and MACs the runner's
evidence seals. Only the daemon process uses it (B-class design): the CLI and the
test runner send their requests to the daemon (``daemon_exec``) and never load it.

Storage: a 0600 file ``state/pipeline_agent_audit/runs/<run_id>/authority.key``.
B-class B-c (a store the agent's processes cannot read) is NOT reachable while the
agent runs as the same OS user as the daemon: the ``security`` CLI keychain item is
readable by any same-user process through that same CLI, and an ACL bound to the
daemon binary holds only for a packaged, stably signed binary (fixing log §31). The
real separation is a different OS user or a sandbox for the agent; with every key
access already here, that change touches only this module and file ownership.

- ``create(data_dir, run_id)`` -> ``EventAuthority`` (refuses an existing key)
- ``load(data_dir, run_id)`` -> ``EventAuthority`` (``KeyUnavailable`` when absent)
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from core.run_protocol.events import EventAuthority, EventError


class KeyUnavailable(RuntimeError):
    """The run's signing key is missing or cannot be read."""


def _key_file(data_dir: "str | Path", run_id: str) -> Path:
    from core.run_protocol.identifiers import validate_run_id
    validate_run_id(run_id)
    return Path(data_dir) / "state" / "pipeline_agent_audit" / "runs" / run_id / "authority.key"


def create(data_dir: "str | Path", run_id: str) -> EventAuthority:
    """A new key for a new run (its audit directory already exists)."""
    key = secrets.token_bytes(32)
    fd = os.open(_key_file(data_dir, run_id), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(key)
        handle.flush()
        os.fsync(handle.fileno())
    return EventAuthority(key)


def load(data_dir: "str | Path", run_id: str) -> EventAuthority:
    try:
        return EventAuthority(_key_file(data_dir, run_id).read_bytes())
    except (OSError, EventError) as exc:
        raise KeyUnavailable(f"authority unavailable: {exc}") from exc


__all__ = ["KeyUnavailable", "create", "load"]
