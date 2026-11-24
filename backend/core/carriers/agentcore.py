"""The AgentCore carrier: one AgentCore Runtime microVM per run round, its results in S3
(design docs/design/2026-11-20-swarm-out-multi-carrier.md §6).

A location is the account's runtime: a ``hive_instances`` row with ``carrier='agentcore'``
and its ``runtime_arn`` (one per account, §6.8). A run round is one runtime session:

- **Open** (``AgentCoreLocation.open_run``): ``InvokeAgentRuntime`` with ``{"action":
  "start", "sid", "bucket", "hive_user", "hive_password"}``. The run credential is random,
  made here and kept only on this machine (``state/swarm-out/run_creds/<dispatch>-<round>.json``,
  0600); the handle names it (``auth_ref``), the dispatch row never holds it. The
  session id is derived from the dispatch, the round and the attempt (``session_id_for``):
  every start is a new attempt, recorded here before the call, so a session id is asked
  for once only (its supervisor would refuse a second start, ``{"started": false, "why":
  "replaced"}``, and a released one is marked ended here). Throttling, the account's session
  quota, or too little of the dispatch's time left once a start thread is free (the
  ``elastic_start`` pool: core/executors) raise ``LocationUnavailable`` (nothing was
  written yet: D6); any other failure is an ordinary error.
- **Talk** (``AgentCoreCarrier.client``): an ``httpx.AsyncClient`` on the session
  endpoint's backend port (``https://8081--<host>/...``). Each request is signed SigV4
  (service ``bedrock-agentcore``, the runtime's region, the empty-payload hash: no
  ``X-Amz-Content-Sha256`` header is sent) and carries the run credential in
  ``X-Swarm-Hive-Auth: Basic ...`` (the platform strips ``Authorization`` before it
  forwards). A request to a session that has stopped makes the platform boot a new empty
  microVM, so the client refuses, before anything is sent, once the round has ended:
  ``client()`` itself raises on what this machine knows (the round's age past
  ``MAX_RUN_S`` less ``END_MARGIN_S``, counted from before the start call; an end it
  recorded), and every ``send`` first reads S3 off the event loop, every time
  (``_ended_in_s3``: ``state.json`` = ``ended``, ``replaced``, ``expired`` or a final
  ``crashed``; a ``closed`` entry in the records manifest; a heartbeat gone stale).
  It follows no redirect and signs no request for another host.
- **When a round ended** (``ended``, ``state``): an end recorded here, then S3, and only
  when S3 shows no end, the round's age (design §6.5: the Host judges by S3). A busy
  backend rewrites ``heartbeat.json`` every few minutes and an idle one is stopped by the
  platform after ``IDLE_TIMEOUT_S``, so a heartbeat older than its cadence +
  ``IDLE_TIMEOUT_S`` + ``END_MARGIN_S`` means the session is most likely gone
  (``heartbeat_lost``): the round reads as stopped and its results are read from S3 at
  once, but that end is not recorded and does not bound ``writes_end_at`` (S3 writes that
  fail for a while look the same). A round without one (an image older than the
  heartbeat) is judged as before. An end by age is recorded once ``writes_end_at`` passed.
- **Results** (``outputs``, ``state``, ``usage``): read from the run's S3 prefix only,
  never from the session. ``outputs`` keeps the record in exactly the place and shape
  ``core.hive_records.pull`` keeps (same checks, same local paths, same readers after it).
- **End** (``release``): ``StopRuntimeSession``; a session already gone counts as done.
- **When nothing more can come** (``writes_end_at``): a round's microVM exists at most the
  platform's lifetime from its start, so its S3 prefix holds everything it will ever hold
  once that time passed (plus the start call and clock skew), or once this host saw an end
  the session wrote itself (an idle end, ``closed``, ``replaced``). A pull that started after
  it and read the whole manifest (``outputs``' ``drained``) is the proof the Host needs that
  a result missing then never comes (core/swarm_out_results ``round_end``).
- **Machine logs** (``log_events``, the ``diagnostics`` duty): the runtime writes to
  CloudWatch Logs under ``/aws/bedrock-agentcore/runtimes/<runtime id>-<endpoint>``
  (the execution role's ``Logs`` statement); the round's lines are read there through the
  location's account session (core/hive_diagnostics shapes them).
- **Correction** (``next_round``, the carrier interface ``core.swarm_out.rerun`` calls,
  D7): while the round's session is open the correction goes into it (the same handle);
  after it ended, a new session as the next round (``open_next_round``), its repositories
  starting at the previous round's returned commits (``refs/hive/<dispatch>/<key>``,
  shipped with ``repo_ship.pack(..., pinned=True)``). Nothing is sent to the old session.
- **Reached by the callers** (``connect``): a record ``{id, name, carrier, _handle}``
  that ``registry.client_for`` turns into the round's client; the dispatch steps after
  ``open_run`` (Brain sync, repositories, work order, session) all go through it.

**S3 layout of one round** (``bridge/<sid>/`` in the location's bucket; written by the
Hive side, read here):

- ``records/manifest/<seq:06d>.json``: one record entry as the Hive's own manifest line
  (``{seq, kind, sha256, size[, key][, path][, listed]}``, core/hive_records), written
  after its file;
- ``records/<name>``: the entry's bytes, ``name`` = ``hive_records._name(seq, kind, key)``
  (recomputed here, never read from the entry);
- ``state.json``: ``{"state": "running" | "waiting" | "ended" | "replaced"}`` from the
  backend, ``{"state": "crashed", "exit", "log_tail", "at", "restarting"}`` or
  ``{"state": "expired", "at"}`` from the supervisor (hive/agentcore/supervisor.py: its
  backend died and was rescued, then started again or not; sealed before the lifetime);
- ``heartbeat.json``: ``{"at", "every"}``, rewritten while the backend is busy
  (core/hive_agentcore);
- ``checks.json``: ``{"at", "orders": [{"work_id", "checks": [...]}, ...]}``, one entry per
  work order with a check record, each what ``GET /api/hive/work/<id>/checks`` serves
  (core/hive_agentcore ``_checks_body``); the dispatch's checks are its order's (the
  Host sends the order under the dispatch id);
- ``usage.json``: ``{"rows": [...]}``, what ``GET /api/hive/usage`` serves, rewritten whole.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import math
import os
import re
import secrets
import stat
import threading
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit

import httpx

from core.carriers.base import (AGENTCORE, DIAGNOSTICS, TIER_ELASTIC, LocationUnavailable, Needs,
                                Offer, RunHandle, current_handle, rounds_of)
from core.swarm_out import SwarmOutError

logger = logging.getLogger(__name__)

KIND = AGENTCORE
SERVICE = "bedrock-agentcore"
CONTROL_SERVICE = "bedrock-agentcore-control"
#: the port the Hive backend listens on in the microVM (the supervisor serves 8080)
BACKEND_PORT = 8081
#: a session lives at most 8 hours (the platform's maxLifetime)
MAX_RUN_S = 28800
#: how long until a new session can take work: first call ~17 s + backend start ~35 s
START_S = 60.0
#: the platform's minimum runtime session id length
SID_MIN_LEN = 33
AUTH_HEADER = "X-Swarm-Hive-Auth"
ENDPOINT_HEADER = "x-amzn-bedrock-agentcore-runtime-session-endpoint-url"
RUN_USER = "swarm-run"
#: InvokeAgentRuntime errors that mean the runtime cannot take a session now (D6)
UNAVAILABLE_CODES = frozenset({"ThrottlingException", "ThrottledException", "ServiceQuotaExceededException"})
NOT_FOUND_CODES = frozenset({"ResourceNotFoundException", "NotFoundException"})
#: used when Service Quotas does not name the account's active-session quota; the
#: platform's own refusal (ServiceQuotaExceededException at the start) still decides
DEFAULT_SESSION_QUOTA = 500
QUOTA_CACHE_S = 3600
SESSION_CACHE_S = 900.0
#: the round counts as ended this long before the platform's lifetime ends (clock skew,
#: the start call's own time)
END_MARGIN_S = 300
#: the runtime's idleRuntimeSessionTimeout (hive/agentcore_provisioner uses this one): an
#: idle backend's session ends this long after its last work
IDLE_TIMEOUT_S = 900
#: a busy backend's heartbeat cadence when its heartbeat.json does not name one in range
#: (core/hive_agentcore.HEARTBEAT_S)
HEARTBEAT_S = 180
HEARTBEAT_MAX_S = 900
START_TIMEOUT_S = 120.0
MIN_START_S = 5.0
SHIP_TIMEOUT_S = 600.0
#: an S3 call's connect/read timeout (per socket read, never a total): a slow S3 frees the
#: worker thread it holds within this
S3_TIMEOUT_S = 20.0
SMALL_OBJECT_MAX = 64 * 1024          # a manifest entry, state.json
CHECKS_MAX = 1024 * 1024
USAGE_MAX = 16 * 1024 * 1024
START_ANSWER_MAX = 64 * 1024
LIST_PAGES_MAX = 50                   # manifest list pages read in one end check
#: how long after the platform's lifetime a round may still have written: its session
#: starts during the start call (at most START_TIMEOUT_S after ``opened_at``, which is taken
#: before it), and this machine's clock may differ from the platform's by END_MARGIN_S
WRITE_GRACE_S = START_TIMEOUT_S + END_MARGIN_S
#: where the runtime's CloudWatch log groups are (the execution role's ``Logs`` statement)
LOG_GROUP_ROOT = "/aws/bedrock-agentcore/runtimes/"
LOG_PAGES_MAX = 10                    # pages of one CloudWatch listing
LOG_GROUPS_MAX = 5                    # log groups of one runtime read (one per endpoint)
LOG_STREAMS_MAX = 5                   # streams named by the session read whole
LOG_EVENTS_MAX = 2000                 # events kept from one read (the newest)
LOG_TIMEOUT_S = 20.0
#: a round's window is read from its end backwards (its end says why it stopped), and one
#: read stops after LOG_CALLS_MAX calls or LOG_READ_BUDGET_S, whichever comes first: it
#: holds a worker thread of the default pool, which the chat path waits on too
LOG_CALLS_MAX = 60
LOG_READ_BUDGET_S = 60.0
#: the shortest piece of the window narrowed to while looking for one a page holds
LOG_MIN_PIECE_MS = 1000

RUNTIME_ARN_RE = re.compile(
    r"^arn:(aws[a-z-]*):bedrock-agentcore:([a-z]{2}(?:-[a-z]+)+-\d):(\d{12}):runtime/([A-Za-z0-9][A-Za-z0-9_-]{0,99})$")
REGION_RE = re.compile(r"^[a-z]{2}(?:-[a-z]+)+-\d$")
ACCOUNT_RE = re.compile(r"^\d{12}$")
BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
IMAGE_REPO_RE = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*$")
SID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{32,79}$")
AUTH_REF_RE = re.compile(r"^run_cred:(so_[0-9a-f]{12}):([1-9]\d{0,3})$")
_HOST_LABEL_RE = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
_PORT_PREFIX_RE = re.compile(r"^\d{2,5}--")
_MANIFEST_KEY_RE = re.compile(r"/records/manifest/(\d{6})\.json$")

#: the round's state.json word -> (run state, reason) (the words core/swarm_out.run_state uses)
#: the elastic row's statuses while it changes (hive/agentcore_provisioner.TRANSIENT)
CHANGING_ROW_STATUSES = ("enabling", "upgrading", "disabling")
_STATES = {"running": ("running", "streaming"), "waiting": ("waiting", "waiting_input"),
           # its backend died, was rescued and is starting again (supervisor)
           "crashed": ("unknown", "starting")}
#: an end reason -> the run state's reason
_END_REASONS = {"closed": "closed", "ended": "idle", "carrier_expired": "carrier_expired",
                "carrier_replaced": "carrier_replaced", "released": "released",
                "expired_sealed": "carrier_expired", "crashed": "idle", "heartbeat_lost": "idle"}
#: ends after which the round writes nothing more: the supervisor stopped its backend,
#: sealed, and wrote its last word. Their time here bounds ``writes_end_at``. A stale
#: heartbeat is not one of them: it travels the same S3 path as the results, so S3 writes
#: failing for a while (the session alive, its results still to come) look the same
NO_MORE_WRITES = frozenset({"expired_sealed", "crashed"})
#: ends read from S3 that are observations of now, never recorded for good (a heartbeat
#: that comes back makes the round open again)
UNRECORDED_ENDS = frozenset({"heartbeat_lost"})


class _NoRunCredential(logging.Filter):
    """botocore logs a request's params at DEBUG, the start payload with the run
    credential among them: such a record is dropped."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            return "hive_password" not in record.getMessage()
        except (TypeError, ValueError, KeyError):     # a record that does not format: dropped
            return False


