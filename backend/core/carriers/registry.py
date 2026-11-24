"""Swarm Out carriers by kind (design docs/design/2026-11-20-swarm-out-multi-carrier.md §4.2).

``carrier(kind)`` is how every per-run operation reaches the carrier of a round's handle
(``carrier(handle.carrier)``); ``carriers()`` is what placement asks for offers. One
instance per kind: carriers hold no per-run state.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from core.carriers.base import AGENTCORE, EC2, SSH, Carrier, RunHandle


def _ec2() -> Carrier:
    from core.carriers.ec2 import Ec2Carrier
    return Ec2Carrier()


def _agentcore() -> Carrier:
    from core.carriers.agentcore import AgentCoreCarrier
    return AgentCoreCarrier()


def _ssh() -> Carrier:
    from core.carriers.ssh import SshCarrier
    return SshCarrier()


#: kind -> how its carrier is made. A carrier joins here when it is built.
_MAKERS: dict[str, Callable[[], Carrier]] = {AGENTCORE: _agentcore, EC2: _ec2, SSH: _ssh}
_made: dict[str, Carrier] = {}


def carrier(kind: str) -> Carrier:
    """The carrier of *kind*; KeyError for a kind this Host has no carrier for."""
    if kind not in _MAKERS:
        raise KeyError(f"no Swarm Out carrier {kind!r}")
    if kind not in _made:
        _made[kind] = _MAKERS[kind]()
    return _made[kind]


def carriers() -> list[Carrier]:
    """Every carrier this Host has, in a fixed order (by kind)."""
    return [carrier(k) for k in sorted(_MAKERS)]


def with_duty(duty: str) -> list[Carrier]:
    """The carriers that have *duty* (``base.DUTIES``), in ``carriers()`` order: a timer or
    machine operation runs for these and skips the others."""
    return [c for c in carriers() if duty in getattr(c, "duties", ())]


async def carrier_kind_of(hive_ref: str) -> str:
    """The carrier kind of the one ``hive_instances`` row with this id or name (``ec2`` for
    a row written before the column, or when there is no single such row: the EC2 location
    then says what is wrong, as every caller has had it; also when the store cannot be read,
    for the same reason)."""
    import aiosqlite
    from core import swarm_out
    try:
        async with aiosqlite.connect(swarm_out._db_path()) as conn:
            await conn.execute("PRAGMA busy_timeout = 3000")
            cur = await conn.execute(
                "SELECT COALESCE(carrier, 'ec2') FROM hive_instances WHERE id = ? OR name = ?",
                (hive_ref, hive_ref))
            rows = await cur.fetchall()
    except Exception as exc:  # noqa: BLE001 — unreadable: the EC2 lookup reports it, as before
        import logging
        logging.getLogger(__name__).info("registry: carrier of %s unreadable (%s)", str(hive_ref)[:40],
                                         type(exc).__name__)
        return EC2
    return str(rows[0][0]) if len(rows) == 1 else EC2


async def location_of(hive_ref: str, duty: Optional[str] = None) -> Any:
    """The location of the machine with this id or name, through the carrier its row names
    (design docs/design/2026-11-24-swarm-out-ssh-machine.md §6): what every per-machine
    caller uses instead of assuming EC2. With *duty*, a SwarmOutError when that carrier does
    not have it (an elastic row keeps no Brains, an SSH machine is not stopped by the Host).
    A kind this Host has no carrier for is refused the same way."""
    from core.swarm_out import SwarmOutError
    kind = await carrier_kind_of(str(hive_ref))
    if kind not in _MAKERS:
        raise SwarmOutError(f"Hive {hive_ref} is of a kind this Host cannot reach ({kind})")
    car = carrier(kind)
    if duty is not None and duty not in getattr(car, "duties", ()):
        raise SwarmOutError(f"Hive {hive_ref} ({kind}) does not do this ({duty})")
    return car.location(str(hive_ref))


def handle_of(row: dict[str, Any]) -> RunHandle:
    """The current round's handle of a dispatch row (``base.current_handle``), its run
    named by the row's session when the handle does not name its chat session yet
    (``RunHandle.session``: a row whose session was found after the dispatch,
    ``swarm_out._log_row_of``). SwarmOutError for a round of a carrier this Host has not,
    or one naming no location."""
    from core.carriers.base import current_handle
    from core.swarm_out import SwarmOutError
    h = current_handle(row)
    if h.carrier not in _MAKERS or not h.location_id:
        # a round of a carrier this Host has not, or naming no location: refused as a run
        # whose machine cannot be reached, as every per-run caller handles it
        raise SwarmOutError(f"this run's round ({h.carrier}) names nothing this Host can reach")
    if h.session is None and isinstance(row.get("session_id"), str) and row["session_id"]:
        h = h.with_session(row["session_id"])
    return h


def client_for(target: Any, timeout: Optional[float] = None) -> Any:
    """A client to what *target* names: a RunHandle through its carrier
    (``Carrier.client``: ``async with`` only), or what a carrier's ``connect`` gave for a
    run (the EC2 carrier: the Hive's record; the AgentCore carrier: a record naming its
    round, ``_handle``, whose client is the round's ``RunClient``; an
    ``httpx.AsyncClient``, ``async with`` or closed by the caller). Every per-run caller
    outside ``core/carriers`` reaches a run through this, never by looking its machine up
    itself."""
    if isinstance(target, RunHandle):
        return carrier(target.carrier).client(target, timeout)
    round_of = target.get("_handle") if isinstance(target, dict) else None
    if isinstance(round_of, RunHandle):
        return carrier(round_of.carrier).client(round_of, timeout)
    from core import swarm_out
    # the record's client; called as the module has it so a replaced one is used
    return swarm_out._client(target) if timeout is None else swarm_out._client(target, timeout)
