"""Tests for Hive cloud instance management API."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient, ASGITransport


@pytest.fixture(autouse=True)
async def _isolate_background_provisioning(monkeypatch):
    """Keep API tests from launching real deploys past their test event loop.

    The create endpoint intentionally schedules provisioning in the background.
    These tests cover request/DB behavior, not AWS provisioning, so a real
    ``HiveProvisioner.deploy`` can outlive the test and retain a SQLite write
    lock while the suite-wide reset fixture starts the next test.
    """
    from routers import hive as hive_router

    provisioner = MagicMock()
    provisioner.deploy = AsyncMock(return_value=None)
    provisioner.cleanup = AsyncMock(return_value=None)
    monkeypatch.setattr(hive_router, "_provisioner", provisioner)

    yield

    tasks = [
        task
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task()
        and task.get_name().startswith(("hive-deploy-", "hive-retry-"))
    ]
    if tasks:
        await asyncio.gather(*tasks)


@pytest.fixture
async def client():
    # Lazy import: a collection-time `import main` attaches main's file log
    # handler to the LIVE ~/.swarm-ai before conftest's per-test sandbox exists.
    from main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_list_accounts_empty(client):
    resp = await client.get("/api/hive/accounts")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_create_account(client):
    resp = await client.post("/api/hive/accounts", json={
        "account_id": "123456789012",
        "label": "test",
        "auth_method": "access_keys",
        "auth_config": {"access_key_id": "AKIA_TEST", "secret_access_key": "secret"},
        "default_region": "us-east-1",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["account_id"] == "123456789012"
    assert data["label"] == "test"
    assert data["auth_method"] == "access_keys"
    assert "auth_config" not in data  # credentials not exposed in response


@pytest.mark.asyncio
async def test_list_accounts_after_create(client):
    # Create
    await client.post("/api/hive/accounts", json={
        "account_id": "111222333444",
        "label": "acct1",
    })
    # List
    resp = await client.get("/api/hive/accounts")
    assert resp.status_code == 200
    accounts = resp.json()
    assert any(a["account_id"] == "111222333444" for a in accounts)


@pytest.mark.asyncio
async def test_delete_account(client):
    # Create
    resp = await client.post("/api/hive/accounts", json={"account_id": "999888777666"})
    account_id = resp.json()["id"]
    # Delete
    resp = await client.delete(f"/api/hive/accounts/{account_id}")
    assert resp.status_code == 200
    # Verify gone
    resp = await client.get("/api/hive/accounts")
    assert all(a["id"] != account_id for a in resp.json())


@pytest.mark.asyncio
async def test_delete_account_not_found(client):
    resp = await client.delete("/api/hive/accounts/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_instances_empty(client):
    resp = await client.get("/api/hive/instances")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_create_instance(client):
    # Need an account first
    acc_resp = await client.post("/api/hive/accounts", json={"account_id": "555666777888"})
    acc_id = acc_resp.json()["id"]

    resp = await client.post("/api/hive/instances", json={
        "name": "test-hive",
        "account_ref": acc_id,
        "region": "us-east-1",
        "instance_type": "m7g.xlarge",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "test-hive"
    assert data["status"] == "pending"
    assert data["instance_type"] == "m7g.xlarge"


@pytest.mark.asyncio
async def test_create_instance_with_custom_ami(client):
    """AC4: custom_ami_id round-trips through create + is returned in response."""
    acc_resp = await client.post("/api/hive/accounts", json={"account_id": "555666777811"})
    acc_id = acc_resp.json()["id"]

    resp = await client.post("/api/hive/instances", json={
        "name": "prebaked-hive",
        "account_ref": acc_id,
        "custom_ami_id": "ami-0868a14e13f356561",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["custom_ami_id"] == "ami-0868a14e13f356561"


@pytest.mark.asyncio
async def test_create_instance_rejects_bad_custom_ami(client):
    """AC4: malformed custom_ami_id is rejected by the field validator."""
    acc_resp = await client.post("/api/hive/accounts", json={"account_id": "555666777822"})
    acc_id = acc_resp.json()["id"]

    resp = await client.post("/api/hive/instances", json={
        "name": "bad-ami-hive",
        "account_ref": acc_id,
        "custom_ami_id": "not-an-ami",
    })
    # middleware error_handler maps pydantic ValidationError → 400
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_create_instance_bad_account(client):
    resp = await client.post("/api/hive/instances", json={
        "name": "orphan-hive",
        "account_ref": "nonexistent-id",
    })
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_instance(client):
    acc_resp = await client.post("/api/hive/accounts", json={"account_id": "444333222111"})
    acc_id = acc_resp.json()["id"]
    inst_resp = await client.post("/api/hive/instances", json={"name": "del-hive", "account_ref": acc_id})
    inst_id = inst_resp.json()["id"]

    resp = await client.delete(f"/api/hive/instances/{inst_id}")
    assert resp.status_code == 200

    resp = await client.get("/api/hive/instances")
    assert all(i["id"] != inst_id for i in resp.json())


@pytest.mark.asyncio
async def test_cascade_delete_account_removes_instances(client):
    acc_resp = await client.post("/api/hive/accounts", json={"account_id": "777888999000"})
    acc_id = acc_resp.json()["id"]
    await client.post("/api/hive/instances", json={"name": "cascade-hive", "account_ref": acc_id})

    # Delete account should cascade to instances
    await client.delete(f"/api/hive/accounts/{acc_id}")

    resp = await client.get("/api/hive/instances")
    assert all(i["name"] != "cascade-hive" for i in resp.json())
