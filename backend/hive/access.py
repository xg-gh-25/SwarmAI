"""Who may log in to an EC2 Hive: the Host's own key and one key per person.

Design docs/design/2026-11-26-hive-access-credentials.md. A Hive used to have one shared
password (user ``admin``) that the Host, the owner's browser and everyone the owner shared
the Hive with all used. Now:

- **The Host's key** (``hive_instances.auth_user`` / ``auth_password``, user ``host`` or
  ``host-b``): what Swarm Out sends (core/swarm_out._auth). No route returns it; a backup
  drops it (core/git_sync_engine). Rotation alternates the two user names so the old and
  the new key can both be in the list for a moment: Caddy refuses a repeated user.
- **A person's key** (table ``hive_access``): made on request, its password returned once
  and only its bcrypt hash kept, revoked alone. The old shared password of a Hive moved to
  this model stays as the entry ``admin`` (``legacy``) until the owner revokes it, so
  nobody it was shared with is cut off by the move.

The Hive holds the list in one file both auth layers read (hive/user_data.py
ACCESS_LIST_PATH). ``apply_access`` is the only writer: it claims the Hive the way an
update does (running -> updating, so it never races an update or another apply), builds
the whole list from the Host's key and every ``hive_access`` row, and runs
``access_apply_sh`` over SSM. Writing goes through SSM, never through the Hive's own login,
so no change here can lock the Host out for good: a failed or unconfirmed change is fixed
by doing it again.
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import aiosqlite
import httpx

from hive.user_data import ACCESS_USER_PATTERN, access_apply_sh, caddy_hash_password

logger = logging.getLogger(__name__)

#: the two user names the Host's own key alternates between (rotate_host_key)
HOST_USERS = ("host", "host-b")
#: the user name of the shared password a Hive had before this model
LEGACY_USER = "admin"
RESERVED_USERS = frozenset(HOST_USERS + (LEGACY_USER,))
LEGACY_LABEL = "旧共享密码"
_NAME_RE = re.compile(rf"^{ACCESS_USER_PATTERN}$")
#: an authenticated, read-only Hive route the Host checks a new key against
KEY_CHECK_PATH = "/api/chat/sessions/admission-state"
KEY_CHECK_TIMEOUT_S = 20.0


class AccessError(Exception):
    """A refused access change. ``status``: 400 for the caller's input, 404 for no such
    Hive or key, 409 for a Hive in a state that cannot take the change now, 502 when the
    Hive did not apply it. The message never holds a password or hash."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def new_key() -> str:
    """A key nobody can guess (192 bits)."""
    return secrets.token_urlsafe(24)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def valid_name(name: Any) -> bool:
    return isinstance(name, str) and bool(_NAME_RE.match(name)) and name not in RESERVED_USERS


def is_ec2(row: dict) -> bool:
    """A row this module acts on: an EC2 Hive with an instance (never an SSH machine,
    an AgentCore or Elastic Workers row)."""
    return (row.get("carrier") or "ec2") == "ec2" and bool(row.get("ec2_instance_id"))


def migrated(row: dict) -> bool:
    """The Hive is on this model: the Host holds its own key."""
    return row.get("auth_user") in HOST_USERS and bool(row.get("auth_password"))


async def _connect(db_path: str) -> aiosqlite.Connection:
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA busy_timeout = 3000")
    return conn


async def _row(db_path: str, hive_id: str) -> dict:
    conn = await _connect(db_path)
    try:
        cur = await conn.execute("SELECT * FROM hive_instances WHERE id = ?", (hive_id,))
        row = await cur.fetchone()
    finally:
        await conn.close()
    if row is None:
        raise AccessError(404, "Hive not found")
    return dict(row)


async def list_access(db_path: str, hive_id: str) -> list[dict]:
    """The person keys of a Hive: user, label, created_at, legacy. Never a hash."""
    await _row(db_path, hive_id)
    conn = await _connect(db_path)
    try:
        cur = await conn.execute(
            "SELECT user, label, created_at, legacy FROM hive_access WHERE hive_id = ? "
            "ORDER BY legacy DESC, created_at, user", (hive_id,))
        rows = [dict(r) for r in await cur.fetchall()]
    finally:
        await conn.close()
    return [{"user": r["user"], "label": r["label"] or r["user"], "created_at": r["created_at"],
             "legacy": bool(r["legacy"])} for r in rows]


async def _person_entries(db_path: str, hive_id: str) -> list[tuple[str, str]]:
    conn = await _connect(db_path)
    try:
        cur = await conn.execute(
            "SELECT user, pass_hash FROM hive_access WHERE hive_id = ? ORDER BY user", (hive_id,))
        return [(r["user"], r["pass_hash"]) for r in await cur.fetchall()]
    finally:
        await conn.close()


async def deploy_access(db_path: str, hive_id: str) -> tuple[str, str, list[tuple[str, str]]]:
    """For a deploy or a retry: a new Host key (user ``host``), its hash, and the person
    keys the Host already has for this Hive (a retry keeps them). The caller stores the key
    with the instance record and renders the list into the user data."""
    key = new_key()
    host_hash = await asyncio.to_thread(caddy_hash_password, key)
    return key, host_hash, await _person_entries(db_path, hive_id)


