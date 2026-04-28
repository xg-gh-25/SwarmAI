"""Tests for Hive cloud instance management API."""
import pytest
from httpx import AsyncClient, ASGITransport
from main import app


@pytest.fixture
async def client():
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
