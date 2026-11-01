"""How many Hives: the desired count, the Hive status and keeping the table true to EC2
(design 2026-10-31-swarmai-aws-account §7.1, §7.3).

- ``hive_fleet`` in config.json (only these routes write it): ``desired_count`` 1..8,
  ``instance_type`` and ``region`` for new Hives, ``replacing`` (new id -> old id).
  Missing on an install that never set it: desired = max(1, Hives that count) — an
  upgrader with no Hive sees "1 short" and one button; nothing is created for them.
- ``set_desired``: raising it is the person's consent: under the fleet lock it counts,
  inserts exactly the difference as ``pending`` rows and writes the count in one
  transaction, then starts those deploys. Repeated or concurrent requests only ever
  create the difference. Lowering it creates and deletes nothing: the person picks which
  Hive to delete. Never above ``MAX_HIVES`` (more than that already: only lowering).
- ``status``: ``ready`` | ``short`` (n) | ``over`` (n) | ``cannot_build`` (the
  capability check says what is missing) | ``unavailable`` (no SwarmAI account).
  Only the Hives in the SwarmAI account count here (another account's are listed apart).
- ``reconcile``: the table follows EC2 — an instance EC2 no longer has (deleted in the
  console) is marked so it no longer counts; nothing is created to replace it (that
  costs money, and the deletion may have been on purpose). A lookup that fails counts as
  present. A replacement that is running retires the Hive it replaces.
- ``replace``: a new Hive first, then the old one is deleted (the last one can only be
  replaced, never deleted).
"""
from __future__ import annotations

import asyncio
import logging
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import aiosqlite

from core import aws_account
from hive import accounts as ha

logger = logging.getLogger(__name__)

DEFAULT_TYPE = "m7g.xlarge"
#: monthly estimate per type, on-demand us-east-1 (shown, never a cap)
MONTHLY_USD = {"m7g.medium": 30, "m7g.large": 60, "m7g.xlarge": 119, "m7g.2xlarge": 238,
               "c7g.medium": 26, "c7g.large": 52, "c7g.xlarge": 104, "c7g.2xlarge": 209,
               "t4g.medium": 25, "t4g.large": 49, "t4g.xlarge": 98, "t4g.2xlarge": 196}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_config() -> dict:
    raw = aws_account._cfg().get(aws_account.HIVE_FLEET_KEY)  # noqa: SLF001
    return dict(raw) if isinstance(raw, dict) else {}


def _save_config(fleet: dict) -> None:
    aws_account._cfg().update({aws_account.HIVE_FLEET_KEY: fleet})  # noqa: SLF001


def new_row(*, name: str, account_ref: str, region: str, instance_type: str,
            owner_name: Optional[str] = None, hive_type: str = "shared",
            version: Optional[str] = None, custom_ami_id: Optional[str] = None) -> dict:
    """A ``pending`` hive_instances row (the one shape every creation path inserts)."""
    now = _now()
    return {
        "id": str(__import__("uuid").uuid4()), "name": name, "owner_name": owner_name,
        "hive_type": hive_type, "account_ref": account_ref, "region": region,
        "instance_type": instance_type,
        "ec2_instance_id": None, "ec2_public_ip": None, "elastic_ip_alloc_id": None,
        "security_group_id": None, "iam_role_name": None, "iam_instance_profile_arn": None,
        "cloudfront_dist_id": None, "cloudfront_domain": None, "s3_bucket": None,
        "ssh_key_name": None, "auth_user": None, "auth_password": None,
        "status": "pending", "version": version, "error_message": None,
        "custom_ami_id": custom_ami_id, "seed_data": None, "shared_content": None,
        "created_at": now, "updated_at": now,
    }


async def insert_rows(conn, rows: list) -> None:
    for row in rows:
        cols = ", ".join(row.keys())
        await conn.execute(f"INSERT INTO hive_instances ({cols}) VALUES "
                           f"({', '.join(':' + k for k in row)})", row)


async def _connect(db_path: Path):
    conn = await aiosqlite.connect(str(db_path))
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA busy_timeout = 3000")
    await conn.execute("PRAGMA foreign_keys = ON")
    return conn


