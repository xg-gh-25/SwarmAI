"""The EC2 carrier: the person's Hive EC2 instances (design
docs/design/2026-11-20-swarm-out-multi-carrier.md §5, §4.2-§4.5).

Today's code in ``core.swarm_out`` behind the carrier contract. Each Hive is a location; a
run's handle names the Hive by id, its CloudFront domain and where its credentials are
(``hive_instances:<id>``), never the credentials. The lookup of a Hive by id or name
(``instance``, ``instance_up``) lives here: ``core.swarm_out`` keeps the two names
(``swarm_out._instance``, ``swarm_out._instance_up``) only so that this module calls them
through it, and no module outside ``core/carriers`` calls them
(tests/test_carriers_no_instance_outside.py).

What this module adds is the memory of a stopped Hive's version: a stopped Hive cannot be
probed, so whether it can take a task is what the last probe while it ran said, at the
version it had then (``_PROBES_FILE`` under the Swarm Out state directory). Every probe of
a running Hive made for placement or for opening a run writes it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from typing import Any, Optional

import httpx

from core.carriers.base import (BRAIN_SYNC, DIAGNOSTICS, EC2, FLEET, IDLE_STOP, TIER_COLD, TIER_WARM,
                                USAGE_SYNC, LocationUnavailable, Needs, Offer, RunHandle)

logger = logging.getLogger(__name__)

#: the last probe of each Hive while it ran: ``{hive_id: {version, capable, at}}``
_PROBES_FILE = "hive_probes.json"
_probes_lock = threading.Lock()

#: the reasons an EC2 offer is blocked (Offer.blocked)
SATURATED, OUT_OF_DATE, UNREACHABLE, UNKNOWN_VERSION = (
    "saturated", "out_of_date", "unreachable", "unknown_version")


# -- the last probe per Hive -------------------------------------------------------------

def _probes_path():
    from core import swarm_out
    return swarm_out._state_dir() / _PROBES_FILE


def _read_probes() -> dict[str, dict[str, Any]]:
    """The kept probe results; empty when there is no file or it cannot be read (a stopped
    Hive is then offered as of unknown version, never as capable)."""
    try:
        d = json.loads(_probes_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in d.items() if isinstance(k, str) and isinstance(v, dict)} \
        if isinstance(d, dict) else {}


def _remember(found: dict[str, tuple[Optional[str], bool]]) -> None:
    """Keep each probed running Hive's result (``{hive_id: (version, capable)}``)."""
    if not found:
        return
    with _probes_lock:
        kept = _read_probes()
        now = time.time()
        for hid, (version, capable) in found.items():
            kept[hid] = {"version": version, "capable": capable, "at": now}
        p = _probes_path()
        tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
        try:
            tmp.write_text(json.dumps(kept, sort_keys=True), encoding="utf-8")
            os.replace(tmp, p)
        except OSError as e:
            logger.warning("carriers.ec2: keeping the Hive probes failed (%s)", type(e).__name__)
            tmp.unlink(missing_ok=True)


async def _remember_one(hive_id: str, version: Optional[str], probe: Optional[dict[str, Any]]) -> None:
    if probe is not None:
        await asyncio.to_thread(_remember, {hive_id: (version, probe["capable"] is True)})


def stopped_blocked(hive_id: str, version: Optional[str], probes: dict[str, dict[str, Any]]) -> Optional[str]:
    """Why a stopped Hive cannot be offered as able to take a task, or None: only one whose
    last probe while running, at the version it has now, found it capable. Known incapable
    at this version: ``out_of_date``; otherwise ``unknown_version``."""
    last = probes.get(hive_id)
    if not isinstance(version, str) or not version or not last or last.get("version") != version:
        return UNKNOWN_VERSION
    return None if last.get("capable") is True else OUT_OF_DATE


def running_blocked(probe: Optional[dict[str, Any]]) -> Optional[str]:
    """Why a running Hive cannot take a run now, or None. A Hive whose version cannot
    take the task is out of date whether or not it is full (D5: out of date is not full)."""
    if probe is None:
        return UNREACHABLE
    if probe["capable"] is not True:
        return OUT_OF_DATE
    if probe["saturated"]:
        return SATURATED
    return None


