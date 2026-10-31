"""Hive cloud instance management API.

Manage AWS accounts and Hive (cloud SwarmAI) instances.
Accounts: CRUD + boto3 STS/EC2/Bedrock verification.
Instances: full lifecycle via HiveProvisioner (EC2, CloudFront, IAM, S3).
"""
import asyncio
from core import executors
import json
import logging
import os
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

import aiosqlite
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from database import db
from hive import accounts as hive_accounts
from hive.provisioner import ALLOWED_INSTANCE_TYPES, HiveProvisioner, HIVE_NAME_RE, AMI_ID_RE, VERSION_RE

logger = logging.getLogger(__name__)

router = APIRouter()

# Lazy-init provisioner (needs db_path which is set at import time)
_provisioner: Optional[HiveProvisioner] = None


def _get_provisioner() -> HiveProvisioner:
    global _provisioner
    if _provisioner is None:
        _provisioner = HiveProvisioner(db.db_path)
    return _provisioner


# Strong references to running background tasks: the event loop keeps only weak ones, so
# an unreferenced task (an update holding a Hive in 'updating') could be collected mid-run.
_BACKGROUND: set[asyncio.Task] = set()


def _keep(task: asyncio.Task) -> asyncio.Task:
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)
    task.add_done_callback(_log_task_failure)
    return task


def start_deploy(instance_id: str) -> None:
    """Start provisioning a ``pending`` row in the background (the fleet's creations)."""
    _keep(asyncio.create_task(_get_provisioner().deploy(instance_id),
                              name=f"hive-deploy-{instance_id[:8]}"))


async def delete_row(instance_id: str) -> None:
    """Clean up a Hive's AWS resources and forget it (a finished replacement's old half)."""
    await _get_provisioner().cleanup(instance_id)
    async with _conn() as c:
        await c.execute("DELETE FROM hive_instances WHERE id = ?", (instance_id,))
        await c.commit()


def _log_task_failure(task: asyncio.Task) -> None:
    """Callback for background deploy tasks — log exceptions instead of silently dropping them."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        logger.error("Background task %s failed: %s", task.get_name(), exc, exc_info=exc)


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

    Uses a separate connection (not the shared db singleton) because the
    Hive router runs in background tasks (asyncio.create_task) that may
    outlive the request lifecycle.  The shared connection's WAL mode and
    busy_timeout still apply at the SQLite level.

    Matches the main WAL connection's pragmas: busy_timeout avoids
    SQLITE_BUSY under concurrent writes, foreign_keys enables CASCADE.
    """
    c = await aiosqlite.connect(str(db.db_path))
    c.row_factory = aiosqlite.Row
    await c.execute("PRAGMA busy_timeout = 3000")
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
    # swarmai (the SwarmAI account) | other (another account: stop and delete only)
    # | merged (its Hives moved to the SwarmAI account) | own (no SwarmAI account: as before)
    role: str = "own"
    hive_count: int = 0


ALLOWED_REGIONS = frozenset({
    "us-east-1", "us-east-2", "us-west-1", "us-west-2",
    "eu-west-1", "eu-west-2", "eu-central-1",
    "ap-northeast-1", "ap-southeast-1", "ap-southeast-2", "ap-south-1",
})


