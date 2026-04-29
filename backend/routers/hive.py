"""Hive cloud instance management API.

Manage AWS accounts and Hive (cloud SwarmAI) instances.
Accounts: CRUD + boto3 STS/EC2/Bedrock verification.
Instances: full lifecycle via HiveProvisioner (EC2, CloudFront, IAM, S3).
"""
import asyncio
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
from hive.provisioner import HiveProvisioner

logger = logging.getLogger(__name__)

router = APIRouter()

# Lazy-init provisioner (needs db_path which is set at import time)
_provisioner: Optional[HiveProvisioner] = None


def _get_provisioner() -> HiveProvisioner:
    global _provisioner
    if _provisioner is None:
        _provisioner = HiveProvisioner(db.db_path)
    return _provisioner


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
    await c.execute("PRAGMA busy_timeout = 500")
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


ALLOWED_REGIONS = frozenset({
    "us-east-1", "us-east-2", "us-west-1", "us-west-2",
    "eu-west-1", "eu-west-2", "eu-central-1",
    "ap-northeast-1", "ap-southeast-1", "ap-southeast-2", "ap-south-1",
})


class HiveInstanceCreate(BaseModel):
    name: str  # validated in create_instance: ^[a-z][a-z0-9-]{0,62}$
    account_ref: str
    region: str = "us-east-1"
    instance_type: str = "m7g.xlarge"
    owner_name: Optional[str] = None
    hive_type: str = "shared"
    version: Optional[str] = None  # if None, uses latest from GitHub

    @field_validator("region")
    @classmethod
    def validate_region(cls, v: str) -> str:
        if v not in ALLOWED_REGIONS:
            raise ValueError(
                f"Region '{v}' is not allowed. Must be one of: {sorted(ALLOWED_REGIONS)}"
            )
        return v


class HiveInstanceUpdate(BaseModel):
    version: str


