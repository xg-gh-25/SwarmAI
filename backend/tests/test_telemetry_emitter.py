"""Unit tests for TelemetryEmitter (TSCC telemetry event construction).

Tests the ``TelemetryEmitter`` class from ``backend/core/telemetry_emitter.py``
which constructs SSE-compatible telemetry event dicts during agent execution.

Testing methodology: unit tests (pytest)

Key invariants verified:

- Each emitter method returns a dict with ``type``, ``thread_id``,
  ``timestamp``, and ``data`` fields
- ``type`` matches one of the five valid telemetry event types
- ``thread_id`` matches the value passed to the constructor
- ``timestamp`` is valid ISO 8601
- All field names in emitted dicts are snake_case (no camelCase)
- ``sources_updated`` normalizes paths: strips absolute prefixes, ``~``,
  and ``{app_data_dir}`` references

Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6, 13.7
"""

import re
from datetime import datetime

import pytest

from core.telemetry_emitter import TelemetryEmitter


VALID_TELEMETRY_TYPES = frozenset({
    "agent_activity",
    "tool_invocation",
    "capability_activated",
    "sources_updated",
    "summary_updated",
})

_SNAKE_CASE_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _assert_valid_iso8601(ts: str) -> None:
    parsed = datetime.fromisoformat(ts)
    assert parsed.tzinfo is not None, "Timestamp must be timezone-aware"


def _assert_all_keys_snake_case(d: dict, path: str = "") -> None:
    for key in d:
        full = f"{path}.{key}" if path else key
        assert _SNAKE_CASE_RE.match(key), f"Key '{full}' is not snake_case"
        if isinstance(d[key], dict):
            _assert_all_keys_snake_case(d[key], full)



def _assert_envelope(event: dict, expected_type: str, thread_id: str) -> None:
    assert set(event.keys()) == {"type", "thread_id", "timestamp", "data"}
    assert event["type"] == expected_type
    assert event["type"] in VALID_TELEMETRY_TYPES
    assert event["thread_id"] == thread_id
    _assert_valid_iso8601(event["timestamp"])
    assert isinstance(event["data"], dict)
    _assert_all_keys_snake_case(event)


class TestAgentActivity:
    def test_correct_structure(self):
        emitter = TelemetryEmitter("thread-001")
        event = emitter.agent_activity("ResearchAgent", "Analyzing documents")
        _assert_envelope(event, "agent_activity", "thread-001")
        assert event["data"] == {
            "agent_name": "ResearchAgent",
            "description": "Analyzing documents",
        }

    def test_thread_id_matches_constructor(self):
        emitter = TelemetryEmitter("custom-thread-xyz")
        event = emitter.agent_activity("Agent", "desc")
        assert event["thread_id"] == "custom-thread-xyz"


class TestToolInvocation:
    def test_correct_structure(self):
        emitter = TelemetryEmitter("thread-002")
        event = emitter.tool_invocation("file_search", "Searching codebase")
        _assert_envelope(event, "tool_invocation", "thread-002")
        assert event["data"] == {
            "tool_name": "file_search",
            "description": "Searching codebase",
        }


class TestCapabilityActivated:
    def test_skill_capability(self):
        emitter = TelemetryEmitter("thread-003")
        event = emitter.capability_activated("skill", "code_review", "Code Review")
        _assert_envelope(event, "capability_activated", "thread-003")
        assert event["data"] == {
            "cap_type": "skill",
            "cap_name": "code_review",
            "label": "Code Review",
        }

    def test_mcp_capability(self):
        emitter = TelemetryEmitter("thread-003")
        event = emitter.capability_activated("mcp", "github", "GitHub MCP")
        assert event["data"]["cap_type"] == "mcp"

    def test_tool_capability(self):
        emitter = TelemetryEmitter("thread-003")
        event = emitter.capability_activated("tool", "grep", "Grep Tool")
        assert event["data"]["cap_type"] == "tool"



