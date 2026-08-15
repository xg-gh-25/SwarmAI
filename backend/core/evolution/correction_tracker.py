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

from core.evolution.class_key import canonical_class_key

logger = logging.getLogger(__name__)

def _default_state_path() -> Path:
    """Default state-file path, resolved AT CALL TIME via the single app-data authority.

    Must NOT be a module-level constant: a frozen home-relative default captures the
    value at import, before a test/sandbox sets SWARM_DATA_DIR — the tracker would then
    persist into the live production tree under the sandbox guard.
    """
    from config import get_app_data_dir

    return get_app_data_dir() / "state" / "correction_tracker.json"


_RESOLVE_DAYS = 30  # Days of silence after gate to mark resolved
_AMBER_THRESHOLD = 1  # post_gate_count for ⚠️
_RED_THRESHOLD = 2  # post_gate_count for 🔴
_MAX_EVIDENCE = 10  # Keep last N evidence entries per class


def _fresh_entry() -> dict:
    """A blank class-state entry (single template — was duplicated 3x inline)."""
    return {
        "count": 0,
        "last": None,
        "active_rule": None,
        "rule_deployed": None,
        "post_rule_count": 0,
        "active_gate": None,
        "gate_deployed": None,
        "post_gate_count": 0,
        "resolved": False,
        "evidence": [],
        "recorded_refs": [],  # seen correction_refs for idempotent recording
    }


