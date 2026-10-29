"""Tests for the /api/chat/sessions/admission-state endpoint (OT03).

WHAT IS TESTED
--------------
The admission-state endpoint tells a smoke probe (and a Host dispatching to a
Hive) whether a new turn would queue right now — WITHOUT starting a stream itself.
A new turn queues only while memory is under critical pressure AND another stream
is in flight; there is no count cap. ``saturated`` must be exactly
``session_unit.stream_admission_blocked()``, the predicate send() waits on.

METHODOLOGY
-----------
Patch the module-level _streaming_count and the memory reading
(resource_monitor.system_memory) and assert saturated / memory fields follow
them. Any number of streams with memory not under pressure is not saturated;
critical pressure with streams in flight is.

MOTIVATION
----------
- OT03: smoke_e2e.py chat_stream falsely timed out (red) when the daemon was
  merely busy.
- A fixed cap of 4 streams queued the 5th turn for 120 s at ~60% memory and then
  let it through anyway; admission now follows real memory pressure.
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from core.resource_monitor import SystemMemory, resource_monitor


def _mem(percent: float, measured: bool = True) -> SystemMemory:
    total = 36 * 1024**3
    return SystemMemory(total=total, available=int(total * (100 - percent) / 100),
                        used=int(total * percent / 100), percent_used=percent,
                        measured=measured)


@pytest.fixture
def client():
    # Import main INSIDE the fixture, not at module level: a collection-time
    # import runs main's module-level logging setup before conftest's per-test
    # SWARM_DATA_DIR sandbox exists, attaching a file handler to the LIVE
    # ~/.swarm-ai/logs/backend-daemon.log.
    from main import app
    return TestClient(app)


def _state(client, streaming: int, mem: SystemMemory) -> dict:
    with patch("core.session_unit._streaming_count", streaming), \
         patch.object(resource_monitor, "system_memory", return_value=mem):
        r = client.get("/api/chat/sessions/admission-state")
    assert r.status_code == 200
    return r.json()


def test_admission_state_zero_streaming_not_saturated(client):
    """No streaming sessions → not saturated, even at critical memory (waiting
    could free nothing)."""
    d = _state(client, 0, _mem(95.0))
    assert d["streaming_count"] == 0
    assert d["saturated"] is False


def test_many_streams_without_memory_pressure_not_saturated(client):
    """The old cap (4) and well past it: no memory pressure → a new turn starts."""
    d = _state(client, 9, _mem(60.0))
    assert d["streaming_count"] == 9
    assert d["saturated"] is False
    assert d["memory_percent"] == 60.0
    assert d["memory_pressure"] == "ok"
    assert "max_concurrent" not in d


def test_warning_pressure_not_saturated(client):
    d = _state(client, 5, _mem(85.0))
    assert d["saturated"] is False
    assert d["memory_pressure"] == "warning"


def test_critical_pressure_with_streams_is_saturated(client):
    """Critical memory pressure with a stream in flight → a new turn queues."""
    d = _state(client, 1, _mem(92.0))
    assert d["saturated"] is True
    assert d["memory_pressure"] == "critical"


def test_unreadable_memory_is_not_pressure(client):
    """system_memory()'s pessimistic stand-in (measured=False, 90%) must not
    queue turns, and the endpoint reports the memory as unknown."""
    d = _state(client, 3, _mem(90.0, measured=False))
    assert d["saturated"] is False
    assert d["memory_percent"] is None
    assert d["memory_pressure"] is None


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
