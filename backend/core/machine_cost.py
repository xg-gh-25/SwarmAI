"""What the machines under Swarm Out cost, next to what the model cost (the history view's
cost section; mock Knowledge/Mockups/2026-11-23-swarm-out-dashboard-redesign-v7.html).

Model cost is each run's own (``result.cost_usd``, the usage ledger). Machine cost is an
estimate from what each carrier bills:

- **EC2 Hives** bill by the hour while their instance runs, busy or idle, and the Host
  stops an idle one (core/hive_idle) or the person may stop or start it anywhere. The
  hours come from EC2 itself, never from ``hive_instances.status`` (which says ``error``
  while an instance still runs): ``record_power`` asks EC2 every 10 minutes (main.py, the
  hive loop) and keeps one ``hive_power`` row per change. An ``on`` row's ``since`` is the
  instance's LaunchTime, an ``off`` row's the stop time EC2 gives in
  StateTransitionReason (the time of the look when it gives none); ``observed_at`` of a
  Hive's last row is refreshed on every look. A Hive's record begins at its first row's
  ``since``: EC2's LaunchTime says the instance has run since then, so the first look
  already covers the hours before it. Earlier hours are unknown, never guessed, and so
  are hours no look saw: a period followed by a new LaunchTime (stopped and started again
  between two looks, e.g. while the Mac slept) ends when it was last seen on, and the
  last period ends at most STALE_S after the last look (EC2 could not be read since).
- **Elastic Workers (AgentCore)** bill per second while a run's session lives: memory for
  the whole session, CPU only while used. SwarmAI does not measure the CPU, so a run's
  estimate is MEMORY_GIB over its billed seconds plus VCPU over its ``active_s`` (an upper
  bound). The session ends at the earlier of its release by this Host (the carrier's end
  marker) and the result's ``ended_at`` plus the idle timeout (a session nobody stops ends
  on its own), and never after its maximum lifetime.

The history view shares an EC2 Hive's hours among its runs by active time
(desktop components/swarm-out/costStats.ts); this module only records and prices.

- ``record_power(db_path)``  — one look at every EC2 Hive; rows appended on change.
- ``machines(db_path, now)`` — the EC2 Hives' on periods and prices, AgentCore's prices.
- ``agentcore_cost(run)``    — a run's billed seconds and dollars, or None.
- ``attach(rows)``           — ``machine`` on every AgentCore run of the runs answer.
"""
from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: hours in a month of the monthly prices (hive/fleet.MONTHLY_USD)
HOURS_PER_MONTH = 730.0
#: AgentCore Runtime on-demand prices, us-east-1 (an estimate: check the AWS price page)
USD_PER_VCPU_HOUR = 0.0895
USD_PER_GIB_HOUR = 0.00945

#: how long after its last look a Hive's open period is still taken as on: two looks
#: (main.py looks every 10 minutes); past it, EC2 has not been read and the hours are unknown
STALE_S = 1200

_ON = frozenset({"pending", "running", "stopping"})
_OFF = frozenset({"stopped", "shutting-down", "terminated", "gone"})
_STOP_AT = re.compile(r"\((\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) GMT\)")

_TABLE = """CREATE TABLE IF NOT EXISTS hive_power (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hive_id TEXT NOT NULL,
    ec2_instance_id TEXT,
    instance_type TEXT,
    state TEXT NOT NULL CHECK (state IN ('on', 'off')),
    since TEXT NOT NULL,
    observed_at TEXT NOT NULL)"""
_INDEX = "CREATE INDEX IF NOT EXISTS idx_hive_power_hive ON hive_power(hive_id, id)"


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _now_iso() -> str:
    return _iso(datetime.now(timezone.utc))


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute(_TABLE)
    conn.execute(_INDEX)
    return conn


def hourly_usd(instance_type: Optional[str]) -> Optional[float]:
    """The on-demand hourly price of an EC2 instance type, None when it is not known."""
    from hive import fleet
    monthly = fleet.MONTHLY_USD.get(instance_type or "")
    return round(monthly / HOURS_PER_MONTH, 5) if monthly else None


def agentcore_size() -> dict[str, Any]:
    from hive import agentcore_provisioner as ap
    return {"vcpu": ap.SIZE["vcpu_base"], "memory_gib": ap.SIZE["memory_gib_base"],
            "idle_timeout_s": ap.IDLE_TIMEOUT_S,
            "usd_per_vcpu_hour": USD_PER_VCPU_HOUR, "usd_per_gib_hour": USD_PER_GIB_HOUR}


