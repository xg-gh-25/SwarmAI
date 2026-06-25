"""Tests for the /api/chat/sessions/admission-state endpoint (OT03).

WHAT IS TESTED
--------------
The admission-state endpoint exposes the daemon-wide concurrent-streaming
count + cap so a smoke probe can distinguish SATURATED (busy, queue a new
turn) from a broken daemon — WITHOUT consuming a streaming slot itself.

The endpoint must reflect the REAL module globals (_streaming_count) and the
REAL cap (SessionUnit.MAX_CONCURRENT_STREAMS), never hardcoded values, and the
`saturated` flag must flip exactly at the cap boundary.

METHODOLOGY
-----------
Patch the module-level _streaming_count global to simulate 0 / cap-1 / cap
streaming sessions and assert the endpoint's count/cap/saturated reflect it.
This is a forcing test: it would fail if the endpoint returned a hardcoded
number or computed saturation off the wrong threshold.

MOTIVATION
----------
- OT03: smoke_e2e.py chat_stream falsely timed out (red) when the daemon was
  merely at the R6 MAX_CONCURRENT_STREAMS cap (busy, not broken).
- PIT49: a status taxonomy that distinguishes busy from broken prevents the
  false-negative this endpoint exists to enable.
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from main import app
from core.session_unit import SessionUnit


CAP = SessionUnit.MAX_CONCURRENT_STREAMS


@pytest.fixture
def client():
    return TestClient(app)


def test_admission_state_zero_streaming_not_saturated(client):
    """No streaming sessions → count 0, not saturated."""
    with patch("core.session_unit._streaming_count", 0):
        r = client.get("/api/chat/sessions/admission-state")
    assert r.status_code == 200
    d = r.json()
    assert d["streaming_count"] == 0
    assert d["max_concurrent"] == CAP
    assert d["saturated"] is False


def test_admission_state_below_cap_not_saturated(client):
    """cap-1 streaming → one slot free, not saturated."""
    if CAP < 1:
        pytest.skip("cap must be >= 1 for this boundary")
    with patch("core.session_unit._streaming_count", CAP - 1):
        r = client.get("/api/chat/sessions/admission-state")
    d = r.json()
    assert d["streaming_count"] == CAP - 1
    assert d["saturated"] is False


def test_admission_state_at_cap_is_saturated(client):
    """Exactly at cap → saturated True (the boundary that queues a new turn)."""
    with patch("core.session_unit._streaming_count", CAP):
        r = client.get("/api/chat/sessions/admission-state")
    d = r.json()
    assert d["streaming_count"] == CAP
    assert d["max_concurrent"] == CAP
    assert d["saturated"] is True


def test_admission_state_over_cap_is_saturated(client):
    """Above cap (transient race) → still saturated, count reported truthfully."""
    with patch("core.session_unit._streaming_count", CAP + 2):
        r = client.get("/api/chat/sessions/admission-state")
    d = r.json()
    assert d["streaming_count"] == CAP + 2
    assert d["saturated"] is True


def test_admission_state_cap_reflects_real_constant_not_hardcoded(client):
    """max_concurrent must echo SessionUnit.MAX_CONCURRENT_STREAMS — patch the
    class attr and confirm the endpoint follows it (no hardcoded literal)."""
    with patch.object(SessionUnit, "MAX_CONCURRENT_STREAMS", 99):
        with patch("core.session_unit._streaming_count", 50):
            r = client.get("/api/chat/sessions/admission-state")
    d = r.json()
    assert d["max_concurrent"] == 99
    assert d["saturated"] is False  # 50 < 99


def test_admission_state_is_read_only_no_slot_consumed(client):
    """The probe must NOT consume a streaming slot: calling it leaves
    _streaming_count unchanged."""
    import core.session_unit as su
    before = su._streaming_count
    client.get("/api/chat/sessions/admission-state")
    assert su._streaming_count == before


def test_admission_state_exposes_stalled_and_idle_fields(client):
    """Payload must expose stalled_streaming (wedge discriminator) +
    idle_live_units (renamed from misleading 'queued')."""
    r = client.get("/api/chat/sessions/admission-state")
    d = r.json()
    assert "stalled_streaming" in d
    assert "idle_live_units" in d
    assert "queued" not in d  # the misleading name was removed (adversarial Q3)
    assert isinstance(d["stalled_streaming"], int)


def test_admission_state_stalled_counts_wedged_streaming_units(client):
    """stalled_streaming counts STREAMING units whose stall exceeds the
    AUTO_RECOVER_STALL_THRESHOLD — the wedge signal that lets the smoke FAIL
    (not skip) on saturation-by-stall (adversarial Q1)."""
    from unittest.mock import MagicMock, patch
    from core.session_unit import AUTO_RECOVER_STALL_THRESHOLD

    advancing = MagicMock()
    advancing.session_id = "advancing-sess"
    advancing.state.value = "streaming"
    advancing.streaming_stall_seconds = 2.0  # fresh — working

    wedged = MagicMock()
    wedged.session_id = "wedged-sess"
    wedged.state.value = "streaming"
    wedged.streaming_stall_seconds = AUTO_RECOVER_STALL_THRESHOLD + 60  # stalled

    idle = MagicMock()
    idle.session_id = "idle-sess"
    idle.state.value = "idle"

    import routers.chat as chat_mod
    fake_router = MagicMock()
    fake_router.list_units.return_value = [advancing, wedged, idle]
    with patch.object(chat_mod, "_get_router", return_value=fake_router):
        d = client.get("/api/chat/sessions/admission-state").json()

    assert d["stalled_streaming"] == 1   # only the wedged one
    assert d["idle_live_units"] == 1     # only the idle one (not the 2 streaming)
