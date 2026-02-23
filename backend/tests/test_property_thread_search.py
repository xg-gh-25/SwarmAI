"""Property-based tests for ThreadSummary search indexing.

**Feature: workspace-refactor, Property 23: ThreadSummary search indexing**

Uses Hypothesis to verify that search queries ThreadSummary.summary_text
and ThreadSummary.key_decisions, NOT raw ChatMessages.content. This is the
critical distinction: the search index is built from summaries, not messages.

**Validates: Requirements 31.1-31.7**
"""
import pytest
from hypothesis import given, strategies as st, settings, HealthCheck, assume
from uuid import uuid4

from database import db
from core.search_manager import search_manager
from tests.helpers import ensure_default_workspace, create_custom_workspace, now_iso


PROPERTY_SETTINGS = settings(
    max_examples=2,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Generate unique marker strings that won't collide with other test data.
# We use a fixed prefix + random alphanumeric suffix so we can search for
# the marker and know exactly whether it should or should not appear.
_ALPHA_NUM = st.characters(whitelist_categories=("L", "N"))

marker_strategy = st.text(
    alphabet=_ALPHA_NUM,
    min_size=6,
    max_size=20,
).map(lambda s: f"MRK{s}MRK").filter(lambda s: len(s.strip()) >= 10)

summary_text_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    min_size=5,
    max_size=200,
).filter(lambda x: x.strip())

thread_title_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    min_size=1,
    max_size=100,
).filter(lambda x: x.strip())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_agent() -> dict:
    """Create a test agent and return its DB row."""
    now = now_iso()
    aid = str(uuid4())
    return await db.agents.put({
        "id": aid,
        "name": f"Agent-{aid[:8]}",
        "description": "Test agent for search",
        "model": "claude-sonnet-4-20250514",
        "permission_mode": "default",
        "is_default": False,
        "created_at": now,
        "updated_at": now,
    })


async def _create_thread(workspace_id: str, agent_id: str, title: str) -> dict:
    """Create a chat thread and return its DB row."""
    now = now_iso()
    thread = {
        "id": str(uuid4()),
        "workspace_id": workspace_id,
        "agent_id": agent_id,
        "mode": "explore",
        "title": title,
        "created_at": now,
        "updated_at": now,
    }
    await db.chat_threads.put(thread)
    return thread


async def _create_summary(thread_id: str, summary_text: str, key_decisions: str = None) -> dict:
    """Create a thread summary and return its DB row."""
    now = now_iso()
    summary = {
        "id": str(uuid4()),
        "thread_id": thread_id,
        "summary_type": "rolling",
        "summary_text": summary_text,
        "key_decisions": key_decisions,
        "updated_at": now,
    }
    await db.thread_summaries.put(summary)
    return summary


async def _create_message(thread_id: str, content: str, role: str = "user") -> dict:
    """Create a raw chat message and return its DB row."""
    now = now_iso()
    msg = {
        "id": str(uuid4()),
        "thread_id": thread_id,
        "role": role,
        "content": content,
        "created_at": now,
    }
    await db.chat_messages.put(msg)
    return msg


# ---------------------------------------------------------------------------
# Property Tests
# ---------------------------------------------------------------------------


class TestSearchQueriesSummaryText:
    """Property 23: ThreadSummary search indexing — summary_text is searchable.

    *For any* search query matching text in ThreadSummary.summary_text,
    the search SHALL return the associated thread.

    **Validates: Requirements 31.1, 31.5**
    """

    @given(marker=marker_strategy, title=thread_title_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_summary_text_is_searchable(
        self,
        marker: str,
        title: str,
    ):
        """Searching for text in ThreadSummary.summary_text returns the thread.

        **Validates: Requirements 31.1, 31.5**
        """
        ws_id = await create_custom_workspace()
        agent = await _create_agent()
        thread = await _create_thread(ws_id, agent["id"], title)
        await _create_summary(thread["id"], f"Discussion about {marker} in the codebase")

        result = await search_manager.search(marker)

        # The thread must appear in results via its summary
        thread_group = next(
            (g for g in result.groups if g.entity_type == "thread"), None
        )
        assert thread_group is not None, (
            f"Expected thread group in results when searching for marker "
            f"'{marker}' present in summary_text"
        )
        matched_ids = {item.id for item in thread_group.items}
        assert thread["id"] in matched_ids, (
            f"Thread {thread['id']} should be found via summary_text containing '{marker}'"
        )


class TestSearchQueriesKeyDecisions:
    """Property 23: ThreadSummary search indexing — key_decisions is searchable.

    *For any* search query matching text in ThreadSummary.key_decisions,
    the search SHALL return the associated thread.

    **Validates: Requirements 31.1, 31.5**
    """

    @given(marker=marker_strategy, title=thread_title_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_key_decisions_is_searchable(
        self,
        marker: str,
        title: str,
    ):
        """Searching for text in ThreadSummary.key_decisions returns the thread.

        **Validates: Requirements 31.5**
        """
        ws_id = await create_custom_workspace()
        agent = await _create_agent()
        thread = await _create_thread(ws_id, agent["id"], title)
        decisions = f'["Decided to use {marker} for the backend"]'
        await _create_summary(thread["id"], "General discussion", key_decisions=decisions)

        result = await search_manager.search(marker)

        thread_group = next(
            (g for g in result.groups if g.entity_type == "thread"), None
        )
        assert thread_group is not None, (
            f"Expected thread group in results when searching for marker "
            f"'{marker}' present in key_decisions"
        )
        matched_ids = {item.id for item in thread_group.items}
        assert thread["id"] in matched_ids, (
            f"Thread {thread['id']} should be found via key_decisions containing '{marker}'"
        )


class TestSearchDoesNotQueryRawMessages:
    """Property 23: ThreadSummary search indexing — raw messages NOT indexed.

    *For any* search query matching text that exists ONLY in
    ChatMessages.content (and NOT in any ThreadSummary), the search
    SHALL NOT return the thread. This is the critical property: search
    indexes summaries, not raw messages.

    **Validates: Requirements 31.1, 31.2**
    """

    @given(
        msg_marker=marker_strategy,
        summary_text=summary_text_strategy,
        title=thread_title_strategy,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_raw_message_content_not_searchable(
        self,
        msg_marker: str,
        summary_text: str,
        title: str,
    ):
        """Text only in ChatMessages.content is NOT returned by search.

        **Validates: Requirements 31.1, 31.2**
        """
        # Ensure the marker does NOT appear in the summary text
        assume(msg_marker not in summary_text)

        ws_id = await create_custom_workspace()
        agent = await _create_agent()
        thread = await _create_thread(ws_id, agent["id"], title)

        # Summary does NOT contain the marker
        await _create_summary(thread["id"], summary_text)

        # Raw message DOES contain the marker
        await _create_message(thread["id"], f"Raw message with {msg_marker} content")

        result = await search_manager.search(msg_marker)

        # The thread must NOT appear in results
        thread_group = next(
            (g for g in result.groups if g.entity_type == "thread"), None
        )
        if thread_group is not None:
            matched_ids = {item.id for item in thread_group.items}
            assert thread["id"] not in matched_ids, (
                f"Thread {thread['id']} should NOT be found when marker "
                f"'{msg_marker}' exists only in raw ChatMessages.content, "
                f"not in ThreadSummary"
            )
