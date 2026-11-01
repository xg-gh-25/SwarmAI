"""The SwarmAI AWS account API (design docs/design/2026-10-31-swarmai-aws-account.md).

Who may call each route is in ``core/swarm_out_ops.py`` (rows ``aws_account.*``,
design §7.7): reading, re-verifying, the capability checks and the automatic detection
are the person's and the agent's; signing in, connecting or changing the account,
storing keys, checking a profile that runs another program and raising the Hive count
are the person's. Every POST needs ``X-Swarm-User-Action`` (``_require_app``). The one
route that hands out credentials answers only a caller holding this boot's token from
``<data dir>/aws/daemon.json`` (the ``python-backend aws-credentials`` subcommand).

Connecting never locks the person out: the candidate is verified first, and a failure
leaves the current account exactly as it was.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from core import aws_account, aws_detect, aws_permissions, aws_signin, executors
from core.app_config_manager import AppConfigManager

logger = logging.getLogger(__name__)
router = APIRouter()
_UA = Header(default=None, alias="X-Swarm-User-Action")
#: detection checks at a time (each in its own process)
_detect_slots = asyncio.Semaphore(4)
#: one connect at a time: the "is the account changing" decision, moving the old
#: account's Hives and the commit happen with no other connect in between
_connect_lock = asyncio.Lock()


def _db_path():
    from database import db
    return db.db_path


async def _fleet_status() -> Optional[dict]:
    if aws_account.is_hive_mode():
        return None
    try:
        from hive import fleet
        return await fleet.status(_db_path())
    except Exception:  # noqa: BLE001 — the account page still shows the account
        logger.warning("hive fleet status failed", exc_info=True)
        return None


async def _view() -> dict:
    return {**aws_account.view(), "hive": await _fleet_status()}


@router.get("")
async def get_account():
    return await _view()


def _require_app(user_action: str | None) -> None:
    """A POST that does work (ada, STS, a Bedrock call) comes from the app or the agent,
    never from a web page: ``X-Swarm-User-Action`` is not a CORS-safelisted header, so a
    cross-site page cannot send it without a preflight the daemon refuses."""
    if not user_action:
        raise HTTPException(status_code=403, detail="X-Swarm-User-Action required")


@router.post("/verify")
async def verify_account(
        user_action: str | None = Header(default=None, alias="X-Swarm-User-Action")):
    """Resolve and pin again now (one STS call; ada or the profile may run)."""
    _require_app(user_action)
    try:
        await executors.run_in("aws_account", aws_account.resolve, True)
    except aws_account.AccountError:
        pass  # the record's state says what went wrong
    return await _view()


class CheckRequest(BaseModel):
    region: Optional[str] = None  # the Hive region to check (default: the main region)


@router.post("/check")
async def check_capabilities(body: Optional[CheckRequest] = None, user_action: str | None = _UA):
    """Run the capability checks: one 1-token Bedrock call, and "can it create" checks
    for Hive and storage (DryRun and policy simulation: nothing is created)."""
    _require_app(user_action)
    region = (body.region if body else None) or None
    if region:
        try:
            aws_signin.check_region(region)
        except aws_signin.SigninError as exc:
            raise HTTPException(status_code=422, detail=exc.message)
    await executors.run_in("aws_account", aws_account.run_capability_checks,
                           AppConfigManager.instance(), region)
    return await _view()


# ── detection (design §6.1 ②) ────────────────────────────────────────────────

@router.get("/detect")
async def detect():
    """Stage 1: every profile on this machine and what its configuration says."""
    rows = await executors.run_in("aws_account", aws_detect.stage1)
    return {"rows": rows, "context": "internal" if aws_account.find_ada() else "external",
            "login": await executors.run_in("aws_account", aws_signin.login_support)}


class DetectVerify(BaseModel):
    profile: str  # "" = ADA's own profile


async def _verify_row(profile: str, user_asked: bool) -> dict:
    row = await executors.run_in("aws_account", aws_detect.find_row, profile)
    if row is None:
        raise HTTPException(status_code=404, detail="no such profile")
    async with _detect_slots:
        # a thread of the default pool waits on the child; the child does the work
        return await asyncio.to_thread(aws_detect.verify, row, user_asked=user_asked)


@router.post("/detect/verify")
async def detect_verify(body: DetectVerify, user_action: str | None = _UA):
    """Stage 2 for one row that is safe to check unasked (no prompt, no file written)."""
    _require_app(user_action)
    return await _verify_row(body.profile, False)


@router.post("/detect/check")
async def detect_check(body: DetectVerify, user_action: str | None = _UA):
    """The person asked to check a row whose program may prompt (Touch ID, MFA, keychain)."""
    _require_app(user_action)
    return await _verify_row(body.profile, True)


# ── signing in (§6.3, §6.3b) ──────────────────────────────────────────────────

def _signin_error(exc: aws_signin.SigninError) -> HTTPException:
    return HTTPException(status_code=409 if exc.kind in ("expired", "denied") else 422,
                         detail={"kind": exc.kind, "message": exc.message})


class SsoStart(BaseModel):
    start_url: str
    sso_region: str


@router.post("/sso/start")
async def sso_start(body: SsoStart, user_action: str | None = _UA):
    _require_app(user_action)
    try:
        return await executors.run_in("aws_account", aws_signin.sso_start, body.start_url,
                                      body.sso_region)
    except aws_signin.SigninError as exc:
        raise _signin_error(exc)


class SsoPoll(BaseModel):
    flow_id: str


@router.post("/sso/poll")
async def sso_poll(body: SsoPoll, user_action: str | None = _UA):
    _require_app(user_action)
    try:
        return await executors.run_in("aws_account", aws_signin.sso_poll, body.flow_id)
    except aws_signin.SigninError as exc:
        raise _signin_error(exc)
    except aws_account.AccountError as exc:
        raise HTTPException(status_code=409, detail={"kind": exc.kind, "message": exc.message})


class LoginStart(BaseModel):
    region: str


@router.post("/login/start")
async def login_start(body: LoginStart, user_action: str | None = _UA):
    _require_app(user_action)
    try:
        return await executors.run_in("aws_account", aws_signin.login_start, body.region)
    except aws_signin.SigninError as exc:
        raise _signin_error(exc)


@router.get("/login/status")
async def login_status():
    return await executors.run_in("aws_account", aws_signin.login_status)


class StoreKeys(BaseModel):
    access_key_id: str
    secret_access_key: str


@router.post("/keys")
async def store_keys(body: StoreKeys, user_action: str | None = _UA):
    """Keep access keys for the ``keys`` source (the secret store; never returned)."""
    _require_app(user_action)
    try:
        key_id = await executors.run_in("aws_account", aws_signin.store_keys, body.access_key_id,
                                        body.secret_access_key)
    except aws_signin.SigninError as exc:
        raise _signin_error(exc)
    # the key ID (never the secret) is what the connect candidate names
    return {"stored": True, "keys_id": key_id}


# ── connect / change the account (§6.1 ④, §6.5, §7.5) ────────────────────────

class Connect(BaseModel):
    candidate: dict
    old_hives: Optional[str] = None  # "keep": another account's Hives stay, stop/delete only


@router.post("/connect")
async def connect(body: Connect, user_action: str | None = _UA):
    """Verify the candidate, then make it the SwarmAI account. When the current account
    has Hives and the new one is another account, the person decides first: keep them
    (stop and delete only) or delete them before changing (409 lists them)."""
    _require_app(user_action)
    async with _connect_lock:
        return await _connect_locked(body)


async def _connect_locked(body: Connect) -> dict:
    from hive import accounts as hive_accounts
    old = aws_account.get_record()
    try:
        rec, creds = await executors.run_in("aws_account", aws_account.verify_candidate,
                                            body.candidate)
    except aws_account.AccountMismatch as exc:
        raise HTTPException(status_code=409, detail={
            "kind": "account_mismatch", "message": exc.message,
            "expected": exc.expected, "actual": exc.actual})
    except aws_account.AccountError as exc:
        raise HTTPException(status_code=401 if exc.kind == "needs_login" else 422,
                            detail={"kind": exc.kind, "message": exc.message})
    kept_row = None
    if not aws_account.is_hive_mode():
        # the account the SwarmAI row's Hives are in decides, not only the record: a
        # missing or unverified record must not let another account adopt them
        row_acct = await hive_accounts.swarmai_row_account(_db_path())
        old_acct = row_acct or (old or {}).get("account_id")
        hives = await hive_accounts.swarmai_hives(_db_path()) \
            if old_acct and old_acct != rec["account_id"] else []
        if hives:
            # the old record can sign in to them only when it is that account's record
            can_keep = bool(old) and (old or {}).get("account_id") == old_acct and \
                (old or {}).get("source") in hive_accounts.KEEPABLE_SOURCES
            if body.old_hives != "keep" or not can_keep:
                raise HTTPException(status_code=409, detail={
                    "kind": "old_hives" if body.old_hives != "keep" else "cannot_keep",
                    "account_id": old_acct,
                    "message": f"{len(hives)} Hive(s) are in {old_acct}: keep them "
                               "(stop and delete only) or delete them first",
                    "hives": hives, "can_keep": can_keep})
            try:
                kept_row = await hive_accounts.keep_old_hives(_db_path(), old)
            except hive_accounts.OtherAccountError as exc:
                raise HTTPException(status_code=409, detail={"kind": "cannot_keep",
                                                             "message": str(exc)})
    try:
        await executors.run_in("aws_account", aws_account.commit, rec, creds)
    except BaseException:
        # the account did not change: its Hives go back to the SwarmAI row
        await hive_accounts.undo_keep(_db_path(), kept_row)
        raise
    try:
        kept = await hive_accounts.kept_records(_db_path()) if not aws_account.is_hive_mode() else []
        await executors.run_in("aws_account", aws_signin.prune, [rec, *kept])
    except Exception:  # noqa: BLE001 — a leftover sign-in is removed at the next connect
        logger.warning("aws account: could not drop unused sign-ins", exc_info=True)
    if not aws_account.is_hive_mode():
        await hive_accounts.ensure_swarmai_row(_db_path())
        try:
            await hive_accounts.migrate(_db_path())
        except Exception:  # noqa: BLE001 — merging old rows is retried at every start
            logger.warning("hive accounts: merge after connect failed", exc_info=True)
    await executors.run_in("aws_account", aws_account.run_capability_checks,
                           AppConfigManager.instance(), None)
    return await _view()


@router.get("/permission-template")
async def permission_template():
    """The IAM policy (and Hive role boundary) to give the account's identity (§7.4)."""
    rec = aws_account.get_record() or {}
    return aws_permissions.template(rec.get("account_id"))


