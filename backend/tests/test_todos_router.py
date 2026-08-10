"""Unit tests for ToDo/Signal API router endpoints.

Tests CRUD operations, conversion to task, pagination, filtering,
and error responses for the /api/todos endpoints.

Requirements: 6.1-6.8
"""
import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def workspace_id(client: TestClient) -> str:
    """Return the singleton workspace ID after seeding workspace_config."""
    import asyncio
    from tests.helpers import ensure_default_workspace
    return asyncio.run(ensure_default_workspace())


@pytest.fixture
def second_workspace_id(client: TestClient) -> str:
    """Return the singleton workspace ID (same as workspace_id in single-workspace model)."""
    import asyncio
    from tests.helpers import ensure_default_workspace
    return asyncio.run(ensure_default_workspace())


@pytest.fixture
def sample_todo(workspace_id: str) -> dict:
    """Sample todo creation payload."""
    return {
        "workspace_id": workspace_id,
        "title": "Review PR #42",
        "description": "Review the pull request for the auth module",
        "source": "github",
        "source_type": "integration",
        "priority": "high",
    }


def _create_todo(client: TestClient, workspace_id: str, **overrides) -> dict:
    """Helper to create a todo and return the response JSON."""
    payload = {
        "workspace_id": workspace_id,
        "title": overrides.pop("title", "Test ToDo"),
        **overrides,
    }
    resp = client.post("/api/todos", json=payload)
    assert resp.status_code == 201
    return resp.json()


# ---------------------------------------------------------------------------
# CRUD Tests
# ---------------------------------------------------------------------------