async def _counted_in_swarmai(conn) -> int:
    """Hives in the SwarmAI account that count; a replacement pair counts as one."""
    cur = await conn.execute(
        f"SELECT id FROM hive_instances WHERE account_ref = ? AND {ha.COUNTED_SQL}",
        (ha.SWARMAI_ROW_ID,))
    ids = {r[0] for r in await cur.fetchall()}
    pairs = sum(1 for new, old in _replacing().items() if new in ids and old in ids)
    return len(ids) - pairs


async def _free_name(conn) -> str:
    for _ in range(20):
        name = f"hive-{secrets.token_hex(2)}"
        cur = await conn.execute("SELECT 1 FROM hive_instances WHERE name = ?", (name,))
        if not await cur.fetchone():
            return name
    raise RuntimeError("could not find a free Hive name")


def _replacing() -> dict:
    return dict(get_config().get("replacing") or {})


#: the last status computed (the account page, the reconcile loop): Need-You reads it
#: from a worker thread without touching the database (core/attention_authority)
_last_status: Optional[dict] = None


def last_status() -> Optional[dict]:
    return dict(_last_status) if _last_status else None


async def status(db_path: Path) -> dict:
    global _last_status
    out = await _status(db_path)
    _last_status = out
    return out


async def _status(db_path: Path) -> dict:
    mode = ha.swarmai_account()
    fleet = get_config()
    if mode is None:
        return {"state": "unavailable", "desired": None, "counted": 0}
    conn = await _connect(db_path)
    try:
        counted = await _counted_in_swarmai(conn)
        counted_all = await ha.counted(conn)
        cur = await conn.execute(
            "SELECT id, name, instance_type, region, status, created_at FROM hive_instances "
            f"WHERE account_ref = ? AND {ha.COUNTED_SQL} ORDER BY created_at", (ha.SWARMAI_ROW_ID,))
        hives = [dict(r) for r in await cur.fetchall()]
        types = [h["instance_type"] for h in hives]
    finally:
        await conn.close()
    counted_now = counted
    desired = fleet.get("desired_count")
    if not isinstance(desired, int):
        desired = max(1, counted_now)
    caps = (aws_account.get_record() or {}).get("capabilities") or {}
    hive_cap = caps.get("hive") or {}
    out = {"desired": desired, "counted": counted_now, "counted_all": counted_all,
           "max": ha.MAX_HIVES, "instance_type": fleet.get("instance_type") or DEFAULT_TYPE,
           "region": fleet.get("region") or mode["region"],
           "monthly_usd": sum(MONTHLY_USD.get(t, 0) for t in types),
           "dismissed": bool(fleet.get("dismissed")),
           # per instance type, so the page can name the monthly cost of what a raise creates
           "prices": dict(MONTHLY_USD),
           # the Hives that count, one line each on the account page (design §6.5)
           "hives": [{"id": h["id"], "name": h["name"], "instance_type": h["instance_type"],
                      "region": h["region"], "status": h["status"],
                      "created_at": h["created_at"]} for h in hives]}
    if counted_now < desired:
        out.update(state="cannot_build" if hive_cap.get("status") == "missing" else "short",
                   short=desired - counted_now, capability=hive_cap)
    elif counted_now > desired:
        out.update(state="over", over=counted_now - desired)
    else:
        out["state"] = "ready"
    return out


def _checked_defaults(instance_type: Optional[str] = None, region: Optional[str] = None,
                      dismissed: Optional[bool] = None) -> dict:
    """The defaults to write, validated before anything is written."""
    from hive.provisioner import ALLOWED_INSTANCE_TYPES
    out: dict = {}
    if instance_type is not None:
        if instance_type not in ALLOWED_INSTANCE_TYPES:
            raise ValueError("instance type not allowed")
        out["instance_type"] = instance_type
    if region is not None:
        import re
        if not re.fullmatch(r"[a-z]{2}(-[a-z]+)+-\d", region):
            raise ValueError("not an AWS region")
        out["region"] = region
    if dismissed is not None:
        out["dismissed"] = bool(dismissed)
    return out


