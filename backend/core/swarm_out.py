"""Swarm Out, Host side: dispatch a task to a Hive and answer what it asks.

v0 scope (docs/design/2026-10-27-swarm-out-v0-dispatch.md): send a task to a Hive,
let it run to the end there, and carry a person's answers back through the S3
bridge (``core/swarm_out_bridge.py`` is the Hive side). No result import, no
monitoring beyond what the dashboard needs to list a run.

- ``dispatch`` — start a session on the Hive with the task and return as soon as the
  Hive names the session. The turn keeps running on the Hive after we disconnect
  (``routers/chat.py`` detaches a turn whose client went away; measured 2026-10-27).
- ``list_runs`` — the local dispatch log, each row with its Hive's open questions
  (each naming its dispatch; an answer the Hive refused stays listed as not delivered)
  and whether its session is streaming on the Hive right now.
- ``answer`` — validate an answer against the question the Hive wrote, then write it
  to ``bridge/<hive>/answer/…``. Only the Host can write there. An approval carries
  what the card showed (``shown_sha256``) and is refused when the ask changed since,
  or when the command was too long to show whole (a deny is always written).

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
import re
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import httpx

from core.swarm_out_bridge import MAX_COMMAND_CHARS, approval_digest

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


class SwarmOutMaybeSent(SwarmOutError):
    """A dispatch the Hive may have taken: it did not name the session in time. Its row
    is recorded without a session; it is neither "sent" nor "nothing was sent"
    (the router answers 504, the dispatch card says so)."""


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
            "SELECT name, status, region FROM hive_instances ORDER BY name")
        rows = [{"name": r[0], "status": r[1], "region": r[2]} for r in await cur.fetchall()]
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


def _s3(inst: dict[str, Any]):
    from hive.provisioner import HiveProvisioner
    from database import db
    session = HiveProvisioner(Path(db.db_path))._get_session(inst["_account"], inst["region"])
    return session.client("s3", region_name=inst["region"])


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
#: hang guard for a Hive preparing a task's workspace (clones a repository it has not
#: cloned before, so minutes are normal)
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
                        repos: list[dict[str, Any]]) -> dict[str, Any]:
    """The Hive's ``POST /api/hive/work``: the prepared order. Raises SwarmOutError with
    the Hive's reason (one of core/hive_workspace's fixed texts) when it is not."""
    from core import hive_workspace
    try:
        r = await client.post("/api/hive/work", json={"work_id": work_id, "repos": repos},
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


async def _release_work(client: httpx.AsyncClient, work_id: str) -> None:
    """Best effort: the Hive removes a workspace whose session never started."""
    try:
        await client.post(f"/api/hive/work/{work_id}/release")
    except httpx.HTTPError:
        logger.warning("swarm_out: releasing work order %s failed", work_id)


async def dispatch(hive_ref: str, title: str, task: str, origin: str = "swarm-out",
                   repos: Optional[list] = None, *, segment: str = "",
                   agent: Optional[dict] = None, extra: Optional[dict] = None,
                   dispatch_id: Optional[str] = None) -> dict[str, Any]:
    """Start the task on the Hive; return {dispatch_id, hive, session_id, title, sent_at,
    repos}. ``repos``: ``[{url, ref[, sha]}]`` the task works on (core/task_repos; ``sha``:
    this machine's commit, its bundle kept on the Hive first by ``_ship_repos``).
    ``segment`` + ``agent``: the project section put before the task and the Project
    Agent the Hive pins the session to (``dispatch_spec``). ``extra``: what the dispatch
    card shows besides (``brief``, ``hive_reason``, ``brain_sync``, ``repo_notes``),
    kept on the row; never over the row's own fields (core/swarm_out_card).
    ``dispatch_id``: the id ``dispatch_spec`` chose (its pipeline run is named after it);
    a new one otherwise. The Hive prepares the task's workspace under it first (a work
    order, core/hive_workspace); every failure after that releases it, except a session
    that may have started."""
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
    inst = await _instance(hive_ref)
    async with _client(inst) as client:
        r = await client.get("/api/chat/sessions/admission-state")
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
            await swarm_out_model.align(client, inst["name"])
        except swarm_out_model.ModelAlignError as e:
            raise SwarmOutError(f"{e}; the task was not sent") from None
        # the Hive's backend makes the work order real before the session starts
        # (design 2026-10-30-run-spec §4.3): a failure there sends nothing
        dispatch_id = dispatch_id or new_dispatch_id()
        try:
            order = await _prepare_work(client, inst, dispatch_id, _work_repos(declared))
        except BaseException:
            await _release_work(client, dispatch_id)     # a prepare this side gave up on
            raise
        from core import hive_workspace
        declared = hive_workspace.task_repos_of(order)
        try:
            session_id = await asyncio.wait_for(
                _start_session(client, task_message(segment + task, order), dispatch_id,
                               _pin_request(agent)), SESSION_ID_WAIT_S)
        except (asyncio.TimeoutError, httpx.ReadTimeout):
            # The task may have been sent and taken: the Hive may be running it. Record
            # the dispatch without a session so this Hive's questions are still listed,
            # and say so instead of a generic failure (fixing spec J1).
            row = {**(extra or {}), **_log_row(inst, None, title, origin, declared, agent, dispatch_id)}
            await asyncio.to_thread(_append, json.dumps(row, ensure_ascii=False) + "\n")
            raise SwarmOutMaybeSent(
                f"Hive {inst['name']} did not name the session within {SESSION_ID_WAIT_S}s; "
                "the task may be running there. Check the Hive's own page before sending "
                "it again.") from None
        except BaseException:
            await _release_work(client, dispatch_id)     # the session never started
            raise
        if not session_id or not KEY_RE.match(session_id):
            await _release_work(client, dispatch_id)
            raise SwarmOutError("the Hive did not name a session")
    row = {**(extra or {}), **_log_row(inst, session_id, title, origin, declared, agent, dispatch_id)}
    line = json.dumps(row, ensure_ascii=False) + "\n"
    await asyncio.to_thread(_append, line)
    logger.info("swarm_out: dispatched %s to %s (session %s)", row["dispatch_id"], inst["name"], session_id)
    return row


def _append(line: str) -> None:
    with open(_log_path(), "a", encoding="utf-8") as f:
        f.write(line)


def _pin_request(agent: Optional[dict]) -> Optional[dict]:
    """What the Hive pins the session to (routers/chat.py ``project_agent``): it
    recomputes the revision from its own copy and refuses the task when it differs."""
    if not agent:
        return None
    return {"brain": agent["brain"], "mode": agent["mode"], "revision": agent["revision"]}


#: the one-line brief a dispatch card shows
MAX_BRIEF_CHARS = 400


def pick_hive(hives: list[dict[str, Any]]) -> tuple[str, str]:
    """(name, reason) of the Hive a task goes to when none is named: running and
    reachable, not saturated; the fewest streaming sessions, then the lowest memory, then
    the name. Deterministic. Raises SwarmOutError when none can take a task. ``reason``
    is a code the card words (core/swarm_out_card, the desktop card): ``only_free``,
    ``idle`` or ``least_busy:<streaming>``; a named Hive's is ``named``."""
    ok = [h for h in hives if h.get("status") == "running" and h.get("reachable") is True
          and h.get("saturated") is False and isinstance(h.get("streaming"), int)]
    if not ok:
        raise SwarmOutError("no running Hive can take a task now (none is running, reachable "
                            "and not full); start one from the Hive card")
    ok.sort(key=lambda h: (h["streaming"], h["memory_percent"] if isinstance(h.get("memory_percent"), (int, float))
                           else 101.0, str(h.get("name"))))
    best = ok[0]
    if len(ok) == 1:
        reason = "only_free"
    elif best["streaming"] == 0:
        reason = "idle"
    else:
        reason = f"least_busy:{best['streaming']}"
    return str(best["name"]), reason


