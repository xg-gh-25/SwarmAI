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
from pydantic import BaseModel, ConfigDict, StrictInt, field_validator

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


def _is_elastic(row: dict) -> bool:
    """Elastic Workers' row (``carrier='agentcore'``): its own routes (/elastic) act on it,
    never the EC2 ones."""
    from core.carriers.base import AGENTCORE
    return row.get("carrier") == AGENTCORE


ELASTIC_NOT_EC2 = "This is Elastic Workers: turn it off in Swarm Out › settings."
NOT_IDLE_STOPPED = "The Host never stops this machine, so it has no idle stop to set."

#: the per-Hive idle stop's minutes besides 0 (always on) and null (the default): the range
#: of the Swarm Out › settings selector (desktop SwarmOutSettings IDLE_CHOICES)
IDLE_MINUTES_MIN = 30
IDLE_MINUTES_MAX = 480


def _idle_stop_kinds() -> frozenset:
    """The carriers whose locations the Host stops when idle (``base.IDLE_STOP``)."""
    from core.carriers import registry
    from core.carriers.base import IDLE_STOP
    return frozenset(c.kind for c in registry.with_duty(IDLE_STOP))


def _idle_stop_applies(row: dict, kinds: frozenset) -> bool:
    """Whether this row has an idle stop of its own to set: an EC2 Hive (core/hive_idle
    stops only those) while the EC2 carrier has the idle_stop duty. Elastic Workers and any
    carrier without the duty (an SSH machine) are never stopped by the Host."""
    from core.carriers.base import EC2
    carrier = row.get("carrier") or EC2
    return carrier == EC2 and carrier in kinds


def _with_idle_stop(rows: list[dict]) -> list[dict]:
    """Each row with its idle stop for the panel: ``idle_stop_minutes`` (its own, None =
    the default), ``idle_stop_effective`` (the minutes after which the Host stops it, 0 =
    never) and ``idle_stop_settable`` (whether it has one to set, ``_idle_stop_applies``)."""
    from core import hive_idle
    default_s = hive_idle.idle_after_s()
    kinds = _idle_stop_kinds()
    out = []
    for r in rows:
        settable = _idle_stop_applies(r, kinds)
        own = hive_idle.own_minutes(r.get("idle_stop_minutes")) if settable else None
        idle_s = hive_idle.own_idle_after_s(own, default_s) if settable else None
        out.append({**r, "idle_stop_minutes": own, "idle_stop_settable": settable,
                    "idle_stop_effective": 0.0 if idle_s is None else idle_s / 60.0})
    return out


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


class HiveIdleStop(BaseModel):
    """One EC2 Hive's own idle stop: null follows the default (app config
    ``hive_idle_stop_minutes``), 0 is always on, N stops it after N idle minutes."""
    model_config = ConfigDict(extra="forbid")
    minutes: Optional[StrictInt]          # required; null is a value

    @field_validator("minutes")
    @classmethod
    def validate_minutes(cls, v: Optional[int]) -> Optional[int]:
        if v is None or v == 0 or IDLE_MINUTES_MIN <= v <= IDLE_MINUTES_MAX:
            return v
        raise ValueError(f"minutes must be null, 0 (always on) or {IDLE_MINUTES_MIN} to "
                         f"{IDLE_MINUTES_MAX}")


class HiveIdleStopResponse(BaseModel):
    id: str
    idle_stop_minutes: Optional[int] = None
    idle_stop_effective: float
    idle_stop_settable: bool


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
    # its idle stop (_with_idle_stop): its own (None = the default), the minutes after which
    # the Host stops it (0 = never), whether it has one to set
    idle_stop_minutes: Optional[int] = None
    idle_stop_effective: float = 0.0
    idle_stop_settable: bool = False


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
            from core import aws_account
            # v4 set in code: botocore otherwise signs with bearer whenever
            # AWS_BEARER_TOKEN_BEDROCK is in os.environ (an API-key chat spawn's, or
            # the person's export), and the check would answer for that key's account
            bedrock = session.client("bedrock", config=aws_account.client_config(signature_version="v4"))
            models = bedrock.list_foundation_models(byProvider="Anthropic")
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
        # five AWS API calls (each up to boto3's own timeouts and retries): not on a
        # 'nav' thread every overlay read waits on
        aws_account_id, checks = await executors.run_nav_action(_run_checks)

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
    # Elastic Workers is not an EC2 Hive: it has its own section (GET /elastic)
    return [HiveInstanceResponse(**r) for r in _with_idle_stop([r for r in rows if not _is_elastic(r)])]


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

    return HiveInstanceResponse(**_with_idle_stop([row])[0])


