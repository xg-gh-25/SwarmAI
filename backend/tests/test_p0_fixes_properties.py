"""Property-based tests for P0 concurrency fixes.

Tests the ``_append_changelog`` function from
``hooks/evolution_maintenance_hook.py`` under concurrent write pressure
using Hypothesis-generated inputs and ``asyncio.gather``.

Testing methodology: property-based (Hypothesis) + async concurrency.
Key properties verified:

- **Property 14: EVOLUTION_CHANGELOG concurrent write safety**
    — For any set of concurrent ``_append_changelog`` calls writing to the
      same file, every entry appears exactly once, no entry is corrupted
      or partially written, and the file is valid JSONL.

# Feature: multi-session-rearchitecture, Property 14: EVOLUTION_CHANGELOG concurrent write safety
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import pytest
from hypothesis import given, settings, HealthCheck, strategies as st

from hooks.evolution_maintenance_hook import _append_changelog
from tests.helpers import PROPERTY_SETTINGS


# ---------------------------------------------------------------------------
# Hypothesis settings
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Generate (action, entry_id, summary) tuples with safe printable strings
changelog_entry = st.tuples(
    st.sampled_from(["deprecate", "prune", "create", "update"]),
    st.from_regex(r"[EOKC]\d{3}", fullmatch=True),
    st.text(
        alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z"), blacklist_characters="\n\r"),
        min_size=1,
        max_size=80,
    ),
)

changelog_entries = st.lists(changelog_entry, min_size=1, max_size=30)


# ---------------------------------------------------------------------------
# Property Tests
# ---------------------------------------------------------------------------


class TestChangelogConcurrentWriteSafety:
    """Property 14: EVOLUTION_CHANGELOG concurrent write safety.

    # Feature: multi-session-rearchitecture, Property 14: EVOLUTION_CHANGELOG concurrent write safety

    *For any* set of concurrent ``_append_changelog`` calls writing to the
    same EVOLUTION_CHANGELOG.jsonl file, every call's entry must appear
    exactly once in the final file, and no entry may be corrupted or
    partially written.  The file must be valid JSONL (one JSON object per
    line).

    **Validates: Requirements 5.1**
    """

    @given(entries=changelog_entries)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_concurrent_writes_produce_valid_complete_jsonl(
        self,
        tmp_path: Path,
        entries: list[tuple[str, str, str]],
    ):
        """All concurrent writes land exactly once and produce valid JSONL.

        **Validates: Requirements 5.1**
        """
        # Unique dir per Hypothesis example to avoid collisions
        work_dir = tmp_path / str(uuid4())
        work_dir.mkdir(parents=True, exist_ok=True)
        changelog_path = work_dir / "EVOLUTION_CHANGELOG.jsonl"

        # Write all entries concurrently via asyncio.gather
        async def write_entry(action: str, entry_id: str, summary: str) -> None:
            await asyncio.to_thread(
                _append_changelog, changelog_path, action, entry_id, summary
            )

        await asyncio.gather(
            *(write_entry(action, eid, summary) for action, eid, summary in entries)
        )

        # Read the resulting file
        raw = changelog_path.read_text(encoding="utf-8")
        lines = raw.strip().split("\n") if raw.strip() else []

        # --- Verify line count matches entry count ---
        assert len(lines) == len(entries), (
            f"Expected {len(entries)} lines, got {len(lines)}"
        )

        # --- Verify every line is valid JSON ---
        parsed = []
        for i, line in enumerate(lines):
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                pytest.fail(f"Line {i} is not valid JSON: {exc}\nContent: {line!r}")
            parsed.append(obj)

        # --- Verify each parsed object has the expected fields ---
        for i, obj in enumerate(parsed):
            assert "ts" in obj, f"Line {i} missing 'ts' field"
            assert "action" in obj, f"Line {i} missing 'action' field"
            assert "id" in obj, f"Line {i} missing 'id' field"
            assert "summary" in obj, f"Line {i} missing 'summary' field"
            assert "source" in obj, f"Line {i} missing 'source' field"

        # --- Verify every input entry appears exactly once ---
        # Build a set of (action, id, summary) from the written entries
        written_tuples = {(obj["action"], obj["id"], obj["summary"]) for obj in parsed}
        input_tuples = set(entries)

        # Since entries may contain duplicates (same action+id+summary),
        # compare as sorted lists for exact multiplicity matching
        written_sorted = sorted((obj["action"], obj["id"], obj["summary"]) for obj in parsed)
        input_sorted = sorted(entries)

        assert written_sorted == input_sorted, (
            f"Mismatch between input entries and written entries.\n"
            f"Missing from file: {sorted(set(input_sorted) - set(written_sorted))}\n"
            f"Extra in file: {sorted(set(written_sorted) - set(input_sorted))}"
        )

        # --- Verify file ends with newline (proper JSONL) ---
        assert raw.endswith("\n"), "JSONL file should end with a newline"


# ---------------------------------------------------------------------------
# Property 13: Hook execution serialization (integration test)
# ---------------------------------------------------------------------------

import time
from dataclasses import dataclass



@dataclass
class _ExecutionRecord:
    """Captured (session_id, start, end) for one hook execution."""
    session_id: str
    start: float
    end: float


class _TimingHook:
    """Mock hook that records execution timestamps.

    Each ``execute()`` call sleeps briefly so that overlapping
    executions would produce measurably overlapping intervals.
    """

    def __init__(self, records: list[_ExecutionRecord]) -> None:
        self._records = records

    @property
    def name(self) -> str:
        return "timing_hook"

    async def execute(self, context: "HookContext") -> None:  # noqa: F821
        start = time.monotonic()
        await asyncio.sleep(0.02)
        end = time.monotonic()
        self._records.append(
            _ExecutionRecord(
                session_id=context.session_id,
                start=start,
                end=end,
            )
        )


class TestHookExecutionSerialization:
    """Property 13: Hook execution serialization.

    # Feature: multi-session-rearchitecture, Property 13: Hook execution serialization

    *For any* sequence of hook submissions from multiple sessions, the
    ``BackgroundHookExecutor`` must execute hooks one at a time.  No two
    hook executions may overlap in time.

    This is an integration test using ``asyncio.gather`` + timing
    assertions (not Hypothesis — timing properties need real concurrency).

    **Validates: Requirements 4.3, 5.4**
    """

    @pytest.mark.asyncio
    async def test_no_two_hook_executions_overlap(self) -> None:
        """Fire hooks from multiple sessions concurrently, verify serial execution.

        **Validates: Requirements 4.3, 5.4**
        """
        from core.session_hooks import (
            BackgroundHookExecutor,
            HookContext,
            SessionLifecycleHookManager,
        )

        records: list[_ExecutionRecord] = []
        timing_hook = _TimingHook(records)

        manager = SessionLifecycleHookManager(timeout_seconds=5.0)
        manager.register(timing_hook)

        executor = BackgroundHookExecutor(hook_manager=manager)
        executor.start()

        # Build 8 contexts from different "sessions"
        contexts = [
            HookContext(
                session_id=f"sess-{i}",
                agent_id="agent-test",
                message_count=i,
                session_start_time="2025-01-01T00:00:00Z",
                session_title=f"Session {i}",
            )
            for i in range(8)
        ]

        # Fire all hooks concurrently (simulates multiple sessions closing)
        await asyncio.gather(*(
            asyncio.to_thread(executor.fire, ctx) for ctx in contexts
        ))

        # Drain — wait for all queued hooks to finish
        await executor.drain(timeout=10.0)

        # --- Verify all hooks completed ---
        assert len(records) == len(contexts), (
            f"Expected {len(contexts)} executions, got {len(records)}"
        )

        # --- Verify no two executions overlap ---
        sorted_records = sorted(records, key=lambda r: r.start)
        for i in range(1, len(sorted_records)):
            prev = sorted_records[i - 1]
            curr = sorted_records[i]
            assert prev.end <= curr.start, (
                f"Overlap detected: execution for '{prev.session_id}' "
                f"ended at {prev.end:.6f} but execution for "
                f"'{curr.session_id}' started at {curr.start:.6f}"
            )
