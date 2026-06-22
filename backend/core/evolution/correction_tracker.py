"""Correction class tracker — Evolution v3 MVP.

Tracks correction classes (e.g. CLASS_A: Confidence → Skip Process) as a
simple state machine. Records events, monitors gate effectiveness, and
auto-resolves classes after 30 days of no recurrence post-gate.

State is persisted to a single JSON file (~/.swarm-ai/state/correction_tracker.json).
All operations are pure — no DB, no LLM, no network.

Concurrency: uses flock on a sidecar .lock file to prevent lost updates
from parallel sessions (same pattern as corrections.jsonl).

Public API:
    CorrectionClassTracker(state_path)
        .record(class_name, evidence)      — log a correction event
        .register_gate(class_name, gate_id, description) — register a structural fix
        .check_auto_resolve()              — mark classes resolved after 30d silence
        .get_class(class_name)             — read class state (returns copy)
        .briefing_lines()                  — formatted status lines for session briefing
"""

from __future__ import annotations

import json
import logging
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_STATE_PATH = Path.home() / ".swarm-ai" / "state" / "correction_tracker.json"

_RESOLVE_DAYS = 30  # Days of silence after gate to mark resolved
_AMBER_THRESHOLD = 1  # post_gate_count for ⚠️
_RED_THRESHOLD = 2  # post_gate_count for 🔴
_MAX_EVIDENCE = 10  # Keep last N evidence entries per class


def _flock_exclusive(fd):
    """Acquire exclusive file lock (cross-platform)."""
    try:
        from utils.file_lock import flock_exclusive
        flock_exclusive(fd)
    except ImportError:
        # Fallback: try fcntl directly (macOS/Linux)
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX)


def _flock_unlock(fd):
    """Release file lock."""
    try:
        from utils.file_lock import flock_unlock
        flock_unlock(fd)
    except ImportError:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)


