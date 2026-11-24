"""The SSH-machine carrier: a machine of the person's own, reached over an SSH tunnel, running
the same Hive package in a container (design docs/design/2026-11-24-swarm-out-ssh-machine.md
§4, decisions D1-D8). The first one is an internal Cloud Desktop: it has the person's
identity, tools and private network, which EC2 Hives and Elastic Workers do not.

Each machine is a ``hive_instances`` row with ``carrier = 'ssh'`` and its settings in
``carrier_config`` (``config``). To the rest of the Host it is a resident Hive: the same
Hive HTTP API, sessions, records, the S3 bridge (``bridge/<name>/``), Brain sync and usage
sync. What differs, and lives here:

- **Where requests go.** The location's record carries ``_base_url``, the local end of the
  machine's tunnel (core/carriers/ssh_tunnel), which ``swarm_out._base`` uses; the record is
  built here from the row (never ``swarm_out._validate_instance``, which wants a CloudFront
  domain), so nothing stored can change the address.
- **No start, no stop.** The Host does not start or stop the machine: the carrier has no
  ``idle_stop`` duty and offers only running machines, in the warm tier.
- **It reaches a private network** (``reaches_private`` per machine, default yes), so a task
  that needs one can be placed here.
- **Its blocked reasons.** ``unreachable`` (no tunnel or no answer), ``cert_expired`` (the
  machine's latest SSH certificate ended: on a Cloud Desktop, Midway; the person renews it),
  ``out_of_date``, ``saturated``, and ``not_general`` when the person narrowed it to tasks that
  need a private network (``general_tasks`` off; it is on by default, D8).
- **Updates itself when idle** (``follow_host``): the new image is built and swapped by
  hive/ssh_provisioner.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Optional

import httpx

from core.carriers import ssh_tunnel
from core.carriers.base import (BRAIN_SYNC, FLEET, FOLLOW_HOST, SSH, TIER_WARM, USAGE_SYNC,
                                LocationUnavailable, Needs, Offer, RunHandle)

logger = logging.getLogger(__name__)

#: an SSH offer's blocked reasons beyond the EC2 ones (core/carriers/ec2)
UNREACHABLE, OUT_OF_DATE, SATURATED = "unreachable", "out_of_date", "saturated"
CERT_EXPIRED, NOT_GENERAL = "cert_expired", "not_general"

#: the defaults of a machine's settings (``config``)
REMOTE_PORT = 18321


def config(row: dict[str, Any]) -> dict[str, Any]:
    """A machine's settings from its row's ``carrier_config``, with the defaults; ValueError
    when the host or a port is not usable (nothing is ever run with them then)."""
    raw = row.get("carrier_config")
    try:
        cfg = json.loads(raw) if isinstance(raw, str) and raw else (raw if isinstance(raw, dict) else {})
    except ValueError:
        raise ValueError("this SSH machine's settings cannot be read") from None
    if not isinstance(cfg, dict):
        raise ValueError("this SSH machine's settings cannot be read")
    out = {"ssh_host": cfg.get("ssh_host"), "remote_port": cfg.get("remote_port", REMOTE_PORT),
           "local_port": cfg.get("local_port"), "source_profile": cfg.get("source_profile"),
           "reaches_private": cfg.get("reaches_private", True) is not False,
           "general_tasks": cfg.get("general_tasks", True) is not False,
           "mounts": [m for m in (cfg.get("mounts") or []) if isinstance(m, str)],
           "cert_renew_hint": str(cfg.get("cert_renew_hint") or "")[:120]}
    if not ssh_tunnel.valid_host(out["ssh_host"]):
        raise ValueError("this SSH machine has no usable SSH host")
    if not ssh_tunnel.valid_port(out["local_port"]) or not ssh_tunnel.valid_port(out["remote_port"]):
        raise ValueError("this SSH machine has no usable ports")
    return out


def _now() -> float:
    return time.time()


async def _rows() -> list[dict[str, Any]]:
    """Every SSH machine's row with its account's role fields, by name."""
    import aiosqlite
    from core import swarm_out
    async with aiosqlite.connect(swarm_out._db_path()) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA busy_timeout = 3000")
        cur = await conn.execute("PRAGMA table_info(hive_instances)")
        if "carrier" not in {r[1] for r in await cur.fetchall()}:
            return []
        cur = await conn.execute(
            "SELECT i.*, a.auth_method AS _auth_method, a.account_id AS _account_id "
            "FROM hive_instances i LEFT JOIN hive_accounts a ON a.id = i.account_ref "
            "WHERE i.carrier = ? ORDER BY i.name", (SSH,))
        return [dict(r) for r in await cur.fetchall()]


def parse_admission(d: Any) -> Optional[dict[str, Any]]:
    """An SSH machine's ``admission-state``, as the EC2 one is read, plus the end of its
    latest SSH certificate (``ssh_cert_valid_until``: None when it has none or did not say)."""
    from core.carriers.ec2 import parse_admission as ec2_parse
    got = ec2_parse(d)
    if got is None:
        return None
    v = d.get("ssh_cert_valid_until")
    got["cert_valid_until"] = float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    return got


def blocked(probe: Optional[dict[str, Any]], cfg: dict[str, Any], needs: Needs,
            now: Optional[float] = None) -> Optional[str]:
    """Why the machine cannot take this task now, or None (design §4.2). No certificate
    reported does not block: not every machine uses SSH certificates."""
    now = _now() if now is None else now
    if not needs.private and needs.named is None and not cfg["general_tasks"]:
        return NOT_GENERAL
    if probe is None:
        return UNREACHABLE
    until = probe.get("cert_valid_until")
    if isinstance(until, (int, float)) and until <= now:
        return CERT_EXPIRED
    if probe.get("capable") is not True:
        return OUT_OF_DATE
    if probe.get("saturated"):
        return SATURATED
    return None


async def private_reach() -> bool:
    """Whether a running SSH machine says it reaches a private network (``/spec`` tells the
    agent so: a task with private needs is placed there, design §4.4). Never raises."""
    try:
        rows = await _rows()
    except Exception as e:  # noqa: BLE001 — unreadable: none known
        logger.info("carriers.ssh: machines unreadable (%s)", type(e).__name__)
        return False
    for r in rows:
        if r.get("status") != "running":
            continue
        try:
            if config(r)["reaches_private"]:
                return True
        except ValueError:
            continue
    return False


# -- the location: one machine -----------------------------------------------------------------

class SshLocation:
    """One SSH machine (``hive_instances.id``; a name is taken too)."""
    carrier = SSH

    def __init__(self, location_id: str, name: Optional[str] = None):
        self.id = str(location_id)
        self.name = name or self.id

    async def _row(self) -> tuple[dict[str, Any], Optional[dict[str, Any]], dict[str, Any]]:
        from core import swarm_out
        row, acct = await swarm_out._row_and_account(self.id)
        if (row.get("carrier") or "ec2") != SSH:
            raise swarm_out.SwarmOutError(f"Hive {row.get('name')} is not an SSH machine")
        self.id, self.name = str(row["id"]), str(row.get("name") or self.id)
        try:
            cfg = config(row)
        except ValueError as e:
            raise swarm_out.SwarmOutError(f"SSH machine {self.name}: {e}") from None
        return row, acct, cfg

    def _record(self, row: dict[str, Any], acct: Optional[dict[str, Any]], cfg: dict[str, Any]) -> dict[str, Any]:
        """The record every per-machine and per-run caller reads (``swarm_out._client``,
        ``_s3``, ``_session_states``), checked as ``_validate_instance`` checks an EC2 Hive's,
        but with its address from the tunnel instead of a CloudFront domain."""
        from core import swarm_out
        if row.get("status") != "running":
            raise swarm_out.SwarmOutError(f"SSH machine {row.get('name')} is {row.get('status')}, not running")
        if not swarm_out.NAME_RE.match(row.get("name") or "") or not swarm_out.BUCKET_RE.match(row.get("s3_bucket") or ""):
            raise swarm_out.SwarmOutError(f"SSH machine {row.get('name')} has an unexpected name or bucket")
        if not row.get("auth_user") or not row.get("auth_password") or acct is None:
            raise swarm_out.SwarmOutError(f"SSH machine {row.get('name')} has no credentials or account")
        return {**row, "carrier": SSH, "cloudfront_domain": None, "_account": acct,
                "_base_url": f"http://127.0.0.1:{cfg['local_port']}"}

    async def hive(self, wait_s: float = ssh_tunnel.READY_TIMEOUT_S) -> dict[str, Any]:
        """The machine's record after making sure its tunnel runs (built when it does not,
        within *wait_s*). SwarmOutError when it is not running or not usable, or the tunnel
        could not be built."""
        from core import swarm_out
        row, acct, cfg = await self._row()
        rec = self._record(row, acct, cfg)
        try:
            await ssh_tunnel.ensure(self.id, cfg["ssh_host"], cfg["local_port"], cfg["remote_port"], wait_s)
        except ssh_tunnel.TunnelError as e:
            raise swarm_out.SwarmOutError(str(e)) from None
        return rec

    async def up(self, deadline: Any = None, held: frozenset = frozenset()) -> dict[str, Any]:
        """``hive``: an SSH machine is never started by the Host. One that another Swarm
        Run holds the task's Brain on (*held*) is refused before anything is asked of it."""
        from core import swarm_out
        if self.id in held or self.name in held:
            raise swarm_out._held_refusal(self.name)
        rec = await self.hive()
        if rec["id"] in held or rec["name"] in held:
            raise swarm_out._held_refusal(rec["name"])
        return rec

    def client(self, timeout: Optional[float] = None) -> "_MachineClient":
        return _MachineClient(self.id, timeout)

    async def sessions(self) -> Optional[dict[str, dict[str, Any]]]:
        from core import swarm_out
        return await swarm_out._session_states(await self.hive())

    async def admission(self, build: bool = False) -> Optional[dict[str, Any]]:
        """The machine's admission state (``parse_admission``), or None when it did not
        answer properly within PROBE_TIMEOUT_S. A probe (the fleet, placement's offers) only
        looks: a tunnel that is not up is started in the background and this answers None,
        so a dead machine never holds a probe for the tunnel's wait. *build*: wait for the
        tunnel first (a caller about to use the machine)."""
        from core import swarm_out
        try:
            row, acct, cfg = await self._row()
            rec = self._record(row, acct, cfg)
            if build:
                await ssh_tunnel.ensure(self.id, cfg["ssh_host"], cfg["local_port"], cfg["remote_port"])
            elif not await asyncio.to_thread(ssh_tunnel.alive, self.id, cfg["local_port"]):
                ssh_tunnel.ensure_soon(self.id, cfg["ssh_host"], cfg["local_port"], cfg["remote_port"])
                return None
            async with swarm_out._client(rec, swarm_out.PROBE_TIMEOUT_S) as client:
                r = await client.get("/api/chat/sessions/admission-state")
            if r.status_code != 200:
                return None
            d = r.json()
        except Exception as e:                          # unreachable, timed out, not JSON
            logger.warning("carriers.ssh: admission probe of %s failed (%s)", self.name, type(e).__name__)
            return None
        return parse_admission(d)

    async def probe(self) -> dict[str, Any]:
        from core import swarm_out
        row, _ = await swarm_out._row_and_account(self.id)
        out: dict[str, Any] = {"status": row.get("status"), "reachable": None, "streaming": None,
                               "saturated": None, "memory_percent": None, "capable": None,
                               "cert_valid_until": None}
        if row.get("status") != "running":
            return out
        p = await self.admission()
        out["reachable"] = p is not None
        if p is not None:
            out.update(p)
        return out

    async def open_run(self, dispatch_id: str, round: int, deadline: Any = None,
                       needs: Optional[Needs] = None) -> RunHandle:
        """Make the machine ready for a run: its tunnel (at most READY_TIMEOUT_S), then its
        ``admission-state`` as an EC2 Hive's is read (``swarm_out._require_work_orders``),
        then its certificate. Each of these failing is LocationUnavailable, before anything is
        written to the machine. A machine another Swarm Run holds the task's Brain on is
        refused (SwarmOutError)."""
        from core import swarm_out
        deadline = deadline or swarm_out._Deadline(swarm_out.DISPATCH_DEADLINE_S)
        held = frozenset(needs.held) if needs else frozenset()
        if self.id in held or self.name in held:
            raise swarm_out._held_refusal(self.name)
        try:
            rec = await self.hive()
        except swarm_out.SwarmOutError as e:
            raise swarm_out.HiveUnavailable(f"SSH machine {self.name}: {e}; nothing was sent", UNREACHABLE) from None
        if rec["id"] in held or rec["name"] in held:
            raise swarm_out._held_refusal(rec["name"])
        body = await swarm_out._within(deadline, f"checking SSH machine {self.name}", 2 * swarm_out.PROBE_TIMEOUT_S,
                                       lambda: swarm_out._require_work_orders(rec))
        until = body.get("ssh_cert_valid_until")
        if isinstance(until, (int, float)) and not isinstance(until, bool) and until <= _now():
            raise swarm_out.HiveUnavailable(
                f"SSH machine {self.name}'s SSH certificate has expired; renew it on the machine; nothing was sent",
                CERT_EXPIRED)
        logger.info("carriers.ssh: opened round %s of %s on %s", round, dispatch_id, self.name)
        return RunHandle(carrier=SSH, location_id=rec["id"], round=int(round), run_ref=None,
                         address=None, auth_ref=f"hive_instances:{rec['id']}", opened_at=_now())