async def apply_access(provisioner, hive_id: str, host: Optional[tuple[str, str]] = None, *,
                       extra_host: Optional[tuple[str, str]] = None,
                       exclude: Optional[str] = None, claimed: bool = False) -> None:
    """Make the Hive's access list the Host key, the optional second Host key *extra_host*
    (rotation) and every ``hive_access`` row but *exclude* (a revoke in progress).

    *host* ``(user, plaintext key)`` is given only by a rotation or a migration; otherwise
    the Host key is read from the store after the claim, so a change that ran in between
    (a rotation) is never written back over. Claims the Hive (running -> updating) unless
    the caller holds the claim, and gives it back as running. Raises AccessError: 409 when
    it cannot be claimed or the Host has no key for it, 409 when the Hive has no access list
    yet (the script's exit 3), 502 when the script failed or did not finish (the Hive keeps
    or gets back its previous list)."""
    row = await _row(str(provisioner.db_path), hive_id)
    if not is_ec2(row):
        raise AccessError(409, "Only an EC2 Hive has an access list")
    if not claimed:
        await _claim(provisioner, hive_id)
    try:
        if host is None:
            row = await _row(str(provisioner.db_path), hive_id)
            host = _host_key(row)
        entries = [(host[0], await asyncio.to_thread(caddy_hash_password, host[1]))]
        if extra_host is not None:
            entries.append((extra_host[0], await asyncio.to_thread(caddy_hash_password, extra_host[1])))
        entries += [e for e in await _person_entries(str(provisioner.db_path), hive_id)
                    if e[0] != exclude]
        script = access_apply_sh(entries, host[0])
        account = await provisioner._get_account(row["account_ref"])
        session = await provisioner._session(account, row["region"], "manage")
        status, output = await asyncio.to_thread(
            provisioner._run_ssm_script, session, row["region"], row["ec2_instance_id"],
            script, f"SwarmAI Hive access list for {row['name']}")
    finally:
        if not claimed:
            await provisioner._update_instance(hive_id, status="running")
    if status == "Success":
        logger.info("Hive %s: access list applied", row["name"])
        return
    from hive.provisioner import _script_error
    reason = _script_error(output)
    if "has no access list yet" in (output or ""):
        raise AccessError(409, "This Hive has no access list yet: update it first")
    raise AccessError(502, f"The Hive did not apply the access list ({status}): {reason}")


def _host_key(row: dict) -> tuple[str, str]:
    if migrated(row):
        return row["auth_user"], row["auth_password"]
    if row.get("auth_user") in HOST_USERS:
        raise AccessError(409, "This Host has no key for the Hive (restored from a backup?): "
                               "rotate the Host key first")
    raise AccessError(409, "This Hive still uses its shared password: update it first")


async def add_access(provisioner, hive_id: str, name: Any) -> dict:
    """A new person key: validated name, password made here, only its hash stored, the
    list applied. Returns the URL, user and password (shown once, never stored). Any
    failure of the apply removes the row again, so a key whose password nobody got never
    reaches a later list."""
    if not valid_name(name):
        raise AccessError(400, "A name is 1-32 lowercase letters, digits or hyphens, and "
                               "not host, host-b or admin")
    row = await _row(str(provisioner.db_path), hive_id)
    if not is_ec2(row):
        raise AccessError(409, "Only an EC2 Hive has an access list")
    _host_key(row)
    password = new_key()
    pass_hash = await asyncio.to_thread(caddy_hash_password, password)
    conn = await _connect(str(provisioner.db_path))
    try:
        try:
            await conn.execute(
                "INSERT INTO hive_access (id, hive_id, user, label, pass_hash, legacy, created_at) "
                "VALUES (?, ?, ?, ?, ?, 0, ?)",
                (uuid.uuid4().hex, hive_id, name, name, pass_hash, _now()))
            await conn.commit()
        except aiosqlite.IntegrityError:
            raise AccessError(400, f"This Hive already has a key named {name}")
    finally:
        await conn.close()
    try:
        await apply_access(provisioner, hive_id)
    except BaseException:
        await _delete_row(str(provisioner.db_path), hive_id, name)
        raise
    domain = row.get("cloudfront_domain") or ""
    return {"url": f"https://{domain}" if domain else "", "user": name, "password": password}


async def _delete_row(db_path: str, hive_id: str, user: str) -> bool:
    conn = await _connect(db_path)
    try:
        cur = await conn.execute("DELETE FROM hive_access WHERE hive_id = ? AND user = ?",
                                 (hive_id, user))
        await conn.commit()
        return cur.rowcount > 0
    finally:
        await conn.close()


