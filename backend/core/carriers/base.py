"""The Swarm Out dispatch layer's shared contract: carriers, locations, run handles, offers
and placements (design docs/design/2026-11-20-swarm-out-multi-carrier.md §4).

Four concepts:

- **Carrier**: one kind of executor (``ec2``: the person's Hive EC2 instances; ``agentcore``:
  one AgentCore Runtime microVM per run; ``ssh``: a machine of the person's own, reached over
  an SSH tunnel). Its three capability fields each have a reader:
  ``reaches_private`` (placement filter), ``max_run_s`` (the run state's "carrier expired"),
  ``brain_lock_scope`` (placement filter for the Swarm Run Brain-version lock).
- **Location**: a place a carrier can open a run: one Hive for ``ec2``, the account's runtime
  for ``agentcore``. Machine-level operations (probe, Brain sync, idle stop, usage sync,
  diagnostics, Swarm Out › settings) go through it.
- **RunHandle**: the address of one round of one run. It is created by
  ``Location.open_run`` and kept in the dispatch row's ``rounds``; every per-run operation
  resolves it from there. It carries no credential (``auth_ref`` only names where one is).
- **Offer / Placement**: what a carrier offers now, and the deterministic choice among offers
  (``core.carriers.placement``).

Only modules under ``core/carriers`` may look a Hive up by id (``swarm_out._instance``);
``tests/test_carriers_no_instance_outside.py`` keeps it so, because a request to an AgentCore
session that already ended boots a new empty microVM.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable

EC2 = "ec2"
AGENTCORE = "agentcore"
#: a machine of the person's own, reached over an SSH tunnel (core/carriers/ssh; design
#: docs/design/2026-11-24-swarm-out-ssh-machine.md)
SSH = "ssh"
KINDS = (EC2, AGENTCORE, SSH)

#: offer tiers, in placement order (design §4.4 step 2)
TIER_WARM = 1        # a running EC2 Hive with room: starts now, already paid for
TIER_ELASTIC = 2     # Elastic Workers (AgentCore): cold start plus transfer
TIER_COLD = 3        # a stopped EC2 Hive: up to START_TIMEOUT_S to start, then runs to idle stop

#: placement reasons the run card words (core/swarm_out_card, the desktop card)
REASONS = ("named", "only_free", "idle", "least_busy", "elastic", "started")

#: the machine-level duties a carrier may have (design §4.5). A carrier lists the ones it
#: has in ``duties`` (an optional attribute: none when absent); every timer and machine
#: operation asks ``core.carriers.registry.with_duty`` and skips a carrier without it. A
#: carrier with a per-location timer duty (``usage_sync``) lists the locations it visits
#: with ``async running() -> list[Location]``.
FLEET = "fleet"                # its locations are probed for the panel and for placement
IDLE_STOP = "idle_stop"        # an idle location is stopped (core/hive_idle)
USAGE_SYNC = "usage_sync"      # usage is pulled per location (core/usage_hive_sync)
BRAIN_SYNC = "brain_sync"      # a location keeps Brains between runs (core/brain_sync_host)
DIAGNOSTICS = "diagnostics"    # a run's machine logs can be read (core/hive_diagnostics)
#: an idle location behind the Host's version is updated by its own carrier
#: (``async follow_host() -> list[str]``, called by core/hive_idle). The EC2 carrier keeps
#: doing this under ``idle_stop``, as before; only another carrier lists this duty.
FOLLOW_HOST = "follow_host"
DUTIES = (FLEET, IDLE_STOP, USAGE_SYNC, BRAIN_SYNC, DIAGNOSTICS, FOLLOW_HOST)


class LocationUnavailable(Exception):
    """A location found unusable BEFORE anything was written to it (it is full, it did not
    answer, its version cannot take the task, the elastic runtime was throttled or is at its
    quota). The only failure after which a dispatch tries the next candidate (design §4.4
    step 3). Any other exception from ``open_run`` or later ends the dispatch.

    ``placement``: set by ``placement.open_with_failover`` when no candidate could take the
    task, the Placement with ``tried`` filled, for the refusal record; None otherwise."""
    placement: Optional["Placement"] = None


@dataclass(frozen=True)
class Offer:
    """One place a run could go now, or (``blocked`` set) one that cannot and why.
    ``location_id``: ``hive_instances.id``."""
    carrier: str
    location_id: str
    name: str
    tier: int
    open_runs: int = 0
    in_flight: int = 0
    streaming: int = 0
    memory_percent: Optional[float] = None
    start_s: float = 0.0
    reaches_private: bool = False
    brain_lock_scope: str = "machine"     # "machine" | "run"
    #: why the location cannot take a run now, or None. Carriers report unusable locations
    #: too ("saturated", "out_of_date", "unreachable", "at_quota") so that placement alone
    #: decides, and records each in Placement.skipped. D5 reads it: a stopped Hive is a
    #: candidate only when every blocked running Hive is "saturated".
    blocked: Optional[str] = None

    def load_key(self) -> tuple:
        """The within-tier order: busier last, then name (design §4.4 step 2)."""
        mem = self.memory_percent if isinstance(self.memory_percent, (int, float)) else 101.0
        if self.tier == TIER_COLD:
            return (self.open_runs + self.in_flight, self.name)
        return (self.open_runs + self.in_flight, self.streaming, mem, self.name)


@dataclass(frozen=True)
class Needs:
    """What placement must respect for one task."""
    private: tuple[str, ...] = ()               # private-network needs (task_spec); () = none
    named: Optional[str] = None                 # a location named by the person or agent
    held: frozenset = frozenset()               # ids or names a Swarm Run holds at another Brain version
    brain: Optional[str] = None                 # the Brain the task goes under (what ``held`` holds)


@dataclass(frozen=True)
class Placement:
    """The result of ``place``: the chosen offer (None: nothing can take the task), the rest
    of the candidates in order (tried after a LocationUnavailable), and every offer left out
    with its reason. ``tried`` is filled by ``placement.open_with_failover``."""
    offer: Optional[Offer]
    layer: Optional[int]
    reason: str
    candidates: tuple[Offer, ...] = ()
    skipped: tuple[tuple[str, str], ...] = ()
    tried: tuple[tuple[str, str], ...] = ()

    def record(self) -> dict[str, Any]:
        """What the dispatch row and a refusal record keep (no credential)."""
        return {"location": self.offer.name if self.offer else None, "layer": self.layer,
                "reason": self.reason, "tried": [list(t) for t in self.tried],
                "skipped": [list(s) for s in self.skipped]}


@dataclass(frozen=True)
class RunHandle:
    """The address of one round of a run. Serialised into the dispatch row's ``rounds``."""
    carrier: str
    location_id: str
    round: int
    run_ref: Optional[str]          # ec2: the Hive's session id; agentcore: the runtime session id
    #                                 (the chat session of either: ``session``)
    address: Optional[str] = None   # ec2: the CloudFront domain; agentcore: the session endpoint URL
    auth_ref: Optional[str] = None  # ec2: "hive_instances:<id>"; agentcore: "run_cred:<dispatch>:<round>"
    opened_at: Optional[float] = None
    extra: dict = field(default_factory=dict)

    @property
    def session(self) -> Optional[str]:
        """The round's chat session on the backend it reaches (what the dispatch row keeps
        in ``session_id``). A carrier that names the run itself (agentcore: ``run_ref`` is
        the runtime session) keeps it in ``extra["session_id"]``, None until the backend
        names it; otherwise (ec2) the run is named by its chat session: ``run_ref``."""
        if "session_id" in (self.extra or {}):
            got = self.extra["session_id"]
            return got if isinstance(got, str) and got else None
        return self.run_ref

    def with_session(self, session_id: Optional[str]) -> "RunHandle":
        """This handle with its chat session named (``session``)."""
        if "session_id" in (self.extra or {}):
            return dataclasses.replace(self, extra={**self.extra, "session_id": session_id})
        return dataclasses.replace(self, run_ref=session_id)

    def to_row(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "RunHandle":
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in row.items() if k in known})