@router.get("/instances/{instance_id}", response_model=HiveInstanceDetailResponse)
async def get_instance(instance_id: str):
    """Get Hive instance details including credentials."""
    async with _conn() as c:
        cursor = await c.execute("SELECT * FROM hive_instances WHERE id = ?", (instance_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Instance not found")
    return HiveInstanceDetailResponse(**_with_idle_stop([dict(row)])[0])


@router.put("/instances/{instance_id}/idle-stop", response_model=HiveIdleStopResponse)
async def set_idle_stop(instance_id: str, body: HiveIdleStop):
    """Set one EC2 Hive's own idle stop (core/hive_idle): ``{"minutes": null}`` follows
    the default, 0 is always on (the Host never stops it; it still follows the Host's
    version when idle), N stops it after N idle minutes. Refused (409) for Elastic Workers
    and any machine the Host never stops (``_idle_stop_applies``). Only the column changes:
    the Hive is neither started nor stopped here, and its idle time keeps counting."""
    _require_desktop()
    async with _conn() as c:
        cursor = await c.execute("SELECT * FROM hive_instances WHERE id = ?", (instance_id,))
        found = await cursor.fetchone()
        if not found:
            raise HTTPException(status_code=404, detail="Instance not found")
        row = dict(found)
        if _is_elastic(row):
            raise HTTPException(status_code=409, detail=ELASTIC_NOT_EC2)
        if not _idle_stop_applies(row, _idle_stop_kinds()):
            raise HTTPException(status_code=409, detail=NOT_IDLE_STOPPED)
        await c.execute("UPDATE hive_instances SET idle_stop_minutes = ? WHERE id = ?",
                        (body.minutes, instance_id))
        await c.commit()
    got = _with_idle_stop([{**row, "idle_stop_minutes": body.minutes}])[0]
    return HiveIdleStopResponse(id=instance_id, idle_stop_minutes=got["idle_stop_minutes"],
                                idle_stop_effective=got["idle_stop_effective"],
                                idle_stop_settable=got["idle_stop_settable"])


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


@router.post("/instances/{instance_id}/restart")
async def restart_instance(instance_id: str):
    """Stop and start a running Hive nothing is using: the owner agent's restart
    (``scripts.swarm_out restart-hive``; the person has Stop and Start). Refused (409)
    when this Host has an open run on it or cannot tell (an unreadable dispatch log), when
    the Hive does not answer its status check or has a session streaming, or when it is
    not running, or while a task is being sent to it. Then it is claimed (running →
    stopping) and the stop and the start run in the background, as an update does: the
    Hive ends 'running', or 'error' with why."""
    _require_desktop()
    async with _conn() as c:
        cursor = await c.execute("SELECT name, status FROM hive_instances WHERE id = ?", (instance_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Instance not found")
    name, status = dict(row)["name"], dict(row)["status"]
    if status != "running":
        raise HTTPException(status_code=409, detail=f"Hive {name} is {status}, not running: "
                                                    "only a running Hive is restarted")
    from core import swarm_out
    held = await executors.run_nav(swarm_out.open_runs_by_hive, True)
    if held is None:
        raise HTTPException(status_code=409, detail=(
            f"cannot tell whether Hive {name} holds open runs (the dispatch log could not be read)"))
    if held.get(str(name), 0) > 0:
        raise HTTPException(status_code=409, detail=(
            f"Hive {name} holds {held[str(name)]} open run(s): a restart would end them"))
    probe = await swarm_out._admission(instance_id)
    if probe is None:
        raise HTTPException(status_code=409, detail=(
            f"Hive {name} did not answer its status check, so whether a session is streaming "
            "on it is unknown: the person restarts it in Swarm Out › settings"))
    if probe["streaming"] > 0:
        raise HTTPException(status_code=409, detail=(
            f"Hive {name} has {probe['streaming']} session(s) streaming: a restart would cut them off"))
    # a dispatch to it has no open run until its session starts (swarm_out._IN_FLIGHT)
    in_flight = (f"a task is being sent to Hive {name}: a restart would cut it off; "
                 "restart it once the dispatch ends")
    if swarm_out.dispatch_in_flight(instance_id, name):
        raise HTTPException(status_code=409, detail=in_flight)
    provisioner = _get_provisioner()
    try:
        await provisioner.claim_stop(instance_id)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if swarm_out.dispatch_in_flight(instance_id, name):    # began while the claim was made
        await provisioner.release_stop_claim(instance_id)
        raise HTTPException(status_code=409, detail=in_flight)
    _keep(asyncio.create_task(provisioner.restart(instance_id), name=f"hive-restart-{instance_id[:8]}"))
    return {"status": "restarting"}


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
    """Delete a Hive and clean up all AWS resources; one fewer is then wanted (the desired
    count goes down by the Hives that stopped counting with it). Without Elastic Workers on standby the SwarmAI account's last
    Hive is refused (design §7.3: somewhere must be left to run tasks)."""
    _require_desktop()
    async with _conn() as c:
        cursor = await c.execute("SELECT * FROM hive_instances WHERE id = ?", (instance_id,))
        found = await cursor.fetchone()
        if not found:
            raise HTTPException(status_code=404, detail="Instance not found")
    if _is_elastic(dict(found)):
        raise HTTPException(status_code=409, detail=ELASTIC_NOT_EC2)
    from hive import fleet
    if (hive_accounts.swarmai_account() is not None and fleet.min_desired() > 0
            and await fleet.is_last(db.db_path, instance_id)):
        raise HTTPException(status_code=409, detail=(
            "This is your last Hive and Elastic Workers is off, so nothing would be left to "
            "run tasks: turn Elastic Workers on in Swarm Out › settings first, or stop this "
            "Hive to stop paying for the instance."))

    provisioner = _get_provisioner()
    try:
        await provisioner.cleanup(instance_id)
    except hive_accounts.ACCOUNT_ERRORS as e:
        # raised before any AWS call: keep the row, or a running instance is forgotten
        raise HTTPException(status_code=409, detail=f"Nothing was deleted: {e}")
    except Exception as e:
        logger.warning("Cleanup had errors (continuing with DB delete): %s", e)

    # Always delete DB record even if AWS cleanup had partial failures; one fewer is then
    # wanted (fleet.delete_and_lower: by the Hives that stopped counting with it)
    await fleet.delete_and_lower(db.db_path, instance_id, dict(found).get("account_ref"))
    return {"deleted": True}


@router.post("/instances/{instance_id}/replace")
async def replace_instance(instance_id: str):
    """Build a new Hive like this one; when it runs, this one is deleted."""
    _require_desktop()
    async with _conn() as c:
        cursor = await c.execute("SELECT * FROM hive_instances WHERE id = ?", (instance_id,))
        found = await cursor.fetchone()
    if found and _is_elastic(dict(found)):
        raise HTTPException(status_code=409, detail=ELASTIC_NOT_EC2)
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
                auth_user=NULL, auth_password=NULL, origin_secret=NULL, updated_at=?
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
    from core.swarm_out import SwarmOutError
    try:
        inst = await brain_sync_host.hive_of(instance_id)
        return await brain_sync_host.sync(inst, brain)
    except (SwarmOutError, brain_sync_host.SyncRefused) as exc:
        raise _brain_sync_error(exc) from None
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"the Hive did not answer ({type(exc).__name__})") from None


# ── Elastic Workers (design 2026-11-20-swarm-out-multi-carrier §6.8) ─────────────
#
# One AgentCore Runtime per account (hive/agentcore_provisioner). Turning it on, updating
# its image and turning it off are claimed here (the answer says at once whether it can be
# done) and run in the background; Swarm Out › settings reads GET /elastic until the row settles.

_elastic: Optional["ElasticHive"] = None


def _get_elastic():
    from hive.agentcore_provisioner import ElasticHive
    global _elastic
    if _elastic is None or _elastic.db_path != db.db_path:
        _elastic = ElasticHive(db.db_path)
    return _elastic


class ElasticEnable(BaseModel):
    # with the SwarmAI account connected: omitted or 'swarmai'; otherwise the account to use
    # (omitted: the only one there is)
    account_ref: Optional[str] = None
    region: Optional[str] = None  # omitted: the account's region


async def _elastic_target(account_ref: Optional[str], region: Optional[str]) -> tuple[str, Optional[str], str]:
    """(account row id, its AWS account id, region) Elastic Workers is turned on in
    (hive/agentcore_provisioner.ElasticHive.target); HTTPException when there is none."""
    from hive.agentcore_provisioner import ElasticTargetError
    try:
        return await _get_elastic().target(account_ref, region)
    except ElasticTargetError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from None


def _elastic_error(exc: Exception) -> HTTPException:
    from hive.agentcore_provisioner import ElasticRefused
    if isinstance(exc, ElasticRefused):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, LookupError):
        return HTTPException(status_code=404, detail="Elastic Workers not found")
    raise exc


@router.get("/elastic")
async def elastic_status():
    """Whether Elastic Workers can be turned on here (and why not), and each one there is
    with its runtime's live state (hive/agentcore_provisioner.ElasticHive.status)."""
    from hive.agentcore_provisioner import standby_on
    elastic = _get_elastic()
    try:
        _, account_id, region = await _elastic_target(None, None)
    except HTTPException as exc:
        out = await elastic.status()
        if not out["hives"]:
            out.update(can_enable=False, reason=str(exc.detail))
    else:
        out = await elastic.status((account_id, region))
    # on standby the Host turns it on by itself (ElasticHive.reconcile); False once the
    # person turned it off here
    from hive.agentcore_provisioner import LAST_STANDBY
    out["standby"] = standby_on()
    # the account the person chose for it with several accounts (None: none chosen)
    from hive.agentcore_provisioner import chosen_account
    out["chosen_account"] = chosen_account()
    # what the standby's last round did or why it could not turn it on (None before one ran)
    out["standby_last"] = LAST_STANDBY.get("did")
    return out


@router.post("/elastic", status_code=202)
async def elastic_enable(body: Optional[ElasticEnable] = None):
    """Turn Elastic Workers on: refused (409) with the reason when there is no usable
    image or it is already on, before anything is created; otherwise the row is written
    ('enabling') and the bucket, role and runtime are made in the background."""
    _require_desktop()
    from hive.agentcore_provisioner import set_standby
    body = body or ElasticEnable()
    account_ref, _, region = await _elastic_target(body.account_ref, body.region)
    elastic = _get_elastic()
    set_standby(True)   # the person turned it on: standby keeps it on from now
    try:
        row = await elastic.claim(account_ref, region)
    except Exception as exc:  # noqa: BLE001 — mapped, or raised again
        raise _elastic_error(exc) from None
    _keep(asyncio.create_task(elastic.provision(row["id"]), name=f"hive-elastic-on-{row['id'][:8]}"))
    return {"id": row["id"], "status": row["status"]}


class ElasticStandby(BaseModel):
    on: bool
    # several accounts in the old mode: the one Elastic Workers uses (kept for the standby)
    account_ref: Optional[str] = None


@router.put("/elastic/standby", status_code=202)
async def elastic_standby(body: ElasticStandby):
    """The person's Elastic Workers switch (Swarm Out › settings, the first-run step). On:
    standby is kept on (and the chosen account recorded) and one standby round starts now.
    Off: standby is turned off before anything else, then a runtime there is turned off;
    with none yet, nothing else happens (a new user can say off before one exists). One
    being changed now (turning on, updating) is turned off by the next standby round, once
    that change ends (ElasticHive.reconcile); it is named in ``after_change``."""
    _require_desktop()
    from hive import agentcore_provisioner as ep
    elastic = _get_elastic()
    if body.on:
        if body.account_ref is not None:
            await _elastic_target(body.account_ref, None)   # an account that exists, or 4xx
            ep.set_chosen_account(body.account_ref)
        ep.set_standby(True)
        _keep(asyncio.create_task(ep.run_standby_round(db.db_path), name="hive-elastic-standby-on"))
        return {"standby": True}
    ep.set_standby(False)
    turning_off, after_change = [], []
    for row in await elastic.rows():
        try:
            await elastic.claim_disable(row["id"])
        except Exception as exc:  # noqa: BLE001 — changing now: the next standby round turns it off
            logger.info("elastic standby off: %s is changing (%s); turned off once that ends",
                        row["id"][:8], type(exc).__name__)
            after_change.append(row["id"])
            continue
        _keep(asyncio.create_task(elastic.run_disable(row["id"]), name=f"hive-elastic-off-{row['id'][:8]}"))
        turning_off.append(row["id"])
    return {"standby": False, "turning_off": turning_off, "after_change": after_change}


@router.post("/elastic/{location_id}/update", status_code=202)
async def elastic_update(location_id: str, body: Optional[HiveInstanceUpdate] = None):
    """Update Elastic Workers' image to a release (default: this SwarmAI's version),
    with its size every time; runs in the background, the row ends 'ready' or 'failed'."""
    _require_desktop()
    elastic = _get_elastic()
    try:
        _, version = await elastic.claim_update(location_id, body.version if body else None)
    except Exception as exc:  # noqa: BLE001 — mapped, or raised again
        raise _elastic_error(exc) from None
    _keep(asyncio.create_task(elastic.run_update(location_id, version), name=f"hive-elastic-up-{location_id[:8]}"))
    return {"id": location_id, "status": "upgrading", "version": version}


@router.delete("/elastic/{location_id}", status_code=202)
async def elastic_disable(location_id: str):
    """Turn Elastic Workers off: its runtime and its row are deleted in the background
    (runs on it end); the bucket and the execution role stay. Standby is turned off first,
    so the Host does not turn it on again (only the person's turn-on does)."""
    _require_desktop()
    from hive.agentcore_provisioner import set_standby
    elastic = _get_elastic()
    set_standby(False)
    try:
        await elastic.claim_disable(location_id)
    except Exception as exc:  # noqa: BLE001 — mapped, or raised again
        raise _elastic_error(exc) from None
    _keep(asyncio.create_task(elastic.run_disable(location_id), name=f"hive-elastic-off-{location_id[:8]}"))
    return {"id": location_id, "status": "disabling"}
