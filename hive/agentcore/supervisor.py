"""AgentCore supervisor: the process an elastic Hive's microVM runs first.

Design docs/design/2026-11-20-swarm-out-multi-carrier.md §6.3 and §6.5. It serves the
AgentCore HTTP contract on 0.0.0.0:8080 and starts the SwarmAI Hive backend on
0.0.0.0:8081 once, on the first ``start``:

  GET  /ping         -> {"status": "Healthy" | "HealthyBusy", "time_of_last_update": <unix s>}
  POST /invocations  -> {"action": "start", "sid", "bucket", "hive_user", "hive_password"}

Why it is shaped this way:

* **Standard library only, and no AWS client in this process.** AgentCore V2 snapshots the
  microVM before any session exists and restores it per session; a client built before the
  snapshot carries no ``aws:AgentCoreRuntimeSourceSid`` and the per-session S3 prefix
  (§6.7) refuses it. Every AWS call therefore runs in a short subprocess of the backend's
  venv python (boto3), created after a start, and so does the backend itself.
* **Started once per session.** Before the backend starts, ``bridge/<sid>/started`` is
  written with ``IfNoneMatch="*"``. When it already exists, the start was re-sent to a new
  microVM or the platform replaced the microVM: ``state.json`` becomes ``replaced`` and the
  backend is not started, so a task never runs twice. Everything that can be refused or
  fail without AWS (the fields, the session id the platform sends, the password hash) is
  done before the marker, so such a failure leaves the session unclaimed.
* **Busy** = a start is in progress, or the started backend says it is busy:
  ``GET /api/hive/busy`` answering ``{"busy": false}`` is idle; no answer yet, a 5xx or any
  other body is busy; a 4xx (the backend cannot say) is idle and logged, never 8 billed
  hours of HealthyBusy. ``time_of_last_update`` is the time the status last changed, never
  refreshed per ping (the platform contract).
* **The password never leaves memory as text**: it reaches the hashing subprocess on stdin
  (not argv, which ``ps`` shows), the backend gets only the bcrypt hash, and nothing here
  logs a request body.
* **A backend that dies keeps what it produced** (``watch_once``, on a thread of its own
  once the backend is healthy). When the backend exits on its own, its process group is
  killed and reaped (``_reap``: what it started, the agent CLI or git, may outlive it), then
  the rescue runs (``rescue``: ``python -m core.hive_rescue`` in the backend venv, with the
  backend's environment, nothing of the backend running): it uploads every record entry written but not in S3,
  closes and seals the open turn with each declared repository's commits bundled (the EC2
  boot cleanup, core/hive_boot), and uploads those entries. Then ``state.json`` becomes
  ``{"state": "crashed", "exit", "log_tail", "at", "restarting", "rescue"}`` and the
  backend is started again, once (``MAX_RESTARTS``); a second death, or a restart that
  never becomes healthy, is rescued the same way and written with ``restarting: false``.
  ``PRE_LIMIT_MARGIN_S`` before the platform's lifetime (``MAX_LIFETIME_S`` from the
  start), a busy backend is stopped, rescued and ``state.json`` becomes ``expired``; an
  idle one has nothing open and is left alone. While a rescue runs, /ping is busy.
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
from base64 import b64encode
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 8080
BACKEND_HOST = "0.0.0.0"
BACKEND_PORT = 8081
BACKEND_DIR = "/opt/swarmai/backend"
VENV_PYTHON = f"{BACKEND_DIR}/.venv/bin/python"
SWARM_HOME = "/home/swarm"
LOG_DIR = f"{SWARM_HOME}/.swarm-ai/logs"
BACKEND_LOG = f"{LOG_DIR}/backend.log"
HEALTH_WAIT_S = 170.0
BCRYPT_COST = 14  # what hive_auth expects (backend/middleware/hive_auth.py)
HELPER_TIMEOUT_S = 60.0
#: the runtime's maxLifetime (hive/agentcore_provisioner.MAX_LIFETIME_S): the platform ends
#: the session this long after it started
MAX_LIFETIME_S = 28800
#: hang guard of one rescue: stopping the backend plus this must fit in PRE_LIMIT_MARGIN_S
RESCUE_TIMEOUT_S = 900.0
#: how long a stopped backend gets to exit before it is killed
STOP_WAIT_S = 30.0
#: the pre-limit seal runs this long before the lifetime ends (from this start's own time,
#: which is after the platform's: the session exists before its first invocation)
PRE_LIMIT_MARGIN_S = 1200
#: how often the watcher looks at the backend
WATCH_S = 5.0
#: restarts after an unexpected exit, per session
MAX_RESTARTS = 1
#: the backend log's tail kept in state.json (the Host reads at most 64 KiB of it)
LOG_TAIL_MAX = 8000

# S3 bucket names; a session id becomes one S3 key segment, so it may not hold a "/".
_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
# The backend's own rule (core/hive_agentcore.SID_RE: no ".", 33 to 256 characters): a start
# the backend could not serve is refused before anything is claimed.
_SID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{32,255}$")
# hive_auth splits Basic "user:password" at the first ":", so a user may not hold one.
_USER_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_BCRYPT_MAX_BYTES = 72  # bcrypt refuses a longer password
# The session id the platform puts on every request to the container.
SESSION_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"
MAX_BODY = 64 * 1024

# Exit code of the S3 helper when the object already exists (IfNoneMatch="*" refused it).
_EXISTS = 3

# Runs in the backend venv after a start. Prints only an error code, never a credential.
_S3_PUT = r"""
import sys
bucket, key, conditional = sys.argv[1], sys.argv[2], sys.argv[3] == "1"
kw = {"IfNoneMatch": "*"} if conditional else {}
try:
    import boto3
    from botocore.exceptions import ClientError
