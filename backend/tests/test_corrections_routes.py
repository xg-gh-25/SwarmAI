"""Correction ledger routes and the chat send path's entry (design 2026-10-30-autonomy-tenets-gaps §2)."""
import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.ingestion import correction_ledger as cl
from core.ingestion import store
from routers import corrections


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "ingestion_db_path", lambda: tmp_path / "ingestion.db")
    ws = tmp_path / "ws"
    (ws / ".context").mkdir(parents=True)
    (ws / ".context" / "MEMORY.md").write_text(
        "# MEMORY\n\n## Corrections\n\n- [correction] **Timezone is ICT** report times use ICT, never UTC.\n",
        encoding="utf-8")
    monkeypatch.setattr("core.project_registry.get_swarmws", lambda: ws)
    app = FastAPI()
    app.include_router(corrections.router)
    return TestClient(app, client=("127.0.0.1", 5000))


def test_pending_then_confirm_by_the_person(client):
    rid = cl.open_row(surface="chat", text="不对 report times are in UTC, use ICT", session_id="s1",
                      client_id="c1")
    pend = client.get("/api/ingestion/corrections/pending", params={"session_id": "s1"}).json()
    assert [r["id"] for r in pend["corrections"]] == [rid]
    key = pend["corrections"][0]["candidates"][0]["key"]
    # without the user-action mark the class is not recorded
    assert client.post(f"/api/ingestion/corrections/{rid}/confirm", json={"choice": key}).status_code == 403
    bad = client.post(f"/api/ingestion/corrections/{rid}/confirm", json={"choice": "made-up"},
                      headers={"X-Swarm-User-Action": "correction"})
    assert bad.status_code == 400
    r = client.post(f"/api/ingestion/corrections/{rid}/confirm", json={"choice": key},
                    headers={"X-Swarm-User-Action": "correction"})
    assert r.status_code == 200 and r.json()["repeat"] is True
    again = client.post(f"/api/ingestion/corrections/{rid}/confirm", json={"choice": "new"},
                        headers={"X-Swarm-User-Action": "correction"})
    assert again.status_code == 409
    rate = client.get("/api/ingestion/corrections/rate").json()
    assert rate["confirmed"] == 1 and rate["rate"] == 1.0
    # neither named: every row still waiting (the CDF panel)
    cl.open_row(surface="escalation", text="chose B, not the recommended A", client_id="esc-1")
    every = client.get("/api/ingestion/corrections/pending").json()["corrections"]
    assert [r["client_id"] for r in every] == ["esc-1"]


def test_a_non_loopback_request_cannot_confirm(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "ingestion_db_path", lambda: tmp_path / "ingestion.db")
    app = FastAPI()
    app.include_router(corrections.router)
    remote = TestClient(app, client=("10.0.0.5", 5000))
    r = remote.post("/api/ingestion/corrections/cor-x/confirm", json={"choice": "new"},
                    headers={"X-Swarm-User-Action": "correction"})
    assert r.status_code == 403


def test_the_send_path_records_only_a_stated_mistake_in_an_existing_session(monkeypatch):
    from routers import chat
    from schemas.message import ChatRequest
    seen = []
    monkeypatch.setattr(cl, "record_chat_message",
                        lambda sid, cid, text, reply="": seen.append((sid, cid, text, reply)))

    class _Msgs:
        async def list_by_session_paginated(self, session_id, limit=None, **kw):
            return [{"role": "assistant", "content": [{"type": "text", "text": "refund by order date"}]}]

    class _Db:
        messages = _Msgs()

    monkeypatch.setattr(chat, "db", _Db())

    async def go():
        chat._maybe_record_correction(ChatRequest(agent_id="a", message="looks fine", session_id="s1", client_id="c0"))
        chat._maybe_record_correction(ChatRequest(agent_id="a", message="不对", session_id=None, client_id="c1"))
        chat._maybe_record_correction(ChatRequest(agent_id="a", message="不对，按到账日", session_id="s1", client_id="c2"))
        await asyncio.gather(*list(chat._CORRECTION_TASKS))

    asyncio.run(go())
    assert seen == [("s1", "c2", "不对，按到账日", "refund by order date")]



def test_the_reject_and_resolve_routes_open_a_row(tmp_path, monkeypatch):
    """The two explicit actions reach the ledger through their real routes."""
    from routers import escalations, swarm_out
    seen = []
    monkeypatch.setattr(cl, "record_swarm_out_reject", lambda d: seen.append(("reject", d)))
    monkeypatch.setattr(cl, "record_escalation", lambda e: seen.append(("escalation", e.id)))
    monkeypatch.setattr(swarm_out, "_require_desktop", lambda: None)
    monkeypatch.setattr("core.hive_return.reject", lambda d, k, t: {"rejected": True, "ref": "r"})
    from core.escalation import block, save_escalation
    ws = tmp_path / "ws"
    (ws / "Projects" / "P").mkdir(parents=True)
    monkeypatch.setattr(escalations, "_get_workspace_root", lambda: ws)
    monkeypatch.setattr(escalations, "_validate_project", lambda p: p)
    esc = block("Q", "S", [], project="P")
    save_escalation(ws, esc)
    app = FastAPI()
    app.include_router(swarm_out.router, prefix="/api/swarm-out")
    app.include_router(escalations.router)
    c = TestClient(app, client=("127.0.0.1", 5000))
    assert c.post("/api/swarm-out/runs/so_1/return/reject", json={}).status_code == 200
    assert c.post(f"/api/escalations/P/{esc.id}/resolve", json={"resolution": "B"}).status_code == 200
    assert seen == [("reject", "so_1"), ("escalation", esc.id)]