class HiveInstanceCreate(BaseModel):
    name: str  # validated in create_instance: ^[a-z][a-z0-9-]{0,62}$
    # with the SwarmAI account connected: omitted or 'swarmai'; otherwise required
    account_ref: Optional[str] = None
    region: str = "us-east-1"
    instance_type: str = "m7g.xlarge"
    owner_name: Optional[str] = None
    hive_type: str = "shared"
    version: Optional[str] = None  # if None, uses latest from GitHub
    custom_ami_id: Optional[str] = None  # pre-baked AMI; None → base AL2023 + full install

    @field_validator("custom_ami_id")
    @classmethod
    def validate_custom_ami_id(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not AMI_ID_RE.match(v):
            raise ValueError(f"Invalid custom_ami_id: {v!r} (must match {AMI_ID_RE.pattern})")
        return v

    @field_validator("region")
    @classmethod
    def validate_region(cls, v: str) -> str:
        if v not in ALLOWED_REGIONS:
            raise ValueError(
                f"Region '{v}' is not allowed. Must be one of: {sorted(ALLOWED_REGIONS)}"
            )
        return v

    @field_validator("instance_type")
    @classmethod
    def validate_instance_type(cls, v: str) -> str:
        if v not in ALLOWED_INSTANCE_TYPES:
            raise ValueError(
                f"Instance type '{v}' not allowed. Must be one of: {sorted(ALLOWED_INSTANCE_TYPES)}"
            )
        return v


class HiveInstanceUpdate(BaseModel):
    version: Optional[str] = None  # None: the latest published release

    @field_validator("version")
    @classmethod
    def validate_version(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not VERSION_RE.fullmatch(v):
            raise ValueError(f"Invalid version string: {v!r}")
        if len(v) > 32:
            raise ValueError("Version string too long (max 32 chars)")
        return v


class HiveRetryRequest(BaseModel):
    # Optional target version: when provided, retry redeploys THIS version
    # instead of the instance's stale recorded one. None -> keep recorded.
    version: Optional[str] = None
    # Required when the Hive's instance still exists: Retry then deletes it and everything
    # on it (after saving its disks as snapshots).
    confirm_data_loss: bool = False

    @field_validator("version")
    @classmethod
    def validate_version(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not VERSION_RE.fullmatch(v):
            raise ValueError(f"Invalid version string: {v!r}")
        if len(v) > 32:
            raise ValueError("Version string too long (max 32 chars)")
        return v


class HiveInstanceResponse(BaseModel):
    """Instance response for list endpoint — no secrets."""
    id: str
    name: str
    owner_name: Optional[str] = None
    hive_type: str = "shared"
    account_ref: str
    region: str
    instance_type: str
    ec2_instance_id: Optional[str] = None
    ec2_public_ip: Optional[str] = None
    elastic_ip_alloc_id: Optional[str] = None
    security_group_id: Optional[str] = None
    iam_role_name: Optional[str] = None
    cloudfront_dist_id: Optional[str] = None
    cloudfront_domain: Optional[str] = None
    s3_bucket: Optional[str] = None
    auth_user: Optional[str] = None
    # auth_password intentionally excluded from list response
    status: str
    version: Optional[str] = None
    custom_ami_id: Optional[str] = None
    error_message: Optional[str] = None
    created_at: str
    updated_at: str


class HiveInstanceDetailResponse(HiveInstanceResponse):
    """Detail response — includes credentials. Only returned by GET /instances/{id}.

    NOTE: auth_password is stored and returned in plaintext. This is acceptable
    because the SQLite DB is local to the user's machine (~/.swarm-ai/data.db).
    The password is generated per-deploy and only used for Caddy basic auth
    (defense-in-depth behind CloudFront). If the DB file is ever shared or
    backed up, credentials will be exposed — document this for users.
    """
    auth_password: Optional[str] = None


class VerifyResult(BaseModel):
    success: bool
    account_id: str
    checks: dict = {}
    error: Optional[str] = None


# ── Account Endpoints ──────────────────────────────────────────────

@router.get("/accounts", response_model=list[HiveAccountResponse])
async def list_accounts():
    """List the AWS accounts Hives are in (design 2026-10-31-swarmai-aws-account §5.5).
    With the SwarmAI account connected its row comes first and new Hives go there."""
    try:
        await hive_accounts.ensure_swarmai_row(db.db_path)
    except Exception as e:  # noqa: BLE001 — the list still shows what is there
        logger.warning("Could not create the SwarmAI account row: %s", e)
    mode = hive_accounts.swarmai_account()
    async with _conn() as c:
        cursor = await c.execute(
            "SELECT a.*, (SELECT COUNT(*) FROM hive_instances i WHERE i.account_ref = a.id) "
            "AS hive_count FROM hive_accounts a "
            "ORDER BY (a.id = ?) DESC, a.created_at", (hive_accounts.SWARMAI_ROW_ID,))
        rows = [dict(r) for r in await cursor.fetchall()]
    return [HiveAccountResponse(**r, role=hive_accounts.row_role(r, mode)) for r in rows]


@router.post("/accounts", response_model=HiveAccountResponse, status_code=201)
async def create_account(body: HiveAccountCreate):
    """Add an AWS account (only while the SwarmAI account is not connected: with it,
    every new Hive is in the SwarmAI account)."""
    _require_desktop()
    if hive_accounts.swarmai_account() is not None:
        raise HTTPException(status_code=409, detail=(
            "Hives use the SwarmAI account. To use another AWS account, change the SwarmAI "
            "account in Settings > SwarmAI account."))
    if body.auth_method not in hive_accounts.CREATABLE_METHODS:   # an allowlist: a new
        raise HTTPException(status_code=422, detail="Invalid auth_method")  # method fails closed
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
    """Remove an AWS account and all its Hive instances.

    Cleans up AWS resources for each instance before deleting DB rows. The SwarmAI
    account's row cannot be deleted (its Hives are deleted one by one).
    """
    _require_desktop()
    if account_id == hive_accounts.SWARMAI_ROW_ID:
        raise HTTPException(status_code=409, detail=(
            "The SwarmAI account cannot be removed here; delete its Hives one by one."))
    async with _conn() as c:
        cursor = await c.execute("SELECT * FROM hive_accounts WHERE id = ?", (account_id,))
        acct = await cursor.fetchone()
    if acct is not None and acct["auth_method"] == hive_accounts.MERGED_METHOD:
        # kept one version so the move to the SwarmAI account can be undone
        raise HTTPException(status_code=409, detail=(
            "This account's Hives were moved to the SwarmAI account; it is kept for now."))
    # U2: Read instances first, then cleanup AWS (long-running) OUTSIDE the connection,
    # then delete DB rows in a quick transaction. Avoids holding DB lock during AWS calls.
    async with _conn() as c:
        cursor = await c.execute("SELECT * FROM hive_instances WHERE account_ref = ?", (account_id,))
        instances = [dict(r) for r in await cursor.fetchall()]

    provisioner = _get_provisioner()
    # An account that cannot be reached: refuse before anything, or the rows go and the
    # instances keep running (and billing) with nothing tracking them.
    if acct is not None and any(i.get("ec2_instance_id") for i in instances):
        try:
            await provisioner._session(dict(acct), instances[0]["region"], "cleanup")
        except hive_accounts.ACCOUNT_ERRORS as e:
            raise HTTPException(status_code=409, detail=f"Nothing was deleted: {e}")
    # Cleanup AWS resources (potentially minutes — EC2 terminate + CF disable)
    for inst in instances:
        if inst.get("ec2_instance_id"):
            try:
                await provisioner.cleanup(inst["id"])
            except Exception as e:
                logger.warning(
                    "Cleanup failed for instance %s (continuing): %s", inst["id"], e
                )

    # Quick transaction: delete DB rows
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

    region = row.get("default_region", "us-east-1")
    if hive_accounts.swarmai_account() is not None and row.get("auth_method") not in (
            hive_accounts.SWARMAI_METHOD, hive_accounts.MERGED_METHOD):
        outcome, actual = await hive_accounts.check_row(db.db_path, row)
        if outcome == "other":
            return VerifyResult(success=False, account_id=actual or row["account_id"], error=(
                f"This is AWS account {actual}, not the SwarmAI account: its Hives can only "
                "be stopped or deleted."))
        if outcome == "merged":
            return VerifyResult(success=True, account_id=row["account_id"],
                                checks={"sts": {"status": "pass", "merged": True}})
        if outcome == "unverified":
            return VerifyResult(success=False, account_id=row["account_id"],
                                error="Could not reach this account; try again later.")

    # M4: Wrap all boto3 calls in to_thread to avoid blocking the event loop
    def _run_checks():
        session = _get_provisioner()._get_session(row, region, "read")
        checks: dict = {}

        # STS
        sts = session.client("sts")
        identity = sts.get_caller_identity()
        checks["sts"] = {"status": "pass", "account": identity["Account"]}

        # EC2
        try:
            ec2 = session.client("ec2")
            ec2.describe_instances(MaxResults=5)
            vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])
            has_vpc = len(vpcs.get("Vpcs", [])) > 0
            checks["ec2"] = {"status": "pass", "default_vpc": has_vpc}
            if not has_vpc:
                checks["ec2"]["warning"] = "No default VPC — Hive deploy may need VPC selection"
        except Exception as e:
            checks["ec2"] = {"status": "fail", "error": str(e)[:100]}

        # Bedrock
        try:
            models = session.client("bedrock").list_foundation_models(byProvider="Anthropic")
            claude_models = [m for m in models.get("modelSummaries", []) if "claude" in m["modelId"]]
            n = len(claude_models)
            checks["bedrock"] = {"status": "pass" if n > 0 else "fail", "claude_models": n}
            if n == 0:
                checks["bedrock"]["error"] = "No Claude models available. Enable model access in Bedrock console."
        except Exception as e:
            checks["bedrock"] = {"status": "fail", "error": str(e)[:100]}

        # IAM
        try:
            iam = session.client("iam")
            iam.list_roles(MaxItems=1)
            checks["iam"] = {"status": "pass"}
        except Exception as e:
            checks["iam"] = {"status": "fail", "error": str(e)[:100]}

        # S3
        try:
            s3 = session.client("s3")
            s3.list_buckets()
            checks["s3"] = {"status": "pass"}
        except Exception as e:
            checks["s3"] = {"status": "fail", "error": str(e)[:100]}

        # CloudFront
        try:
            cf = session.client("cloudfront")
            dists = cf.list_distributions(MaxItems="1")
            count = dists.get("DistributionList", {}).get("Quantity", 0)
            checks["cloudfront"] = {"status": "pass", "existing_distributions": count}
        except Exception as e:
            checks["cloudfront"] = {"status": "fail", "error": str(e)[:100]}

        return identity["Account"], checks

    try:
        import asyncio
        aws_account_id, checks = await executors.run_nav(_run_checks)

        async with _conn() as c:
            await c.execute("UPDATE hive_accounts SET verified_at = ? WHERE id = ?", (_now(), account_id))
            await c.commit()

        return VerifyResult(
            success=all(v.get("status") == "pass" for v in checks.values()),
            account_id=aws_account_id,
            checks=checks,
        )
    except Exception as e:
        return VerifyResult(success=False, account_id=row["account_id"], error=str(e))


# ── Instance Endpoints ─────────────────────────────────────────────

@router.get("/instances", response_model=list[HiveInstanceResponse])
async def list_instances():
    """List all Hive instances (first ending any update or retry SwarmAI was stopped in the middle of)."""
    try:
        await _get_provisioner().release_stale_updates()
    except Exception as e:
        logger.warning("Could not release stale Hive updates: %s", e)
    async with _conn() as c:
        cursor = await c.execute("SELECT * FROM hive_instances ORDER BY created_at DESC")
        rows = [dict(r) for r in await cursor.fetchall()]
    return [HiveInstanceResponse(**r) for r in rows]


@router.post("/instances", response_model=HiveInstanceResponse, status_code=201)
async def create_instance(body: HiveInstanceCreate):
    """Deploy a new Hive instance.

    Creates a DB record and launches a background task that provisions
    all AWS resources (IAM, SG, EC2, EIP, CloudFront). The API returns
    immediately with status='pending'. Poll GET /instances/{id} for progress.
    """
    _require_desktop()
    if not HIVE_NAME_RE.match(body.name):
        raise HTTPException(
            status_code=422,
            detail="Name must start with a letter, contain only lowercase letters/numbers/hyphens, max 63 chars.",
        )
    mode = hive_accounts.swarmai_account()
    if mode is not None:
        if body.account_ref not in (None, hive_accounts.SWARMAI_ROW_ID):
            raise HTTPException(status_code=409, detail="New Hives are created in the SwarmAI account.")
        await hive_accounts.ensure_swarmai_row(db.db_path)
        account_ref = hive_accounts.SWARMAI_ROW_ID
    elif body.account_ref is None:
        raise HTTPException(status_code=422, detail="account_ref is required")
    elif body.account_ref == hive_accounts.SWARMAI_ROW_ID:
        raise HTTPException(status_code=409, detail=(
            "The SwarmAI account is not connected: connect it in Settings > SwarmAI account."))
    else:
        account_ref = body.account_ref

    now = _now()
    row = {
        "id": str(uuid.uuid4()),
        "name": body.name,
        "owner_name": body.owner_name,
        "hive_type": body.hive_type,
        "account_ref": account_ref,
        "region": body.region,
        "instance_type": body.instance_type,
        "ec2_instance_id": None, "ec2_public_ip": None,
        "elastic_ip_alloc_id": None, "security_group_id": None,
        "iam_role_name": None, "iam_instance_profile_arn": None,
        "cloudfront_dist_id": None, "cloudfront_domain": None,
        "s3_bucket": None, "ssh_key_name": None,
        "auth_user": None, "auth_password": None,
        "status": "pending",
        "version": body.version, "error_message": None,
        "custom_ami_id": body.custom_ami_id,
        "seed_data": None, "shared_content": None,
        "created_at": now, "updated_at": now,
    }
    # count and insert under the fleet lock: two requests at 7 Hives cannot both pass
    async with hive_accounts.fleet_lock, _conn() as c:
        cursor = await c.execute("SELECT auth_method FROM hive_accounts WHERE id = ?", (account_ref,))
        acct = await cursor.fetchone()
        if not acct or acct["auth_method"] == hive_accounts.MERGED_METHOD:
            raise HTTPException(status_code=404, detail="Account not found")
        cursor = await c.execute("SELECT id FROM hive_instances WHERE name = ?", (body.name,))
        if await cursor.fetchone():
            raise HTTPException(status_code=409, detail=f"Hive '{body.name}' already exists")
        n = await hive_accounts.counted(c)
        if n + 1 > hive_accounts.MAX_HIVES:
            raise HTTPException(status_code=409, detail=hive_accounts.over_limit_message(n))
        cols = ", ".join(row.keys())
        placeholders = ", ".join(f":{k}" for k in row.keys())
        await c.execute(
            f"INSERT INTO hive_instances ({cols}) VALUES ({placeholders})", row,
        )
        await c.commit()

    # Launch provisioner in background
    provisioner = _get_provisioner()
    task = asyncio.create_task(provisioner.deploy(row["id"]), name=f"hive-deploy-{row['id'][:8]}")
    task.add_done_callback(_log_task_failure)

    return HiveInstanceResponse(**row)


@router.get("/instances/{instance_id}", response_model=HiveInstanceDetailResponse)
async def get_instance(instance_id: str):
    """Get Hive instance details including credentials."""
    async with _conn() as c:
        cursor = await c.execute("SELECT * FROM hive_instances WHERE id = ?", (instance_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Instance not found")
    return HiveInstanceDetailResponse(**dict(row))


@router.post("/instances/{instance_id}/stop")
async def stop_instance(instance_id: str):
    """Stop a running Hive (EC2 stop_instances)."""
    _require_desktop()
    # U3: No redundant status check — provisioner's atomic gate is the single source of truth.
    # Avoids TOCTOU and inconsistent error responses (400 vs 409).
    async with _conn() as c:
        cursor = await c.execute("SELECT id FROM hive_instances WHERE id = ?", (instance_id,))
        if not await cursor.fetchone():
            raise HTTPException(status_code=404, detail="Instance not found")

    provisioner = _get_provisioner()
    try:
        await provisioner.stop(instance_id)
    except RuntimeError as e:
        if "not in" in str(e):
            raise HTTPException(status_code=409, detail=str(e))
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"status": "stopped"}


@router.post("/instances/{instance_id}/start")
async def start_instance(instance_id: str):
    """Start a stopped Hive (EC2 start_instances + wait healthy)."""
    _require_desktop()
    # U3: No redundant status check — provisioner's atomic gate is the single source of truth.
    async with _conn() as c:
        cursor = await c.execute("SELECT id FROM hive_instances WHERE id = ?", (instance_id,))
        if not await cursor.fetchone():
            raise HTTPException(status_code=404, detail="Instance not found")

    provisioner = _get_provisioner()
    try:
        await provisioner.start(instance_id)
    except RuntimeError as e:
        if "not in" in str(e):
            raise HTTPException(status_code=409, detail=str(e))
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"status": "running"}


@router.get("/latest-version")
async def latest_version():
    """The latest published SwarmAI release: what a Hive can be updated to."""
    _require_desktop()
    try:
        return {"version": await _get_provisioner()._resolve_version(None)}
    except Exception as e:
        logger.warning("latest Hive version lookup failed: %s", e)
        raise HTTPException(status_code=502, detail="Cannot determine the latest SwarmAI release")


@router.post("/instances/{instance_id}/update")
async def update_instance(instance_id: str, body: Optional[HiveInstanceUpdate] = None):
    """Update a running Hive to a release (default: the latest published one) via SSM.

    Claims the Hive (running → updating) and returns; the update runs in the background
    and ends 'running', with the new version or with error_message saying why not."""
    _require_desktop()
    async with _conn() as c:
        cursor = await c.execute("SELECT status FROM hive_instances WHERE id = ?", (instance_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Instance not found")
        if dict(row)["status"] != "running":
            raise HTTPException(status_code=400, detail="Instance must be running to update")

    provisioner = _get_provisioner()
    try:
        version = await provisioner._resolve_version(body.version if body else None)
    except Exception as e:
        logger.warning("Hive update: version lookup failed: %s", e)
        raise HTTPException(status_code=502, detail="Cannot determine the latest SwarmAI release")
    if len(version) > 32 or not VERSION_RE.fullmatch(version):
        raise HTTPException(status_code=400 if body and body.version else 502,
                            detail="Unexpected release version")
    try:
        await provisioner.claim_update(instance_id)
    except RuntimeError as e:
        # G6: concurrent update or the Hive left 'running' → 409 Conflict
        raise HTTPException(status_code=409, detail=str(e))
    _keep(asyncio.create_task(provisioner.update(instance_id, version, claimed=True),
                              name=f"hive-update-{instance_id[:8]}"))
    return {"status": "updating", "version": version}


@router.delete("/instances/{instance_id}")
async def delete_instance(instance_id: str):
    """Delete a Hive and clean up all AWS resources. The SwarmAI account's last Hive is
    replaced, never deleted (design §7.3: at least one)."""
    _require_desktop()
    async with _conn() as c:
        cursor = await c.execute("SELECT id FROM hive_instances WHERE id = ?", (instance_id,))
        if not await cursor.fetchone():
            raise HTTPException(status_code=404, detail="Instance not found")
    from hive import fleet
    if hive_accounts.swarmai_account() is not None and await fleet.is_last(db.db_path, instance_id):
        raise HTTPException(status_code=409, detail=(
            "This is your last Hive: replace it instead (a new one is built first, then "
            "this one is deleted), or stop it to stop paying for the instance."))

    provisioner = _get_provisioner()
    try:
        await provisioner.cleanup(instance_id)
    except hive_accounts.ACCOUNT_ERRORS as e:
        # raised before any AWS call: keep the row, or a running instance is forgotten
        raise HTTPException(status_code=409, detail=f"Nothing was deleted: {e}")
    except Exception as e:
        logger.warning("Cleanup had errors (continuing with DB delete): %s", e)

    # Always delete DB record even if AWS cleanup had partial failures
    async with _conn() as c:
        await c.execute("DELETE FROM hive_instances WHERE id = ?", (instance_id,))
        await c.commit()
    return {"deleted": True}


@router.post("/instances/{instance_id}/replace")
async def replace_instance(instance_id: str):
    """Build a new Hive like this one; when it runs, this one is deleted."""
    _require_desktop()
    from hive import fleet
    try:
        return await fleet.replace(db.db_path, instance_id, start_deploy=start_deploy)
    except LookupError:
        raise HTTPException(status_code=404, detail="Instance not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/instances/{instance_id}/reset-password")
async def reset_password(instance_id: str):
    """Reset Hive auth password via SSM. Returns new credentials."""
    _require_desktop()
    async with _conn() as c:
        cursor = await c.execute("SELECT status FROM hive_instances WHERE id = ?", (instance_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Instance not found")
        if dict(row)["status"] != "running":
            raise HTTPException(status_code=400, detail="Instance must be running to reset password")

    provisioner = _get_provisioner()
    try:
        new_password = await provisioner.reset_password(instance_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"auth_user": "admin", "auth_password": new_password}


@router.get("/instances/{instance_id}/credentials")
async def get_instance_credentials(instance_id: str):
    """Return only the auth credentials for a Hive instance."""
    _require_desktop()  # G2: prevent Hive from reading its own credentials
    async with _conn() as c:
        cursor = await c.execute(
            "SELECT auth_user, auth_password FROM hive_instances WHERE id = ?",
            (instance_id,),
        )
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Instance not found")
    row = dict(row)
    return {"auth_user": row["auth_user"], "auth_password": row["auth_password"]}


@router.post("/instances/{instance_id}/retry")
async def retry_instance(instance_id: str, body: Optional[HiveRetryRequest] = None):
    """Retry a failed deploy — cleanup partial resources and redeploy.

    Optional body {"version": "x.y.z"} redeploys that version instead of the
    instance's stale recorded one. Bodyless POST keeps the recorded version.

    When the Hive's EC2 instance still exists (it was running and then a stop, start or
    health check failed), Retry would delete it and everything on it: the request must
    say {"confirm_data_loss": true}, and every disk is saved as an EBS snapshot first.
    If a snapshot fails, nothing is deleted. Start is the non-destructive way back.
    The work runs in the background (status 'deleting', then the deploy's own states).
    """
    _require_desktop()
    async with _conn() as c:
        cursor = await c.execute("SELECT status FROM hive_instances WHERE id = ?", (instance_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Instance not found")
        if dict(row)["status"] != "error":
            raise HTTPException(status_code=400, detail="Only errored instances can be retried")

    provisioner = _get_provisioner()
    exists = await provisioner.instance_exists(instance_id)
    if exists and not (body is not None and body.confirm_data_loss):
        raise HTTPException(status_code=409, detail=(
            "This Hive's instance still exists: Retry deletes it and everything on it. "
            "Try Start first; to rebuild anyway, confirm, and its disks are saved as "
            "snapshots in your AWS account before anything is deleted."))
    async with hive_accounts.fleet_lock, _conn() as c:
        mode = hive_accounts.swarmai_account()
        if mode is not None:
            cursor = await c.execute(
                "SELECT a.* FROM hive_accounts a JOIN hive_instances i ON i.account_ref = a.id "
                "WHERE i.id = ?", (instance_id,))
            acct = await cursor.fetchone()
            if acct is None or hive_accounts.row_role(dict(acct), mode) != "swarmai":
                raise HTTPException(status_code=409, detail=(
                    "This Hive is in another AWS account: it can be deleted, not rebuilt there."))
        n = await hive_accounts.counted(c, excluding=instance_id)
        if n + 1 > hive_accounts.MAX_HIVES:
            raise HTTPException(status_code=409, detail=hive_accounts.over_limit_message(n))
        cursor = await c.execute(
            "UPDATE hive_instances SET status='deleting', error_message=?, updated_at=? "
            "WHERE id=? AND status='error'",
            ("Saving the Hive's disks as snapshots before deleting it..." if exists
             else "Cleaning up the failed deploy...", _now(), instance_id))
        await c.commit()
        if cursor.rowcount == 0:
            raise HTTPException(status_code=409, detail="The Hive changed state; refresh and try again")
    version = body.version if body is not None else None
    _keep(asyncio.create_task(_retry(provisioner, instance_id, version, exists),
                              name=f"hive-retry-{instance_id[:8]}"))
    return {"status": "retrying"}


async def _set_retry_state(instance_id: str, status: str, message: str) -> None:
    async with _conn() as c:
        await c.execute(
            "UPDATE hive_instances SET status=?, error_message=?, updated_at=? WHERE id=?",
            (status, message, _now(), instance_id))
        await c.commit()


async def _retry(provisioner: HiveProvisioner, instance_id: str, version: Optional[str],
                 back_up: bool) -> None:
    """The background half of a Retry. Each step records itself in error_message, so a
    run SwarmAI stops in the middle of ends (release_stale_updates) saying how far it got."""
    try:
        await _retry_steps(provisioner, instance_id, version, back_up)
    except Exception as e:
        logger.exception("Hive retry failed for %s", instance_id)
        await _set_retry_state(instance_id, "error",
                               f"Retry failed: {type(e).__name__}: {str(e)[:300]}")


async def _retry_steps(provisioner: HiveProvisioner, instance_id: str, version: Optional[str],
                       back_up: bool) -> None:
    snapshots: list[str] = []
    if back_up:
        try:
            snapshots = await provisioner.snapshot_volumes(instance_id, "before retry")
        except Exception as e:
            logger.error("Hive retry stopped, disk snapshot failed for %s: %s", instance_id, e)
            await _set_retry_state(
                instance_id, "error",
                f"Retry stopped: saving the Hive's disks failed ({type(e).__name__}: "
                f"{str(e)[:200]}); nothing was deleted.")
            return
        await _set_retry_state(
            instance_id, "deleting",
            f"Disks saved as snapshots {', '.join(snapshots)}; deleting the old instance...")

    # Cleanup any partial resources first
    try:
        await provisioner.cleanup(instance_id)
    except Exception as e:
        logger.warning("Retry cleanup had errors (continuing): %s", e)
    # cleanup() only logs a failed terminate: check before forgetting the instance, or it
    # keeps running (and billing) untracked
    if back_up and await provisioner.instance_exists(instance_id):
        await _set_retry_state(
            instance_id, "error",
            "Retry stopped: the old instance could not be deleted (it is still running in "
            f"your AWS account). Its disks were saved as snapshots {', '.join(snapshots)}.")
        return

    # Reset instance state
    async with _conn() as c:
        await c.execute(
            """UPDATE hive_instances SET
                status='pending', error_message=NULL,
                ec2_instance_id=NULL, ec2_public_ip=NULL, elastic_ip_alloc_id=NULL,
                security_group_id=NULL, iam_role_name=NULL, iam_instance_profile_arn=NULL,
                cloudfront_dist_id=NULL, cloudfront_domain=NULL, s3_bucket=NULL,
                auth_user=NULL, auth_password=NULL, updated_at=?
                WHERE id=?""",
            (_now(), instance_id),
        )
        # Optional version override: write the target version so deploy's
        # _resolve_version(instance['version']) picks it up (deploy re-resolves
        # bucket + everything else, so the version column is the only write).
        if version:
            await c.execute(
                "UPDATE hive_instances SET version=? WHERE id=?",
                (version, instance_id),
            )
        await c.commit()

    # Redeploy
    await provisioner.deploy(instance_id)


# ── Brains on a Hive (design 2026-10-28-project-agent-flow §6): the console's column ──

def _brain_sync_error(exc: Exception) -> HTTPException:
    """A Hive that is not running / has no credentials, or a sync the host refuses."""
    return HTTPException(status_code=409, detail=str(exc))


@router.get("/instances/{instance_id}/brains")
async def instance_brains(instance_id: str) -> dict:
    """Each local Brain against this Hive: versions, changed files, running sessions,
    whether it can be dispatched there (and why not)."""
    from core import brain_sync_host
    from core.swarm_out import SwarmOutError
    try:
        return await brain_sync_host.status(instance_id)
    except (SwarmOutError, brain_sync_host.SyncRefused) as exc:
        raise _brain_sync_error(exc) from None
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"the Hive did not answer ({type(exc).__name__})") from None


@router.post("/instances/{instance_id}/brains/sync-defaults")
async def instance_sync_default_brains(instance_id: str) -> dict:
    """Sync the SwarmAI and General Brains to this Hive when missing or out of date."""
    from core import brain_sync_host
    from core.swarm_out import SwarmOutError
    try:
        return await brain_sync_host.sync_defaults(instance_id)
    except (SwarmOutError, brain_sync_host.SyncRefused) as exc:
        raise _brain_sync_error(exc) from None
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"the Hive did not answer ({type(exc).__name__})") from None


@router.post("/instances/{instance_id}/brains/{brain}/sync")
async def instance_sync_brain(instance_id: str, brain: str) -> dict:
    """Push one Brain to this Hive now."""
    from core import brain_sync_host
    from core.swarm_out import SwarmOutError, _instance
    try:
        inst = await _instance(instance_id)
        return await brain_sync_host.sync(inst, brain)
    except (SwarmOutError, brain_sync_host.SyncRefused) as exc:
        raise _brain_sync_error(exc) from None
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"the Hive did not answer ({type(exc).__name__})") from None
