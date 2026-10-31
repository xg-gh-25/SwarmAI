"""Which AWS account a Hive runs in, and the boto3 session to reach it
(design 2026-10-31-swarmai-aws-account §5.4, §5.5, §7.3).

Two modes, decided by one predicate (``swarmai_account``):

- **SwarmAI mode** — the SwarmAI AWS account is connected here (``aws_account.active()``
  and the record names an account id). New Hives are created only in it, through the
  row ``id='swarmai'`` (``auth_method='swarmai'``, no credentials of its own: the session
  comes from ``aws_account.boto_session``, pinned to the record's account). A row the
  migration could not show to be the same account keeps its own credentials, but they
  are used only to stop, delete, clean up and read the Hives already recorded on it.
- **Legacy mode** — no resolvable account record: every row works exactly as before
  (its own access keys or profile, else the default chain), and rows can be added.

The migration (``migrate``) only re-points rows; it never deletes a row and never touches
``auth_config``. What it changed is appended to a 0600 file first, so it can be undone.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import aiosqlite

from core import aws_account

logger = logging.getLogger(__name__)

SWARMAI_ROW_ID = "swarmai"
SWARMAI_METHOD = "swarmai"
#: a row whose Hives were moved to the SwarmAI row (kept one version, read-only)
MERGED_METHOD = "merged"
#: what another account's credentials may still be used for
OTHER_ACCOUNT_PURPOSES = frozenset({"stop", "cleanup", "read"})
PURPOSES = OTHER_ACCOUNT_PURPOSES | {"manage"}
#: XG-decided guard against mis-operation: it counts machines, not spend
MAX_HIVES = 8
_STS_TIMEOUT_S = 25

#: one lock for every path that can add a Hive (create, retry): count and write inside it
fleet_lock = asyncio.Lock()


class OtherAccountError(RuntimeError):
    """The Hive's account is not the SwarmAI account and the action needs it."""


def swarmai_account() -> Optional[dict]:
    """``{"account_id", "region"}`` in SwarmAI mode, else ``None`` (legacy mode)."""
    if not aws_account.active():
        return None
    rec = aws_account.get_record() or {}
    if not rec.get("account_id"):
        return None
    return {"account_id": rec["account_id"], "region": rec.get("region") or "us-east-1"}


def row_role(row: dict, mode: Optional[dict] = None) -> str:
    """How the Hive settings show a row: ``swarmai`` (the SwarmAI account), ``disconnected``
    (the SwarmAI row while that account is not connected: nothing can be done with it),
    ``other`` (another account, stop/delete only), ``merged`` (its Hives moved to the
    SwarmAI row), ``own`` (legacy mode: the row works as before)."""
    method = row.get("auth_method")
    if method == SWARMAI_METHOD:
        return SWARMAI_METHOD if mode is not None else "disconnected"
    if method == MERGED_METHOD:
        return MERGED_METHOD
    return "other" if mode is not None else "own"


def _legacy_session(row: dict, region: str):
    import boto3
    auth_config = json.loads(row.get("auth_config") or "{}")
    method = row.get("auth_method", "access_keys")
    kwargs: dict[str, Any] = {"region_name": region}
    if method == "access_keys" and auth_config.get("access_key_id"):
        kwargs["aws_access_key_id"] = auth_config["access_key_id"]
        kwargs["aws_secret_access_key"] = auth_config.get("secret_access_key", "")
    elif method == "sso" and auth_config.get("profile"):
        kwargs["profile_name"] = auth_config["profile"]
    # else: default credential chain (IAM role, env vars, etc.)
    return boto3.Session(**kwargs)


