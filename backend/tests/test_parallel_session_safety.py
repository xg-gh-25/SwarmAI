"""Tests for parallel session safety — Priority 1 fixes.

Validates the three Priority 1 fixes for multi-chat parallel execution:

1. Per-session permission queues (no cross-session starvation)
2. Per-session concurrency lock (no double-send corruption)
3. SQLite WAL mode (no concurrent read/write contention)

These tests verify that parallel chat sessions work independently
without impacting each other.
"""

import asyncio
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Fix 1: Per-session permission queues
# ---------------------------------------------------------------------------


class TestPerSessionPermissionQueues:
    """Verify permission requests route directly to the correct session."""

    def test_get_session_queue_creates_queue_lazily(self):
        """Each session gets its own queue on first access."""
        from core.permission_manager import PermissionManager

        pm = PermissionManager()
        q1 = pm.get_session_queue("session-A")
        q2 = pm.get_session_queue("session-B")

        assert q1 is not q2, "Different sessions must get different queues"
        assert isinstance(q1, asyncio.Queue)
        assert isinstance(q2, asyncio.Queue)

    def test_get_session_queue_returns_same_queue_for_same_session(self):
        """Repeated access to the same session returns the same queue instance."""
        from core.permission_manager import PermissionManager

        pm = PermissionManager()
        q1 = pm.get_session_queue("session-X")
        q2 = pm.get_session_queue("session-X")

        assert q1 is q2, "Same session must return same queue instance"

    def test_remove_session_queue_cleans_up(self):
        """remove_session_queue discards the queue and frees memory."""
        from core.permission_manager import PermissionManager

        pm = PermissionManager()
        q = pm.get_session_queue("session-Z")
        assert q is not None

        pm.remove_session_queue("session-Z")
        # After removal, a new call should create a fresh queue
        q2 = pm.get_session_queue("session-Z")
        assert q2 is not q, "After removal, a new queue should be created"

    def test_remove_session_queue_noop_for_unknown(self):
        """remove_session_queue for unknown session doesn't raise."""
        from core.permission_manager import PermissionManager

        pm = PermissionManager()
        pm.remove_session_queue("nonexistent")  # Should not raise

    @pytest.mark.asyncio
    async def test_enqueue_permission_request_routes_to_session(self):
        """enqueue_permission_request puts the request in the session's queue."""
        from core.permission_manager import PermissionManager

        pm = PermissionManager()
        request = {"requestId": "perm_123", "sessionId": "session-A"}

        await pm.enqueue_permission_request("session-A", request)

        q = pm.get_session_queue("session-A")
        assert not q.empty()
        item = await q.get()
        assert item["requestId"] == "perm_123"

    @pytest.mark.asyncio
    async def test_parallel_sessions_dont_cross_contaminate(self):
        """Permission requests from session A never appear in session B's queue."""
        from core.permission_manager import PermissionManager

        pm = PermissionManager()
        req_a = {"requestId": "perm_A", "sessionId": "session-A"}
        req_b = {"requestId": "perm_B", "sessionId": "session-B"}

        await pm.enqueue_permission_request("session-A", req_a)
        await pm.enqueue_permission_request("session-B", req_b)

        q_a = pm.get_session_queue("session-A")
        q_b = pm.get_session_queue("session-B")

        item_a = await q_a.get()
        item_b = await q_b.get()

        assert item_a["requestId"] == "perm_A"
        assert item_b["requestId"] == "perm_B"

        # Both queues should now be empty — no cross-contamination
        assert q_a.empty()
        assert q_b.empty()

    @pytest.mark.asyncio
    async def test_no_busy_loop_under_parallel_load(self):
        """Multiple sessions consuming concurrently don't busy-loop.

        The old design re-enqueued non-matching requests with sleep(0.01),
        causing O(N) re-queues. The new design has zero re-queues.
        """
        from core.permission_manager import PermissionManager

        pm = PermissionManager()
        num_sessions = 5
        requests_per_session = 3

        # Enqueue requests for each session
        for i in range(num_sessions):
            session_id = f"session-{i}"
            for j in range(requests_per_session):
                await pm.enqueue_permission_request(session_id, {
                    "requestId": f"perm_{i}_{j}",
                    "sessionId": session_id,
                })

        # Each session's queue should have exactly its own requests
        for i in range(num_sessions):
            q = pm.get_session_queue(f"session-{i}")
            assert q.qsize() == requests_per_session
            for j in range(requests_per_session):
                item = await q.get()
                assert item["sessionId"] == f"session-{i}"


