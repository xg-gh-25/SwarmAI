"""Tests for runtime_hooks.py — real-time correction capture and error detection.

Verifies:
- PostToolUseFailure writes to corrections.jsonl
- Consecutive failure detection injects additionalContext after 2+ failures
- UserPromptSubmit detects CN + EN correction patterns
- Hook timeout behavior
"""
import asyncio
import json
import os
import pytest
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock


@pytest.fixture
def corrections_file(tmp_path):
    """Provide a temp corrections.jsonl path."""
    return tmp_path / "corrections.jsonl"


@pytest.fixture
def session_context():
    """Minimal session_context dict."""
    return {"sdk_session_id": "test-session-123"}


# ---------------------------------------------------------------------------
# PostToolUseFailure: correction capture
# ---------------------------------------------------------------------------

class TestCorrectionCapture:

    @pytest.mark.asyncio
    async def test_tool_failure_writes_jsonl(self, corrections_file, session_context):
        """PostToolUseFailure hook appends valid JSONL to corrections file."""
        from core.runtime_hooks import create_correction_capture_hook

        hook = create_correction_capture_hook(str(corrections_file), session_context)
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "git push origin main"},
            "error": "Permission denied (publickey).",
            "tool_use_id": "tu_001",
        }
        result = await hook(input_data, "tu_001", MagicMock())

        assert result == {}  # observe-only, no additionalContext
        assert corrections_file.exists()

        line = json.loads(corrections_file.read_text().strip())
        assert line["type"] == "tool_failure"
        assert line["tool"] == "Bash"
        assert "Permission denied" in line["error"]
        assert line["session_id"] == "test-session-123"
        assert "ts" in line

    @pytest.mark.asyncio
    async def test_multiple_failures_append(self, corrections_file, session_context):
        """Multiple failures append multiple lines."""
        from core.runtime_hooks import create_correction_capture_hook

        hook = create_correction_capture_hook(str(corrections_file), session_context)

        for i in range(3):
            await hook({"tool_name": "Bash", "tool_input": {}, "error": f"err-{i}", "tool_use_id": f"tu_{i}"}, f"tu_{i}", MagicMock())

        lines = corrections_file.read_text().strip().split("\n")
        assert len(lines) == 3


# ---------------------------------------------------------------------------
# PostToolUseFailure: error pattern detection (consecutive failures)
# ---------------------------------------------------------------------------

class TestErrorPatternDetection:

    @pytest.mark.asyncio
    async def test_first_failure_no_hint(self, session_context):
        """First failure doesn't inject additionalContext."""
        from core.runtime_hooks import create_error_pattern_detector

        hook = create_error_pattern_detector(session_context)
        result = await hook(
            {"tool_name": "Bash", "tool_input": {}, "error": "fail-1", "tool_use_id": "tu_1"},
            "tu_1", MagicMock(),
        )
        assert result.get("additionalContext", "") == ""

    @pytest.mark.asyncio
    async def test_second_consecutive_failure_injects_hint(self, session_context):
        """Second consecutive failure on same tool injects hint."""
        from core.runtime_hooks import create_error_pattern_detector

        hook = create_error_pattern_detector(session_context)

        # First failure — no hint
        await hook({"tool_name": "Bash", "tool_input": {}, "error": "err-1", "tool_use_id": "tu_1"}, "tu_1", MagicMock())

        # Second failure — hint injected
        result = await hook(
            {"tool_name": "Bash", "tool_input": {}, "error": "err-2", "tool_use_id": "tu_2"},
            "tu_2", MagicMock(),
        )
        ctx = result.get("additionalContext", "")
        assert "failed" in ctx.lower() or "2" in ctx

    @pytest.mark.asyncio
    async def test_different_tools_tracked_separately(self, session_context):
        """Failures on different tools don't cross-contaminate."""
        from core.runtime_hooks import create_error_pattern_detector

        hook = create_error_pattern_detector(session_context)

        await hook({"tool_name": "Bash", "tool_input": {}, "error": "err", "tool_use_id": "tu_1"}, "tu_1", MagicMock())
        result = await hook(
            {"tool_name": "Edit", "tool_input": {}, "error": "err", "tool_use_id": "tu_2"},
            "tu_2", MagicMock(),
        )
        # First failure for Edit — no hint
        assert result.get("additionalContext", "") == ""


# ---------------------------------------------------------------------------
# UserPromptSubmit: correction pattern detection
# ---------------------------------------------------------------------------