# ── EC2: recording ──────────────────────────────────────────────────────────


def ec2_power(session: Any, region: str, ids: list[str]) -> dict[str, dict[str, Any]]:
    """{instance id: {"state", "launch", "transition"}} from one describe_instances; an id EC2
    does not know is ``gone``. Raises when EC2 cannot be asked."""
    ec2 = session.client("ec2", region_name=region)
    out: dict[str, dict[str, Any]] = {}
    try:
        res = ec2.describe_instances(InstanceIds=ids)
    except Exception as exc:  # noqa: BLE001 — one unknown id fails the batch: ask each
        code = ((getattr(exc, "response", None) or {}).get("Error") or {}).get("Code", "")
        if code != "InvalidInstanceID.NotFound" or len(ids) == 1:
            if code == "InvalidInstanceID.NotFound":
                return {ids[0]: {"state": "gone", "launch": None, "transition": None}}
            raise
        for iid in ids:
            out.update(ec2_power(session, region, [iid]))
        return out
    for r in res.get("Reservations", []):
        for i in r.get("Instances", []):
            m = _STOP_AT.search(i.get("StateTransitionReason") or "")
            launch = i.get("LaunchTime")
            out[i["InstanceId"]] = {
                "state": (i.get("State") or {}).get("Name") or "",
                "launch": _iso(launch) if isinstance(launch, datetime) else None,
                "transition": _iso(datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")) if m else None,
            }
    for iid in ids:
        out.setdefault(iid, {"state": "gone", "launch": None, "transition": None})
    return out


def _observe(conn: sqlite3.Connection, hive: dict[str, Any], seen: dict[str, Any], now: str) -> bool:
    """Append a row when EC2's state or period start differs from the Hive's last row, else
    refresh that row's ``observed_at``. True when a row was appended. A state that is
    neither on nor off (EC2 gave something new) writes nothing."""
    state = seen.get("state")
    if state in _ON:
        new, since = "on", seen.get("launch") or now
    elif state in _OFF:
        new, since = "off", seen.get("transition") or now
    else:
        return False
    last = conn.execute("SELECT id, state, since FROM hive_power WHERE hive_id = ? "
                        "ORDER BY id DESC LIMIT 1", (hive["id"],)).fetchone()
    changed = last is None or last["state"] != new or (new == "on" and last["since"] != since)
    if not changed:
        conn.execute("UPDATE hive_power SET observed_at = ? WHERE id = ?", (now, last["id"]))
        return False
    conn.execute("INSERT INTO hive_power (hive_id, ec2_instance_id, instance_type, state, since, "
                 "observed_at) VALUES (?, ?, ?, ?, ?, ?)",
                 (hive["id"], hive.get("ec2_instance_id"), hive.get("instance_type"), new, since, now))
    return True


def _record_power_sync(db_path: Path) -> dict[str, int]:
    from hive import accounts as ha
    conn = _connect(db_path)
    try:
        hives = [dict(r) for r in conn.execute(
            "SELECT id, account_ref, region, instance_type, ec2_instance_id FROM hive_instances "
            "WHERE ec2_instance_id IS NOT NULL AND ec2_instance_id != ''")]
        accounts = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM hive_accounts")}
    finally:
        conn.close()
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for h in hives:
        groups.setdefault((h["account_ref"], h["region"]), []).append(h)
    appended = skipped = 0
    for (acct, region), items in groups.items():
        row = accounts.get(acct)
        try:
            if row is None:
                raise LookupError("no account row")
            session = ha.session_for(row, region, "read")
            seen = ec2_power(session, region, [h["ec2_instance_id"] for h in items])
        except Exception as exc:  # noqa: BLE001 — EC2 cannot be asked: nothing is recorded
            logger.info("machine cost: could not read EC2 in %s (%s)", region, type(exc).__name__)
            skipped += len(items)
            continue
        now = _now_iso()
        conn = _connect(db_path)
        try:
            for h in items:
                if _observe(conn, h, seen.get(h["ec2_instance_id"]) or {}, now):
                    appended += 1
            conn.commit()
        finally:
            conn.close()
    return {"appended": appended, "skipped": skipped}


async def record_power(db_path: Path) -> dict[str, int]:
    """One look at every EC2 Hive (see the header): {"appended", "skipped"}."""
    return await asyncio.to_thread(_record_power_sync, db_path)


# ── EC2: reading ────────────────────────────────────────────────────────────


def machines(db_path: Path, now: Optional[str] = None) -> dict[str, Any]:
    """The runs answer's ``machines``: every EC2 Hive with a record (its name, instance type,
    hourly price, when its record begins and its on periods as [start, end]) and AgentCore's
    size and prices. A period ends at the next row's stop time when that row is off, at its
    own last look when the next row is a new start (the stop was not seen), and the last one
    at *now* for a Hive still in hive_instances, never more than STALE_S after its last look;
    at its last look for a removed Hive."""
    now = now or _now_iso()
    conn = _connect(db_path)
    try:
        names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM hive_instances")}
        rows = [dict(r) for r in conn.execute("SELECT * FROM hive_power ORDER BY hive_id, id")]
    finally:
        conn.close()
    by: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by.setdefault(r["hive_id"], []).append(r)
    ec2 = []
    for hid, rs in by.items():
        periods: list[list[Optional[str]]] = []
        for i, r in enumerate(rs):
            if r["state"] != "on":
                continue
            nxt = rs[i + 1] if i + 1 < len(rs) else None
            if nxt is not None:
                end = nxt["since"] if nxt["state"] == "off" else r["observed_at"]
            elif hid in names:
                seen = _ts(r["observed_at"])
                cap = None if seen is None else seen + STALE_S
                end = now if cap is None or (_ts(now) or 0) <= cap else _iso(datetime.fromtimestamp(cap, timezone.utc))
            else:
                end = r["observed_at"]
            if (_ts(end) or 0) > (_ts(r["since"]) or 0):
                periods.append([r["since"], end])
        itype = rs[-1]["instance_type"]
        ec2.append({"hive_id": hid, "name": names.get(hid), "instance_type": itype,
                    "hourly_usd": hourly_usd(itype), "recorded_since": rs[0]["since"],
                    "on": periods})
    return {"ec2": ec2, "agentcore": agentcore_size(), "estimate": True}


# ── AgentCore ───────────────────────────────────────────────────────────────


def _ts(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    return None


def agentcore_cost(run: dict[str, Any]) -> Optional[dict[str, Any]]:
    """{"seconds", "usd"} of an AgentCore run, None when a round has no start or no end
    (see the header) or the run did not run on AgentCore."""
    from core.carriers import agentcore as ac
    rounds = [r for r in (run.get("rounds") or []) if isinstance(r, dict)]
    if not rounds or any(r.get("carrier") != "agentcore" for r in rounds):
        return None
    size = agentcore_size()
    result = run.get("result") if isinstance(run.get("result"), dict) else {}
    result_end = _ts(result.get("ended_at"))
    seconds = 0.0
    for i, r in enumerate(rounds):
        start = _ts(r.get("opened_at"))
        if start is None:
            return None
        ends = []
        rel = ac.released_at(str(r.get("run_ref") or ""))
        if rel is not None:
            ends.append(rel)
        if i == len(rounds) - 1 and result_end is not None:
            ends.append(result_end + size["idle_timeout_s"])
        if not ends:
            return None
        end = min(min(ends), start + ac.MAX_RUN_S)
        seconds += max(0.0, end - start)
    active = result.get("active_s")
    active = float(active) if isinstance(active, (int, float)) and not isinstance(active, bool) else 0.0
    usd = (seconds / 3600 * size["memory_gib"] * size["usd_per_gib_hour"]
           + min(active, seconds) / 3600 * size["vcpu"] * size["usd_per_vcpu_hour"])
    return {"seconds": round(seconds), "usd": round(usd, 4)}


def attach(rows: list[dict[str, Any]]) -> None:
    """``machine`` on every AgentCore run of the runs answer (None when it cannot be told);
    other runs get none (their machine cost is shared in the history view)."""
    for r in rows:
        if any(isinstance(x, dict) and x.get("carrier") == "agentcore" for x in (r.get("rounds") or [])):
            try:
                r["machine"] = agentcore_cost(r)
            except Exception as exc:  # noqa: BLE001 — a cost that cannot be told is unknown
                logger.debug("machine cost: %s: %s", r.get("dispatch_id"), type(exc).__name__)
                r["machine"] = None