class TestCreateTodo:
    """Tests for POST /api/todos. Validates: Requirement 6.2"""

    def test_create_todo_success(self, client: TestClient, sample_todo: dict):
        resp = client.post("/api/todos", json=sample_todo)
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == sample_todo["title"]
        assert data["description"] == sample_todo["description"]
        assert data["source_type"] == "integration"
        assert data["priority"] == "high"
        assert data["status"] == "pending"
        assert data["workspace_id"] == sample_todo["workspace_id"]
        assert "id" in data
        assert "created_at" in data
        assert "updated_at" in data

    def test_create_todo_minimal(self, client: TestClient, workspace_id: str):
        """Only workspace_id and title are required."""
        resp = client.post("/api/todos", json={
            "workspace_id": workspace_id,
            "title": "Minimal todo",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "Minimal todo"
        assert data["source_type"] == "manual"
        assert data["priority"] == "none"
        assert data["status"] == "pending"
        assert data["description"] is None

    def test_create_todo_with_due_date(self, client: TestClient, workspace_id: str):
        resp = client.post("/api/todos", json={
            "workspace_id": workspace_id,
            "title": "Due soon",
            "due_date": "2099-12-31T23:59:59Z",
        })
        assert resp.status_code == 201
        assert resp.json()["due_date"] is not None

    def test_create_todo_missing_title(self, client: TestClient, workspace_id: str):
        resp = client.post("/api/todos", json={"workspace_id": workspace_id})
        assert resp.status_code in (400, 422)

    def test_create_todo_missing_workspace_id(self, client: TestClient):
        resp = client.post("/api/todos", json={"title": "No workspace"})
        assert resp.status_code in (400, 422)


class TestGetTodo:
    """Tests for GET /api/todos/{id}. Validates: Requirement 6.3"""

    def test_get_todo_success(self, client: TestClient, workspace_id: str):
        created = _create_todo(client, workspace_id, title="Fetch me")
        resp = client.get(f"/api/todos/{created['id']}")
        assert resp.status_code == 200
        assert resp.json()["id"] == created["id"]
        assert resp.json()["title"] == "Fetch me"

    def test_get_todo_not_found(self, client: TestClient):
        resp = client.get("/api/todos/nonexistent-id-999")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()


class TestUpdateTodo:
    """Tests for PUT /api/todos/{id}. Validates: Requirement 6.4"""

    def test_update_todo_title(self, client: TestClient, workspace_id: str):
        created = _create_todo(client, workspace_id, title="Old title")
        resp = client.put(f"/api/todos/{created['id']}", json={"title": "New title"})
        assert resp.status_code == 200
        assert resp.json()["title"] == "New title"

    def test_update_todo_status(self, client: TestClient, workspace_id: str):
        created = _create_todo(client, workspace_id)
        resp = client.put(f"/api/todos/{created['id']}", json={"status": "in_discussion"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "in_discussion"

    def test_update_todo_priority(self, client: TestClient, workspace_id: str):
        created = _create_todo(client, workspace_id)
        resp = client.put(f"/api/todos/{created['id']}", json={"priority": "medium"})
        assert resp.status_code == 200
        assert resp.json()["priority"] == "medium"

    def test_update_todo_not_found(self, client: TestClient):
        resp = client.put("/api/todos/nonexistent-id-999", json={"title": "Nope"})
        assert resp.status_code == 404

    def test_update_todo_partial(self, client: TestClient, workspace_id: str):
        """Partial update should only change provided fields."""
        created = _create_todo(client, workspace_id, title="Keep me", priority="high")
        resp = client.put(f"/api/todos/{created['id']}", json={"description": "Added desc"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Keep me"
        assert data["priority"] == "high"
        assert data["description"] == "Added desc"


class TestDeleteTodo:
    """Tests for DELETE /api/todos/{id}. Validates: Requirement 6.5"""

    def test_delete_todo_success(self, client: TestClient, workspace_id: str):
        created = _create_todo(client, workspace_id)
        resp = client.delete(f"/api/todos/{created['id']}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    def test_delete_todo_soft_deletes(self, client: TestClient, workspace_id: str):
        """Delete should set status to 'deleted', not remove the record."""
        created = _create_todo(client, workspace_id)
        client.delete(f"/api/todos/{created['id']}")
        # The todo should still be retrievable
        resp = client.get(f"/api/todos/{created['id']}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    def test_delete_todo_not_found(self, client: TestClient):
        resp = client.delete("/api/todos/nonexistent-id-999")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Convert to Task
# ---------------------------------------------------------------------------

class TestConvertToTask:
    """Tests for POST /api/todos/{id}/convert-to-task. Validates: Requirement 6.6"""

    def test_convert_success(self, client: TestClient, workspace_id: str):
        created = _create_todo(client, workspace_id, title="Convert me", priority="high")
        resp = client.post(f"/api/todos/{created['id']}/convert-to-task", json={
            "agent_id": "default",
        })
        assert resp.status_code == 200
        task = resp.json()
        assert task["title"] == "Convert me"
        assert task["status"] == "draft"
        assert task["source_todo_id"] == created["id"]
        assert task["workspace_id"] == workspace_id

    def test_convert_updates_todo_status(self, client: TestClient, workspace_id: str):
        """After conversion, the ToDo status should be 'handled' with task_id set."""
        created = _create_todo(client, workspace_id, title="Handle me")
        resp = client.post(f"/api/todos/{created['id']}/convert-to-task", json={
            "agent_id": "default",
        })
        assert resp.status_code == 200
        task_id = resp.json()["id"]

        todo_resp = client.get(f"/api/todos/{created['id']}")
        assert todo_resp.status_code == 200
        todo = todo_resp.json()
        assert todo["status"] == "handled"
        assert todo["task_id"] == task_id

    def test_convert_with_overrides(self, client: TestClient, workspace_id: str):
        created = _create_todo(client, workspace_id, title="Original title")
        resp = client.post(f"/api/todos/{created['id']}/convert-to-task", json={
            "agent_id": "default",
            "title": "Overridden title",
            "description": "Custom description",
            "priority": "low",
        })
        assert resp.status_code == 200
        task = resp.json()
        assert task["title"] == "Overridden title"
        assert task["description"] == "Custom description"

    def test_convert_not_found(self, client: TestClient):
        resp = client.post("/api/todos/nonexistent-id-999/convert-to-task", json={
            "agent_id": "default",
        })
        assert resp.status_code == 404

    def test_convert_invalid_agent(self, client: TestClient, workspace_id: str):
        created = _create_todo(client, workspace_id)
        resp = client.post(f"/api/todos/{created['id']}/convert-to-task", json={
            "agent_id": "nonexistent-agent-xyz",
        })
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# List, Pagination, and Filtering
# ---------------------------------------------------------------------------

class TestListTodos:
    """Tests for GET /api/todos. Validates: Requirements 6.1, 6.8"""

    def test_list_empty(self, client: TestClient):
        resp = client.get("/api/todos")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_returns_created_todos(self, client: TestClient, workspace_id: str):
        _create_todo(client, workspace_id, title="First")
        _create_todo(client, workspace_id, title="Second")
        resp = client.get("/api/todos")
        assert resp.status_code == 200
        titles = [t["title"] for t in resp.json()]
        assert "First" in titles
        assert "Second" in titles

    def test_list_filter_by_workspace(
        self, client: TestClient, workspace_id: str, second_workspace_id: str
    ):
        _create_todo(client, workspace_id, title="WS1 todo")
        _create_todo(client, second_workspace_id, title="WS2 todo")

        resp = client.get(f"/api/todos?workspace_id={workspace_id}")
        assert resp.status_code == 200
        data = resp.json()
        # In singleton model, both IDs resolve to the same workspace,
        # so all todos are visible.
        assert all(t["workspace_id"] == workspace_id for t in data)
        assert any(t["title"] == "WS1 todo" for t in data)
        assert any(t["title"] == "WS2 todo" for t in data)

    def test_list_filter_by_status(self, client: TestClient, workspace_id: str):
        todo1 = _create_todo(client, workspace_id, title="Pending one")
        _create_todo(client, workspace_id, title="Discussed one")
        # Update second to in_discussion
        client.put(f"/api/todos/{_create_todo(client, workspace_id, title='Disc')['id']}",
                    json={"status": "in_discussion"})

        resp = client.get("/api/todos?status=pending")
        assert resp.status_code == 200
        data = resp.json()
        assert all(t["status"] == "pending" for t in data)

    def test_list_pagination_limit(self, client: TestClient, workspace_id: str):
        for i in range(5):
            _create_todo(client, workspace_id, title=f"Todo {i}")

        resp = client.get("/api/todos?limit=3")
        assert resp.status_code == 200
        assert len(resp.json()) == 3

    def test_list_pagination_offset(self, client: TestClient, workspace_id: str):
        for i in range(5):
            _create_todo(client, workspace_id, title=f"Todo {i}")

        all_resp = client.get("/api/todos?limit=100")
        total = len(all_resp.json())

        offset_resp = client.get(f"/api/todos?offset=2&limit=100")
        assert offset_resp.status_code == 200
        assert len(offset_resp.json()) == total - 2

    def test_list_pagination_limit_and_offset(self, client: TestClient, workspace_id: str):
        for i in range(10):
            _create_todo(client, workspace_id, title=f"Page todo {i}")

        resp = client.get("/api/todos?limit=3&offset=2")
        assert resp.status_code == 200
        assert len(resp.json()) <= 3

    def test_list_limit_accepts_flow_board_500(self, client: TestClient, workspace_id: str):
        """The Flow board (ToDoOverlay) fetches limit=500 to derive zones client-side.
        The router cap MUST accept it (aligned to todo_manager.list's min(limit,1000)
        authority) — a le=200 cap here 400'd the Flow tab and was mislabeled a backend
        outage. Regression guard for that bug."""
        resp = client.get("/api/todos?limit=500")
        assert resp.status_code == 200

    def test_list_limit_boundary_1000_and_over(self, client: TestClient, workspace_id: str):
        """le=1000 mirrors the manager ceiling: 1000 accepted, 1001 rejected at the
        FastAPI boundary (validation preserved — the cap is aligned, not removed)."""
        assert client.get("/api/todos?limit=1000").status_code == 200
        assert client.get("/api/todos?limit=1001").status_code == 400

    def test_list_default_limit_unchanged(self, client: TestClient, workspace_id: str):
        """Raising the cap must NOT change the default: no limit param → still capped
        at 50 (default is independent of le)."""
        for i in range(55):
            _create_todo(client, workspace_id, title=f"Default cap {i}")
        resp = client.get("/api/todos")
        assert resp.status_code == 200
        assert len(resp.json()) <= 50


# ---------------------------------------------------------------------------
# Response format (snake_case)
# ---------------------------------------------------------------------------

class TestResponseFormat:
    """Validates: Requirement 6.7 - snake_case field names."""

    def test_response_uses_snake_case(self, client: TestClient, workspace_id: str):
        created = _create_todo(client, workspace_id, source_type="email")
        resp = client.get(f"/api/todos/{created['id']}")
        data = resp.json()
        # Verify snake_case keys
        assert "workspace_id" in data
        assert "source_type" in data
        assert "created_at" in data
        assert "updated_at" in data
        assert "due_date" in data
        assert "task_id" in data
        # Verify no camelCase keys
        assert "workspaceId" not in data
        assert "sourceType" not in data
        assert "createdAt" not in data


# ---------------------------------------------------------------------------
# ToDo Flow-Closure — History, Stats, Review (run_d28de5fd)
# ---------------------------------------------------------------------------

class TestFlowClosureEndpoints:
    """/history, /history/stats, /{id}/review + new response fields (AC1/AC5/AC6)."""

    def test_response_exposes_flow_fields(self, client: TestClient, workspace_id: str):
        """AC1: the 7 new columns reach the API response via _dict_to_response."""
        todo = _create_todo(client, workspace_id, title="flow fields")
        # new fields present (None by default), proving _dict_to_response threads them
        for f in ("review_state", "review_kind", "dispatched_session_id",
                  "dispatched_tab_label", "dispatched_at", "completed_at", "reviewed_at"):
            assert f in todo, f"{f} missing from ToDoResponse"
        assert todo["review_state"] is None

    def test_history_stats_shape(self, client: TestClient, workspace_id: str):
        """AC6: /history/stats returns the 5 aggregations."""
        _create_todo(client, workspace_id, title="h1")
        resp = client.get("/api/todos/history/stats")
        assert resp.status_code == 200
        data = resp.json()
        for k in ("throughput_weekly", "completion_rate", "source_distribution",
                  "confirm_vs_auto", "reject_rate", "totals"):
            assert k in data

    def test_history_returns_todos(self, client: TestClient, workspace_id: str):
        """AC5: /history returns a todos list (not soft-filtered)."""
        _create_todo(client, workspace_id, title="hist item")
        resp = client.get("/api/todos/history")
        assert resp.status_code == 200
        data = resp.json()
        assert "todos" in data and "count" in data
        assert any(t["title"] == "hist item" for t in data["todos"])

    def test_review_confirm_sets_handled(self, client: TestClient, workspace_id: str):
        """AC5: confirm → status=handled + review_state=confirmed (locked invariant)."""
        todo = _create_todo(client, workspace_id, title="to confirm")
        resp = client.post(f"/api/todos/{todo['id']}/review", json={"action": "confirm"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "handled"
        after = client.get(f"/api/todos/{todo['id']}").json()
        assert after["status"] == "handled"
        assert after["review_state"] == "confirmed"
        assert after["review_kind"] == "manual"

    def test_review_reject_creates_new_todo(self, client: TestClient, workspace_id: str):
        """AC5: reject → original closed rejected + a NEW pending todo is created."""
        todo = _create_todo(client, workspace_id, title="to reject")
        resp = client.post(f"/api/todos/{todo['id']}/review", json={"action": "reject"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "cancelled"
        assert body["new_todo_id"] and body["new_todo_id"] != todo["id"]
        orig = client.get(f"/api/todos/{todo['id']}").json()
        assert orig["review_state"] == "rejected"
        new = client.get(f"/api/todos/{body['new_todo_id']}").json()
        assert new["status"] == "pending"
        assert new["title"] == "to reject"

    def test_review_invalid_action(self, client: TestClient, workspace_id: str):
        todo = _create_todo(client, workspace_id, title="bad action")
        resp = client.post(f"/api/todos/{todo['id']}/review", json={"action": "nope"})
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# ToDo Flow-Closure — Dispatch + Retreat (run_5088b841, A2)
# ---------------------------------------------------------------------------

class TestDispatchRetreat:
    """POST /{id}/dispatch writes dispatched_* + keeps status pending; /retreat clears it."""

    def test_dispatch_writes_snapshot_keeps_pending(self, client: TestClient, workspace_id: str):
        """AC3/AC4: dispatch writes tab_label+dispatched_at, status STAYS pending."""
        todo = _create_todo(client, workspace_id, title="to dispatch")
        resp = client.post(f"/api/todos/{todo['id']}/dispatch", json={"tab_label": "Tab 2"})
        assert resp.status_code == 200
        d = resp.json()
        assert d["dispatched_tab_label"] == "Tab 2"
        assert d["dispatched_at"] is not None
        assert d["status"] == "pending"  # locked invariant — NOT in_discussion
        assert d["dispatched_session_id"] is None  # not given → backfilled later

    def test_dispatch_with_session_id(self, client: TestClient, workspace_id: str):
        todo = _create_todo(client, workspace_id, title="dispatch w/ sid")
        resp = client.post(f"/api/todos/{todo['id']}/dispatch", json={"tab_label": "Tab 1", "session_id": "sess-abc"})
        assert resp.status_code == 200
        assert resp.json()["dispatched_session_id"] == "sess-abc"

    def test_retreat_clears_snapshot(self, client: TestClient, workspace_id: str):
        """AC5: retreat clears dispatched_* → back to ① To Do zone."""
        todo = _create_todo(client, workspace_id, title="to retreat")
        client.post(f"/api/todos/{todo['id']}/dispatch", json={"tab_label": "Tab 3", "session_id": "s1"})
        resp = client.post(f"/api/todos/{todo['id']}/retreat")
        assert resp.status_code == 200
        d = resp.json()
        assert d["dispatched_session_id"] is None
        assert d["dispatched_tab_label"] is None
        assert d["dispatched_at"] is None
        assert d["status"] == "pending"

    def test_retreat_resets_in_discussion_to_pending(self, client: TestClient, workspace_id: str):
        """Retreat on an in_discussion todo (bind-session path, NO dispatched_*) must
        reset status→pending, else deriveStatus keeps it 'In Progress' and the UI
        'To Do' control is a silent no-op (adversarial: reworked-status-dropdown)."""
        todo = _create_todo(client, workspace_id, title="in discussion retreat")
        # Reach in_discussion WITHOUT a dispatch snapshot (mirrors bind-session).
        client.put(f"/api/todos/{todo['id']}", json={"status": "in_discussion"})
        resp = client.post(f"/api/todos/{todo['id']}/retreat")
        assert resp.status_code == 200
        d = resp.json()
        assert d["status"] == "pending"
        assert d["dispatched_at"] is None

    def test_retreat_status_reset_is_narrow_to_in_discussion(self, client: TestClient, workspace_id: str):
        """The reset fires ONLY from in_discussion — never from a terminal status, and
        never from plain pending. Guards both directions the narrow `== IN_DISCUSSION`
        check protects: (a) a terminal todo is not resurrected, (b) a pending todo is
        untouched. A broadened guard (e.g. `!= terminal`) would still pass a cancelled
        case, so we ALSO assert the in_discussion→pending move in the same test to lock
        the guard's exact width (adversarial pass-3: the terminal-only variant was
        vacuous — passed with the guard removed)."""
        # (a) terminal stays terminal
        term = _create_todo(client, workspace_id, title="terminal retreat")
        client.put(f"/api/todos/{term['id']}", json={"status": "cancelled"})
        assert client.post(f"/api/todos/{term['id']}/retreat").json()["status"] == "cancelled"
        # (b) in_discussion resets to pending (this arm RED-fails if the guard is removed)
        ind = _create_todo(client, workspace_id, title="ind retreat")
        client.put(f"/api/todos/{ind['id']}", json={"status": "in_discussion"})
        assert client.post(f"/api/todos/{ind['id']}/retreat").json()["status"] == "pending"

    def test_dispatch_404(self, client: TestClient):
        resp = client.post("/api/todos/nonexistent/dispatch", json={"tab_label": "Tab 1"})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# linked_context str/dict contract (run_61f1b635)
# ---------------------------------------------------------------------------

class TestLinkedContextStringContract:
    """A JSON-object linked_context must round-trip as a STRING through every read
    path. Regression: the SQLite adapter _row_to_dict auto-json.loads any '{'/'['
    string, turning linked_context into a dict, which violated ToDoResponse's
    Optional[str] and 400'd the whole list. Fix: SQLiteToDosTable._row_to_dict
    re-serializes it back to a string at the DB-read boundary (covers list, get,
    update, list_history in one place)."""

    _WORK_PACKET = '{"next_step": "do the thing", "files": ["a.py"], "notes": "ctx"}'
    _CJK_PACKET = '{"next_step": "做事情", "notes": "你好"}'

    def test_list_returns_object_linked_context_as_string(self, client: TestClient, workspace_id: str):
        """AC1/AC2: GET /api/todos is 200 and linked_context is a JSON string, not a dict."""
        created = _create_todo(
            client, workspace_id, title="obj ctx list",
            source_type="manual", linked_context=self._WORK_PACKET,
        )
        resp = client.get(f"/api/todos?workspace_id={workspace_id}")
        assert resp.status_code == 200, resp.text
        row = next(t for t in resp.json() if t["id"] == created["id"])
        assert isinstance(row["linked_context"], str), \
            f"expected str, got {type(row['linked_context']).__name__}"
        import json as _json
        assert _json.loads(row["linked_context"])["next_step"] == "do the thing"

    def test_get_returns_object_linked_context_as_string(self, client: TestClient, workspace_id: str):
        """AC2: single GET path also coerces dict -> str."""
        created = _create_todo(
            client, workspace_id, title="obj ctx get",
            source_type="manual", linked_context=self._WORK_PACKET,
        )
        resp = client.get(f"/api/todos/{created['id']}")
        assert resp.status_code == 200, resp.text
        assert isinstance(resp.json()["linked_context"], str)

    def test_history_returns_object_linked_context_as_string(self, client: TestClient, workspace_id: str):
        """AC1: the /history path (bypasses _dict_to_response, uses list_history)
        must ALSO emit linked_context as a string — the Gate-1 hole."""
        _create_todo(
            client, workspace_id, title="obj ctx history",
            source_type="manual", linked_context=self._WORK_PACKET,
        )
        resp = client.get("/api/todos/history")
        assert resp.status_code == 200, resp.text
        row = next(t for t in resp.json()["todos"] if t["title"] == "obj ctx history")
        assert isinstance(row["linked_context"], str)

    def test_cjk_linked_context_round_trips_without_escaping(self, client: TestClient, workspace_id: str):
        """CJK work packets (the real case — Library packet is Chinese) must round-trip
        as raw UTF-8, matching what the writer (JS JSON.stringify / skill json.dumps)
        stored — no \\uXXXX escaping drift on the read path (ensure_ascii=False)."""
        created = _create_todo(
            client, workspace_id, title="cjk ctx",
            source_type="manual", linked_context=self._CJK_PACKET,
        )
        row = client.get(f"/api/todos/{created['id']}").json()
        assert isinstance(row["linked_context"], str)
        assert "你好" in row["linked_context"], "CJK must be raw, not \\uXXXX-escaped"
        import json as _json
        assert _json.loads(row["linked_context"])["notes"] == "你好"


# ---------------------------------------------------------------------------
# linked_context on UPDATE (run_162b8817 — the silent-drop bug fix) + attachments
# ---------------------------------------------------------------------------

class TestUpdatePersistsLinkedContext:
    """PUT /todos/{id} must PERSIST linked_context. Historically update() built its
    updates dict without a linked_context branch, so the schema accepted the field
    and the manager silently dropped it — an edit to the work-packet was a no-op.
    RED-on-revert: remove the branch in todo_manager.update() → this fails."""

    def test_update_persists_linked_context(self, client: TestClient, workspace_id: str):
        created = _create_todo(client, workspace_id, title="Editable")
        import json as _json
        packet = _json.dumps({"next_step": "do it", "files": ["a.ts"], "notes": "keep"})
        resp = client.put(f"/api/todos/{created['id']}", json={"linked_context": packet})
        assert resp.status_code == 200
        # Re-fetch: the packet MUST be there (not dropped).
        row = client.get(f"/api/todos/{created['id']}").json()
        assert row["linked_context"] is not None, "linked_context was silently dropped on update"
        parsed = _json.loads(row["linked_context"])
        assert parsed["next_step"] == "do it"
        assert parsed["files"] == ["a.ts"]
        assert parsed["notes"] == "keep"

    def test_update_linked_context_merge_preserves_other_fields(self, client: TestClient, workspace_id: str):
        """The edit CONTRACT (frontend does the merge; backend stores verbatim): a
        client that read-merges and re-sends the full packet must round-trip intact."""
        import json as _json
        created = _create_todo(
            client, workspace_id, title="Rich",
            linked_context=_json.dumps({"next_step": "old", "commits": ["c1"], "acceptance": "green"}),
        )
        # Simulate the frontend merge: change next_step, preserve commits+acceptance.
        merged = _json.dumps({"next_step": "new", "commits": ["c1"], "acceptance": "green"})
        client.put(f"/api/todos/{created['id']}", json={"linked_context": merged})
        row = client.get(f"/api/todos/{created['id']}").json()
        parsed = _json.loads(row["linked_context"])
        assert parsed["next_step"] == "new"
        assert parsed["commits"] == ["c1"]
        assert parsed["acceptance"] == "green"


class TestTodoAttachmentSecurity:
    """The attachment endpoints must reject path-traversal in todo_id — a crafted
    id must never escape <workspace>/Attachments/todos/."""

    @pytest.mark.parametrize("bad_id", ["..", "../evil", "a/b", "a\\b", "..%2f", "."])
    def test_upload_rejects_traversal_todo_id(self, client: TestClient, bad_id: str):
        # No multipart body needed — the id guard fires before file handling
        # (or the route simply doesn't match a slash-containing id → 404/400/405).
        resp = client.post(f"/api/todos/{bad_id}/attachments", files={"file": ("x.txt", b"hi")})
        assert resp.status_code in (400, 404, 405), (
            f"traversal id {bad_id!r} not rejected (got {resp.status_code})"
        )

    def test_list_attachments_empty_for_new_todo(self, client: TestClient, workspace_id: str):
        created = _create_todo(client, workspace_id, title="No attachments")
        resp = client.get(f"/api/todos/{created['id']}/attachments")
        assert resp.status_code == 200
        assert resp.json()["attachments"] == []