# ---------------------------------------------------------------------------
# Fix 2: Per-session concurrency lock
# ---------------------------------------------------------------------------


class TestPerSessionConcurrencyLock:
    """Verify that concurrent execution on the same session is rejected."""

    def test_session_lock_created_lazily(self):
        """_get_session_lock creates a lock on first access."""
        from core.agent_manager import AgentManager

        am = AgentManager()
        lock = am._get_session_lock("test-session")
        assert isinstance(lock, asyncio.Lock)

    def test_same_session_returns_same_lock(self):
        """Repeated access to the same session returns the same lock."""
        from core.agent_manager import AgentManager

        am = AgentManager()
        lock1 = am._get_session_lock("test-session")
        lock2 = am._get_session_lock("test-session")
        assert lock1 is lock2

    def test_different_sessions_get_different_locks(self):
        """Different sessions get independent locks."""
        from core.agent_manager import AgentManager

        am = AgentManager()
        lock_a = am._get_session_lock("session-A")
        lock_b = am._get_session_lock("session-B")
        assert lock_a is not lock_b

    @pytest.mark.asyncio
    async def test_session_busy_yields_error(self):
        """When a session is already executing, the second caller gets SESSION_BUSY."""
        from core.agent_manager import AgentManager

        am = AgentManager()

        # Manually acquire the lock to simulate a running session
        lock = am._get_session_lock("busy-session")
        await lock.acquire()

        try:
            # Attempt to execute on the same session — should fail with SESSION_BUSY
            events = []
            async for event in am._execute_on_session(
                agent_config={"id": "default"},
                query_content="test",
                display_text="test",
                session_id="busy-session",
                enable_skills=False,
                enable_mcp=False,
                is_resuming=True,
                content=None,
                user_message="test",
                agent_id="default",
                app_session_id="busy-session",
            ):
                events.append(event)

            assert len(events) == 1
            assert events[0]["code"] == "SESSION_BUSY"
            assert "still processing" in events[0]["error"]
        finally:
            lock.release()

    @pytest.mark.asyncio
    async def test_different_sessions_not_blocked(self):
        """Locking session A does NOT block session B."""
        from core.agent_manager import AgentManager

        am = AgentManager()

        lock_a = am._get_session_lock("session-A")
        await lock_a.acquire()

        try:
            lock_b = am._get_session_lock("session-B")
            # Session B's lock should be available
            assert not lock_b.locked()
        finally:
            lock_a.release()


# ---------------------------------------------------------------------------
# Fix 3: SQLite WAL mode
# ---------------------------------------------------------------------------


