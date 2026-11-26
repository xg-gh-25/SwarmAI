"""The Hive Caddyfile exempts /health from the origin guard and basic auth only as the
literal request path.

Caddy's path matcher (and the {http.request.uri.path} placeholder) compares a cleaned,
decoded, case-folded path while the backend receives the raw one, so a path-matcher
exemption also let /x/../health, //health, /HEALTH and /%68ealth past basic auth (and the
percent-encoded form past the origin guard). Both exemptions now compare the raw request
target {http.request.uri}. A new Hive renders that from user data; an existing Hive gets
the same file from the first step of its next update (health_exempt_fix_sh).

The live tests run a real Caddy when a ``caddy`` binary is on PATH; the update step runs
under bash with ``caddy`` / ``systemctl`` / ``curl`` stubs. No AWS call is made.
"""

import asyncio
import base64
import http.client
import http.server
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hive.user_data import (
    HEALTH_EXEMPT_EXPR,
    HEALTH_PROTECTED_LINE,
    health_exempt_fix_sh,
    origin_guard_editor_source,
    render_user_data,
)

_HEREDOC_OPEN = "cat > /etc/caddy/Caddyfile << 'CADDY'\n"
_REPO_CADDYFILE = Path(__file__).resolve().parents[2] / "hive" / "Caddyfile"
FAKE_HASH = "$2a$14$" + "a" * 53
SECRET = "S" * 40 + "x_-"
GUARD_LINE = f"        not expression `{HEALTH_EXEMPT_EXPR}`\n"

# Every spelling that a cleaning / decoding / case-folding matcher takes for /health.
ALIASES = ("/x/../health", "/health/", "//health", "/%68ealth", "/HEALTH", "/./health",
           "/health/.", "/api/x/../../health")
OPEN = ("/health", "/health?probe=1")


def _caddyfile_of(user_data: str) -> str:
    return user_data.split(_HEREDOC_OPEN, 1)[1].split("\nCADDY\n", 1)[0] + "\n"


def _render(origin_secret=None, auth_hash=FAKE_HASH) -> str:
    return _caddyfile_of(render_user_data(
        s3_bucket="swarmai-hive-123456789012-us-east-1", version="2.4.4", auth_user="admin",
        auth_hash=auth_hash, region="us-east-1", origin_secret=origin_secret))


# The exemption lines as a Hive rendered them before this fix (commit c5defaa9f): the
# guard compared the decoded {http.request.uri.path}, basic auth used the path matcher.
_OLD_GUARD_HEAD = (
    "    # ORIGIN-GUARD-RULE-BEGIN\n"
    "    # /health is exempt only as the literal request path: the path matcher cleans dot\n"
    "    # segments, but the backend gets the raw path, so /x/../health must not pass.\n"
    "    @foreign_origin {\n"
    '        not expression `{http.request.uri.path} == "/health"`\n')
_OLD_PROTECTED = "    @protected not path /health\n"


def _pre_fix(fresh: str) -> str:
    """The Caddyfile an existing Hive has: the one this commit renders, with the lines it
    replaced put back."""
    comment = re.search(r"((?:    # [^\n]*\n){4})(?=    # ORIGIN-GUARD-RULE-BEGIN\n|"
                        + re.escape(HEALTH_PROTECTED_LINE) + ")", fresh).group(1)
    assert "/health is exempt from the origin guard and basic auth" in comment
    old = fresh.replace(comment, "", 1).replace(HEALTH_PROTECTED_LINE, _OLD_PROTECTED, 1)
    if "ORIGIN-GUARD-RULE-BEGIN" in old:
        new_head = "    # ORIGIN-GUARD-RULE-BEGIN\n    @foreign_origin {\n" + GUARD_LINE
        assert old.count(new_head) == 1
        old = old.replace(new_head, _OLD_GUARD_HEAD, 1)
    return old


# ── the rendered files ─────────────────────────────────────────────────────