_HIVE_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,62}$")


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
    """Remove an AWS account and all its Hive instances.

    Cleans up AWS resources for each instance before deleting DB rows.
    """
    _require_desktop()
    async with _conn() as c:
        cursor = await c.execute("SELECT * FROM hive_instances WHERE account_ref = ?", (account_id,))
        instances = [dict(r) for r in await cursor.fetchall()]

    # Cleanup AWS resources for each instance that has an EC2 instance
    provisioner = _get_provisioner()
    for inst in instances:
        if inst.get("ec2_instance_id"):
            try:
                await provisioner.cleanup(inst["id"])
            except Exception as e:
                logger.warning(
                    "Cleanup failed for instance %s (continuing): %s", inst["id"], e
                )

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

        # EC2 (launch permission)
        try:
            ec2 = session.client("ec2")
            ec2.describe_instances(MaxResults=5)
            # Also check we can describe VPCs (needed for SG creation)
            vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])
            has_vpc = len(vpcs.get("Vpcs", [])) > 0
            checks["ec2"] = {"status": "pass", "default_vpc": has_vpc}
            if not has_vpc:
                checks["ec2"]["warning"] = "No default VPC — Hive deploy may need VPC selection"
        except Exception as e:
            checks["ec2"] = {"status": "fail", "error": str(e)[:100]}

        # Bedrock (Claude model access)
        try:
            models = session.client("bedrock").list_foundation_models(byProvider="Anthropic")
            claude_models = [m for m in models.get("modelSummaries", []) if "claude" in m["modelId"]]
            n = len(claude_models)
            checks["bedrock"] = {"status": "pass" if n > 0 else "fail", "claude_models": n}
            if n == 0:
                checks["bedrock"]["error"] = "No Claude models available. Enable model access in Bedrock console."
        except Exception as e:
            checks["bedrock"] = {"status": "fail", "error": str(e)[:100]}

        # IAM (can create roles — needed for Hive instance profiles)
        try:
            iam = session.client("iam")
            iam.list_roles(MaxItems=1)
            checks["iam"] = {"status": "pass"}
        except Exception as e:
            checks["iam"] = {"status": "fail", "error": str(e)[:100]}

        # S3 (can create buckets — needed for hive release storage)
        try:
            s3 = session.client("s3")
            s3.list_buckets()
            checks["s3"] = {"status": "pass"}
        except Exception as e:
            checks["s3"] = {"status": "fail", "error": str(e)[:100]}

        # CloudFront (can create distributions)
        try:
            cf = session.client("cloudfront")
            dists = cf.list_distributions(MaxItems="1")
            count = dists.get("DistributionList", {}).get("Quantity", 0)
            checks["cloudfront"] = {"status": "pass", "existing_distributions": count}
        except Exception as e:
            checks["cloudfront"] = {"status": "fail", "error": str(e)[:100]}

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
    """Deploy a new Hive instance.

    Creates a DB record and launches a background task that provisions
    all AWS resources (IAM, SG, EC2, EIP, CloudFront). The API returns
    immediately with status='pending'. Poll GET /instances/{id} for progress.
    """
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
        # Check name uniqueness
        cursor = await c.execute("SELECT id FROM hive_instances WHERE name = ?", (body.name,))
        if await cursor.fetchone():
            raise HTTPException(status_code=409, detail=f"Hive '{body.name}' already exists")

    now = _now()
    row = {
        "id": str(uuid.uuid4()),
        "name": body.name,
        "owner_name": body.owner_name,
        "hive_type": body.hive_type,
        "account_ref": body.account_ref,
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
        "seed_data": None, "shared_content": None,
        "created_at": now, "updated_at": now,
    }
    async with _conn() as c:
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
    async with _conn() as c:
        cursor = await c.execute("SELECT status FROM hive_instances WHERE id = ?", (instance_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Instance not found")
        if dict(row)["status"] != "running":
            raise HTTPException(status_code=400, detail="Instance is not running")

    provisioner = _get_provisioner()
    try:
        await provisioner.stop(instance_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"status": "stopped"}


@router.post("/instances/{instance_id}/start")
async def start_instance(instance_id: str):
    """Start a stopped Hive (EC2 start_instances + wait healthy)."""
    _require_desktop()
    async with _conn() as c:
        cursor = await c.execute("SELECT status FROM hive_instances WHERE id = ?", (instance_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Instance not found")
        if dict(row)["status"] != "stopped":
            raise HTTPException(status_code=400, detail="Instance is not stopped")

    provisioner = _get_provisioner()
    try:
        await provisioner.start(instance_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"status": "running"}


@router.post("/instances/{instance_id}/update")
async def update_instance(instance_id: str, body: HiveInstanceUpdate):
    """Update a Hive to a new version via SSM Run Command."""
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
        await provisioner.update(instance_id, body.version)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"status": "updated", "version": body.version}


@router.delete("/instances/{instance_id}")
async def delete_instance(instance_id: str):
    """Delete a Hive and clean up all AWS resources."""
    _require_desktop()
    async with _conn() as c:
        cursor = await c.execute("SELECT id FROM hive_instances WHERE id = ?", (instance_id,))
        if not await cursor.fetchone():
            raise HTTPException(status_code=404, detail="Instance not found")

    provisioner = _get_provisioner()
    try:
        await provisioner.cleanup(instance_id)
    except Exception as e:
        logger.warning("Cleanup had errors (continuing with DB delete): %s", e)

    # Always delete DB record even if AWS cleanup had partial failures
    async with _conn() as c:
        await c.execute("DELETE FROM hive_instances WHERE id = ?", (instance_id,))
        await c.commit()
    return {"deleted": True}


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


@router.get("/instances/{instance_id}/health")
async def health_proxy(instance_id: str):
    """Proxy health check to remote Hive instance.

    Prefers CloudFront domain (HTTPS) because the SG only allows port 80
    from CloudFront IPs — direct IP access is blocked by design.
    Falls back to direct IP only if no CloudFront domain is available yet.
    """
    async with _conn() as c:
        cursor = await c.execute(
            "SELECT ec2_public_ip, cloudfront_domain, auth_user, auth_password FROM hive_instances WHERE id = ?",
            (instance_id,),
        )
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Instance not found")
        row = dict(row)

    # Build basic auth from instance credentials
    auth = None
    if row.get("auth_user") and row.get("auth_password"):
        auth = httpx.BasicAuth(row["auth_user"], row["auth_password"])

    # Prefer CloudFront (SG blocks direct IP from non-CF sources)
    cf_domain = row.get("cloudfront_domain")
    if cf_domain:
        # PE-M2: SSRF guard — only allow *.cloudfront.net domains
        if not cf_domain.endswith(".cloudfront.net"):
            raise HTTPException(status_code=400, detail="Invalid CloudFront domain")
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"https://{cf_domain}/health", auth=auth)
                return resp.json()
        except Exception as e:
            return {"status": "unreachable", "error": str(e)}

    # Fallback to direct IP (only during initial deploy before CF is ready)
    ip = row.get("ec2_public_ip")
    if not ip:
        raise HTTPException(status_code=400, detail="No IP or domain for this instance")

    # SSRF guard: only allow public IPv4 addresses
    import ipaddress
    try:
        addr = ipaddress.ip_address(ip)
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
            raise HTTPException(status_code=400, detail="Instance IP is not a public address")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid IP address for this instance")

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"http://{ip}/health", auth=auth)
            return resp.json()
    except Exception as e:
        return {"status": "unreachable", "error": str(e)}


@router.post("/instances/{instance_id}/retry")
async def retry_instance(instance_id: str):
    """Retry a failed deploy — cleanup partial resources and redeploy."""
    _require_desktop()
    async with _conn() as c:
        cursor = await c.execute("SELECT status FROM hive_instances WHERE id = ?", (instance_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Instance not found")
        if dict(row)["status"] != "error":
            raise HTTPException(status_code=400, detail="Only errored instances can be retried")

    provisioner = _get_provisioner()
    # Cleanup any partial resources first
    try:
        await provisioner.cleanup(instance_id)
    except Exception as e:
        logger.warning("Retry cleanup had errors (continuing): %s", e)

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
        await c.commit()

    # Redeploy
    task = asyncio.create_task(provisioner.deploy(instance_id), name=f"hive-retry-{instance_id[:8]}")
    task.add_done_callback(_log_task_failure)
    return {"status": "retrying"}