async def dispatch_spec(hive_ref: Optional[str], title: str, brain: str, task: str, acceptance: str,
                        mode: Optional[str] = None, execution: Optional[str] = None,
                        origin: str = "swarm-out", *, seen_revision: str, brief: str = "",
                        why: str = "", returns: Optional[str] = None) -> dict[str, Any]:
    """Dispatch under *brain*'s Project Agent (design 2026-10-28-project-agent-flow §7):
    the host builds the Task Spec from the definition (``core.task_spec``), refuses it
    when the definition is not the revision the person was shown (``seen_revision``,
    from ``/spec``) or admission fails (naming the rule), syncs the Brain when the
    Hive's copy differs, then dispatches with the project section and the pin. The row
    records the spec; the result says what the sync did (``brain_sync``). No
    ``hive_ref``: ``pick_hive`` chooses (the person named none). ``brief``: one or two
    sentences of what the task is, for the dispatch card."""
    from core import brain_sync_host, task_spec
    from core.project_registry import get_swarmws
    ws = get_swarmws()
    try:
        spec = await asyncio.to_thread(task_spec.build, ws, brain, task, acceptance, mode, execution, why,
                                       "hive", returns=returns)
    except task_spec.SpecError as e:
        raise SwarmOutError(str(e)) from None
    if not seen_revision or spec["revision"] != seen_revision:
        raise SwarmOutError(f"{brain}'s Project Agent changed since it was shown; show the spec again")
    reasons = task_spec.admit(spec)
    if reasons:
        raise SwarmOutError(f"{brain} cannot be sent to a Hive: " + "; ".join(reasons))
    if hive_ref:
        hive_reason = "named"
    else:
        hive_ref, hive_reason = pick_hive((await fleet())["hives"])
    inst = await _instance(hive_ref)
    # a Hive that cannot prepare the workspace would run the task the old way: it is
    # refused before anything (the Brain included) is sent to it (run-spec §4.4)
    await _require_work_orders(inst)
    try:
        remote = await brain_sync_host.hive_brains(inst)
        if not remote["supported"]:
            raise SwarmOutError(f"Hive {inst['name']}'s version cannot receive Brains; update it once")
        local = await asyncio.to_thread(brain_sync_host.local_brain, ws, brain)
        if local["digest"] is None:
            raise SwarmOutError(f"{brain} cannot be packaged: {local['error']}")
        have = remote["brains"].get(brain) or {}
        synced = "up to date"
        if have.get("digest") != local["digest"]:
            await brain_sync_host.sync(inst, brain, ws)
            synced = "synced"
        elif have.get("current_digest") != local["digest"]:
            # the Hive's copy was changed there: the host is the only writer, so it is restored
            await brain_sync_host.sync(inst, brain, ws)
            n = have.get("changed_files")
            synced = f"restored {n} file(s) the Hive had changed" if isinstance(n, int) else "restored"
    except brain_sync_host.SyncRefused as e:
        raise SwarmOutError(f"{brain} could not be synced to {inst['name']}: {e}; nothing was sent") from None
    except httpx.HTTPError as e:
        raise SwarmOutError(f"{brain} could not be synced to {inst['name']}: the Hive did not answer "
                            f"({type(e).__name__}); nothing was sent") from None
    agent = {k: spec[k] for k in ("brain", "mode", "revision", "profile", "delivery", "acceptance", "gates")}
    # how the task runs, decided here for this request (design 2026-10-30-run-spec §3)
    agent["execution"] = spec["execution"]["how"]
    agent["execution_why"] = spec["execution_why"]
    agent["returns"] = spec["returns"]
    # the checks the run is expected to run: compared with its Bash calls when its
    # result is read (core/swarm_out_results)
    agent["validations"] = [{"id": v["id"], "command": v["command"]} for v in spec["validations"]]
    # this machine's commit of each repository, put on the Hive before the task starts
    await _ship_repos(inst, spec["repos"])
    row = await dispatch(hive_ref, title, spec["task"], origin,
                         [{"url": r["url"], "ref": r["ref"], **({"sha": r["sha"]} if r.get("sha") else {}),
                           "name": r["name"], "access": r["access"]}
                          for r in spec["repos"]],
                         segment=task_spec.segment(spec), agent=agent,
                         extra={"brief": " ".join((brief or "").split())[:MAX_BRIEF_CHARS],
                                "hive_reason": hive_reason, "brain_sync": synced,
                                "repo_notes": [{"name": r["name"], "url": r["url"], "dirty": r.get("dirty") or 0}
                                               for r in spec["repos"]]})
    return {**row, "brain_sync": synced}


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