@runtime_checkable
class Location(Protocol):
    """A place a carrier can open runs (one Hive, or the elastic runtime)."""
    id: str
    name: str
    carrier: str

    async def probe(self) -> dict[str, Any]: ...

    async def open_run(self, dispatch_id: str, round: int, deadline: Any,
                       needs: Optional[Needs] = None) -> RunHandle:
        """Make the location ready for a run and return its handle. Raises
        LocationUnavailable only before any write to the location. *needs*: the task's,
        for what the location itself must refuse (a Brain held there, by machine)."""
        ...


@runtime_checkable
class Carrier(Protocol):
    """One kind of executor. Per-run operations take the round's handle.

    Optional members, read with ``getattr`` by the callers:

    - ``async connect(handle)``: what the post-open steps and the per-run readers reach
      the round through (``registry.client_for`` takes it): ec2, the Hive's record;
      agentcore, a small record ``{id, name, carrier, _handle}``.
    - ``async next_round(handle, dispatch_id, deadline=None) -> RunHandle``: where a
      correction goes: *handle* itself while its round can take it, else a new round the
      carrier opened (the caller appends it to ``rounds`` once it took the correction).
      A carrier without it sends every correction into the current round.
    - ``run_scoped``: True when every round is a machine of its own (agentcore): its
      state and results are read per round from the carrier (``state``, ``outputs``),
      never from a Hive's session list, and a dispatch that fails after ``open_run``
      releases the round."""
    kind: str
    reaches_private: bool
    max_run_s: Optional[int]
    brain_lock_scope: str

    async def offers(self, needs: Needs) -> list[Offer]: ...

    def location(self, location_id: str) -> Location: ...

    def client(self, handle: RunHandle, timeout: float) -> Any:
        """An httpx.AsyncClient bound to the run (ec2: CloudFront + Basic; agentcore: SigV4 +
        X-Swarm-Hive-Auth). Raises before any request when the run has ended (agentcore)."""
        ...

    async def outputs(self, handle: RunHandle, dispatch_id: str) -> dict[str, Any]: ...

    async def state(self, handle: RunHandle) -> tuple[str, str]: ...

    async def release(self, handle: RunHandle, dispatch_id: str) -> None: ...


def handle_from_legacy_row(row: dict[str, Any]) -> RunHandle:
    """A dispatch row written before ``rounds`` existed: its one EC2 round (design §4.3)."""
    return RunHandle(carrier=EC2, location_id=str(row.get("hive_id") or ""), round=1,
                     run_ref=row.get("session_id"), auth_ref=f"hive_instances:{row.get('hive_id')}")


def rounds_of(row: dict[str, Any]) -> list[RunHandle]:
    """Every round of a dispatch row, oldest first."""
    raw = row.get("rounds")
    got = [RunHandle.from_row(r) for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []
    return got or [handle_from_legacy_row(row)]


def current_handle(row: dict[str, Any]) -> RunHandle:
    return rounds_of(row)[-1]