def session_for(row: dict, region: str, purpose: str = "manage"):
    """The boto3 session for one Hive row and what it is used for (``manage`` | ``stop``
    | ``cleanup`` | ``read``). Raises ``OtherAccountError`` or ``aws_account.AccountError``
    instead of reaching some other account."""
    if purpose not in PURPOSES:
        raise ValueError(f"unknown purpose {purpose!r}")
    method = row.get("auth_method")
    if method == MERGED_METHOD:
        raise OtherAccountError("This account's Hives were moved to the SwarmAI account.")
    mode = swarmai_account()
    if method == SWARMAI_METHOD:
        if mode is None:
            raise aws_account.AccountError(
                "unconnected", "The SwarmAI AWS account is not connected: connect it in "
                               "Settings > SwarmAI account.")
        if row.get("account_id") != mode["account_id"]:
            raise OtherAccountError(
                f"This Hive is in AWS account {row.get('account_id')}, not the SwarmAI "
                f"account {mode['account_id']}.")
        return aws_account.boto_session(region)
    if mode is not None and purpose not in OTHER_ACCOUNT_PURPOSES:
        raise OtherAccountError(
            f"This Hive is in another AWS account ({row.get('account_id')}): it can only be "
            "stopped or deleted. Verify the account in Settings > Hive if it is in fact the "
            "SwarmAI account.")
    return _legacy_session(row, region)


#: what ``session_for`` raises instead of reaching some other account
ACCOUNT_ERRORS = (OtherAccountError, aws_account.AccountError)


# ── migration (design §5.5) ────────────────────────────────────────────────────

def _backup_file() -> Path:
    return aws_account.aws_dir() / "hive-accounts-migration.json"


def _append_backup(entry: dict) -> None:
    path = _backup_file()
    entries: list = []
    if path.exists():
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            entries = None  # type: ignore[assignment]
        if not isinstance(entries, list):  # damaged: keep it aside, never overwrite it
            aside = path.with_name(f"{path.name}.corrupt-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}")
            path.rename(aside)
            logger.warning("hive: the migration backup could not be read; kept as %s", aside.name)
            entries = []
    entries.append(entry)
    aws_account._write_private(path, json.dumps(entries, indent=2))  # noqa: SLF001


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _connect(db_path: Path):
    conn = await aiosqlite.connect(str(db_path))
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA busy_timeout = 3000")
    await conn.execute("PRAGMA foreign_keys = ON")
    return conn


async def ensure_swarmai_row(db_path: Path) -> Optional[dict]:
    """In SwarmAI mode, make sure the SwarmAI row exists (database only, no AWS call).
    A row that already holds Hives keeps its account id: re-pointing it would send
    those Hives' actions to another account (changing account is §7.5, not this)."""
    mode = swarmai_account()
    if mode is None:
        return None
    conn = await _connect(db_path)
    try:
        await conn.execute(
            "INSERT OR IGNORE INTO hive_accounts (id, account_id, label, auth_method, "
            "auth_config, default_region, created_at, verified_at) "
            "VALUES (?, ?, 'SwarmAI account', ?, '{}', ?, ?, NULL)",
            (SWARMAI_ROW_ID, mode["account_id"], SWARMAI_METHOD, mode["region"], _now()))
        cur = await conn.execute("SELECT * FROM hive_accounts WHERE id = ?", (SWARMAI_ROW_ID,))
        row = dict(await cur.fetchone())
        if row["account_id"] != mode["account_id"]:
            cur = await conn.execute(
                "SELECT COUNT(*) FROM hive_instances WHERE account_ref = ?", (SWARMAI_ROW_ID,))
            if (await cur.fetchone())[0] == 0:
                await conn.execute(
                    "UPDATE hive_accounts SET account_id = ?, default_region = ? WHERE id = ?",
                    (mode["account_id"], mode["region"], SWARMAI_ROW_ID))
                row.update(account_id=mode["account_id"], default_region=mode["region"])
            else:
                logger.warning("hive: the SwarmAI row holds Hives in account %s, the SwarmAI "
                               "account is now %s", row["account_id"], mode["account_id"])
        await conn.commit()
        return row
    finally:
        await conn.close()


def _sts_account(row: dict) -> str:
    from botocore.config import Config
    s = _legacy_session(row, row.get("default_region") or "us-east-1")
    sts = s.client("sts", config=Config(connect_timeout=10, read_timeout=15,
                                        retries={"max_attempts": 1}))
    return sts.get_caller_identity()["Account"]