def test_every_health_exemption_is_the_literal_uri():
    """Both rendered Caddyfiles and the reference copy exempt /health only through the
    raw-URI expression; no path matcher or decoded-path compare is left in front of auth."""
    reference = _REPO_CADDYFILE.read_text()
    for name, caddy, guarded in (("unguarded", _render(), False),
                                 ("guarded", _render(SECRET), True),
                                 ("hive/Caddyfile", reference, True)):
        assert caddy.count(HEALTH_PROTECTED_LINE) == 1, name
        assert "basic_auth @protected {" in caddy, name
        assert ("@foreign_origin {\n" + GUARD_LINE in caddy) is guarded, name
        assert "not path /health" not in caddy, name
        assert "{http.request.uri.path}" not in caddy.replace(
            "{http.request.uri.path} clean", ""), name  # only the comment names it
    assert _pre_fix(_render(SECRET)) != _render(SECRET)


@pytest.mark.skipif(shutil.which("caddy") is None, reason="no caddy binary on PATH")
def test_rendered_auth_route_matches_the_raw_uri(tmp_path):
    for secret in (None, SECRET):
        f = tmp_path / "Caddyfile"
        f.write_text(_render(secret).replace("/var/log/caddy/hive-access.log",
                                             str(tmp_path / "access.log")))
        r = subprocess.run(["caddy", "adapt", "--config", str(f), "--adapter", "caddyfile"],
                           capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr[-2000:]
        routes = json.loads(r.stdout)["apps"]["http"]["servers"]["srv0"]["routes"]
        auth = next(rt for rt in routes if rt["handle"][0]["handler"] == "authentication")
        assert auth["match"] == [{"not": [{"expression": HEALTH_EXEMPT_EXPR}]}]


# ── a live Caddy ───────────────────────────────────────────────────────────


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _Backend:
    """Stands in for the Hive backend: records the raw path of every request it gets;
    /api/chat/stream answers an event stream whose second event comes 1.5 s later."""

    def __init__(self):
        self.seen: list[str] = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                outer.seen.append(self.path)
                if self.path == "/api/chat/stream":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    self.wfile.write(b"data: one\n\n")
                    self.wfile.flush()
                    time.sleep(1.5)
                    self.wfile.write(b"data: two\n\n")
                    return
                body = f"backend {self.path}".encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


class _LiveCaddy:
    def __init__(self, tmp_path: Path, caddy: str, backend_port: int):
        self.port = _free_port()
        self.err_path = tmp_path / "caddy.err"
        # No admin endpoint (it would bind :2019); the guarded file has a global block.
        order = "    order respond before basic_auth\n"
        if order in caddy:
            caddy = caddy.replace(order, order + "    admin off\n", 1)
        else:
            caddy = "{\n    admin off\n}\n" + caddy
        caddy = (caddy.replace(":80 {\n", f":{self.port} {{\n", 1)
                 .replace("127.0.0.1:18321", f"127.0.0.1:{backend_port}")
                 .replace("/var/log/caddy/hive-access.log", str(tmp_path / "access.log")))
        self.file = tmp_path / "Caddyfile"
        self.file.write_text(caddy)

    def __enter__(self):
        self.err = open(self.err_path, "w")
        self.proc = subprocess.Popen(["caddy", "run", "--config", str(self.file),
                                      "--adapter", "caddyfile"],
                                     stdout=self.err, stderr=self.err)
        deadline = time.monotonic() + 15
        while True:
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=1).close()
                return self
            except OSError:
                assert self.proc.poll() is None and time.monotonic() < deadline, \
                    self.err_path.read_text()[-2000:]
                time.sleep(0.2)

    def __exit__(self, *exc):
        self.proc.terminate()
        self.proc.wait(timeout=10)
        self.err.close()

    def get(self, path: str, headers: dict) -> int:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request("GET", path, headers=headers)  # the path is sent as is
            resp = conn.getresponse()
            resp.read()
            return resp.status
        finally:
            conn.close()


def _auth_header() -> dict:
    return {"Authorization": "Basic " + base64.b64encode(b"admin:pw").decode()}


def _pw_hash() -> str:
    import bcrypt
    return bcrypt.hashpw(b"pw", bcrypt.gensalt(rounds=4)).decode()


_live = pytest.mark.skipif(shutil.which("caddy") is None, reason="no caddy binary on PATH")