# ── how many Hives (§7.3) ─────────────────────────────────────────────────────

class Fleet(BaseModel):
    desired_count: Optional[int] = None
    instance_type: Optional[str] = None
    region: Optional[str] = None
    dismissed: Optional[bool] = None   # "later": Need-You stops asking, the page still shows it


@router.put("/hive-fleet")
async def set_fleet(body: Fleet, user_action: str | None = _UA):
    """Raising the count creates exactly the difference (the person's consent: the
    button names the count, the monthly estimate and the account)."""
    _require_app(user_action)
    if aws_account.is_hive_mode():
        raise HTTPException(status_code=403, detail="Hive management is only available from the desktop app.")
    from hive import fleet
    try:
        defaults = {"instance_type": body.instance_type, "region": body.region,
                    "dismissed": body.dismissed}
        if body.desired_count is not None:
            # the defaults are saved with the count, and only when the count is accepted
            from routers.hive import start_deploy
            await fleet.set_desired(_db_path(), body.desired_count, start_deploy=start_deploy,
                                    defaults=defaults)
        else:
            await fleet.set_defaults(**defaults)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return await _view()


class DefaultVpc(BaseModel):
    region: str


@router.post("/default-vpc")
async def default_vpc(body: DefaultVpc, user_action: str | None = _UA):
    """Create the default VPC a Hive needs in *region* (the person clicked it on the
    capability line; off-machine, so the person's), then check that region again."""
    _require_app(user_action)
    if aws_account.is_hive_mode():
        raise HTTPException(status_code=403, detail="Only available from the desktop app.")
    try:
        await executors.run_in("aws_account", aws_account.create_default_vpc, body.region)
    except aws_account.AccountError as exc:
        raise HTTPException(status_code=403 if exc.kind == "missing" else _STATUS.get(exc.kind, 422),
                            detail={"kind": exc.kind, "message": exc.message})
    await executors.run_in("aws_account", aws_account.run_capability_checks,
                           AppConfigManager.instance(), body.region)
    return await _view()


_STATUS = {"unconnected": 404, "needs_login": 401, "account_mismatch": 409}


@router.get("/credentials")
async def get_credentials(x_swarm_credentials_token: str | None = Header(default=None)):
    """Short-term credentials in the ``credential_process`` format, pinned to the account."""
    if not aws_account.token_ok(x_swarm_credentials_token):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    try:
        creds = await executors.run_in("aws_account", aws_account.resolve)
    except aws_account.AccountError as exc:
        return JSONResponse({"error": exc.kind, "message": exc.message},
                            status_code=_STATUS.get(exc.kind, 503))
    return creds.process_json()