#: hang guard for sending one repository's bundle to a Hive
SHIP_TIMEOUT_S = 600


async def _ship_repos(inst: dict[str, Any], repos: list[dict[str, Any]]) -> None:
    """Each repository with a commit of this machine (``sha``): a git bundle of it
    (core/repo_ship) to the Hive's ``PUT /api/hive/repos``, which only keeps it
    (core/hive_repo_inbox); the Hive's backend fetches it when it prepares the workspace
    (core/hive_workspace). A
    remote that has the commit already needs no bundle. Raises SwarmOutError; nothing is
    sent to the session then. (design 2026-10-30-hive-task-repos-from-host §2)"""
    from core import repo_ship
    for r in repos:
        if not r.get("sha"):
            continue
        try:
            path, _base, size = await asyncio.to_thread(
                repo_ship.pack, Path(r["checkout"]), r["url"], r["ref"], r["sha"])
        except repo_ship.ShipError as e:
            raise SwarmOutError(f"{r['name']} could not be packed: {e}; nothing was sent") from None
        if path is None:
            continue                         # the remote has the commit: the Hive fetches it
        try:
            status, detail = await _put_repo(inst, r, path, size)
        finally:
            path.unlink(missing_ok=True)
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
                         project_agent: Optional[dict] = None) -> Optional[str]:
    """POST the message and read SSE only until the Hive names the session, then drop
    the connection: the Hive keeps running the turn (it detaches a gone client). The
    session works on the work order ``work_id`` the Hive prepared (its repositories are
    the order's, routers/chat.py)."""
    body: dict[str, Any] = {"agent_id": "default", "message": message}
    if work_id:
        body["work_id"] = work_id
    if project_agent:
        body["project_agent"] = project_agent
    async with client.stream("POST", "/api/chat/stream", json=body,
                             timeout=httpx.Timeout(HTTP_TIMEOUT_S, read=SESSION_ID_WAIT_S)) as r:
        if r.status_code != 200:
            if project_agent and r.status_code in (400, 422):
                # routers/chat.py refuses a pin it cannot make (the Hive's copy is not
                # this revision): the Hive's own text is never shown
                raise SwarmOutError("the Hive refused the Project Agent pin (its copy of the Brain "
                                    "is not the revision sent); send it again")
            raise SwarmOutError(f"the Hive refused the task (HTTP {r.status_code})")
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
    out: dict[str, Any] = {"kind": rec["kind"], "hive": hive, "session_id": sid,
                           "request_id": rid, "asked_at": str(rec.get("asked_at", ""))[:40]}
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


