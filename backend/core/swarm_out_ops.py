"""swarm_out_ops — every Swarm Out and Hive-management operation, and who may call it.

Design: docs/design/2026-10-31-swarm-out-ops-registry.md §5 (phase 1). This is data,
not a framework: one row per HTTP operation of ``/api/swarm-out`` and ``/api/hive``,
plus one per Host-proxy route (generated from ``core.swarm_out_remote.ROUTES``).

Readers:
- ``core.run_owner_gate`` denies an agent's Bash command that names the path of an
  operation the owner's agent may not call (``person_only_match``). The regexes are
  generated here, so a person-only route cannot be left out of the gate by hand.
- ``tests/test_swarm_out_ops.py``: every route the app serves under those prefixes is
  in this table (a new route without a row turns it red), every person-only path is
  denied in its encoded / ``..`` / quote-split spellings, the owner agent's reads are
  not, and the ``scripts.swarm_out`` CLI calls only operations the owner agent may call.

What this is NOT: a boundary. The gate reads command text, and the agent and the
daemon share an OS user (design §2.4: the Hive's password and the account's AWS keys
are readable, so a determined agent need not call the Host at all). "Person-only" here
is a speed bump until the person-signature phase (design §6).

Callers: ``person`` (the desktop App) · ``owner_agent`` (the owner's agent through its
Bash) · ``internal:<name>`` (backend code; ``internal:host`` = the Host's daemon calling
a Hive over the network). A non-owner channel agent is never a caller: it is refused
any command naming Swarm Out (``security_hooks.owner_command_denial``).

The gate matches PATHS, not methods (a curl's method is not reliably readable from the
text), so a path shared by a person-only op and an owner-agent op would block both; the
test keeps such paths from existing.

Public: ``Op``, ``ops``, ``WILDCARD_ROUTES``, ``match``, ``person_only_match``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

PERSON = "person"
OWNER = "owner_agent"
CONDUCTOR = "internal:conductor"
HOST = "internal:host"


@dataclass(frozen=True)
class Op:
    name: str
    method: str
    path: str            # FastAPI template, e.g. "/api/swarm-out/runs/{dispatch_id}/discard"
    callers: frozenset
    weight: str          # read | change | irreversible | off_machine


def _op(name: str, method: str, path: str, callers: "tuple[str, ...]", weight: str) -> Op:
    return Op(name, method, path, frozenset(callers), weight)


_SO = "/api/swarm-out"
_HV = "/api/hive"
_P = (PERSON,)
_PO = (PERSON, OWNER)
_POC = (PERSON, OWNER, CONDUCTOR)

_STATIC: "tuple[Op, ...]" = (
    # ── dispatch (routers/swarm_out.py) ──
    _op("hive.list", "GET", f"{_SO}/hives", _PO, "read"),
    _op("hive.fleet", "GET", f"{_SO}/fleet", _PO, "read"),
    _op("run.list", "GET", f"{_SO}/runs", _PO, "read"),
    _op("run.result", "GET", f"{_SO}/runs/{{dispatch_id}}/result", _PO, "read"),
    _op("run.refresh", "POST", f"{_SO}/runs/{{dispatch_id}}/refresh", _PO, "read"),
    _op("task.route", "POST", f"{_SO}/route", _POC, "read"),
    _op("task.spec", "POST", f"{_SO}/spec", _POC, "read"),
    _op("task.dispatch", "POST", f"{_SO}/dispatch", _POC, "off_machine"),
    # ── the person's commands on a run (run-review §1.1) ──
    _op("run.accept", "POST", f"{_SO}/runs/{{dispatch_id}}/verdict", _P, "irreversible"),
    _op("run.manual_landed", "POST", f"{_SO}/runs/{{dispatch_id}}/manual-landed", _P, "irreversible"),
    _op("run.return_prepare", "POST", f"{_SO}/runs/{{dispatch_id}}/return/prepare", _P, "change"),
    _op("run.return_land", "POST", f"{_SO}/runs/{{dispatch_id}}/return/land", _P, "irreversible"),
    _op("run.rerun", "POST", f"{_SO}/runs/{{dispatch_id}}/rerun", _P, "change"),
    _op("run.stop", "POST", f"{_SO}/runs/{{dispatch_id}}/stop", _P, "change"),
    _op("run.discard", "POST", f"{_SO}/runs/{{dispatch_id}}/discard", _P, "irreversible"),
    # approvals and question answers share one route; the gate cannot read the body,
    # so the whole route is the person's (a Conductor answering a question will be an
    # internal call, swarm-run §5.8)
    _op("ask.answer", "POST", f"{_SO}/asks/{{hive_id}}/{{session_id}}/{{request_id}}/answer",
        _P, "off_machine"),
    # ── the Host proxy to a run's Hive session (routers/swarm_out_remote.py) ──
    _op("remote.relay_open", "POST", f"{_SO}/remote/{{sid}}/relay", _P, "change"),
    _op("remote.relay_close", "DELETE", f"{_SO}/remote/{{sid}}/relay", _P, "change"),
    # ── Hive management (routers/hive.py) ──
    # what costs money, deletes, hands over a password or sends code off this machine
    # is the person's; reads and keeping a Hive up to date are the owner agent's too
    _op("hive.account.list", "GET", f"{_HV}/accounts", _P, "read"),
    _op("hive.account.create", "POST", f"{_HV}/accounts", _P, "off_machine"),
    _op("hive.account.delete", "DELETE", f"{_HV}/accounts/{{account_id}}", _P, "irreversible"),
    _op("hive.account.verify", "POST", f"{_HV}/accounts/{{account_id}}/verify", _P, "off_machine"),
    _op("hive.instance.list", "GET", f"{_HV}/instances", _P, "read"),
    _op("hive.create", "POST", f"{_HV}/instances", _P, "off_machine"),
    _op("hive.instance.get", "GET", f"{_HV}/instances/{{instance_id}}", _P, "read"),
    _op("hive.delete", "DELETE", f"{_HV}/instances/{{instance_id}}", _P, "irreversible"),
    _op("hive.stop", "POST", f"{_HV}/instances/{{instance_id}}/stop", _P, "change"),
    _op("hive.start", "POST", f"{_HV}/instances/{{instance_id}}/start", _P, "off_machine"),
    _op("hive.latest_version", "GET", f"{_HV}/latest-version", _PO, "read"),
    _op("hive.update", "POST", f"{_HV}/instances/{{instance_id}}/update", _PO, "change"),
    _op("hive.reset_password", "POST", f"{_HV}/instances/{{instance_id}}/reset-password", _P,
        "irreversible"),
    # the Hive's login: whoever holds it can act as the person on the Hive (§2.4 ③)
    _op("hive.credentials", "GET", f"{_HV}/instances/{{instance_id}}/credentials", _P,
        "off_machine"),
    _op("hive.retry", "POST", f"{_HV}/instances/{{instance_id}}/retry", _P, "off_machine"),
    _op("hive.brain.status", "GET", f"{_HV}/instances/{{instance_id}}/brains", _PO, "read"),
    _op("hive.brain.sync_defaults", "POST", f"{_HV}/instances/{{instance_id}}/brains/sync-defaults",
        _P, "off_machine"),
    _op("hive.brain.sync", "POST", f"{_HV}/instances/{{instance_id}}/brains/{{brain}}/sync", _P,
        "off_machine"),
    # ── served by a Hive, called by the Host's daemon (routers/hive_brains.py; 404 on a
    # desktop). No agent calls them, on either side. ──
    _op("hive.serve.brain_install", "PUT", f"{_HV}/brains/{{brain}}", (HOST,), "change"),
    _op("hive.serve.brain_list", "GET", f"{_HV}/brains", (HOST,), "read"),
    _op("hive.serve.repo", "PUT", f"{_HV}/repos", (HOST,), "change"),
    _op("hive.serve.work", "POST", f"{_HV}/work", (HOST,), "change"),
    _op("hive.serve.work_release", "POST", f"{_HV}/work/{{work_id}}/release", (HOST,), "irreversible"),
    _op("hive.serve.bundle", "GET", f"{_HV}/bundles/{{session_id}}/{{key}}", (HOST,), "read"),
    _op("hive.serve.usage", "GET", f"{_HV}/usage", (HOST,), "read"),
    _op("hive.serve.records", "GET", f"{_HV}/records/{{session_id}}", (HOST,), "read"),
    _op("hive.serve.record", "GET", f"{_HV}/records/{{session_id}}/{{seq}}", (HOST,), "read"),
    _op("hive.serve.lessons", "GET", f"{_HV}/lessons/{{session_id}}", (HOST,), "read"),
)

#: app routes that stand for many ops (the proxy's catch-all forwards exactly ROUTES)
WILDCARD_ROUTES = {f"{_SO}/remote/{{sid}}/{{path:path}}": "remote."}


def _remote_ops() -> "tuple[Op, ...]":
    """One op per forwarded proxy route. Writing into a Hive session (sending, answering
    for it, stopping, compacting) is the person's (run-review §1.1); reading it is the
    owner agent's too (the run's result already hands the agent the Hive's text)."""
    from core.swarm_out_remote import ROUTES
    out = []
    for r in ROUTES:
        kind = "read" if r.method == "GET" else "write"
        name = "remote." + kind + "." + re.sub(r"[{}]", "", r.template).replace("/", ".")
        out.append(_op(name, r.method, f"{_SO}/remote/{{sid}}/{r.template}",
                       _PO if kind == "read" else _P, "read" if kind == "read" else "change"))
    return tuple(out)


