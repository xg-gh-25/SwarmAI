"""Tests for the Escalation Protocol (core/escalation.py).

Covers: data model, L0 INFORM, L2 BLOCK, SSE event builder,
persistence (save/load/list), and resolution flow.

# Feature: escalation-protocol
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from core.escalation import (
    Escalation,
    Level,
    Option,
    block,
    build_sse_event,
    get_open_escalations,
    inform,
    load_escalation,
    resolve,
    save_escalation,
)


# ---------------------------------------------------------------------------
# L0 INFORM
# ---------------------------------------------------------------------------

class TestInform:
    def test_inform_creates_l0(self):
        esc = inform(
            title="Chose approach 2",
            situation="PRODUCT.md aligns with caching strategy.",
            trigger="CLEAR_EVALUATION",
            pipeline_stage="evaluate",
            project="TestProject",
        )
        assert esc.level == Level.INFORM
        assert esc.status == "resolved"
        assert esc.resolved_by == "swarm"
        assert esc.title == "Chose approach 2"
        assert esc.id.startswith("esc_")

    def test_inform_no_options(self):
        esc = inform(title="FYI", situation="All good.")
        assert esc.options == []
        assert esc.recommendation is None

    def test_inform_with_evidence(self):
        esc = inform(
            title="Test",
            situation="Context",
            evidence=["PRODUCT.md: caching is priority #1"],
        )
        assert len(esc.evidence) == 1


# ---------------------------------------------------------------------------
# L2 BLOCK
# ---------------------------------------------------------------------------

class TestBlock:
    def test_block_creates_l2_open(self):
        esc = block(
            title="Ambiguous scope: improve performance",
            situation="Cannot determine what to optimize.",
            options=[
                Option(label="API latency", description="Focus on backend response times"),
                Option(label="UI render", description="Focus on frontend paint time"),
                Option(label="Discuss", description="Let me explain more"),
            ],
            trigger="AMBIGUOUS_SCOPE",
            pipeline_stage="evaluate",
            project="ClientApp",
        )
        assert esc.level == Level.BLOCK
        assert esc.status == "open"
        assert len(esc.options) == 3
        assert esc.resolved_at is None

    def test_block_with_recommendation(self):
        esc = block(
            title="Architecture choice",
            situation="Monolith vs microservice.",
            options=[
                Option(label="Monolith", description="Keep it simple", is_recommendation=True),
                Option(label="Microservice", description="Scale later"),
            ],
            recommendation="Monolith — team of 1, not worth the overhead.",
        )
        assert esc.recommendation is not None
        assert esc.options[0].is_recommendation is True

    def test_block_without_project_is_ephemeral(self):
        esc = block(
            title="What to focus on?",
            situation="Multiple priorities.",
            options=[Option(label="A", description="Option A")],
        )
        assert esc.project is None


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

class TestResolve:
    def test_resolve_changes_status(self):
        esc = block(
            title="Test", situation="Test",
            options=[Option(label="Yes", description="Do it")],
        )
        resolved = resolve(esc, resolution="Yes", resolved_by="user")
        assert resolved.status == "resolved"
        assert resolved.resolved_by == "user"
        assert resolved.resolution == "Yes"
        assert resolved.resolved_at is not None
        # Original unchanged
        assert esc.status == "open"

    def test_resolve_preserves_context(self):
        esc = block(
            title="T", situation="S",
            options=[Option(label="A", description="B")],
            project="P", pipeline_stage="evaluate",
            evidence=["doc1"],
        )
        resolved = resolve(esc, "A")
        assert resolved.project == "P"
        assert resolved.pipeline_stage == "evaluate"
        assert resolved.evidence == ["doc1"]


# ---------------------------------------------------------------------------
# SSE Event Builder
# ---------------------------------------------------------------------------

class TestSSEEvent:
    def test_event_has_required_fields(self):
        esc = block(
            title="Test", situation="Context",
            options=[Option(label="A", description="B", risk="low")],
            trigger="AMBIGUOUS_SCOPE",
            pipeline_stage="evaluate",
            project="X",
        )
        event = build_sse_event(esc)
        assert event["type"] == "escalation"
        assert event["level"] == 2
        assert event["levelName"] == "BLOCK"
        assert event["trigger"] == "AMBIGUOUS_SCOPE"
        assert event["title"] == "Test"
        assert event["situation"] == "Context"
        assert event["pipelineStage"] == "evaluate"
        assert event["project"] == "X"
        assert event["status"] == "open"
        assert len(event["options"]) == 1
        assert event["options"][0]["label"] == "A"
        assert event["options"][0]["risk"] == "low"

    def test_event_is_json_serializable(self):
        esc = inform(title="T", situation="S", evidence=["e1"])
        event = build_sse_event(esc)
        # Must not raise
        serialized = json.dumps(event)
        assert '"type": "escalation"' in serialized

    def test_inform_event_has_level_0(self):
        esc = inform(title="T", situation="S")
        event = build_sse_event(esc)
        assert event["level"] == 0
        assert event["levelName"] == "INFORM"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

class TestPersistence:
    @pytest.fixture()
    def workspace(self, tmp_path):
        """Create a temp workspace with project structure."""
        project_dir = tmp_path / "Projects" / "TestProject"
        project_dir.mkdir(parents=True)
        return tmp_path

    def test_save_and_load(self, workspace):
        esc = block(
            title="Test", situation="Context",
            options=[Option(label="A", description="B")],
            project="TestProject",
        )
        save_escalation(workspace, esc)
        loaded = load_escalation(workspace, "TestProject", esc.id)
        assert loaded is not None
        assert loaded.id == esc.id
        assert loaded.title == "Test"
        assert loaded.level == Level.BLOCK
        assert len(loaded.options) == 1
        assert loaded.options[0].label == "A"

    def test_save_noop_without_project(self, workspace):
        esc = inform(title="T", situation="S")
        # Should not raise
        save_escalation(workspace, esc)
        # Nothing was written
        assert not (workspace / "Projects").exists() or \
            not list((workspace / "Projects").rglob("esc_*.json"))

    def test_get_open_escalations(self, workspace):
        esc1 = block(title="Open1", situation="S", options=[], project="TestProject")
        esc2 = block(title="Open2", situation="S", options=[], project="TestProject")
        esc3 = resolve(
            block(title="Resolved", situation="S", options=[], project="TestProject"),
            resolution="Done",
        )
        save_escalation(workspace, esc1)
        save_escalation(workspace, esc2)
        save_escalation(workspace, esc3)

        open_escs = get_open_escalations(workspace, "TestProject")
        assert len(open_escs) == 2
        titles = {e.title for e in open_escs}
        assert titles == {"Open1", "Open2"}

    def test_load_nonexistent_returns_none(self, workspace):
        result = load_escalation(workspace, "TestProject", "esc_nonexistent")
        assert result is None

    def test_get_open_empty_project(self, workspace):
        result = get_open_escalations(workspace, "TestProject")
        assert result == []


# ---------------------------------------------------------------------------
# Level enum
# ---------------------------------------------------------------------------

class TestLevel:
    def test_level_ordering(self):
        assert Level.INFORM < Level.CONSULT < Level.BLOCK

    def test_level_names(self):
        assert Level.INFORM.name == "INFORM"
        assert Level.CONSULT.name == "CONSULT"
        assert Level.BLOCK.name == "BLOCK"
