"""ASGI boundary tests for the CDF wake endpoint.

The tests send empty POST requests with explicit client addresses and verify both
independent authorization layers: loopback origin and constant-time secret match.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI

from routers.ingestion_wake import router


@pytest.mark.asyncio
async def test_wake_rejects_bad_secret_and_remote_peer(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))
    from core.ingestion.wake import install_owner_event, uninstall_owner_event

    event = asyncio.Event()
    secret = install_owner_event(event, asyncio.get_running_loop())
    app = FastAPI()
    app.include_router(router)
    try:
        local = httpx.ASGITransport(app=app, client=("127.0.0.1", 1234))
        async with httpx.AsyncClient(transport=local, base_url="http://test") as client:
            assert (await client.post("/api/ingestion/wake", headers={"X-Swarm-Wake": "bad"})).status_code == 403
            assert (await client.post("/api/ingestion/wake", headers={"X-Swarm-Wake": secret})).status_code == 204
        remote = httpx.ASGITransport(app=app, client=("10.0.0.2", 1234))
        async with httpx.AsyncClient(transport=remote, base_url="http://test") as client:
            assert (await client.post("/api/ingestion/wake", headers={"X-Swarm-Wake": secret})).status_code == 403
    finally:
        uninstall_owner_event()