@lru_cache(maxsize=1)
def ops() -> "tuple[Op, ...]":
    """Every op: the rows above plus one per proxy route."""
    return _STATIC + _remote_ops()


# possessive: a parameter takes its whole segment, so a shorter person-only path cannot
# match inside a longer owner path by stopping early (``…/instances/${IID}/brains`` is not
# ``…/instances/{instance_id}``)
_PARAM = r"[^\s/'\"?#]++"
# where a path ends: right after its last segment, at any character that cannot continue
# that segment (``…/stop,`` ``…/stop.``); after trailing slashes (the server redirects
# ``…/stop//`` to ``…/stop``) only at the end of the word, so ``…/instances/${ID}/…``
# is not ``…/instances``
_END = r"(?:/+(?=$|[\s'\"?#;&|)<>`\\])|(?![\w/%-]))"


def _template_re(template: str) -> str:
    parts = re.split(r"(\{[^}]+\})", template)
    return "".join(_PARAM if p.startswith("{") else re.escape(p) for p in parts) + _END


def _segments_re(template: str) -> "re.Pattern[str]":
    return re.compile("^" + "".join(
        "[^/]+" if p.startswith("{") else re.escape(p)
        for p in re.split(r"(\{[^}]+\})", template)) + "$")


@lru_cache(maxsize=1)
def _person_only() -> "tuple[tuple[Op, re.Pattern[str]], ...]":
    return tuple((op, re.compile(_template_re(op.path)))
                 for op in ops() if OWNER not in op.callers)


def person_only_match(text: str) -> "Op | None":
    """The first op the owner agent may not call whose path appears in *text*
    (the caller passes the command already decoded / de-quoted), else None."""
    for op, rx in _person_only():
        if rx.search(text):
            return op
    return None


def match(method: str, path: str) -> "Op | None":
    """The op for a concrete request (``path`` without the query), else None."""
    for op in ops():
        if op.method == method and _segments_re(op.path).match(path):
            return op
    return None
