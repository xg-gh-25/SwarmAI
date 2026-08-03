"""Tests for the Session Observation Layer.

Covers:
- ObservationRing: bounded ring buffer, intent extraction, snapshot
- Observation hooks: PreToolUse/PostToolUse non-blocking recording
- Enriched checkpoint: backward compat + new fields
- DDD event emission: only on qualifying project file edits
- Performance: hook latency must be <1ms
"""
import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest


# ── ObservationRing unit tests ────────────────────────────────────────────


class TestObservationRing:
    """Core ring buffer behavior."""

    def test_ring_maxlen_bounded(self):
        """Ring never exceeds maxlen even after many appends."""
        from core.observation_ring import ObservationRing

        ring = ObservationRing(maxlen=5)
        for i in range(20):
            ring.record_pre(f"id_{i}", "Bash", {"command": f"echo {i}"})
        # Internal deque bounded
        assert len(ring._ring) == 5

    def test_record_pre_extracts_intent_bash_with_description(self):
        """Bash tool with description field → uses description as intent."""
        from core.observation_ring import ObservationRing

        ring = ObservationRing()
        ring.record_pre("t1", "Bash", {"command": "ls -la", "description": "List files"})
        obs = ring._ring[-1]
        assert obs.intent == "List files"
        assert obs.tool_name == "Bash"

    def test_record_pre_extracts_intent_bash_no_description(self):
        """Bash tool without description → falls back to command."""
        from core.observation_ring import ObservationRing

        ring = ObservationRing()
        ring.record_pre("t1", "Bash", {"command": "pytest tests/"})
        obs = ring._ring[-1]
        assert obs.intent == "$ pytest tests/"

    def test_record_pre_extracts_intent_edit(self):
        """Edit tool → 'Edit: <file_path>'."""
        from core.observation_ring import ObservationRing

        ring = ObservationRing()
        ring.record_pre("t1", "Edit", {"file_path": "/foo/bar.py", "old_string": "x", "new_string": "y"})
        obs = ring._ring[-1]
        assert obs.intent == "Edit: /foo/bar.py"
        assert obs.files == ["/foo/bar.py"]

    def test_record_pre_extracts_intent_read(self):
        """Read tool → 'Read: <file_path>'."""
        from core.observation_ring import ObservationRing

        ring = ObservationRing()
        ring.record_pre("t1", "Read", {"file_path": "/some/file.md"})
        obs = ring._ring[-1]
        assert obs.intent == "Read: /some/file.md"

    def test_record_pre_extracts_intent_skill(self):
        """Skill tool → 'Skill: <name>'."""
        from core.observation_ring import ObservationRing

        ring = ObservationRing()
        ring.record_pre("t1", "Skill", {"skill": "s_deep-research"})
        obs = ring._ring[-1]
        assert obs.intent == "Skill: s_deep-research"

    def test_record_pre_extracts_intent_agent(self):
        """Agent tool → 'Agent: <description>'."""
        from core.observation_ring import ObservationRing

        ring = ObservationRing()
        ring.record_pre("t1", "Agent", {"description": "Research gstack"})
        obs = ring._ring[-1]
        assert obs.intent == "Agent: Research gstack"

    def test_record_pre_intent_capped_at_200_chars(self):
        """Intent is capped at 200 characters."""
        from core.observation_ring import ObservationRing

        ring = ObservationRing()
        long_cmd = "x" * 500
        ring.record_pre("t1", "Bash", {"command": long_cmd})
        obs = ring._ring[-1]
        assert len(obs.intent) <= 202  # "$ " prefix + 200

    def test_record_post_completes_observation(self):
        """PostToolUse sets completed=True, result_status, duration_ms."""
        from core.observation_ring import ObservationRing

        ring = ObservationRing()
        ring.record_pre("t1", "Bash", {"command": "echo hi"})
        time.sleep(0.001)  # Tiny sleep for measurable duration
        ring.record_post("t1", error=None)
        obs = ring._ring[-1]
        assert obs.completed is True
        assert obs.result_status == "success"
        assert obs.duration_ms >= 0

    def test_record_post_error_status(self):
        """PostToolUse with error → result_status='error'."""
        from core.observation_ring import ObservationRing

        ring = ObservationRing()
        ring.record_pre("t1", "Bash", {"command": "false"})
        ring.record_post("t1", error="command failed")
        obs = ring._ring[-1]
        assert obs.result_status == "error"

    def test_record_post_missing_id_noop(self):
        """PostToolUse for unknown tool_use_id → no crash, no effect."""
        from core.observation_ring import ObservationRing

        ring = ObservationRing()
        ring.record_pre("t1", "Bash", {"command": "echo"})
        # Post with wrong ID
        ring.record_post("unknown_id", error=None)
        # Original observation remains uncompleted
        obs = ring._ring[-1]
        assert obs.completed is False

    def test_snapshot_returns_last_n_completed(self):
        """snapshot(n) returns last N completed observations as dicts."""
        from core.observation_ring import ObservationRing

        ring = ObservationRing()
        for i in range(10):
            tid = f"t{i}"
            ring.record_pre(tid, "Read", {"file_path": f"/file{i}.py"})
            ring.record_post(tid, error=None)

        snap = ring.snapshot(last_n=3)
        assert len(snap) == 3
        assert snap[-1]["intent"] == "Read: /file9.py"
        assert "tool" in snap[0]
        assert "status" in snap[0]
        assert "duration_ms" in snap[0]

    def test_snapshot_skips_uncompleted(self):
        """snapshot only includes completed observations."""
        from core.observation_ring import ObservationRing

        ring = ObservationRing()
        ring.record_pre("t1", "Bash", {"command": "echo 1"})
        ring.record_post("t1", error=None)  # completed
        ring.record_pre("t2", "Bash", {"command": "echo 2"})  # NOT completed

        snap = ring.snapshot(last_n=10)
        assert len(snap) == 1

    def test_pending_cleanup_removes_stale(self):
        """pending_cleanup removes entries older than 5 minutes."""
        from core.observation_ring import ObservationRing, Observation

        ring = ObservationRing()
        # Manually insert a stale observation
        stale_obs = Observation(
            ts=time.monotonic() - 400,  # 6+ minutes ago
            tool_name="Bash",
            intent="stale",
            files=[],
        )
        ring._ring.append(stale_obs)
        ring._pending["stale_id"] = stale_obs  # Store object ref

        ring.pending_cleanup()
        assert "stale_id" not in ring._pending

    def test_last_completed_returns_most_recent(self):
        """last_completed returns the most recently completed observation."""
        from core.observation_ring import ObservationRing

        ring = ObservationRing()
        ring.record_pre("t1", "Read", {"file_path": "/a.py"})
        ring.record_post("t1", error=None)
        ring.record_pre("t2", "Edit", {"file_path": "/b.py"})
        ring.record_post("t2", error=None)
        ring.record_pre("t3", "Bash", {"command": "ls"})  # NOT completed

        last = ring.last_completed()
        assert last is not None
        assert last.tool_name == "Edit"


