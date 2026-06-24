"""R6a — Session resource arbitration: decouple backend from the UX ceiling.

Tests that backend spawn/resume decisions are gated SOLELY by
``spawn_budget`` (real RAM), not by ``compute_max_tabs`` (a frontend UX
constant). See Knowledge/Designs/2026-06-24-session-lifecycle-unified-recovery-design.md
§9 and run_6ea35431.

Acceptance criteria covered:
- AC1: ``compute_max_tabs`` has 0 references in session_router.py + retry_manager.py
- AC2: ``compute_max_tabs`` has exactly 1 non-test consumer (system.py)
- AC3: a crashed tab resumes regardless of peer ``alive_count`` when RAM allows
        (was REFUSED by the ``alive >= max_tabs`` ceiling guard)
- AC4: removing the ceiling does NOT weaken OOM protection — ``spawn_budget``
        still DENIES on a memory-constrained machine (the COE05 floor survives)
- AC5: N idle sessions on a memory-abundant machine coexist with zero eviction
- AC6: first-tab-sacred + budget-as-sole-gate (no budget-free spawn path)
"""
import re
from pathlib import Path

import pytest

from core.resource_monitor import SpawnBudget
from core.session_router import SessionRouter
from core.session_unit import SessionUnit, SessionState

_BACKEND = Path(__file__).resolve().parent.parent
_ROUTER_SRC = _BACKEND / "core" / "session_router.py"
_RETRY_SRC = _BACKEND / "core" / "retry_manager.py"


def _code_lines(path: Path) -> list[str]:
    """Return source lines with the module docstring stripped.

    AC1 allows ``compute_max_tabs`` to appear in the module docstring header
    (historical reference) but NOT in executable code.
    """
    text = path.read_text()
    # Drop the leading module docstring (first triple-quoted block).
    m = re.match(r'\s*(?:r?"""|r?\'\'\')', text)
    if m:
        # find the closing triple-quote
        q = text[m.end() - 3 : m.end()]
        close = text.index(q, m.end())
        text = text[close + 3 :]
    return text.splitlines()


def _count_calls(path: Path) -> int:
    """Count executable (non-comment, non-docstring) compute_max_tabs refs."""
    n = 0
    for line in _code_lines(path):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "compute_max_tabs" in line:
            n += 1
    return n


# ---------------------------------------------------------------------------
# AC1: backend no longer consults the UX ceiling
# ---------------------------------------------------------------------------

class TestCeilingRemovedFromBackend:
    def test_session_router_has_no_compute_max_tabs(self):
        """session_router.py executes 0 compute_max_tabs calls (AC1)."""
        assert _count_calls(_ROUTER_SRC) == 0, (
            "session_router.py still references compute_max_tabs in executable "
            "code — backend spawn/eviction must be gated by spawn_budget, not "
            "the UX ceiling."
        )

    def test_retry_manager_has_no_compute_max_tabs(self):
        """retry_manager.py executes 0 compute_max_tabs calls (AC1)."""
        assert _count_calls(_RETRY_SRC) == 0, (
            "retry_manager.py still references compute_max_tabs — a crashed tab "
            "must resume based on RAM (spawn_budget), not peer count."
        )


# ---------------------------------------------------------------------------
# AC2: compute_max_tabs has exactly one legitimate (UX) consumer
# ---------------------------------------------------------------------------

class TestComputeMaxTabsSingleConsumer:
    def test_only_system_router_consumes_compute_max_tabs(self):
        """Exactly 1 non-test, non-definition consumer: routers/system.py (AC2)."""
        consumers: set[str] = set()
        for py in _BACKEND.rglob("*.py"):
            rel = py.relative_to(_BACKEND).as_posix()
            if "/tests/" in f"/{rel}" or rel.startswith("tests/"):
                continue
            if py.name == "resource_monitor.py":
                continue  # definition site
            for line in _code_lines(py):
                s = line.strip()
                if s.startswith("#"):
                    continue
                if "compute_max_tabs" in line:
                    consumers.add(rel)
                    break
        assert consumers == {"routers/system.py"}, (
            f"Expected compute_max_tabs consumed ONLY by routers/system.py "
            f"(the /api/max-tabs UX endpoint), got: {sorted(consumers)}"
        )


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _make_router():
    from unittest.mock import MagicMock
    return SessionRouter(prompt_builder=MagicMock())


def _add_unit(router, sid, state):
    unit = SessionUnit(
        session_id=sid,
        agent_id="test-agent",
        on_state_change=router._on_unit_state_change,
    )
    unit.state = state
    router._units[sid] = unit
    return unit


def _budget(can_spawn: bool, reason: str = "ok"):
    return SpawnBudget(
        can_spawn=can_spawn,
        reason=reason,
        available_mb=8000.0 if can_spawn else 100.0,
        estimated_cost_mb=600.0,
    )


# ---------------------------------------------------------------------------
# AC3: resume is not refused by peer count when RAM allows
# ---------------------------------------------------------------------------

