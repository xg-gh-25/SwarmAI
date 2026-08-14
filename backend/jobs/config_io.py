"""config_io — the SINGLE serialization authority for the signal-pipeline config.yaml.

config.yaml is written by TWO independent processes: self_tune (scheduled weekday
read-modify-write) and the /api/community/feeds endpoints (user edits). Before this
module, self_tune did an unlocked, non-atomic `open('w')` — so a UI write racing a
self_tune run was last-writer-wins CLOBBER.

`mutate_config(mutator)` is the fix: it serializes EVERY read-modify-write under one
exclusive flock on a `.config.yaml.lock` SIDECAR and writes atomically (tmp +
os.replace). BOTH writers route through it (R27 — a lock only one writer takes
serializes nothing), so concurrent edits can never drop each other.

Design cloned from core.artifact_registry._mutate_manifest / _write_manifest — the
proven pattern in this codebase. Two load-bearing details (Gate-1, run_3c37f24e):
  - The lock is a SIDECAR, never config.yaml itself: os.replace swaps the inode, so a
    flock held on the replaced file would be silently dropped (GUI22).
  - The lock path is `.expanduser().resolve()`-canonicalized so the daemon and any
    CLI/subprocess key the SAME inode even given a symlinked/un-resolved root
    (artifact_registry.py:117).
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Callable, Optional, TypeVar

import yaml

from utils.file_lock import flock_exclusive, flock_unlock

from .paths import CONFIG_FILE

logger = logging.getLogger(__name__)

T = TypeVar("T")

# The ONE source of the config.yaml header (previously duplicated inline in
# self_tune's write). mutate_config writes it on every save so it's never lost.
CONFIG_HEADER = (
    "# Swarm Signal Pipeline — Feed Configuration\n"
    "# Auto-tuned by self_tune.py based on MEMORY.md + Projects/ + DailyActivity.\n"
    "# Manual edits are preserved; self-tune only modifies user_context and\n"
    "# auto-managed feeds.\n\n"
)


def _lock_path(config_path: Path) -> Path:
    # Sidecar next to config.yaml, canonicalized so daemon + CLI + scheduler
    # subprocess all flock the SAME inode (never the config file itself — GUI22).
    return (config_path.parent / ".config.yaml.lock").expanduser().resolve()


def read_config(config_path: Optional[Path] = None) -> dict:
    """Read config.yaml → dict. Returns {} if missing/empty/corrupt (never raises).

    Lock-free (reads are safe; only the read-modify-WRITE cycle needs the lock)."""
    path = config_path or CONFIG_FILE
    if not path.is_file():
        return {}
    try:
        return yaml.safe_load(path.read_text()) or {}
    except (yaml.YAMLError, OSError) as e:
        logger.warning("config_io: failed to read %s: %s", path, e)
        return {}


def mutate_config(
    mutator: Callable[[dict], T],
    config_path: Optional[Path] = None,
    *,
    write_if: Optional[Callable[[dict, T], bool]] = None,
) -> T:
    """Serialize a read-modify-write of config.yaml under a sidecar flock.

    Reads config.yaml (fresh, inside the lock), calls ``mutator(config)`` (which
    mutates the dict in place and/or returns a value), then writes it back
    atomically (tmp + os.replace) with the header preserved. Returns whatever the
    mutator returns.

    ``write_if(config, result) -> bool`` (optional): gate the WRITE. When provided
    and it returns False, the atomic write is SKIPPED — the read+mutate still ran
    under the lock, but the file is not touched. This avoids a spurious rewrite on a
    no-op mutation (e.g. a self_tune cycle with zero changes would otherwise re-dump
    the whole file every run — mtime churn + key reordering). Omit it (default) for
    an always-write caller (the /api/feeds endpoints always change something).

    The exclusive flock on the `.config.yaml.lock` sidecar means concurrent callers
    (a UI write + a self_tune run) queue instead of clobbering — the whole cycle is
    atomic w.r.t. other mutate_config callers. Hold time is bounded (mutators do only
    in-memory dict work + a small dump; slow context extraction happens OUTSIDE the
    lock). If the mutator raises, the original file is left UNTOUCHED.
    """
    path = (config_path or CONFIG_FILE)
    lock_path = _lock_path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    lock_fd = open(lock_path, "w")
    try:
        flock_exclusive(lock_fd)
        # fresh read INSIDE the lock — never a stale snapshot from before the wait
        config = read_config(path)
        result = mutator(config)
        if write_if is None or write_if(config, result):
            _write_atomic(path, config)
        return result
    finally:
        flock_unlock(lock_fd)
        lock_fd.close()


def _write_atomic(path: Path, config: dict) -> None:
    """Write config atomically: header + yaml.dump to a tmp file in the SAME dir,
    then os.replace. A crash mid-write leaves the original intact (never a partial).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    body = CONFIG_HEADER + yaml.dump(
        config, default_flow_style=False, sort_keys=False, allow_unicode=True
    )
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp", prefix=".config-")
    try:
        with open(fd, "w", encoding="utf-8") as f:
            f.write(body)
        Path(tmp_path).replace(path)
    except BaseException:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except OSError:
            pass
        raise