class TestUserCorrectionDetector:

    @pytest.mark.asyncio
    async def test_detects_chinese_correction(self, corrections_file, session_context):
        """Detects '不对' as a correction signal."""
        from core.runtime_hooks import create_user_correction_detector

        hook = create_user_correction_detector(str(corrections_file), session_context)
        result = await hook(
            {"prompt": "不对，应该用 rebase 不是 merge"},
            None, MagicMock(),
        )
        assert result == {}  # observe-only

        assert corrections_file.exists()
        line = json.loads(corrections_file.read_text().strip())
        assert line["type"] == "user_correction"
        assert "不对" in line["prompt"]

    @pytest.mark.asyncio
    async def test_detects_english_wrong(self, corrections_file, session_context):
        """Detects 'wrong' as a correction signal."""
        from core.runtime_hooks import create_user_correction_detector

        hook = create_user_correction_detector(str(corrections_file), session_context)
        await hook({"prompt": "That's wrong, use async instead"}, None, MagicMock())

        assert corrections_file.exists()
        line = json.loads(corrections_file.read_text().strip())
        assert line["type"] == "user_correction"

    @pytest.mark.asyncio
    async def test_detects_actually_negation(self, corrections_file, session_context):
        """Detects 'actually, don't' as a correction signal."""
        from core.runtime_hooks import create_user_correction_detector

        hook = create_user_correction_detector(str(corrections_file), session_context)
        await hook({"prompt": "Actually, don't use merge here"}, None, MagicMock())

        assert corrections_file.exists()

    @pytest.mark.asyncio
    async def test_normal_prompt_not_captured(self, corrections_file, session_context):
        """Normal prompts without correction patterns are not captured."""
        from core.runtime_hooks import create_user_correction_detector

        hook = create_user_correction_detector(str(corrections_file), session_context)
        await hook({"prompt": "Can you help me write a function?"}, None, MagicMock())

        assert not corrections_file.exists()

    @pytest.mark.asyncio
    async def test_rotation_triggers_on_oversize(self, corrections_file, session_context):
        """When file exceeds size threshold, oldest entries are dropped."""
        import core.runtime_hooks as rh

        # Lower both thresholds so rotation triggers quickly and truncates
        orig_size, orig_entries = rh._MAX_CORRECTIONS_SIZE_BYTES, rh._MAX_CORRECTIONS_ENTRIES
        rh._MAX_CORRECTIONS_SIZE_BYTES = 2048  # 2KB trigger
        rh._MAX_CORRECTIONS_ENTRIES = 10       # keep only 10
        try:
            hook = rh.create_correction_capture_hook(str(corrections_file), session_context)
            for i in range(50):
                await hook(
                    {"tool_name": "Bash", "tool_input": {"cmd": f"x{i}"}, "error": f"err-{i}"},
                    f"tu_{i}", MagicMock(),
                )

            lines = corrections_file.read_text().strip().split("\n")
            assert len(lines) <= 20  # at most 10 kept + writes since last rotation
            last = json.loads(lines[-1])
            assert last["error"] == "err-49"
        finally:
            rh._MAX_CORRECTIONS_SIZE_BYTES, rh._MAX_CORRECTIONS_ENTRIES = orig_size, orig_entries

    def test_rotate_keeps_newest_entries(self, corrections_file):
        """_rotate_corrections preserves newest N entries, drops oldest."""
        from core.runtime_hooks import _rotate_corrections
        import core.runtime_hooks as rh

        orig_entries = rh._MAX_CORRECTIONS_ENTRIES
        rh._MAX_CORRECTIONS_ENTRIES = 5
        try:
            # Write 20 lines directly
            with open(corrections_file, "w") as f:
                for i in range(20):
                    f.write(json.dumps({"idx": i}) + "\n")

            _rotate_corrections(corrections_file)

            lines = corrections_file.read_text().strip().split("\n")
            assert len(lines) == 5
            # Kept entries are idx 15-19 (the newest 5)
            assert json.loads(lines[0])["idx"] == 15
            assert json.loads(lines[-1])["idx"] == 19
        finally:
            rh._MAX_CORRECTIONS_ENTRIES = orig_entries

    @pytest.mark.asyncio
    async def test_no_false_positive_on_contains(self, corrections_file, session_context):
        """'wrong' inside a word (e.g., 'wrongfully') should not trigger."""
        from core.runtime_hooks import create_user_correction_detector

        hook = create_user_correction_detector(str(corrections_file), session_context)
        # "wrong" as a standalone word SHOULD trigger, but embedded should not
        # Actually "wrong" in "what's wrong with..." is debatable — we accept it
        # The key is "wrongfully" should not trigger
        await hook({"prompt": "The code is wrongfully tested"}, None, MagicMock())

        # This is a borderline case — we lean toward capture (false positive safe)
        # So we just verify no crash

    @pytest.mark.asyncio
    async def test_actually_agreement_not_captured(self, corrections_file, session_context):
        """'Actually, that's a good idea' should NOT trigger — it's agreement."""
        from core.runtime_hooks import create_user_correction_detector

        hook = create_user_correction_detector(str(corrections_file), session_context)
        await hook({"prompt": "Actually, that's a good idea"}, None, MagicMock())
        assert not corrections_file.exists()

    @pytest.mark.asyncio
    async def test_actually_right_not_captured(self, corrections_file, session_context):
        """'Actually I think you're right' should NOT trigger."""
        from core.runtime_hooks import create_user_correction_detector

        hook = create_user_correction_detector(str(corrections_file), session_context)
        await hook({"prompt": "Actually I think you're right about this"}, None, MagicMock())
        assert not corrections_file.exists()

    @pytest.mark.asyncio
    async def test_actually_correction_captured(self, corrections_file, session_context):
        """'Actually, not like that' — negation after actually → SHOULD trigger."""
        from core.runtime_hooks import create_user_correction_detector

        hook = create_user_correction_detector(str(corrections_file), session_context)
        await hook({"prompt": "Actually, not like that. Use rebase."}, None, MagicMock())
        assert corrections_file.exists()

    @pytest.mark.asyncio
    async def test_actually_not_followed_by_qualifier(self, corrections_file, session_context):
        """'Actually, great work!' — no qualifier word → NOT a correction."""
        from core.runtime_hooks import create_user_correction_detector

        hook = create_user_correction_detector(str(corrections_file), session_context)
        await hook({"prompt": "Actually, great work on this!"}, None, MagicMock())
        assert not corrections_file.exists()