for _name in ("botocore.endpoint", "botocore.hooks", "botocore.client", "botocore.httpsession"):
    _lg = logging.getLogger(_name)
    if not any(isinstance(f, _NoRunCredential) for f in _lg.filters):
        _lg.addFilter(_NoRunCredential())


class AgentCoreError(SwarmOutError, RuntimeError):
    """An ordinary failure of the AgentCore carrier; the message is safe to show. A
    SwarmOutError, so every per-run caller handles it as it handles a Hive it cannot use
    (a round that ended, ``RunEnded``, among them)."""


class RunEnded(AgentCoreError):
    """The round's session has ended: nothing may be sent to it (a request would boot a
    new empty microVM). ``why``: ``closed`` | ``ended`` | ``carrier_expired`` | ``carrier_replaced`` |
    ``released``."""

    def __init__(self, why: str):
        super().__init__(f"this run's Elastic Workers session has ended ({why}); nothing was sent")
        self.why = why


# ── small pieces ─────────────────────────────────────────────────────────────────

def _now() -> float:
    return time.time()


def _state_dir() -> Path:
    from core import swarm_out
    return swarm_out._state_dir()


def _db_path() -> str:
    from database import db
    return str(db.db_path)


def _host_version() -> str:
    from config import settings
    return str(settings.app_version)


def _session_for(acct: dict, region: str, purpose: str):
    """The boto3 session for the location's account (hive/accounts.session_for: raises
    for an account this Host may not use for ``purpose``). Blocking."""
    from hive.accounts import session_for
    return session_for(acct, region, purpose)


def _boto_config(timeout: float):
    from core import aws_account
    t = max(1.0, float(timeout))
    # no automatic retry: a throttled start moves to the next location (D6), and a start
    # resent after a timeout is refused by the supervisor as a replay
    return aws_account.client_config(connect_timeout=min(10.0, t), read_timeout=t,
                                     retries={"max_attempts": 1, "mode": "standard"})


def _error_code(exc: BaseException) -> Optional[str]:
    resp = getattr(exc, "response", None)
    if isinstance(resp, dict):
        code = (resp.get("Error") or {}).get("Code")
        return str(code) if code else None
    return None


def _owner(bucket: str) -> dict:
    from hive.provisioner import bucket_owner
    return bucket_owner(bucket)


#: how many times one round may be started (a start that failed, or whose answer never
#: came back, uses its attempt up: its session id is never asked for again)
MAX_ATTEMPTS = 20


def session_id_for(dispatch_id: str, round: int, attempt: int = 1) -> str:
    """The runtime session id of one attempt at one round of a dispatch: derived from the
    three, at least SID_MIN_LEN characters. Each start of a round is a new attempt
    (``AgentCoreLocation.open_run``): a session id that was asked for once is never asked
    for again, because its microVM's supervisor refuses a second start (``started``) and a
    released one is marked ended here."""
    from core.hive_records import DISPATCH_RE
    if not DISPATCH_RE.fullmatch(dispatch_id or "") or type(round) is not int or not 1 <= round <= 9999 \
            or type(attempt) is not int or not 1 <= attempt <= MAX_ATTEMPTS:
        raise ValueError("not a dispatch id, round and attempt")
    sid = f"swarm-out-{dispatch_id}-round-{round:04d}" + (f"-try-{attempt:02d}" if attempt > 1 else "")
    if len(sid) < SID_MIN_LEN:
        sid += "-" + "0" * (SID_MIN_LEN - len(sid) - 1)
    return sid


def _tried_path(sid: str) -> Path:
    if not SID_RE.fullmatch(sid or ""):
        raise ValueError("not a session id")
    d = _state_dir() / "agentcore" / "tried"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{sid}.json"