def _too_old(asked_at: str, now: float) -> bool:
    try:
        t = calendar.timegm(time.strptime(asked_at, "%Y-%m-%dT%H:%M:%SZ"))
    except (ValueError, OverflowError):
        return True
    return now - t > ASK_MAX_AGE_S


def _open_asks(s3, bucket: str, hive: str) -> list[dict[str, Any]]:
    """Questions the Hive wrote that are neither answered, closed nor too old, and the
    ones whose answer the Hive's bridge refused (``refused``: not delivered, the person
    must answer on the Hive's own page) so a refusal is never mistaken for delivery."""
    def keys(prefix: str) -> dict[str, Any]:
        found: dict[str, Any] = {}
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            found.update({o["Key"][len(prefix):]: o.get("LastModified") for o in page.get("Contents", [])})
        return found
    base = f"bridge/{hive}/"
    asks = keys(base + "ask/")
    answered = keys(base + "answer/")
    closed = keys(base + "closed/")
    now = time.time()

    def refused(rel: str) -> bool:
        """A recent close that says the answer was refused (older ones are past the
        longest wait anyway, so their bodies are not read)."""
        lm = closed.get(rel)
        if lm is None or (hasattr(lm, "timestamp") and now - lm.timestamp() > ASK_MAX_AGE_S):
            return False
        try:
            body = json.loads(s3.get_object(Bucket=bucket, Key=base + "closed/" + rel)["Body"].read(4096))
        except Exception:   # unreadable: treated as an ordinary close
            logger.debug("swarm out: close record %s unreadable, treated as ordinary",
                         rel, exc_info=True)
            return False
        return isinstance(body, dict) and body.get("why") == "answer_refused"

    out = []
    for rel in sorted(asks):
        was_refused = False
        if rel in closed:
            # a refusal always follows a Host answer: no other close body is read
            if rel not in answered or not refused(rel):
                continue
            was_refused = True
        elif rel in answered:
            continue
        try:
            body = s3.get_object(Bucket=bucket, Key=base + "ask/" + rel)["Body"].read(64 * 1024)
            clean = _clean_ask(json.loads(body), hive)
        except Exception as e:   # one unreadable or malformed ask must not hide the others
            logger.warning("swarm_out: skipped an unreadable ask from %s (%s)", hive, type(e).__name__)
            continue
        if clean is not None and not _too_old(clean["asked_at"], now):
            if was_refused:
                clean["refused"] = True
            out.append(clean)
    return out