class TestResumeNotRefusedByPeerCount:
    @pytest.mark.asyncio
    async def test_acquire_succeeds_with_many_peers_when_ram_ok(self):
        """With 4 alive peers but RAM available, a new acquire is NOT blocked
        by a count ceiling — spawn_budget(can_spawn=True) grants it (AC3).

        Pre-R6a this queued+timed-out because chat_max=3 < 4 alive.
        """
        from unittest.mock import patch
        router = _make_router()
        # 4 alive peers, all IDLE (evictable in principle, but we assert no
        # eviction is needed because RAM permits a fresh slot).
        for i in range(4):
            _add_unit(router, f"peer-{i}", SessionState.IDLE)
        requesting = _add_unit(router, "requesting", SessionState.COLD)

        with patch("core.resource_monitor.resource_monitor") as mock_rm:
            mock_rm.spawn_budget.return_value = _budget(True)
            mock_rm.invalidate_cache.return_value = None
            result = await router._acquire_slot(requesting)

        assert result == "ready", (
            f"Expected 'ready' (RAM permits) with 4 alive peers, got '{result}'. "
            f"Backend must not refuse on peer count when spawn_budget allows."
        )


# ---------------------------------------------------------------------------
# AC4: OOM protection survives at the budget layer
# ---------------------------------------------------------------------------

class TestBudgetStillProtectsOOM:
    @pytest.mark.asyncio
    async def test_budget_denied_does_not_blindly_spawn(self):
        """When spawn_budget denies AND no idle peer is evictable, acquire must
        NOT return 'ready' — it queues/timeouts (AC4: ceiling removal does not
        open a budget-free spawn path)."""
        from unittest.mock import patch
        router = _make_router()
        router.QUEUE_TIMEOUT = 0.01
        # one alive STREAMING peer (protected, NOT evictable) so eviction can't rescue
        _add_unit(router, "streamer", SessionState.STREAMING)
        requesting = _add_unit(router, "requesting", SessionState.COLD)

        with patch("core.resource_monitor.resource_monitor") as mock_rm:
            mock_rm.spawn_budget.return_value = _budget(False, "memory at 92%")
            mock_rm.invalidate_cache.return_value = None
            result = await router._acquire_slot(requesting)

        assert result == "timeout", (
            f"Expected 'timeout' when budget denies + no evictable idle, got "
            f"'{result}'. Removing the ceiling must NOT create a budget-free "
            f"spawn path (COE05 floor)."
        )


# ---------------------------------------------------------------------------
# AC5: all-idle on a memory-abundant machine — zero eviction
# ---------------------------------------------------------------------------

class TestNoEvictionWhenRamAbundant:
    @pytest.mark.asyncio
    async def test_no_eviction_when_budget_permits(self):
        """3 idle chat peers + RAM available → new acquire grants WITHOUT
        calling _evict_idle (AC5: capacity follows RAM, no peer killed)."""
        from unittest.mock import patch, AsyncMock
        router = _make_router()
        for i in range(3):
            _add_unit(router, f"idle-{i}", SessionState.IDLE)
        requesting = _add_unit(router, "requesting", SessionState.COLD)

        with patch("core.resource_monitor.resource_monitor") as mock_rm, \
             patch.object(router, "_evict_idle", new=AsyncMock(return_value=True)) as spy_evict:
            mock_rm.spawn_budget.return_value = _budget(True)
            mock_rm.invalidate_cache.return_value = None
            result = await router._acquire_slot(requesting)

        assert result == "ready", f"Expected 'ready', got '{result}'"
        spy_evict.assert_not_called(), (
            "RAM permits a fresh slot — no peer tab should be evicted (AC5)."
        )


# ---------------------------------------------------------------------------
# AC6: first-tab-sacred preserved
# ---------------------------------------------------------------------------

class TestFirstTabSacred:
    @pytest.mark.asyncio
    async def test_first_tab_allowed_even_if_budget_denies(self):
        """With 0 alive sessions, the first tab is always granted regardless of
        budget (AC6 first-tab-sacred exception preserved)."""
        from unittest.mock import patch
        router = _make_router()
        requesting = _add_unit(router, "first", SessionState.COLD)

        with patch("core.resource_monitor.resource_monitor") as mock_rm:
            # Even a denying budget must not block the very first tab.
            mock_rm.spawn_budget.return_value = _budget(False, "pessimistic")
            mock_rm.invalidate_cache.return_value = None
            result = await router._acquire_slot(requesting)

        assert result == "ready", (
            f"First chat tab must always be granted, got '{result}'"
        )

    @pytest.mark.asyncio
    async def test_first_chat_tab_sacred_even_with_live_channel(self):
        """REVIEW 4.1: a lone alive CHANNEL session must NOT subject the first
        CHAT tab to budget denial. First-tab-sacred is keyed on the CHAT pool
        (_chat_alive_count == 0), mirroring the channel slot's own first-tab
        rule — otherwise _evict_idle (chat-scoped) can't rescue the chat tab
        from a channel-induced budget denial and it queues to timeout."""
        from unittest.mock import patch
        router = _make_router()
        # A channel session is alive (background listener).
        chan = _add_unit(router, "channel", SessionState.IDLE)
        chan.is_channel_session = True
        # First CHAT tab requests a slot.
        requesting = _add_unit(router, "first-chat", SessionState.COLD)

        with patch("core.resource_monitor.resource_monitor") as mock_rm:
            # Budget DENIES (memory pressure). Without pool-scoped first-tab,
            # the chat tab would queue→timeout because the channel can't be
            # evicted by the chat-scoped _evict_idle.
            mock_rm.spawn_budget.return_value = _budget(False, "memory at 91%")
            mock_rm.invalidate_cache.return_value = None
            result = await router._acquire_slot(requesting)

        assert result == "ready", (
            f"First CHAT tab must be granted even with a live channel and a "
            f"denying budget (_chat_alive_count==0), got '{result}'"
        )
