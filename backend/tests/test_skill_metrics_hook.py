"""Tests for SkillMetricsHook — post-session skill invocation detection."""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta

from hooks.skill_metrics_hook import (
    SkillMetricsHook,
    _detect_skill_invocations,
    _estimate_duration,
)


class TestDetectSkillInvocations:
    """Tests for _detect_skill_invocations()."""

    def test_detects_tool_use_skill_block(self):
        """Detect Skill tool_use blocks in assistant messages."""
        messages = [
            {"role": "user", "content": "Run the pdf skill", "created_at": "2026-04-09T10:00:00"},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Skill",
                        "input": {"skill": "pdf"},
                    }
                ],
                "created_at": "2026-04-09T10:00:05",
            },
            {"role": "user", "content": "Thanks, looks good", "created_at": "2026-04-09T10:01:00"},
        ]
        invocations = _detect_skill_invocations(messages)
        assert len(invocations) == 1
        assert invocations[0]["skill_name"] == "pdf"
        assert invocations[0]["outcome"] == "success"
        assert invocations[0]["user_satisfaction"] == "accepted"

    def test_detects_text_pattern_using_skill(self):
        """Detect 'Using Skill: X' text pattern."""
        messages = [
            {"role": "user", "content": "help me", "created_at": "2026-04-09T10:00:00"},
            {
                "role": "assistant",
                "content": "Using Skill: summarize to handle your request.",
                "created_at": "2026-04-09T10:00:05",
            },
            {"role": "user", "content": "perfect", "created_at": "2026-04-09T10:01:00"},
        ]
        invocations = _detect_skill_invocations(messages)
        assert len(invocations) == 1
        assert invocations[0]["skill_name"] == "summarize"

    def test_detects_launching_skill_pattern(self):
        """Detect 'Launching skill: X' text pattern."""
        messages = [
            {"role": "user", "content": "build a page", "created_at": "2026-04-09T10:00:00"},
            {
                "role": "assistant",
                "content": "Launching skill: frontend-design for this task.",
                "created_at": "2026-04-09T10:00:05",
            },
            {"role": "user", "content": "ok", "created_at": "2026-04-09T10:01:00"},
        ]
        invocations = _detect_skill_invocations(messages)
        assert len(invocations) == 1
        assert invocations[0]["skill_name"] == "frontend-design"

    def test_detects_correction(self):
        """Detect user correction following skill invocation."""
        messages = [
            {"role": "user", "content": "run pdf", "created_at": "2026-04-09T10:00:00"},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "name": "Skill", "input": {"skill": "pdf"}},
                ],
                "created_at": "2026-04-09T10:00:05",
            },
            {
                "role": "user",
                "content": "No, that's wrong. Fix it.",
                "created_at": "2026-04-09T10:01:00",
            },
        ]
        invocations = _detect_skill_invocations(messages)
        assert len(invocations) == 1
        assert invocations[0]["user_satisfaction"] == "correction"
        assert invocations[0]["outcome"] == "partial"

    def test_no_skill_invocations(self):
        """No invocations in a regular conversation."""
        messages = [
            {"role": "user", "content": "hello", "created_at": "2026-04-09T10:00:00"},
            {"role": "assistant", "content": "Hi there!", "created_at": "2026-04-09T10:00:05"},
        ]
        invocations = _detect_skill_invocations(messages)
        assert len(invocations) == 0

    def test_multiple_skills_in_session(self):
        """Detect multiple skill invocations in a single session."""
        messages = [
            {"role": "user", "content": "run pdf", "created_at": "2026-04-09T10:00:00"},
            {
                "role": "assistant",
                "content": "Using Skill: pdf to process the file.",
                "created_at": "2026-04-09T10:00:05",
            },
            {"role": "user", "content": "now translate it", "created_at": "2026-04-09T10:01:00"},
            {
                "role": "assistant",
                "content": "Using Skill: translate for this.",
                "created_at": "2026-04-09T10:01:05",
            },
            {"role": "user", "content": "thanks", "created_at": "2026-04-09T10:02:00"},
        ]
        invocations = _detect_skill_invocations(messages)
        assert len(invocations) == 2
        names = {inv["skill_name"] for inv in invocations}
        assert "pdf" in names
        assert "translate" in names

    def test_empty_messages(self):
        """Empty message list produces no invocations."""
        assert _detect_skill_invocations([]) == []

    def test_tool_use_with_list_content_and_text(self):
        """Handles mixed content blocks (tool_use + text) correctly."""
        messages = [
            {"role": "user", "content": "do something", "created_at": "2026-04-09T10:00:00"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Let me use a skill."},
                    {"type": "tool_use", "name": "Skill", "input": {"skill": "deep-research"}},
                ],
                "created_at": "2026-04-09T10:00:05",
            },
            {"role": "user", "content": "great work", "created_at": "2026-04-09T10:05:00"},
        ]
        invocations = _detect_skill_invocations(messages)
        assert len(invocations) == 1
        assert invocations[0]["skill_name"] == "deep-research"


class TestEstimateDuration:
    """Tests for _estimate_duration()."""

    def test_valid_timestamps(self):
        dur = _estimate_duration("2026-04-09T10:00:00", "2026-04-09T10:01:30")
        assert dur == pytest.approx(90.0, abs=1.0)

    def test_empty_timestamps(self):
        assert _estimate_duration("", "2026-04-09T10:00:00") == 0.0
        assert _estimate_duration("2026-04-09T10:00:00", "") == 0.0

    def test_invalid_timestamps(self):
        assert _estimate_duration("not-a-date", "also-not") == 0.0

    def test_microsecond_timestamps(self):
        dur = _estimate_duration(
            "2026-04-09T10:00:00.123456",
            "2026-04-09T10:00:10.654321",
        )
        assert dur == pytest.approx(10.53, abs=0.1)


class TestSkillMetricsHookProperties:
    """Tests for SkillMetricsHook basic properties."""

    def test_hook_name(self):
        hook = SkillMetricsHook()
        assert hook.name == "skill-metrics"