async def set_desired(db_path: Path, desired: int, *, start_deploy,
                      defaults: Optional[dict] = None) -> dict:
    """Write the desired count (and *defaults*, saved only when the count is accepted);
    raising it creates exactly the difference (then ``start_deploy(id)`` for each new
    row, after the commit). Every read-modify-write of ``hive_fleet`` happens under the
    fleet lock and re-reads the config inside it (a replacement written meanwhile stays)."""
    if ha.swarmai_account() is None:
        raise ValueError("connect the SwarmAI account first")
    if not isinstance(desired, int) or not 1 <= desired <= ha.MAX_HIVES:
        raise ValueError(f"between 1 and {ha.MAX_HIVES} Hives")
    changes = _checked_defaults(**(defaults or {}))
    new_ids: list = []
    async with ha.fleet_lock:
        fleet = {**get_config(), **changes}
        await ha.ensure_swarmai_row(db_path)
        conn = await _connect(db_path)
        try:
            counted = await _counted_in_swarmai(conn)
            counted_all = await ha.counted(conn)
            create = max(0, desired - counted)
            if create and counted_all + create > ha.MAX_HIVES:
                raise ValueError(ha.over_limit_message(counted_all))
            mode = ha.swarmai_account() or {}
            for _ in range(create):
                row = new_row(name=await _free_name(conn), account_ref=ha.SWARMAI_ROW_ID,
                              region=fleet.get("region") or mode.get("region") or "us-east-1",
                              instance_type=fleet.get("instance_type") or DEFAULT_TYPE,
                              hive_type="my")
                await insert_rows(conn, [row])
                new_ids.append(row["id"])
            fleet.update(desired_count=desired, dismissed=False)
            _save_config(fleet)
            await conn.commit()
        finally:
            await conn.close()
    for iid in new_ids:
        start_deploy(iid)
    return {"desired": desired, "created": new_ids}


async def set_defaults(*, instance_type: Optional[str] = None, region: Optional[str] = None,
                       dismissed: Optional[bool] = None) -> dict:
    changes = _checked_defaults(instance_type, region, dismissed)
    async with ha.fleet_lock:
        fleet = {**get_config(), **changes}
        _save_config(fleet)
    return fleet


async def is_last(db_path: Path, instance_id: str) -> bool:
    """True when deleting *instance_id* would leave the SwarmAI account with no Hive that
    counts (the last one can only be replaced)."""
    conn = await _connect(db_path)
    try:
        cur = await conn.execute(
            f"SELECT id, account_ref FROM hive_instances WHERE {ha.COUNTED_SQL}")
        rows = [dict(r) for r in await cur.fetchall()]
    finally:
        await conn.close()
    mine = [r for r in rows if r["account_ref"] == ha.SWARMAI_ROW_ID]
    return any(r["id"] == instance_id for r in mine) and len(mine) == 1


async def replace(db_path: Path, instance_id: str, *, start_deploy) -> dict:
    """A new Hive like *instance_id*; once it runs, ``reconcile`` deletes the old one."""
    async with ha.fleet_lock:
        conn = await _connect(db_path)
        try:
            cur = await conn.execute("SELECT * FROM hive_instances WHERE id = ?", (instance_id,))
            old = await cur.fetchone()
            if old is None:
                raise LookupError("Instance not found")
            old = dict(old)
            if old["account_ref"] != ha.SWARMAI_ROW_ID:
                raise ValueError("only a Hive in the SwarmAI account can be replaced")
            if instance_id in _replacing().values():
                raise ValueError("this Hive is already being replaced")
            n = await ha.counted(conn, excluding=instance_id)
            if n + 1 > ha.MAX_HIVES:
                raise ValueError(ha.over_limit_message(n))
            row = new_row(name=await _free_name(conn), account_ref=ha.SWARMAI_ROW_ID,
                          region=old["region"], instance_type=old["instance_type"],
                          owner_name=old.get("owner_name"), hive_type=old.get("hive_type") or "my")
            await insert_rows(conn, [row])
            fleet = get_config()
            fleet.setdefault("replacing", {})[row["id"]] = instance_id
            _save_config(fleet)
            await conn.commit()
        finally:
            await conn.close()
    start_deploy(row["id"])
    return {"new": row["id"], "replaces": instance_id}