class TestSourcesUpdated:
    def test_correct_structure(self):
        emitter = TelemetryEmitter("thread-004")
        event = emitter.sources_updated("src/main.py", "Project")
        _assert_envelope(event, "sources_updated", "thread-004")
        assert event["data"] == {
            "source_path": "src/main.py",
            "origin": "Project",
        }

    def test_strips_absolute_path(self):
        emitter = TelemetryEmitter("t1")
        event = emitter.sources_updated("/home/user/project/src/main.py", "Project")
        path = event["data"]["source_path"]
        assert not path.startswith("/"), f"Path still absolute: {path}"

    def test_strips_tilde_prefix(self):
        emitter = TelemetryEmitter("t1")
        event = emitter.sources_updated("~/projects/app/config.yaml", "Notes")
        path = event["data"]["source_path"]
        assert not path.startswith("~"), f"Path still has tilde: {path}"
        assert not path.startswith("/"), f"Expanded tilde left absolute path: {path}"

    def test_strips_app_data_dir(self):
        emitter = TelemetryEmitter("t1")
        event = emitter.sources_updated(
            "{app_data_dir}/skills/my_skill.py", "Knowledge Base"
        )
        path = event["data"]["source_path"]
        assert "{app_data_dir}" not in path, f"app_data_dir not stripped: {path}"
        assert not path.startswith("/")

    def test_strips_app_data_dir_case_insensitive(self):
        emitter = TelemetryEmitter("t1")
        event = emitter.sources_updated(
            "{APP_DATA_DIR}/notes/readme.md", "Notes"
        )
        path = event["data"]["source_path"]
        assert "{APP_DATA_DIR}" not in path.upper()

    def test_relative_path_unchanged(self):
        emitter = TelemetryEmitter("t1")
        event = emitter.sources_updated("backend/core/agent.py", "Project")
        assert event["data"]["source_path"] == "backend/core/agent.py"

    def test_dot_slash_stripped(self):
        emitter = TelemetryEmitter("t1")
        event = emitter.sources_updated("./src/utils.py", "Project")
        assert event["data"]["source_path"] == "src/utils.py"


class TestSummaryUpdated:
    def test_correct_structure(self):
        emitter = TelemetryEmitter("thread-005")
        summary = ["Found 3 issues", "Proposed fix for auth bug"]
        event = emitter.summary_updated(summary)
        _assert_envelope(event, "summary_updated", "thread-005")
        assert event["data"] == {"key_summary": summary}

    def test_empty_summary(self):
        emitter = TelemetryEmitter("thread-005")
        event = emitter.summary_updated([])
        assert event["data"]["key_summary"] == []



class TestTimestamp:
    @pytest.fixture()
    def emitter(self):
        return TelemetryEmitter("ts-thread")

    def test_agent_activity_timestamp(self, emitter):
        _assert_valid_iso8601(emitter.agent_activity("A", "d")["timestamp"])

    def test_tool_invocation_timestamp(self, emitter):
        _assert_valid_iso8601(emitter.tool_invocation("T", "d")["timestamp"])

    def test_capability_activated_timestamp(self, emitter):
        _assert_valid_iso8601(
            emitter.capability_activated("skill", "s", "S")["timestamp"]
        )

    def test_sources_updated_timestamp(self, emitter):
        _assert_valid_iso8601(
            emitter.sources_updated("p.py", "Project")["timestamp"]
        )

    def test_summary_updated_timestamp(self, emitter):
        _assert_valid_iso8601(emitter.summary_updated(["x"])["timestamp"])


class TestSnakeCaseKeys:
    @pytest.fixture()
    def emitter(self):
        return TelemetryEmitter("sc-thread")

    def test_agent_activity_keys(self, emitter):
        _assert_all_keys_snake_case(emitter.agent_activity("A", "d"))

    def test_tool_invocation_keys(self, emitter):
        _assert_all_keys_snake_case(emitter.tool_invocation("T", "d"))

    def test_capability_activated_keys(self, emitter):
        _assert_all_keys_snake_case(
            emitter.capability_activated("mcp", "m", "M")
        )

    def test_sources_updated_keys(self, emitter):
        _assert_all_keys_snake_case(
            emitter.sources_updated("file.py", "Project")
        )

    def test_summary_updated_keys(self, emitter):
        _assert_all_keys_snake_case(emitter.summary_updated(["s"]))
