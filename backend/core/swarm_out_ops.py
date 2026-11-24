"""swarm_out_ops — every Swarm Out and Hive-management operation, and who may call it.

Design: docs/design/2026-10-31-swarm-out-ops-registry.md §5 (phase 1). This is data,
not a framework: one row per HTTP operation of ``/api/swarm-out``, ``/api/hive`` and
``/api/aws-account``,
plus one per Host-proxy route (generated from ``core.swarm_out_remote.ROUTES``).

Readers:
- ``core.run_owner_gate`` denies an agent's Bash command that names the path of an
  operation the owner's agent may not call (``person_only_match``). The regexes are
  generated here, so a person-only route cannot be left out of the gate by hand.
- ``tests/test_swarm_out_ops.py``: every route the app serves under those prefixes is
  in this table (a new route without a row turns it red), every person-only path is
  denied in its encoded / ``..`` / quote-split spellings, the owner agent's reads are
  not, and the two CLIs an agent calls these routes through, ``scripts.swarm_out`` and
  the ``pipeline-v1 child`` commands (``core.run_protocol.child_cli``), call only
  operations the owner agent may call and refuse a non-owner channel session.

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
_AA = "/api/aws-account"
#: the ``python-backend aws-credentials`` subcommand a ``credential_process`` runs
CREDENTIAL_PROCESS = "internal:credential_process"
_P = (PERSON,)
_PO = (PERSON, OWNER)
_POC = (PERSON, OWNER, CONDUCTOR)

_STATIC: "tuple[Op, ...]" = (
    # ── dispatch (routers/swarm_out.py) ──
    _op("hive.list", "GET", f"{_SO}/hives", _PO, "read"),
    # one round of Elastic Workers' standby now (turn on, update, repair): never past the
    # person's turn-off, which only their turn-on in Swarm Out › settings undoes
    _op("hive.elastic.standby_round", "POST", f"{_SO}/elastic/standby", _PO, "off_machine"),
    _op("hive.fleet", "GET", f"{_SO}/fleet", _PO, "read"),
    _op("run.list", "GET", f"{_SO}/runs", _PO, "read"),
    _op("run.result", "GET", f"{_SO}/runs/{{dispatch_id}}/result", _PO, "read"),
    _op("run.refresh", "POST", f"{_SO}/runs/{{dispatch_id}}/refresh", _PO, "read"),
    # why its Hive session stopped, read over SSM with a fixed script (core/hive_diagnostics)
    _op("run.diagnostics", "GET", f"{_SO}/runs/{{dispatch_id}}/diagnostics", _PO, "read"),
    # waits until a followed run changes (core/swarm_out_follow; agent-owns-its-runs §2.3)
    _op("run.watch", "POST", f"{_SO}/watch", _PO, "read"),
    # a Swarm Run's card (core/swarm_run.view), and the person's stop of the whole run:
    # every child still running and the lead run's turn (design 2026-10-31-swarm-run §5.9)
    _op("swarm_run.view", "GET", f"{_SO}/swarm-runs/{{run_id}}", _PO, "read"),
    _op("swarm_run.stop", "POST", f"{_SO}/swarm-runs/{{run_id}}/stop", _P, "change"),
    # the chat card for one of a run's waits (core/swarm_out_wait_card): it writes only
    # this machine's record of the card; the person's pick is forwarded by the daemon
    _op("ask.card", "POST", f"{_SO}/runs/{{dispatch_id}}/card", _PO, "change"),
    # one wait whole, for the agent that answers it (it writes only this machine's record
    # of what was shown: the answer is bound to it)
    _op("ask.view", "GET", f"{_SO}/runs/{{dispatch_id}}/waits/{{request_id}}", _PO, "change"),
    _op("task.route", "POST", f"{_SO}/route", _POC, "read"),
    _op("task.spec", "POST", f"{_SO}/spec", _POC, "read"),
    _op("task.dispatch", "POST", f"{_SO}/dispatch", _POC, "off_machine"),
    # several dispatches at once (``dispatch-many``), each item what /dispatch takes
    _op("task.dispatch_batch", "POST", f"{_SO}/dispatch/batch", _POC, "off_machine"),
    # ── the commands on a run (run-review §1.1): the person's, and the dispatching agent's
    # (design 2026-10-31-swarm-out-agent-owns-its-runs §2.1): every run was dispatched by
    # an agent, which answers for it. What an agent may not do with them (land protected
    # paths, discard while something would be lost, re-send more than twice) the routes
    # refuse for ``by: agent`` (core/swarm_out_actor) ──
    _op("run.accept", "POST", f"{_SO}/runs/{{dispatch_id}}/verdict", _PO, "irreversible"),
    # "landed by hand" settles the run and releases its workspace with any commits not
    # landed yet: the person's (an agent lands commits with return/land)
    _op("run.manual_landed", "POST", f"{_SO}/runs/{{dispatch_id}}/manual-landed", _P, "irreversible"),
    _op("run.return_prepare", "POST", f"{_SO}/runs/{{dispatch_id}}/return/prepare", _PO, "change"),
    # both versions of one changed file the prepared review lists, from git's objects
    # here (core/hive_return.file_versions): nothing written, nothing sent
    # the returned file for the person's diff view; an agent reviews the change with git in
    # this machine's checkout, so Hive-written file text reaches no agent through it
    _op("run.return_file", "GET", f"{_SO}/runs/{{dispatch_id}}/return/file", _P, "read"),
    _op("run.return_land", "POST", f"{_SO}/runs/{{dispatch_id}}/return/land", _PO, "irreversible"),
    _op("run.rerun", "POST", f"{_SO}/runs/{{dispatch_id}}/rerun", _PO, "change"),
    _op("run.stop", "POST", f"{_SO}/runs/{{dispatch_id}}/stop", _PO, "change"),
    _op("run.discard", "POST", f"{_SO}/runs/{{dispatch_id}}/discard", _PO, "irreversible"),
    # a run held back only by returned files that never came: settled without them, a
    # recorded decision (it releases the Hive workspace, and the files there with it)
    _op("run.settle_without_files", "POST", f"{_SO}/runs/{{dispatch_id}}/settle-without-files", _PO,
        "irreversible"),
    # a session of the same person takes over a run whose owner session is gone; the
    # person's New session in the Swarm Out tab hands that tab's open runs over
    _op("run.adopt", "POST", f"{_SO}/runs/{{dispatch_id}}/adopt", (OWNER,), "change"),
    _op("session.hand_over", "POST", f"{_SO}/sessions/hand-over", _P, "change"),
    # the funnel's last gate (design 2026-11-02-hive-run-decision-record §1): the
    # dispatching agent hands a landed result it cannot judge to the person, and reports
    # the revert the person asked for; keeping or reverting is the person's
    _op("run.ask_person", "POST", f"{_SO}/runs/{{dispatch_id}}/ask-person", (OWNER,), "change"),
    _op("run.ask_answer", "POST", f"{_SO}/runs/{{dispatch_id}}/ask-person/answer", _P, "change"),
    _op("run.reverted", "POST", f"{_SO}/runs/{{dispatch_id}}/reverted", (OWNER,), "change"),
    # approvals and question answers share one route; the gate cannot read the body,
    # so the whole route is the person's (the dispatching agent answers through
    # ask.answer_host below, never this route)
    _op("ask.answer", "POST", f"{_SO}/asks/{{hive_id}}/{{session_id}}/{{request_id}}/answer",
        _P, "off_machine"),
    # the dispatching agent's own answer to a wait whose class it answers for, and its
    # hand-over of a wait to the person (design 2026-10-31-hive-approvals §2.3): the
    # session the route checks comes from the CLI, so an agent gives them only through it
    _op("ask.answer_host", "POST", f"{_SO}/runs/{{dispatch_id}}/waits/{{request_id}}/answer-host",
        (OWNER,), "off_machine"),
    _op("ask.escalate", "POST", f"{_SO}/runs/{{dispatch_id}}/waits/{{request_id}}/escalate",
        (OWNER,), "change"),
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
    # one Hive's own idle stop (always on, N minutes or the default): a setting, it starts
    # or stops nothing; the owner agent sets it too, through idle-stop (the person asks
    # their agent to keep a Hive on)
    _op("hive.idle_stop", "PUT", f"{_HV}/instances/{{instance_id}}/idle-stop", _PO, "change"),
    # restarting a running Hive nothing uses (none of this Host's open runs, no session
    # streaming: the route checks both) is the owner agent's too, through restart-hive
    _op("hive.restart", "POST", f"{_HV}/instances/{{instance_id}}/restart", _PO, "change"),
    # starting a stopped Hive resumes a machine the person already has: the dispatching
    # agent may, when no Hive runs (zero-touch §1); creating, deleting, replacing one is
    # the person's
    _op("hive.start", "POST", f"{_HV}/instances/{{instance_id}}/start", _PO, "off_machine"),
    _op("hive.latest_version", "GET", f"{_HV}/latest-version", _PO, "read"),
    _op("hive.update", "POST", f"{_HV}/instances/{{instance_id}}/update", _PO, "change"),
    _op("hive.reset_password", "POST", f"{_HV}/instances/{{instance_id}}/reset-password", _P,
        "irreversible"),
    # the Hive's login: whoever holds it can act as the person on the Hive (§2.4 ③)
    _op("hive.credentials", "GET", f"{_HV}/instances/{{instance_id}}/credentials", _P,
        "off_machine"),
    _op("hive.retry", "POST", f"{_HV}/instances/{{instance_id}}/retry", _P, "off_machine"),
    # a new Hive first, then the old one is deleted (§7.3: the last one is only replaced)
    _op("hive.replace", "POST", f"{_HV}/instances/{{instance_id}}/replace", _P, "irreversible"),
    _op("hive.brain.status", "GET", f"{_HV}/instances/{{instance_id}}/brains", _PO, "read"),
    _op("hive.brain.sync_defaults", "POST", f"{_HV}/instances/{{instance_id}}/brains/sync-defaults",
        _P, "off_machine"),
    _op("hive.brain.sync", "POST", f"{_HV}/instances/{{instance_id}}/brains/{{brain}}/sync", _P,
        "off_machine"),
    # Elastic Workers (hive/agentcore_provisioner; design 2026-11-20-swarm-out-multi-carrier
    # §6.8): the same callers as a Hive's list, create, update and delete. The Host keeps it
    # on standby by itself; an agent reads it in hive.list (core/swarm_out.hives: its row and
    # the standby's last result), since this path is also the person's turn-on
    _op("hive.elastic.status", "GET", f"{_HV}/elastic", _P, "read"),
    _op("hive.elastic.enable", "POST", f"{_HV}/elastic", _P, "off_machine"),
    _op("hive.elastic.update", "POST", f"{_HV}/elastic/{{location_id}}/update", _PO, "change"),
    _op("hive.elastic.disable", "DELETE", f"{_HV}/elastic/{{location_id}}", _P, "irreversible"),
    # the person's Elastic Workers switch and account choice (Swarm Out › settings)
    _op("hive.elastic.standby", "PUT", f"{_HV}/elastic/standby", _P, "off_machine"),
    # SSH machines (hive/ssh_provisioner; design 2026-11-24-swarm-out-ssh-machine §5.3):
    # adding one installs software on the person's machine and creates a role in the
    # account, and its settings decide what runs with the person's identity there, so these
    # are the person's, as Elastic Workers' are. An agent reads the machines in hive.list
    # (core/swarm_out.hives lists every row with its carrier); a machine follows the Host's
    # version by itself (core/carriers/ssh.follow_host), so an agent needs no update route
    _op("hive.ssh.list", "GET", f"{_HV}/ssh", _P, "read"),
    _op("hive.ssh.add", "POST", f"{_HV}/ssh", _P, "off_machine"),
    _op("hive.ssh.retry", "POST", f"{_HV}/ssh/{{machine_id}}/retry", _P, "off_machine"),
    _op("hive.ssh.update", "POST", f"{_HV}/ssh/{{machine_id}}/update", _P, "change"),
    _op("hive.ssh.options", "PATCH", f"{_HV}/ssh/{{machine_id}}", _P, "change"),
    _op("hive.ssh.remove", "DELETE", f"{_HV}/ssh/{{machine_id}}", _P, "irreversible"),
    # ── the SwarmAI AWS account (routers/aws_account.py; design
    # 2026-10-31-swarmai-aws-account §7.7): reads for the person and the agent; signing
    # in, connecting, keys and creating Hives for the person only; the credentials route
    # answers only the per-boot token the credential_process subcommand reads. ──
    _op("aws_account.get", "GET", _AA, _PO, "read"),
    _op("aws_account.verify", "POST", f"{_AA}/verify", _PO, "read"),
    _op("aws_account.check", "POST", f"{_AA}/check", _PO, "read"),
    _op("aws_account.credentials", "GET", f"{_AA}/credentials", (OWNER, CREDENTIAL_PROCESS), "read"),
    _op("aws_account.detect", "GET", f"{_AA}/detect", _PO, "read"),
    # automatic stage-2 checks: no prompt, no file written (core/aws_detect)
    _op("aws_account.detect_verify", "POST", f"{_AA}/detect/verify", _PO, "read"),
    # runs a program from the person's own profile that may prompt (Touch ID, MFA)
    _op("aws_account.detect_check", "POST", f"{_AA}/detect/check", _P, "change"),
    _op("aws_account.sso_start", "POST", f"{_AA}/sso/start", _P, "change"),
    _op("aws_account.sso_poll", "POST", f"{_AA}/sso/poll", _P, "change"),
    _op("aws_account.login_start", "POST", f"{_AA}/login/start", _P, "change"),
    _op("aws_account.login_status", "GET", f"{_AA}/login/status", _PO, "read"),
    _op("aws_account.keys", "POST", f"{_AA}/keys", _P, "change"),
    _op("aws_account.connect", "POST", f"{_AA}/connect", _P, "change"),
    _op("aws_account.permission_template", "GET", f"{_AA}/permission-template", _PO, "read"),
    # raising the count creates Hives (EC2, CloudFront: they cost money)
    _op("aws_account.hive_fleet", "PUT", f"{_AA}/hive-fleet", _P, "off_machine"),
    _op("aws_account.default_vpc", "POST", f"{_AA}/default-vpc", _P, "off_machine"),
    # ── served by a Hive, called by the Host's daemon (routers/hive_brains.py; 404 on a
    # desktop). No agent calls them, on either side. ──
    _op("hive.serve.brain_install", "PUT", f"{_HV}/brains/{{brain}}", (HOST,), "change"),
    _op("hive.serve.brain_list", "GET", f"{_HV}/brains", (HOST,), "read"),
    _op("hive.serve.repo", "PUT", f"{_HV}/repos", (HOST,), "change"),
    _op("hive.serve.work", "POST", f"{_HV}/work", (HOST,), "change"),
    _op("hive.serve.work_order", "GET", f"{_HV}/work/{{work_id}}", (HOST,), "read"),
    # a work order's check record (scripts/task_checks), read by the Host for the
    # checks' statuses
    _op("hive.serve.work_checks", "GET", f"{_HV}/work/{{work_id}}/checks", (HOST,), "read"),
    _op("hive.serve.work_release", "POST", f"{_HV}/work/{{work_id}}/release", (HOST,), "irreversible"),
    _op("hive.serve.bundle", "GET", f"{_HV}/bundles/{{session_id}}/{{key}}", (HOST,), "read"),
    _op("hive.serve.usage", "GET", f"{_HV}/usage", (HOST,), "read"),
    # an AgentCore Hive's own busy answer (core/hive_agentcore.busy; 404 on any other)
    _op("hive.serve.busy", "GET", f"{_HV}/busy", (HOST,), "read"),
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


#: the commands on a run an agent may give with its limits (core/swarm_out_actor): a
#: request says who gives it (``by``), which the routes cannot verify, so an agent gives
#: them only through the Swarm Out CLI (``scripts.swarm_out``) or a Swarm Run lead's
#: ``pipeline-v1 child`` commands (``core.run_protocol.child_cli``, on the run's own
#: children), and core/run_owner_gate refuses a direct HTTP call to any of them from a
#: command
AGENT_LIMITED = frozenset({"run.accept", "run.return_prepare", "run.return_land",
                           "run.rerun", "run.stop", "run.discard",
                           "run.settle_without_files", "run.adopt",
                           "ask.answer_host", "ask.escalate", "run.ask_person", "run.reverted",
                           # only when no Hive takes this Host's runs: the CLI checks it
                           "hive.start",
                           # through restart-hive, which waits for the restart to end
                           "hive.restart",
                           # through idle-stop
                           "hive.idle_stop"})


@lru_cache(maxsize=1)
def _agent_limited() -> "tuple[tuple[Op, re.Pattern[str]], ...]":
    return tuple((op, re.compile(_template_re(op.path))) for op in ops() if op.name in AGENT_LIMITED)


def agent_limited_match(text: str) -> "Op | None":
    """The first command on a run whose path appears in *text* (decoded, de-quoted by
    the caller), else None: the gate makes sure such a call cannot say it is the
    person's (core/run_owner_gate)."""
    for op, rx in _agent_limited():
        if rx.search(text):
            return op
    return None


def match(method: str, path: str) -> "Op | None":
    """The op for a concrete request (``path`` without the query), else None."""
    for op in ops():
        if op.method == method and _segments_re(op.path).match(path):
            return op
    return None