class CorrectionClassTracker:
    """Pure state machine for correction class tracking.

    One tracker instance per operation is intentional for isolation.
    Each mutating operation acquires a file lock, re-reads state from disk,
    mutates, and writes atomically. This prevents lost updates from parallel
    sessions at the cost of two file reads per operation — acceptable because
    corrections are rare events (1-2 per session).
    """

    def __init__(self, state_path: Path | None = None) -> None:
        self._state_path = state_path or _DEFAULT_STATE_PATH
        self._state: dict[str, dict] = self._load()

    def _load(self) -> dict[str, dict]:
        """Load state from disk. Returns empty dict on missing/corrupt file."""
        try:
            if self._state_path.exists():
                data = json.loads(self._state_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Correction tracker state corrupt/unreadable, resetting: %s", exc)
        return {}

    def _save(self) -> None:
        """Atomic write: tmp file + rename."""
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd = tempfile.NamedTemporaryFile(
            mode="w",
            dir=self._state_path.parent,
            suffix=".tmp",
            delete=False,
            encoding="utf-8",
        )
        try:
            json.dump(self._state, tmp_fd, indent=2, ensure_ascii=False)
            tmp_fd.close()
            Path(tmp_fd.name).replace(self._state_path)
        except Exception:
            tmp_fd.close()
            try:
                Path(tmp_fd.name).unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def _locked_mutate(self, mutator) -> None:
        """Acquire flock, re-read state, apply mutator, save atomically.

        Prevents lost updates from parallel sessions writing simultaneously.
        """
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self._state_path.with_suffix(".json.lock")
        fd = open(lock_path, "w")
        try:
            _flock_exclusive(fd)
            # Re-read state under lock (another session may have written)
            self._state = self._load()
            mutator()
            self._save()
        finally:
            _flock_unlock(fd)
            fd.close()

    def record(self, class_name: str, evidence: str = "") -> None:
        """Record a correction event for a class."""

        def _mutate():
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

            if class_name not in self._state:
                self._state[class_name] = {
                    "count": 0,
                    "last": None,
                    "active_gate": None,
                    "gate_deployed": None,
                    "post_gate_count": 0,
                    "resolved": False,
                    "evidence": [],
                }

            entry = self._state[class_name]
            entry["count"] += 1
            entry["last"] = today

            # Store evidence for later classification
            if "evidence" not in entry:
                entry["evidence"] = []
            if evidence:
                entry["evidence"].append({"date": today, "text": evidence[:500]})
                entry["evidence"] = entry["evidence"][-_MAX_EVIDENCE:]

            # If a gate is active, increment post-gate counter
            if entry["active_gate"]:
                entry["post_gate_count"] += 1

            # Un-resolve if it was previously resolved (class recurred)
            if entry["resolved"]:
                entry["resolved"] = False

        self._locked_mutate(_mutate)

    def register_gate(self, class_name: str, gate_id: str, description: str = "") -> None:
        """Register a structural fix (code gate) for a correction class."""

        def _mutate():
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

            if class_name not in self._state:
                self._state[class_name] = {
                    "count": 0,
                    "last": None,
                    "active_gate": None,
                    "gate_deployed": None,
                    "post_gate_count": 0,
                    "resolved": False,
                    "evidence": [],
                }

            entry = self._state[class_name]
            entry["active_gate"] = gate_id
            entry["gate_deployed"] = today
            entry["post_gate_count"] = 0  # Reset counter on new gate

        self._locked_mutate(_mutate)

    def check_auto_resolve(self) -> list[str]:
        """Check all classes for 30-day auto-resolve. Returns list of resolved class names."""
        resolved_classes = []

        def _mutate():
            now = datetime.now(timezone.utc)

            for class_name, entry in self._state.items():
                if entry.get("resolved"):
                    continue
                if not entry.get("active_gate"):
                    continue
                if entry.get("post_gate_count", 0) > 0:
                    continue

                # Check if gate has been deployed for >= 30 days
                gate_date_str = entry.get("gate_deployed")
                if not gate_date_str:
                    continue

                try:
                    gate_date = datetime.strptime(gate_date_str, "%Y-%m-%d").replace(
                        tzinfo=timezone.utc
                    )
                    if (now - gate_date) >= timedelta(days=_RESOLVE_DAYS):
                        entry["resolved"] = True
                        resolved_classes.append(class_name)
                except ValueError:
                    continue

        self._locked_mutate(_mutate)
        return resolved_classes

    def get_class(self, class_name: str) -> dict | None:
        """Get state for a specific class. Returns a copy (safe from mutation)."""
        entry = self._state.get(class_name)
        return dict(entry) if entry is not None else None

    def class_names(self) -> list[str]:
        """List all tracked class names (for escalation iteration)."""
        return list(self._state.keys())

    def briefing_lines(self) -> list[str]:
        """Generate status lines for session briefing.

        Format: "🧬 CLASS_A: 11 total, 0 since gate (GC12, 10d) ✅"
        Returns empty list if no active (unresolved) classes.
        """
        lines = []
        now = datetime.now(timezone.utc)

        for class_name, entry in sorted(self._state.items()):
            if entry.get("resolved"):
                continue

            count = entry.get("count", 0)
            post_gate = entry.get("post_gate_count", 0)
            gate = entry.get("active_gate")

            if gate:
                # Calculate days since gate deployed
                gate_date_str = entry.get("gate_deployed", "")
                try:
                    gate_date = datetime.strptime(gate_date_str, "%Y-%m-%d").replace(
                        tzinfo=timezone.utc
                    )
                    days_since = (now - gate_date).days
                    days_str = f"{days_since}d"
                except ValueError:
                    days_str = "?"

                # Status indicator
                if post_gate >= _RED_THRESHOLD:
                    status = "\U0001f534"  # 🔴
                elif post_gate >= _AMBER_THRESHOLD:
                    status = "⚠️"
                else:
                    status = "✅"

                lines.append(
                    f"\U0001f9ec {class_name}: {count} total, "
                    f"{post_gate} since gate ({gate}, {days_str}) {status}"
                )
            else:
                # No gate — just show count
                lines.append(f"\U0001f9ec {class_name}: {count} total, no gate")

        return lines