@_live
@pytest.mark.parametrize("secret", [None, SECRET], ids=["unguarded", "guarded"])
def test_live_basic_auth_covers_every_health_alias(tmp_path, secret):
    """With the origin secret (or on a Hive without the guard) and without the credential,
    every alias of /health gets 401 and never reaches the backend; /health, with or
    without a query, is answered by the backend without the credential."""
    origin = {"X-Swarm-Origin": secret} if secret else {}
    with _Backend() as backend, \
            _LiveCaddy(tmp_path, _render(secret, _pw_hash()), backend.port) as caddy:
        for path in ALIASES:
            assert caddy.get(path, origin) == 401, path
        assert backend.seen == []
        for path in OPEN:
            assert caddy.get(path, origin) == 200, path
        assert backend.seen == list(OPEN)
        # the credential still opens the aliases (they are ordinary paths now)
        assert caddy.get("/x/../health", {**origin, **_auth_header()}) == 200
        assert backend.seen[-1] == "/x/../health"


@_live
def test_live_origin_guard_refuses_every_health_alias(tmp_path):
    """Without the secret every alias, the percent-encoded /%68ealth included, gets 403
    before basic auth, also when it carries the credential."""
    with _Backend() as backend, \
            _LiveCaddy(tmp_path, _render(SECRET, _pw_hash()), backend.port) as caddy:
        for path in ALIASES + ("/", "/api/x"):
            assert caddy.get(path, {}) == 403, path
            assert caddy.get(path, _auth_header()) == 403, path
            assert caddy.get(path, {"X-Swarm-Origin": SECRET + "x"}) == 403, path
        assert backend.seen == []
        for path in OPEN:
            assert caddy.get(path, {}) == 200, path


@_live
def test_live_api_and_sse_routes_still_work(tmp_path):
    """With the secret and the credential the API reaches the backend, and the SSE route
    delivers its first event before the backend ends the response."""
    full = {"X-Swarm-Origin": SECRET, **_auth_header()}
    with _Backend() as backend, \
            _LiveCaddy(tmp_path, _render(SECRET, _pw_hash()), backend.port) as caddy:
        assert caddy.get("/api/x", full) == 200
        assert caddy.get("/api/chat/stream", {"X-Swarm-Origin": SECRET}) == 401
        conn = http.client.HTTPConnection("127.0.0.1", caddy.port, timeout=10)
        try:
            start = time.monotonic()
            conn.request("GET", "/api/chat/stream", headers=full)
            resp = conn.getresponse()
            assert resp.status == 200
            got = b""
            while b"data: one\n" not in got:  # read1: what has arrived (chunked decoded)
                chunk = resp.read1(1024)
                assert chunk, got
                got += chunk
            first = time.monotonic() - start
            rest = resp.read()
            total = time.monotonic() - start
        finally:
            conn.close()
        assert b"data: two" in rest
        assert first < 1.0 <= total, (first, total)  # streamed, not buffered to the end
        assert backend.seen == ["/api/x", "/api/chat/stream"]


# ── an existing Hive: the update step ─────────────────────────────────────


class _Box:
    """The update step under bash with stubs: ``caddy`` (validate fails while a flag file
    exists), ``systemctl`` (logs), ``curl`` (answers what Caddy would for the Caddyfile on
    disk, unless a path is forced to a code)."""

    def __init__(self, tmp: Path, caddyfile_text: str):
        self.tmp = tmp
        self.caddyfile = tmp / "Caddyfile"
        self.caddyfile.write_text(caddyfile_text)
        self.caddyfile.chmod(0o640)
        self.bin = tmp / "bin"
        self.bin.mkdir()
        self.validate_fails = tmp / "validate-fails"
        self.forced = tmp / "forced.json"  # {path: code}
        self.systemctl_log = tmp / "systemctl.log"
        self._stub("caddy", f'[ "$1" = validate ] && [ -f "{self.validate_fails}" ] && exit 1\n'
                            'exit 0\n')
        self._stub("systemctl", f'echo "$*" >> "{self.systemctl_log}"\nexit 0\n')
        self._stub("curl", _CURL_STUB.replace("__CADDYFILE__", repr(str(self.caddyfile)))
                   .replace("__FORCED__", repr(str(self.forced))), interpreter=sys.executable)

    def _stub(self, name, body, interpreter="/bin/bash"):
        p = self.bin / name
        p.write_text(f"#!{interpreter}\n" + body)
        p.chmod(p.stat().st_mode | stat.S_IEXEC)

    def run(self) -> subprocess.CompletedProcess:
        line = 'HEALTH_CADDYFILE="/etc/caddy/Caddyfile"'
        script = health_exempt_fix_sh()
        assert script.count(line) == 1, "the step names its Caddyfile in one variable"
        script = "set -euo pipefail\n" + script.replace(
            line, f'HEALTH_CADDYFILE="{self.caddyfile}"')
        env = {**os.environ, "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}"}
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                              timeout=60, env=env)

    def restarts(self) -> int:
        if not self.systemctl_log.exists():
            return 0
        return self.systemctl_log.read_text().count("restart caddy")