class TestSQLiteWALMode:
    """Verify that SQLite connections use WAL mode and busy timeout."""

    @pytest.mark.asyncio
    async def test_wal_connection_enables_wal_mode(self):
        """_WALConnection sets journal_mode=WAL on first use."""
        import tempfile
        import aiosqlite
        from database.sqlite import _WALConnection

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        # Clear the class-level cache so our test db_path is fresh
        _WALConnection._wal_initialized.discard(db_path)

        try:
            async with _WALConnection(db_path) as conn:
                cursor = await conn.execute("PRAGMA journal_mode")
                row = await cursor.fetchone()
                assert row[0] == "wal", f"Expected WAL mode, got {row[0]}"

                cursor = await conn.execute("PRAGMA busy_timeout")
                row = await cursor.fetchone()
                assert row[0] == 5000, f"Expected busy_timeout=5000, got {row[0]}"
        finally:
            Path(db_path).unlink(missing_ok=True)
            Path(db_path + "-wal").unlink(missing_ok=True)
            Path(db_path + "-shm").unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_wal_mode_persists_across_connections(self):
        """WAL mode set by first connection persists for subsequent connections."""
        import tempfile
        from database.sqlite import _WALConnection

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        _WALConnection._wal_initialized.discard(db_path)

        try:
            # First connection enables WAL
            async with _WALConnection(db_path) as conn:
                cursor = await conn.execute("PRAGMA journal_mode")
                row = await cursor.fetchone()
                assert row[0] == "wal"

            # Second connection should also be WAL (persisted in DB file)
            async with _WALConnection(db_path) as conn:
                cursor = await conn.execute("PRAGMA journal_mode")
                row = await cursor.fetchone()
                assert row[0] == "wal"
        finally:
            Path(db_path).unlink(missing_ok=True)
            Path(db_path + "-wal").unlink(missing_ok=True)
            Path(db_path + "-shm").unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_concurrent_writes_dont_raise_busy(self):
        """Two parallel writes to the same WAL-mode DB don't raise SQLITE_BUSY."""
        import tempfile
        from database.sqlite import _WALConnection

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        _WALConnection._wal_initialized.discard(db_path)

        try:
            # Create a table
            async with _WALConnection(db_path) as conn:
                await conn.execute(
                    "CREATE TABLE IF NOT EXISTS test (id TEXT PRIMARY KEY, data TEXT)"
                )
                await conn.commit()

            # Run two concurrent inserts
            async def insert_row(row_id: str, data: str):
                async with _WALConnection(db_path) as conn:
                    await conn.execute(
                        "INSERT INTO test (id, data) VALUES (?, ?)",
                        (row_id, data),
                    )
                    await conn.commit()

            # Both should complete without SQLITE_BUSY
            await asyncio.gather(
                insert_row("row-1", "data-1"),
                insert_row("row-2", "data-2"),
            )

            # Verify both rows exist
            async with _WALConnection(db_path) as conn:
                cursor = await conn.execute("SELECT COUNT(*) FROM test")
                row = await cursor.fetchone()
                assert row[0] == 2, f"Expected 2 rows, got {row[0]}"
        finally:
            Path(db_path).unlink(missing_ok=True)
            Path(db_path + "-wal").unlink(missing_ok=True)
            Path(db_path + "-shm").unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Integration: parallel sessions E2E simulation
# ---------------------------------------------------------------------------


class TestParallelSessionIntegration:
    """Integration tests simulating real parallel chat session scenarios."""

    @pytest.mark.asyncio
    async def test_permission_queues_isolated_under_parallel_streams(self):
        """Simulate two parallel sessions both getting permission requests.

        In the old design, both sessions' forwarder tasks would compete
        for the same global queue, causing re-enqueues and delays.
        In the new design, each session reads directly from its own queue.
        """
        from core.permission_manager import PermissionManager

        pm = PermissionManager()
        received_a = []
        received_b = []

        # Simulate two session forwarders reading from their queues
        async def session_consumer(session_id: str, results: list):
            q = pm.get_session_queue(session_id)
            while True:
                try:
                    item = await asyncio.wait_for(q.get(), timeout=0.1)
                    results.append(item)
                except asyncio.TimeoutError:
                    break

        # Enqueue permission requests for both sessions
        for i in range(3):
            await pm.enqueue_permission_request("sess-A", {
                "requestId": f"A-{i}", "sessionId": "sess-A"
            })
            await pm.enqueue_permission_request("sess-B", {
                "requestId": f"B-{i}", "sessionId": "sess-B"
            })

        # Both consumers run concurrently
        await asyncio.gather(
            session_consumer("sess-A", received_a),
            session_consumer("sess-B", received_b),
        )

        # Verify perfect isolation
        assert len(received_a) == 3
        assert len(received_b) == 3
        assert all(r["sessionId"] == "sess-A" for r in received_a)
        assert all(r["sessionId"] == "sess-B" for r in received_b)