def parse_admission(d: Any) -> Optional[dict[str, Any]]:
    """An ``admission-state`` answer validated as ``swarm_out._admission`` does, or None."""
    from core import swarm_out
    if not isinstance(d, dict):
        return None
    streaming, saturated = swarm_out._count(d.get("streaming_count"), 0), d.get("saturated")
    if streaming is None or not isinstance(saturated, bool):
        return None
    return {"streaming": streaming, "saturated": saturated,
            "memory_percent": swarm_out._percent(d.get("memory_percent")),
            "capable": all(d.get(c) is True for c in swarm_out.REQUIRED_CAPS)}


# -- a Hive by id or name ----------------------------------------------------------------

async def instance(hive_ref: str) -> dict[str, Any]:
    """A running Hive by id or name, with its account row; validated fields only
    (``swarm_out._validate_instance``). ``swarm_out._instance``."""
    from core import swarm_out
    return swarm_out._validate_instance(*await swarm_out._row_and_account(hive_ref))


async def instance_up(hive_ref: str, deadline: Any, held: frozenset = frozenset()) -> dict[str, Any]:
    """``instance``, after bringing a stopped Hive up (``swarm_out._ensure_running``): a
    dispatch to a stopped Hive starts it first. Only a Hive whose status is the one thing
    wrong is started: one whose address, bucket, credentials or account are not usable is
    refused for that, one of an account this Host may not run tasks on is refused
    (``refuse_unmanaged``), and one in *held* (ids: another Swarm Run holds this Brain
    there) is refused before it is started. A Hive found running again (an idle stop's
    claim given back meanwhile) is taken as it is, after a wake of it under way ends (it may
    update the Hive). Any other status is refused as before.
    ``swarm_out._instance_up``."""
    from core import hive_idle, swarm_out
    try:
        inst = await swarm_out._instance(hive_ref)
    except swarm_out.SwarmOutError as refused:
        try:
            row, acct = await swarm_out._row_and_account(hive_ref)
        except Exception:  # noqa: BLE001 — no single Hive / store unreadable: the refusal stands
            raise refused from None
        if row.get("status") == "running":
            inst = await swarm_out._instance(hive_ref)
        else:
            if row.get("status") not in hive_idle.WAKES_FROM:
                raise
            swarm_out._validate_instance({**row, "status": "running"}, acct)   # unusable: its own reason
            await refuse_unmanaged(row.get("name"), acct)
            if str(row.get("id")) in held:
                raise swarm_out._held_refusal(str(row.get("name"))) from None
            await swarm_out._ensure_running(row, deadline)
            inst = await swarm_out._instance(hive_ref)
    if hive_idle.wake_pending(str(inst.get("id"))):
        # a wake started for another dispatch: it may update the Hive now that it runs,
        # so this dispatch waits for it instead of sending under the update
        await swarm_out._ensure_running(inst, deadline)
        inst = await swarm_out._instance(hive_ref)
    await refuse_unmanaged(inst.get("name"), inst.get("_account"))
    return inst


async def refuse_unmanaged(name: Any, acct: Any) -> None:
    """Refuse (SwarmOutError) a Hive of an account this Host may not run tasks on: another
    account's Hive may only be read, stopped or deleted, so no run goes there (R1.8;
    ``offers`` leaves it out too). Read from the account row's ``auth_method``, which a
    stored account always has (the column is NOT NULL); a record without it is not one this
    check can read, and is left to the checks that read the record."""
    from core import swarm_out
    from hive import accounts
    if not isinstance(acct, dict) or "auth_method" not in acct:
        return
    mode = await asyncio.to_thread(accounts.swarmai_account)
    if not _manageable(acct, mode):
        raise swarm_out.SwarmOutError(f"Hive {name} is in an account this Host may not run tasks on; "
                                      "nothing was sent")


# -- the Hives this Host may run on ------------------------------------------------------