def _next_attempt(dispatch_id: str, round: int) -> str:
    """The session id of the round's next attempt, recorded here as asked for before the
    start call is made (so an answer that never came back still uses the attempt up)."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        sid = session_id_for(dispatch_id, round, attempt)
        p = _tried_path(sid)
        try:
            fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        except FileExistsError:
            continue
        os.close(fd)                          # its existence is the record
        return sid
    raise AgentCoreError(f"round {round} of {dispatch_id} was started {MAX_ATTEMPTS} times; "
                         "discard the run and send it again")


def parse_runtime_arn(arn: Any) -> Optional[dict]:
    """{partition, region, account, runtime_id} of a runtime ARN, or None."""
    m = RUNTIME_ARN_RE.fullmatch(arn) if isinstance(arn, str) else None
    if not m:
        return None
    return {"partition": m.group(1), "region": m.group(2), "account": m.group(3), "runtime_id": m.group(4)}


def _endpoint(url: Any, region: str) -> str:
    """The session endpoint URL, checked: https, a host under the region's AgentCore
    session domain (``<id>.runtime.bedrock-agentcore.<region>.on.aws``, as InvokeAgentRuntime
    returns it: never a host an AWS customer controls, since every request to it carries a
    signature and the run credential), no credentials, port, query or fragment."""
    if not isinstance(url, str) or len(url) > 2048 or not REGION_RE.fullmatch(region or ""):
        raise AgentCoreError("Elastic Workers gave no session endpoint")
    p = urlsplit(url.strip())
    host = (p.hostname or "").lower()
    labels = host.split(".")
    domain = f".runtime.bedrock-agentcore.{region}.on.aws"
    if p.scheme != "https" or p.username or p.password or p.port is not None or p.query or p.fragment \
            or not all(_HOST_LABEL_RE.fullmatch(x) for x in labels) \
            or not host.endswith(domain) or len(labels) != len(domain.split(".")):
        raise AgentCoreError("Elastic Workers gave an unexpected session endpoint")
    return urlunsplit(("https", host, p.path.rstrip("/"), "", ""))


def port_url(endpoint: str, region: str, port: int = BACKEND_PORT) -> str:
    """The session endpoint for another port: its first host label prefixed ``<port>--``."""
    p = urlsplit(_endpoint(endpoint, region))
    first, _, rest = p.hostname.partition(".")
    first = _PORT_PREFIX_RE.sub("", first)
    return urlunsplit(("https", f"{port}--{first}.{rest}", p.path, "", ""))


def _image_tag(uri: Any) -> Optional[str]:
    """The tag of a container image URI (``...:<tag>``, also ``...:<tag>@<digest>`` as the
    Elastic Workers' runtime is made: hive/agentcore_provisioner.pinned_uri), None for a bare
    digest or no tag."""
    if not isinstance(uri, str):
        return None
    last = uri.partition("@")[0].rsplit("/", 1)[-1]
    return last.rsplit(":", 1)[1] if ":" in last else None


# ── the run credential (this machine only) ───────────────────────────────────────

def _cred_dir() -> Path:
    d = _state_dir() / "run_creds"
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    st = os.lstat(d)
    if not stat.S_ISDIR(st.st_mode) or (hasattr(os, "getuid") and st.st_uid != os.getuid()):
        raise AgentCoreError("the run credential directory is not this user's own directory")
    if st.st_mode & 0o077:
        os.chmod(d, 0o700)
    return d


def _cred_path(dispatch_id: str, round: int) -> Path:
    session_id_for(dispatch_id, round)            # a dispatch id and round, before a path is built
    return _cred_dir() / f"{dispatch_id}-{round}.json"


def _read_cred(path: Path) -> dict:
    fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_mode & 0o077 \
                or (hasattr(os, "getuid") and st.st_uid != os.getuid()):
            raise AgentCoreError("the run credential file is not private to this user")
        with os.fdopen(os.dup(fd), "r", encoding="utf-8") as f:
            body = json.loads(f.read(4096))
    finally:
        os.close(fd)
    if not (isinstance(body, dict) and isinstance(body.get("user"), str)
            and isinstance(body.get("password"), str) and len(body["password"]) >= 32):
        raise AgentCoreError("the run credential file does not read")
    return {"user": body["user"], "password": body["password"]}


def run_credential(dispatch_id: str, round: int, create: bool = False) -> dict:
    """The round's run credential ``{user, password}``. ``create``: make it when there is
    none (an existing one is kept: a start resent for the round must carry the same).
    Blocking (a small file)."""
    path = _cred_path(dispatch_id, round)
    if create and not path.exists():
        # written whole to a private temporary file, then linked into place: the name
        # never shows a partial file, and a credential already there is never replaced
        body = json.dumps({"user": RUN_USER, "password": secrets.token_urlsafe(32)})
        tmp = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            try:
                os.write(fd, body.encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)
            try:
                os.link(tmp, path)
            except FileExistsError:
                pass                                # made meanwhile: that one is the round's
        finally:
            tmp.unlink(missing_ok=True)
    try:
        return _read_cred(path)
    except FileNotFoundError:
        raise AgentCoreError("this run's credential is not on this machine") from None
    except ValueError:
        raise AgentCoreError("the run credential file does not read") from None


def _drop_cred(dispatch_id: str, round: int) -> None:
    _cred_path(dispatch_id, round).unlink(missing_ok=True)


def _basic(cred: dict) -> str:
    return "Basic " + base64.b64encode(f"{cred['user']}:{cred['password']}".encode()).decode()


# ── what this machine knows of a round's end ─────────────────────────────────────

def _ended_path(sid: str) -> Path:
    if not SID_RE.fullmatch(sid or ""):
        raise ValueError("not a session id")
    d = _state_dir() / "agentcore" / "ended"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{sid}.json"


def _mark_ended(sid: str, why: str) -> None:
    p = _ended_path(sid)
    if p.exists():
        return
    tmp = p.with_name(f".{p.name}.{secrets.token_hex(4)}.tmp")
    tmp.write_text(json.dumps({"why": why, "at": _now()}), encoding="utf-8")
    os.replace(tmp, p)


def _marked_end(sid: str) -> Optional[str]:
    """The end recorded here, or None; a marker that exists but does not read is an end
    (failing open would send to a stopped session)."""
    p = _ended_path(sid)
    try:
        body = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return "ended"
    why = body.get("why") if isinstance(body, dict) else None
    return why if why in _END_REASONS else "ended"


def released_at(sid: str) -> Optional[float]:
    """When this Host released the session (``release``: StopRuntimeSession), from its end
    marker; None when it was not released here or the marker does not read. Read by
    core/machine_cost: a released session stops billing then."""
    try:
        body = json.loads(_ended_path(sid).read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    if not isinstance(body, dict) or body.get("why") != "released":
        return None
    at = body.get("at")
    return float(at) if isinstance(at, (int, float)) and not isinstance(at, bool) else None


#: ends the round's own session wrote (state.json ``ended`` / ``replaced``, a ``closed``
#: records entry): it writes nothing after them, so a read that starts once this host has
#: seen one holds everything the round will ever hold (``writes_end_at``)
_SESSION_ENDS = ("closed", "ended", "carrier_replaced")


def _session_end_seen_at(sid: str) -> Optional[float]:
    """When this host first saw an end the round's session wrote itself
    (``_SESSION_ENDS``, from its end marker), or None: no marker, another end
    (``released``, ``carrier_expired``), or a marker that does not read."""
    try:
        body = json.loads(_ended_path(sid).read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    if not isinstance(body, dict) or body.get("why") not in _SESSION_ENDS:
        return None
    at = body.get("at")
    return float(at) if isinstance(at, (int, float)) and not isinstance(at, bool) else None


def _expired(handle: RunHandle) -> bool:
    return not isinstance(handle.opened_at, (int, float)) \
        or _now() - float(handle.opened_at) > MAX_RUN_S - END_MARGIN_S


def _no_more_writes_at(sid: Any) -> Optional[float]:
    """When this machine recorded an end after which the round writes nothing more
    (``NO_MORE_WRITES``), or None."""
    try:
        body = json.loads(_ended_path(str(sid or "")).read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    if not isinstance(body, dict) or body.get("why") not in NO_MORE_WRITES or not _finite(body.get("at")):
        return None
    return float(body["at"])


def writes_end_at(handle: RunHandle) -> Optional[float]:
    """When the round can have written nothing more to its S3 prefix: its microVM lives
    at most MAX_RUN_S from the session's start, which is no later than START_TIMEOUT_S
    after ``opened_at``, and the clocks may differ by END_MARGIN_S (``WRITE_GRACE_S``);
    sooner when S3 said so (``NO_MORE_WRITES``, recorded with the time it was read), or
    when the session ended itself before that (an idle end: state.json ``ended``, a
    ``closed`` entry, ``replaced``): the time this host first saw that end
    (``_session_end_seen_at``), since the session wrote it last. None for a handle
    without a start time."""
    at = handle.opened_at if isinstance(handle, RunHandle) else None
    if not isinstance(at, (int, float)) or isinstance(at, bool):
        return None
    bound = float(at) + MAX_RUN_S + WRITE_GRACE_S
    try:
        seen = _session_end_seen_at(handle.run_ref or "")
    except ValueError:                       # not a session id: only the lifetime bounds it
        seen = None
    sooner = _no_more_writes_at(handle.run_ref)
    return min(x for x in (bound, seen, sooner) if x is not None)


def _ended_here(handle: RunHandle) -> Optional[str]:
    """Why the round has ended, from what this machine knows alone (no I/O but a small
    file): past the lifetime, or an end recorded here."""
    if _expired(handle):
        return "carrier_expired"
    return _marked_end(handle.run_ref or "")


def _parse_handle(handle: RunHandle) -> tuple[str, str, int]:
    """(session id, dispatch id, round) of an AgentCore handle; ValueError otherwise."""
    if not isinstance(handle, RunHandle) or handle.carrier != AGENTCORE:
        raise ValueError("not an AgentCore run handle")
    m = AUTH_REF_RE.fullmatch(handle.auth_ref or "")
    if not m or int(m.group(2)) != handle.round:
        raise ValueError("the handle names no run credential of its round")
    sid = handle.run_ref or ""
    if sid not in {session_id_for(m.group(1), handle.round, a) for a in range(1, MAX_ATTEMPTS + 1)}:
        raise ValueError("the handle's session is not its dispatch's")
    return sid, m.group(1), handle.round


def _bucket_of(handle: RunHandle) -> str:
    bucket = (handle.extra or {}).get("bucket")
    if not isinstance(bucket, str) or not BUCKET_RE.fullmatch(bucket):
        raise AgentCoreError("the run's handle names no bucket")
    return bucket


def _region_of(handle: RunHandle) -> str:
    region = (handle.extra or {}).get("region")
    if not isinstance(region, str) or not REGION_RE.fullmatch(region):
        raise AgentCoreError("the run's handle names no region")
    return region


# ── S3 reads (blocking: callers run them in a worker thread) ──────────────────────

def _get_bytes(s3: Any, bucket: str, key: str, cap: int) -> Optional[bytes]:
    """An object's bytes up to ``cap`` (ValueError past it); None when it is not there."""
    try:
        r = s3.get_object(Bucket=bucket, Key=key, **_owner(bucket))
    except Exception as exc:  # noqa: BLE001 — classified below
        if _error_code(exc) in ("NoSuchKey", "404", "NotFound"):
            return None
        raise
    length = r.get("ContentLength")
    if isinstance(length, int) and length > cap:
        raise ValueError("too_large")
    data = r["Body"].read(cap + 1)
    if len(data) > cap:
        raise ValueError("too_large")
    return data


def _get_json(s3: Any, bucket: str, key: str, cap: int) -> Any:
    data = _get_bytes(s3, bucket, key, cap)
    if data is None:
        return None
    try:
        return json.loads(data)
    except ValueError:
        raise ValueError("unexpected_body") from None


#: sid -> the last manifest key already read by an end check (manifest objects are
#: written once and never change, so each check reads only the ones after it)
_manifest_seen: dict[str, str] = {}


def _finite(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _alive_at(s3: Any, bucket: str, sid: str, st: Any) -> Optional[tuple[float, float]]:
    """(the round's newest sign of life, its heartbeat cadence) from ``heartbeat.json`` and
    a ``crashed`` state.json's ``at``; None when the round gives neither (an image older
    than the heartbeat: no staleness is judged). Worker text: anything else is no sign."""
    try:
        hb = _get_json(s3, bucket, f"bridge/{sid}/heartbeat.json", SMALL_OBJECT_MAX)
    except ValueError:
        hb = None
    times: list[float] = []
    every = float(HEARTBEAT_S)
    if isinstance(hb, dict) and _finite(hb.get("at")):
        times.append(float(hb["at"]))
        if _finite(hb.get("every")) and 0 < hb["every"] <= HEARTBEAT_MAX_S:
            every = float(hb["every"])
    if isinstance(st, dict) and st.get("state") == "crashed" and _finite(st.get("at")):
        times.append(float(st["at"]))
    return (max(times), every) if times else None


def _ended_in_s3(s3: Any, bucket: str, sid: str) -> Optional[str]:
    """Why the round's S3 prefix says it ended, else None: ``carrier_replaced`` / ``ended``
    / ``expired_sealed`` / ``crashed`` (final) from state.json, ``closed`` when its records
    manifest holds a closed entry, ``heartbeat_lost`` when its newest sign of life is older
    than its cadence + IDLE_TIMEOUT_S + END_MARGIN_S. Reads state.json, the manifest
    objects not read before and the heartbeat; raises when S3 does not answer (no answer
    is no proof the round is open)."""
    st = _get_json(s3, bucket, f"bridge/{sid}/state.json", SMALL_OBJECT_MAX)
    word = st.get("state") if isinstance(st, dict) else None
    if word == "replaced":
        return "carrier_replaced"
    if word == "ended":
        return "ended"
    if word == "expired":
        return "expired_sealed"
    if word == "crashed" and st.get("restarting") is False:
        return "crashed"
    prefix = f"bridge/{sid}/records/manifest/"
    after = _manifest_seen.get(sid, prefix)
    for _ in range(LIST_PAGES_MAX):
        page = s3.list_objects_v2(Bucket=bucket, Prefix=prefix, StartAfter=after, **_owner(bucket))
        keys = sorted(k for k in (o.get("Key") or "" for o in page.get("Contents") or [])
                      if k.startswith(prefix) and _MANIFEST_KEY_RE.search(k) and k > after)
        for key in keys:
            try:
                e = _get_json(s3, bucket, key, SMALL_OBJECT_MAX)
            except ValueError:
                e = None
            if isinstance(e, dict) and e.get("kind") == "closed":
                return "closed"
            after = key
            _manifest_seen[sid] = key
        if not page.get("IsTruncated") or not keys:
            break
    else:
        raise AgentCoreError("the round's record manifest is too long to read in one check")
    alive = _alive_at(s3, bucket, sid, st)
    if alive is not None and _now() - alive[0] > alive[1] + IDLE_TIMEOUT_S + END_MARGIN_S:
        return "heartbeat_lost"
    return None


def _manifest_page(s3: Any, bucket: str, sid: str, after: int, limit: int) -> tuple[list[Any], bool]:
    """The manifest entries after seq ``after`` in seq order (up to ``limit``), and whether
    more follow. An entry whose object does not read is given as None (it stops the pull)."""
    prefix = f"bridge/{sid}/records/manifest/"
    page = s3.list_objects_v2(Bucket=bucket, Prefix=prefix, StartAfter=f"{prefix}{after:06d}.json",
                              MaxKeys=limit + 1, **_owner(bucket))
    keys = sorted(k for k in (o.get("Key") or "" for o in page.get("Contents") or [])
                  if _MANIFEST_KEY_RE.search(k) and k.startswith(prefix))
    out: list[Any] = []
    for key in keys[:limit]:
        try:
            e = _get_json(s3, bucket, key, SMALL_OBJECT_MAX)
        except ValueError:
            e = None
        # the object's name and its entry must agree on the seq (a renamed object is no entry)
        if not isinstance(e, dict) or e.get("seq") != int(_MANIFEST_KEY_RE.search(key).group(1)):
            e = None
        out.append(e)
        if e is None:
            break
    return out, len(keys) > limit or bool(page.get("IsTruncated"))


def _get_entry_file(s3: Any, bucket: str, key: str, cap: int, dest: Path) -> tuple[str, int]:
    """Stream an entry's bytes to ``dest`` (a temporary path): (sha256, size); ValueError
    past ``cap`` or when the object is not there."""
    try:
        r = s3.get_object(Bucket=bucket, Key=key, **_owner(bucket))
    except Exception as exc:  # noqa: BLE001 — classified below
        if _error_code(exc) in ("NoSuchKey", "404", "NotFound"):
            raise ValueError("entry_missing") from None
        raise
    length = r.get("ContentLength")
    if isinstance(length, int) and length > cap:
        raise ValueError("too_large")
    h = hashlib.sha256()
    size = 0
    body = r["Body"]
    with open(dest, "wb") as f:
        while True:
            chunk = body.read(1 << 20)
            if not chunk:
                break
            size += len(chunk)
            if size > cap:
                raise ValueError("too_large")
            h.update(chunk)
            f.write(chunk)
        f.flush()
        os.fsync(f.fileno())
    return h.hexdigest(), size


ROUND_MARK = ".round.json"
ROUNDS_DIR = "rounds"


def _record_dir_for_round(dispatch_id: str, round: int) -> Optional[Path]:
    """The dispatch's kept record directory for ``round`` (core/hive_records.local_dir).
    A later round's record starts again at seq 1 (a new session), so the earlier round's
    kept record is moved, whole, to ``rounds/<n>/`` inside it first (never deleted; the
    readers read the current round's). None when the kept record is of a later round."""
    from core import hive_records
    d = hive_records.local_dir(dispatch_id)
    mark = d / ROUND_MARK
    kept_round = 1
    try:
        body = json.loads(mark.read_text(encoding="utf-8"))
        if isinstance(body, dict) and type(body.get("round")) is int:
            kept_round = body["round"]
    except (OSError, ValueError):
        pass
    if kept_round > round:
        return None
    if kept_round < round and d.is_dir():
        aside = d / ROUNDS_DIR / str(kept_round)
        aside.mkdir(parents=True, exist_ok=True)
        for p in list(d.iterdir()):
            if p.name != ROUNDS_DIR and not p.name.endswith(".lock"):
                os.replace(p, aside / p.name)
    if kept_round != round or not mark.exists():
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f".round.{secrets.token_hex(4)}.tmp"
        tmp.write_text(json.dumps({"round": round}), encoding="utf-8")
        os.replace(tmp, mark)
    return d


def _pull_s3(row: dict, dispatch_id: str, round: int, s3: Any, bucket: str, sid: str) -> dict:
    """``core.hive_records.pull`` for a round whose record is in S3: the same checks on
    each entry, the same local copy, the same steps after it. Blocking."""
    from core import hive_records as hr
    d0 = hr.local_dir(dispatch_id)
    # one S3 pull of a dispatch at a time: the move of an earlier round and the pull
    # itself (a lock file is never moved aside)
    with hr._Lock(d0, ".s3-pull.lock"):
        return _pull_s3_locked(row, dispatch_id, round, s3, bucket, sid)


def _pull_s3_locked(row: dict, dispatch_id: str, round: int, s3: Any, bucket: str, sid: str) -> dict:
    from core import hive_records as hr
    from core.hive_files import note_not_sealed, place_pending
    d = _record_dir_for_round(dispatch_id, round)
    if d is None:
        return {**hr._UNSUPPORTED, "supported": True, "error": "older_round"}
    out: dict[str, Any] = {"supported": True, "error": None, "drained": False}
    more = True
    try:
        for _ in range(hr.PULL_PAGES):
            kept = hr.local_entries(dispatch_id) if (d / hr.MANIFEST).exists() else []
            cur = kept[-1]["seq"] if kept else 0
            listed, more = _manifest_page(s3, bucket, sid, cur, hr.PAGE)
            d.mkdir(parents=True, exist_ok=True)
            used = hr._kept_bytes(d, kept)
            for e in listed:
                why = hr._valid_entry(e, cur + 1) if e is not None else "unexpected_body"
                if not why and e["kind"] not in hr.ALWAYS_KEPT and (
                        len(kept) >= hr.MAX_KEPT_ENTRIES or used + e["size"] > hr.MAX_KEPT_BYTES):
                    why = "record_too_large"
                if why == "record_too_large" and e["kind"] in ("file", "turn"):
                    entry = {**{k: e[k] for k in ("seq", "kind", "sha256", "size", "path") if k in e},
                             "file": hr._name(e["seq"], "file"), "at": hr._now(), "skipped": why,
                             **({"listed": e["listed"]} if "listed" in e else {})}
                    hr._append_line(d, entry)
                    kept.append(entry)
                    cur = e["seq"]
                    continue
                if why:
                    out["error"] = why
                    break
                name = hr._name(e["seq"], e["kind"], e.get("key"))
                tmp = d / f".tmp-{secrets.token_hex(6)}"
                try:
                    sha, size = _get_entry_file(s3, bucket, f"bridge/{sid}/records/{name}", e["size"], tmp)
                    if sha != e["sha256"] or size != e["size"]:
                        raise ValueError("sha_mismatch")
                    os.replace(tmp, d / name)
                except ValueError as exc:
                    tmp.unlink(missing_ok=True)
                    out["error"] = str(exc)[:40]
                    break
                except BaseException:
                    tmp.unlink(missing_ok=True)
                    raise
                entry = {"seq": e["seq"], "kind": e["kind"], "file": name, "sha256": sha,
                         "size": size, "at": hr._now(), **({"key": e["key"]} if e["kind"] == "bundle" else {}),
                         **({"path": e["path"]} if e["kind"] == "file" else {}),
                         **({"listed": e["listed"]} if e["kind"] == "file" and "listed" in e else {})}
                hr._append_line(d, entry)
                kept.append(entry)
                used += size
                cur = e["seq"]
            if out["error"] or not more:
                break
        if d.is_dir():
            hr._prune_bundles(d)
            place_pending(row, d)
            note_not_sealed(d)
        # every entry the round listed is read here: what is not here was not in S3 now
        out["drained"] = out["error"] is None and not more
    except Exception as exc:  # noqa: BLE001 — S3 slow or unreadable: keep what is kept, retry next check
        logger.warning("agentcore: pulling %s from S3 failed (%s)", dispatch_id, type(exc).__name__)
        if not ((d / hr.MANIFEST).exists() and hr.local_entries(dispatch_id)):
            return dict(hr._UNSUPPORTED)
        out["error"] = "hive_silent"
        out["drained"] = False
    return {**out, **hr.state(dispatch_id)}


def _check_lines(body: Any, work_id: str) -> Optional[list[dict]]:
    """The check record of the work order *work_id* in a round's checks.json
    (``{"at", "orders": [{"work_id", "checks"}, ...]}``, core/hive_agentcore
    ``_checks_body``), as ``swarm_out_results`` keeps check records (same cleaning as its
    HTTP read); None when the body lists no such order. The order is picked by its id,
    the one the Host sent it under (the dispatch id), never by its place in the list."""
    from core.swarm_out_results import MAX_CHECK_LINES
    orders = body.get("orders") if isinstance(body, dict) else None
    mine = [o for o in orders if isinstance(o, dict) and o.get("work_id") == work_id] \
        if isinstance(orders, list) else []
    lines = mine[-1].get("checks") if mine else None
    if not isinstance(lines, list):
        return None
    keep = []
    for x in lines[-MAX_CHECK_LINES:]:
        if not isinstance(x, dict) or not isinstance(x.get("id"), str) or len(x["id"]) > 100:
            continue
        code = x.get("exit")
        keep.append({"id": x["id"],
                     "exit": code if isinstance(code, int) and not isinstance(code, bool) else None,
                     "started_at": x.get("started_at") if isinstance(x.get("started_at"), str) else None,
                     "ended_at": x.get("ended_at") if isinstance(x.get("ended_at"), str) else None})
    return keep


# ── the location ─────────────────────────────────────────────────────────────────

async def _location_rows(location_id: Optional[str] = None) -> list[tuple[dict, Optional[dict]]]:
    """Every AgentCore location row (or the one with ``location_id``) with its account row."""
    import aiosqlite
    async with aiosqlite.connect(_db_path()) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA busy_timeout = 3000")
        sql = ("SELECT * FROM hive_instances WHERE carrier = ? AND runtime_arn IS NOT NULL"
               + (" AND id = ?" if location_id is not None else "") + " ORDER BY name")
        cur = await conn.execute(sql, (AGENTCORE, location_id) if location_id is not None else (AGENTCORE,))
        rows = [dict(r) for r in await cur.fetchall()]
        out = []
        for inst in rows:
            cur = await conn.execute("SELECT * FROM hive_accounts WHERE id = ?", (inst.get("account_ref"),))
            acct = await cur.fetchone()
            out.append((inst, dict(acct) if acct is not None else None))
    return out


def _active_sessions(location_id: str) -> int:
    """Rounds this Host opened at the location that have not ended as far as it knows
    (the platform has no per-runtime session count to ask). A round that closed in S3 but
    was not looked at since still counts, up to its lifetime: an upper bound, so the
    offer may say ``at_quota`` early, never late. Blocking."""
    from core import swarm_out
    n = 0
    for row in swarm_out._read_log():
        try:
            for h in rounds_of(row):
                if h.carrier == AGENTCORE and h.location_id == location_id and not _ended_here(h):
                    n += 1
        except (TypeError, ValueError):
            continue
    return n


_quota_cache: dict[tuple[str, str], tuple[float, int]] = {}


def _session_quota(session: Any, account: str, region: str) -> int:
    """The account's active-session quota (Service Quotas), cached; DEFAULT_SESSION_QUOTA
    when it cannot be read. Blocking."""
    got = _quota_cache.get((account, region))
    if got and time.monotonic() - got[0] < QUOTA_CACHE_S:
        return got[1]
    value = DEFAULT_SESSION_QUOTA
    try:
        sq = session.client("service-quotas", region_name=region, config=_boto_config(10))
        kw: dict[str, Any] = {"ServiceCode": SERVICE}
        for _ in range(5):
            page = sq.list_service_quotas(**kw)
            for q in page.get("Quotas") or []:
                name = str(q.get("QuotaName") or "").lower()
                if "active" in name and "session" in name and isinstance(q.get("Value"), (int, float)):
                    value = int(q["Value"])
                    break
            else:
                if page.get("NextToken"):
                    kw["NextToken"] = page["NextToken"]
                    continue
            break
    except Exception as exc:  # noqa: BLE001 — the platform's refusal at the start still decides
        logger.info("agentcore: session quota not read (%s)", type(exc).__name__)
    _quota_cache[(account, region)] = (time.monotonic(), value)
    return value


class AgentCoreLocation:
    """The account's AgentCore runtime (one ``hive_instances`` row)."""

    carrier = AGENTCORE

    def __init__(self, location_id: str, name: Optional[str] = None,
                 row: Optional[tuple[dict, Optional[dict]]] = None):
        self.id = location_id
        self.name = name or location_id
        self._row = row

    async def _load(self) -> tuple[dict, dict, dict]:
        """(row, account row, parsed ARN), read now; AgentCoreError when unusable."""
        rows = await _location_rows(self.id)
        if len(rows) != 1:
            raise AgentCoreError("no Elastic Workers with this id")
        inst, acct = rows[0]
        arn = parse_runtime_arn(inst.get("runtime_arn"))
        if acct is None or arn is None or not BUCKET_RE.fullmatch(inst.get("s3_bucket") or ""):
            raise AgentCoreError(f"Elastic Workers {inst.get('name')} has no account, runtime or bucket")
        self.name = str(inst.get("name") or self.id)
        self._row = (inst, acct)
        return inst, acct, arn

    def _readiness(self, session: Any, inst: dict, arn: dict) -> dict[str, Any]:
        """Runtime status, image tag, active sessions and quota; ``blocked`` when the
        runtime cannot take a run now. Blocking."""
        out: dict[str, Any] = {"status": None, "image": None, "active": None, "quota": None, "blocked": None}
        try:
            ctl = session.client(CONTROL_SERVICE, region_name=arn["region"], config=_boto_config(15))
            rt = ctl.get_agent_runtime(agentRuntimeId=arn["runtime_id"])
        except Exception as exc:  # noqa: BLE001 — not answering: offered as blocked, never tried
            logger.info("agentcore: %s not read (%s)", inst.get("name"), type(exc).__name__)
            out["blocked"] = "unreachable"
            return out
        out["status"] = rt.get("status")
        out["image"] = _image_tag(((rt.get("agentRuntimeArtifact") or {}).get("containerConfiguration") or {})
                                  .get("containerUri"))
        out["active"] = _active_sessions(self.id)
        out["quota"] = _session_quota(session, arn["account"], arn["region"])
        # a row being turned on, updated or turned off (hive/agentcore_provisioner) takes no
        # run, even while the runtime still answers READY
        if out["status"] != "READY" or inst.get("status") in CHANGING_ROW_STATUSES:
            out["blocked"] = "not_ready"
        elif (out["image"] or "").removeprefix("v") != _host_version():
            out["blocked"] = "image_version"
        elif out["active"] >= out["quota"]:
            out["blocked"] = "at_quota"
        return out

    async def probe(self) -> dict[str, Any]:
        """The runtime's state and session count; touches no run."""
        inst, acct, arn = await self._load()
        session = await asyncio.to_thread(_session_for, acct, arn["region"], "read")
        got = await asyncio.to_thread(self._readiness, session, inst, arn)
        return {"carrier": AGENTCORE, "name": self.name, **got}

    async def open_run(self, dispatch_id: str, round: int, deadline: Any,
                       needs: Optional[Needs] = None) -> RunHandle:
        """Start the round's session and return its handle. ``LocationUnavailable`` only
        when the runtime refused before anything was written (throttled, at its quota,
        too little time left); any other failure is an AgentCoreError. *needs*: nothing
        in it is the location's to refuse (every round has its own microVM and Brain
        copy: no Brain is held here). The handle names its chat session
        (``extra["session_id"]``, ``RunHandle.session``) None: the backend names it when
        the work order's session starts."""
        session_id_for(dispatch_id, round)            # a dispatch id and round, before anything else
        left = deadline.left() if hasattr(deadline, "left") else (
            float(deadline) if isinstance(deadline, (int, float)) else START_TIMEOUT_S)
        ends_at = time.monotonic() + left
        if left < MIN_START_S:
            raise LocationUnavailable(f"{self.name}: too little time left to start a session")
        inst, acct, arn = await self._load()
        bucket = inst["s3_bucket"]
        try:
            session = await asyncio.to_thread(_session_for, acct, arn["region"], "manage")
        except Exception as exc:  # noqa: BLE001 — an account this Host cannot use: nothing written
            raise LocationUnavailable(f"{self.name}: {type(exc).__name__}") from None
        cred = await asyncio.to_thread(run_credential, dispatch_id, round, True)
        # a new attempt for every start: an earlier one may have been released, or started
        # without its answer coming back (its supervisor then refuses a second start)
        sid = await asyncio.to_thread(_next_attempt, dispatch_id, round)
        payload = {"action": "start", "sid": sid, "bucket": bucket,
                   "hive_user": cred["user"], "hive_password": cred["password"]}
        # before the call: the platform's lifetime starts at the session's start, which is
        # no earlier than this
        opened_at = _now()
        # the start blocks until the microVM's backend is up (about 17 s): on its own pool
        # (several dispatches start at once), and a step of the dispatch's ``timings``
        from core import executors
        started = time.monotonic()
        start_ends = min(ends_at, started + START_TIMEOUT_S)

        def start() -> tuple[Any, Optional[str]]:
            # its time is what is left once a thread of the pool is free: a wait behind
            # other rounds' starts is not added on top of the dispatch's deadline
            t = start_ends - time.monotonic()
            if t < MIN_START_S:
                raise LocationUnavailable(f"{self.name}: too little time left to start a session")
            return self._invoke(session, inst["runtime_arn"], arn["region"], sid, payload, t)
        try:
            answer, url = await executors.run_in("elastic_start", start)
        finally:
            if hasattr(deadline, "took"):
                deadline.took(f"opening a round on Elastic Workers {self.name}", started)
        if not isinstance(answer, dict) or answer.get("started") is not True:
            why = answer.get("why") if isinstance(answer, dict) and isinstance(answer.get("why"), str) else None
            raise AgentCoreError(f"Elastic Workers {self.name} refused the start"
                                 + (f" ({why[:40]})" if why else "") + "; the run was not started again")
        try:
            address = _endpoint(url, arn["region"])
        except AgentCoreError:
            # the session started: it is stopped here, or its microVM runs on, billed, to
            # its idle timeout with nothing able to reach it
            await executors.run_in("elastic_stop", self._stop_quietly, session, inst["runtime_arn"],
                                   arn["region"], sid)
            raise
        return RunHandle(carrier=AGENTCORE, location_id=self.id, round=round, run_ref=sid,
                         address=address, auth_ref=f"run_cred:{dispatch_id}:{round}",
                         opened_at=opened_at,
                         extra={"bucket": bucket, "region": arn["region"], "runtime_arn": inst["runtime_arn"],
                                "session_id": None})

    @staticmethod
    def _stop_quietly(session: Any, runtime_arn: str, region: str, sid: str) -> None:
        """StopRuntimeSession for a session this Host started but cannot use. Blocking; a
        failure is logged (the session then ends at its idle timeout)."""
        try:
            session.client(SERVICE, region_name=region, config=_boto_config(30)).stop_runtime_session(
                runtimeSessionId=sid, agentRuntimeArn=runtime_arn)
        except Exception as exc:  # noqa: BLE001 — the start's own error is the one raised
            logger.warning("agentcore: stopping session %s failed (%s)", sid, _error_code(exc) or type(exc).__name__)
        _mark_ended(sid, "released")

    def _invoke(self, session: Any, runtime_arn: str, region: str, sid: str, payload: dict,
                timeout: float) -> tuple[Any, Optional[str]]:
        """InvokeAgentRuntime(start): (parsed answer, session endpoint URL). Blocking. The
        payload carries the run credential: it is never logged."""
        client = session.client(SERVICE, region_name=region, config=_boto_config(timeout))
        try:
            r = client.invoke_agent_runtime(agentRuntimeArn=runtime_arn, runtimeSessionId=sid,
                                            payload=json.dumps(payload).encode("utf-8"),
                                            contentType="application/json", accept="application/json")
        except Exception as exc:  # noqa: BLE001 — classified below
            code = _error_code(exc)
            if code in UNAVAILABLE_CODES:
                raise LocationUnavailable(
                    f"{self.name}: {'at_quota' if code == 'ServiceQuotaExceededException' else 'throttled'}") from None
            raise AgentCoreError(f"Elastic Workers {self.name} did not start the session "
                                 f"({code or type(exc).__name__})") from None
        url = r.get("sessionEndpointUrl")
        if not url:
            headers = ((r.get("ResponseMetadata") or {}).get("HTTPHeaders") or {})
            url = next((v for k, v in headers.items() if k.lower() == ENDPOINT_HEADER), None)
        body = r.get("response")
        raw = body.read(START_ANSWER_MAX + 1) if hasattr(body, "read") else (body or b"")
        try:
            answer = json.loads(raw) if raw and len(raw) <= START_ANSWER_MAX else None
        except ValueError:
            answer = None
        return answer, url


# ── the client bound to one round ────────────────────────────────────────────────

def _sign(request: httpx.Request, frozen: Any, region: str) -> None:
    """SigV4 over the method, URL and host only, with the empty-payload hash (no
    ``X-Amz-Content-Sha256`` header: the platform then checks the empty-payload hash)."""
    from core import aws_account
    for k in ("X-Amz-Content-Sha256", "Authorization", "X-Amz-Date", "X-Amz-Security-Token"):
        if k in request.headers:
            del request.headers[k]
    request.headers.update(aws_account.sigv4_headers(
        request.method, str(request.url), request.url.netloc.decode("ascii"), frozen,
        service=SERVICE, region=region))


class RunClient(httpx.AsyncClient):
    """A client bound to one round: before every send it makes sure the round has not
    ended (a request to a stopped session boots a new empty microVM), then its request
    hook signs the request."""

    def __init__(self, *, carrier: "AgentCoreCarrier", handle: RunHandle, **kw: Any):
        self._carrier, self._handle = carrier, handle
        self._frozen: Any = None
        self._run_auth: Optional[str] = None
        self._host = httpx.URL(kw["base_url"]).host
        hooks = kw.pop("event_hooks", {}) or {}
        hooks = {"request": [self._sign_hook, *hooks.get("request", [])],
                 "response": list(hooks.get("response", []))}
        super().__init__(event_hooks=hooks, **kw)

    async def _sign_hook(self, request: httpx.Request) -> None:
        if self._frozen is None:
            raise AgentCoreError("the request was not checked before it was signed")
        if request.url.scheme != "https" or request.url.host != self._host:
            raise AgentCoreError("refused to sign a request for another host")
        _sign(request, self._frozen, _region_of(self._handle))

    async def send(self, request: httpx.Request, **kw: Any) -> httpx.Response:
        follow = kw.get("follow_redirects")
        if not isinstance(follow, bool):                # httpx's "use the client's default"
            follow = self.follow_redirects
        if follow:
            raise AgentCoreError("a client of an Elastic Workers runtime run follows no redirect")
        if request.url.scheme != "https" or request.url.host != self._host:
            raise AgentCoreError("this client sends only to its run's session")
        why = await self._carrier.ended(self._handle)
        if why:
            raise RunEnded(why)
        if self._run_auth is None:
            _sid, did, rnd = _parse_handle(self._handle)
            self._run_auth = _basic(await asyncio.to_thread(run_credential, did, rnd))
        request.headers[AUTH_HEADER] = self._run_auth
        # the session endpoint hands a request with no Content-Length (an httpx GET) to the
        # container in a form the backend's server refuses (uvicorn: 400 "Invalid HTTP request
        # received", measured on a live runtime); a request with no body says so
        if "content-length" not in request.headers and "transfer-encoding" not in request.headers:
            request.headers["Content-Length"] = "0"
        session = await self._carrier._session(self._handle, "manage")
        self._frozen = await asyncio.to_thread(lambda: session.get_credentials().get_frozen_credentials())
        return await super().send(request, **kw)


# ── the carrier ──────────────────────────────────────────────────────────────────

class AgentCoreCarrier:
    """Elastic Workers: one AgentCore Runtime session per run round."""

    kind = AGENTCORE
    reaches_private = False          # no internal network or credentials reach the microVM
    max_run_s: Optional[int] = MAX_RUN_S
    brain_lock_scope = "run"         # every round has its own microVM and Brain copy
    #: every round is a machine of its own (core/carriers/base.Carrier): its state and
    #: results are read here from S3, and a dispatch that fails after open_run releases it.
    #: One duty: a round's machine logs are read from CloudWatch (``log_events``). No idle
    #: stop (a microVM ends itself), no Brains kept between runs, usage read with each
    #: result (``usage``), not by a timer.
    run_scoped = True
    duties = frozenset({DIAGNOSTICS})

    def __init__(self, transport: Optional[httpx.AsyncBaseTransport] = None) -> None:
        self._names: dict[str, str] = {}
        self._sessions: dict[tuple[str, str], tuple[float, Any]] = {}
        self._transport = transport          # the clients' transport (tests); None: httpx's own
        #: (location, purpose, region) -> (the boto3 session it was made from, its S3 client):
        #: a client is thread-safe, a session is not, so each is made once, under the lock
        self._s3_clients: dict[tuple[str, str, str], tuple[Any, Any]] = {}
        self._s3_lock = threading.Lock()

    async def offers(self, needs: Needs) -> list[Offer]:
        """One tier-2 offer per runtime in an account this Host can manage; blocked
        ``not_ready`` / ``image_version`` / ``at_quota`` / ``unreachable`` when it cannot
        take a run now (placement decides, and records why)."""
        out: list[Offer] = []
        for inst, acct in await _location_rows():
            arn = parse_runtime_arn(inst.get("runtime_arn"))
            if acct is None or arn is None or not BUCKET_RE.fullmatch(inst.get("s3_bucket") or ""):
                continue
            try:
                session = await asyncio.to_thread(_session_for, acct, arn["region"], "manage")
            except Exception as exc:  # noqa: BLE001 — not an account this Host can manage: not offered
                logger.info("agentcore: %s not offered (%s)", inst.get("name"), type(exc).__name__)
                continue
            loc = AgentCoreLocation(str(inst["id"]), str(inst.get("name") or inst["id"]), (inst, acct))
            self._names[loc.id] = loc.name
            got = await asyncio.to_thread(loc._readiness, session, inst, arn)
            out.append(Offer(carrier=AGENTCORE, location_id=loc.id, name=loc.name, tier=TIER_ELASTIC,
                             open_runs=got["active"] or 0, start_s=START_S, reaches_private=self.reaches_private,
                             brain_lock_scope=self.brain_lock_scope, blocked=got["blocked"]))
        return out

    def location(self, location_id: str) -> AgentCoreLocation:
        return AgentCoreLocation(location_id, self._names.get(location_id))

    def writes_end_at(self, handle: RunHandle) -> Optional[float]:
        """When the round can have written nothing more (module ``writes_end_at``)."""
        return writes_end_at(handle)

    async def log_events(self, handle: RunHandle, since: float, until: float,
                         terms: list[str]) -> dict[str, Any]:
        """The round's CloudWatch log events between *since* and *until* (epoch seconds):
        every event of the runtime's log groups that names one of *terms* (the round's
        session ids), and every event of a stream whose name carries one. ``{"groups",
        "events": [{"at", "stream", "message"}] oldest first, "total", "truncated"}``; no
        groups when the runtime has none. Read through the location's account session
        (``read``); AgentCoreError when CloudWatch refuses or fails. Sends nothing to the
        session."""
        arn = parse_runtime_arn((handle.extra or {}).get("runtime_arn"))
        if arn is None:
            raise AgentCoreError("the run's handle names no runtime")
        clean = [t for t in terms if isinstance(t, str) and _LOG_TERM_RE.fullmatch(t)]
        if not clean:
            raise ValueError("no session id this read can use")
        session = await self._session(handle, "read")
        return await asyncio.to_thread(_read_logs, session, _region_of(handle), arn["runtime_id"], clean,
                                       int(since * 1000), int(until * 1000))

    async def _session(self, handle: RunHandle, purpose: str) -> Any:
        """The boto3 session of the handle's location for ``purpose`` (cached)."""
        key = (handle.location_id, purpose)
        got = self._sessions.get(key)
        if got is None or time.monotonic() - got[0] > SESSION_CACHE_S:
            rows = await _location_rows(handle.location_id)
            if len(rows) != 1 or rows[0][1] is None:
                raise AgentCoreError("the run's Elastic Workers is no longer known here")
            # resolved again after SESSION_CACHE_S: an account that changed (another
            # account now, disconnected) is noticed (hive/accounts.session_for)
            got = (time.monotonic(), await asyncio.to_thread(_session_for, rows[0][1], _region_of(handle), purpose))
            self._sessions[key] = got
        return got[1]

    async def _s3(self, handle: RunHandle, purpose: str = "read") -> Any:
        """The S3 client of the handle's location for ``purpose``: made once per boto3
        session (again when the session is resolved again), with S3_TIMEOUT_S."""
        session = await self._session(handle, purpose)
        region = _region_of(handle)
        key = (handle.location_id, purpose, region)
        got = self._s3_clients.get(key)
        if got is not None and got[0] is session:
            return got[1]

        def make() -> Any:
            with self._s3_lock:
                had = self._s3_clients.get(key)
                if had is not None and had[0] is session:
                    return had[1]
                client = session.client("s3", region_name=region, config=_boto_config(S3_TIMEOUT_S))
                self._s3_clients[key] = (session, client)
                return client
        return await asyncio.to_thread(make)

    async def bridge(self, handle: RunHandle, purpose: str = "read") -> tuple[Any, str, str]:
        """(S3 client for *purpose*, bucket, name) of the round's bridge,
        ``bridge/<name>/`` with the runtime session as its name: where the round's Hive
        writes its waits (core/swarm_out_bridge in AgentCore mode) and the Host answers
        them (``answer/``). Touches neither S3 nor the session here."""
        try:
            sid, _did, _rnd = _parse_handle(handle)
        except ValueError as exc:
            raise AgentCoreError(f"this run's elastic round cannot be addressed ({exc})") from None
        return await self._s3(handle, purpose), _bucket_of(handle), sid

    async def ended(self, handle: RunHandle) -> Optional[str]:
        """Why the round has ended, or None: an end recorded here, then S3 (read on every
        call; an end found is recorded here for good), and only when S3 shows none the
        round's age (``carrier_expired``): a round past its lifetime whose result is in S3
        ended the way S3 says. Sends nothing to the session; raises when S3 does not answer
        and the round is not past its lifetime."""
        sid, _did, _rnd = _parse_handle(handle)
        why = await asyncio.to_thread(_marked_end, sid)
        if why:
            return why
        try:
            s3 = await self._s3(handle)
            # read before every request to the round: its own pool, never the default one
            from core import executors
            why = await executors.run_in("elastic_s3", _ended_in_s3, s3, _bucket_of(handle), sid)
        except Exception:
            if _expired(handle):         # whatever S3 holds, nothing runs past the lifetime
                return "carrier_expired"
            raise
        if why:
            if why not in UNRECORDED_ENDS:
                await asyncio.to_thread(_mark_ended, sid, why)
            return why
        if not _expired(handle):
            return None
        # past the lifetime and no end in S3: recorded once nothing can be written any more
        # (until then a closed entry may still arrive and say how it ended)
        opened = float(handle.opened_at) if _finite(handle.opened_at) else None
        if opened is None or _now() > opened + MAX_RUN_S + WRITE_GRACE_S:
            await asyncio.to_thread(_mark_ended, sid, "carrier_expired")
        return "carrier_expired"

    async def connect(self, handle: RunHandle) -> dict[str, Any]:
        """What the post-open steps and the per-run readers reach the round through
        (``registry.client_for``): ``{id, name, carrier, _handle}``, its client the
        round's ``RunClient``. Sends nothing to the session. RunEnded when the round has
        ended (what this machine knows, then S3): from then on no request may reach it;
        AgentCoreError when its location or S3 cannot be read."""
        try:
            _parse_handle(handle)
        except ValueError as exc:
            raise AgentCoreError(f"this run's elastic round cannot be addressed ({exc})") from None
        try:
            why = await self.ended(handle)
        except AgentCoreError:
            raise
        except Exception as exc:  # noqa: BLE001 — no answer from S3 is no proof the round is open
            raise AgentCoreError(f"Elastic Workers' state could not be read ({type(exc).__name__})") from None
        if why:
            raise RunEnded(why)
        name = self._names.get(handle.location_id)
        if name is None:
            rows = await _location_rows(handle.location_id)
            name = str(rows[0][0].get("name") or handle.location_id) if len(rows) == 1 else handle.location_id
            self._names[handle.location_id] = name
        return {"id": handle.location_id, "name": name, "carrier": AGENTCORE, "_handle": handle}

    def client(self, handle: RunHandle, timeout: Any = None) -> RunClient:
        """A client of the round's backend (port 8081 of its session endpoint). Raises
        RunEnded at once when the round is past its lifetime or this machine recorded its
        end (callers rely on that refusal before any client exists: one small marker file
        is read, on the event loop); every send checks S3 for an end, then reads the run
        credential off the event loop (``RunClient.send``). *timeout*: httpx's (seconds
        or an ``httpx.Timeout``); None: the Hive clients' default."""
        from core import swarm_out
        if timeout is None:
            timeout = swarm_out.HTTP_TIMEOUT_S
        _parse_handle(handle)
        why = _ended_here(handle)
        if why:
            raise RunEnded(why)
        base = port_url(handle.address or "", _region_of(handle), BACKEND_PORT)
        return RunClient(carrier=self, handle=handle, base_url=base,
                         headers=dict(swarm_out.HIVE_HEADERS),
                         timeout=timeout, follow_redirects=False,
                         event_hooks={"response": swarm_out.hive_event_hooks()["response"]},
                         **({"transport": self._transport} if self._transport is not None else {}))

    async def outputs(self, handle: RunHandle, dispatch_id: str, row: Optional[dict] = None) -> dict[str, Any]:
        """Take the round's new record entries from S3 into this machine's kept record
        (``hive_records.pull``'s result: ``{supported, seq, closed, last_at, error}``) and
        its check records (``check_records``, None when it wrote none)."""
        sid, did, rnd = _parse_handle(handle)
        if did != dispatch_id:
            raise ValueError("the handle is not this dispatch's")
        if row is None:
            from core import swarm_out
            row = await asyncio.to_thread(swarm_out._log_row_of, dispatch_id)
        bucket = _bucket_of(handle)
        try:
            s3 = await self._s3(handle)
        except Exception as exc:  # noqa: BLE001 — as hive_records.pull maps an unusable Hive
            from core import hive_records as hr
            logger.warning("agentcore: no S3 client for %s (%s)", dispatch_id, type(exc).__name__)
            kept = await asyncio.to_thread(hr.local_entries, dispatch_id)
            if not kept:
                return {**hr._UNSUPPORTED, "check_records": None}
            return {"supported": True, "error": "hive_unusable",
                    **await asyncio.to_thread(hr.state, dispatch_id), "check_records": None}
        rec = await asyncio.to_thread(_pull_s3, {**row, "dispatch_id": dispatch_id}, dispatch_id, rnd, s3, bucket, sid)
        try:
            checks = _check_lines(await asyncio.to_thread(_get_json, s3, bucket, f"bridge/{sid}/checks.json", CHECKS_MAX),
                                  dispatch_id)
        except Exception as exc:  # noqa: BLE001 — keep what an earlier read kept
            logger.warning("agentcore: check record of %s not read (%s)", dispatch_id, type(exc).__name__)
            checks = None
        return {**rec, "check_records": checks}

    async def state(self, handle: RunHandle) -> tuple[str, str]:
        """(state, reason) of the round, from S3, then its age (``ended``): stopped
        ``closed`` / ``idle`` / ``carrier_expired`` / ``carrier_replaced``; else the
        round's state.json."""
        sid, _did, _rnd = _parse_handle(handle)
        try:
            why = await self.ended(handle)
        except Exception as exc:  # noqa: BLE001 — S3 unreadable: say so, nothing settles on it
            logger.info("agentcore: state of %s not read (%s)", sid, type(exc).__name__)
            return "unknown", "hive_silent"
        if why:
            return "stopped", _END_REASONS.get(why, "idle")
        try:
            s3 = await self._s3(handle)
            st = await asyncio.to_thread(_get_json, s3, _bucket_of(handle), f"bridge/{sid}/state.json", SMALL_OBJECT_MAX)
        except Exception as exc:  # noqa: BLE001
            logger.info("agentcore: state of %s not read (%s)", sid, type(exc).__name__)
            return "unknown", "hive_silent"
        word = st.get("state") if isinstance(st, dict) else None
        if word in _STATES:
            return _STATES[word]
        return ("unknown", "starting") if _now() - float(handle.opened_at or 0) < 10 * START_S else ("unknown", "hive_silent")

    async def usage(self, handle: RunHandle, dispatch_id: str) -> dict[str, Any]:
        """The round's usage rows (``usage.json``) into this machine's usage ledger, each
        keyed by the dispatch and the row's id (``hive:<dispatch>:<rid>``; a later round
        ``hive:<dispatch>.r<n>:<rid>``: each microVM numbers its rows from 1). Rewritten
        whole by the round, so reading it again stores nothing twice."""
        from core import usage_accounting, usage_hive_sync
        sid, did, rnd = _parse_handle(handle)
        if did != dispatch_id:
            raise ValueError("the handle is not this dispatch's")
        s3 = await self._s3(handle)
        body = await asyncio.to_thread(_get_json, s3, _bucket_of(handle), f"bridge/{sid}/usage.json", USAGE_MAX)
        raw = body.get("rows") if isinstance(body, dict) else None
        if not isinstance(raw, list):
            return {"rows": 0, "stored": True}
        name = usage_hive_sync._text(self._names.get(handle.location_id)) or "elastic"
        key = dispatch_id if rnd == 1 else f"{dispatch_id}.r{rnd}"
        rows = [r for r in (usage_hive_sync.clean(x, name, key, lambda _s: dispatch_id) for x in raw) if r is not None]
        stored = await asyncio.to_thread(usage_accounting.insert_now, rows) if rows else True
        return {"rows": len(rows), "stored": bool(stored)}

    async def release(self, handle: RunHandle, dispatch_id: str) -> None:
        """Stop the round's session (StopRuntimeSession; a session already gone counts as
        stopped) and forget its run credential. Recorded as ended first, so no client
        sends to it from then on."""
        sid, did, rnd = _parse_handle(handle)
        if did != dispatch_id:
            raise ValueError("the handle is not this dispatch's")
        await asyncio.to_thread(_mark_ended, sid, "released")
        # ended here from now on: its credential has no use left, whatever the stop does
        await asyncio.to_thread(_drop_cred, did, rnd)
        session = await self._session(handle, "stop")
        arn = (handle.extra or {}).get("runtime_arn")
        if parse_runtime_arn(arn) is None:
            raise AgentCoreError("the run's handle names no runtime")

        def stop() -> None:
            client = session.client(SERVICE, region_name=_region_of(handle), config=_boto_config(30))
            try:
                client.stop_runtime_session(runtimeSessionId=sid, agentRuntimeArn=arn)
            except Exception as exc:  # noqa: BLE001 — classified below
                if _error_code(exc) not in NOT_FOUND_CODES:
                    raise AgentCoreError(f"Elastic Workers session was not stopped "
                                         f"({_error_code(exc) or type(exc).__name__})") from None
        from core import executors
        await executors.run_in("elastic_stop", stop)

    async def next_round(self, handle: RunHandle, dispatch_id: str, deadline: Any = None) -> RunHandle:
        """Where a correction of the run goes (D7; core/carriers/base.Carrier): *handle*
        itself while its session is open (the correction goes into it), else a new round
        (``open_next_round``'s), which the caller appends to ``rounds`` once it took the
        correction. Sends nothing to the session: whether it ended is read here and from
        S3. AgentCoreError when that cannot be read or the new round cannot be opened."""
        from core import swarm_out
        try:
            _sid, did, _rnd = _parse_handle(handle)
        except ValueError as exc:
            raise AgentCoreError(f"this run's elastic round cannot be addressed ({exc})") from None
        if did != dispatch_id:
            raise AgentCoreError("the handle is not this dispatch's")
        row = await asyncio.to_thread(swarm_out._log_row_of, dispatch_id)
        cur = current_handle(row)
        if (cur.carrier, cur.round, cur.run_ref) != (handle.carrier, handle.round, handle.run_ref):
            raise AgentCoreError("the run has a later round than the one named")
        try:
            why = await self.ended(handle)
        except AgentCoreError:
            raise
        except Exception as exc:  # noqa: BLE001 — no answer is no proof either way: nothing is sent
            raise AgentCoreError(f"Elastic Workers' state could not be read ({type(exc).__name__}); "
                                 "nothing was sent") from None
        if not why:
            return handle
        return await self._open_next(row, handle, deadline)

    async def open_next_round(self, row: dict, correction: str, deadline: Any = None) -> RunHandle:
        """A correction after the round's session ended (D7): a new session as the next
        round, its repositories starting at the previous round's returned commits
        (``refs/hive/<dispatch>/<key>``, each shipped pinned). Nothing is sent to the old
        session. Returns the new handle for the caller to append to ``rounds``; its
        ``extra["repos"]`` names each repository's start (``{url, ref, sha, name}``) for
        the work order the caller prepares with the correction."""
        from core import swarm_out
        text = (correction or "").strip()
        if not text:
            raise AgentCoreError("say what to change")
        if len(text) > swarm_out.MAX_TASK_CHARS:
            raise AgentCoreError(f"the correction is longer than {swarm_out.MAX_TASK_CHARS} characters")
        prev = current_handle(row)
        if prev.carrier != AGENTCORE:
            raise AgentCoreError("the run's last round is not on Elastic Workers")
        _parse_handle(prev)
        if not await self.ended(prev):
            raise AgentCoreError("the run's session is still open: send the correction into it")
        return await self._open_next(row, prev, deadline)

    async def _open_next(self, row: dict, prev: RunHandle, deadline: Any) -> RunHandle:
        """The next round after *prev* (ended): its repositories packed at the returned
        commits, its session started, each bundle put into its inbox. A failure after the
        start releases the new round."""
        from core import repo_ship
        did = str(row.get("dispatch_id") or "")
        starts = await asyncio.to_thread(_returned_starts, did, row)
        packs: list[tuple[dict, Optional[Path], int]] = []
        handle: Optional[RunHandle] = None
        try:
            for s in starts:
                if not s.get("checkout"):
                    packs.append((s, None, 0))
                    continue
                try:
                    path, _base, size = await asyncio.to_thread(
                        repo_ship.pack, Path(s["checkout"]), s["url"], s["ref"], s["sha"], pinned=True)
                except repo_ship.ShipError as e:
                    raise AgentCoreError(f"{s.get('name') or s['url']} could not be packed: {e}; nothing was sent") from None
                packs.append((s, path, size))
            handle = await self.location(prev.location_id).open_run(did, prev.round + 1, deadline or START_TIMEOUT_S)
            for s, path, size in packs:
                if path is not None:
                    await self._put_repo(handle, s, path, size)
        except BaseException:
            if handle is not None:
                try:
                    await self.release(handle, did)
                except Exception as exc:  # noqa: BLE001 — it ends on its own when idle
                    logger.warning("agentcore: releasing a failed round of %s failed (%s)", did, type(exc).__name__)
            raise
        finally:
            for _s, path, _n in packs:
                if path is not None:
                    await asyncio.to_thread(path.unlink, missing_ok=True)
        repos = [{"url": s["url"], "ref": s["ref"], "sha": s["sha"], **({"name": s["name"]} if s.get("name") else {})}
                 for s in starts]
        return RunHandle(**{**handle.to_row(), "extra": {**handle.extra, "repos": repos, "after_round": prev.round}})

    async def _put_repo(self, handle: RunHandle, s: dict, path: Path, size: int) -> None:
        """One bundle into the new round's repository inbox (``PUT /api/hive/repos``), kept
        where the work order looks for it, or AgentCoreError."""
        from core import hive_repo_inbox
        data = await asyncio.to_thread(path.read_bytes)
        try:
            async with self.client(handle, SHIP_TIMEOUT_S) as client:
                resp = await client.put("/api/hive/repos", params={"url": s["url"], "branch": s["ref"], "sha": s["sha"]},
                                        content=data, headers={"Content-Type": "application/octet-stream"})
        except httpx.HTTPError as e:
            raise AgentCoreError(f"Elastic Workers did not take {s.get('name') or s['url']} ({type(e).__name__})") from None
        try:
            body = resp.json()
        except ValueError:
            body = None
        if resp.status_code != 200 or not (isinstance(body, dict) and body.get("bundle") == hive_repo_inbox.bundle_rel(s["sha"])
                                           and body.get("bytes") == size):
            raise AgentCoreError(f"Elastic Workers did not keep {s.get('name') or s['url']} ({resp.status_code})")


#: a session id put into a CloudWatch filter pattern (inside quotes): letters, digits,
#: ``_`` and ``-`` only, so it can never close the quote or add a term
_LOG_TERM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,79}$")


def _read_logs(session: Any, region: str, runtime_id: str, terms: list[str],
               start_ms: int, end_ms: int) -> dict[str, Any]:
    """``AgentCoreCarrier.log_events``, blocking. Per log group there are two reads (the
    events naming a term; a stream named by the session, read whole), each given its share
    of the calls left, so one busy group never takes the others' reads; calls left after
    that go to the reads that were cut. Each read goes from the window's end backwards
    (``newest``): CloudWatch pages a time range oldest first, so a range whose events do
    not fit in one page is narrowed to its newer half until one does, taken whole, and the
    end moved back to its start, the next range twice as wide as that piece; the round's
    last lines are therefore read first in every group. The read stops at LOG_CALLS_MAX calls
    or LOG_READ_BUDGET_S (``truncated``, as when a read did not reach the window's start,
    or held LOG_EVENTS_MAX events first). The newest LOG_EVENTS_MAX events are kept."""
    deadline = time.monotonic() + LOG_READ_BUDGET_S
    calls = 0
    truncated = False
    seen: dict[tuple, dict] = {}

    def spend() -> bool:
        """One more call may be made (counted here)."""
        nonlocal calls, truncated
        if calls >= LOG_CALLS_MAX or time.monotonic() >= deadline:
            truncated = True
            return False
        calls += 1
        return True

    try:
        logs = session.client("logs", region_name=region, config=_boto_config(LOG_TIMEOUT_S))

        def page_of(base: dict, lo: int, hi: int, token: Optional[str] = None) -> Optional[str]:
            """One filter page of [lo, hi] kept in ``seen``; its next token."""
            kw = {**base, "startTime": lo, "endTime": hi, **({"nextToken": token} if token else {})}
            page = logs.filter_log_events(**kw)
            for e in page.get("events") or []:
                msg, at = e.get("message"), e.get("timestamp")
                if not isinstance(msg, str) or not isinstance(at, int):
                    continue
                stream = str(e.get("logStreamName") or "")
                seen.setdefault((stream, at, e.get("eventId") or msg),
                                {"at": at, "stream": stream, "message": msg.rstrip("\n"), "read": id(base)})
            got = page.get("nextToken")
            return got if isinstance(got, str) and got else None

        def newest(base: dict, st: dict, share: int) -> bool:
            """Read one read's window from its end backwards within *share* calls, from where
            it stopped (``st``: ``hi``, the end still to read; ``width``, the next range's
            width: twice the last piece one page held). True when it reached the start."""
            nonlocal truncated
            used = 0

            def go() -> bool:
                nonlocal used
                if used >= share or not spend():
                    return False
                used += 1
                return True
            while st["hi"] > start_ms:
                hi = st["hi"]
                # events already held from the window's end back to ``hi``
                if sum(1 for e in seen.values() if e["read"] == id(base) and e["at"] >= hi) >= LOG_EVENTS_MAX:
                    st["full"] = truncated = True
                    return False
                lo = max(start_ms, hi - st["width"]) if st.get("width") else start_ms
                if not go():
                    return False
                token = page_of(base, lo, hi)
                while token and hi - lo > LOG_MIN_PIECE_MS:      # narrow to the newer half
                    if not go():
                        # a later pass goes on with the next, narrower piece (this one's
                        # first page is held)
                        st["width"] = max((hi - lo) // 2, LOG_MIN_PIECE_MS)
                        return False
                    lo = (lo + hi) // 2
                    token = page_of(base, lo, hi)
                while token:                                     # a piece too short to narrow
                    if not go():
                        st["width"] = hi - lo
                        return False
                    token = page_of(base, lo, hi, token)
                # [lo, hi] is read whole: the next range, as wide again twice over, ends before it
                st["width"] = 2 * max(hi - lo, LOG_MIN_PIECE_MS)
                st["hi"] = lo - 1 if lo > start_ms else start_ms
            return True

        prefix = f"{LOG_GROUP_ROOT}{runtime_id}-"
        groups: list[str] = []
        kw: dict[str, Any] = {"logGroupNamePrefix": prefix}
        for _ in range(LOG_PAGES_MAX):
            if not spend():
                break
            page = logs.describe_log_groups(**kw)
            groups += [g["logGroupName"] for g in page.get("logGroups") or []
                       if isinstance(g.get("logGroupName"), str) and g["logGroupName"].startswith(prefix)]
            if not page.get("nextToken"):
                break
            kw["nextToken"] = page["nextToken"]
        groups = sorted(set(groups))[:LOG_GROUPS_MAX]
        pattern = " ".join(f'?"{t}"' for t in terms)
        reads: list[dict[str, Any]] = []
        for g in groups:
            reads.append({"logGroupName": g, "filterPattern": pattern})
            if not spend():
                break
            page = logs.describe_log_streams(logGroupName=g, orderBy="LastEventTime", descending=True, limit=50)
            named = [x["logStreamName"] for x in page.get("logStreams") or []
                     if isinstance(x.get("logStreamName"), str) and any(t in x["logStreamName"] for t in terms)]
            if named:
                reads.append({"logGroupName": g, "logStreamNames": named[:LOG_STREAMS_MAX]})
        states = [{"hi": end_ms} for _ in reads]
        # each read gets its share of the calls left (a read that needs fewer leaves them to
        # the next); then the calls still left go to the reads that were cut, in turn
        cut = list(range(len(reads)))
        for _ in range(2):
            todo, cut = cut, []
            for n, i in enumerate(todo):
                if calls >= LOG_CALLS_MAX:
                    cut.append(i)
                    continue
                if not newest(reads[i], states[i], max(1, (LOG_CALLS_MAX - calls) // (len(todo) - n))) \
                        and not states[i].get("full"):
                    cut.append(i)
            if not cut or calls >= LOG_CALLS_MAX or time.monotonic() >= deadline:
                break
        if cut:
            truncated = True
    except Exception as exc:  # noqa: BLE001 — classified for the caller
        raise AgentCoreError(f"CloudWatch Logs did not answer ({_error_code(exc) or type(exc).__name__})") from None
    events = sorted(({k: v for k, v in e.items() if k != "read"} for e in seen.values()),
                    key=lambda e: (e["at"], e["stream"]))
    total = len(events)
    return {"groups": groups, "events": events[-LOG_EVENTS_MAX:], "total": total,
            "truncated": truncated or total > LOG_EVENTS_MAX}


def _returned_starts(dispatch_id: str, row: dict) -> list[dict]:
    """Where each of the dispatch's repositories starts in the next round: the commit at
    ``refs/hive/<dispatch>/<key>`` in its checkout, imported there now when the round's
    newest bundle of it is named by no turn record (``hive_return.import_unrecorded``: a
    turn its backend's death or the lifetime cut off), else when the previous round's
    commits were taken back (core/hive_return), else the dispatch's own start (``{url, ref,
    sha}``; no checkout: nothing to ship, the remote has it). Blocking."""
    from core import hive_return, repo_ship
    states: dict[str, dict] = {}
    try:
        d = hive_return._state_dir(dispatch_id)
        for p in sorted(d.glob("*.json")) if d.is_dir() else []:
            try:
                st = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(st, dict) and isinstance(st.get("url"), str):
                states[st["url"]] = st
    except hive_return.ReturnError:
        pass
    out = []
    for r in row.get("repos") or []:
        if not isinstance(r, dict) or not isinstance(r.get("url"), str):
            continue
        try:
            got = hive_return.import_unrecorded(dispatch_id, row, r)
        except hive_return.ReturnError as e:
            raise AgentCoreError(f"{r.get('name') or r['url']}: the last round's newest commits could not "
                                 f"be taken back ({e}); nothing was sent") from None
        if got is not None:
            out.append({"url": r["url"], "ref": got["branch"].removeprefix("refs/heads/"), "sha": got["tip"],
                        "checkout": got["repo"], "name": r.get("name"), "from": got["ref"]})
            continue
        st = states.get(r["url"])
        ref = f"refs/hive/{dispatch_id}/{st.get('key')}" if st else None
        if st and st.get("ref") == ref and isinstance(st.get("repo"), str) and isinstance(st.get("branch"), str):
            try:
                sha = repo_ship.resolve(Path(st["repo"]), ref)
            except repo_ship.ShipError as e:
                raise AgentCoreError(f"{r.get('name') or r['url']}: the last round's commits are gone "
                                     f"from this machine ({e}); nothing was sent") from None
            out.append({"url": r["url"], "ref": st["branch"], "sha": sha, "checkout": st["repo"],
                        "name": r.get("name"), "from": ref})
        else:
            out.append({"url": r["url"], "ref": r.get("ref"), "sha": r.get("sha"), "name": r.get("name")})
    return out


# ── provisioning ─────────────────────────────────────────────────────────────────

#: what a round may write under its own prefix (design §6.7); answer/ is the Host's
SESSION_WRITES = ("started", "state.json", "checks.json", "usage.json", "decisions.json",
                  "heartbeat.json", "ask/*", "closed/*", "records/*")


def execution_role_policy(account: str, region: str, bucket: str, image_repo: Optional[str]) -> dict:
    """The runtime execution role's policy: Bedrock invoke, its logs, pulling its image,
    and S3 only under the session's own ``bridge/<sid>/`` (``aws:AgentCoreRuntimeSourceSid``):
    put on what a round writes, get on its prefix, list with that prefix; ``answer/`` is
    read only. Requires the V2 platform (the key is set per session).

    ``image_repo``: the account's private ECR repository the runtime pulls its image from
    (``hive/agentcore_provisioner.image_location``), or None for an image on public ECR,
    which is pulled without credentials: the role then has no ECR permission at all."""
    if not ACCOUNT_RE.fullmatch(account or "") or not REGION_RE.fullmatch(region or "") \
            or not BUCKET_RE.fullmatch(bucket or "") \
            or (image_repo is not None and not IMAGE_REPO_RE.fullmatch(image_repo)):
        raise ValueError("not an account, region, bucket and image repository")
    part = "aws-cn" if region.startswith("cn-") else "aws-us-gov" if region.startswith("us-gov-") else "aws"
    prefix = "bridge/${aws:AgentCoreRuntimeSourceSid}"
    obj = f"arn:{part}:s3:::{bucket}/{prefix}"
    policy: dict = {
        "Version": "2012-10-17",
        "Statement": [
            {"Sid": "BedrockInvoke", "Effect": "Allow",
             "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
             # models and inference profiles (a cross-region profile routes to any region);
             # not provisioned throughput or another account's resources
             "Resource": [f"arn:{part}:bedrock:*::foundation-model/*",
                          f"arn:{part}:bedrock:*:{account}:inference-profile/*",
                          f"arn:{part}:bedrock:*:{account}:application-inference-profile/*"]},
            {"Sid": "Logs", "Effect": "Allow",
             "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents",
                        "logs:DescribeLogStreams"],
             "Resource": [f"arn:{part}:logs:{region}:{account}:log-group:/aws/bedrock-agentcore/runtimes/*",
                          f"arn:{part}:logs:{region}:{account}:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*"]},
            {"Sid": "LogGroups", "Effect": "Allow", "Action": ["logs:DescribeLogGroups"],
             "Resource": [f"arn:{part}:logs:{region}:{account}:log-group:*"]},
            {"Sid": "BridgeWrite", "Effect": "Allow", "Action": ["s3:PutObject"],
             "Resource": [f"{obj}/{p}" for p in SESSION_WRITES]},
            {"Sid": "BridgeRead", "Effect": "Allow", "Action": ["s3:GetObject"], "Resource": [f"{obj}/*"]},
            {"Sid": "BridgeList", "Effect": "Allow", "Action": ["s3:ListBucket"],
             "Resource": [f"arn:{part}:s3:::{bucket}"],
             "Condition": {"StringLike": {"s3:prefix": [f"{prefix}/*"]}}},
            {"Sid": "AnswersAreTheHosts", "Effect": "Deny", "Action": ["s3:PutObject", "s3:DeleteObject"],
             "Resource": [f"{obj}/answer/*"]},
        ],
    }
    if image_repo is not None:
        policy["Statement"][3:3] = [
            {"Sid": "ImagePull", "Effect": "Allow",
             "Action": ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
             "Resource": [f"arn:{part}:ecr:{region}:{account}:repository/{image_repo}"]},
            {"Sid": "ImageToken", "Effect": "Allow", "Action": ["ecr:GetAuthorizationToken"], "Resource": "*"},
        ]
    return policy
