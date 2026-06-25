"""R6 Step C — orphan-only eviction (cross-tab eviction structurally impossible).

design §9.9 (amended §9.8): "no eviction of a *client-owned* session; orphan GC
is permitted and required." _evict_idle's chat path now filters candidates to
sessions owned by NO live window (not in open_tabs.json). Effects:
- A window-owned IDLE tab is NEVER evicted — even with force=True (the old "sole
  anti-starvation force-kill" can no longer kill a user's tab).
- An ORPHAN (unowned) IDLE session IS still evictable → anti-starvation preserved.
- Ownership unknowable (open_tabs unreadable → None) → evict NOTHING (fail-safe).
- Channel eviction (channel_only=True) is exempt from the orphan filter.
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.session_unit import SessionState, SessionUnit
from core.session_router import SessionRouter


def _make_router() -> SessionRouter:
    r = SessionRouter(prompt_builder=MagicMock(), config=MagicMock())
    r._lifecycle_manager = None  # skip hook firing
    return r


def _add_idle(router: SessionRouter, session_id: str, *, idle_age: float = 9999,
              is_channel: bool = False) -> SessionUnit:
    unit = SessionUnit(session_id=session_id, agent_id="default")
    unit._transition(SessionState.IDLE)
    unit.is_channel_session = is_channel
    unit.last_used = time.time() - idle_age  # well past grace by default
    unit._hooks_enqueued = True
    unit.kill = AsyncMock()
    router._units[session_id] = unit
    return unit


def _other(router: SessionRouter) -> SessionUnit:
    """A throwaway 'requesting' unit to exclude from eviction."""
    u = SessionUnit(session_id="requester", agent_id="default")
    return u


async def _evict(router, exclude, *, owned, force=False, channel_only=False):
    with patch("routers.settings.owned_session_ids", return_value=owned):
        return await router._evict_idle(exclude=exclude, force=force,
                                        channel_only=channel_only)


@pytest.mark.asyncio
async def test_owned_tab_is_never_evicted_even_with_force():
    """The core isolation guarantee: a window-owned tab survives force=True."""
    router = _make_router()
    owned_tab = _add_idle(router, "owned-1")
    result = await _evict(router, _other(router), owned={"owned-1"}, force=True)
    assert result is False
    owned_tab.kill.assert_not_awaited()


@pytest.mark.asyncio
async def test_orphan_is_still_evictable():
    """Anti-starvation preserved: an unowned (orphan) idle session is evicted."""
    router = _make_router()
    orphan = _add_idle(router, "orphan-1")
    result = await _evict(router, _other(router), owned=set())  # no window owns it
    assert result is True
    orphan.kill.assert_awaited_once()


@pytest.mark.asyncio
async def test_mixed_only_orphan_evicted_owned_survives():
    router = _make_router()
    owned_tab = _add_idle(router, "owned-1")
    orphan = _add_idle(router, "orphan-1")
    result = await _evict(router, _other(router), owned={"owned-1"})
    assert result is True
    orphan.kill.assert_awaited_once()
    owned_tab.kill.assert_not_awaited()


@pytest.mark.asyncio
async def test_all_owned_refuses_eviction():
    """When every idle chat session is window-owned, eviction refuses → queue."""
    router = _make_router()
    a = _add_idle(router, "owned-1")
    b = _add_idle(router, "owned-2")
    result = await _evict(router, _other(router), owned={"owned-1", "owned-2"}, force=True)
    assert result is False
    a.kill.assert_not_awaited()
    b.kill.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknowable_ownership_refuses_eviction():
    """Fail-safe: open_tabs unreadable (None) → evict NOTHING (don't guess)."""
    router = _make_router()
    would_be = _add_idle(router, "x-1")
    result = await _evict(router, _other(router), owned=None, force=True)
    assert result is False
    would_be.kill.assert_not_awaited()


@pytest.mark.asyncio
async def test_channel_eviction_exempt_from_orphan_filter():
    """Channel pool (1-slot, no window) is not subject to the orphan filter."""
    router = _make_router()
    chan = _add_idle(router, "chan-1", is_channel=True)
    # Even though no open_tabs entry exists for it, channel_only eviction proceeds.
    result = await _evict(router, _other(router), owned=set(), channel_only=True)
    assert result is True
    chan.kill.assert_awaited_once()


@pytest.mark.asyncio
async def test_orphan_within_grace_still_protected_without_force():
    """Grace still applies AMONG orphans: a freshly-idle orphan needs force."""
    router = _make_router()
    fresh_orphan = _add_idle(router, "orphan-fresh", idle_age=5)  # within grace
    # Not forced → grace protects even an orphan.
    result = await _evict(router, _other(router), owned=set(), force=False)
    assert result is False
    fresh_orphan.kill.assert_not_awaited()
    # Forced → grace bypassed among orphans.
    result2 = await _evict(router, _other(router), owned=set(), force=True)
    assert result2 is True
    fresh_orphan.kill.assert_awaited_once()
