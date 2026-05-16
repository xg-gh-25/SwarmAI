"""Gate Promotion — graduated enforcement for DDD cultivation soft gates.

Manages the lifecycle of soft gates through data-driven promotion:
  soft (observe) → 30 days data → evaluate criteria → hard (enforce)

Three gates tracked:
  1. trust_annotation — blocks auto-apply for low-trust DDD sections
  2. noise_filter — rejects noise DailyActivity writes
  3. file_tracker — enforces test coverage for multi-file changes

Promotion criteria (ALL must be true):
  - trigger_count >= 20
  - false_positive_rate < 10%
  - user_overrides in last 14d == 0
  - incidents_prevented >= 1

Auto-demotion: 3 user overrides in 7d → demote back to soft.

Public symbols:
    - GateManager       — main class for gate lifecycle
    - GateStatus        — per-gate tracking data
    - GATE_NAMES        — list of all managed gate names
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

GATE_NAMES = ("trust_annotation", "noise_filter", "file_tracker")

# Promotion criteria thresholds
MIN_TRIGGERS = 20
MAX_FP_RATE = 0.10
MAX_OVERRIDES_14D = 0
MIN_INCIDENTS = 1

# Demotion criteria
DEMOTION_OVERRIDES = 3
DEMOTION_WINDOW_DAYS = 7


@dataclass
class GateStatus:
    """Tracking data for a single soft gate."""
    installed_at: str = ""
    trigger_count: int = 0
    false_positive_count: int = 0
    user_overrides: int = 0
    override_dates: list[str] = field(default_factory=list)
    incidents_prevented: list[str] = field(default_factory=list)
    promotion_eligible: bool = False
    promoted_at: str | None = None
    demoted_at: str | None = None

    @property
    def is_promoted(self) -> bool:
        """Gate is currently in hard enforcement mode."""
        return self.promoted_at is not None and self.demoted_at is None

    @property
    def fp_rate(self) -> float:
        """False positive rate (0.0 to 1.0)."""
        if self.trigger_count == 0:
            return 0.0
        return self.false_positive_count / self.trigger_count

    @property
    def overrides_in_last_14d(self) -> int:
        """Count of user overrides in the last 14 days."""
        cutoff = (date.today() - timedelta(days=14)).isoformat()
        return sum(1 for d in self.override_dates if d >= cutoff)

    @property
    def overrides_in_last_7d(self) -> int:
        """Count of user overrides in the last 7 days (for demotion check)."""
        cutoff = (date.today() - timedelta(days=7)).isoformat()
        return sum(1 for d in self.override_dates if d >= cutoff)

    def meets_promotion_criteria(self) -> bool:
        """Check all 4 promotion criteria."""
        return (
            self.trigger_count >= MIN_TRIGGERS
            and self.fp_rate < MAX_FP_RATE
            and self.overrides_in_last_14d <= MAX_OVERRIDES_14D
            and len(self.incidents_prevented) >= MIN_INCIDENTS
        )

    def should_demote(self) -> bool:
        """Check demotion criteria: 3+ overrides in 7 days."""
        return self.is_promoted and self.overrides_in_last_7d >= DEMOTION_OVERRIDES


class GateManager:
    """Manages the lifecycle of soft→hard gate promotion.

    Persists gate data to .artifacts/gate_promotion_data.json.
    Called by:
      - Orchestrator channels (record_trigger, record_false_positive)
      - Timer tick (check_promotions)
      - User actions (record_override)
    """

    def __init__(self, artifacts_dir: Path) -> None:
        self._file = artifacts_dir / "gate_promotion_data.json"
        self._gates: dict[str, GateStatus] = {}
        self._load()

    def _load(self) -> None:
        """Load gate data from disk, initializing missing gates."""
        if self._file.exists():
            try:
                raw = json.loads(self._file.read_text(encoding="utf-8"))
                for name in GATE_NAMES:
                    if name in raw:
                        self._gates[name] = GateStatus(**raw[name])
                    else:
                        self._gates[name] = self._new_gate()
            except (json.JSONDecodeError, OSError, TypeError) as exc:
                logger.warning("gate_promotion: failed to load %s: %s", self._file, exc)
                self._gates = {name: self._new_gate() for name in GATE_NAMES}
        else:
            self._gates = {name: self._new_gate() for name in GATE_NAMES}

    def _save(self) -> None:
        """Persist gate data to disk."""
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            data = {name: asdict(gate) for name, gate in self._gates.items()}
            self._file.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("gate_promotion: failed to save: %s", exc)

    @staticmethod
    def _new_gate() -> GateStatus:
        """Create a new gate with today's install date."""
        return GateStatus(installed_at=date.today().isoformat())

    def get(self, name: str) -> GateStatus | None:
        """Get gate status by name."""
        return self._gates.get(name)

    def is_gate_hard(self, name: str) -> bool:
        """Check if a gate is currently in hard enforcement mode."""
        gate = self._gates.get(name)
        return gate.is_promoted if gate else False

    def record_trigger(self, name: str) -> None:
        """Record that a gate's condition was triggered (detected the issue)."""
        gate = self._gates.get(name)
        if gate:
            gate.trigger_count += 1
            self._save()

    def record_false_positive(self, name: str) -> None:
        """Record that a gate fired on something that wasn't actually an issue."""
        gate = self._gates.get(name)
        if gate:
            gate.false_positive_count += 1
            self._save()

    def record_override(self, name: str) -> None:
        """Record that a user overrode (ignored) the gate's enforcement."""
        gate = self._gates.get(name)
        if gate:
            gate.user_overrides += 1
            gate.override_dates.append(date.today().isoformat())
            # Check demotion
            if gate.should_demote():
                gate.demoted_at = datetime.now().isoformat()
                logger.info(
                    "gate_promotion: demoted '%s' (3+ overrides in 7d)", name
                )
            self._save()

    def record_incident(self, name: str, description: str) -> None:
        """Record that the gate prevented a real issue."""
        gate = self._gates.get(name)
        if gate:
            gate.incidents_prevented.append(
                f"{date.today().isoformat()}: {description}"
            )
            self._save()

    def check_promotions(self) -> list[str]:
        """Check all gates for promotion eligibility. Returns names promoted.

        Called by TIMER_30MIN handler. Only promotes gates that meet all criteria.
        """
        promoted: list[str] = []
        for name, gate in self._gates.items():
            if gate.is_promoted:
                continue  # Already promoted
            if gate.meets_promotion_criteria():
                gate.promoted_at = datetime.now().isoformat()
                gate.promotion_eligible = True
                promoted.append(name)
                logger.info(
                    "gate_promotion: promoted '%s' to hard enforcement "
                    "(triggers=%d, fp_rate=%.2f, overrides_14d=%d, incidents=%d)",
                    name, gate.trigger_count, gate.fp_rate,
                    gate.overrides_in_last_14d, len(gate.incidents_prevented),
                )

        if promoted:
            self._save()
        return promoted

    def get_promotion_summary(self) -> dict[str, Any]:
        """Get summary of all gates for session briefing / dashboard."""
        summary: dict[str, Any] = {}
        for name, gate in self._gates.items():
            summary[name] = {
                "status": "hard" if gate.is_promoted else "soft",
                "trigger_count": gate.trigger_count,
                "fp_rate": round(gate.fp_rate, 3),
                "overrides_14d": gate.overrides_in_last_14d,
                "incidents": len(gate.incidents_prevented),
                "eligible": gate.meets_promotion_criteria(),
                "progress": f"{gate.trigger_count}/{MIN_TRIGGERS} triggers",
            }
        return summary
