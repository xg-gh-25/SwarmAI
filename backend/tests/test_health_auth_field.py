"""GET /health exposes an `auth` field (valid|expired|unknown).

Tests call health_check() directly (not via TestClient) to avoid the full
lifespan startup. The auth check is gathered with the DB check under a 1s
cap; timeout/error → unknown; initializing branch hardcodes unknown.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest


def _patch_validator(status):
    fake = type("V", (), {"check": AsyncMock(return_value=status)})()
    return patch("core.session_registry.get_credential_validator", return_value=fake)


@pytest.mark.asyncio
async def test_health_auth_valid():
    import main
    orig = main._startup_complete
    main._startup_complete = True
    try:
        with _patch_validator("valid"):
            data = await main.health_check()
    finally:
        main._startup_complete = orig
    assert data.get("auth") == "valid", data


@pytest.mark.asyncio
async def test_health_auth_expired():
    import main
    orig = main._startup_complete
    main._startup_complete = True
    try:
        with _patch_validator("expired"):
            data = await main.health_check()
    finally:
        main._startup_complete = orig
    assert data.get("auth") == "expired"


@pytest.mark.asyncio
async def test_health_auth_timeout_maps_unknown():
    """check() exceeding the 1s budget → auth='unknown', health unaffected."""
    import main

    async def _slow(_region):
        await asyncio.sleep(5)
        return "valid"

    fake = type("V", (), {"check": _slow})()
    orig = main._startup_complete
    main._startup_complete = True
    try:
        with patch("core.session_registry.get_credential_validator", return_value=fake):
            data = await main.health_check()
    finally:
        main._startup_complete = orig
    assert data.get("auth") == "unknown", data
    assert data.get("status") in ("healthy", "degraded")


@pytest.mark.asyncio
async def test_health_auth_error_maps_unknown():
    """check() raising → auth='unknown' (fail-open, gather return_exceptions)."""
    import main
    fake = type("V", (), {"check": AsyncMock(side_effect=RuntimeError("boom"))})()
    orig = main._startup_complete
    main._startup_complete = True
    try:
        with patch("core.session_registry.get_credential_validator", return_value=fake):
            data = await main.health_check()
    finally:
        main._startup_complete = orig
    assert data.get("auth") == "unknown"


@pytest.mark.asyncio
async def test_health_initializing_branch_auth_unknown():
    """Before startup completes → no STS, auth=unknown."""
    import main
    orig = main._startup_complete
    main._startup_complete = False
    try:
        data = await main.health_check()
    finally:
        main._startup_complete = orig
    assert data.get("status") == "initializing"
    assert data.get("auth") == "unknown"