def _manageable(acct: dict[str, Any], mode: Optional[dict]) -> bool:
    """Whether the Host may manage this account's Hives (hive/accounts.row_role): the
    SwarmAI account while connected, or a legacy row in legacy mode. Another account's
    Hive may only be read, stopped or deleted, so no run goes there (R1.8). A Hive with
    no account row (``auth_method`` is NOT NULL, so None means the join found none) is
    not manageable either."""
    from hive import accounts
    return acct.get("auth_method") is not None and accounts.row_role(acct, mode) in (
        accounts.SWARMAI_METHOD, "own")


async def _hive_rows() -> list[dict[str, Any]]:
    """Every Hive with its account's role fields, by name."""
    import aiosqlite
    from core import swarm_out
    async with aiosqlite.connect(swarm_out._db_path()) as conn:
        await conn.execute("PRAGMA busy_timeout = 3000")
        cur = await conn.execute(
            "SELECT i.id, i.name, i.status, i.version, a.auth_method, a.account_id "
            "FROM hive_instances i LEFT JOIN hive_accounts a ON a.id = i.account_ref ORDER BY i.name")
        return [dict(zip(("id", "name", "status", "version", "auth_method", "account_id"), r))
                for r in await cur.fetchall()]


async def _named_carrier(ref: str) -> Optional[str]:
    """The carrier of the one row with this id or name, or None when there is no such
    row or the store cannot be read (opening the location then says what it is)."""
    import aiosqlite
    from core import swarm_out
    try:
        async with aiosqlite.connect(swarm_out._db_path()) as conn:
            await conn.execute("PRAGMA busy_timeout = 3000")
            cur = await conn.execute(
                "SELECT COALESCE(carrier, 'ec2') FROM hive_instances WHERE id = ? OR name = ?", (ref, ref))
            rows = await cur.fetchall()
    except Exception as exc:  # noqa: BLE001 — unreadable: offered as before, the open decides
        logger.info("ec2: carrier of %s unreadable (%s)", ref[:40], type(exc).__name__)
        return None
    return str(rows[0][0]) if len(rows) == 1 else None


def _in_flight(hive_id: str, name: str) -> int:
    """Dispatches to this Hive in progress here (``swarm_out.in_flight_at``: by its id, and
    by its name for one placed on a named Hive whose id is not read yet)."""
    from core import swarm_out
    return swarm_out.in_flight_at(hive_id, name)


# -- the location: one Hive --------------------------------------------------------------

