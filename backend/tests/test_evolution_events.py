"""Tests for evolution SSE event marker extraction in the chat router.

Validates that ``_extract_evolution_events`` correctly parses
``<!-- EVOLUTION_EVENT: {...} -->`` markers embedded in agent text output,
normalises the ``"event"`` field to ``"type"`` for frontend compatibility,
and gracefully handles malformed / missing markers.

Requirements: self-evolution E2E pipeline
"""

import json
import pytest

# Import the private helper directly from the chat module.
from routers.chat import _extract_evolution_events


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _text_msg(text: str) -> dict:
    """Build a minimal message dict with string content."""
    return {"content": text}


def _blocks_msg(text: str) -> dict:
    """Build a message dict with content-blocks format."""
    return {"content": [{"type": "text", "text": text}]}


def _marker(payload: dict) -> str:
    """Wrap a dict as an EVOLUTION_EVENT HTML comment marker."""
    return f"<!-- EVOLUTION_EVENT: {json.dumps(payload)} -->"


# ---------------------------------------------------------------------------
# Tests: basic extraction
# ---------------------------------------------------------------------------


class TestExtractEvolutionEvents:
    """Tests for _extract_evolution_events from chat.py."""

    def test_single_marker_string_content(self):
        """Standard case: one marker in a string content message."""
        payload = {"event": "evolution_start", "data": {"triggerType": "reactive"}}
        msg = _text_msg(f"Some text before {_marker(payload)} and after")
        events = _extract_evolution_events(msg)
        assert len(events) == 1
        assert events[0]["type"] == "evolution_start"
        assert events[0]["data"]["triggerType"] == "reactive"

    def test_single_marker_blocks_content(self):
        """Content-blocks format (list of dicts with type=text)."""
        payload = {"event": "evolution_result", "data": {"outcome": "success"}}
        msg = _blocks_msg(f"prefix {_marker(payload)} suffix")
        events = _extract_evolution_events(msg)
        assert len(events) == 1
        assert events[0]["type"] == "evolution_result"

    def test_text_field_fallback(self):
        """Message with ``text`` field instead of ``content``."""
        payload = {"event": "evolution_stuck_detected", "data": {"detectedSignals": ["repeated_error"]}}
        msg = {"text": f"x {_marker(payload)} y"}
        events = _extract_evolution_events(msg)
        assert len(events) == 1
        assert events[0]["type"] == "evolution_stuck_detected"

    def test_multiple_markers(self):
        """Two markers in one message both get extracted."""
        p1 = {"event": "evolution_start", "data": {"attemptNumber": 1}}
        p2 = {"event": "evolution_result", "data": {"outcome": "failure"}}
        msg = _text_msg(f"{_marker(p1)} middle {_marker(p2)}")
        events = _extract_evolution_events(msg)
        assert len(events) == 2
        assert events[0]["type"] == "evolution_start"
        assert events[1]["type"] == "evolution_result"


# ---------------------------------------------------------------------------
# Tests: field normalisation ("event" → "type")
# ---------------------------------------------------------------------------


class TestFieldNormalisation:
    """Verify the "event" → "type" rename for frontend SSE compatibility."""

    def test_event_key_renamed_to_type(self):
        """The ``event`` key must become ``type`` in the output."""
        payload = {"event": "evolution_help_request", "data": {}}
        events = _extract_evolution_events(_text_msg(_marker(payload)))
        assert "type" in events[0]
        assert "event" not in events[0], "Original 'event' key should be removed"

    def test_data_field_preserved(self):
        """The ``data`` sub-dict must pass through untouched."""
        inner = {"triggerType": "proactive", "description": "better approach", "strategySelected": "optimize_in_place", "attemptNumber": 1}
        payload = {"event": "evolution_start", "data": inner}
        events = _extract_evolution_events(_text_msg(_marker(payload)))
        assert events[0]["data"] == inner


# ---------------------------------------------------------------------------
# Tests: malformed / edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Graceful handling of malformed markers and empty messages."""

    def test_no_markers_returns_empty(self):
        """Plain text with no markers → empty list."""
        assert _extract_evolution_events(_text_msg("just normal text")) == []

    def test_empty_content_returns_empty(self):
        """Empty string content → empty list."""
        assert _extract_evolution_events(_text_msg("")) == []

    def test_none_content_returns_empty(self):
        """Missing content field → empty list."""
        assert _extract_evolution_events({}) == []
        assert _extract_evolution_events({"content": None}) == []

    def test_empty_blocks_returns_empty(self):
        """Empty content blocks list → empty list."""
        assert _extract_evolution_events({"content": []}) == []

    def test_malformed_json_silently_skipped(self):
        """Marker with invalid JSON → silently ignored, no crash."""
        msg = _text_msg("<!-- EVOLUTION_EVENT: {not valid json} -->")
        assert _extract_evolution_events(msg) == []

    def test_missing_event_key_skipped(self):
        """Valid JSON but no ``event`` key → skipped."""
        msg = _text_msg('<!-- EVOLUTION_EVENT: {"data": {"x": 1}} -->')
        assert _extract_evolution_events(msg) == []

    def test_marker_with_extra_whitespace(self):
        """Whitespace around the JSON inside the marker is tolerated."""
        payload = {"event": "evolution_result", "data": {"outcome": "success"}}
        raw = json.dumps(payload)
        msg = _text_msg(f"<!--   EVOLUTION_EVENT:   {raw}   -->")
        events = _extract_evolution_events(msg)
        assert len(events) == 1
        assert events[0]["type"] == "evolution_result"

    def test_mixed_valid_and_invalid_markers(self):
        """One good marker + one bad marker → only good one returned."""
        good = {"event": "evolution_start", "data": {}}
        msg = _text_msg(
            f"<!-- EVOLUTION_EVENT: broken!! --> text {_marker(good)}"
        )
        events = _extract_evolution_events(msg)
        assert len(events) == 1
        assert events[0]["type"] == "evolution_start"

    def test_non_text_blocks_ignored(self):
        """Content blocks that aren't type=text are safely skipped."""
        payload = {"event": "evolution_result", "data": {"outcome": "success"}}
        msg = {
            "content": [
                {"type": "image", "url": "http://example.com/img.png"},
                {"type": "text", "text": _marker(payload)},
            ]
        }
        events = _extract_evolution_events(msg)
        assert len(events) == 1