class _MachineClient:
    """``async with``: a client to the machine's Hive, its address found from the location
    when the block is entered (never one kept from before)."""

    def __init__(self, location_id: str, timeout: Optional[float]):
        self._id, self._timeout = location_id, timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> httpx.AsyncClient:
        from core import swarm_out
        rec = await SshLocation(self._id).hive()
        self._client = swarm_out._client(rec) if self._timeout is None else swarm_out._client(rec, self._timeout)
        return await self._client.__aenter__()

    async def __aexit__(self, *exc) -> Any:
        assert self._client is not None
        return await self._client.__aexit__(*exc)


def _ssh_handle(handle: RunHandle) -> str:
    if handle.carrier != SSH or not handle.location_id:
        raise ValueError(f"not an SSH machine's run handle: {handle.carrier!r}")
    return handle.location_id


# -- the carrier ---------------------------------------------------------------------------

class SshCarrier:
    kind = SSH
    reaches_private = True                # per machine: each offer carries its own
    max_run_s: Optional[int] = None
    brain_lock_scope = "machine"
    duties = frozenset({FLEET, BRAIN_SYNC, USAGE_SYNC, FOLLOW_HOST})

    async def offers(self, needs: Needs) -> list[Offer]:
        """One warm offer per running SSH machine of an account the Host manages. Only a
        live tunnel is probed: a machine without one is ``unreachable`` now and its tunnel
        is built in the background for the next dispatch (design §4.3). A named machine of
        this carrier gets its one offer unprobed; opening it finds out what it is."""
        from core import swarm_out
        from core.carriers.ec2 import _manageable
        from hive import accounts
        rows = await _rows()
        if needs.named is not None:
            named = [r for r in rows if needs.named in (r["id"], r["name"])]
            if len(named) != 1:
                return []
            r = named[0]
            try:
                cfg = config(r)
            except ValueError:
                return []
            return [Offer(SSH, str(r["id"]), str(r["name"]), TIER_WARM,
                          in_flight=swarm_out.in_flight_at(str(r["id"]), str(r["name"])),
                          reaches_private=cfg["reaches_private"], brain_lock_scope=self.brain_lock_scope)]
        mode = await asyncio.to_thread(accounts.swarmai_account)
        open_runs = await asyncio.to_thread(swarm_out.open_runs_by_hive) or {}
        out: list[Offer] = []
        for r in rows:
            if r.get("status") != "running":
                continue
            acct = {"auth_method": r.get("_auth_method"), "account_id": r.get("_account_id"),
                    "id": r.get("account_ref")}
            if not _manageable(acct, mode):
                continue
            try:
                cfg = config(r)
            except ValueError as e:
                logger.warning("carriers.ssh: %s left out (%s)", r.get("name"), e)
                continue
            loc = SshLocation(str(r["id"]), str(r["name"]))
            probe = await loc.admission(build=False)
            if probe is None:
                ssh_tunnel.ensure_soon(loc.id, cfg["ssh_host"], cfg["local_port"], cfg["remote_port"])
            name = str(r["name"])
            out.append(Offer(SSH, loc.id, name, TIER_WARM, open_runs=open_runs.get(name, 0),
                             in_flight=swarm_out.in_flight_at(loc.id, name),
                             streaming=probe["streaming"] if probe else 0,
                             memory_percent=probe["memory_percent"] if probe else None,
                             reaches_private=cfg["reaches_private"], brain_lock_scope=self.brain_lock_scope,
                             blocked=blocked(probe, cfg, needs)))
        return out

    def location(self, location_id: str, name: Optional[str] = None) -> SshLocation:
        return SshLocation(location_id, name)

    def client(self, handle: RunHandle, timeout: Optional[float] = None) -> _MachineClient:
        """``async with carrier.client(handle) as c``: a client to the round's machine, its
        tunnel's address found again each time (a rebuilt tunnel is on the same port)."""
        return _MachineClient(_ssh_handle(handle), timeout)

    async def connect(self, handle: RunHandle) -> dict[str, Any]:
        return await self.location(_ssh_handle(handle)).hive()

    async def running(self) -> list[SshLocation]:
        """The running SSH machines: the locations a timer of this carrier visits."""
        return [SshLocation(str(r["id"]), str(r["name"])) for r in await _rows() if r.get("status") == "running"]

    async def outputs(self, handle: RunHandle, dispatch_id: str) -> dict[str, Any]:
        """The run's kept result after one check on its machine, as an EC2 Hive's
        (``swarm_out_results.fetch``, which reaches it through this carrier's ``connect``).
        Never raises for the machine: a failure is the record's ``fetch_error``."""
        from core import swarm_out, swarm_out_results
        loc_id = _ssh_handle(handle)
        row = await asyncio.to_thread(swarm_out._log_row_of, dispatch_id)
        row = {**row, "hive_id": loc_id, "session_id": handle.run_ref or row.get("session_id")}
        return await swarm_out_results.fetch(row)

    async def state(self, handle: RunHandle) -> tuple[str, str]:
        """(state, reason) of the round's session from its machine, as an EC2 Hive's
        (``swarm_out.run_state`` over the row's status and the machine's session report)."""
        from core import swarm_out
        loc_id = _ssh_handle(handle)
        try:
            status: Optional[str] = (await swarm_out._hive_statuses()).get(loc_id, "gone")
        except Exception as e:  # noqa: BLE001 — store unreadable: the machine is asked anyway
            logger.warning("carriers.ssh: reading the statuses failed (%s)", type(e).__name__)
            status = None
        sessions = None
        if status in ("running", None):
            try:
                sessions = await self.location(loc_id).sessions()
            except Exception as e:  # noqa: BLE001 — the machine did not say: unknown
                logger.warning("carriers.ssh: session states of %s failed (%s)", loc_id, type(e).__name__)
        return swarm_out.run_state({"session_id": handle.run_ref}, status, sessions, False)

    async def release(self, handle: RunHandle, dispatch_id: str) -> None:
        """Tell the machine's Hive to remove the task's workspace (404: no such order there,
        done too). Raises when it was not done: the caller tries again."""
        from core import swarm_out, swarm_out_results
        if not isinstance(dispatch_id, str) or not swarm_out_results.DISPATCH_ID_RE.fullmatch(dispatch_id):
            raise ValueError("not a dispatch id")
        async with self.client(handle, swarm_out.PROBE_TIMEOUT_S * 4) as c:
            resp = await c.post(f"/api/hive/work/{dispatch_id}/release")
        if resp.status_code not in (200, 404):
            raise swarm_out.SwarmOutError(f"the SSH machine did not release {dispatch_id} (HTTP {resp.status_code})")

    async def follow_host(self) -> list[str]:
        """Update each running SSH machine that is idle and behind the Host's version, once
        per version (design §5.4): claimed (running → updating), then
        ``hive.ssh_provisioner.update`` in the background, which ends 'running' (new or old
        container) or 'error'. Idle is ``hive_idle.busy_reasons`` empty, as for an EC2 Hive.
        Names of the machines whose update started."""
        from core import hive_idle, swarm_out
        version = hive_idle._host_version()
        if version is None:
            return []
        machines = [r for r in await _rows() if r.get("status") == "running" and hive_idle._behind(r, version)
                    and (str(r["id"]), version) not in _TRIED]
        if not machines:
            return []
        open_runs = await asyncio.to_thread(swarm_out.open_runs_by_hive, True)
        lessons = await asyncio.to_thread(hive_idle._lessons_by_hive)
        started: list[str] = []
        for r in machines:
            mid, name = str(r["id"]), str(r["name"])
            try:
                sessions = await SshLocation(mid, name).sessions()
            except Exception:  # noqa: BLE001 — unreachable: unknown, so busy
                sessions = None
            reasons = hive_idle.busy_reasons(None if open_runs is None else open_runs.get(name, 0), sessions,
                                             None if lessons is None else lessons.get(mid, 0),
                                             swarm_out.dispatch_in_flight(mid, name))
            if reasons:
                continue
            from hive import ssh_provisioner
            try:
                await ssh_provisioner.claim_update(mid)
            except RuntimeError:                            # its status changed: tried next round
                continue
            if swarm_out.dispatch_in_flight(mid, name):     # began while the claim was made
                await ssh_provisioner.release_update_claim(mid)
                continue
            _TRIED.add((mid, version))                      # once per version, from here on
            logger.info("carriers.ssh: SSH machine %s is at v%s: updating it to the Host's v%s", name,
                        str(r.get("version") or "?").removeprefix("v"), version)
            task = asyncio.get_running_loop().create_task(ssh_provisioner.update(mid, version),
                                                          name=f"ssh-update-{mid[:8]}")
            _UPDATES.add(task)
            task.add_done_callback(_UPDATES.discard)
            started.append(name)
        return started


#: (machine id, version) whose automatic update was tried (once per version, as hive_idle)
_TRIED: set[tuple[str, str]] = set()
_UPDATES: set[asyncio.Task] = set()