class Ec2Location:
    """One Hive (``hive_instances.id``; a name is taken too: every lookup is by id or
    name). Its machine-level operations: the probe, its record for the Hive's own APIs
    (Brain sync, the S3 bridge, Swarm Out › settings), its sessions, a client, opening a run."""
    carrier = EC2

    def __init__(self, hive_id: str, name: Optional[str] = None):
        self.id = str(hive_id)
        self.name = name or self.id

    async def hive(self) -> dict[str, Any]:
        """The Hive's validated record (``swarm_out._instance``): SwarmOutError when it is
        not running or not usable. For the operations that take a Hive's record."""
        from core import swarm_out
        inst = await swarm_out._instance(self.id)
        self.name = str(inst.get("name") or self.name)
        return inst

    async def up(self, deadline: Any, held: frozenset = frozenset()) -> dict[str, Any]:
        """``hive``, after starting a stopped Hive (``swarm_out._instance_up``)."""
        from core import swarm_out
        inst = await swarm_out._instance_up(self.id, deadline, held)
        self.name = str(inst.get("name") or self.name)
        return inst

    def client(self, timeout: Optional[float] = None) -> "_HiveClient":
        """``async with location.client() as c``: a client to this Hive."""
        return _HiveClient(self.id, timeout)

    async def sessions(self) -> Optional[dict[str, dict[str, Any]]]:
        """The Hive's own report of its sessions (``swarm_out._session_states``), or None
        when it did not say. Raises SwarmOutError when it is not running."""
        from core import swarm_out
        return await swarm_out._session_states(await self.hive())

    async def admission(self) -> Optional[dict[str, Any]]:
        """A running Hive's admission state, validated (``parse_admission``), or None when
        it did not answer properly within PROBE_TIMEOUT_S. ``swarm_out._admission``."""
        from core import swarm_out
        try:
            inst = await swarm_out._instance(self.id)
            async with swarm_out._client(inst, swarm_out.PROBE_TIMEOUT_S) as client:
                r = await client.get("/api/chat/sessions/admission-state")
            if r.status_code != 200:
                return None
            d = r.json()
        except Exception as e:                          # unreachable, timed out, not JSON
            logger.warning("swarm_out: admission probe failed (%s)", type(e).__name__)
            return None
        return parse_admission(d)

    async def probe(self) -> dict[str, Any]:
        """``{status, reachable, streaming, saturated, memory_percent, capable}``: today's
        ``admission-state`` probe of a running Hive (``swarm_out._admission``), kept for
        when it is stopped. A Hive not running is not asked (the probe fields are None)."""
        from core import swarm_out
        row, _ = await swarm_out._row_and_account(self.id)
        self.name = str(row.get("name") or self.name)
        out: dict[str, Any] = {"status": row.get("status"), "reachable": None, "streaming": None,
                               "saturated": None, "memory_percent": None, "capable": None}
        if row.get("status") != "running":
            return out
        p = await swarm_out._admission(self.id)
        await _remember_one(self.id, row.get("version"), p)
        out["reachable"] = p is not None
        if p is not None:
            out.update(p)
        return out

    async def open_run(self, dispatch_id: str, round: int, deadline: Any = None,
                       needs: Optional[Needs] = None) -> RunHandle:
        """Make the Hive ready for a run and return the round's handle (``run_ref`` None:
        the session is named later). A stopped Hive is started first (``up``; a start that
        fails or runs out of time is a SwarmOutError, never a reason to try another: it
        goes on starting), as is today's dispatch. A Hive of an account the Host may not
        manage, or one another Swarm Run holds the task's Brain on (*needs*.held), is
        refused (SwarmOutError) before anything is asked of it. Then its
        ``admission-state`` is read (``swarm_out._require_work_orders``, the step
        ``checking Hive <name>``): not answering, a version that cannot take the task, or
        full raise LocationUnavailable (``swarm_out.HiveUnavailable``, its ``why`` one of
        ``unreachable``, ``out_of_date``, ``saturated``), before anything is written to the
        Hive. *deadline*: the dispatch's ``swarm_out._Deadline``."""
        from core import swarm_out
        deadline = deadline or swarm_out._Deadline(swarm_out.DISPATCH_DEADLINE_S)
        held = frozenset(needs.held) if needs else frozenset()
        inst = await self.up(deadline, held)
        hive_id = str(inst.get("id") or self.id)
        if hive_id in held or self.name in held:
            brain = (needs.brain if needs else None) or "this Brain"
            raise swarm_out.SwarmOutError(f"Hive {self.name} holds {brain} for a running Swarm Run at another "
                                          "version; send it to another Hive, or when that run ends")
        try:
            await swarm_out._within(deadline, f"checking Hive {self.name}", 2 * swarm_out.PROBE_TIMEOUT_S,
                                    lambda: swarm_out._require_work_orders(inst))
        except LocationUnavailable as e:
            why = getattr(e, "why", None)
            if why in (SATURATED, OUT_OF_DATE):
                await asyncio.to_thread(_remember, {hive_id: (inst.get("version"), why == SATURATED)})
            raise
        await asyncio.to_thread(_remember, {hive_id: (inst.get("version"), True)})
        logger.info("carriers.ec2: opened round %s of %s on %s", round, dispatch_id, self.name)
        return RunHandle(carrier=EC2, location_id=hive_id, round=int(round), run_ref=None,
                         address=inst.get("cloudfront_domain"), auth_ref=f"hive_instances:{hive_id}",
                         opened_at=time.time())


