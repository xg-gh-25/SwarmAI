"""DDD Cultivation Orchestrator — extracted from context_health_hook.py God Object.

Orchestrates 7 independent DDD feed channels. Each channel runs in its own
try/except — one crash never affects others. Returns merged findings list.

Channels:
    1. DDD staleness detection (context_health_hook._check_ddd_staleness)
    2. Auto-apply mechanical proposals (context_health_hook._auto_apply_ddd_proposals)
    3. DDD→KNOWLEDGE injection (context_health_hook._inject_ddd_into_knowledge)
    4. Knowledge staleness detection (context_health_hook._detect_knowledge_staleness)
    5. Entity index validation (context_health_hook._validate_entity_index)
    6. Signal→DDD bridge (hooks.signal_ddd_bridge)
    7. Code Intelligence drift (core.code_intel_feed)

Public symbols:
    - DddCultivationOrchestrator  — main orchestrator class
"""
import logging
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

# Type alias for channel functions
ChannelFn = Callable[[Path, str], list[str]]


class DddCultivationOrchestrator:
    """Orchestrates DDD Cultivation feed channels with fault isolation.

    Each channel is a (name, callable) pair. The callable receives (root, ws_path)
    and returns a list of findings (strings). If a channel raises, the error is
    logged and captured as a finding — other channels continue unaffected.
    """

    def __init__(self) -> None:
        self.channels: list[tuple[str, ChannelFn]] = [
            ("ddd_staleness", self._ch_ddd_staleness),
            ("auto_apply_proposals", self._ch_auto_apply),
            ("ddd_knowledge_injection", self._ch_inject_knowledge),
            ("knowledge_staleness", self._ch_knowledge_staleness),
            ("entity_index_validation", self._ch_entity_index),
            ("signal_ddd_bridge", self._ch_signal_bridge),
            ("code_intel_drift", self._ch_code_intel),
        ]

    def run(self, root: Path, ws_path: str) -> list[str]:
        """Execute all channels, return merged findings.

        Each channel runs independently. Failures are captured as findings
        (not re-raised). Returns all findings from all successful channels
        plus error notices from failed ones.
        """
        all_findings: list[str] = []

        for name, channel_fn in self.channels:
            try:
                findings = channel_fn(root, ws_path)
                if findings:
                    all_findings.extend(findings)
            except Exception as exc:
                # Capture error as finding — never let one channel kill others
                logger.warning(
                    "ddd_orchestrator: channel '%s' failed (non-blocking): %s",
                    name, exc,
                )
                all_findings.append(
                    f"CHANNEL_ERROR: {name} — {type(exc).__name__}: {exc}"
                )

        return all_findings

    # ── Channel implementations (delegate to existing code) ──────────────

    def _ch_ddd_staleness(self, root: Path, ws_path: str) -> list[str]:
        """Channel 1: Check DDD document staleness."""
        from hooks.context_health_hook import ContextHealthHook

        hook = ContextHealthHook()
        return hook._check_ddd_staleness(root, ws_path)

    def _ch_auto_apply(self, root: Path, ws_path: str) -> list[str]:
        """Channel 2: Auto-apply mechanical DDD refresh proposals."""
        from hooks.context_health_hook import ContextHealthHook

        hook = ContextHealthHook()
        hook._auto_apply_ddd_proposals(root)
        return []  # Side-effect only, no findings

    def _ch_inject_knowledge(self, root: Path, ws_path: str) -> list[str]:
        """Channel 3: Inject Active Projects & DDD section into KNOWLEDGE.md."""
        from hooks.context_health_hook import ContextHealthHook

        hook = ContextHealthHook()
        hook._inject_ddd_into_knowledge(root)
        return []  # Side-effect only, no findings

    def _ch_knowledge_staleness(self, root: Path, ws_path: str) -> list[str]:
        """Channel 4: Detect when backend code changed but KNOWLEDGE.md didn't."""
        from hooks.context_health_hook import ContextHealthHook

        hook = ContextHealthHook()
        return hook._detect_knowledge_staleness(root, ws_path)

    def _ch_entity_index(self, root: Path, ws_path: str) -> list[str]:
        """Channel 5: Validate entity index stale references."""
        from hooks.context_health_hook import ContextHealthHook

        hook = ContextHealthHook()
        return hook._validate_entity_index(root)

    def _ch_signal_bridge(self, root: Path, ws_path: str) -> list[str]:
        """Channel 6: Signal→DDD bridge (high-relevance signals → proposals)."""
        from hooks.signal_ddd_bridge import bridge_signals_to_ddd

        proposal_count = bridge_signals_to_ddd(ws_path)
        if proposal_count > 0:
            logger.info(
                "ddd_orchestrator: signal bridge generated %d proposals",
                proposal_count,
            )
        return []

    def _ch_code_intel(self, root: Path, ws_path: str) -> list[str]:
        """Channel 7: Code Intelligence drift → TECH.md proposals."""
        from core.code_intel_feed import detect_tech_drift

        drift_count = detect_tech_drift(ws_path)
        if drift_count > 0:
            logger.info(
                "ddd_orchestrator: code intel drift generated %d proposals",
                drift_count,
            )
        return []