async def _session_states(inst: dict[str, Any]) -> Optional[dict[str, dict[str, Any]]]:
    """Each session the Hive holds, from its own streaming-state endpoint (the one its
    tabs read): ``{session_id: {streaming, waiting_input, activity}}``, or None when the
    Hive did not say. Only those three fields are kept, and only with the expected
    types (Hive-written data)."""
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
                    "activity": activity if isinstance(activity, str) and _ACTIVITY_RE.match(activity) else None}
    return out


def run_state(row: dict[str, Any], hive_status: Optional[str],
              sessions: Optional[dict[str, dict[str, Any]]], has_open_ask: bool) -> tuple[str, str]:
    """(state, reason) of one dispatched run: waiting | running | stopped | unknown.

    The Hive's own report of the session decides when there is one; an open (not
    refused) mirrored question decides ``waiting`` only when the Hive could not be
    asked. "stopped" means the session is not working now: the Host cannot tell a
    finished run from a failed or stopped one."""
    if hive_status == "gone":
        return "stopped", "hive_gone"
    if hive_status is not None and hive_status != "running":
        return "stopped", "hive_stopped"
    if not row.get("session_id"):
        return "unknown", "no_session_id"
    if sessions is None:
        return ("waiting", "question") if has_open_ask else ("unknown", "hive_silent")
    e = sessions.get(row["session_id"])
    if e is None:
        return "stopped", "not_held"
    if e["waiting_input"]:
        return "waiting", "waiting_input"
    if e["streaming"] or e["activity"] not in (None, "idle", "waiting_input"):
        return "running", e["activity"] if e["activity"] not in (None, "idle") else "streaming"
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

    def read_asks() -> list[dict[str, Any]]:
        s3 = _s3(inst)
        return _open_asks(s3, inst["s3_bucket"], inst["name"])  # cleaned

    async def questions() -> None:
        try:
            # bounded like the probes: a slow S3 must not hold up the others' poll (the
            # worker thread may finish later; its answer is then dropped)
            for rec in await asyncio.wait_for(asyncio.to_thread(read_asks), QUESTIONS_TIMEOUT_S):
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


async def list_runs() -> dict[str, Any]:
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
    sessions_of: dict[str, Optional[dict[str, dict[str, Any]]]] = {}
    for hive_id, (hive_asks, sessions, errs) in zip(targets, results):
        asks.extend(hive_asks)
        sessions_of[hive_id] = sessions
        errors.extend(errs)
    open_asks = {(a["hive_id"], a.get("session_id")) for a in asks if not a.get("refused")}
    for r in rows:
        state, reason = run_state(r, status_of.get(r["hive_id"]),
                                  sessions_of.get(r["hive_id"]),
                                  (r["hive_id"], r.get("session_id")) in open_asks)
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
    for a in asks:
        a["dispatch_title"] = titles.get((a["hive_id"], a.get("session_id")))
        a["possible_dispatch_title"] = unnamed.get(a["hive_id"]) if a["dispatch_title"] is None else None
    # each run's kept result (core/swarm_out_results); the checks due start here
    from core import swarm_out_results
    try:
        await swarm_out_results.attach(rows, status_of)
    except Exception as e:                          # results unreadable: the runs still list
        logger.warning("swarm_out: attaching results failed (%s)", type(e).__name__)
        errors.append("could not read the runs' results")
    return {"runs": rows, "asks": asks, "errors": errors}