# ---------------------------------------------------------------------------
# Fix: _pending_requests memory leak
# ---------------------------------------------------------------------------


class TestPendingRequestsCleanup:
    """Verify that _pending_requests are cleaned up after decisions."""

    def test_pending_request_stored_and_retrieved(self):
        """store_pending_request / get_pending_request round-trip."""
        from core.permission_manager import PermissionManager

        pm = PermissionManager()
        req = {"id": "perm_abc", "session_id": "s1", "status": "pending"}
        pm.store_pending_request(req)

        assert pm.get_pending_request("perm_abc") is not None
        assert pm.get_pending_request("perm_abc")["status"] == "pending"

    def test_remove_pending_request_frees_memory(self):
        """remove_pending_request removes the entry from the dict."""
        from core.permission_manager import PermissionManager

        pm = PermissionManager()
        pm.store_pending_request({"id": "perm_123", "status": "pending"})
        assert pm.get_pending_request("perm_123") is not None

        pm.remove_pending_request("perm_123")
        assert pm.get_pending_request("perm_123") is None

    def test_remove_pending_request_noop_for_unknown(self):
        """remove_pending_request for unknown ID doesn't raise."""
        from core.permission_manager import PermissionManager

        pm = PermissionManager()
        pm.remove_pending_request("nonexistent")  # Should not raise

    def test_update_then_remove_pending_request(self):
        """update + remove leaves no trace in _pending_requests."""
        from core.permission_manager import PermissionManager

        pm = PermissionManager()
        pm.store_pending_request({"id": "perm_456", "status": "pending"})
        pm.update_pending_request("perm_456", {"status": "approve"})
        assert pm.get_pending_request("perm_456")["status"] == "approve"

        pm.remove_pending_request("perm_456")
        assert pm.get_pending_request("perm_456") is None


# ---------------------------------------------------------------------------
# Fix: _approved_commands memory leak
# ---------------------------------------------------------------------------


class TestApprovedCommandsCleanup:
    """Verify that _approved_commands are cleaned up when sessions end."""

    def test_approve_and_check_round_trip(self):
        """approve_command / is_command_approved round-trip."""
        from core.permission_manager import PermissionManager

        pm = PermissionManager()
        pm.approve_command("session-1", "rm -rf /tmp/test")
        assert pm.is_command_approved("session-1", "rm -rf /tmp/test")
        assert not pm.is_command_approved("session-1", "rm -rf /other")

    def test_clear_session_approvals_frees_memory(self):
        """clear_session_approvals removes all approved commands for a session."""
        from core.permission_manager import PermissionManager

        pm = PermissionManager()
        pm.approve_command("session-X", "cmd1")
        pm.approve_command("session-X", "cmd2")
        assert pm.is_command_approved("session-X", "cmd1")
        assert pm.is_command_approved("session-X", "cmd2")

        pm.clear_session_approvals("session-X")
        assert not pm.is_command_approved("session-X", "cmd1")
        assert not pm.is_command_approved("session-X", "cmd2")

    def test_clear_session_approvals_noop_for_unknown(self):
        """clear_session_approvals for unknown session doesn't raise."""
        from core.permission_manager import PermissionManager

        pm = PermissionManager()
        pm.clear_session_approvals("nonexistent")  # Should not raise

    def test_clear_one_session_does_not_affect_others(self):
        """Clearing session A's approvals leaves session B's intact."""
        from core.permission_manager import PermissionManager

        pm = PermissionManager()
        pm.approve_command("session-A", "dangerous-cmd")
        pm.approve_command("session-B", "dangerous-cmd")

        pm.clear_session_approvals("session-A")
        assert not pm.is_command_approved("session-A", "dangerous-cmd")
        assert pm.is_command_approved("session-B", "dangerous-cmd")