# What Caddy answers for a request, from the Caddyfile on disk: the guard (403 unless
# exempt or the secret is sent), then basic auth (401 unless exempt), else the backend.
# A pre-fix file exempts like Caddy did: the guard on the decoded path, basic auth on the
# cleaned, case-folded path.
_CURL_STUB = r'''import json, posixpath, re, sys
from pathlib import Path
from urllib.parse import unquote
args = sys.argv[1:]
url = args[-1]
assert url.startswith("http://127.0.0.1/") and "--path-as-is" in args, args
raw = url[len("http://127.0.0.1"):]
forced = Path(__FORCED__)
if forced.exists():
    codes = json.loads(forced.read_text())
    if raw in codes:
        print(codes[raw], end="")
        sys.exit(0)
caddy = Path(__CADDYFILE__).read_text()
sent = None
for i, a in enumerate(args[:-1]):
    if a == "-H":
        assert args[i + 1].startswith("@"), "a header (the secret) on curl's argv"
        line = Path(args[i + 1][1:]).read_text()
        if line.startswith("X-Swarm-Origin: "):
            sent = line[len("X-Swarm-Origin: "):].strip()
literal = raw == "/health" or raw.startswith("/health?")
decoded = unquote(raw.split("?")[0])
cleaned = posixpath.normpath(re.sub("/+", "/", decoded)).lower()
rule = re.search(r"# ORIGIN-GUARD-RULE-BEGIN\n(.*?)# ORIGIN-GUARD-RULE-END", caddy, re.S)
if rule:
    secret = re.search(r"not header X-Swarm-Origin (\S+)", rule.group(1)).group(1)
    exempt = literal if "{http.request.uri} ==" in rule.group(1) else decoded == "/health"
    if not exempt and sent != secret:
        print("403", end="")
        sys.exit(0)
if "@protected not expression `{http.request.uri} ==" in caddy:
    exempt = literal
elif "@protected not path /health" in caddy:
    exempt = cleaned == "/health"
else:
    exempt = False
print("200" if exempt else "401", end="")
'''


@pytest.mark.parametrize("secret", [None, SECRET], ids=["unguarded", "guarded"])
def test_update_step_turns_a_legacy_caddyfile_into_a_fresh_one(tmp_path, secret):
    box = _Box(tmp_path, _pre_fix(_render(secret)))
    r = box.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert box.caddyfile.read_text() == _render(secret)
    assert "now the literal request path only" in r.stdout
    assert box.restarts() == 1
    assert stat.S_IMODE(box.caddyfile.stat().st_mode) == 0o640
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Caddyfile", "bin", "systemctl.log"]
    assert SECRET not in r.stdout + r.stderr


@pytest.mark.parametrize("secret", [None, SECRET], ids=["unguarded", "guarded"])
def test_update_step_leaves_a_fixed_caddyfile_alone(tmp_path, secret):
    fresh = _render(secret)
    box = _Box(tmp_path, fresh)
    r = box.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert box.caddyfile.read_text() == fresh
    assert "already the literal request path" in r.stdout
    assert box.restarts() == 0
    assert not (tmp_path / "Caddyfile.health-bak").exists()