#: one merge at a time (startup migration and a Verify click can overlap)
_merge_lock = asyncio.Lock()


async def merge_row(db_path: Path, row: dict) -> str:
    """``check_row`` without the account it found."""
    return (await check_row(db_path, row))[0]


async def check_row(db_path: Path, row: dict) -> "tuple[str, Optional[str]]":
    """Check one legacy row with STS; when its credentials reach the SwarmAI account,
    move its Hives to the SwarmAI row. ``(merged | other | unverified, account found)``.
    Nothing moves onto a SwarmAI row that names another account (it still holds Hives
    from before the account changed): those Hives would then be reachable by nobody."""
    mode = swarmai_account()
    if mode is None or row.get("auth_method") in (SWARMAI_METHOD, MERGED_METHOD):
        return "other", None
    try:
        actual = await asyncio.wait_for(asyncio.to_thread(_sts_account, row), _STS_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 — offline, expired: try again next start
        logger.info("hive: account row %s not verified (%s)", row.get("id"), type(exc).__name__)
        return "unverified", None
    if actual != mode["account_id"]:
        return "other", actual
    async with _merge_lock:
        target = await ensure_swarmai_row(db_path)
        if target is None or target["account_id"] != mode["account_id"]:
            return "unverified", actual
        return await _move(db_path, row), actual


async def _move(db_path: Path, row: dict) -> str:
    conn = await _connect(db_path)
    try:
        cur = await conn.execute("SELECT auth_method FROM hive_accounts WHERE id = ?", (row["id"],))
        now = await cur.fetchone()
        if now is None:
            return "unverified"  # deleted meanwhile
        if now[0] == MERGED_METHOD:
            return "merged"
        cur = await conn.execute("SELECT id FROM hive_instances WHERE account_ref = ?", (row["id"],))
        moved = [r[0] for r in await cur.fetchall()]
        _append_backup({"at": _now(), "row_id": row["id"], "account_id": row.get("account_id"),
                        "label": row.get("label"), "auth_method": now[0],
                        "instances_moved": moved})
        await conn.execute("UPDATE hive_instances SET account_ref = ? WHERE account_ref = ?",
                           (SWARMAI_ROW_ID, row["id"]))
        await conn.execute("UPDATE hive_accounts SET auth_method = ? WHERE id = ?",
                           (MERGED_METHOD, row["id"]))
        await conn.commit()
        logger.info("hive: account row %s is the SwarmAI account; %d Hive(s) moved",
                    row["id"], len(moved))
        return "merged"
    finally:
        await conn.close()


async def migrate(db_path: Path) -> dict:
    """Daemon startup, SwarmAI mode only; idempotent. ``{row_id: outcome}``."""
    if await ensure_swarmai_row(db_path) is None:
        return {}
    conn = await _connect(db_path)
    try:
        cur = await conn.execute(
            "SELECT * FROM hive_accounts WHERE auth_method NOT IN (?, ?)",
            (SWARMAI_METHOD, MERGED_METHOD))
        rows = [dict(r) for r in await cur.fetchall()]
    finally:
        await conn.close()
    return {row["id"]: await merge_row(db_path, row) for row in rows}


# ── how many Hives (design §7.3) ───────────────────────────────────────────────

#: a Hive counts while its EC2 instance may exist: everything but a failed create
COUNTED_SQL = "NOT (status = 'error' AND ec2_instance_id IS NULL) AND status != 'deleted'"


async def counted(conn, *, excluding: Optional[str] = None) -> int:
    sql = f"SELECT COUNT(*) FROM hive_instances WHERE {COUNTED_SQL}"
    args: tuple = ()
    if excluding:
        sql += " AND id != ?"
        args = (excluding,)
    cur = await conn.execute(sql, args)
    return (await cur.fetchone())[0]


def over_limit_message(n: int) -> str:
    return (f"At most {MAX_HIVES} Hives: there are {n} already. Delete one first."
            if n <= MAX_HIVES else
            f"There are {n} Hives, more than the limit of {MAX_HIVES}: you can only reduce them.")
