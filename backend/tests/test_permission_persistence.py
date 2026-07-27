"""Tests for PermissionManager pending-request disk durability (Gate-1 Slice A).

WHAT IS TESTED
--------------
Before this change, `PermissionManager._pending_requests` was an in-memory dict on a
module singleton — it survived a subprocess respawn (the daemon keeps the singleton)
but NOT a daemon restart (new process → fresh empty dict). A pending approval in
flight when the daemon bounced was orphaned.

This change mirrors _pending_requests to a JSON file under ~/.swarm-ai (atomic
mkstemp+replace, matching escalation.py) and loads it on __init__. Idempotency is
keyed by (session_id, tool_call_id) so a restart-replayed resolve cannot double-fire.

Ported concept from andrewyng/openworker's Inbox (durable, resolve-from-anywhere,
tool_call_id-keyed).

KEY DESIGN (Gate-1 B2): the class is synchronous and had NO lock; mutate+persist is
now guarded by a threading.Lock so concurrent sessions cannot lose writes.
"""

from __future__ import annotations

import pytest

from core.permission_manager import PermissionManager


@pytest.fixture
def persist_path(tmp_path):
    return tmp_path / "pending_approvals.json"


class TestPersistenceSurvivesRestart:
    def test_pending_request_recovered_by_new_instance(self, persist_path):
        pm1 = PermissionManager(persist_path=persist_path)
        pm1.store_pending_request(
            {
                "id": "perm_abc",
                "session_id": "sess1",
                "tool_name": "mcp__slack-mcp__post_message",
                "tool_input": "{}",
                "tool_call_id": "toolu_123",
                "reason": "external op",
                "status": "pending",
                "created_at": "2026-07-27T00:00:00",
            }
        )
        # Simulate a daemon restart: a brand-new PermissionManager (new process).
        pm2 = PermissionManager(persist_path=persist_path)
        recovered = pm2.get_pending_request("perm_abc")
        assert recovered is not None
        assert recovered["tool_call_id"] == "toolu_123"
        assert recovered["session_id"] == "sess1"

    def test_removed_request_retained_as_resolved_not_pending(self, persist_path):
        # NOTE: remove_pending_request MARKS resolved (not delete) — the Gate-2 fix.
        # The record is retained across restart for idempotency, but must no longer
        # count as a live PENDING prompt. (The old test asserting get_pending_request
        # returns None encoded the delete-on-remove bug that made is_resolved dead code.)
        pm1 = PermissionManager(persist_path=persist_path)
        pm1.store_pending_request(
            {"id": "perm_x", "session_id": "s", "tool_call_id": "tx", "status": "pending"}
        )
        pm1.remove_pending_request("perm_x")
        pm2 = PermissionManager(persist_path=persist_path)
        rec = pm2.get_pending_request("perm_x")
        assert rec is not None and rec["status"] != "pending"  # retained, resolved
        assert pm2.get_pending_for_session("s") == []  # NOT a live pending prompt

    def test_update_persisted(self, persist_path):
        pm1 = PermissionManager(persist_path=persist_path)
        pm1.store_pending_request(
            {"id": "perm_u", "session_id": "s", "status": "pending"}
        )
        pm1.update_pending_request("perm_u", {"status": "approve"})
        pm2 = PermissionManager(persist_path=persist_path)
        assert pm2.get_pending_request("perm_u")["status"] == "approve"


class TestIdempotency:
    def test_is_resolved_by_session_and_tool_call_id(self, persist_path):
        pm = PermissionManager(persist_path=persist_path)
        pm.store_pending_request(
            {
                "id": "perm_1",
                "session_id": "sess1",
                "tool_call_id": "toolu_9",
                "status": "pending",
            }
        )
        # Not resolved yet.
        assert pm.is_resolved("sess1", "toolu_9") is False
        pm.update_pending_request("perm_1", {"status": "approve"})
        # Now resolved — a restart-replayed decision for the same key is a no-op.
        assert pm.is_resolved("sess1", "toolu_9") is True

    def test_is_resolved_false_for_unknown_key(self, persist_path):
        pm = PermissionManager(persist_path=persist_path)
        assert pm.is_resolved("nope", "nope") is False


class TestRealGateSequenceIdempotency:
    """Drives the REAL external_approval_gate resolution sequence — store(pending) →
    (decision) → remove — NOT the manual update_pending_request shortcut. This is the
    path production runs; the earlier test that manually set status="approve" was
    test-theater (it proved a path the gate never executes). Adversarial Gate-2 catch."""

    def test_resolved_survives_remove_and_restart(self, persist_path):
        pm1 = PermissionManager(persist_path=persist_path)
        pm1.store_pending_request(
            {
                "id": "perm_g",
                "session_id": "sess1",
                "tool_call_id": "toolu_g",
                "tool_name": "mcp__slack-mcp__post_message",
                "status": "pending",
            }
        )
        # The REAL gate calls remove_pending_request after the decision — it must NOT
        # erase the idempotency record.
        pm1.remove_pending_request("perm_g")
        # After a daemon restart, the resolved decision must still be recognizable so
        # a replayed tool call is NOT re-prompted.
        pm2 = PermissionManager(persist_path=persist_path)
        assert pm2.is_resolved("sess1", "toolu_g") is True, (
            "is_resolved must survive the gate's remove() + a restart — "
            "otherwise the idempotency guard is dead code"
        )

    def test_get_pending_for_session_excludes_resolved(self, persist_path):
        # A resolved (kept-for-idempotency) record must NOT re-surface as a live prompt.
        pm = PermissionManager(persist_path=persist_path)
        pm.store_pending_request(
            {"id": "perm_r", "session_id": "s", "tool_call_id": "t", "status": "pending"}
        )
        pm.remove_pending_request("perm_r")
        assert pm.get_pending_for_session("s") == []

    def test_resolved_records_pruned_to_bound_growth(self, persist_path):
        # Resolved records are kept for idempotency but must be bounded, or the store
        # grows without limit. Store many, resolve all, reload → count is capped.
        pm = PermissionManager(persist_path=persist_path)
        for i in range(PermissionManager.MAX_RESOLVED_RETAINED + 50):
            rid = f"perm_{i}"
            pm.store_pending_request(
                {"id": rid, "session_id": "s", "tool_call_id": f"t{i}", "status": "pending"}
            )
            pm.remove_pending_request(rid)
        pm2 = PermissionManager(persist_path=persist_path)
        resolved = [
            r for r in pm2._pending_requests.values()
            if r.get("status") != "pending"
        ]
        assert len(resolved) <= PermissionManager.MAX_RESOLVED_RETAINED


class TestBackCompat:
    def test_default_no_persist_path_is_in_memory(self):
        # A PermissionManager with no persist_path must still work (never crash on
        # save), preserving the old in-memory behavior for callers that don't opt in.
        pm = PermissionManager()
        pm.store_pending_request({"id": "p", "session_id": "s", "status": "pending"})
        assert pm.get_pending_request("p") is not None

    def test_corrupt_persist_file_does_not_crash_init(self, persist_path):
        persist_path.write_text("{ this is not valid json", encoding="utf-8")
        # Load must fail-open (empty store), never crash the daemon on boot.
        pm = PermissionManager(persist_path=persist_path)
        assert pm.get_pending_request("anything") is None