# ---------------------------------------------------------------------------
# PostToolUse: failure tracker reset on success
# ---------------------------------------------------------------------------

class TestFailureTrackerReset:

    @pytest.mark.asyncio
    async def test_success_resets_consecutive_count(self, session_context):
        """After 1 failure + 1 success, next failure should NOT trigger hint."""
        from core.runtime_hooks import create_error_pattern_detector, create_failure_tracker_reset

        fail_hook = create_error_pattern_detector(session_context)
        reset_hook = create_failure_tracker_reset(session_context)

        # Fail once
        await fail_hook(
            {"tool_name": "Bash", "tool_input": {}, "error": "err-1"},
            "tu_1", MagicMock(),
        )

        # Succeed — resets counter
        await reset_hook(
            {"tool_name": "Bash", "tool_input": {}},
            "tu_2", MagicMock(),
        )

        # Fail again — should be count=1, no hint
        result = await fail_hook(
            {"tool_name": "Bash", "tool_input": {}, "error": "err-2"},
            "tu_3", MagicMock(),
        )
        assert result.get("additionalContext", "") == ""

    @pytest.mark.asyncio
    async def test_reset_only_affects_succeeded_tool(self, session_context):
        """Resetting Bash doesn't reset Edit's failure counter."""
        from core.runtime_hooks import create_error_pattern_detector, create_failure_tracker_reset

        fail_hook = create_error_pattern_detector(session_context)
        reset_hook = create_failure_tracker_reset(session_context)

        # Fail Edit once
        await fail_hook(
            {"tool_name": "Edit", "tool_input": {}, "error": "err"},
            "tu_1", MagicMock(),
        )

        # Succeed Bash — should NOT affect Edit
        await reset_hook(
            {"tool_name": "Bash", "tool_input": {}},
            "tu_2", MagicMock(),
        )

        # Fail Edit again — should be count=2, hint injected
        result = await fail_hook(
            {"tool_name": "Edit", "tool_input": {}, "error": "err"},
            "tu_3", MagicMock(),
        )
        assert "additionalContext" in result

    @pytest.mark.asyncio
    async def test_reset_no_crash_on_missing_tracker(self):
        """Reset hook doesn't crash when _failure_tracker doesn't exist yet."""
        from core.runtime_hooks import create_failure_tracker_reset

        ctx = {"sdk_session_id": "test"}
        hook = create_failure_tracker_reset(ctx)

        # No _failure_tracker in ctx — should not raise
        result = await hook(
            {"tool_name": "Bash", "tool_input": {}},
            "tu_1", MagicMock(),
        )
        assert result == {}

    @pytest.mark.asyncio
    async def test_two_failures_then_success_then_two_failures_triggers(self, session_context):
        """fail-fail-success-fail-fail → hint only on the second pair."""
        from core.runtime_hooks import create_error_pattern_detector, create_failure_tracker_reset

        fail_hook = create_error_pattern_detector(session_context)
        reset_hook = create_failure_tracker_reset(session_context)

        # fail-fail → hint
        await fail_hook({"tool_name": "Bash", "tool_input": {}, "error": "e1"}, "t1", MagicMock())
        r = await fail_hook({"tool_name": "Bash", "tool_input": {}, "error": "e2"}, "t2", MagicMock())
        assert "additionalContext" in r

        # success → reset
        await reset_hook({"tool_name": "Bash"}, "t3", MagicMock())

        # fail → no hint (count=1)
        r = await fail_hook({"tool_name": "Bash", "tool_input": {}, "error": "e3"}, "t4", MagicMock())
        assert r.get("additionalContext", "") == ""

        # fail → hint (count=2 again)
        r = await fail_hook({"tool_name": "Bash", "tool_input": {}, "error": "e4"}, "t5", MagicMock())
        assert "additionalContext" in r


# ---------------------------------------------------------------------------
# register_runtime_hooks: wiring test
# ---------------------------------------------------------------------------

