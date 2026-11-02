"""Swarm Out, Host side: dispatch a task to a Hive and answer what it asks.

v0 scope (docs/design/2026-10-27-swarm-out-v0-dispatch.md): send a task to a Hive,
let it run to the end there, and carry a person's answers back through the S3
bridge (``core/swarm_out_bridge.py`` is the Hive side). No result import, no
monitoring beyond what the dashboard needs to list a run.

- ``dispatch`` — start a session on the Hive with the task and return as soon as the
  Hive names the session. The turn keeps running on the Hive after we disconnect
  (``routers/chat.py`` detaches a turn whose client went away; measured 2026-10-27).
- ``list_runs`` — the local dispatch log, each row with its Hive's waits (each naming
  its dispatch) in two lists: ``asks``, what needs the person (waiting, or an answer
  the Hive refused, with its ``refused_reason`` code), and ``answered``, what was answered from
  here (sent, taken: the Hive closed it ``why: delivered``, gone: closed otherwise),
  each with what was chosen (design 2026-10-31-swarm-out-single-loop-fixes §2.1);
  and whether its session is streaming on the Hive right now. Computed by
  ``refresh_runs`` (one at a time) and kept as ``snapshot``; ``run_refresh_loop``
  recomputes it every minute so runs settle and Hive workspaces are released even
  when no one looks (docs/design/2026-10-31-swarm-out-run-review.md §3.1).
- ``answer`` — validate an answer against the question the Hive wrote, then write it
  to ``bridge/<hive>/answer/…``. Only the Host can write there. An approval carries
  what the card showed (``shown_sha256``) and is refused when the ask changed since,
  or when the command was too long to show whole (a deny is always written). A pipeline
  decision (``kind: decision``, listed in the Hive's ``decisions.json``) is answered with
  one of its option ids and what its card showed.

Hive credentials are read from ``hive_instances`` inside the daemon and never leave
this module: no return value, log line or dispatch record carries them. Outbound
URLs are built only from a stored CloudFront domain that matches ``DOMAIN_RE``.
"""
from __future__ import annotations

import asyncio
import base64
import calendar
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

import httpx

from core.swarm_out_bridge import MAX_COMMAND_CHARS, approval_digest
from core.swarm_out_decisions import DECISION_CAPS, KEY_PREFIX as DECISION_KEY_PREFIX, \
    MAX_DECISIONS, SNAPSHOT as DECISIONS_SNAPSHOT, decision_digest

# Hive-reported counts of what it did not mirror are capped like every Hive field
_MAX_OMITTED = 10_000

logger = logging.getLogger(__name__)

DOMAIN_RE = re.compile(r"^[a-z0-9-]{1,63}\.cloudfront\.net$")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$")
KEY_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
BUCKET_RE = re.compile(r"^[a-z0-9.-]{3,63}$")
MAX_TASK_CHARS = 20000
MAX_TITLE_CHARS = 120
MAX_ANSWER_CHARS = 4000
SESSION_ID_WAIT_S = 60       # hang-guard: the Hive names the session within seconds
# A prompt waits at most 4 h on the Hive (permission_manager / ask_question_manager,
# both below the WAITING_INPUT watchdog); an older ask has no waiter left, whatever
# its markers say (e.g. the Hive was destroyed before it could write closed/).
ASK_MAX_AGE_S = 4 * 3600 + 5 * 60
HTTP_TIMEOUT_S = 20
# A status probe (session state, admission state) is a hang-guard too: a Hive that does
# not answer within this is reported as not reachable, so one dead Hive never holds
# up the panel's or the tab badge's poll.
PROBE_TIMEOUT_S = 5
# Sanity bound on the counts a Hive reports (Hive-written data is untrusted).
MAX_REPORTED_SESSIONS = 1000
# The S3 read of one Hive's questions, bounded for the same reason as the probes.
QUESTIONS_TIMEOUT_S = 10
# A session activity the Hive reports (a word like idle, streaming, preparing).
_ACTIVITY_RE = re.compile(r"^[a-z_]{1,32}$")

UNATTENDED_PREAMBLE = (
    "[Swarm Out] This task was dispatched to you and runs unattended: nobody is "
    "watching this session. Decide what you can decide yourself and state your "
    "assumptions in your final reply. Use AskUserQuestion only for a decision you "
    "genuinely cannot make; write each question so it is understandable on its own, "
    "because the person reads it elsewhere, possibly hours later. Finish with a reply "
    "that says what you did and where the results are.\n\n"
    "Task:\n"
)


class SwarmOutError(Exception):
    """A dispatch or answer that cannot be done; the message is safe to show."""


class SwarmOutRefused(SwarmOutError):
    """The task is refused at intake (its spec does not pass admission): nothing was
    asked of a Hive and nothing was sent. Recorded in ``refusals.jsonl``, never in the
    dispatch log (design 2026-10-30-dispatch-center §2.3); the router answers 422."""

    def __init__(self, message: str, private: bool = False):
        super().__init__(message)
        self.private = private