class _HiveClient:
    """``async with``: today's client to the handle's Hive (``swarm_out._client``: its
    CloudFront domain with Basic auth), the Hive looked up when the block is entered."""

    def __init__(self, hive_id: str, timeout: Optional[float]):
        self._hive_id, self._timeout = hive_id, timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> httpx.AsyncClient:
        from core import swarm_out
        inst = await swarm_out._instance(self._hive_id)
        self._client = swarm_out._client(inst) if self._timeout is None else swarm_out._client(inst, self._timeout)
        return await self._client.__aenter__()

    async def __aexit__(self, *exc) -> Any:
        assert self._client is not None
        return await self._client.__aexit__(*exc)


def _ec2_handle(handle: RunHandle) -> str:
    if handle.carrier != EC2 or not handle.location_id:
        raise ValueError(f"not an EC2 run handle: {handle.carrier!r}")
    return handle.location_id


# -- the carrier -------------------------------------------------------------------------

class Ec2Carrier:
    kind = EC2
    reaches_private = False
    max_run_s: Optional[int] = None
    brain_lock_scope = "machine"
    duties = frozenset({FLEET, IDLE_STOP, USAGE_SYNC, BRAIN_SYNC, DIAGNOSTICS})

    async def offers(self, needs: Needs) -> list[Offer]:
        """One offer per Hive of an account the Host manages (another account's Hives are
        left out entirely, R1.8), placement order left to ``placement.place``:

        - running: tier 1 after a fresh probe; blocked ``unreachable``, ``out_of_date`` or
          ``saturated`` when it cannot take a run (``running_blocked``);
        - stopped (or stopping): tier 3, ``start_s`` = START_TIMEOUT_S; blocked unless its
          last probe while running, at its present version, found it capable
          (``stopped_blocked``);
        - any other status (updating, provisioning, failed): not offered, as today.

        *needs* is placement's to apply (private reach, a named Hive, Brain locks). A named
        location is not looked for among the Hives: its one offer names it as given,
        unprobed, and opening it finds out what it is (``Ec2Location.open_run``), as a
        dispatch to a named Hive always has; only a name another carrier's row holds
        (Elastic Workers) gets no offer here, so that carrier's offer is the one placed."""
        from core import hive_idle, swarm_out
        from hive import accounts
        if needs.named is not None:
            ref = str(needs.named)
            if (await _named_carrier(ref)) not in (None, EC2):
                return []
            return [Offer(EC2, ref, ref, TIER_WARM, in_flight=_in_flight(ref, ref),
                          reaches_private=self.reaches_private, brain_lock_scope=self.brain_lock_scope)]
        mode = await asyncio.to_thread(accounts.swarmai_account)
        rows = [h for h in await _hive_rows() if _manageable(h, mode)]
        running = [h for h in rows if h["status"] == "running"]
        stopped = [h for h in rows if h["status"] in hive_idle.WAKES_FROM]
        probes = await asyncio.gather(*(swarm_out._admission(h["id"]) for h in running))
        await asyncio.to_thread(_remember, {h["id"]: (h["version"], p["capable"] is True)
                                            for h, p in zip(running, probes) if p is not None})
        open_runs = await asyncio.to_thread(swarm_out.open_runs_by_hive) or {}
        kept = await asyncio.to_thread(_read_probes) if stopped else {}
        out: list[Offer] = []
        for h, p in zip(running, probes):
            name = str(h["name"])
            out.append(Offer(EC2, str(h["id"]), name, TIER_WARM, open_runs=open_runs.get(name, 0),
                             in_flight=_in_flight(h["id"], name),
                             streaming=p["streaming"] if p else 0,
                             memory_percent=p["memory_percent"] if p else None,
                             reaches_private=self.reaches_private, brain_lock_scope=self.brain_lock_scope,
                             blocked=running_blocked(p)))
        for h in stopped:
            name = str(h["name"])
            out.append(Offer(EC2, str(h["id"]), name, TIER_COLD, open_runs=open_runs.get(name, 0),
                             in_flight=_in_flight(h["id"], name), start_s=float(swarm_out.START_TIMEOUT_S),
                             reaches_private=self.reaches_private, brain_lock_scope=self.brain_lock_scope,
                             blocked=stopped_blocked(str(h["id"]), h["version"], kept)))
        return out

    def location(self, location_id: str, name: Optional[str] = None) -> Ec2Location:
        return Ec2Location(location_id, name)

    def client(self, handle: RunHandle, timeout: Optional[float] = None) -> _HiveClient:
        """``async with carrier.client(handle) as c``: today's ``_client`` for the Hive
        (its own default timeout when *timeout* is None)."""
        return _HiveClient(_ec2_handle(handle), timeout)

    async def connect(self, handle: RunHandle) -> dict[str, Any]:
        """What a per-run operation reaches the run through (``registry.client_for``): the
        handle's Hive's validated record, looked up once. SwarmOutError when the Hive is
        not running or not usable, as every per-run caller has had it."""
        return await self.location(_ec2_handle(handle)).hive()

    async def running(self) -> list[Ec2Location]:
        """The running Hives, by id: the locations a timer of this carrier visits."""
        from core import swarm_out
        statuses = await swarm_out._hive_statuses()
        return [Ec2Location(h) for h, st in sorted(statuses.items()) if st == "running"]

    async def outputs(self, handle: RunHandle, dispatch_id: str) -> dict[str, Any]:
        """The run's kept result after one check on its Hive (``swarm_out_results.fetch``,
        which takes the run records first, ``hive_records.pull``). Never raises for the
        Hive: a failure is the record's ``fetch_error``."""
        from core import swarm_out, swarm_out_results
        hive_id = _ec2_handle(handle)
        row = await asyncio.to_thread(swarm_out._log_row_of, dispatch_id)
        row = {**row, "hive_id": hive_id, "session_id": handle.run_ref or row.get("session_id")}
        return await swarm_out_results.fetch(row)

    async def state(self, handle: RunHandle) -> tuple[str, str]:
        """(state, reason) of the round's session from its Hive (``swarm_out.run_state``
        over the Hive's status and its streaming-state report). Questions and decisions
        are not read here: a run waiting on one reads as its session reports. A round
        whose session is not named yet (``run_ref`` None) is ``unknown``/``no_session_id``
        while its Hive runs, as a dispatch row without a session is today: whoever learns
        the session writes it into the round's handle."""
        from core import swarm_out
        hive_id = _ec2_handle(handle)
        try:
            status: Optional[str] = (await swarm_out._hive_statuses()).get(hive_id, "gone")
        except Exception as e:  # noqa: BLE001 — store unreadable: the Hive is asked anyway
            logger.warning("carriers.ec2: reading the Hives' statuses failed (%s)", type(e).__name__)
            status = None
        sessions = None
        if status in ("running", None):
            try:
                sessions = await self.location(hive_id).sessions()
            except Exception as e:  # noqa: BLE001 — the Hive did not say: unknown, never a guess
                logger.warning("carriers.ec2: session states of %s failed (%s)", hive_id, type(e).__name__)
        return swarm_out.run_state({"session_id": handle.run_ref}, status, sessions, False)

    async def release(self, handle: RunHandle, dispatch_id: str) -> None:
        """Tell the Hive to remove the task's workspace, as ``swarm_out_settle`` does
        (``POST /api/hive/work/<id>/release``; 404, no such order there, is done too).
        Raises when it was not done (another answer, no answer): the caller tries again."""
        from core import swarm_out, swarm_out_results
        if not isinstance(dispatch_id, str) or not swarm_out_results.DISPATCH_ID_RE.fullmatch(dispatch_id):
            raise ValueError("not a dispatch id")
        async with self.client(handle, swarm_out.PROBE_TIMEOUT_S * 4) as c:
            resp = await c.post(f"/api/hive/work/{dispatch_id}/release")
        if resp.status_code not in (200, 404):
            raise swarm_out.SwarmOutError(f"the Hive did not release {dispatch_id} (HTTP {resp.status_code})")
