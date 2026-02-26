"""TSCC filesystem-based snapshot manager.

This module provides the ``TSCCSnapshotManager`` class, which creates,
lists, and retrieves point-in-time snapshots of TSCC thread state.
Snapshots are stored as JSON files in the thread's snapshot directory
under the SwarmWS workspace.

Key public symbols:

- ``TSCCSnapshotManager``  — Snapshot CRUD with dedup and retention
- ``MAX_SNAPSHOTS_PER_THREAD`` — Retention cap (50 snapshots per thread)

Path resolution uses ``TSCCStateManager`` to determine whether a thread
is workspace-scoped or project-scoped, then delegates to
``SwarmWorkspaceManager`` for the workspace root path.

Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 11.4, 11.5
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4

from schemas.tscc import (
    TSCCActiveCapabilities,
    TSCCSnapshot,
    TSCCSource,
    TSCCState,
)

logger = logging.getLogger(__name__)

MAX_SNAPSHOTS_PER_THREAD = 50


class TSCCSnapshotManager:
    """Filesystem-based TSCC snapshot manager with dedup and retention.

    Creates, lists, and retrieves point-in-time snapshots of thread
    cognitive state.  Snapshots are stored as JSON files with colon-safe
    filenames in the thread's ``snapshots/`` directory.

    Parameters
    ----------
    workspace_manager:
        ``SwarmWorkspaceManager`` instance for path resolution.
    state_manager:
        ``TSCCStateManager`` instance for thread→project mapping.
    """

    MAX_SNAPSHOTS_PER_THREAD = MAX_SNAPSHOTS_PER_THREAD

    def __init__(self, workspace_manager, state_manager) -> None:
        self._workspace_manager = workspace_manager
        self._state_manager = state_manager

    def _get_snapshot_dir(self, thread_id: str) -> Path:
        """Resolve the snapshot directory for a thread.

        Workspace-scoped threads:
            ``{workspace}/chats/{thread_id}/snapshots/``
        Project-scoped threads:
            ``{workspace}/Projects/{project_dir}/chats/{thread_id}/snapshots/``

        Uses the in-memory state from ``_state_manager`` to determine scope.
        Falls back to workspace-scoped if no state exists.
        """
        ws_path = self._workspace_manager._resolve_workspace_path(None)

        # Check if thread has state with a project_id
        state: Optional[TSCCState] = None
        if thread_id in self._state_manager._states:
            state = self._state_manager._states[thread_id]

        if state and state.project_id:
            try:
                project_dir = self._workspace_manager._find_project_dir(
                    state.project_id, ws_path
                )
                return project_dir / "chats" / thread_id / "snapshots"
            except ValueError:
                logger.warning(
                    "Project %s not found, falling back to workspace scope",
                    state.project_id,
                )

        return Path(ws_path) / "chats" / thread_id / "snapshots"

    def _is_duplicate(
        self, snapshot_dir: Path, reason: str, window_seconds: int = 30
    ) -> bool:
        """Check if a snapshot with the same reason exists within the dedup window.

        Scans existing snapshot files in reverse chronological order and
        returns True if any snapshot with the same ``reason`` was created
        within ``window_seconds`` of now.
        """
        if not snapshot_dir.exists():
            return False

        now = datetime.now(timezone.utc)
        for f in sorted(snapshot_dir.glob("snapshot_*.json"), reverse=True):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if data.get("reason") != reason:
                    continue
                ts = datetime.fromisoformat(data["timestamp"])
                if (now - ts).total_seconds() <= window_seconds:
                    return True
                # Past the window — no need to check older files
                return False
            except (json.JSONDecodeError, KeyError, OSError, ValueError):
                continue
        return False

    def _enforce_retention(self, snapshot_dir: Path) -> None:
        """Delete oldest snapshots if count exceeds MAX_SNAPSHOTS_PER_THREAD."""
        files = sorted(snapshot_dir.glob("snapshot_*.json"))
        excess = len(files) - self.MAX_SNAPSHOTS_PER_THREAD
        if excess > 0:
            for f in files[:excess]:
                try:
                    f.unlink()
                except OSError:
                    logger.warning("Failed to delete snapshot: %s", f)

    @staticmethod
    def _colon_safe_timestamp(ts: str) -> str:
        """Convert ISO 8601 timestamp to colon-safe filename fragment.

        ``2025-01-15T10:30:00+00:00`` → ``2025-01-15T10-30-00Z``
        """
        dt = datetime.fromisoformat(ts)
        return dt.strftime("%Y-%m-%dT%H-%M-%SZ")

    def create_snapshot(
        self, thread_id: str, state: TSCCState, reason: str
    ) -> Optional[TSCCSnapshot]:
        """Create a point-in-time snapshot from current thread state.

        Returns None if a duplicate snapshot (same reason within 30s) exists.
        Enforces retention cap after writing.

        Parameters
        ----------
        thread_id:
            The thread to snapshot.
        state:
            Current ``TSCCState`` to capture.
        reason:
            Human-readable trigger reason.

        Returns
        -------
        The created ``TSCCSnapshot``, or None if deduplicated.
        """
        snapshot_dir = self._get_snapshot_dir(thread_id)

        if self._is_duplicate(snapshot_dir, reason):
            return None

        snapshot_dir.mkdir(parents=True, exist_ok=True)

        now = datetime.now(timezone.utc).isoformat()
        snapshot = TSCCSnapshot(
            snapshot_id=str(uuid4()),
            thread_id=thread_id,
            timestamp=now,
            reason=reason,
            lifecycle_state=state.lifecycle_state,
            active_agents=list(state.live_state.active_agents),
            active_capabilities=TSCCActiveCapabilities(
                skills=list(state.live_state.active_capabilities.skills),
                mcps=list(state.live_state.active_capabilities.mcps),
                tools=list(state.live_state.active_capabilities.tools),
            ),
            what_ai_doing=list(state.live_state.what_ai_doing),
            active_sources=[
                TSCCSource(path=s.path, origin=s.origin)
                for s in state.live_state.active_sources
            ],
            key_summary=list(state.live_state.key_summary),
        )

        safe_ts = self._colon_safe_timestamp(now)
        short_id = snapshot.snapshot_id[:8]
        filename = f"snapshot_{safe_ts}_{short_id}.json"
        filepath = snapshot_dir / filename

        filepath.write_text(
            json.dumps(snapshot.model_dump(), indent=2),
            encoding="utf-8",
        )

        self._enforce_retention(snapshot_dir)
        return snapshot

    def list_snapshots(self, thread_id: str) -> list[TSCCSnapshot]:
        """List all snapshots for a thread in chronological order.

        Sorts by the ``timestamp`` field inside each JSON file to ensure
        correct ordering even when filenames share the same second.
        Corrupted or unreadable JSON files are logged and skipped.
        """
        snapshot_dir = self._get_snapshot_dir(thread_id)
        if not snapshot_dir.exists():
            return []

        snapshots = []
        for f in snapshot_dir.glob("snapshot_*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                snapshots.append(TSCCSnapshot(**data))
            except (json.JSONDecodeError, OSError, Exception) as exc:
                logger.warning("Skipping corrupted snapshot %s: %s", f, exc)

        snapshots.sort(key=lambda s: s.timestamp)
        return snapshots

    def get_snapshot(
        self, thread_id: str, snapshot_id: str
    ) -> Optional[TSCCSnapshot]:
        """Retrieve a single snapshot by ID.

        Returns None if the snapshot is not found.
        """
        for snap in self.list_snapshots(thread_id):
            if snap.snapshot_id == snapshot_id:
                return snap
        return None