def _ec2_states(session, region: str, ids: list) -> dict:
    """{instance id: state}; an id EC2 does not know is "gone". Raises when EC2 cannot
    be asked (the caller then treats every instance as present)."""
    ec2 = session.client("ec2", region_name=region)
    out: dict = {}
    for iid in ids:
        try:
            res = ec2.describe_instances(InstanceIds=[iid])
            states = [i["State"]["Name"] for r in res.get("Reservations", []) for i in r["Instances"]]
            out[iid] = states[0] if states else "gone"
        except Exception as exc:  # noqa: BLE001
            code = ((getattr(exc, "response", None) or {}).get("Error") or {}).get("Code", "")
            if code == "InvalidInstanceID.NotFound":
                out[iid] = "gone"
            else:
                raise
    return out


async def reconcile(db_path: Path, *, cleanup) -> dict:
    """Bring the table in line with EC2 and finish replacements. ``cleanup(id)`` deletes
    a Hive (the old half of a finished replacement)."""
    if ha.swarmai_account() is None:
        return {}
    conn = await _connect(db_path)
    try:
        cur = await conn.execute(
            "SELECT * FROM hive_instances WHERE account_ref = ? AND ec2_instance_id IS NOT NULL "
            "AND status IN ('running', 'stopped', 'error')", (ha.SWARMAI_ROW_ID,))
        rows = [dict(r) for r in await cur.fetchall()]
        cur = await conn.execute("SELECT * FROM hive_accounts WHERE id = ?", (ha.SWARMAI_ROW_ID,))
        acct = await cur.fetchone()
    finally:
        await conn.close()
    gone: list = []
    gone_ec2: list = []
    by_region: dict = {}
    for r in rows:
        by_region.setdefault(r["region"], []).append(r)
    for region, items in by_region.items():
        try:
            session = await asyncio.to_thread(ha.session_for, dict(acct), region, "read")
            states = await asyncio.to_thread(_ec2_states, session, region,
                                             [r["ec2_instance_id"] for r in items])
        except Exception as exc:  # noqa: BLE001 — cannot ask EC2: everything stays present
            logger.info("hive fleet: could not check %s (%s)", region, type(exc).__name__)
            continue
        for r in items:
            if states.get(r["ec2_instance_id"]) in ("gone", "terminated"):
                gone.append(r["id"])
                gone_ec2.append((r["id"], r["ec2_instance_id"]))
    if gone:
        gone = []   # only the rows actually changed (a retry may have re-pointed one meanwhile)
        conn = await _connect(db_path)
        try:
            for iid, ec2_id in gone_ec2:
                # only while the row still points at the instance EC2 no longer has: a
                # retry that launched a new instance meanwhile keeps it (and it counts)
                cur = await conn.execute(
                    "UPDATE hive_instances SET status = 'error', ec2_instance_id = NULL, "
                    "ec2_public_ip = NULL, error_message = ?, updated_at = ? "
                    "WHERE id = ? AND ec2_instance_id = ?",
                    ("Its EC2 instance no longer exists (deleted outside SwarmAI). Retry builds "
                     "it again; Delete removes what is left.", _now(), iid, ec2_id))
                if cur.rowcount:
                    gone.append(iid)
            await conn.commit()
        finally:
            await conn.close()
        logger.info("hive fleet: %d Hive(s) no longer exist in EC2", len(gone))
    finished: list = []
    async with ha.fleet_lock:
        # under the lock, re-read: a replacement started meanwhile is never dropped, and
        # only the pairs settled here are removed
        replacing = _replacing()
        if replacing:
            done: list = []
            conn = await _connect(db_path)
            try:
                for new_id, old_id in replacing.items():
                    cur = await conn.execute("SELECT status FROM hive_instances WHERE id = ?", (new_id,))
                    row = await cur.fetchone()
                    if row is None or row[0] == "error":
                        done.append(new_id)  # the new one failed or is gone: the old one stays
                    elif row[0] == "running":
                        finished.append(old_id)
                        done.append(new_id)
            finally:
                await conn.close()
            if done:
                fleet = get_config()
                fleet["replacing"] = {k: v for k, v in (fleet.get("replacing") or {}).items()
                                      if k not in done}
                _save_config(fleet)
    for old_id in finished:
        try:
            await cleanup(old_id)
        except Exception:  # noqa: BLE001 — the old one stays listed; deleting it is one click
            logger.warning("hive fleet: could not delete the replaced Hive %s", old_id, exc_info=True)
    return {"gone": gone, "replaced": finished}
