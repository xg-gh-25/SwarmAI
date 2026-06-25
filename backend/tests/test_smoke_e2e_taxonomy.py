"""Unit tests for smoke_e2e.py OT03 additions: SKIPPED status taxonomy +
content-shape text extraction.

WHAT IS TESTED
--------------
1. SmokeResult.skip() records a SKIPPED check that does NOT flip exit code to
   failure — the core OT03 guarantee: a saturated (busy) daemon is healthy,
   not broken, and must never false-red.
2. _extract_event_text() pulls assistant text from both event shapes (list of
   blocks, plain string, top-level text) so the content-shape check sees real
   content.

These are the pure, unit-testable cores of the smoke script. The script itself
is an integration probe (run live against a daemon); these guard its logic.

MOTIVATION
----------
- OT03: chat_stream falsely timed-out red when the daemon was at the R6
  MAX_CONCURRENT_STREAMS cap. skip() is the fix; this test pins that a skip
  never fails the run.
- PIT49: status taxonomy (pass/fail/skip) prevents the false-negative.
"""

import sys
from pathlib import Path

import pytest

# smoke_e2e lives in repo-root scripts/, not backend/ — add it to path.
_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from smoke_e2e import SmokeResult, _extract_event_text, _check_no_stuck_streaming  # noqa: E402


# ─── SKIPPED taxonomy ─────────────────────────────────────────────


def test_skip_does_not_fail_the_run():
    """A skipped check must NOT flip all_passed to False (busy != broken)."""
    r = SmokeResult()
    r.record("health", True, "ok")
    r.skip("chat_stream", "daemon saturated 3/3")
    assert r.all_passed is True  # the skip must not fail the run


def test_real_failure_still_fails_the_run():
    """A genuine fail still fails — skip leniency must not mask real failures."""
    r = SmokeResult()
    r.record("health", True)
    r.skip("chat_stream", "saturated")
    r.record("sessions_list", False, "500")
    assert r.all_passed is False


def test_summary_counts_pass_fail_skip_separately():
    r = SmokeResult()
    r.record("a", True)
    r.record("b", False)
    r.skip("c", "busy")
    s = r.summary
    assert "1/3 passed" in s
    assert "1 failed" in s
    assert "1 skipped" in s


def test_skip_records_skip_status():
    r = SmokeResult()
    r.skip("chat_stream", "saturated")
    name, status, detail = r.results[0]
    assert name == "chat_stream"
    assert status == "skip"
    assert "saturated" in detail


def test_all_pass_no_skip_no_fail():
    r = SmokeResult()
    r.record("a", True)
    r.record("b", True)
    assert r.all_passed is True
    assert "2/2 passed" in r.summary


# ─── content-shape text extraction ────────────────────────────────


def test_extract_text_from_block_list():
    evt = {
        "type": "assistant",
        "content": [
            {"type": "text", "text": "Hello "},
            {"type": "tool_use", "name": "Bash"},  # no text — contributes nothing
            {"type": "text", "text": "world"},
        ],
    }
    assert _extract_event_text(evt) == "Hello world"


def test_extract_text_from_plain_string_content():
    evt = {"type": "assistant", "content": "plain reply"}
    assert _extract_event_text(evt) == "plain reply"


def test_extract_text_from_top_level_text_delta():
    evt = {"type": "text_delta", "text": "chunk"}
    assert _extract_event_text(evt) == "chunk"


def test_extract_text_empty_when_no_text():
    evt = {"type": "assistant", "content": [{"type": "tool_use", "name": "Read"}]}
    assert _extract_event_text(evt) == ""


def test_extract_text_handles_missing_content():
    assert _extract_event_text({"type": "result"}) == ""


# ─── Q1: wedge detection must NOT be masked by saturation ─────────


@pytest.mark.asyncio
async def test_stuck_streaming_probe_fails_on_stalled():
    """The wedge probe must FAIL (not pass/skip) when admission-state reports
    stalled streaming sessions — the OT01 wedge the saturation-skip would
    otherwise mask (adversarial Q1)."""
    import httpx
    from unittest.mock import patch, AsyncMock, MagicMock

    fake_resp = MagicMock()
    fake_resp.json.return_value = {"stalled_streaming": 2, "saturated": True}
    fake_client = AsyncMock()
    fake_client.get = AsyncMock(return_value=fake_resp)
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)

    r = SmokeResult()
    with patch.object(httpx, "AsyncClient", return_value=fake_client):
        await _check_no_stuck_streaming("http://x", r)

    name, status, detail = r.results[-1]
    assert name == "no_stuck_streaming"
    assert status == "fail"  # stalled streams = wedge = fail
    assert r.all_passed is False


@pytest.mark.asyncio
async def test_stuck_streaming_probe_passes_when_none_stalled():
    """No stalled streams (legitimately busy or idle) → probe passes."""
    import httpx
    from unittest.mock import patch, AsyncMock, MagicMock

    fake_resp = MagicMock()
    fake_resp.json.return_value = {"stalled_streaming": 0, "saturated": True}
    fake_client = AsyncMock()
    fake_client.get = AsyncMock(return_value=fake_resp)
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)

    r = SmokeResult()
    with patch.object(httpx, "AsyncClient", return_value=fake_client):
        await _check_no_stuck_streaming("http://x", r)

    name, status, _ = r.results[-1]
    assert name == "no_stuck_streaming"
    assert status == "pass"
    assert r.all_passed is True
