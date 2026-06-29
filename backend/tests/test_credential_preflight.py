"""Pre-flight credential check in SessionUnit._ensure_spawned (Gate-1 fix).

The bug: when AWS credentials are expired, a cold spawn proceeds into the CLI
subprocess which stalls retrying the failing credential_process (spinner forever).

The fix: _ensure_spawned pre-flights get_credential_validator().check(region)
BEFORE spawning. On "expired" → emit CREDENTIALS_EXPIRED SSE error + _abort
(no spawn). On "unknown"/"valid" → fall through to spawn (fail-open: behavior
identical to today on any non-definitive signal).
"""
from unittest.mock import AsyncMock, patch

import pytest

from core.session_unit import SessionUnit, SessionState


def _cold_unit(session_id: str = "test-cred-preflight") -> SessionUnit:
    unit = SessionUnit(session_id=session_id, agent_id="default")
    # default state is COLD — that's what triggers _ensure_spawned
    return unit


async def _collect(unit, options=None, config=None):
    events = []
    async for ev in unit._ensure_spawned(options, config):
        events.append(ev)
    return events


@pytest.mark.asyncio
async def test_preflight_expired_emits_credentials_expired_and_aborts():
    """check→expired → CREDENTIALS_EXPIRED SSE event + _abort, NO _spawn."""
    unit = _cold_unit()
    fake_validator = type("V", (), {"check": AsyncMock(return_value="expired")})()

    with patch("core.session_registry.get_credential_validator", return_value=fake_validator), \
         patch.object(unit, "_spawn", new=AsyncMock()) as mock_spawn:
        events = await _collect(unit, options=object(), config=None)

    codes = [e.get("code") for e in events]
    assert "CREDENTIALS_EXPIRED" in codes, f"got {events}"
    assert any(e.get("_abort") for e in events), "must abort"
    mock_spawn.assert_not_called(), "must NOT spawn when expired"
    # the error event must carry an actionable mwinit hint
    cred_ev = next(e for e in events if e.get("code") == "CREDENTIALS_EXPIRED")
    assert "mwinit" in (cred_ev.get("suggested_action") or "").lower()


@pytest.mark.asyncio
async def test_preflight_unknown_proceeds_to_spawn():
    """check→unknown → fail-open: _spawn IS called (behavior == today)."""
    unit = _cold_unit()
    fake_validator = type("V", (), {"check": AsyncMock(return_value="unknown")})()

    with patch("core.session_registry.get_credential_validator", return_value=fake_validator), \
         patch.object(unit, "_spawn", new=AsyncMock()) as mock_spawn:
        events = await _collect(unit, options=object(), config=None)

    assert not any(e.get("code") == "CREDENTIALS_EXPIRED" for e in events)
    mock_spawn.assert_called_once()


@pytest.mark.asyncio
async def test_preflight_valid_proceeds_to_spawn():
    """check→valid → _spawn IS called, no credential error."""
    unit = _cold_unit()
    fake_validator = type("V", (), {"check": AsyncMock(return_value="valid")})()

    with patch("core.session_registry.get_credential_validator", return_value=fake_validator), \
         patch.object(unit, "_spawn", new=AsyncMock()) as mock_spawn:
        events = await _collect(unit, options=object(), config=None)

    assert not any(e.get("code") == "CREDENTIALS_EXPIRED" for e in events)
    mock_spawn.assert_called_once()