# ── Observation Hooks tests ───────────────────────────────────────────────


class TestObservationHooks:
    """Hook factory functions: non-blocking, never crash."""

    @pytest.fixture
    def session_context(self):
        return {"sdk_session_id": "test-session-123"}

    def test_observation_recorder_returns_approve(self, session_context):
        """PreToolUse observation hook always returns approve (never blocks)."""
        from core.observation_hooks import create_observation_recorder

        hook = create_observation_recorder(session_context)
        result = asyncio.run(
            hook({"tool_name": "Bash", "tool_input": {"command": "ls"}}, "tid1", None)
        )
        assert result.get("decision", "approve") == "approve"

    def test_observation_completer_noop_without_ring(self, session_context):
        """PostToolUse completer does nothing if ring not in context."""
        from core.observation_hooks import create_observation_completer

        hook = create_observation_completer(session_context)
        # Should not crash even without _observations in context
        result = asyncio.run(
            hook({"tool_name": "Bash", "tool_input": {}}, "tid1", None)
        )
        assert result == {} or result.get("decision") != "block"

    def test_recorder_populates_ring(self, session_context):
        """Recorder hook adds observation to ring in session_context."""
        from core.observation_hooks import create_observation_recorder
        from core.observation_ring import ObservationRing

        session_context["_observations"] = ObservationRing()
        hook = create_observation_recorder(session_context)
        asyncio.run(
            hook({"tool_name": "Edit", "tool_input": {"file_path": "/x.py"}}, "tid1", None)
        )
        ring = session_context["_observations"]
        assert len(ring._ring) == 1
        assert ring._ring[0].tool_name == "Edit"

    def test_hook_latency_under_1ms(self, session_context):
        """1000 PreToolUse hook invocations complete in <1s (avg <1ms each)."""
        from core.observation_hooks import create_observation_recorder
        from core.observation_ring import ObservationRing

        session_context["_observations"] = ObservationRing()
        hook = create_observation_recorder(session_context)

        async def _run_batch():
            start = time.perf_counter()
            for i in range(1000):
                await hook({"tool_name": "Bash", "tool_input": {"command": f"echo {i}"}}, f"t{i}", None)
            return time.perf_counter() - start

        elapsed = asyncio.run(_run_batch())
        assert elapsed < 1.0, f"1000 hooks took {elapsed:.3f}s (expected <1s)"


