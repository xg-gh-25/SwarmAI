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


def _cfg(use_bedrock=True, auth_method="ada"):
    """A fake AppConfigManager.instance() with the given auth mode/method."""
    values = {"aws_region": "us-east-1", "use_bedrock": use_bedrock, "auth_method": auth_method}
    return type("Cfg", (), {"get": lambda self, k, d=None: values.get(k, d)})()


@pytest.mark.asyncio
async def test_preflight_expired_ada_mentions_ada_remediation():
    """Bedrock+ada, check→expired → CREDENTIALS_EXPIRED + _abort, ada-specific fix."""
    unit = _cold_unit()
    fake_validator = type("V", (), {"check": AsyncMock(return_value="expired")})()

    with patch("core.session_registry.get_credential_validator", return_value=fake_validator), \
         patch("core.app_config_manager.AppConfigManager.instance", return_value=_cfg(True, "ada")), \
         patch.object(unit, "_spawn", new=AsyncMock()) as mock_spawn:
        events = await _collect(unit, options=object(), config=None)

    codes = [e.get("code") for e in events]
    assert "CREDENTIALS_EXPIRED" in codes, f"got {events}"
    assert any(e.get("_abort") for e in events), "must abort"
    mock_spawn.assert_not_called(), "must NOT spawn when expired"
    cred_ev = next(e for e in events if e.get("code") == "CREDENTIALS_EXPIRED")
    action = (cred_ev.get("suggested_action") or "").lower()
    assert "ada" in action or "mwinit" in action


@pytest.mark.asyncio
async def test_preflight_expired_sso_does_NOT_say_mwinit():
    """Bedrock+sso, check→expired → remediation says `aws sso login`, NOT mwinit."""
    unit = _cold_unit()
    fake_validator = type("V", (), {"check": AsyncMock(return_value="expired")})()

    with patch("core.session_registry.get_credential_validator", return_value=fake_validator), \
         patch("core.app_config_manager.AppConfigManager.instance", return_value=_cfg(True, "sso")), \
         patch.object(unit, "_spawn", new=AsyncMock()):
        events = await _collect(unit, options=object(), config=None)

    cred_ev = next(e for e in events if e.get("code") == "CREDENTIALS_EXPIRED")
    action = (cred_ev.get("suggested_action") or "").lower()
    assert "aws sso login" in action
    assert "mwinit" not in action


@pytest.mark.asyncio
async def test_preflight_apikey_mode_skips_STS_and_spawns():
    """use_bedrock=false (Anthropic-direct) → NO STS check, NO expired event,
    proceeds to spawn. An API-key user must not get 'AWS credentials expired'."""
    unit = _cold_unit()
    # Even if the validator WOULD say expired, apikey mode must never call it.
    check_mock = AsyncMock(return_value="expired")
    fake_validator = type("V", (), {"check": check_mock})()

    with patch("core.session_registry.get_credential_validator", return_value=fake_validator), \
         patch("core.app_config_manager.AppConfigManager.instance", return_value=_cfg(False, "apikey")), \
         patch.object(unit, "_spawn", new=AsyncMock()) as mock_spawn:
        events = await _collect(unit, options=object(), config=None)

    assert not any(e.get("code") == "CREDENTIALS_EXPIRED" for e in events)
    check_mock.assert_not_called(), "apikey mode must NOT run the AWS STS check"
    mock_spawn.assert_called_once()


@pytest.mark.asyncio
async def test_preflight_unknown_proceeds_to_spawn():
    """check→unknown → fail-open: _spawn IS called (behavior == today)."""
    unit = _cold_unit()
    fake_validator = type("V", (), {"check": AsyncMock(return_value="unknown")})()

    with patch("core.session_registry.get_credential_validator", return_value=fake_validator), \
         patch("core.app_config_manager.AppConfigManager.instance", return_value=_cfg(True, "sso")), \
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
         patch("core.app_config_manager.AppConfigManager.instance", return_value=_cfg(True, "sso")), \
         patch.object(unit, "_spawn", new=AsyncMock()) as mock_spawn:
        events = await _collect(unit, options=object(), config=None)

    assert not any(e.get("code") == "CREDENTIALS_EXPIRED" for e in events)
    mock_spawn.assert_called_once()