@pytest.mark.parametrize("failure, restarts", [
    ("validate", 0),
    ("health-refused", 2),
    ("alias-open", 2),
    ("alias-with-secret-open", 2),
])
def test_update_step_restores_the_previous_file_on_failure(tmp_path, failure, restarts):
    """Any failure after the edit puts the previous file back (restarting Caddy with it
    when it had been restarted) and stops with exit 1, the reason on stdout."""
    legacy = _pre_fix(_render(SECRET))
    box = _Box(tmp_path, legacy)
    if failure == "validate":
        box.validate_fails.touch()
    else:
        box.forced.write_text(json.dumps({
            "health-refused": {"/health": "401"},
            "alias-open": {"/x/../health": "200"},
            "alias-with-secret-open": {},
        }[failure]))
    if failure == "alias-with-secret-open":
        # a guard that still lets the alias past basic auth: answered 403 without the
        # header (so the deny-side check passes) and 200 with the secret
        box._stub("curl", 'case "$*" in *"-H @"*) printf 200 ;; *"/x/../health"*) printf 403 ;;'
                          ' *) printf 200 ;; esac\n')
    r = box.run()
    assert r.returncode == 1, r.stdout + r.stderr
    assert "ERROR: Caddy /health fix" in r.stdout and "previous Caddyfile restored" in r.stdout
    assert box.caddyfile.read_text() == legacy
    assert box.restarts() == restarts
    assert not (tmp_path / "Caddyfile.health-bak").exists()
    assert SECRET not in r.stdout + r.stderr


def _safe_unknown_layouts() -> dict:
    return {
        # the oldest layout protects everything with `basicauth *`
        "basicauth *": _render().replace(HEALTH_PROTECTED_LINE, "").replace(
            "basic_auth @protected {", "basicauth * {"),
        # already fixed, but edited by hand so the editor cannot reproduce it
        "fixed, comment reworded": _render().replace("must not pass.", "never pass."),
        "fixed, guard secret unreadable": _render(SECRET).replace(
            f"X-Swarm-Origin {SECRET}", "X-Swarm-Origin {$UNREADABLE}"),
    }


@pytest.mark.parametrize("layout", list(_safe_unknown_layouts()))
def test_update_step_leaves_an_unknown_layout_alone(tmp_path, layout):
    """A Caddyfile the editor cannot edit whose basic auth is one of the two shapes known
    safe (on every request, or exactly the literal /health line) is left as it is:
    WARN, exit 0, no restart."""
    text = _safe_unknown_layouts()[layout]
    box = _Box(tmp_path, text)
    r = box.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "WARN: Caddy /health exemption" in r.stdout
    assert box.caddyfile.read_text() == text
    assert box.restarts() == 0


def _stuck_layouts() -> dict:
    guarded = _pre_fix(_render(SECRET))
    return {
        "legacy line twice": _render() + _OLD_PROTECTED + _OLD_PROTECTED,
        "legacy and fixed line": _render() + _OLD_PROTECTED,
        "guard secret unreadable": guarded.replace(f"X-Swarm-Origin {SECRET}",
                                                   "X-Swarm-Origin {$UNREADABLE}"),
        "old guard line, no basic-auth line": guarded.replace(_OLD_PROTECTED, ""),
        # hand-edited spellings of a path exemption the editor cannot rewrite
        "block form": _render().replace(
            HEALTH_PROTECTED_LINE, "    @protected {\n        not path /health\n    }\n"),
        "double space": _render().replace(HEALTH_PROTECTED_LINE, "    @protected not path  /health\n"),
        "other case": _render().replace(HEALTH_PROTECTED_LINE, "    @protected not path /Health\n"),
        "path shorthand": _render().replace(
            HEALTH_PROTECTED_LINE, '    @protected not expression `{path} == "/health"`\n'),
        "wildcard": _render().replace(HEALTH_PROTECTED_LINE, "    @protected not path /heal*\n"),
        "regexp": _render().replace(HEALTH_PROTECTED_LINE,
                                    "    @protected not path_regexp ^/h.alth$\n"),
        "escaped": _render().replace(HEALTH_PROTECTED_LINE, "    @protected not path /%68ealth\n"),
    }