async def revoke_access(provisioner, hive_id: str, user: str) -> None:
    """Apply the list without *user*, and only then remove its row, both under one claim
    (no other access change can write a list in between that still holds the row): a
    revoke that fails (the Hive busy, SSM failing) leaves the key listed here, so the
    person can see it is still valid and revoke again. A revoke of a key that has no row
    applies the list all the same, so repeating a revoke always converges the Hive to the
    list shown."""
    row = await _row(str(provisioner.db_path), hive_id)
    if not is_ec2(row):
        raise AccessError(409, "Only an EC2 Hive has an access list")
    _host_key(row)
    await _claim(provisioner, hive_id)
    try:
        await apply_access(provisioner, hive_id, exclude=user, claimed=True)
        await _delete_row(str(provisioner.db_path), hive_id, user)
    finally:
        await provisioner._update_instance(hive_id, status="running")


async def _claim(provisioner, hive_id: str) -> None:
    try:
        await provisioner.claim_update(hive_id)
    except RuntimeError:
        raise AccessError(409, "The Hive is not running or is busy (an update or another "
                               "access change); try again when it is running")


async def _key_works(row: dict, user: str, key: str) -> bool:
    """The Hive answers the authenticated check route with *user* / *key*."""
    from core.swarm_out import HIVE_HEADERS, hive_event_hooks
    domain = row.get("cloudfront_domain")
    if not domain:
        return False
    try:
        async with httpx.AsyncClient(base_url=f"https://{domain}", timeout=KEY_CHECK_TIMEOUT_S,
                                     headers=HIVE_HEADERS, auth=(user, key),
                                     follow_redirects=False,
                                     event_hooks=hive_event_hooks()) as client:
            resp = await client.get(KEY_CHECK_PATH)
        return resp.status_code == 200
    except httpx.HTTPError:
        return False


async def rotate_host_key(provisioner, hive_id: str) -> None:
    """Give the Host a new key without a moment where it has none: apply the list with
    the old and the new key (the other Host user name), check the new key against the
    Hive, store it, then apply the list without the old key, all under one claim so no
    other access change runs in between. A Hive without a Host key (after a restore:
    backups drop it) gets a new one in a single apply."""
    row = await _row(str(provisioner.db_path), hive_id)
    if not is_ec2(row):
        raise AccessError(409, "Only an EC2 Hive has an access list")
    if _shared_password(row):
        raise AccessError(409, "This Hive still uses its shared password: update it first")
    key = new_key()
    await _claim(provisioner, hive_id)
    try:
        # the Host key as it is once this rotation holds the claim (another rotation may
        # have finished in between: a double click), never as it was before
        row = await _row(str(provisioner.db_path), hive_id)
        if _shared_password(row):
            raise AccessError(409, "This Hive still uses its shared password: update it first")
        old_user, old_key = row.get("auth_user"), row.get("auth_password")
        new_user = HOST_USERS[1] if old_user == HOST_USERS[0] else HOST_USERS[0]
        if old_user in HOST_USERS and old_key:
            await apply_access(provisioner, hive_id, (new_user, key),
                               extra_host=(old_user, old_key), claimed=True)
            if not await _key_works(row, new_user, key):
                raise AccessError(502, "The Hive did not accept the new Host key; the old one "
                                       "still works. Rotate again")
            await provisioner._update_instance(hive_id, auth_user=new_user, auth_password=key)
            await apply_access(provisioner, hive_id, (new_user, key), claimed=True)
        else:
            await apply_access(provisioner, hive_id, (new_user, key), claimed=True)
            await provisioner._update_instance(hive_id, auth_user=new_user, auth_password=key)
    finally:
        await provisioner._update_instance(hive_id, status="running")


def _shared_password(row: dict) -> bool:
    user = row.get("auth_user")
    return user not in HOST_USERS and user is not None and bool(row.get("auth_password"))


async def migrate(provisioner, hive_id: str) -> bool:
    """Move a Hive whose update just gave it an access list (its update holds the claim)
    to this model: its shared password becomes the legacy entry ``admin`` (same password,
    so nobody it was shared with is cut off), the Host gets its own key, the list is
    applied, and only then the shared password is dropped from the Host. Returns True
    when it moved; a failed apply leaves the Host on the shared password (which the
    Hive still accepts) and the next update tries again."""
    row = await _row(str(provisioner.db_path), hive_id)
    if not is_ec2(row) or migrated(row):
        return False
    old_user, old_password = row.get("auth_user"), row.get("auth_password")
    if old_user != LEGACY_USER or not old_password:
        return False
    legacy_hash = await asyncio.to_thread(caddy_hash_password, old_password)
    conn = await _connect(str(provisioner.db_path))
    try:
        await conn.execute("DELETE FROM hive_access WHERE hive_id = ? AND user = ?",
                           (hive_id, LEGACY_USER))
        await conn.execute(
            "INSERT INTO hive_access (id, hive_id, user, label, pass_hash, legacy, created_at) "
            "VALUES (?, ?, ?, ?, ?, 1, ?)",
            (uuid.uuid4().hex, hive_id, LEGACY_USER, LEGACY_LABEL, legacy_hash, _now()))
        await conn.commit()
    finally:
        await conn.close()
    key = new_key()
    await apply_access(provisioner, hive_id, (HOST_USERS[0], key), claimed=True)
    await provisioner._update_instance(hive_id, auth_user=HOST_USERS[0], auth_password=key)
    logger.info("Hive %s moved to the access list; its shared password is the entry %s",
                row["name"], LEGACY_USER)
    return True