def _record_refusal(brain: str, title: str, reasons: list[str], private: bool) -> None:
    """One intake refusal (its own file: a dispatch-log row without a session means "may
    have been sent", and the dispatch log's readers expect a Hive)."""
    row = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "brain": brain,
           "title": " ".join((title or "").split())[:MAX_TITLE_CHARS], "private": private,
           "reasons": [str(r)[:300] for r in reasons[:6]]}
    d = _state_dir()
    with open(d / "refusals.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    # the running counts the history reads (the log itself is never read back whole)
    n = refusal_counts()
    n = {"total": n["total"] + 1, "private": n["private"] + int(private)}
    tmp = d / f".refusal-counts.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(n), encoding="utf-8")
    os.replace(tmp, d / "refusal-counts.json")


def refusal_counts() -> dict[str, int]:
    """``{"total", "private"}`` of the intake refusals recorded (the history's line)."""
    try:
        n = json.loads((_state_dir() / "refusal-counts.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"total": 0, "private": 0}
    ok = isinstance(n, dict) and all(isinstance(n.get(k), int) for k in ("total", "private"))
    return {"total": n["total"], "private": n["private"]} if ok else {"total": 0, "private": 0}


class SwarmOutMaybeSent(SwarmOutError):
    """A dispatch the Hive may have taken: the request reached it but no session was
    named (a wait that ran out, a connection reset, a stream that ended first). Its row
    is recorded without a session and its work order is kept; it is neither "sent" nor
    "nothing was sent" (the router answers 504, the dispatch card says so)."""


#: Errors on the session request that come after the request was written: the Hive may
#: be running the task (a reply cut off, or a wait for it that ran out). An error before
#: that (no connection, a write that failed) means nothing was sent.
_MAYBE_TAKEN = (asyncio.TimeoutError, httpx.ReadTimeout, httpx.ReadError, httpx.RemoteProtocolError)


#: One deadline for a whole dispatch (``dispatch_spec`` and the ``dispatch`` it calls).
#: The dispatch CLI stops waiting after scripts/swarm_out.DISPATCH_TIMEOUT_S (under the
#: agent's 10-minute Bash ceiling) and can then only say the task may be running; so
#: every step that asks the Hive something, or packs a repository for it, runs within
#: what is left of this, and a step that does not fit fails, naming the step, before
#: the session starts (nothing was sent). The step guards (PREPARE_TIMEOUT_S,
#: SHIP_TIMEOUT_S, ...) still cap each step; httpx timeouts are per read or write, never
#: a total, so the deadline is kept with asyncio.wait_for.
DISPATCH_DEADLINE_S = 480
#: the best-effort release of a work order after a failure, on top of the deadline
RELEASE_WAIT_S = 10
#: less time than this left for a step: it is not started
_MIN_STEP_S = 1.0


class _Deadline:
    """What is left of one dispatch's DISPATCH_DEADLINE_S, on the monotonic clock."""

    def __init__(self, seconds: float):
        self._at = time.monotonic() + seconds

    def left(self) -> float:
        return self._at - time.monotonic()

    def budget(self, step: str, cap: float, keep: float = 0.0) -> float:
        """How long *step* may take: its own *cap*, or less when less is left once
        *keep* (the session's window, held back for it) is set aside. Raises
        SwarmOutError when what is left is too short to start the step (a step whose
        own cap is shorter than _MIN_STEP_S is not refused for that)."""
        t = min(cap, self.left() - keep)
        if t < min(cap, _MIN_STEP_S):
            raise SwarmOutError(f"the dispatch ran out of time before {step} (a dispatch has "
                                f"{DISPATCH_DEADLINE_S:g} s in all); nothing was sent")
        return t


async def _within(deadline: _Deadline, step: str, cap: float, make, keep: Optional[float] = None,
                  hint: str = "") -> Any:
    """``await make()`` within *step*'s budget (``_Deadline.budget``; *keep* defaults to
    the session's window). A step that runs past it is stopped and fails as
    SwarmOutError naming the step: it comes before the session, so nothing was sent."""
    t = deadline.budget(step, cap, SESSION_ID_WAIT_S if keep is None else keep)
    try:
        return await asyncio.wait_for(make(), t)
    except asyncio.TimeoutError:
        raise SwarmOutError(f"{step} did not finish in the {t:.0f} s the dispatch had left "
                            f"(a dispatch has {DISPATCH_DEADLINE_S:g} s in all); nothing was sent"
                            + (f". {hint}" if hint else "")) from None


def _state_dir() -> Path:
    from config import get_app_data_dir
    d = Path(get_app_data_dir()) / "state" / "swarm-out"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _log_path() -> Path:
    return _state_dir() / "dispatches.jsonl"


def _db_path() -> str:
    from database import db
    return str(db.db_path)


async def hives() -> dict[str, Any]:
    """The Hives this Host knows, by name and status: what the Swarm Out panel needs to
    say whether there is anywhere to dispatch to, and what an agent needs to pick one.
    Never addresses or credentials."""
    import aiosqlite
    async with aiosqlite.connect(_db_path()) as conn:
        await conn.execute("PRAGMA busy_timeout = 3000")
        cur = await conn.execute(
            "SELECT id, name, status, region FROM hive_instances ORDER BY name")
        # the id is the row's key, not a secret: an agent updates a Hive by it (hive.update)
        rows = [{"id": r[0], "name": r[1], "status": r[2], "region": r[3]} for r in await cur.fetchall()]
    return {"hives": rows, "running": sum(1 for r in rows if r["status"] == "running")}


async def _hive_statuses() -> dict[str, str]:
    """Every Hive this Host knows, id -> status. Each running one's questions are listed,
    whether or not anything was ever dispatched to it from here."""
    import aiosqlite
    async with aiosqlite.connect(_db_path()) as conn:
        await conn.execute("PRAGMA busy_timeout = 3000")
        cur = await conn.execute("SELECT id, status FROM hive_instances")
        return {r[0]: r[1] for r in await cur.fetchall()}


async def _instance(hive_ref: str) -> dict[str, Any]:
    """A running Hive by id or name, with its account row; validated fields only."""
    import aiosqlite
    async with aiosqlite.connect(_db_path()) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA busy_timeout = 3000")
        cur = await conn.execute(
            "SELECT * FROM hive_instances WHERE id = ? OR name = ?", (hive_ref, hive_ref))
        rows = [dict(r) for r in await cur.fetchall()]
        if len(rows) != 1:
            raise SwarmOutError(f"no single Hive named {hive_ref!r}")
        inst = rows[0]
        cur = await conn.execute("SELECT * FROM hive_accounts WHERE id = ?", (inst["account_ref"],))
        acct = await cur.fetchone()
    return _validate_instance(inst, dict(acct) if acct is not None else None)


def _validate_instance(inst: dict[str, Any], acct: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Refuse a Hive whose stored address, name or bucket is not the expected shape:
    every outbound URL and S3 key is built from these fields."""
    if inst.get("status") != "running":
        raise SwarmOutError(f"Hive {inst.get('name')} is {inst.get('status')}, not running")
    domain = (inst.get("cloudfront_domain") or "").strip().lower()
    if not DOMAIN_RE.match(domain) or not NAME_RE.match(inst.get("name") or "") \
            or not BUCKET_RE.match(inst.get("s3_bucket") or ""):
        raise SwarmOutError(f"Hive {inst.get('name')} has an unexpected address or bucket")
    if not inst.get("auth_user") or not inst.get("auth_password") or acct is None:
        raise SwarmOutError(f"Hive {inst.get('name')} has no credentials or account")
    return {**inst, "cloudfront_domain": domain, "_account": acct}


# Every answer a Hive gives is read uncompressed: the Host's size caps count the bytes
# it buffers, and a decoded compressed answer could expand far past them before any cap
# runs. So every request asks for identity, and an answer that comes back encoded anyway
# is refused before a body byte is read (an httpx error, which every reader built on
# _client already handles as a failed call).
HIVE_HEADERS: dict[str, str] = {"Accept-Encoding": "identity"}


async def _refuse_encoded(response: httpx.Response) -> None:
    encoding = response.headers.get("content-encoding", "").strip().lower()
    if encoding not in ("", "identity"):
        raise httpx.DecodingError("the Hive sent a compressed answer", request=response.request)


def hive_event_hooks() -> dict[str, list]:
    """The event hooks every client to a Hive carries (see HIVE_HEADERS)."""
    return {"response": [_refuse_encoded]}


def _client(inst: dict[str, Any], timeout: float = HTTP_TIMEOUT_S) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=_base(inst), headers={**_auth(inst), **HIVE_HEADERS},
                             timeout=timeout, follow_redirects=False,
                             event_hooks=hive_event_hooks())


def _auth(inst: dict[str, Any]) -> dict[str, str]:
    token = base64.b64encode(f"{inst['auth_user']}:{inst['auth_password']}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _base(inst: dict[str, Any]) -> str:
    return f"https://{inst['cloudfront_domain']}"


def _s3(inst: dict[str, Any], purpose: str = "manage"):
    """The Hive's S3 client: ``read`` to show its questions, ``manage`` to answer them
    (another account's Hive may only be read; hive/accounts.session_for)."""
    from hive.provisioner import HiveProvisioner
    from database import db
    session = HiveProvisioner(Path(db.db_path))._get_session(
        inst["_account"], inst["region"], purpose)
    return session.client("s3", region_name=inst["region"])


def _owner(bucket: str) -> dict:
    """``ExpectedBucketOwner`` when the bucket name carries its account."""
    from hive.provisioner import bucket_owner
    return bucket_owner(bucket)


def new_dispatch_id() -> str:
    """A dispatch's id; also its work order's on the Hive (core/hive_workspace)."""
    return f"so_{uuid.uuid4().hex[:12]}"


def _log_row(inst: dict[str, Any], session_id: Optional[str], title: str, origin: str,
             repos: Optional[list] = None, agent: Optional[dict] = None,
             dispatch_id: Optional[str] = None) -> dict[str, Any]:
    """One dispatch log row; ``session_id`` None when the Hive never named the session.
    ``agent``: the Project Agent the task went under (brain, mode, revision, profile,
    delivery, acceptance), from the host's own Task Spec. ``repos``: each repository as
    the Hive prepared it (its worktree ``dir``, ``access``)."""
    row = {"dispatch_id": dispatch_id or new_dispatch_id(), "hive_id": inst["id"],
           "hive": inst["name"], "session_id": session_id, "title": title,
           "origin": origin, "sent_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "repos": repos or []}
    if agent:
        row["project_agent"] = agent
    return row


def task_message(task: str, order: Optional[dict]) -> str:
    """What the Hive receives: the unattended preamble, the workspace the Hive prepared
    (core/task_repos.task_text), then the task."""
    from core.task_repos import task_text
    return UNATTENDED_PREAMBLE.replace("Task:\n", task_text(order) + "Task:\n", 1) + task


_SHA_RE = re.compile(r"[0-9a-f]{40,64}")
#: the read guard of a Hive preparing a task's workspace (it clones a repository it has
#: not cloned before, so minutes are normal). The prepare as a whole runs within the
#: dispatch's DISPATCH_DEADLINE_S; this is also the delay of LATE_RELEASE_S
PREPARE_TIMEOUT_S = 1200


def _work_repos(declared: list[dict]) -> list[dict[str, Any]]:
    """The work order's repositories: each with its ``name`` and ``access`` (a dispatch
    without a Task Spec writes in every repository it names)."""
    out, names = [], set()
    for r in declared:
        name = str(r.get("name") or r["dir"].rsplit("/", 1)[1])
        while name in names:
            name += "-2"
        names.add(name)
        out.append({"url": r["url"], "ref": r["ref"], **({"sha": r["sha"]} if r.get("sha") else {}),
                    "name": name, "access": r.get("access") if r.get("access") in ("read", "write") else "write"})
    return out


async def _prepare_work(client: httpx.AsyncClient, inst: dict[str, Any], work_id: str,
                        repos: list[dict[str, Any]],
                        checks: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """The Hive's ``POST /api/hive/work``: the prepared order. Raises SwarmOutError with
    the Hive's reason (one of core/hive_workspace's fixed texts) when it is not.
    ``checks``: the task's setup and checks by id (``work_checks``), sent only to a Hive
    that runs them (admission ``task_checks``)."""
    from core import hive_workspace
    body_out: dict[str, Any] = {"work_id": work_id, "repos": repos}
    if checks:
        body_out["checks"] = checks
    try:
        r = await client.post("/api/hive/work", json=body_out,
                              timeout=httpx.Timeout(HTTP_TIMEOUT_S, read=PREPARE_TIMEOUT_S))
    except httpx.HTTPError as e:
        raise SwarmOutError(f"Hive {inst['name']} did not prepare the workspace ({type(e).__name__}); "
                            "nothing was sent") from None
    if r.status_code == 404:
        raise SwarmOutError(f"Hive {inst['name']}'s version cannot prepare a task's workspace; update it once")
    body: Any = None
    try:
        body = r.json()
    except ValueError:
        pass
    if r.status_code != 200:
        # only a fixed reason, about a repository this order named, is shown
        detail = str(body.get("detail") or "") if isinstance(body, dict) else ""
        known = {*hive_workspace.ERRORS, *(f"{x['name']}: {e}" for x in repos for e in hive_workspace.ERRORS)}
        raise SwarmOutError(f"Hive {inst['name']} could not prepare the workspace"
                            + (f" ({detail})" if detail in known else f" (HTTP {r.status_code})")
                            + "; nothing was sent")
    got = {x.get("name"): x for x in body.get("repos") or [] if isinstance(x, dict)} \
        if isinstance(body, dict) and isinstance(body.get("repos"), list) else None
    if not (got is not None and body.get("work_id") == work_id and set(got) == {x["name"] for x in repos}
            and body.get("outputs") == hive_workspace.outputs_rel(work_id)):
        raise SwarmOutError(f"Hive {inst['name']} did not confirm the workspace; nothing was sent")
    # the order is what this machine sent: the Hive adds only where each repository is,
    # and the commit of one sent without (its branch's head there)
    order = {"work_id": work_id, "outputs": body["outputs"], "repos": []}
    for x in repos:
        h = got[x["name"]]
        sha = x.get("sha") or h.get("sha")
        if h.get("dir") != hive_workspace.repo_rel(work_id, x["name"]) or h.get("access") != x["access"] \
                or (x.get("sha") and h.get("sha") != x["sha"]) or not (isinstance(sha, str) and _SHA_RE.fullmatch(sha)):
            raise SwarmOutError(f"Hive {inst['name']} prepared an unexpected workspace; nothing was sent")
        order["repos"].append({**x, "sha": sha, "dir": h["dir"],
                               "branch": hive_workspace.branch(work_id) if x["access"] == "write" else None})
    return order


#: A prepare this side gave up on goes on in the Hive's thread (the dropped request does
#: not stop it, routers/hive_brains) and can make the workspace again after the release
#: that follows at once; so the order is released once more, LATE_RELEASE_S later. That
#: covers a prepare that ends within it (the usual first clone); nothing on the Hive
#: bounds a prepare, so one that runs longer (waiting on another order's clone of the
#: same repository, several repositories) still leaves its workspace to the Hive's
#: retention sweep: only the Hive's release and prepare agreeing on a cancelled order
#: closes that. In memory: a daemon restart leaves it to the sweep too.
LATE_RELEASE_S = PREPARE_TIMEOUT_S
_LATE_RELEASES: set = set()


async def _release_late(inst: dict[str, Any], work_id: str) -> None:
    await asyncio.sleep(LATE_RELEASE_S)
    try:
        async with _client(inst) as client:
            await _release_work(client, work_id)
    except Exception as e:                          # best effort, like the first release
        logger.warning("swarm_out: releasing work order %s again failed (%s)", work_id, type(e).__name__)


def _release_again_later(inst: dict[str, Any], work_id: str) -> None:
    try:
        task = asyncio.get_running_loop().create_task(_release_late(inst, work_id))
    except RuntimeError:
        return
    _LATE_RELEASES.add(task)
    task.add_done_callback(_LATE_RELEASES.discard)


async def _release_work(client: httpx.AsyncClient, work_id: str) -> None:
    """Best effort: the Hive removes a workspace whose session never started. Bounded by
    RELEASE_WAIT_S, so a Hive that does not answer never holds up the failure's answer."""
    try:
        await asyncio.wait_for(client.post(f"/api/hive/work/{work_id}/release"), RELEASE_WAIT_S)
    except (httpx.HTTPError, asyncio.TimeoutError):
        logger.warning("swarm_out: releasing work order %s failed", work_id)


async def dispatch(hive_ref: str, title: str, task: str, origin: str = "swarm-out",
                   repos: Optional[list] = None, *, segment: str = "",
                   agent: Optional[dict] = None, extra: Optional[dict] = None,
                   dispatch_id: Optional[str] = None,
                   deadline: Optional[_Deadline] = None,
                   checks: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Start the task on the Hive; return {dispatch_id, hive, session_id, title, sent_at,
    repos}. ``repos``: ``[{url, ref[, sha]}]`` the task works on (core/task_repos; ``sha``:
    this machine's commit, its bundle kept on the Hive first by ``_ship_repos``).
    ``segment`` + ``agent``: the project section put before the task and the Project
    Agent the Hive pins the session to (``dispatch_spec``). ``extra``: what the dispatch
    card shows besides (``brief``, ``hive_reason``, ``brain_sync``, ``repo_notes``),
    kept on the row; never over the row's own fields (core/swarm_out_card).
    ``dispatch_id``: the id ``dispatch_spec`` chose (its pipeline run is named after it);
    a new one otherwise. The Hive prepares the task's workspace under it first (a work
    order, core/hive_workspace); every failure after that releases it, except one where
    the Hive may have taken the task (it answered the session request, or the reply was
    cut off or did not come in time) but named no session: that one is recorded without a
    session, keeps the work order and raises SwarmOutMaybeSent. ``deadline``: the
    dispatch's (``dispatch_spec``'s); a new DISPATCH_DEADLINE_S otherwise. Every step runs within it, the session's window
    (SESSION_ID_WAIT_S) held back until the session is started."""
    from core import task_repos
    title = " ".join((title or "").split())[:MAX_TITLE_CHARS]
    task = (task or "").strip()
    if not title or not task:
        raise SwarmOutError("a dispatch needs a title and a task")
    if len(task) > MAX_TASK_CHARS:
        raise SwarmOutError(f"the task is longer than {MAX_TASK_CHARS} characters")
    try:
        declared = task_repos.parse(repos)
    except task_repos.TaskRepoError as e:
        raise SwarmOutError(str(e)) from None
    deadline = deadline or _Deadline(DISPATCH_DEADLINE_S)
    inst = await _instance(hive_ref)
    name = inst["name"]
    async with _client(inst) as client:
        r = await _within(deadline, f"checking Hive {name}", HTTP_TIMEOUT_S,
                          lambda: client.get("/api/chat/sessions/admission-state"))
        if r.status_code != 200:
            raise SwarmOutError(f"Hive {inst['name']} did not answer (HTTP {r.status_code})")
        if r.json().get("saturated"):
            raise SwarmOutError(f"Hive {inst['name']} is busy; try again later")
        if r.json().get("work_orders") is not True:
            raise SwarmOutError(f"Hive {inst['name']}'s version cannot prepare a task's workspace; update it once")
        # the task runs on the model this host runs on (core/swarm_out_model); only a
        # Hive that can take the task has its model set
        from core import swarm_out_model
        try:
            # at most three requests (read the settings, check the model, set it)
            await _within(deadline, f"setting Hive {name}'s model", 3 * HTTP_TIMEOUT_S,
                          lambda: swarm_out_model.align(client, inst["name"]))
        except swarm_out_model.ModelAlignError as e:
            raise SwarmOutError(f"{e}; the task was not sent") from None
        # the Hive's backend makes the work order real before the session starts
        # (design 2026-10-30-run-spec §4.3): a failure there sends nothing
        dispatch_id = dispatch_id or new_dispatch_id()
        work = _work_repos(declared)
        prepare_step = f"preparing the workspace on Hive {name}"
        deadline.budget(prepare_step, PREPARE_TIMEOUT_S, SESSION_ID_WAIT_S)   # nothing asked yet
        try:
            order = await _within(deadline, prepare_step, PREPARE_TIMEOUT_S,
                                  lambda: _prepare_work(client, inst, dispatch_id, work, checks),
                                  hint="A first clone of a large repository can take this long; the Hive "
                                       "goes on building its copy, so sending the task again in a few "
                                       "minutes is usually faster")
        except BaseException:
            await _release_work(client, dispatch_id)     # a prepare this side gave up on
            _release_again_later(inst, dispatch_id)      # ... may still be running there
            raise
        from core import hive_workspace
        declared = hive_workspace.task_repos_of(order)
        taken: list = []                  # set once the Hive answered the request with 200
        try:
            # the window held back for it, or what is left when that is less
            wait = deadline.budget(f"starting the session on Hive {name}", SESSION_ID_WAIT_S)
            session_id = await asyncio.wait_for(
                _start_session(client, task_message(segment + task, order), dispatch_id,
                               _pin_request(agent, task), on_accepted=lambda: taken.append(True)),
                wait)
        except BaseException as e:
            if not (taken or isinstance(e, _MAYBE_TAKEN)):
                await _release_work(client, dispatch_id)     # the request never reached the Hive
                raise
            # The Hive may be running the task (fixing spec J1, e2e-fixes #3): record the
            # dispatch without a session so it is listed and this Hive's questions still
            # are, keep the work order, and say so instead of a generic failure
            _record_maybe_sent(extra, inst, title, origin, declared, agent, dispatch_id)
            if not isinstance(e, Exception):
                raise                                        # a cancel stays a cancel
            waited = isinstance(e, (asyncio.TimeoutError, httpx.ReadTimeout))
            raise SwarmOutMaybeSent(
                f"Hive {name} " + (f"did not name the session within {wait:.0f}s" if waited
                                   else "took the task, but the connection ended before it "
                                        "named the session")
                + "; the task may be running there. Check the Hive's own page before "
                "sending it again.") from None
        if not session_id or not KEY_RE.match(session_id):
            # answered with 200 (the only way to get here) but named no usable session
            _record_maybe_sent(extra, inst, title, origin, declared, agent, dispatch_id)
            raise SwarmOutMaybeSent(
                f"Hive {name} took the task but did not name the session; the task may be "
                "running there. Check the Hive's own page before sending it again.")
    row = {**(extra or {}), **_log_row(inst, session_id, title, origin, declared, agent, dispatch_id)}
    line = json.dumps(row, ensure_ascii=False) + "\n"
    await asyncio.to_thread(_append, line)
    logger.info("swarm_out: dispatched %s to %s (session %s)", row["dispatch_id"], inst["name"], session_id)
    return row


def _record_maybe_sent(extra: Optional[dict], inst: dict[str, Any], title: str, origin: str,
                       declared: Any, agent: Optional[dict], dispatch_id: str) -> None:
    """The dispatch's row without a session. Written in place (one short line), so a
    cancelled dispatch still leaves it."""
    row = {**(extra or {}), **_log_row(inst, None, title, origin, declared, agent, dispatch_id)}
    _append(json.dumps(row, ensure_ascii=False) + "\n")


def _append(line: str) -> None:
    with open(_log_path(), "a", encoding="utf-8") as f:
        f.write(line)


#: how many corrections of one pipeline task can each get a run of their own
MAX_PIPELINE_RERUNS = 50


def pipeline_run_id(dispatch_id: str, k: int = 0) -> str:
    """The id of a pipeline task's run: ``run_`` + the dispatch's first eight hex. The
    run of its *k*-th correction (a re-run after the run ended, ``rerun``): ``run_`` +
    eight hex of a hash of the dispatch and *k*, so every run of the task is one of a
    known set (``swarm_out_results.execution_check``)."""
    if k == 0:
        return "run_" + dispatch_id.removeprefix("so_")[:8]
    import hashlib
    return "run_" + hashlib.sha256(f"{dispatch_id}#{k}".encode()).hexdigest()[:8]


def pipeline_run_ids(dispatch_id: str) -> set[str]:
    """Every run id a pipeline task's runs can have: its first and its corrections'."""
    return {pipeline_run_id(dispatch_id, k) for k in range(MAX_PIPELINE_RERUNS + 1)}


def _pipeline_task(row: dict[str, Any]) -> bool:
    agent = row.get("project_agent")
    return isinstance(agent, dict) and agent.get("execution") == "pipeline" and bool(agent.get("run_id"))


def _next_rerun(dispatch_id: str, kept: dict[str, Any]) -> int:
    """The number of this correction's run, counted here under the run's lock (two at
    once never get the same one): one past the last this Host named and the last the
    Hive's result shows. Never derived from the result alone, which lists only some of
    a session's runs. A number whose send fails is not reused (its run may exist)."""
    from core import swarm_out_settle
    res = kept.get("result") if isinstance(kept.get("result"), dict) else {}
    shown = {r.get("run_id") for r in res.get("pipeline_runs") or [] if isinstance(r, dict)}
    seen = max((k for k in range(1, MAX_PIPELINE_RERUNS + 1) if pipeline_run_id(dispatch_id, k) in shown),
               default=0)
    got: dict[str, int] = {}

    def change(rec: dict[str, Any]):
        n = rec.get("pipeline_reruns") if isinstance(rec.get("pipeline_reruns"), int) else 0
        k = max(n, seen) + 1
        if k > MAX_PIPELINE_RERUNS:
            raise SwarmOutError(f"this task had {MAX_PIPELINE_RERUNS} corrections; dispatch it again")
        rec["pipeline_reruns"] = got["k"] = k
    swarm_out_settle._update(dispatch_id, change)
    return got["k"]


def _rerun_pin(row: dict[str, Any], k: int, text: str) -> dict:
    """What a correction of a pipeline task sends with it (routers/chat.py on the Hive):
    its run, number *k* (``_next_rerun``), with the task's own profile."""
    agent = row["project_agent"]
    from core.run_protocol.engine import REQUIREMENT_MAX
    return {"brain": agent.get("brain"), "mode": agent.get("mode"), "revision": agent.get("revision"),
            "execution": {"how": "pipeline", "profile": agent.get("profile"),
                          "run_id": pipeline_run_id(row["dispatch_id"], k),
                          "requirement": text if len(text) <= REQUIREMENT_MAX else None}}


def _rerun_how(pin: dict, brain: str) -> str:
    ex = pin["execution"]
    return (f"How: if this session's pipeline run had ended, this correction runs in a new run "
            f"`{ex['run_id']}` (profile `{ex['profile']}`), created with this message; otherwise it "
            f"continues in the open run. Begin with `pipeline-v1 status --project {brain} --run-id "
            f"{ex['run_id']}` (when it says there is no such run, use the session's open run) and "
            "follow its next step; do not create another run and do not commit outside a run.")


def _pin_request(agent: Optional[dict], task: str = "") -> Optional[dict]:
    """What the Hive pins the session to (routers/chat.py ``project_agent``): it
    recomputes the revision from its own copy and refuses the task when it differs.
    ``execution``: how the task runs, decided here (run-spec §4.2); for a pipeline, the
    run the Hive creates with the session and its requirement (*task*, the task's own
    words, when the run takes that many; a longer one: the run writes its own)."""
    if not agent:
        return None
    req = {"brain": agent["brain"], "mode": agent["mode"], "revision": agent["revision"]}
    if agent.get("execution") == "native":
        req["execution"] = {"how": "native"}
    elif agent.get("execution") == "pipeline":
        from core.run_protocol.engine import REQUIREMENT_MAX
        req["execution"] = {"how": "pipeline", "profile": agent.get("profile"), "run_id": agent.get("run_id"),
                            "requirement": task if task and len(task) <= REQUIREMENT_MAX else None}
    return req


#: the one-line brief a dispatch card shows
MAX_BRIEF_CHARS = 400


def pick_hive(hives: list[dict[str, Any]], open_runs: Optional[dict[str, int]] = None) -> tuple[str, str]:
    """(name, reason) of the Hive a task goes to when none is named: running and
    reachable, not saturated; the fewest of this Host's runs still open there
    (``open_runs`` by Hive name: a run waiting on the person is not streaming but still
    holds its Hive), then the fewest streaming sessions, then the lowest memory, then the
    name; only a Hive whose version has ``REQUIRED_CAPS`` (``capable``). Deterministic.
    Raises SwarmOutError when none can take a task. ``reason`` is a
    code the card words (core/swarm_out_card, the desktop card): ``only_free``, ``idle``
    (nothing open, nothing streaming) or ``least_busy:<n>`` (n: the larger of the two);
    a named Hive's is ``named``."""
    held = open_runs or {}
    up = [h for h in hives if h.get("status") == "running" and h.get("reachable") is True
          and h.get("saturated") is False and isinstance(h.get("streaming"), int)]
    ok = [h for h in up if h.get("capable") is True]
    if not ok and up:
        raise SwarmOutError("no running Hive has this version yet: update one with no open runs "
                            "(update-hive), then send the task")
    if not ok:
        raise SwarmOutError("no running Hive can take a task now (none is running, reachable "
                            "and not full): start a stopped one (start-hive)")
    ok.sort(key=lambda h: (held.get(str(h.get("name")), 0), h["streaming"],
                           h["memory_percent"] if isinstance(h.get("memory_percent"), (int, float)) else 101.0,
                           str(h.get("name"))))
    best = ok[0]
    busy = max(held.get(str(best.get("name")), 0), best["streaming"])
    if len(ok) == 1:
        reason = "only_free"
    elif busy == 0:
        reason = "idle"
    else:
        reason = f"least_busy:{busy}"
    return str(best["name"]), reason


def open_runs_by_hive() -> dict[str, int]:
    """This Host's runs still open on each Hive (by name): dispatched under a Project
    Agent and neither settled nor discarded. A row without one never settles
    (swarm_out_settle), so it is not counted."""
    from core import swarm_out_settle
    out: dict[str, int] = {}
    try:
        rows = _read_log()
    except (OSError, ValueError):
        return {}                 # only a tie-breaker: an unreadable log never fails a dispatch
    for r in rows:
        if not isinstance(r.get("project_agent"), dict) or not isinstance(r.get("dispatch_id"), str):
            continue
        try:
            rec = swarm_out_settle.load(r["dispatch_id"])
        except swarm_out_settle.SettleError:
            continue
        if rec.get("settled") or rec.get("abandoned_at"):
            continue
        out[str(r.get("hive"))] = out.get(str(r.get("hive")), 0) + 1
    return out


async def dispatch_spec(hive_ref: Optional[str], title: str, brain: str, task: str, acceptance: str,
                        mode: Optional[str] = None, execution: Optional[str] = None,
                        origin: str = "swarm-out", *, seen_revision: str, brief: str = "",
                        why: str = "", returns: Optional[str] = None,
                        needs_private: str = "", dispatched_from: Optional[str] = None) -> dict[str, Any]:
    """Dispatch under *brain*'s Project Agent (design 2026-10-28-project-agent-flow §7):
    the host builds the Task Spec from the definition (``core.task_spec``), refuses it
    when the definition is not the revision ``/spec`` returned (``seen_revision``) or admission fails (naming the rule), syncs the Brain when the
    Hive's copy differs, then dispatches with the project section and the pin. The row
    records the spec; the result says what the sync did (``brain_sync``). No
    ``hive_ref``: ``pick_hive`` chooses (the person named none). ``brief``: one or two
    sentences of what the task is, for the dispatch card. The whole of it runs within one
    DISPATCH_DEADLINE_S, started here and passed to every step (``_Deadline``)."""
    from core import brain_sync_host, task_spec
    from core.project_registry import get_swarmws
    deadline = _Deadline(DISPATCH_DEADLINE_S)
    ws = get_swarmws()
    try:
        spec = await asyncio.to_thread(task_spec.build, ws, brain, task, acceptance, mode, execution, why,
                                       "hive", returns=returns, needs_private=needs_private)
    except task_spec.SpecError as e:
        raise SwarmOutError(str(e)) from None
    if not seen_revision or spec["revision"] != seen_revision:
        raise SwarmOutError(f"{brain}'s Project Agent changed since spec returned it; run spec again")
    reasons = task_spec.admit(spec)
    if reasons:
        # refused at intake: before a Hive is chosen or the Brain synced (dispatch-center §2)
        private = bool(spec.get("private"))
        try:
            await asyncio.to_thread(_record_refusal, brain, title, reasons, private)
        except OSError as exc:                     # the record is not the refusal
            logger.warning("swarm_out: refusal not recorded (%s)", type(exc).__name__)
        raise SwarmOutRefused(f"{brain} cannot be sent to a Hive: " + "; ".join(reasons), private)
    if hive_ref:
        hive_reason = "named"
    else:
        # the Hives are probed at once, each within PROBE_TIMEOUT_S
        hive_ref, hive_reason = pick_hive((await _within(deadline, "choosing a Hive", 4 * PROBE_TIMEOUT_S,
                                                         fleet))["hives"],
                                          await asyncio.to_thread(open_runs_by_hive))
    inst = await _instance(hive_ref)
    # a Hive that cannot prepare the workspace would run the task the old way: it is
    # refused before anything (the Brain included) is sent to it (run-spec §4.4)
    await _within(deadline, f"checking Hive {inst['name']}", 2 * PROBE_TIMEOUT_S,
                  lambda: _require_work_orders(inst))
    # a Hive that runs a task's checks by id (scripts/task_checks) gets them as data and
    # keeps a record of each run; an older one gets them as text and its statuses are
    # inferred from the session's command text (core/swarm_out_results._checks)
    checks_by_id = bool(spec["setup"] or spec["validations"]) and (await _within(
        deadline, f"checking Hive {inst['name']}", 2 * PROBE_TIMEOUT_S,
        lambda: _has_cap(inst, "task_checks"))) is True
    try:
        remote = await _within(deadline, f"reading Hive {inst['name']}'s Brains", 2 * HTTP_TIMEOUT_S,
                               lambda: brain_sync_host.hive_brains(inst))
        if not remote["supported"]:
            raise SwarmOutError(f"Hive {inst['name']}'s version cannot receive Brains; update it once")
        local = await asyncio.to_thread(brain_sync_host.local_brain, ws, brain)
        if local["digest"] is None:
            raise SwarmOutError(f"{brain} cannot be packaged: {local['error']}")
        have = remote["brains"].get(brain) or {}
        synced = "up to date"
        sync_step = f"syncing {brain} to Hive {inst['name']}"
        if have.get("digest") != local["digest"]:
            await _within(deadline, sync_step, brain_sync_host.SYNC_TIMEOUT_S,
                          lambda: brain_sync_host.sync(inst, brain, ws))
            synced = "synced"
        elif have.get("current_digest") != local["digest"]:
            # the Hive's copy was changed there: the host is the only writer, so it is restored
            await _within(deadline, sync_step, brain_sync_host.SYNC_TIMEOUT_S,
                          lambda: brain_sync_host.sync(inst, brain, ws))
            n = have.get("changed_files")
            synced = f"restored {n} file(s) the Hive had changed" if isinstance(n, int) else "restored"
    except brain_sync_host.SyncRefused as e:
        raise SwarmOutError(f"{brain} could not be synced to {inst['name']}: {e}; nothing was sent") from None
    except httpx.HTTPError as e:
        raise SwarmOutError(f"{brain} could not be synced to {inst['name']}: the Hive did not answer "
                            f"({type(e).__name__}); nothing was sent") from None
    agent = {k: spec[k] for k in ("brain", "mode", "revision", "profile", "acceptance", "gates")}
    # the dispatch's id first: a pipeline task's run is named after it, so the project
    # section can name the run the Hive creates with the session (run-spec §4.2)
    dispatch_id = new_dispatch_id()
    run_id = pipeline_run_id(dispatch_id) if spec["execution"]["how"] == "pipeline" else None
    if run_id:
        agent["run_id"] = run_id
    # how the task runs, decided here for this request (design 2026-10-30-run-spec §3)
    agent["execution"] = spec["execution"]["how"]
    agent["execution_why"] = spec["execution_why"]
    agent["returns"] = spec["returns"]
    # the checks the run is expected to run: their statuses come from the Hive's check
    # record when it runs them by id (checks_by_id below), else from its Bash calls
    # (core/swarm_out_results._checks)
    agent["validations"] = [{"id": v["id"], "command": v["command"]} for v in spec["validations"]]
    # read from the Hive's check record instead (core/swarm_out_results._records_of)
    agent["checks_by_id"] = checks_by_id
    # this machine's commit of each repository, put on the Hive before the task starts
    await _ship_repos(inst, spec["repos"], deadline)
    row = await dispatch(hive_ref, title, spec["task"], origin,
                         [{"url": r["url"], "ref": r["ref"], **({"sha": r["sha"]} if r.get("sha") else {}),
                           "name": r["name"], "access": r["access"]}
                          for r in spec["repos"]],
                         segment=task_spec.segment(spec, run_id, work_id=dispatch_id if checks_by_id else None),
                         agent=agent, dispatch_id=dispatch_id, deadline=deadline,
                         checks=work_checks(spec) if checks_by_id else None,
                         extra={"brief": " ".join((brief or "").split())[:MAX_BRIEF_CHARS],
                                "hive_reason": hive_reason, "brain_sync": synced,
                                # the agent session that follows the run (agent-owns-its-runs §2.1)
                                "dispatched_from": _session_or_none(dispatched_from),
                                "repo_notes": [{"name": r["name"], "url": r["url"], "dirty": r.get("dirty") or 0}
                                               for r in spec["repos"]]})
    # the first line of the run's decision record (design 2026-11-02-hive-run-decision-record §3)
    from core import swarm_out_record
    await asyncio.to_thread(
        swarm_out_record.append_quietly, row["dispatch_id"],
        "agent" if _session_or_none(dispatched_from) else "person", "dispatch", spec["execution_why"],
        _session_or_none(dispatched_from), "dispatch", None,
        {"hive": inst.get("name"), "brain": brain, "execution": spec["execution"]["how"],
         "returns": spec["returns"], "revision": agent.get("revision")})
    return {**row, "brain_sync": synced}


def work_checks(spec: dict[str, Any]) -> dict[str, Any]:
    """A task's setup and checks as the Hive's work order takes them (core/hive_workspace
    ``prepare``): each command with the repository and directory it runs in, a check with
    its id. The Hive runs nothing else (scripts/task_checks)."""
    return {"setup": [{"command": s["command"], "repo": s.get("repo") or "", "cwd": s.get("cwd") or ""}
                      for s in spec["setup"]],
            "validations": [{"id": v["id"], "command": v["command"], "repo": v.get("repo") or "",
                             "cwd": v.get("cwd") or ""} for v in spec["validations"]]}


def _session_or_none(sid: Optional[str]) -> Optional[str]:
    from core.app_session_id import valid_app_session_id
    return sid if valid_app_session_id(sid) else None


async def _require_work_orders(inst: dict[str, Any]) -> None:
    try:
        async with _client(inst, PROBE_TIMEOUT_S) as client:
            r = await client.get("/api/chat/sessions/admission-state")
    except httpx.HTTPError as e:
        raise SwarmOutError(f"Hive {inst['name']} did not answer ({type(e).__name__}); nothing was sent") from None
    try:
        body = r.json() if r.status_code == 200 else None
    except ValueError:
        body = None
    if not isinstance(body, dict) or body.get("work_orders") is not True:
        raise SwarmOutError(f"Hive {inst['name']}'s version cannot prepare a task's workspace; update it once")
    if body.get("fixed_reserved") is not True:
        # an older Hive takes the reserved actions from the synced definition, which no
        # longer carries them: it could let push or a release through without the person
        raise SwarmOutError(f"Hive {inst['name']}'s version does not keep the reserved actions "
                            "for the person; update it once")
    if body.get("zero_touch") is not True:
        # an older Hive asks the person about a dangerous command and waits; a dispatched
        # task waits for nobody (design 2026-11-01-brain-to-hive-zero-touch)
        raise SwarmOutError(f"Hive {inst['name']}'s version waits for a person on a dispatched "
                            "task; update it once")


#: what a Hive's admission state must say before it takes a Brain's task
#: (``_require_work_orders``); ``fleet`` reports it as ``capable``, and ``pick_hive``
#: chooses only such a Hive
REQUIRED_CAPS = ("work_orders", "fixed_reserved", "zero_touch")


#: hang guard for sending one repository's bundle to a Hive
SHIP_TIMEOUT_S = 600


async def _ship_repos(inst: dict[str, Any], repos: list[dict[str, Any]],
                      deadline: Optional[_Deadline] = None) -> None:
    """Each repository with a commit of this machine (``sha``): a git bundle of it
    (core/repo_ship) to the Hive's ``PUT /api/hive/repos``, which only keeps it
    (core/hive_repo_inbox); the Hive's backend fetches it when it prepares the workspace
    (core/hive_workspace). A
    remote that has the commit already needs no bundle. Raises SwarmOutError; nothing is
    sent to the session then. (design 2026-10-30-hive-task-repos-from-host §2)
    ``deadline``: the dispatch's; packing (its ``git bundle``) and each upload run within
    what is left of it."""
    from core import repo_ship
    deadline = deadline or _Deadline(DISPATCH_DEADLINE_S)
    for r in repos:
        if not r.get("sha"):
            continue
        git_s = deadline.budget(f"packing {r['name']}", repo_ship.GIT_TIMEOUT_S, SESSION_ID_WAIT_S)
        try:
            path, _base, size = await asyncio.to_thread(
                repo_ship.pack, Path(r["checkout"]), r["url"], r["ref"], r["sha"], timeout=git_s)
        except repo_ship.ShipError as e:
            raise SwarmOutError(f"{r['name']} could not be packed: {e}; nothing was sent") from None
        if path is None:
            continue                         # the remote has the commit: the Hive fetches it
        try:
            status, detail = await _within(deadline, f"sending {r['name']} to Hive {inst['name']}",
                                           SHIP_TIMEOUT_S, lambda: _put_repo(inst, r, path, size))
        finally:
            await asyncio.to_thread(path.unlink, missing_ok=True)
        if status == 200:
            continue
        if status == 404:
            raise SwarmOutError(f"Hive {inst['name']}'s version cannot receive repositories; update it once")
        raise SwarmOutError(f"Hive {inst['name']} did not keep {r['name']} ({status}: {detail}); "
                            "nothing was sent")


async def _put_repo(inst: dict[str, Any], r: dict[str, Any], path: Path, size: int) -> tuple[int, str]:
    """(status, detail) of one ``PUT /api/hive/repos``; 200 only when the Hive kept the
    bundle where the task text says it is."""
    from core import hive_repo_inbox
    data = await asyncio.to_thread(path.read_bytes)       # at most repo_ship.MAX_BUNDLE_BYTES
    params = {"url": r["url"], "branch": r["ref"], "sha": r["sha"]}
    try:
        async with _client(inst, SHIP_TIMEOUT_S) as client:
            resp = await client.put("/api/hive/repos", params=params, content=data,
                                    headers={"Content-Type": "application/octet-stream"})
    except httpx.HTTPError as e:
        raise SwarmOutError(f"Hive {inst['name']} did not take {r['name']} ({type(e).__name__}); "
                            "nothing was sent") from None
    detail, body = "", None
    try:
        body = resp.json()
        detail = str(body.get("detail") or "")[:200] if isinstance(body, dict) else ""
    except ValueError:
        pass
    if resp.status_code == 200 and not (isinstance(body, dict)
                                        and body.get("bundle") == hive_repo_inbox.bundle_rel(r["sha"])
                                        and body.get("bytes") == size):
        return 500, "the Hive did not confirm the bundle"
    return resp.status_code, detail


async def _start_session(client: httpx.AsyncClient, message: str,
                         work_id: Optional[str] = None,
                         project_agent: Optional[dict] = None,
                         session_id: Optional[str] = None,
                         on_accepted: Optional[Callable[[], None]] = None) -> Optional[str]:
    """POST the message and read SSE only until the Hive names the session, then drop
    the connection: the Hive keeps running the turn (it detaches a gone client). The
    session works on the work order ``work_id`` the Hive prepared (its repositories are
    the order's, routers/chat.py). With ``session_id`` the message goes into that
    session (a re-run with a correction) instead of a new one. ``on_accepted`` is called
    once the Hive answers 200: from then on it has the task, whatever happens to the
    stream."""
    body: dict[str, Any] = {"agent_id": "default", "message": message}
    if session_id:
        body["session_id"] = session_id
    if work_id:
        body["work_id"] = work_id
    if project_agent:
        body["project_agent"] = project_agent
    async with client.stream("POST", "/api/chat/stream", json=body,
                             timeout=httpx.Timeout(HTTP_TIMEOUT_S, read=SESSION_ID_WAIT_S)) as r:
        if r.status_code != 200:
            if project_agent and session_id and r.status_code in (400, 409, 422):
                # a correction's pipeline run (routers/chat.py): refused, nothing was sent
                raise SwarmOutError(f"the Hive did not start a pipeline run for the correction (HTTP "
                                    f"{r.status_code}: the session is still working, or its run "
                                    "could not be made); nothing was sent")
            if project_agent and r.status_code in (400, 422):
                # routers/chat.py refuses a pin it cannot make. The Brain was synced
                # just before, so its copy is the host's: the two compute the revision
                # differently, which sending again would only repeat. The Hive's own
                # text is never shown
                raise SwarmOutError("the Hive computes a different revision of the Brain than this "
                                    "host (its version differs): update the Hive; the task was not "
                                    "sent")
            raise SwarmOutError(f"the Hive refused the task (HTTP {r.status_code})")
        if on_accepted:
            on_accepted()
        async for line in r.aiter_lines():
            if not line.startswith("data:"):
                continue
            try:
                ev = json.loads(line[5:].strip())
            except ValueError:
                continue
            if isinstance(ev, dict):
                sid = ev.get("session_id") or ev.get("sessionId")
                if isinstance(sid, str) and sid:
                    return sid
    return None


def _read_log() -> list[dict[str, Any]]:
    p = _log_path()
    if not p.exists():
        return []
    out = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(ln)
        except ValueError:
            continue                      # a torn last line from a crash
        if isinstance(row, dict) and row.get("dispatch_id"):
            out.append(row)
    return out


def _clean_ask(rec: Any, hive: str) -> Optional[dict[str, Any]]:
    """Rebuild a Hive-written ask from known fields only, each typed and capped. The
    Hive's own ``hive`` field is ignored: the caller sets it from the Host's record."""
    if not isinstance(rec, dict) or rec.get("kind") not in ("question", "approval"):
        return None
    sid, rid = str(rec.get("session_id", "")), str(rec.get("request_id", ""))
    if not KEY_RE.match(sid) or not KEY_RE.match(rid):
        return None
    from core import approval_class
    out: dict[str, Any] = {"kind": rec["kind"], "hive": hive, "session_id": sid,
                           "request_id": rid, "asked_at": str(rec.get("asked_at", ""))[:40],
                           # what the wait is about (design 2026-10-31-hive-approvals §2.2):
                           # None from a Hive too old to say, which the dispatching agent
                           # may not answer (its answer would be recorded as the person's)
                           "approval_class": approval_class.clean(rec.get("approval_class"))}
    if rec["kind"] == "approval":
        raw_cmd = str(rec.get("command", ""))
        tool, command = str(rec.get("tool_name", ""))[:100], raw_cmd[:MAX_COMMAND_CHARS]
        # a command cut short cannot be approved here. A Hive that predates the
        # ``truncated`` field cut at exactly MAX_COMMAND_CHARS without saying so, so
        # without the field a command of that length counts as cut
        hive_says = rec.get("truncated") if "truncated" in rec else len(raw_cmd) >= MAX_COMMAND_CHARS
        out.update(tool_name=tool, command=command, reason=str(rec.get("reason", ""))[:1000],
                   truncated=bool(hive_says) or len(raw_cmd) > MAX_COMMAND_CHARS,
                   # what the card shows; the answer carries it and the Hive's bridge
                   # delivers only when it matches the request still waiting
                   shown_sha256=approval_digest(tool, command, str(rec.get("reason", ""))[:1000]))
        return out
    qs = []
    all_qs = rec.get("questions") if isinstance(rec.get("questions"), list) else []
    omitted_q = max(0, len(all_qs) - 20)
    omitted_o = 0
    for q in all_qs[:20]:
        if not isinstance(q, dict) or not str(q.get("question", "")).strip():
            omitted_q += 1
            continue
        opts = q.get("options") if isinstance(q.get("options"), list) else []
        kept = [{"label": str(o.get("label", ""))[:500], "description": str(o.get("description", ""))[:1000]}
                for o in opts[:10] if isinstance(o, dict) and str(o.get("label", "")).strip()]
        omitted_o += len(opts) - len(kept)
        qs.append({"question": str(q["question"])[:MAX_ANSWER_CHARS], "header": str(q.get("header", ""))[:200],
                   "multiSelect": bool(q.get("multiSelect")), "options": kept})
    if not qs:
        return None
    out["questions"] = qs
    # dropped on the Hive (it says so in ``omitted``) or here: the card shows the total
    hive_omitted = rec.get("omitted") if isinstance(rec.get("omitted"), dict) else {}

    def _n(v: Any) -> int:
        return min(_MAX_OMITTED, v) if isinstance(v, int) and v > 0 else 0
    omitted_q += _n(hive_omitted.get("questions"))
    omitted_o += _n(hive_omitted.get("options"))
    if omitted_q or omitted_o:
        out["omitted"] = {"questions": omitted_q, "options": omitted_o}
    return out


def _clean_decision(rec: Any, hive: str) -> Optional[dict[str, Any]]:
    """One entry of a Hive's ``decisions.json``, rebuilt from known fields, each typed
    and capped as the Hive caps it: a pipeline run's question, why the evidence was not
    enough, and 2-3 options. An option id is kept whole (it is sent back as is); an entry
    whose ids are not short distinct strings is dropped. ``shown_sha256`` is what the
    card shows (the answer carries it; the Hive delivers only on a match);
    ``truncated``: a field was longer than the card shows, so it is not answered here."""
    if not isinstance(rec, dict) or rec.get("kind") != "decision":
        return None
    sid, rid, run_id = (str(rec.get(k, "")) for k in ("session_id", "request_id", "run_id"))
    if not (KEY_RE.match(sid) and KEY_RE.match(rid) and KEY_RE.match(run_id)) \
            or not rid.startswith(DECISION_KEY_PREFIX):
        return None
    opts = rec.get("options") if isinstance(rec.get("options"), list) else []
    options = []
    for o in opts[:3]:
        oid = o.get("id") if isinstance(o, dict) else None
        if not isinstance(oid, str) or not 0 < len(oid) <= 200:
            return None
        options.append({"id": oid, "choice": str(o.get("choice", ""))[:DECISION_CAPS["choice"]],
                        "consequence": str(o.get("consequence", ""))[:DECISION_CAPS["consequence"]],
                        "reversible": o.get("reversible") is not False,
                        "risk": "high" if o.get("risk") == "high" else "low"})
    if len(options) < 2 or len(options) != len(opts) or len({o["id"] for o in options}) != len(options):
        return None
    fields = {k: str(rec.get(k, "")) for k in ("decision_request_id", "question", "why")}
    cut = any(len(v) > DECISION_CAPS[k] for k, v in fields.items()) or any(
        len(str(o.get(k, ""))) > DECISION_CAPS[k] for o in opts for k in ("choice", "consequence"))
    out: dict[str, Any] = {"kind": "decision", "hive": hive, "session_id": sid, "request_id": rid,
                           "asked_at": str(rec.get("asked_at", ""))[:40], "run_id": run_id,
                           "project": str(rec.get("project", ""))[:100],
                           "decision_kind": str(rec.get("decision_kind", ""))[:60],
                           "options": options,
                           **{k: v[:DECISION_CAPS[k]] for k, v in fields.items()}}
    out["truncated"] = rec.get("truncated") is not False or cut
    out["shown_sha256"] = decision_digest(out)
    from core import approval_class
    out["approval_class"] = approval_class.clean(rec.get("approval_class"))
    if rec.get("refused") is True:
        # a Hive from before decision closes says so in its snapshot (no reason given)
        out["snapshot_refused"] = True
    return out


def _read_decisions(s3, bucket: str, hive: str) -> list[dict[str, Any]]:
    """The pipeline decisions the Hive lists as waiting (its ``decisions.json``, the
    whole current list, rewritten each pass it can read its runs), cleaned. None listed:
    an empty list (an older Hive writes no snapshot)."""
    try:
        raw = s3.get_object(Bucket=bucket, Key=f"bridge/{hive}/{DECISIONS_SNAPSHOT}",
                            **_owner(bucket))["Body"].read(2 * 1024 * 1024)
    except s3.exceptions.NoSuchKey:
        return []
    body = json.loads(raw)
    entries = body.get("decisions") if isinstance(body, dict) else None
    out = []
    for rec in (entries if isinstance(entries, list) else [])[:MAX_DECISIONS]:
        clean = _clean_decision(rec, hive)
        if clean is not None:
            out.append(clean)
    return out


def _too_old(asked_at: str, now: float) -> bool:
    try:
        t = calendar.timegm(time.strptime(asked_at, "%Y-%m-%dT%H:%M:%SZ"))
    except (ValueError, OverflowError):
        return True
    return now - t > ASK_MAX_AGE_S


#: why a Hive did not use an answer (``closed/`` ``reason``); anything else is "other"
REFUSAL_REASONS = ("no_option", "changed", "too_long", "run_refused", "not_recorded",
                   "not_host_class")
#: a wait's phase on the Host (design 2026-10-31-swarm-out-single-loop-fixes §2.1)
WAIT_PHASES = ("waiting", "sent", "taken", "refused", "gone")
MAX_CHOSEN_CHARS = 1000
#: bodies that never change once written (an answer, a delivered close), and the ask a
#: card answered, by object key and its LastModified: read once, not every poll
_BODIES: dict[tuple[str, str], Any] = {}
_BODIES_MAX = 4000


def _keys(s3, bucket: str, prefix: str) -> dict[str, Any]:
    found: dict[str, Any] = {}
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix, **_owner(bucket)):
        found.update({o["Key"][len(prefix):]: o.get("LastModified") for o in page.get("Contents", [])})
    return found


def _age_ok(lm: Any, now: float) -> bool:
    """An object written within the longest wait (no LastModified: kept)."""
    return not hasattr(lm, "timestamp") or now - lm.timestamp() <= ASK_MAX_AGE_S


def _body(s3, bucket: str, key: str, lm: Any, cap: int, keep: bool) -> Any:
    """A bridge object's JSON, or None when it is unreadable. ``keep``: it never changes
    once written, so it is read once per LastModified."""
    ck = (key, str(lm))
    if keep and ck in _BODIES:
        return _BODIES[ck]
    try:
        body = json.loads(s3.get_object(Bucket=bucket, Key=key, **_owner(bucket))["Body"].read(cap))
    except Exception:  # noqa: BLE001 — unreadable: the caller treats it as absent
        logger.debug("swarm out: bridge object %s unreadable", key, exc_info=True)
        return None
    if keep:
        if len(_BODIES) >= _BODIES_MAX:
            _BODIES.clear()
        _BODIES[ck] = body
    return body


def _chosen(kind: str, answer: Any) -> str:
    """What the person chose, from the Host's own answer file (only the Host writes
    ``answer/``: the Hive role can only read it), for the card's one line."""
    if not isinstance(answer, dict):
        return ""
    if kind == "decision":
        label = answer.get("shown_label")
        out = label if isinstance(label, str) and label else str(answer.get("option_id") or "")
    elif kind == "approval":
        out = answer.get("decision") if answer.get("decision") in ("approve", "deny") else ""
    else:
        vals = answer.get("answers") if isinstance(answer.get("answers"), dict) else {}
        out = " · ".join(str(v) for v in vals.values())
    return out[:MAX_CHOSEN_CHARS]


def _open_asks(s3, bucket: str, hive: str,
               answered_out: Optional[list[dict[str, Any]]] = None,
               decisions: Optional[list[dict[str, Any]]] = None) -> list[dict[str, Any]]:
    """The waits of one Hive that need the person (design 2026-10-31-swarm-out-single-
    loop-fixes §2.1): questions and approvals the Hive wrote that are neither answered,
    closed nor too old, and the ones whose answer the Hive's bridge refused (``refused``,
    with its ``refused_reason`` code: not used, so never mistaken for delivery; an
    approval's own ``reason`` is its text).

    ``decisions``: the pipeline decisions the Hive lists (``_read_decisions``); one
    answered here is marked the same way (refused, or moved to the answered list).
    None when the list could not be read: then an answered decision is not listed at
    all this time (it may still be waiting; never concluded gone from a list not read).
    ``answered_out``: when given, the waits answered from here are appended to it with
    their ``phase``: ``sent`` (the Hive has not closed it), ``taken`` (closed ``why:
    delivered``, the only sign the answer was used), ``gone`` (closed otherwise, or no
    longer listed: answered on the Hive's page, expired, the session ended). Each keeps
    ``chosen`` (from the Host's own answer file) and ``question_text``; an answer older
    than the longest wait is no longer listed."""
    base = f"bridge/{hive}/"
    asks = _keys(s3, bucket, base + "ask/")
    answered = _keys(s3, bucket, base + "answer/")
    closed = _keys(s3, bucket, base + "closed/")
    now = time.time()

    def close_of(rel: str) -> Optional[dict[str, Any]]:
        """The close record (``{}`` when it is unreadable: an ordinary close), or None."""
        if rel not in closed:
            return None
        ck = (base + "closed/" + rel, str(closed[rel]))
        body = _BODIES.get(ck)
        if body is None:
            body = _body(s3, bucket, ck[0], closed[rel], 4096, keep=False)
            # a delivered close is final (kept); a refusal may still turn plain
            if isinstance(body, dict) and body.get("why") == "delivered":
                _BODIES[ck] = body
        return body if isinstance(body, dict) else {}

    def phase_of(rel: str, listed: bool) -> tuple[str, str]:
        cl = close_of(rel)
        if cl is None:
            return ("sent" if listed else "gone"), ""
        if cl.get("why") == "delivered":
            return "taken", ""
        if cl.get("why") == "answer_refused":
            reason = cl.get("reason")
            return "refused", reason if reason in REFUSAL_REASONS else "other"
        return "gone", ""

    def answered_entry(rec: dict[str, Any], rel: str, phase: str, answer: Any) -> dict[str, Any]:
        if rec["kind"] == "decision":
            text = answer.get("shown_question") if isinstance(answer, dict) else None
            question = text if isinstance(text, str) and text else rec.get("question", "")
        elif rec["kind"] == "approval":
            question = rec.get("command", "")
        else:
            question = next((q["question"] for q in rec.get("questions") or []), "")
        return {"kind": rec["kind"], "hive": hive, "session_id": rec["session_id"],
                "request_id": rec["request_id"], "asked_at": rec.get("asked_at", ""),
                "phase": phase, "chosen": _chosen(rec["kind"], answer),
                "question_text": str(question)[:MAX_CHOSEN_CHARS],
                "answered_at": str(answer.get("answered_at", ""))[:40] if isinstance(answer, dict) else "",
                # who answered (the Host wrote it): the dispatching agent's own answer is
                # the host's, shown and counted apart from the person's
                "answered_by": "host" if isinstance(answer, dict) and answer.get("answered_by") == "host"
                               else "person",
                "approval_class": rec.get("approval_class")}

    out = []
    for rel in sorted(asks):
        is_answered = rel in answered
        if rel in closed and not is_answered:
            continue                               # closed before any answer from here
        if is_answered and not _age_ok(answered[rel], now):
            continue
        listed = rel not in closed
        # an answered ask's text is read once (by LastModified: a restart rewrites it)
        raw = _body(s3, bucket, base + "ask/" + rel, asks[rel], 64 * 1024, keep=is_answered)
        if raw is None:   # one unreadable or malformed ask must not hide the others
            logger.warning("swarm_out: skipped an unreadable ask from %s", hive)
            continue
        clean = _clean_ask(raw, hive)
        if clean is None:
            continue
        clean = dict(clean)
        if not is_answered:
            if not _too_old(clean["asked_at"], now):
                clean["phase"] = "waiting"
                out.append(clean)
            continue
        phase, reason = phase_of(rel, listed)
        answer = _body(s3, bucket, base + "answer/" + rel, answered[rel], 64 * 1024, keep=True)
        if phase == "refused":
            clean.update(refused=True, phase="refused", refused_reason=reason, chosen=_chosen(clean["kind"], answer))
            out.append(clean)
        elif answered_out is not None:
            answered_out.append(answered_entry(clean, rel, phase, answer))
    # the pipeline decisions: listed in decisions.json, answered and closed like a question
    listed_rel = set()
    for d in list(decisions or []):
        rel = f"{d['session_id']}/{d['request_id']}.json"
        listed_rel.add(rel)
        if rel not in answered:
            d.pop("snapshot_refused", None)
            d["phase"] = "waiting"
            continue
        decisions.remove(d)
        phase, reason = phase_of(rel, True)
        if phase == "sent" and d.pop("snapshot_refused", False):
            phase, reason = "refused", "other"
        answer = _body(s3, bucket, base + "answer/" + rel, answered[rel], 64 * 1024, keep=True)
        if phase == "refused":
            d.update(refused=True, phase="refused", refused_reason=reason, chosen=_chosen("decision", answer))
            out.append(d)
        elif answered_out is not None:
            answered_out.append(answered_entry(d, rel, phase, answer))
    # a Hive from before decision closes never closes one: an answered decision it no
    # longer lists may have been taken, so it is not shown as "not used"
    closes_decisions = any(rel.partition("/")[2].startswith(DECISION_KEY_PREFIX) for rel in closed)
    if answered_out is not None and decisions is not None:
        # an answered decision the Hive no longer lists: its card text is in the answer
        for rel, lm in sorted(answered.items()):
            sid, _, name = rel.partition("/")
            rid = name[:-5] if name.endswith(".json") else ""
            if not rid.startswith(DECISION_KEY_PREFIX) or rel in listed_rel or not _age_ok(lm, now) \
                    or not KEY_RE.match(sid) or not KEY_RE.match(rid):
                continue
            answer = _body(s3, bucket, base + "answer/" + rel, lm, 64 * 1024, keep=True)
            if not isinstance(answer, dict):
                continue
            phase, _reason = phase_of(rel, False)
            if rel not in closed and not closes_decisions:
                continue
            rec = {"kind": "decision", "session_id": sid, "request_id": rid,
                   "asked_at": "", "question": ""}
            # no longer waiting: a refusal no longer matters (settled elsewhere)
            answered_out.append(answered_entry(rec, rel, "gone" if phase == "refused" else phase, answer))
    return out


async def _session_states(inst: dict[str, Any]) -> Optional[dict[str, dict[str, Any]]]:
    """Each session the Hive holds, from its own streaming-state endpoint (the one its
    tabs read): ``{session_id: {streaming, waiting_input, activity, background_work}}``,
    or None when the Hive did not say. Only those fields are kept, and only with the
    expected types (Hive-written data; an older Hive sends no ``background_work``)."""
    async with _client(inst, PROBE_TIMEOUT_S) as client:
        r = await client.get("/api/chat/sessions/streaming-state")
    if r.status_code != 200:
        return None
    sessions = r.json().get("sessions")
    if not isinstance(sessions, dict):
        return None
    out: dict[str, dict[str, Any]] = {}
    for sid, e in sessions.items():
        if not isinstance(sid, str) or not isinstance(e, dict):
            continue
        activity = e.get("activity")
        out[sid] = {"streaming": e.get("streaming") is True,
                    "waiting_input": e.get("waiting_input") is True,
                    "activity": activity if isinstance(activity, str) and _ACTIVITY_RE.match(activity) else None,
                    "background_work": e.get("background_work") is True}
    return out


def run_state(row: dict[str, Any], hive_status: Optional[str],
              sessions: Optional[dict[str, dict[str, Any]]], has_open_ask: bool,
              has_open_decision: bool = False, has_refused: bool = False,
              has_sent: bool = False) -> tuple[str, str]:
    """(state, reason) of one dispatched run: waiting | running | stopped | unknown.

    A pipeline decision of the session that the Hive lists as waiting (not answered)
    decides ``waiting``: the run waits on a person whether or not a turn is open. Then an
    answer the Hive refused (``answer_refused``: the person answers on the Hive's page
    or discards), then an answer sent and not yet taken while nothing else waits
    (``running``, ``answer_sent``: nothing to do but wait for the Hive's next pass).
    Otherwise the Hive's own report of the session decides when there is one; an open
    mirrored question decides ``waiting`` only when the Hive could not be asked.
    "stopped" means the session is not working now: the Host cannot tell a finished run
    from a failed or stopped one."""
    if hive_status == "gone":
        return "stopped", "hive_gone"
    if hive_status == "updating":
        # back once the update ends: not stopped, so nothing settles on it meanwhile
        return "unknown", "hive_updating"
    if hive_status is not None and hive_status != "running":
        return "stopped", "hive_stopped"
    if row.get("not_started"):
        # its Hive has no started session for the order (``_find_sessions``)
        return "stopped", "not_started"
    if not row.get("session_id"):
        return "unknown", "no_session_id"
    if has_open_decision:
        return "waiting", "decision"
    if has_refused:
        return "waiting", "answer_refused"
    if has_sent and not has_open_ask:
        return "running", "answer_sent"
    if sessions is None:
        return ("waiting", "question") if has_open_ask else ("unknown", "hive_silent")
    e = sessions.get(row["session_id"])
    if e is None:
        return "stopped", "not_held"
    if e["waiting_input"]:
        return "waiting", "waiting_input"
    if e["streaming"] or e["activity"] not in (None, "idle", "waiting_input"):
        # the reason names what runs; a Hive activity that says it waits (its own word for
        # a queued or starting turn) is not a wait for anyone: only the branches above
        # give a run the reason of a wait
        a = e["activity"]
        return "running", a if a not in (None, "idle") and not a.startswith("waiting") else "streaming"
    if e.get("background_work"):
        # the turn ended but its sub-agents or background commands still run, and their
        # end starts the turn that writes the real result: not finished yet
        return "running", "background"
    return "stopped", "idle"


async def _read_hive(hive_id: str) -> tuple[list[dict[str, Any]], Optional[dict[str, dict[str, Any]]], list[str]]:
    """One running Hive's open questions and session states, and what went wrong."""
    errors: list[str] = []
    asks: list[dict[str, Any]] = []
    try:
        inst = await _instance(hive_id)
    except SwarmOutError as e:
        return [], None, [str(e)]
    except Exception as e:                          # store unreadable: show it, keep the list
        logger.warning("swarm_out: reading the Hive record failed (%s)", type(e).__name__)
        return [], None, ["could not read a Hive record"]

    def read_asks() -> tuple[list[dict[str, Any]], bool]:
        """The waits (cleaned), each with its ``phase``: the ones that need the person
        (waiting, refused) and the ones answered from here (sent, taken, gone; split off
        by _compute_runs). A decision list that cannot be read keeps the questions (and
        is reported)."""
        s3 = _s3(inst, "read")
        decisions: Optional[list[dict[str, Any]]]
        try:
            decisions = _read_decisions(s3, inst["s3_bucket"], inst["name"])
        except Exception as e:
            logger.warning("swarm_out: reading the decisions of %s failed (%s)",
                           inst["name"], type(e).__name__)
            decisions = None          # unknown: no answered decision is concluded gone
        answered: list[dict[str, Any]] = []
        found = _open_asks(s3, inst["s3_bucket"], inst["name"], answered, decisions)
        return found + (decisions or []) + answered, decisions is not None

    async def questions() -> None:
        try:
            # bounded like the probes: a slow S3 must not hold up the others' poll (the
            # worker thread may finish later; its answer is then dropped)
            found, decisions_read = await asyncio.wait_for(asyncio.to_thread(read_asks),
                                                           QUESTIONS_TIMEOUT_S)
            if not decisions_read:
                errors.append(f"could not read the decisions of Hive {inst['name']}")
            for rec in found:
                rec["hive_id"] = inst["id"]
                asks.append(rec)
        except SwarmOutError as e:
            errors.append(str(e))
        except Exception as e:                      # S3 unreachable or slow: show it, keep the list
            logger.warning("swarm_out: listing questions failed (%s)", type(e).__name__)
            errors.append(f"could not read the questions of Hive {inst['name']}")

    async def states() -> Optional[dict[str, dict[str, Any]]]:
        try:
            return await _session_states(inst)
        except Exception as e:                      # Hive unreachable: state unknown
            logger.warning("swarm_out: reading session state failed (%s)", type(e).__name__)
            return None

    _, sessions = await asyncio.gather(questions(), states())
    return asks, sessions, errors


async def _statuses_or_none(errors: list[str]) -> Optional[dict[str, str]]:
    try:
        return await _hive_statuses()
    except Exception as e:                          # store unreadable: the log's Hives only
        logger.warning("swarm_out: listing the Hives failed (%s)", type(e).__name__)
        errors.append("could not list the running Hives")
        return None


#: the release in flight (core/swarm_out_settle.release_due): one at a time, so a Hive
#: that does not answer never piles tasks up across polls
_RELEASE: dict[str, Any] = {"task": None}


def _release_done(task: "asyncio.Task") -> None:
    _RELEASE["task"] = None
    if not task.cancelled() and task.exception() is not None:
        logger.warning("swarm_out: releasing failed (%s)", type(task.exception()).__name__)


#: the last runs answer (refresh_runs) and when it was made (monotonic clock). The
#: refresh loop keeps it current whether or not the panel looks; GET /runs reuses it
#: while fresh; the Needs You source only reads it (snapshot). ``gen`` counts the
#: person's commands (invalidate_snapshot): a computation stores its answer only when no
#: command came while it ran, so a refresh that read the records before a command never
#: passes for one made after it. ``task`` is the computation running, ``task_gen`` the
#: generation it started at.
_SNAPSHOT: dict[str, Any] = {"at": None, "data": None, "task": None, "task_gen": 0, "gen": 0}
SNAPSHOT_FRESH_S = 20
REFRESH_EVERY_S = 60


def snapshot() -> Optional[dict[str, Any]]:
    """The last runs answer, or None before the first refresh. Never refreshes."""
    return _SNAPSHOT["data"]


def invalidate_snapshot() -> None:
    """A person's action changed a run: the next list recomputes, and a computation
    already running (it read the records before the action) is not kept as current."""
    _SNAPSHOT["gen"] += 1
    _SNAPSHOT["at"] = None


def _computed(task: "asyncio.Task", gen: int) -> None:
    """A computation ended: keep its answer when no command came while it ran. Runs
    whether or not anyone still waits on it (a caller that went away)."""
    if task.cancelled():
        return
    if task.exception() is not None:
        logger.warning("swarm_out: computing the runs failed (%s)", type(task.exception()).__name__)
        return
    if gen == _SNAPSHOT["gen"]:
        _SNAPSHOT.update(at=time.monotonic(), data=task.result())


async def refresh_runs() -> dict[str, Any]:
    """Recompute the runs answer and keep it as the snapshot. One at a time: a caller
    that arrives while a current one runs waits for it; one that arrives after a command
    waits for the computation from before the command to end, then starts a new one."""
    while True:
        task = _SNAPSHOT["task"]
        if task is not None and not task.done():
            if _SNAPSHOT["task_gen"] == _SNAPSHOT["gen"]:
                return await asyncio.shield(task)
            try:
                await asyncio.shield(task)        # from before a command: let it end
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — it is done; the next one is computed below
                logger.debug("swarm_out: earlier runs snapshot failed: %s", e)
            continue
        gen = _SNAPSHOT["gen"]
        task = asyncio.ensure_future(_compute_runs())
        _SNAPSHOT.update(task=task, task_gen=gen)
        task.add_done_callback(lambda t, g=gen: _computed(t, g))


def cancel_refresh() -> None:
    """Shutdown: stop a computation still running."""
    task = _SNAPSHOT["task"]
    if task is not None and not task.done():
        task.cancel()


#: the wake pass started after the last refresh (core/swarm_out_wake), while it runs
_WAKE: Optional[asyncio.Task] = None


async def run_refresh_loop() -> None:
    """Refresh the runs every REFRESH_EVERY_S while this Host has dispatched anything.
    Never raises."""
    while True:
        try:
            if await asyncio.to_thread(lambda: _log_path().exists()):
                await refresh_runs()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — tried again next round
            logger.warning("swarm_out: refreshing the runs failed (%s)", type(e).__name__)
        # the sessions that dispatched a run it needs now are woken (design
        # 2026-11-02-hive-run-decision-record §2; core/swarm_out_wake): in a task of its
        # own, one pass at a time, so a slow send never holds the refresh
        global _WAKE
        if _WAKE is None or _WAKE.done():
            from core import swarm_out_wake
            _WAKE = asyncio.create_task(swarm_out_wake.wake_sessions(), name="swarm-out-wake")
        await asyncio.sleep(REFRESH_EVERY_S)


#: a dispatch younger than this may still be naming its session: not asked about yet
FIND_SESSION_AFTER_S = 2 * SESSION_ID_WAIT_S


async def _order_state(hive_id: str, work_id: str) -> Optional[dict[str, Any]]:
    """The Hive's answer about one work order (GET /api/hive/work/{id}), or None when
    it did not answer properly (an older Hive has no such route)."""
    try:
        inst = await _instance(hive_id)
        async with _client(inst, PROBE_TIMEOUT_S) as client:
            r = await client.get(f"/api/hive/work/{work_id}")
        d = r.json() if r.status_code == 200 else None
    except Exception as e:                          # unreachable, timed out, not JSON
        logger.warning("swarm_out: work order probe failed (%s)", type(e).__name__)
        return None
    if not isinstance(d, dict) or not isinstance(d.get("exists"), bool):
        return None
    sid = d.get("session_id")
    return {"exists": d["exists"], "claimed": d.get("claimed") is True,
            "session_id": sid if isinstance(sid, str) and KEY_RE.match(sid) else None}


def _found_session(dispatch_id: str) -> Optional[str]:
    """The session ``_find_sessions`` found for a dispatch whose id never came back
    (kept in its settle record), or None."""
    from core import swarm_out_settle
    try:
        found = swarm_out_settle.load(dispatch_id).get("session_found")
    except swarm_out_settle.SettleError:
        return None
    return found if isinstance(found, str) and KEY_RE.match(found) else None


def _lost_rows(rows: list[dict[str, Any]], status_of: dict[str, Optional[str]],
               now: float) -> list[dict[str, Any]]:
    """The open rows with no session id whose Hive is asked about its work order now
    (file reads only: run off the event loop)."""
    from core import swarm_out_settle
    todo = []
    for r in rows:
        if r.get("session_id") or status_of.get(r.get("hive_id")) != "running":
            continue
        try:
            rec = swarm_out_settle.load(r["dispatch_id"])
        except (swarm_out_settle.SettleError, KeyError):
            rec = {}
        if rec.get("settled") or rec.get("abandoned_at"):
            continue
        found = rec.get("session_found")
        if isinstance(found, str) and KEY_RE.match(found):
            r["session_id"] = found
            continue
        if rec.get("not_started") is True:
            todo.append(r)                 # asked again: a slow start may claim it later
            continue
        try:
            sent = calendar.timegm(time.strptime(str(r.get("sent_at")), "%Y-%m-%dT%H:%M:%SZ"))
        except (ValueError, OverflowError):
            continue
        if now - sent >= FIND_SESSION_AFTER_S:
            todo.append(r)
    return todo


def _keep_found(dispatch_id: str, session_id: Optional[str]) -> None:
    """Record what the Hive said about a lost session: the session that claimed the
    order, or (None) that the order is still unclaimed."""
    from core import swarm_out_settle

    def change(rec: dict[str, Any]) -> None:
        if session_id:
            rec["session_found"] = session_id
            rec.pop("not_started", None)
        else:
            rec["not_started"] = True
    try:
        swarm_out_settle._update(dispatch_id, change)
    except swarm_out_settle.SettleError:
        pass


async def _find_sessions(rows: list[dict[str, Any]], status_of: dict[str, Optional[str]]) -> None:
    """A dispatch whose session id never came back (the Hive took too long to name it):
    ask its running Hive about the work order. Claimed with a session → that session is
    kept in the run's settle record, and every reader of the run's log row gets it
    (``_log_row_of``), so the run's commands work on it; an order that exists and is not
    claimed → ``not_started`` on this refresh (nothing ran yet, the agent may discard
    it). A not-started run is asked again on every refresh until the order is claimed,
    because a slow start can still claim it: the row says ``not_started`` only when this
    refresh's answer said so. No order at all (released, a rebuilt Hive) or no answer →
    the row stays unknown: a session may have run."""
    todo = await asyncio.to_thread(_lost_rows, rows, status_of, time.time())
    answers = await asyncio.gather(*(_order_state(r["hive_id"], r["dispatch_id"]) for r in todo))
    for r, st in zip(todo, answers):
        if st is None:
            continue
        if st["claimed"] and st["session_id"]:
            r["session_id"] = st["session_id"]
            await asyncio.to_thread(_keep_found, r["dispatch_id"], st["session_id"])
        elif st["exists"] and not st["claimed"]:
            r["not_started"] = True
            await asyncio.to_thread(_keep_found, r["dispatch_id"], None)


async def run_row(dispatch_id: str) -> Optional[dict[str, Any]]:
    """One run's row as ``/runs`` has it now (state, settle, result), or None."""
    data = await list_runs(SNAPSHOT_FRESH_S)
    for r in data.get("runs") or []:
        if isinstance(r, dict) and r.get("dispatch_id") == dispatch_id:
            return r
    return None


async def list_runs(max_age_s: float = 0) -> dict[str, Any]:
    """The runs answer: the snapshot when it is younger than ``max_age_s`` (GET /runs
    passes SNAPSHOT_FRESH_S), else a fresh one (refresh_runs)."""
    at, data = _SNAPSHOT["at"], _SNAPSHOT["data"]
    if max_age_s > 0 and at is not None and data is not None and time.monotonic() - at < max_age_s:
        return data
    return await refresh_runs()


async def _compute_runs() -> dict[str, Any]:
    """Dispatch log rows (newest first) and the open questions of every running Hive
    and of every Hive in the log.

    Each row has ``state`` / ``state_reason`` (run_state) and, derived from it,
    ``waiting`` and ``running`` (True / False, or None when the state is unknown,
    never a guess). The Hives are read concurrently, each status probe bounded by
    PROBE_TIMEOUT_S."""
    rows = list(reversed(await asyncio.to_thread(_read_log)))
    errors: list[str] = []
    statuses = await _statuses_or_none(errors)
    if statuses is None:                            # store unreadable: ask the log's Hives
        status_of = {r["hive_id"]: None for r in rows}
    else:                                           # a log Hive the store no longer has is gone
        status_of = {**{r["hive_id"]: "gone" for r in rows}, **statuses}
    # every running Hive, and the log's Hives when the store could not say
    targets = sorted(h for h, st in status_of.items() if st in ("running", None))
    results = await asyncio.gather(*(_read_hive(h) for h in targets))
    asks: list[dict[str, Any]] = []
    answered: list[dict[str, Any]] = []
    sessions_of: dict[str, Optional[dict[str, dict[str, Any]]]] = {}
    for hive_id, (hive_asks, sessions, errs) in zip(targets, results):
        for a in hive_asks:
            # ``asks``: what needs the person (waiting, refused), as every reader counts
            # it; ``answered``: answered from here (sent, taken, gone), for the cards
            (answered if a.get("phase") in ("sent", "taken", "gone") else asks).append(a)
        sessions_of[hive_id] = sessions
        errors.extend(errs)
    await _find_sessions(rows, status_of)
    open_asks = {(a["hive_id"], a.get("session_id")) for a in asks if not a.get("refused")}
    open_decisions = {(a["hive_id"], a.get("session_id")) for a in asks
                      if not a.get("refused") and a.get("kind") == "decision"}
    refused_of = {(a["hive_id"], a.get("session_id")) for a in asks if a.get("refused")}
    sent_of = {(a["hive_id"], a.get("session_id")) for a in answered if a.get("phase") == "sent"}
    for r in rows:
        at = (r["hive_id"], r.get("session_id"))
        state, reason = run_state(r, status_of.get(r["hive_id"]),
                                  sessions_of.get(r["hive_id"]),
                                  at in open_asks, at in open_decisions,
                                  at in refused_of, at in sent_of)
        r["state"], r["state_reason"] = state, reason
        r["waiting"] = state == "waiting"
        r["running"] = None if state == "unknown" else state == "running"
    # every prompt on the Hive is mirrored (its own jobs and web sessions too): each
    # ask names the dispatch it belongs to, or None for one this Host did not send.
    # A dispatch whose session was never named (it timed out) may own an unmatched
    # ask: that ask names it as ``possible_dispatch_title``, never as certain.
    titles = {(r["hive_id"], r["session_id"]): r.get("title") for r in rows if r.get("session_id")}
    unnamed = {}
    for r in rows:
        r["session_unknown"] = not r.get("session_id")
        if r["session_unknown"]:
            unnamed.setdefault(r["hive_id"], r.get("title"))   # rows are newest first
    for a in [*asks, *answered]:
        a["dispatch_title"] = titles.get((a["hive_id"], a.get("session_id")))
        a["possible_dispatch_title"] = unnamed.get(a["hive_id"]) if a["dispatch_title"] is None else None
    # each run's kept result (core/swarm_out_results); the checks due start here
    from core import swarm_out_results
    try:
        await swarm_out_results.attach(rows, status_of)
    except Exception as e:                          # results unreadable: the runs still list
        logger.warning("swarm_out: attaching results failed (%s)", type(e).__name__)
        errors.append("could not read the runs' results")
    # each Project Agent run's next step and whether it settles (core/swarm_out_settle);
    # a settled one's Hive is told to remove its workspace, in the background
    from core import swarm_out_settle
    try:
        tell = await asyncio.to_thread(swarm_out_settle.annotate, rows, _RELEASE["task"] is None)
        if tell and _RELEASE["task"] is None:
            _RELEASE["task"] = asyncio.create_task(swarm_out_settle.release_due(tell))
            _RELEASE["task"].add_done_callback(_release_done)
    except Exception as e:                          # never breaks the list
        logger.warning("swarm_out: settling failed (%s)", type(e).__name__)
        errors.append("could not read the runs' acceptance")
    # whether the person has anything to do on each run (design
    # 2026-11-02-hive-run-decision-record §1): one rule for the panel's "waiting for you"
    from core import swarm_out_record

    def _needs_person() -> None:
        for r in rows:
            if isinstance(r, dict) and isinstance(r.get("project_agent"), dict):
                r["needs_person"] = swarm_out_record.needs_person(r, asks)
    try:
        await asyncio.to_thread(_needs_person)
    except Exception as e:                          # never breaks the list
        logger.warning("swarm_out: who acts next failed (%s)", type(e).__name__)
    # intake refusals, for the history's line (dispatch-center §2.3)
    try:
        refusals = await asyncio.to_thread(refusal_counts)
    except (OSError, UnicodeError):
        refusals = None
    return {"runs": rows, "asks": asks, "answered": answered, "errors": errors, "refusals": refusals}


# ── the person's commands on a run (design 2026-10-31-swarm-out-run-review §3.3) ──
#
# Person-only: routers/swarm_out serves them to the desktop, core/run_owner_gate refuses
# them from a command.

#: refreshes started by a command (a reference keeps each alive until it ends)
_REFRESHES: set = set()


def _refresh_soon() -> None:
    """A command changed a run: recompute the runs now, in the background (the settle
    and the Hive's release happen there, not in the request)."""
    invalidate_snapshot()
    try:
        task = asyncio.get_running_loop().create_task(refresh_runs())
    except RuntimeError:
        return
    _REFRESHES.add(task)
    task.add_done_callback(_REFRESHES.discard)


async def _session_row(dispatch_id: str) -> dict[str, Any]:
    row = await asyncio.to_thread(_log_row_of, dispatch_id)
    if not row.get("session_id"):
        raise SwarmOutError("this run has no session on its Hive")
    return row


async def _not_closed(dispatch_id: str) -> None:
    """Refuse a command on a run that is closed (discarded, expired) or accepted: the
    same rule the Host proxy applies to its session (swarm_out_settle.closed_state)."""
    from core import swarm_out_settle
    rec = await asyncio.to_thread(swarm_out_settle.load, dispatch_id)
    state = await asyncio.to_thread(swarm_out_settle.closed_state, rec, dispatch_id)
    if state == "closed":
        raise SwarmOutError("this run is closed")
    if state == "accepted":
        raise SwarmOutError("this run is accepted")


async def stop_run(dispatch_id: str) -> dict[str, Any]:
    """Stop the run's current turn on its Hive (the session and the workspace stay)."""
    row = await _session_row(dispatch_id)
    await _not_closed(dispatch_id)
    inst = await _instance(row["hive_id"])
    async with _client(inst) as client:
        r = await client.post(f"/api/chat/stop/{row['session_id']}")
    if r.status_code >= 300:
        raise SwarmOutError(f"the Hive did not stop it (HTTP {r.status_code})")
    _refresh_soon()
    return {"stopped": True}


async def rerun(dispatch_id: str, text: str) -> dict[str, Any]:
    """Send the person's correction into the run's own session as a new message: the
    Hive runs a new turn in the same workspace, and the verdict on the last result is
    void once that turn ends (it is bound to its ``ended_at``)."""
    text = (text or "").strip()
    if not text:
        raise SwarmOutError("say what to change")
    if len(text) > MAX_TASK_CHARS:
        raise SwarmOutError(f"the correction is longer than {MAX_TASK_CHARS} characters")
    from core import swarm_out_settle
    row = await _session_row(dispatch_id)
    await _not_closed(dispatch_id)
    from core import swarm_out_results
    kept = await asyncio.to_thread(swarm_out_results.load, dispatch_id) or {}
    if kept.get("fetch_error") in swarm_out_settle.NO_RESULT_ERRORS:
        raise SwarmOutError("this run's session is gone from its Hive: discard it and send it again")
    inst = await _instance(row["hive_id"])
    pin = None
    if _pipeline_task(row):
        cap = await _has_cap(inst, "pipeline_rerun")
        if cap is None:
            raise SwarmOutError(f"Hive {inst.get('name')} did not answer; nothing was sent")
        if cap:             # an older Hive runs the correction without a run of its own
            k = await asyncio.to_thread(_next_rerun, dispatch_id, kept)
            pin = _rerun_pin({**row, "dispatch_id": dispatch_id}, k, text)
    message = text if pin is None else _rerun_how(pin, str(pin.get("brain") or "")) + "\n\n" + text
    async with _client(inst) as client:
        sid = await _start_session(client, message, project_agent=pin, session_id=row["session_id"])
    if sid != row["session_id"]:
        raise SwarmOutError("the Hive did not take the correction into the run's session")
    await asyncio.to_thread(swarm_out_results.turn_started, dispatch_id)
    _refresh_soon()
    out: dict[str, Any] = {"sent": True}
    if pin is not None:
        # the run the correction gets when the session's run had ended; an open run
        # takes the correction itself and this one is never made
        out["run_id"] = pin["execution"]["run_id"]
    elif _pipeline_task(row):
        out["pipeline"] = "this Hive's version runs a correction without a pipeline run; update it when the run ends"
    return out


async def _has_cap(inst: dict[str, Any], cap: str) -> Optional[bool]:
    """Whether the Hive's admission state says *cap*; None when it did not answer (not
    the same as an older Hive)."""
    try:
        async with _client(inst, PROBE_TIMEOUT_S) as client:
            r = await client.get("/api/chat/sessions/admission-state")
        body = r.json() if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None
    if not isinstance(body, dict):
        return None
    return body.get(cap) is True


async def discard_run(dispatch_id: str, note: str = "") -> dict[str, Any]:
    """Discard the run: stop it when it has a session (a failure to stop does not keep
    it), record the discard, and let the refresh settle it and tell its Hive to remove
    the workspace. From then on its session is closed (core/swarm_out_remote)."""
    from core import swarm_out_settle
    row = await asyncio.to_thread(_log_row_of, dispatch_id)
    if not isinstance(row.get("project_agent"), dict):
        raise SwarmOutError("a run sent before Project Agents cannot be discarded here")
    await _not_closed(dispatch_id)
    stopped: Optional[bool] = None
    if row.get("session_id"):
        try:
            inst = await _instance(row["hive_id"])
            async with _client(inst) as client:
                r = await client.post(f"/api/chat/stop/{row['session_id']}")
            stopped = r.status_code < 300
        except (SwarmOutError, httpx.HTTPError):
            stopped = False
    try:
        await asyncio.to_thread(swarm_out_settle.discard, dispatch_id, note)
    except swarm_out_settle.SettleError as e:
        raise SwarmOutError(str(e)) from None
    _refresh_soon()
    return {"discarded": True, "stopped": stopped}


def _log_row_of(dispatch_id: str) -> dict[str, Any]:
    """The dispatch log's row, with the session ``_find_sessions`` found when the log has
    none (the Hive named it too late): every command on the run reads the run here."""
    for r in _read_log():
        if r.get("dispatch_id") == dispatch_id:
            if not r.get("session_id"):
                found = _found_session(dispatch_id)
                if found:
                    return {**r, "session_id": found}
            return r
    raise SwarmOutError(f"no dispatch {dispatch_id!r}")


async def run_result(dispatch_id: str) -> dict[str, Any]:
    """The whole kept record of one run (with the reply), for the panel only."""
    from core import swarm_out_actor, swarm_out_record, swarm_out_results, swarm_out_settle
    await asyncio.to_thread(_log_row_of, dispatch_id)
    rec = await asyncio.to_thread(swarm_out_results.load, dispatch_id)
    # the waits the dispatching agent answered itself (design 2026-10-31-hive-approvals H8)
    answers = await asyncio.to_thread(swarm_out_actor.host_answers, dispatch_id)
    # every decision on the run, oldest first (design 2026-11-02-hive-run-decision-record §3)
    settle_rec = await asyncio.to_thread(swarm_out_settle.load, dispatch_id)
    return {"dispatch_id": dispatch_id, "record": _shown_checks(rec), "host_answers": answers,
            "decisions": swarm_out_record.entries(settle_rec), "ask_person": swarm_out_record.ask(settle_rec)}


def _shown_checks(rec: Any) -> Any:
    """A copy of a kept record with its check statuses as they are shown (a record from
    before ``not_seen`` says ``not_run``); the file itself is not touched."""
    from core import swarm_out_results
    res = (rec or {}).get("result") if isinstance(rec, dict) else None
    if not isinstance(res, dict) or not isinstance(res.get("checks"), list):
        return rec
    checks = [{**c, "status": swarm_out_results.check_status(c.get("status"))} if isinstance(c, dict) else c
              for c in res["checks"]]
    return {**rec, "result": {**res, "checks": checks}}


async def refresh_result(dispatch_id: str) -> dict[str, Any]:
    """Check one run's Hive now (the card's refresh) and return what /runs carries."""
    from core import swarm_out_results
    row = await asyncio.to_thread(_log_row_of, dispatch_id)
    rec = await swarm_out_results.fetch(row)
    end = ((rec or {}).get("result") or {}).get("ended_at")
    spend = await asyncio.to_thread(swarm_out_results.spend_of, [dispatch_id], None, {dispatch_id: end})
    return {"dispatch_id": dispatch_id,
            "result": swarm_out_results.public(rec, row.get("repos"), row.get("project_agent"),
                                               spend.get(dispatch_id)),
            "result_error": rec.get("fetch_error"), "result_checked_at": rec.get("checked_at")}


FLEET_KEYS = ("name", "status", "version", "region", "instance_type",
              "reachable", "streaming", "saturated", "memory_percent", "capable", "open_runs")


def _count(v: Any, low: int) -> Optional[int]:
    return v if isinstance(v, int) and not isinstance(v, bool) and low <= v <= MAX_REPORTED_SESSIONS else None


def _percent(v: Any) -> Optional[float]:
    """A reported memory percentage, or None (absent, unreadable on the Hive, or a
    Hive from before admission followed memory)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 100:
        return None
    return round(float(v), 1)


async def _admission(hive_id: str) -> Optional[dict[str, Any]]:
    """A running Hive's admission state, validated, or None when it did not answer
    properly within PROBE_TIMEOUT_S."""
    try:
        inst = await _instance(hive_id)
        async with _client(inst, PROBE_TIMEOUT_S) as client:
            r = await client.get("/api/chat/sessions/admission-state")
        if r.status_code != 200:
            return None
        d = r.json()
    except Exception as e:                          # unreachable, timed out, not JSON
        logger.warning("swarm_out: admission probe failed (%s)", type(e).__name__)
        return None
    if not isinstance(d, dict):
        return None
    streaming, saturated = _count(d.get("streaming_count"), 0), d.get("saturated")
    if streaming is None or not isinstance(saturated, bool):
        return None
    return {"streaming": streaming, "saturated": saturated,
            "memory_percent": _percent(d.get("memory_percent")),
            "capable": all(d.get(c) is True for c in REQUIRED_CAPS)}


async def fleet() -> dict[str, Any]:
    """Every Hive this Host knows, with what a running one reports about its load:
    the Swarm Out panel's fleet. Each row is built from FLEET_KEYS only, never an
    address or a credential. Running Hives are probed concurrently."""
    import aiosqlite
    async with aiosqlite.connect(_db_path()) as conn:
        await conn.execute("PRAGMA busy_timeout = 3000")
        cur = await conn.execute(
            "SELECT id, name, status, version, region, instance_type FROM hive_instances ORDER BY name")
        found = [dict(zip(("id", "name", "status", "version", "region", "instance_type"), r))
                 for r in await cur.fetchall()]
    running = [h for h in found if h["status"] == "running"]
    probes = dict(zip([h["id"] for h in running],
                      await asyncio.gather(*(_admission(h["id"]) for h in running))))
    held = await asyncio.to_thread(open_runs_by_hive)
    out = []
    for h in found:
        row = {k: h.get(k) for k in ("name", "status", "version", "region", "instance_type")}
        row["open_runs"] = held.get(str(h.get("name")), 0)
        if h["status"] == "running":
            p = probes.get(h["id"])
            row.update({"reachable": p is not None, "streaming": p and p["streaming"],
                        "saturated": p and p["saturated"], "memory_percent": p and p["memory_percent"],
                        "capable": p["capable"] if p else None})
        else:
            row.update({"reachable": None, "streaming": None, "saturated": None, "memory_percent": None,
                        "capable": None})
        out.append({k: row[k] for k in FLEET_KEYS})
    return {"hives": out, "running": len(running)}


def _check_answer(rec: dict[str, Any], answers: Any, decision: Any,
                  option_id: Any = None) -> dict[str, Any]:
    if rec.get("kind") == "decision":
        if not isinstance(option_id, str) or option_id not in {o["id"] for o in rec["options"]}:
            raise SwarmOutError("pick one of the decision's options")
        if rec["truncated"]:
            raise SwarmOutError("this decision is too long to show whole; answer it on the "
                                "Hive's own page")
        return {"option_id": option_id}
    if rec.get("kind") == "approval":
        if decision not in ("approve", "deny"):
            raise SwarmOutError("an approval takes approve or deny")
        return {"decision": decision}
    asked = {q.get("question") for q in rec.get("questions") or [] if isinstance(q, dict)}
    if not isinstance(answers, dict) or not answers:
        raise SwarmOutError("answer every question")
    clean: dict[str, str] = {}
    for q, a in answers.items():
        if q not in asked or not isinstance(a, str) or not a.strip() or len(a) > MAX_ANSWER_CHARS:
            raise SwarmOutError("an answer does not match the question")
        clean[q] = a.strip()
    if set(clean) != asked:
        raise SwarmOutError("answer every question")
    return {"answers": clean}


async def answer(hive_ref: str, session_id: str, request_id: str,
                 answers: Any = None, decision: Any = None,
                 shown_sha256: Any = None, option_id: Any = None,
                 via: Optional[str] = None, answered_by: str = "person") -> dict[str, Any]:
    """Write an answer for an open question to the bridge, the person's or
    (``answered_by="host"``) the dispatching agent's (core/swarm_out_wait_card
    ``answer_as_host``; design 2026-10-31-hive-approvals §2.3), which the Hive records as
    the host's and takes only for a class in ``approval_class.HOST_CLASSES``. ``via="chat"``:
    the person answered it on a chat card in the session that dispatched the run, and
    the daemon forwarded it; kept on the answer. An approval names what the card showed
    (``shown_sha256``); it is refused when the ask changed since, or when the command was
    too long to be shown whole. A pipeline decision (a ``dc-`` request the Hive lists in
    ``decisions.json``) is answered with one of its option ids and what its card showed."""
    from core import approval_class
    if not KEY_RE.match(session_id or "") or not KEY_RE.match(request_id or ""):
        raise SwarmOutError("unknown question")
    if answered_by not in ("person", "host"):
        raise SwarmOutError("unknown answerer")

    def _host_may(rec: dict[str, Any]) -> None:
        if answered_by == "host" and rec.get("approval_class") not in approval_class.HOST_CLASSES:
            raise SwarmOutError(
                f"this wait is the person's to answer ({rec.get('approval_class') or 'its Hive does not say what it is about'}): "
                "ask them with `swarm_out card`, or `swarm_out escalate`")
    inst = await _instance(hive_ref)
    s3 = await asyncio.to_thread(_s3, inst)
    base = f"bridge/{inst['name']}/"
    rel = f"{session_id}/{request_id}.json"

    def _load(prefix: str) -> Optional[bytes]:
        try:
            return s3.get_object(Bucket=inst["s3_bucket"], Key=base + prefix + rel,
                                 **_owner(inst["s3_bucket"]))["Body"].read(64 * 1024)
        except s3.exceptions.NoSuchKey:
            return None

    if request_id.startswith(DECISION_KEY_PREFIX):
        listed = await asyncio.to_thread(_read_decisions, s3, inst["s3_bucket"], inst["name"])
        rec = next((d for d in listed if d["session_id"] == session_id
                    and d["request_id"] == request_id), None)
        if rec is None or await asyncio.to_thread(_load, "closed/") is not None:
            raise SwarmOutError("this decision is no longer waiting on the Hive")
        _host_may(rec)
        if await asyncio.to_thread(_load, "answer/") is not None:
            raise SwarmOutError("this question was already answered")
        body = {"request_id": request_id, **_check_answer(rec, None, None, option_id),
                "answered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "answered_by": answered_by}
        if shown_sha256 != rec["shown_sha256"]:
            raise SwarmOutError("the decision changed on the Hive since it was shown; reload")
        body["shown_sha256"] = shown_sha256
        # what the person picked from, for the card's line once the Hive stops listing
        # it (the Hive reads only request_id, option_id and shown_sha256)
        option = next(o for o in rec["options"] if o["id"] == option_id)
        body["shown_label"] = f"{option_id} — {option['choice']}"[:MAX_CHOSEN_CHARS]
        body["shown_question"] = rec.get("question", "")[:MAX_CHOSEN_CHARS]
        if via == "chat":
            body["via"] = "chat"
        await asyncio.to_thread(_put_once, s3, inst, base + "answer/" + rel, body)
        if answered_by == "person":
            await asyncio.to_thread(_record_person_answer, session_id, request_id, "decision",
                                    body["shown_label"])
        return {"ok": True, "request_id": request_id}
    raw = await asyncio.to_thread(_load, "ask/")
    if raw is None:
        raise SwarmOutError("unknown question")
    if await asyncio.to_thread(_load, "closed/") is not None:
        raise SwarmOutError("this question is no longer open on the Hive")
    if await asyncio.to_thread(_load, "answer/") is not None:
        raise SwarmOutError("this question was already answered")
    rec = _clean_ask(json.loads(raw), inst["name"])
    if rec is None or _too_old(rec["asked_at"], time.time()):
        raise SwarmOutError("this question is no longer open on the Hive")
    _host_may(rec)
    body = {"request_id": request_id, **_check_answer(rec, answers, decision),
            "answered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "answered_by": answered_by}
    if answered_by == "host" and rec["kind"] == "question":
        # the host picks one of the options, never free text (design §4 layer 2)
        labels = {q["question"]: {o["label"] for o in q.get("options") or []} for q in rec["questions"]}
        if any(v not in labels.get(q, set()) for q, v in body["answers"].items()):
            raise SwarmOutError("answer with one of the options")
    if rec["kind"] == "approval" and decision == "approve":   # a deny is always safe
        if shown_sha256 != rec["shown_sha256"]:
            raise SwarmOutError("the command changed on the Hive since it was shown; reload")
        if rec["truncated"]:
            raise SwarmOutError("this command is too long to show whole; approve it on the "
                                "Hive's own page, or deny it here")
    if rec["kind"] == "approval" and isinstance(shown_sha256, str):
        body["shown_sha256"] = shown_sha256
    if via == "chat":
        body["via"] = "chat"

    await asyncio.to_thread(_put_once, s3, inst, base + "answer/" + rel, body)
    if answered_by == "person":
        await asyncio.to_thread(_record_person_answer, session_id, request_id, rec["kind"],
                                decision if rec["kind"] == "approval" else json.dumps(body.get("answers"), ensure_ascii=False))
    return {"ok": True, "request_id": request_id}


def dispatch_of_session(session_id: str) -> Optional[str]:
    """The dispatch whose Hive session is *session_id* (the runs as last listed, then the
    log), else None."""
    snap = snapshot() or {}
    for r in snap.get("runs") or []:
        if isinstance(r, dict) and r.get("session_id") == session_id and r.get("dispatch_id"):
            return r["dispatch_id"]
    for r in _read_log():
        if r.get("session_id") == session_id and r.get("dispatch_id"):
            return r["dispatch_id"]
    return None


def _record_person_answer(session_id: str, request_id: str, kind: str, picked: Any) -> None:
    """The person's answer to one of a run's waits, one entry of its decision record."""
    from core import swarm_out_record
    did = dispatch_of_session(session_id)
    if did:
        swarm_out_record.append_quietly(did, "person", "answer_wait", "", None, "answer_wait",
                                        {"request_id": request_id, "kind": kind,
                                         "pick": str(picked or "")[:400]})


def _put_once(s3, inst: dict[str, Any], key: str, body: dict[str, Any]) -> None:
    """Write an answer. IfNoneMatch: two answers at once — the second is refused, not
    overwritten."""
    try:
        s3.put_object(Bucket=inst["s3_bucket"], Key=key, **_owner(inst["s3_bucket"]),
                      Body=json.dumps(body, ensure_ascii=False).encode(), IfNoneMatch="*",
                      ContentType="application/json", ServerSideEncryption="AES256")
    except Exception as e:
        code = getattr(e, "response", {}).get("Error", {}).get("Code", "")
        if code in ("PreconditionFailed", "ConditionalRequestConflict"):
            raise SwarmOutError("this question was already answered") from None
        raise
