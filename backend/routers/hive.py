"""Hive cloud instance management API.

Manage AWS accounts and Hive (cloud SwarmAI) instances.
Phase 1: CRUD + account verification via boto3 STS.
Phase 2: Full provisioner (EC2, CloudFront, IAM).
"""
import json
import logging
import os
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

import aiosqlite
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from database import db

logger = logging.getLogger(__name__)

router = APIRouter()


def _require_desktop():
    """Block write operations when running as a Hive instance.

    Hive management (deploy/stop/delete instances, manage AWS accounts)
    is only allowed from the desktop app.  A Hive should not be able to
    create/destroy other Hives or manage its own AWS accounts.
    """
    if os.environ.get("SWARMAI_MODE") == "hive":
        raise HTTPException(
            status_code=403,
            detail="Hive management is only available from the desktop app.",
        )


@asynccontextmanager
async def _conn():
    """Async context manager for DB access with Row factory.

    Matches the main WAL connection's pragmas: busy_timeout avoids
    SQLITE_BUSY under concurrent writes, foreign_keys enables CASCADE.
    """
    c = await aiosqlite.connect(str(db.db_path))
    c.row_factory = aiosqlite.Row
    await c.execute("PRAGMA busy_timeout = 100")
    await c.execute("PRAGMA foreign_keys = ON")
    try:
        yield c
    finally:
        await c.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Models ──────────────────────────────────────────────────────────

class HiveAccountCreate(BaseModel):
    account_id: str
    label: str = ""
    auth_method: str = "access_keys"
    auth_config: dict = {}
    default_region: str = "us-east-1"


class HiveAccountResponse(BaseModel):
    id: str
    account_id: str
    label: str
    auth_method: str
    default_region: str
    created_at: str
    verified_at: Optional[str] = None


class HiveInstanceCreate(BaseModel):
    name: str  # validated in create_instance: ^[a-z][a-z0-9-]{0,62}$
    account_ref: str
    region: str = "us-east-1"
    instance_type: str = "m7g.xlarge"


_HIVE_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,62}$")


class HiveInstanceResponse(BaseModel):
    id: str
    name: str
    account_ref: str
    region: str
    instance_type: str
    ec2_instance_id: Optional[str] = None
    ec2_public_ip: Optional[str] = None
    cloudfront_domain: Optional[str] = None
    status: str
    version: Optional[str] = None
    error_message: Optional[str] = None
    created_at: str
    updated_at: str


class VerifyResult(BaseModel):
    success: bool
    account_id: str
    checks: dict = {}
    error: Optional[str] = None


# ── Account Endpoints ──────────────────────────────────────────────

@router.get("/accounts", response_model=list[HiveAccountResponse])
async def list_accounts():
    """List all configured AWS accounts."""
    async with _conn() as c:
        cursor = await c.execute("SELECT * FROM hive_accounts ORDER BY created_at")
        rows = [dict(r) for r in await cursor.fetchall()]
    return [HiveAccountResponse(**r) for r in rows]


@router.post("/accounts", response_model=HiveAccountResponse, status_code=201)
async def create_account(body: HiveAccountCreate):
    """Add a new AWS account."""
    _require_desktop()
    row = {
        "id": str(uuid.uuid4()),
        "account_id": body.account_id,
        "label": body.label or body.account_id,
        "auth_method": body.auth_method,
        "auth_config": json.dumps(body.auth_config),
        "default_region": body.default_region,
        "created_at": _now(),
        "verified_at": None,
    }
    async with _conn() as c:
        await c.execute(
            """INSERT INTO hive_accounts
               (id, account_id, label, auth_method, auth_config, default_region, created_at, verified_at)
               VALUES (:id, :account_id, :label, :auth_method, :auth_config, :default_region, :created_at, :verified_at)""",
            row,
        )
        await c.commit()
    return HiveAccountResponse(**row)


@router.delete("/accounts/{account_id}")
async def delete_account(account_id: str):
    """Remove an AWS account and all its Hive instances."""
    _require_desktop()
    async with _conn() as c:
        await c.execute("DELETE FROM hive_instances WHERE account_ref = ?", (account_id,))
        cursor = await c.execute("DELETE FROM hive_accounts WHERE id = ?", (account_id,))
        await c.commit()
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Account not found")
    return {"deleted": True}


