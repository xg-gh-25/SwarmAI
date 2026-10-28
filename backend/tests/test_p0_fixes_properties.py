"""Property-based tests for P0 concurrency fixes.

Testing methodology: property-based (Hypothesis) + async concurrency.
Key property verified: post-session hook executions never overlap
(``TestHookExecutionSerialization``).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import pytest
from hypothesis import given, strategies as st

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