def _log_row_of(dispatch_id: str) -> dict[str, Any]:
    for r in _read_log():
        if r.get("dispatch_id") == dispatch_id:
            return r
    raise SwarmOutError(f"no dispatch {dispatch_id!r}")


async def run_result(dispatch_id: str) -> dict[str, Any]:
    """The whole kept record of one run (with the reply), for the panel only."""
    from core import swarm_out_results
    await asyncio.to_thread(_log_row_of, dispatch_id)
    rec = await asyncio.to_thread(swarm_out_results.load, dispatch_id)
    return {"dispatch_id": dispatch_id, "record": rec}


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
              "reachable", "streaming", "saturated", "memory_percent")


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
            "memory_percent": _percent(d.get("memory_percent"))}


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
    out = []
    for h in found:
        row = {k: h.get(k) for k in ("name", "status", "version", "region", "instance_type")}
        if h["status"] == "running":
            p = probes.get(h["id"])
            row.update({"reachable": p is not None, "streaming": p and p["streaming"],
                        "saturated": p and p["saturated"], "memory_percent": p and p["memory_percent"]})
        else:
            row.update({"reachable": None, "streaming": None, "saturated": None, "memory_percent": None})
        out.append({k: row[k] for k in FLEET_KEYS})
    return {"hives": out, "running": len(running)}


def _check_answer(rec: dict[str, Any], answers: Any, decision: Any) -> dict[str, Any]:
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
                 shown_sha256: Any = None) -> dict[str, Any]:
    """Write the person's answer for an open question to the bridge. An approval names
    what the card showed (``shown_sha256``); it is refused when the ask changed since,
    or when the command was too long to be shown whole."""
    if not KEY_RE.match(session_id or "") or not KEY_RE.match(request_id or ""):
        raise SwarmOutError("unknown question")
    inst = await _instance(hive_ref)
    s3 = await asyncio.to_thread(_s3, inst)
    base = f"bridge/{inst['name']}/"
    rel = f"{session_id}/{request_id}.json"

    def _load(prefix: str) -> Optional[bytes]:
        try:
            return s3.get_object(Bucket=inst["s3_bucket"], Key=base + prefix + rel)["Body"].read(64 * 1024)
        except s3.exceptions.NoSuchKey:
            return None

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
    body = {"request_id": request_id, **_check_answer(rec, answers, decision),
            "answered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if rec["kind"] == "approval" and decision == "approve":   # a deny is always safe
        if shown_sha256 != rec["shown_sha256"]:
            raise SwarmOutError("the command changed on the Hive since it was shown; reload")
        if rec["truncated"]:
            raise SwarmOutError("this command is too long to show whole; approve it on the "
                                "Hive's own page, or deny it here")
    if rec["kind"] == "approval" and isinstance(shown_sha256, str):
        body["shown_sha256"] = shown_sha256

    def _put() -> None:
        try:   # IfNoneMatch: two answers at once — the second is refused, not overwritten
            s3.put_object(Bucket=inst["s3_bucket"], Key=base + "answer/" + rel,
                          Body=json.dumps(body, ensure_ascii=False).encode(), IfNoneMatch="*",
                          ContentType="application/json", ServerSideEncryption="AES256")
        except Exception as e:
            code = getattr(e, "response", {}).get("Error", {}).get("Code", "")
            if code in ("PreconditionFailed", "ConditionalRequestConflict"):
                raise SwarmOutError("this question was already answered") from None
            raise
    await asyncio.to_thread(_put)
    return {"ok": True, "request_id": request_id}