@router.post("/accounts/{account_id}/verify", response_model=VerifyResult)
async def verify_account(account_id: str):
    """Verify AWS permissions for Hive deployment."""
    _require_desktop()
    async with _conn() as c:
        cursor = await c.execute("SELECT * FROM hive_accounts WHERE id = ?", (account_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Account not found")
        row = dict(row)

    auth_config = json.loads(row.get("auth_config", "{}"))
    region = row.get("default_region", "us-east-1")

    try:
        import boto3
        kwargs: dict = {"region_name": region}
        if row["auth_method"] == "access_keys" and auth_config.get("access_key_id"):
            kwargs["aws_access_key_id"] = auth_config["access_key_id"]
            kwargs["aws_secret_access_key"] = auth_config.get("secret_access_key", "")
        elif row["auth_method"] == "sso" and auth_config.get("profile"):
            kwargs["profile_name"] = auth_config["profile"]

        session = boto3.Session(**kwargs)
        checks: dict = {}

        # STS
        sts = session.client("sts")
        identity = sts.get_caller_identity()
        checks["sts"] = {"status": "pass", "account": identity["Account"]}

        # EC2
        try:
            session.client("ec2").describe_instances(MaxResults=5)
            checks["ec2"] = {"status": "pass"}
        except Exception as e:
            checks["ec2"] = {"status": "fail", "error": str(e)[:100]}

        # Bedrock
        try:
            models = session.client("bedrock").list_foundation_models(byProvider="Anthropic")
            n = len([m for m in models.get("modelSummaries", []) if "claude" in m["modelId"]])
            checks["bedrock"] = {"status": "pass" if n > 0 else "fail", "claude_models": n}
        except Exception as e:
            checks["bedrock"] = {"status": "fail", "error": str(e)[:100]}

        # Update verified_at
        async with _conn() as c:
            await c.execute("UPDATE hive_accounts SET verified_at = ? WHERE id = ?", (_now(), account_id))
            await c.commit()

        return VerifyResult(
            success=all(v.get("status") == "pass" for v in checks.values()),
            account_id=identity["Account"],
            checks=checks,
        )
    except Exception as e:
        return VerifyResult(success=False, account_id=row["account_id"], error=str(e))


# ── Instance Endpoints ─────────────────────────────────────────────

@router.get("/instances", response_model=list[HiveInstanceResponse])
async def list_instances():
    """List all Hive instances."""
    async with _conn() as c:
        cursor = await c.execute("SELECT * FROM hive_instances ORDER BY created_at DESC")
        rows = [dict(r) for r in await cursor.fetchall()]
    return [HiveInstanceResponse(**r) for r in rows]


@router.post("/instances", response_model=HiveInstanceResponse, status_code=201)
async def create_instance(body: HiveInstanceCreate):
    """Deploy a new Hive instance (Phase 1: DB record only)."""
    _require_desktop()
    if not _HIVE_NAME_RE.match(body.name):
        raise HTTPException(
            status_code=422,
            detail="Name must start with a letter, contain only lowercase letters/numbers/hyphens, max 63 chars.",
        )
    async with _conn() as c:
        cursor = await c.execute("SELECT id FROM hive_accounts WHERE id = ?", (body.account_ref,))
        if not await cursor.fetchone():
            raise HTTPException(status_code=404, detail="Account not found")

    now = _now()
    row = {
        "id": str(uuid.uuid4()),
        "name": body.name,
        "account_ref": body.account_ref,
        "region": body.region,
        "instance_type": body.instance_type,
        "ec2_instance_id": None, "ec2_public_ip": None,
        "elastic_ip_alloc_id": None, "security_group_id": None,
        "iam_role_name": None, "cloudfront_dist_id": None,
        "cloudfront_domain": None, "ssh_key_name": None,
        "status": "pending",
        "version": None, "error_message": None,
        "created_at": now, "updated_at": now,
    }
    async with _conn() as c:
        await c.execute(
            """INSERT INTO hive_instances
               (id, name, account_ref, region, instance_type,
                ec2_instance_id, ec2_public_ip, elastic_ip_alloc_id,
                security_group_id, iam_role_name, cloudfront_dist_id,
                cloudfront_domain, ssh_key_name, status, version,
                error_message, created_at, updated_at)
               VALUES (:id, :name, :account_ref, :region, :instance_type,
                :ec2_instance_id, :ec2_public_ip, :elastic_ip_alloc_id,
                :security_group_id, :iam_role_name, :cloudfront_dist_id,
                :cloudfront_domain, :ssh_key_name, :status, :version,
                :error_message, :created_at, :updated_at)""",
            row,
        )
        await c.commit()
    return HiveInstanceResponse(**row)


@router.get("/instances/{instance_id}", response_model=HiveInstanceResponse)
async def get_instance(instance_id: str):
    """Get Hive instance details."""
    async with _conn() as c:
        cursor = await c.execute("SELECT * FROM hive_instances WHERE id = ?", (instance_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Instance not found")
    return HiveInstanceResponse(**dict(row))


@router.post("/instances/{instance_id}/stop")
async def stop_instance(instance_id: str):
    """Stop a running Hive (Phase 2: ec2.stop_instances)."""
    _require_desktop()
    async with _conn() as c:
        r = await c.execute(
            "UPDATE hive_instances SET status='stopped', updated_at=? WHERE id=? AND status='running'",
            (_now(), instance_id))
        await c.commit()
        if r.rowcount == 0:
            raise HTTPException(status_code=404, detail="Not found or not running")
    return {"status": "stopped"}


@router.post("/instances/{instance_id}/start")
async def start_instance(instance_id: str):
    """Start a stopped Hive (Phase 2: ec2.start_instances)."""
    _require_desktop()
    async with _conn() as c:
        r = await c.execute(
            "UPDATE hive_instances SET status='running', updated_at=? WHERE id=? AND status='stopped'",
            (_now(), instance_id))
        await c.commit()
        if r.rowcount == 0:
            raise HTTPException(status_code=404, detail="Not found or not stopped")
    return {"status": "running"}


@router.delete("/instances/{instance_id}")
async def delete_instance(instance_id: str):
    """Delete a Hive (Phase 2: cleanup AWS resources first)."""
    _require_desktop()
    async with _conn() as c:
        r = await c.execute("DELETE FROM hive_instances WHERE id = ?", (instance_id,))
        await c.commit()
        if r.rowcount == 0:
            raise HTTPException(status_code=404, detail="Instance not found")
    return {"deleted": True}