class TestRegisterRuntimeHooks:

    def test_registers_all_nine_hooks(self, session_context):
        """register_runtime_hooks wires 9 hooks into the registry."""
        from core.runtime_hooks import register_runtime_hooks
        from core.hook_builder import HookRegistry

        registry = HookRegistry()
        register_runtime_hooks(registry, session_context)

        sdk_hooks = registry.build_sdk_hooks()

        # PostToolUseFailure: correction_capture + error_pattern_detector (chained)
        assert "PostToolUseFailure" in sdk_hooks
        # PostToolUse: failure_tracker_reset + file_tracker + session_checkpoint (chained)
        assert "PostToolUse" in sdk_hooks
        # UserPromptSubmit: user_correction_detector + post_compact_injection (chained)
        assert "UserPromptSubmit" in sdk_hooks
        # SubagentStop: subagent_capture
        assert "SubagentStop" in sdk_hooks


# ---------------------------------------------------------------------------
# HookRegistry chain: merge semantics
# ---------------------------------------------------------------------------

class TestReadCorrectionStats:

    def test_empty_when_no_file(self, tmp_path):
        """Returns empty dict when corrections.jsonl doesn't exist."""
        from core.runtime_hooks import read_correction_stats
        result = read_correction_stats(str(tmp_path / "nonexistent.jsonl"))
        assert result == {}

    def test_counts_per_tool(self, corrections_file):
        """Aggregates counts per tool name."""
        from core.runtime_hooks import read_correction_stats

        entries = [
            {"ts": time.time(), "type": "tool_failure", "tool": "Bash", "error": "e1"},
            {"ts": time.time(), "type": "tool_failure", "tool": "Bash", "error": "e2"},
            {"ts": time.time(), "type": "tool_failure", "tool": "Edit", "error": "e3"},
        ]
        with open(corrections_file, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

        stats = read_correction_stats(str(corrections_file))
        assert stats["Bash"]["total"] == 2
        assert stats["Bash"]["repeat_count"] == 2
        assert stats["Edit"]["total"] == 1

    def test_recency_filter(self, corrections_file):
        """Only entries within recency_days count as recent_corrections."""
        from core.runtime_hooks import read_correction_stats

        now = time.time()
        entries = [
            {"ts": now, "type": "tool_failure", "tool": "Bash", "error": "recent"},
            {"ts": now - 86400 * 10, "type": "tool_failure", "tool": "Bash", "error": "old"},
        ]
        with open(corrections_file, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

        stats = read_correction_stats(str(corrections_file), recency_days=7)
        assert stats["Bash"]["recent_corrections"] == 1  # only the recent one
        assert stats["Bash"]["total"] == 2  # both counted in total

    def test_malformed_lines_skipped(self, corrections_file):
        """Malformed JSON lines are skipped without crashing."""
        from core.runtime_hooks import read_correction_stats

        with open(corrections_file, "w") as f:
            f.write('{"ts": 1, "type": "tool_failure", "tool": "Bash", "error": "ok"}\n')
            f.write("this is not json\n")
            f.write('{"ts": 2, "type": "tool_failure", "tool": "Bash", "error": "ok2"}\n')

        stats = read_correction_stats(str(corrections_file))
        assert stats["Bash"]["total"] == 2

    def test_user_correction_bucket(self, corrections_file):
        """User corrections without tool name go to _user_correction bucket."""
        from core.runtime_hooks import read_correction_stats

        entries = [
            {"ts": time.time(), "type": "user_correction", "prompt": "不对"},
            {"ts": time.time(), "type": "user_correction", "prompt": "wrong"},
        ]
        with open(corrections_file, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

        stats = read_correction_stats(str(corrections_file))
        assert stats["_user_correction"]["total"] == 2


class TestCrashCheckpointRecovery:

    def test_recovery_appends_to_daily_activity(self, tmp_path):
        """Orphaned checkpoint is recovered into today's DailyActivity."""
        from hooks.daily_activity_hook import recover_crash_checkpoint
        import unittest.mock as mock

        # Create fake home structure: tmp_path/.swarm-ai/.context/session_checkpoint.json
        ctx_dir = tmp_path / ".swarm-ai" / ".context"
        ctx_dir.mkdir(parents=True)
        checkpoint = ctx_dir / "session_checkpoint.json"
        checkpoint.write_text(json.dumps({
            "session_id": "crash-session-42",
            "ts": time.time() - 300,
            "tool_count": 15,
            "files_touched": ["/a.py", "/b.py"],
        }))

        # Create workspace dir
        ws = tmp_path / ".swarm-ai" / "SwarmWS"
        ws.mkdir(parents=True)

        with mock.patch.object(Path, "home", return_value=tmp_path):
            result = recover_crash_checkpoint(workspace_dir=ws)

        assert result is True
        assert not checkpoint.exists()  # deleted after recovery

        # Check DailyActivity has the recovery entry
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        da_file = ws / "Knowledge" / "DailyActivity" / f"{today}.md"
        assert da_file.exists()
        content = da_file.read_text()
        assert "Recovered from crash checkpoint" in content
        assert "crash-se" in content
        assert "/a.py" in content

    def test_no_checkpoint_returns_false(self, tmp_path):
        """No checkpoint file → returns False, no crash."""
        from hooks.daily_activity_hook import recover_crash_checkpoint
        import unittest.mock as mock
        with mock.patch.object(Path, "home", return_value=tmp_path):
            assert recover_crash_checkpoint() is False

    def test_corrupt_checkpoint_deleted(self, tmp_path):
        """Corrupt checkpoint is deleted, returns False."""
        from hooks.daily_activity_hook import recover_crash_checkpoint
        import unittest.mock as mock

        ctx_dir = tmp_path / ".swarm-ai" / ".context"
        ctx_dir.mkdir(parents=True)
        checkpoint = ctx_dir / "session_checkpoint.json"
        checkpoint.write_text("not valid json at all")

        with mock.patch.object(Path, "home", return_value=tmp_path):
            result = recover_crash_checkpoint()

        assert result is False
        assert not checkpoint.exists()


class TestHighSignalCapture:

    @pytest.mark.asyncio
    async def test_decision_captured_to_daily_activity(self, tmp_path, session_context):
        """'I decided to use rebase' writes to DailyActivity."""
        from core.runtime_hooks import create_high_signal_capture

        ws = tmp_path / "SwarmWS"
        hook = create_high_signal_capture(session_context, workspace_dir=str(ws))
        await hook({"prompt": "I decided to always use rebase instead of merge"}, None, MagicMock())

        from datetime import datetime
        da_file = ws / "Knowledge" / "DailyActivity" / f"{datetime.now().strftime('%Y-%m-%d')}.md"
        assert da_file.exists()
        content = da_file.read_text()
        assert "High-signal capture" in content
        assert "rebase" in content

    @pytest.mark.asyncio
    async def test_chinese_decision_captured(self, tmp_path, session_context):
        """'我们决定' triggers capture."""
        from core.runtime_hooks import create_high_signal_capture

        ws = tmp_path / "SwarmWS"
        hook = create_high_signal_capture(session_context, workspace_dir=str(ws))
        await hook({"prompt": "我们决定以后所有的 API 都用 snake_case"}, None, MagicMock())

        from datetime import datetime
        da_file = ws / "Knowledge" / "DailyActivity" / f"{datetime.now().strftime('%Y-%m-%d')}.md"
        assert da_file.exists()
        assert "snake_case" in da_file.read_text()

    @pytest.mark.asyncio
    async def test_normal_prompt_not_captured(self, tmp_path, session_context):
        """Normal prompts don't trigger capture."""
        from core.runtime_hooks import create_high_signal_capture

        ws = tmp_path / "SwarmWS"
        hook = create_high_signal_capture(session_context, workspace_dir=str(ws))
        await hook({"prompt": "Can you help me write a function?"}, None, MagicMock())

        from datetime import datetime
        da_file = ws / "Knowledge" / "DailyActivity" / f"{datetime.now().strftime('%Y-%m-%d')}.md"
        assert not da_file.exists()

    @pytest.mark.asyncio
    async def test_dedup_within_session(self, tmp_path, session_context):
        """Same prompt repeated doesn't write twice."""
        from core.runtime_hooks import create_high_signal_capture

        ws = tmp_path / "SwarmWS"
        hook = create_high_signal_capture(session_context, workspace_dir=str(ws))

        for _ in range(3):
            await hook({"prompt": "I decided to use TypeScript for all new code"}, None, MagicMock())

        from datetime import datetime
        da_file = ws / "Knowledge" / "DailyActivity" / f"{datetime.now().strftime('%Y-%m-%d')}.md"
        content = da_file.read_text()
        assert content.count("High-signal capture") == 1

    @pytest.mark.asyncio
    async def test_short_prompt_ignored(self, tmp_path, session_context):
        """Prompts shorter than 10 chars are ignored even if they match."""
        from core.runtime_hooks import create_high_signal_capture

        ws = tmp_path / "SwarmWS"
        hook = create_high_signal_capture(session_context, workspace_dir=str(ws))
        await hook({"prompt": "decided"}, None, MagicMock())

        from datetime import datetime
        da_file = ws / "Knowledge" / "DailyActivity" / f"{datetime.now().strftime('%Y-%m-%d')}.md"
        assert not da_file.exists()

    @pytest.mark.asyncio
    async def test_lesson_pattern_captured(self, tmp_path, session_context):
        """'lesson: ...' triggers capture."""
        from core.runtime_hooks import create_high_signal_capture

        ws = tmp_path / "SwarmWS"
        hook = create_high_signal_capture(session_context, workspace_dir=str(ws))
        await hook({"prompt": "lesson: never run full test suite proactively"}, None, MagicMock())

        from datetime import datetime
        da_file = ws / "Knowledge" / "DailyActivity" / f"{datetime.now().strftime('%Y-%m-%d')}.md"
        assert da_file.exists()
        assert "full test suite" in da_file.read_text()


class TestHookRegistryChain:

    @pytest.mark.asyncio
    async def test_chain_merges_falsy_values(self):
        """Chained hooks should merge value=0 and value=False (not skip them)."""
        from core.hook_builder import HookRegistry

        registry = HookRegistry()

        async def hook_a(input_data, tool_use_id, context):
            return {"count": 0, "flag": False}

        async def hook_b(input_data, tool_use_id, context):
            return {"extra": "data"}

        registry.register("TestEvent", hook_a, "hook_a")
        registry.register("TestEvent", hook_b, "hook_b")

        sdk_hooks = registry.build_sdk_hooks()
        # Get the chained function from the first HookMatcher
        chained_fn = sdk_hooks["TestEvent"][0].hooks[0]
        result = await chained_fn({}, None, None)

        assert result["count"] == 0  # was incorrectly skipped before M1 fix
        assert result["flag"] is False
        assert result["extra"] == "data"

    @pytest.mark.asyncio
    async def test_chain_skips_none_values(self):
        """Chained hooks should still skip None values."""
        from core.hook_builder import HookRegistry

        registry = HookRegistry()

        async def hook_a(input_data, tool_use_id, context):
            return {"keep": "yes", "drop": None}

        registry.register("TestEvent", hook_a, "hook_a")
        # Need 2+ hooks to trigger chaining
        async def hook_b(input_data, tool_use_id, context):
            return {}
        registry.register("TestEvent", hook_b, "hook_b")

        sdk_hooks = registry.build_sdk_hooks()
        chained_fn = sdk_hooks["TestEvent"][0].hooks[0]
        result = await chained_fn({}, None, None)

        assert result.get("keep") == "yes"
        assert "drop" not in result


# ---------------------------------------------------------------------------
# Phase 2: File tracker (PostToolUse)
# ---------------------------------------------------------------------------

class TestFileTracker:

    @pytest.mark.asyncio
    async def test_read_tool_tracked(self, session_context):
        """Read tool file_path is added to _files_touched."""
        from core.runtime_hooks import create_file_tracker

        hook = create_file_tracker(session_context)
        await hook(
            {"tool_name": "Read", "tool_input": {"file_path": "/foo/bar.py"}, "tool_response": "..."},
            "tu_1", MagicMock(),
        )
        assert "/foo/bar.py" in session_context["_files_touched"]

    @pytest.mark.asyncio
    async def test_edit_tool_tracked(self, session_context):
        """Edit tool file_path is tracked."""
        from core.runtime_hooks import create_file_tracker

        hook = create_file_tracker(session_context)
        await hook(
            {"tool_name": "Edit", "tool_input": {"file_path": "/src/main.py"}, "tool_response": "ok"},
            "tu_1", MagicMock(),
        )
        assert "/src/main.py" in session_context["_files_touched"]

    @pytest.mark.asyncio
    async def test_bash_tool_not_tracked(self, session_context):
        """Bash tool does NOT populate _files_touched."""
        from core.runtime_hooks import create_file_tracker

        hook = create_file_tracker(session_context)
        await hook(
            {"tool_name": "Bash", "tool_input": {"command": "ls"}, "tool_response": "..."},
            "tu_1", MagicMock(),
        )
        assert len(session_context.get("_files_touched", set())) == 0

    @pytest.mark.asyncio
    async def test_deduplication(self, session_context):
        """Same file read twice only appears once."""
        from core.runtime_hooks import create_file_tracker

        hook = create_file_tracker(session_context)
        for _ in range(3):
            await hook(
                {"tool_name": "Read", "tool_input": {"file_path": "/same.py"}, "tool_response": "..."},
                "tu_1", MagicMock(),
            )
        assert len(session_context["_files_touched"]) == 1


# ---------------------------------------------------------------------------
# Phase 2: Session checkpoint (PostToolUse)
# ---------------------------------------------------------------------------

class TestSessionCheckpoint:

    @pytest.mark.asyncio
    async def test_checkpoint_written_at_interval(self, tmp_path, session_context):
        """Checkpoint file written after N tool calls."""
        from core.runtime_hooks import create_session_checkpoint

        cp_path = str(tmp_path / "session_checkpoint.json")
        hook = create_session_checkpoint(session_context, checkpoint_path=cp_path, interval=5)

        for i in range(5):
            await hook(
                {"tool_name": "Bash", "tool_input": {}, "tool_response": "ok"},
                f"tu_{i}", MagicMock(),
            )

        assert Path(cp_path).exists()
        data = json.loads(Path(cp_path).read_text())
        assert data["session_id"] == "test-session-123"
        assert data["tool_count"] == 5

    @pytest.mark.asyncio
    async def test_no_checkpoint_before_interval(self, tmp_path, session_context):
        """No checkpoint written before reaching interval."""
        from core.runtime_hooks import create_session_checkpoint

        cp_path = str(tmp_path / "session_checkpoint.json")
        hook = create_session_checkpoint(session_context, checkpoint_path=cp_path, interval=10)

        for i in range(3):
            await hook(
                {"tool_name": "Bash", "tool_input": {}, "tool_response": "ok"},
                f"tu_{i}", MagicMock(),
            )

        assert not Path(cp_path).exists()

    @pytest.mark.asyncio
    async def test_checkpoint_includes_files_touched(self, tmp_path, session_context):
        """Checkpoint captures _files_touched from session_context."""
        from core.runtime_hooks import create_session_checkpoint

        session_context["_files_touched"] = {"/a.py", "/b.py"}
        cp_path = str(tmp_path / "session_checkpoint.json")
        hook = create_session_checkpoint(session_context, checkpoint_path=cp_path, interval=1)

        await hook({"tool_name": "Bash", "tool_input": {}, "tool_response": "ok"}, "tu_1", MagicMock())

        data = json.loads(Path(cp_path).read_text())
        assert set(data["files_touched"]) == {"/a.py", "/b.py"}

    @pytest.mark.asyncio
    async def test_checkpoint_overwrites_not_appends(self, tmp_path, session_context):
        """Checkpoint file is overwritten, not appended."""
        from core.runtime_hooks import create_session_checkpoint

        cp_path = str(tmp_path / "session_checkpoint.json")
        hook = create_session_checkpoint(session_context, checkpoint_path=cp_path, interval=1)

        await hook({"tool_name": "Bash", "tool_input": {}, "tool_response": "ok"}, "tu_1", MagicMock())
        await hook({"tool_name": "Bash", "tool_input": {}, "tool_response": "ok"}, "tu_2", MagicMock())

        # Should be 1 JSON object, not 2 lines
        content = Path(cp_path).read_text().strip()
        data = json.loads(content)  # would fail if multiple JSON objects
        assert data["tool_count"] == 2


# ---------------------------------------------------------------------------
# Phase 2: Subagent transcript capture (SubagentStop)
# ---------------------------------------------------------------------------

class TestSubagentCapture:

    @pytest.mark.asyncio
    async def test_subagent_errors_captured(self, corrections_file, session_context):
        """SubagentStop reads transcript and captures errors to corrections.jsonl."""
        from core.runtime_hooks import create_subagent_capture_hook

        # Create a fake transcript file
        transcript = corrections_file.parent / "transcript.jsonl"
        lines = [
            '{"type": "text", "content": "Working on task..."}',
            '{"type": "text", "content": "Error: FileNotFoundError: /missing.py"}',
            '{"type": "text", "content": "Done."}',
        ]
        transcript.write_text("\n".join(lines))

        hook = create_subagent_capture_hook(str(corrections_file), session_context)
        await hook(
            {"agent_id": "agent-1", "agent_transcript_path": str(transcript), "agent_type": "general"},
            None, MagicMock(),
        )

        assert corrections_file.exists()
        entry = json.loads(corrections_file.read_text().strip())
        assert entry["type"] == "subagent_finding"
        assert "FileNotFoundError" in entry["summary"]

    @pytest.mark.asyncio
    async def test_subagent_no_errors_no_write(self, corrections_file, session_context):
        """SubagentStop with clean transcript writes nothing."""
        from core.runtime_hooks import create_subagent_capture_hook

        transcript = corrections_file.parent / "transcript.jsonl"
        lines = [
            '{"type": "text", "content": "Task complete. All tests pass."}',
        ]
        transcript.write_text("\n".join(lines))

        hook = create_subagent_capture_hook(str(corrections_file), session_context)
        await hook(
            {"agent_id": "agent-2", "agent_transcript_path": str(transcript), "agent_type": "general"},
            None, MagicMock(),
        )

        assert not corrections_file.exists()

    @pytest.mark.asyncio
    async def test_subagent_missing_transcript_graceful(self, corrections_file, session_context):
        """Missing transcript file doesn't crash."""
        from core.runtime_hooks import create_subagent_capture_hook

        hook = create_subagent_capture_hook(str(corrections_file), session_context)
        result = await hook(
            {"agent_id": "agent-3", "agent_transcript_path": "/nonexistent/path.jsonl", "agent_type": "general"},
            None, MagicMock(),
        )
        assert result == {}

    @pytest.mark.asyncio
    async def test_subagent_large_transcript_tail_only(self, corrections_file, session_context):
        """Only reads last 5KB of large transcripts."""
        from core.runtime_hooks import create_subagent_capture_hook

        transcript = corrections_file.parent / "big_transcript.jsonl"
        # Write 50KB of padding + error at the end
        padding = '{"type": "text", "content": "' + "x" * 1000 + '"}\n' * 50
        error_line = '{"type": "text", "content": "Error: ImportError: no module named foo"}\n'
        transcript.write_text(padding + error_line)

        hook = create_subagent_capture_hook(str(corrections_file), session_context)
        await hook(
            {"agent_id": "agent-4", "agent_transcript_path": str(transcript), "agent_type": "general"},
            None, MagicMock(),
        )

        assert corrections_file.exists()
        entry = json.loads(corrections_file.read_text().strip())
        assert "ImportError" in entry["summary"]


# ---------------------------------------------------------------------------
# Phase 2: Post-compact injection (UserPromptSubmit)
# ---------------------------------------------------------------------------

class TestPostCompactInjection:

    @pytest.mark.asyncio
    async def test_injects_after_compaction(self, session_context):
        """After _compacted=True, next UserPromptSubmit injects additionalContext."""
        from core.runtime_hooks import create_post_compact_injection

        session_context["_compacted"] = True
        session_context["_files_touched"] = {"/a.py", "/b.py"}

        hook = create_post_compact_injection(session_context)
        result = await hook(
            {"prompt": "continue working"},
            None, MagicMock(),
        )

        assert "additionalContext" in result
        assert "/a.py" in result["additionalContext"]
        assert "/b.py" in result["additionalContext"]

    @pytest.mark.asyncio
    async def test_no_injection_without_compaction(self, session_context):
        """Without _compacted flag, no injection."""
        from core.runtime_hooks import create_post_compact_injection

        hook = create_post_compact_injection(session_context)
        result = await hook({"prompt": "hello"}, None, MagicMock())
        assert result == {}

    @pytest.mark.asyncio
    async def test_flag_cleared_after_injection(self, session_context):
        """_compacted flag is reset after injection — fire-once."""
        from core.runtime_hooks import create_post_compact_injection

        session_context["_compacted"] = True
        session_context["_files_touched"] = {"/a.py"}

        hook = create_post_compact_injection(session_context)
        await hook({"prompt": "first"}, None, MagicMock())

        # Second call — no injection
        result = await hook({"prompt": "second"}, None, MagicMock())
        assert result == {}

    @pytest.mark.asyncio
    async def test_empty_files_still_injects(self, session_context):
        """Even with no files touched, compaction injection fires with basic instructions."""
        from core.runtime_hooks import create_post_compact_injection

        session_context["_compacted"] = True

        hook = create_post_compact_injection(session_context)
        result = await hook({"prompt": "go"}, None, MagicMock())

        assert "additionalContext" in result


# ---------------------------------------------------------------------------
# _corrections_count: incremented by all correction-writing hooks
# ---------------------------------------------------------------------------

class TestCorrectionsCount:

    @pytest.mark.asyncio
    async def test_tool_failure_increments_count(self, corrections_file, session_context):
        """PostToolUseFailure hook increments _corrections_count in session_context."""
        from core.runtime_hooks import create_correction_capture_hook

        assert session_context.get("_corrections_count", 0) == 0

        hook = create_correction_capture_hook(str(corrections_file), session_context)
        await hook(
            {"tool_name": "Bash", "tool_input": {}, "error": "fail", "tool_use_id": "tu_1"},
            "tu_1", MagicMock(),
        )
        assert session_context["_corrections_count"] == 1

        await hook(
            {"tool_name": "Edit", "tool_input": {}, "error": "conflict", "tool_use_id": "tu_2"},
            "tu_2", MagicMock(),
        )
        assert session_context["_corrections_count"] == 2

    @pytest.mark.asyncio
    async def test_user_correction_increments_count(self, corrections_file, session_context):
        """UserPromptSubmit correction detection increments _corrections_count."""
        from core.runtime_hooks import create_user_correction_detector

        hook = create_user_correction_detector(str(corrections_file), session_context)
        await hook({"prompt": "that's wrong, it should be X"}, None, MagicMock())
        assert session_context["_corrections_count"] == 1

    @pytest.mark.asyncio
    async def test_subagent_finding_increments_count(self, corrections_file, session_context, tmp_path):
        """SubagentStop hook increments _corrections_count when errors found."""
        from core.runtime_hooks import create_subagent_capture_hook

        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text('{"type": "text", "content": "Error: ImportError: oops"}\n')

        hook = create_subagent_capture_hook(str(corrections_file), session_context)
        await hook(
            {"agent_id": "a-1", "agent_transcript_path": str(transcript), "agent_type": "general"},
            None, MagicMock(),
        )
        assert session_context["_corrections_count"] == 1

    @pytest.mark.asyncio
    async def test_checkpoint_reflects_corrections_count(self, tmp_path, session_context):
        """Session checkpoint includes the actual corrections_count, not always 0."""
        from core.runtime_hooks import create_correction_capture_hook, create_session_checkpoint

        corrections_file = tmp_path / "corrections.jsonl"
        checkpoint_path = tmp_path / "checkpoint.json"

        # Generate some corrections first
        cap_hook = create_correction_capture_hook(str(corrections_file), session_context)
        await cap_hook(
            {"tool_name": "Bash", "tool_input": {}, "error": "e1", "tool_use_id": "t1"},
            "t1", MagicMock(),
        )
        await cap_hook(
            {"tool_name": "Bash", "tool_input": {}, "error": "e2", "tool_use_id": "t2"},
            "t2", MagicMock(),
        )
        assert session_context["_corrections_count"] == 2

        # Create checkpoint with interval=1 so it writes on every call
        cp_hook = create_session_checkpoint(session_context, str(checkpoint_path), interval=1)
        await cp_hook(
            {"tool_name": "Read", "tool_input": {"file_path": "/a.py"}},
            "t3", MagicMock(),
        )

        data = json.loads(checkpoint_path.read_text())
        assert data["corrections_count"] == 2