@pytest.mark.parametrize("layout", list(_stuck_layouts()))
def test_update_step_stops_on_a_layout_it_cannot_fix(tmp_path, layout):
    """A Caddyfile the editor cannot edit whose basic auth is not one of the two shapes
    known safe (it may still exempt /health by path) stops the update (exit 1, the
    reason on stdout), file unchanged, no restart: the update never reports success
    while a bypass may stay."""
    text = _stuck_layouts()[layout]
    box = _Box(tmp_path, text)
    r = box.run()
    assert r.returncode == 1, r.stdout + r.stderr
    assert "ERROR: Caddy /health fix: the Caddyfile's basic-auth exemption is not one" in r.stdout
    assert box.caddyfile.read_text() == text
    assert box.restarts() == 0
    assert not (tmp_path / "Caddyfile.health-bak").exists()
    assert SECRET not in r.stdout + r.stderr


def test_update_step_stops_on_an_unexpected_editor_result(tmp_path):
    """An editor answer other than changed / unchanged / unknown / stuck (empty, a typo,
    a value added later) stops the update with the previous file: the result is an
    enum, never a pass by default."""
    legacy = _pre_fix(_render())
    box = _Box(tmp_path, legacy)
    box._stub("python3", 'echo "surprise"\n')  # the editor runs as `python3 -c ...`
    r = box.run()
    assert r.returncode == 1, r.stdout + r.stderr
    assert "unexpected editor result 'surprise'" in r.stdout
    assert box.caddyfile.read_text() == legacy
    assert box.restarts() == 0


def test_guard_editor_applies_after_the_update_step(tmp_path):
    """The origin guard (added to an existing Hive after the update script) anchors on the
    new basic-auth line: after the step it gives the file a new guarded Hive renders; on
    a file the step did not fix it refuses and changes nothing."""
    box = _Box(tmp_path, _pre_fix(_render()))
    editor = origin_guard_editor_source()

    def apply():
        return subprocess.run(["python3", "-c", editor, "apply", str(box.caddyfile)],
                              input=SECRET, capture_output=True, text=True, timeout=30)

    before = box.caddyfile.read_text()
    refused = apply()
    assert refused.returncode != 0 and box.caddyfile.read_text() == before
    assert "Update the Hive first" in refused.stderr  # the step is what fixes this file
    assert box.run().returncode == 0
    assert apply().returncode == 0
    assert box.caddyfile.read_text() == _render(SECRET)


def _update_script() -> str:
    from hive.provisioner import HiveProvisioner
    p = HiveProvisioner(Path("/tmp/test.db"))
    ssm = MagicMock()
    ssm.send_command.return_value = {"Command": {"CommandId": "c-1"}}
    ssm.get_command_invocation.return_value = {"Status": "Success", "StandardOutputContent": ""}
    session = MagicMock()
    session.client.return_value = ssm

    async def fake_to_thread(fn, *args, **kwargs):
        return fn() if getattr(fn, "__name__", "") == "_run_command" else None

    instance = {"id": "inst-1", "name": "test-hive", "account_ref": "acc-1",
                "region": "us-east-1", "ec2_instance_id": "i-abc123",
                "s3_bucket": "swarmai-hive-test"}
    with patch.object(p, "_get_instance", new_callable=AsyncMock, return_value=instance), \
         patch.object(p, "_get_account", new_callable=AsyncMock, return_value={}), \
         patch.object(p, "_update_instance", new_callable=AsyncMock), \
         patch.object(p, "_sync_release_to_s3", new_callable=AsyncMock), \
         patch.object(p, "_wait_healthy_via_ssm", new_callable=AsyncMock, return_value=True), \
         patch.object(p, "_get_session", return_value=session), \
         patch("hive.provisioner.asyncio.to_thread", side_effect=fake_to_thread):
        asyncio.run(p.update("inst-1", "9.9.9", claimed=True))
    return ssm.send_command.call_args.kwargs["Parameters"]["commands"][0]


def test_update_script_runs_the_step_before_the_install():
    """Every update runs the step first: before the backup and rsync of the install, so a
    failure leaves the Hive on its version with its previous Caddyfile."""
    script = _update_script()
    step = health_exempt_fix_sh()
    assert script.count(step) == 1
    assert script.index(step) < script.index("rsync -a --delete --exclude='backend/.venv' /opt/swarmai/ "
                                             "/opt/swarmai.bak/")
    assert script.index("set -euo pipefail") < script.index(step)
    r = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True)
    assert r.returncode == 0, r.stderr