def _merge_drift(raw_state: dict) -> dict:
    """Collapse drifted duplicate keys onto their canonical key. Self-healing.

    The bug: the same logical class lived under multiple raw keys (lowercase
    "operational" accumulating real recurrence; "OPERATIONAL" holding the
    accepted rule on a count=0 phantom). This groups every raw key by
    ``canonical_class_key`` and merges each group into ONE entry.

    Merge rules (Gate-1 hardened):
      - count / post_rule_count / post_gate_count: see below — NOT a blind sum.
      - active_rule / active_gate: carried from a single WINNER member (the one
        with the most-recent deploy date); the winner's post_*_count rides along.
        post_*_count is NEVER summed across members — summing would fabricate
        failures against a rule/gate that the losing members' recurrences never
        post-dated (Gate-1 must-fix #1).
      - count: summed (every recurrence is real regardless of which key it hit).
      - evidence: concat, sorted by date, last _MAX_EVIDENCE kept (deterministic
        => idempotent — Gate-1 must-fix #3).
      - resolved: AND (any unresolved member => merged unresolved).
      - last: max date across members.

    Idempotent: a single-member group passes through unchanged, so running this
    on already-merged state yields identical output. Total: never raises on
    sparse/legacy entries or None deploy dates (Gate-1 must-fix #2).
    """
    groups: dict[str, list[dict]] = {}
    for raw_key, entry in raw_state.items():
        if not isinstance(entry, dict):
            continue
        ckey = canonical_class_key(raw_key)
        groups.setdefault(ckey, []).append(entry)

    merged: dict[str, dict] = {}
    for ckey, members in groups.items():
        if not ckey:
            # Defensive: an empty canonical key (blank/whitespace raw key) — drop
            # it rather than create a "" entry. record() guards against creating
            # these going forward; this cleans any legacy stragglers.
            continue
        if len(members) == 1:
            # Fast path: no drift. Pass through unchanged (idempotency anchor).
            merged[ckey] = members[0]
            continue

        # Gate-2 LOW-3: start from a shallow copy of the first member, not a bare
        # template, so any field outside _fresh_entry() (future schema growth) is
        # preserved instead of silently amputated for drifted classes only.
        out = _fresh_entry()
        out.update(dict(members[0]))
        out["count"] = sum(int(m.get("count", 0) or 0) for m in members)

        # resolved = AND (any unresolved -> unresolved)
        out["resolved"] = all(bool(m.get("resolved", False)) for m in members)

        # last = max date string. str() coercion (Gate-2 HIGH): legacy/corrupt
        # entries may hold non-string dates (int) — mixed-type max() raises, which
        # _load would swallow and silently RE-BURY the very drift we heal. Coercing
        # makes _merge_drift truly total. Secondary key is identity-stable, but a
        # single deserialized file has stable order so this is belt-and-suspenders.
        last_dates = [str(m["last"]) for m in members if m.get("last")]
        out["last"] = max(last_dates) if last_dates else None

        # Rule winner: member with a non-empty active_rule and the latest
        # rule_deployed. None deploy date sorts earliest (Gate-1 #2: no max(None)).
        # Secondary key active_rule makes equal-date ties deterministic (Gate-2 LOW-2).
        rule_members = [m for m in members if m.get("active_rule")]
        if rule_members:
            winner = max(
                rule_members,
                key=lambda m: (str(m.get("rule_deployed") or ""), str(m.get("active_rule") or "")),
            )
            out["active_rule"] = winner.get("active_rule")
            out["rule_deployed"] = winner.get("rule_deployed")
            out["post_rule_count"] = int(winner.get("post_rule_count", 0) or 0)
        else:
            # No rule among members — clear the template-copied fields from members[0].
            out["active_rule"] = None
            out["rule_deployed"] = None
            out["post_rule_count"] = 0

        # Gate winner: same pattern, independent of the rule winner.
        gate_members = [m for m in members if m.get("active_gate")]
        if gate_members:
            gwinner = max(
                gate_members,
                key=lambda m: (str(m.get("gate_deployed") or ""), str(m.get("active_gate") or "")),
            )
            out["active_gate"] = gwinner.get("active_gate")
            out["gate_deployed"] = gwinner.get("gate_deployed")
            out["post_gate_count"] = int(gwinner.get("post_gate_count", 0) or 0)
        else:
            out["active_gate"] = None
            out["gate_deployed"] = None
            out["post_gate_count"] = 0

        # Evidence: concat all members, sort by date (str-coerced, stable), keep last N.
        all_evidence = []
        for m in members:
            ev = m.get("evidence")
            if isinstance(ev, list):
                all_evidence.extend(e for e in ev if isinstance(e, dict))
        all_evidence.sort(key=lambda e: str(e.get("date", "")))
        out["evidence"] = all_evidence[-_MAX_EVIDENCE:]

        # recorded_refs: order-preserving UNION across ALL members (Gate-2 M1).
        # The merge sums count across members, so the dedup ledger MUST also union
        # across members — keeping only members[0]'s refs while summing all counts
        # would let a dropped ref re-record and inflate the counter past true
        # recurrence (the exact "false structural proposal fires" failure mode).
        seen_refs: list = []
        for m in members:
            for r in (m.get("recorded_refs") or []):
                if r not in seen_refs:
                    seen_refs.append(r)
        out["recorded_refs"] = seen_refs

        merged[ckey] = out

    return merged


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
        self._state_path = state_path or _default_state_path()
        self._state: dict[str, dict] = self._load()

    def _load(self) -> dict[str, dict]:
        """Load state from disk, merging drifted duplicate keys. Empty on corrupt.

        Drift merge is self-healing (runs every load) and idempotent. If the
        merge itself raises on some pathological entry, degrade to the raw loaded
        state rather than bricking every tracker construction (Gate-1 must-fix #2).
        """
        try:
            if self._state_path.exists():
                data = json.loads(self._state_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    try:
                        return _merge_drift(data)
                    except Exception as exc:  # noqa: BLE001 — never brick construction
                        logger.warning("drift merge degraded, using raw state: %s", exc)
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

    def record(self, class_name: str, evidence: str = "",
               correction_ref: str | None = None) -> None:
        """Record a correction event for a class.

        ``correction_ref`` (optional): a stable id for the underlying correction.
        When provided, recording is IDEMPOTENT by ref — re-recording the same
        correction_ref is a no-op (no count increment, no evidence append). This
        is the precision teeth for autonomous cognitive recording (run_448a4f7f):
        the live pending queue parked ONE correction_ref 3x with 3 different
        evidence strings, so text-dedup would triple-count. The seen-ref ledger
        (``recorded_refs``) persists in the entry — separate from the 10-cap
        evidence list — so dedup survives evidence roll-off AND process reloads
        (the maintenance hook builds a fresh tracker each session). When omitted
        (the operational ``counted`` path), behavior is unchanged: every call counts.
        """
        ckey = canonical_class_key(class_name)
        if not ckey:
            # Blank/whitespace class name -> nothing to record (Gate-1 AC5: no crash).
            logger.debug("record() ignoring empty class name: %r", class_name)
            return

        def _mutate():
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

            if ckey not in self._state:
                self._state[ckey] = _fresh_entry()

            entry = self._state[ckey]

            # Idempotency by correction_ref (precision teeth). The ledger lives in
            # the entry so it persists to disk and is checked under the same lock
            # as the count mutation (atomic). Unbounded by design — it must NOT
            # share the 10-cap evidence window, or an aged-off ref re-inflates.
            if correction_ref is not None:
                seen = entry.setdefault("recorded_refs", [])
                if correction_ref in seen:
                    return  # already counted this correction — no-op
                seen.append(correction_ref)

            entry["count"] += 1
            entry["last"] = today

            # Store evidence for later classification
            if "evidence" not in entry:
                entry["evidence"] = []
            if evidence:
                entry["evidence"].append({"date": today, "text": evidence[:500]})
                entry["evidence"] = entry["evidence"][-_MAX_EVIDENCE:]

            # Escalation counters. .get() is mandatory — legacy state entries predate
            # the rule fields (Gate-1 Phase-2 lesson). A gate SUPERSEDES a rule: once a
            # gate is active the class is past the rule stage, so post_rule_count freezes
            # and only post_gate_count advances (mutual exclusion).
            if entry.get("active_gate"):
                entry["post_gate_count"] = entry.get("post_gate_count", 0) + 1
            elif entry.get("active_rule"):
                entry["post_rule_count"] = entry.get("post_rule_count", 0) + 1

            # Un-resolve if it was previously resolved (class recurred)
            if entry["resolved"]:
                entry["resolved"] = False

        self._locked_mutate(_mutate)

    def register_rule(self, class_name: str, rule_id: str, description: str = "") -> None:
        """Register an L1 rule (AGENT/STEERING) accepted for a correction class.

        Mirror of register_gate. Marks that a rule now exists, so a recurrence
        AFTER this point advances post_rule_count — and once post_rule_count
        crosses the threshold the escalation ladder proposes a GATE (rule failed
        -> escalate the enforcement mechanism). This is the caller Phase 2
        deferred: accepting a rule proposal in the dashboard calls this.
        """

        ckey = canonical_class_key(class_name)
        if not ckey:
            logger.debug("register_rule() ignoring empty class name: %r", class_name)
            return

        def _mutate():
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

            if ckey not in self._state:
                self._state[ckey] = _fresh_entry()

            entry = self._state[ckey]
            entry["active_rule"] = rule_id
            entry["rule_deployed"] = today
            entry["post_rule_count"] = 0  # Reset counter on new rule

        self._locked_mutate(_mutate)

    def unregister_rule(self, class_name: str) -> None:
        """Revert register_rule for a correction class (the missing inverse).

        Clears active_rule + rule_deployed and resets post_rule_count to 0 — the
        exact fields register_rule sets — so a mis-accepted governance rule can be
        undone without hand-editing the state JSON. count/evidence/resolved are
        PRESERVED: unregister reverts only the rule marker, not the class history.
        Unknown class is a no-op (never creates a phantom entry). flock-safe via
        _locked_mutate.
        """

        ckey = canonical_class_key(class_name)
        if not ckey:
            logger.debug("unregister_rule() ignoring empty class name: %r", class_name)
            return

        def _mutate():
            entry = self._state.get(ckey)
            if entry is None:
                return  # no-op: never fabricate a class
            entry["active_rule"] = None
            entry["rule_deployed"] = None
            entry["post_rule_count"] = 0

        self._locked_mutate(_mutate)

    def register_gate(self, class_name: str, gate_id: str, description: str = "") -> None:
        """Register a structural fix (code gate) for a correction class."""

        ckey = canonical_class_key(class_name)
        if not ckey:
            logger.debug("register_gate() ignoring empty class name: %r", class_name)
            return

        def _mutate():
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

            if ckey not in self._state:
                self._state[ckey] = _fresh_entry()

            entry = self._state[ckey]
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
        """Get state for a specific class. Returns a copy (safe from mutation).

        Canonicalizes the lookup key so "operational" and "OPERATIONAL" resolve
        to the same merged entry.
        """
        entry = self._state.get(canonical_class_key(class_name))
        return dict(entry) if entry is not None else None

    def class_names(self) -> list[str]:
        """List all tracked class names (for escalation iteration)."""
        return list(self._state.keys())

    def has_red(self) -> bool:
        """True if any unresolved class is in the 🔴 state (recurrence past fix).

        Structural signal — computed from state (post_gate/post_rule >= RED),
        NOT by string-matching the emoji in briefing_lines(). Consumers (e.g. the
        session-briefing divergence check, M4-3) must use THIS, not substring-scan
        the render output, so the signal survives any future format change to
        briefing_lines(). Same threshold the render uses: a gate that did not hold
        (post_gate >= RED) or a rule that did not hold (post_rule >= RED).
        """
        for entry in self._state.values():
            if entry.get("resolved"):
                continue
            post_gate = entry.get("post_gate_count", 0) or 0
            post_rule = entry.get("post_rule_count", 0) or 0
            if entry.get("active_gate") and post_gate >= _RED_THRESHOLD:
                return True
            if entry.get("active_rule") and post_rule >= _RED_THRESHOLD:
                return True
        return False

    def has_gate_failure(self) -> bool:
        """True if any unresolved class recurred past a DEPLOYED GATE (post_gate >= RED).

        Strictly narrower than has_red(): this is gate-failure ONLY, excluding
        rule-only recurrence. It is the signal for the M4-3 score↔reality
        DIVERGENCE headline — a deployed STRUCTURAL fix that did not hold is the
        real "the loop is broken despite a green eval score" event.

        Why not has_red() for divergence (meta-review HIGH, run_cf491cab): a
        rule-only chronically-recurring class (e.g. live OPERATIONAL: post_rule=50,
        799 recurrences, no gate) is a KNOWN-OPEN item already surfaced by its
        per-class tracker line. Headlining it as DIVERGENCE on EVERY session
        forever trains the operator to ignore the banner — destroying the signal
        precisely when a real new gate-failure appears. Divergence must be
        event-like (a gate that was supposed to hold and didn't), not level-like
        (anything currently red). has_red() stays for the per-class red status;
        has_gate_failure() gates the headline.
        """
        for entry in self._state.values():
            if entry.get("resolved"):
                continue
            post_gate = entry.get("post_gate_count", 0) or 0
            if entry.get("active_gate") and post_gate >= _RED_THRESHOLD:
                return True
        return False

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
            elif entry.get("active_rule"):
                # Rule accepted but no gate yet. Show rule state — a rule-active
                # class is NOT "no fix" (Gate-1 Check-5). post_rule_count crossing
                # RED means the rule failed -> a gate proposal is due.
                rule = entry.get("active_rule")
                post_rule = entry.get("post_rule_count", 0)
                status = "\U0001f534" if post_rule >= _RED_THRESHOLD else "✅"
                lines.append(
                    f"\U0001f9ec {class_name}: {count} total, "
                    f"{post_rule} since rule ({rule}) {status}"
                )
            else:
                # No structural fix at all — just show count.
                lines.append(f"\U0001f9ec {class_name}: {count} total, no fix")

        return lines