except Exception as e:
    print(type(e).__name__, file=sys.stderr)
    sys.exit(1)
try:
    boto3.client("s3").put_object(Bucket=bucket, Key=key, Body=sys.stdin.buffer.read(),
                                  ContentType="application/json", **kw)
except ClientError as e:
    code = e.response.get("Error", {}).get("Code", "")
    if conditional and code in ("PreconditionFailed", "ConditionalRequestConflict"):
        sys.exit(3)
    print(code or "ClientError", file=sys.stderr)
    sys.exit(1)
except Exception as e:
    print(type(e).__name__, file=sys.stderr)
    sys.exit(1)
"""

_HASH = (
    "import sys, bcrypt; "
    "print(bcrypt.hashpw(sys.stdin.buffer.read(), bcrypt.gensalt(int(sys.argv[1]))).decode())"
)

STATE: dict = {
    "proc": None,        # the backend's Popen, once started
    "starting": False,   # a start is in progress
    "marked": False,     # this process wrote bridge/<sid>/started
    "status": "Healthy",
    "changed": int(time.time()),
    "auth": None,        # Basic credential for the backend's own API (memory only)
    "report": None,      # the answer of the start that claimed this session
    "env": None,         # the backend's environment (the password's hash, never the password)
    "bridge": None,      # (bucket, sid) of this session's bridge prefix
    "started_at": None,  # when this start began (time.time())
    "restarts": 0,       # backends started again after an unexpected exit
    "rescuing": False,   # a rescue (or the pre-limit seal) is running
    "stopping": False,   # the supervisor itself stops the backend: its exit is no crash
    "expired": False,    # the pre-limit seal ran
}
LOCK = threading.Lock()


class HelperError(RuntimeError):
    """A subprocess in the backend venv failed; the message holds no credential."""


def backend_command() -> list[str]:
    return [VENV_PYTHON, "-m", "uvicorn", "main:app",
            "--host", BACKEND_HOST, "--port", str(BACKEND_PORT), "--log-level", "info"]


def backend_env(sid: str, bucket: str, user: str, pass_hash: str) -> dict:
    return dict(
        os.environ,
        HOME=SWARM_HOME,
        SWARMAI_MODE="hive",
        SWARMAI_PORT=str(BACKEND_PORT),
        SWARMAI_CARRIER="agentcore",
        SWARMAI_AC_SID=sid,
        SWARMAI_AC_BUCKET=bucket,
        HIVE_USER=user,
        HIVE_PASS_HASH=pass_hash,
        CLAUDE_CODE_USE_BEDROCK="1",
        CLAUDE_CODE_DISABLE_AUTO_MEMORY="1",
        SWARM_RESUME_VIA_QUERY="true",
        AWS_REGION=os.environ.get("AWS_REGION", "us-east-1"),
    )


def _helper(args: list[str], stdin: bytes) -> subprocess.CompletedProcess:
    try:
        return subprocess.run([VENV_PYTHON, *args], input=stdin, capture_output=True,
                              timeout=HELPER_TIMEOUT_S, cwd=BACKEND_DIR)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise HelperError(type(e).__name__) from None


def s3_put(bucket: str, key: str, obj: dict, *, if_absent: bool) -> bool:
    """Write ``obj`` as JSON to s3://bucket/key. With ``if_absent``, False when the object
    already exists. Raises HelperError on any other failure."""
    body = json.dumps(obj).encode()
    r = _helper(["-c", _S3_PUT, bucket, key, "1" if if_absent else "0"], body)
    if r.returncode == 0:
        return True
    if if_absent and r.returncode == _EXISTS:
        return False
    lines = (r.stderr or b"").decode(errors="replace").strip().splitlines()
    raise HelperError(lines[-1][-120:] if lines else f"exit {r.returncode}")


def hash_password(password: str) -> str:
    r = _helper(["-c", _HASH, str(BCRYPT_COST)], password.encode())
    out = (r.stdout or b"").decode().strip()
    if r.returncode != 0 or not out.startswith("$2"):
        raise HelperError(f"bcrypt exit {r.returncode}")
    return out


def http_get_json(url: str, headers: dict | None = None, timeout: float = 3.0):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode() or "null")


def _set_status(new: str) -> dict:
    with LOCK:
        if STATE["status"] != new:
            STATE["status"] = new
            STATE["changed"] = int(time.time())
        return {"status": STATE["status"], "time_of_last_update": STATE["changed"]}


def _running() -> bool:
    proc = STATE["proc"]
    return proc is not None and proc.poll() is None


def busy() -> bool:
    if STATE["starting"] or STATE["rescuing"]:
        return True
    if not _running():
        return False
    headers = {}
    if STATE["auth"]:
        # X-Swarm-Hive-Auth is what the backend reads behind the session endpoint (§6.3);
        # Authorization is what it reads elsewhere. Same credential, loopback only.
        headers = {"X-Swarm-Hive-Auth": STATE["auth"], "Authorization": STATE["auth"]}
    try:
        _, body = http_get_json(f"http://127.0.0.1:{BACKEND_PORT}/api/hive/busy", headers)
    except urllib.error.HTTPError as e:
        # The backend answered but cannot say (a 4xx: no such route, auth refused): counting
        # that as busy would hold the microVM, billed, to its 8-hour limit with nothing to
        # produce. A 5xx (hive_auth's 503 while busy checking) is transient: busy.
        if e.code >= 500:
            return True
        print(f"supervisor: /api/hive/busy answered {e.code}; not counted as busy", flush=True)
        return False
    except Exception:  # noqa: BLE001 - a started backend that does not answer is still working
        return True
    return not (isinstance(body, dict) and body.get("busy") is False)


def ping() -> dict:
    return _set_status("HealthyBusy" if busy() else "Healthy")


def _wait_healthy(proc, t0: float) -> float | None:
    while time.time() - t0 < HEALTH_WAIT_S:
        if proc.poll() is not None:
            return None
        try:
            status, _ = http_get_json(f"http://127.0.0.1:{BACKEND_PORT}/health", timeout=2.0)
            if status == 200:
                return round(time.time() - t0, 1)
        except Exception:  # noqa: BLE001 - not up yet
            pass
        time.sleep(0.5)
    return None


def _log_tail(n: int = 40) -> str:
    try:
        with open(BACKEND_LOG, "rb") as f:
            return b"\n".join(f.read().splitlines()[-n:]).decode(errors="replace")
    except OSError:
        return ""


def _refuse(body: dict, platform_sid: str | None) -> str | None:
    """Why this start is refused before anything is claimed, or None."""
    sid, bucket = body.get("sid"), body.get("bucket")
    user, password = body.get("hive_user"), body.get("hive_password")
    if not all(isinstance(v, str) and v for v in (sid, bucket, user, password)):
        return "sid, bucket, hive_user and hive_password are required"
    if not _SID_RE.match(sid) or not _BUCKET_RE.match(bucket):
        return "sid or bucket is not a valid name"
    # the platform names the session on every request it forwards; a start without that
    # name did not come through the session endpoint, and is refused
    if platform_sid is None or platform_sid != sid:
        return "sid is not this session's id"
    if not _USER_RE.match(user):
        return "hive_user may hold only letters, digits, '.', '_' and '-'"
    if len(password.encode()) > _BCRYPT_MAX_BYTES:
        return f"hive_password is longer than {_BCRYPT_MAX_BYTES} bytes"
    return None


def start(body: dict, platform_sid: str | None = None) -> dict:
    why = _refuse(body, platform_sid)
    if why:
        return {"started": False, "why": why}
    sid, bucket = body["sid"], body["bucket"]
    user, password = body["hive_user"], body["hive_password"]
    with LOCK:
        if STATE["starting"] or _running():
            return {"started": False, "why": "already running", "starting": STATE["starting"],
                    "report": STATE["report"]}
        if STATE["marked"]:
            # This process claimed the session before and its backend is not running: the
            # answer that start gave, never a second run and not a replacement.
            proc = STATE["proc"]
            return {"started": False, "why": "already started", "report": STATE["report"],
                    "backend_exit": proc.poll() if proc is not None else None}
        STATE["starting"] = True
    _set_status("HealthyBusy")
    t0 = time.time()
    try:
        # Hashed before the session is claimed: a failure here leaves it unclaimed, so a
        # re-sent start can still run it.
        try:
            pass_hash = hash_password(password)
        except HelperError as e:
            return {"started": False, "why": f"the password could not be hashed: {e}"}
        try:
            first = s3_put(bucket, f"bridge/{sid}/started", {"started_at": int(t0)}, if_absent=True)
        except HelperError as e:
            return {"started": False, "why": f"the started marker could not be written: {e}"}
        if not first:
            try:
                s3_put(bucket, f"bridge/{sid}/state.json", {"state": "replaced"}, if_absent=False)
                written = True
            except HelperError:
                written = False
            return {"started": False, "why": "replaced", "state_written": written}
        STATE["marked"] = True
        STATE["auth"] = "Basic " + b64encode(f"{user}:{password}".encode()).decode()
        STATE["env"] = backend_env(sid, bucket, user, pass_hash)
        STATE["bridge"] = (bucket, sid)
        STATE["started_at"] = t0
        try:
            proc = _launch(STATE["env"])
        except OSError as e:
            STATE["report"] = {"started": False, "why": f"the backend could not be launched: {type(e).__name__}"}
            return STATE["report"]
        STATE["proc"] = proc
        ready = _wait_healthy(proc, t0)
        if ready is None and proc.poll() is None:
            # Never healthy: stop it, or a hung backend would hold the microVM busy for 8 h.
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        report = {"started": ready is not None, "backend_ready_s": ready}
        if ready is None:
            report.update(why="the backend did not become healthy", backend_exit=proc.poll(),
                          log_tail=_log_tail())
        STATE["report"] = report
        if ready is not None:
            start_watch()
        return report
    finally:
        with LOCK:
            STATE["starting"] = False


def _launch(env: dict):
    """Start the backend in a process group of its own (its output appended to
    BACKEND_LOG), so it and everything it started can be stopped together; raises OSError.
    With umask 0077, as the EC2 Hive's unit (UMask=0077, hive/swarmai-hive.service): what
    the Hive writes is readable by the Hive user only, never by the Feature Map replay user."""
    os.makedirs(LOG_DIR, mode=0o700, exist_ok=True)
    os.chmod(LOG_DIR, 0o700)                        # also one an earlier start made
    fd = os.open(BACKEND_LOG, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "ab") as log:
        return subprocess.Popen(backend_command(), cwd=BACKEND_DIR, env=env, umask=0o077,
                                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)


def _signal_group(proc, sig) -> None:
    """Send *sig* to the backend's process group (the backend and what it started: the
    agent CLI, git). A group with no process left, or a process without a pid, is done."""
    pid = getattr(proc, "pid", None)
    if not isinstance(pid, int) or pid <= 1:
        return
    try:
        os.killpg(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _reap(proc) -> None:
    """Before a rescue: nothing the dead backend started may still write (an OOM kill
    takes the backend alone, its children live on). The group is killed and the backend
    waited for."""
    _signal_group(proc, signal.SIGKILL)
    if proc is not None:
        try:
            proc.wait(timeout=STOP_WAIT_S)
        except subprocess.TimeoutExpired:
            print("supervisor: the backend did not exit after SIGKILL", flush=True)


def _stop(proc) -> None:
    """Stop a backend this supervisor decided to stop (never counted as a crash): the
    group gets SIGTERM, then SIGKILL after STOP_WAIT_S, and is reaped before this returns."""
    STATE["stopping"] = True
    if proc is None:
        return
    if proc.poll() is None:
        _signal_group(proc, signal.SIGTERM)
        proc.terminate()
        try:
            proc.wait(timeout=STOP_WAIT_S)
        except subprocess.TimeoutExpired:
            proc.kill()
    _reap(proc)


def rescue() -> dict:
    """Upload what the dead backend wrote, seal its open turn with its commits and upload
    that (``python -m core.hive_rescue`` in the backend venv; no backend may run). Its
    JSON answer, or ``{"ok": false, "why"}``. Never raises."""
    env = STATE["env"]
    if env is None:
        return {"ok": False, "why": "no backend was started"}
    try:
        r = subprocess.run([VENV_PYTHON, "-m", "core.hive_rescue"], cwd=BACKEND_DIR, env=env,
                           capture_output=True, timeout=RESCUE_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"supervisor: the rescue did not run to its end ({type(e).__name__})", flush=True)
        return {"ok": False, "why": type(e).__name__}
    try:                          # its own log lines, next to the backend's
        with open(BACKEND_LOG, "ab") as log:
            log.write(r.stderr or b"")
    except OSError:
        pass
    lines = (r.stdout or b"").decode(errors="replace").strip().splitlines()
    try:
        got = json.loads(lines[-1]) if lines else None
    except ValueError:
        got = None
    if not isinstance(got, dict):
        got = {"ok": False, "why": f"exit {r.returncode}"}
    print(f"supervisor: rescue exit {r.returncode}: {json.dumps(got)[:300]}", flush=True)
    return got


def _write_state(obj: dict) -> bool:
    bucket, sid = STATE["bridge"]
    try:
        s3_put(bucket, f"bridge/{sid}/state.json", obj, if_absent=False)
        return True
    except HelperError as e:
        print(f"supervisor: state.json {obj.get('state')} not written ({e})", flush=True)
        return False


def _crashed(code, restart: bool = True) -> bool:
    """The backend exited on its own: rescue, write crashed, start it again once (never
    past the pre-limit time). True when a restarted backend is running (still watched)."""
    with LOCK:
        STATE["rescuing"] = True
    try:
        _reap(STATE["proc"])
        tail = _log_tail()[-LOG_TAIL_MAX:]
        got = rescue()
        again = restart and STATE["restarts"] < MAX_RESTARTS
        _write_state({"state": "crashed", "exit": code, "log_tail": tail, "at": int(time.time()),
                      "restarting": again, "rescue": got})
        if not again:
            return False
        STATE["restarts"] += 1
        print(f"supervisor: the backend exited ({code}); starting it again", flush=True)
        try:
            proc = _launch(STATE["env"])
        except OSError as e:
            print(f"supervisor: the backend could not be started again ({type(e).__name__})", flush=True)
            proc = None
        STATE["proc"] = proc if proc is not None else STATE["proc"]
        if proc is not None and _wait_healthy(proc, time.time()) is not None:
            return True
        # never healthy: stopped, rescued again, and nothing more is started
        _stop(proc)
        got = rescue()
        _write_state({"state": "crashed", "exit": proc.poll() if proc is not None else None,
                      "log_tail": _log_tail()[-LOG_TAIL_MAX:], "at": int(time.time()),
                      "restarting": False, "rescue": got})
        return False
    finally:
        with LOCK:
            STATE["rescuing"] = False


def _seal_at_limit() -> bool:
    """Past the pre-limit time: stop a busy backend, rescue, write expired. True once there
    is nothing left to watch; False while the backend is idle (nothing open: looked at
    again, a correction may still start a turn before the Host stops sending)."""
    if not busy():
        return False
    STATE["expired"] = True
    with LOCK:
        STATE["rescuing"] = True
    try:
        print("supervisor: the session nears its lifetime; sealing what is open", flush=True)
        _stop(STATE["proc"])
        got = rescue()
        _write_state({"state": "expired", "at": int(time.time()), "rescue": got})
    finally:
        with LOCK:
            STATE["rescuing"] = False
    return True


def watch_once() -> bool:
    """One look at the backend: a rescue (and, before the pre-limit time, one restart) when
    it exited on its own; past the pre-limit time, the seal once it is busy. False once
    there is nothing left to watch."""
    if STATE["expired"]:
        return False
    proc = STATE["proc"]
    if proc is None:
        return False
    t0 = STATE["started_at"]
    past = t0 is not None and time.time() >= t0 + MAX_LIFETIME_S - PRE_LIMIT_MARGIN_S
    code = proc.poll()
    if code is not None:
        if STATE["stopping"]:
            return False
        return _crashed(code, restart=not past)
    if past:
        return not _seal_at_limit()
    return True


_WAKE = threading.Event()         # never set: the watcher's wait between looks


def _watch() -> None:
    while True:
        _WAKE.wait(WATCH_S)
        try:
            if not watch_once():
                return
        except Exception as e:  # noqa: BLE001 - the watcher must not die of one bad look
            print(f"supervisor: watching the backend failed ({type(e).__name__})", flush=True)


def start_watch() -> None:
    threading.Thread(target=_watch, name="backend-watch", daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, obj: dict) -> None:
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if self.path == "/ping":
            self._send(200, ping())
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        if self.path != "/invocations":
            self._send(404, {"error": "not found"})
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if not 0 <= n <= MAX_BODY:
            self._send(400, {"error": f"Content-Length must be 0 to {MAX_BODY}"})
            return
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            self._send(400, {"error": "not JSON"})
            return
        if not isinstance(body, dict) or body.get("action") != "start":
            self._send(400, {"error": "action must be start"})
            return
        self._send(200, start(body, self.headers.get(SESSION_HEADER)))

    def log_message(self, fmt, *args):  # the request line only, never a body
        print("supervisor:", fmt % args, flush=True)


def serve(host: str = LISTEN_HOST, port: int = LISTEN_PORT) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), Handler)


if __name__ == "__main__":
    serve().serve_forever()