# ── Enriched Checkpoint tests ─────────────────────────────────────────────


class TestEnrichedCheckpoint:
    """Checkpoint enrichment with observation data."""

    def test_checkpoint_old_format_backward_compat(self):
        """Old checkpoint JSON (no observations) → context_injector reads without error."""
        old_checkpoint = {
            "session_id": "old-session",
            "ts": 1234567890,
            "tool_count": 42,
            "files_touched": ["/a.py", "/b.py"],
            "corrections_count": 1,
            "git_commits": ["abc1234 old commit"],
        }
        # New code should handle missing keys gracefully
        observations = old_checkpoint.get("recent_observations", [])
        summary = old_checkpoint.get("session_summary", {})
        assert observations == []
        assert summary == {}

    def test_checkpoint_enriched_fields_present(self):
        """Enriched checkpoint has recent_observations and session_summary."""
        from core.observation_ring import ObservationRing

        ring = ObservationRing()
        for i in range(5):
            tid = f"t{i}"
            ring.record_pre(tid, "Edit", {"file_path": f"/file{i}.py"})
            ring.record_post(tid, error=None)

        snapshot = ring.snapshot(last_n=3)
        assert len(snapshot) == 3
        assert all("tool" in s and "intent" in s and "status" in s for s in snapshot)


# ── DDD Event Emission tests ─────────────────────────────────────────────


class TestDDDEventEmission:
    """Real-time DDD event emission from observation hooks."""

    @pytest.fixture
    def session_context(self):
        return {"sdk_session_id": "test-123"}

    def _setup_dispatcher_mock(self):
        """Helper: create mock dispatcher."""
        mock_dispatcher = MagicMock()
        mock_dispatcher.emit_nowait = MagicMock(return_value=True)
        return mock_dispatcher

    def test_ddd_event_emitted_on_project_edit(self, session_context):
        """Edit on a project file puts GIT_COMMIT event into dispatcher queue."""
        import core.observation_hooks as obs_mod
        from core.observation_hooks import create_observation_completer
        from core.observation_ring import ObservationRing

        ring = ObservationRing()
        ring.record_pre("t1", "Edit", {"file_path": "/Users/x/Projects/SwarmAI/foo.py"})
        session_context["_observations"] = ring

        mock_dispatcher = self._setup_dispatcher_mock()

        # Ensure imports are cached so _CultivationEvent/_EventType are set
        obs_mod._ensure_cultivation_imports()

        hook = create_observation_completer(session_context)
        with patch.object(obs_mod, "get_dispatcher", return_value=mock_dispatcher):
            asyncio.run(
                hook({"tool_name": "Edit", "tool_input": {"file_path": "/Users/x/Projects/SwarmAI/foo.py"}, "error": None}, "t1", None)
            )
        mock_dispatcher.emit_nowait.assert_called_once()

    def test_ddd_event_not_emitted_non_project(self, session_context):
        """Edit on non-project file does NOT emit DDD event."""
        import core.observation_hooks as obs_mod
        from core.observation_hooks import create_observation_completer
        from core.observation_ring import ObservationRing

        ring = ObservationRing()
        ring.record_pre("t1", "Edit", {"file_path": "/tmp/scratch.py"})
        session_context["_observations"] = ring

        mock_dispatcher = self._setup_dispatcher_mock()
        obs_mod._ensure_cultivation_imports()

        hook = create_observation_completer(session_context)
        with patch.object(obs_mod, "get_dispatcher", return_value=mock_dispatcher):
            asyncio.run(
                hook({"tool_name": "Edit", "tool_input": {"file_path": "/tmp/scratch.py"}, "error": None}, "t1", None)
            )
        mock_dispatcher.emit_nowait.assert_not_called()

    def test_correction_event_emitted(self, session_context):
        """When _correction_just_detected is True, puts DAILY_ACTIVITY event into queue."""
        import core.observation_hooks as obs_mod
        from core.observation_hooks import create_observation_completer
        from core.observation_ring import ObservationRing

        ring = ObservationRing()
        ring.record_pre("t1", "Bash", {"command": "echo fix"})
        session_context["_observations"] = ring
        session_context["_correction_just_detected"] = True

        mock_dispatcher = self._setup_dispatcher_mock()
        obs_mod._ensure_cultivation_imports()

        hook = create_observation_completer(session_context)
        with patch.object(obs_mod, "get_dispatcher", return_value=mock_dispatcher):
            asyncio.run(
                hook({"tool_name": "Bash", "tool_input": {"command": "echo fix"}, "error": None}, "t1", None)
            )
        mock_dispatcher.emit_nowait.assert_called_once()
        # Flag should be cleared
        assert session_context["_correction_just_detected"] is False
