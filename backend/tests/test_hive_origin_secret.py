"""A Hive's origin answers only its own CloudFront distribution (design
2026-11-04-kirocrew-borrow §3.C1).

Every CloudFront distribution is in the global prefix list the Hive's security group
allows, so a distribution someone else creates could reach the origin. Each Hive gets a
random secret that only its own distribution sends as ``X-Swarm-Origin``; the Caddyfile
answers 403 to anything else (``/health`` excepted) before basic auth, and its access log
drops the header. Everything here runs against fakes: no AWS call is made.
"""

import asyncio
import copy
import json
import logging
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hive.provisioner import HiveProvisioner

# The fake CloudFront checks every request against botocore's own CloudFront model (the
# same check boto runs before sending), so a field name the real API does not accept
# fails here, not on a live Hive.
import botocore.session
from botocore.validate import ParamValidator

_CF_MODEL = botocore.session.get_session().get_service_model("cloudfront")


def _validate_cf(operation: str, params: dict) -> None:
    report = ParamValidator().validate(
        params, _CF_MODEL.operation_model(operation).input_shape)
    assert not report.has_errors(), report.generate_report()
from hive.user_data import render_user_data

FAKE_HASH = "$2a$14$" + "a" * 53
_HEREDOC_OPEN = "cat > /etc/caddy/Caddyfile << 'CADDY'\n"


def _caddyfile_of(user_data: str) -> str:
    """The Caddyfile the user-data heredoc writes on the instance."""
    return user_data.split(_HEREDOC_OPEN, 1)[1].split("\nCADDY\n", 1)[0] + "\n"


def _render(origin_secret=None) -> str:
    return render_user_data(s3_bucket="swarmai-hive-123456789012-us-east-1", version="2.4.4",
                            auth_user="admin", auth_hash=FAKE_HASH, region="us-east-1",
                            origin_secret=origin_secret)


def _secret_in_logs(caplog, secret: str) -> list[str]:
    hits = []
    for rec in caplog.records:
        text = rec.getMessage() + (rec.exc_text or "")
        if rec.exc_info and rec.exc_info[1] is not None:
            text += str(rec.exc_info[1])
        if secret in text:
            hits.append(text)
    return hits


# ── fakes ─────────────────────────────────────────────────────────────────


class FakeCloudFront:
    """create/get/update_distribution with ETags and a Status that turns Deployed one
    read after an update. Every call is appended to the shared ``events`` list."""

    def __init__(self, events: list, config: dict | None = None, deploy_reads: int = 1):
        self.events = events
        self.config = copy.deepcopy(config) if config else None
        self.etag = "E1"
        self.pending_reads = 0
        self.deploy_reads = deploy_reads  # InProgress reads after a change
        self.created: dict | None = None

    def create_distribution(self, DistributionConfig):
        _validate_cf("CreateDistribution", {"DistributionConfig": DistributionConfig})
        self.events.append("cf.create_distribution")
        self.created = copy.deepcopy(DistributionConfig)
        self.config = copy.deepcopy(DistributionConfig)
        self.pending_reads = self.deploy_reads
        return {"Distribution": {"Id": "EDIST", "DomainName": "d1.cloudfront.net"}}

    def get_distribution(self, Id):
        if self.pending_reads:
            self.pending_reads -= 1
            status = "InProgress"
        else:
            status = "Deployed"
        self.events.append(f"cf.get_distribution:{status}")
        return {"Distribution": {"Id": Id, "Status": status,
                                 "DistributionConfig": copy.deepcopy(self.config)},
                "ETag": self.etag}

    def update_distribution(self, Id, DistributionConfig, IfMatch):
        assert IfMatch == self.etag, "update_distribution must pass the ETag it read"
        _validate_cf("UpdateDistribution", {"Id": Id, "DistributionConfig": DistributionConfig,
                                            "IfMatch": IfMatch})
        self.events.append("cf.update_distribution")
        self.config = copy.deepcopy(DistributionConfig)
        self.etag = f"E{int(self.etag[1:]) + 1}"
        self.pending_reads = self.deploy_reads
        return {"Distribution": {"Id": Id}, "ETag": self.etag}

    def header_value(self):
        origin = self.config["Origins"]["Items"][0]
        items = (origin.get("CustomHeaders") or {}).get("Items") or []
        values = [h["HeaderValue"] for h in items if h["HeaderName"] == "X-Swarm-Origin"]
        return values[0] if values else None


class ScriptRunningSSM:
    """SSM whose origin-guard commands really run: the script's CADDYFILE is pointed at a
    temp file and ``caddy`` / ``systemctl`` / ``curl`` are stubs on PATH. The version
    update command is acknowledged without running."""

    def __init__(self, events: list, tmp: Path, caddyfile: Path):
        self.events = events
        self.tmp = tmp
        self.caddyfile = caddyfile
        self.results: dict[str, tuple[str, str]] = {}
        self.bin = tmp / "bin"
        self.bin.mkdir(exist_ok=True)
        self.fail_flag = tmp / "validate-fails"
        self.curl_code = tmp / "curl-code"  # when present: every curl answers this code
        self.systemctl_log = tmp / "systemctl.log"
        self._stub("caddy", f'[ "$1" = validate ] && [ -f "{self.fail_flag}" ] && exit 1\nexit 0\n')
        self._stub("systemctl", f'echo "$*" >> "{self.systemctl_log}"\nexit 0\n')
        # What the Caddyfile on disk would answer: with the guard, 403 unless the request
        # carries its secret (then basic auth: 401); without it, basic auth: 401.
        self._stub("curl", f'''import re, sys
from pathlib import Path
forced = Path({str(self.curl_code)!r})
if forced.exists():
    print(forced.read_text().strip(), end="")
    sys.exit(0)
caddy = Path({str(caddyfile)!r}).read_text()
args = sys.argv[1:]
sent = None
for i, a in enumerate(args[:-1]):
    if a == "-H" and args[i + 1].startswith("@"):
        line = Path(args[i + 1][1:]).read_text()
        if line.startswith("X-Swarm-Origin: "):
            sent = line[len("X-Swarm-Origin: "):].strip()
    assert not (a == "-H" and "X-Swarm-Origin" in args[i + 1]), "secret on curl's argv"
m = re.search(r"not header X-Swarm-Origin (\\S+)", caddy)
print("401" if m is None or sent == m.group(1) else "403", end="")
''', interpreter=sys.executable)

    def _stub(self, name: str, body: str, interpreter: str = "/bin/bash") -> None:
        p = self.bin / name
        p.write_text(f"#!{interpreter}\n" + body)
        p.chmod(p.stat().st_mode | stat.S_IEXEC)

    def restarts(self) -> int:
        if not self.systemctl_log.exists():
            return 0
        return self.systemctl_log.read_text().count("restart caddy")

    def send_command(self, InstanceIds, DocumentName, Parameters, TimeoutSeconds, Comment):
        cid = f"cmd-{len(self.results) + 1}"
        script = Parameters["commands"][0]
        if "origin guard" not in Comment:
            self.events.append("ssm.version_update")
            self.results[cid] = ("Success", "")
            return {"Command": {"CommandId": cid}}
        mode = "apply" if "apply" in Comment else "remove"
        self.events.append(f"ssm.origin_{mode}")
        line = 'CADDYFILE="/etc/caddy/Caddyfile"'
        assert line in script, "the guard script must name its Caddyfile in one variable"
        script = script.replace(line, f'CADDYFILE="{self.caddyfile}"')
        env = {**os.environ, "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}"}
        proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                              timeout=60, env=env)
        status = "Success" if proc.returncode == 0 else "Failed"
        self.results[cid] = (status, proc.stdout + proc.stderr)
        return {"Command": {"CommandId": cid}}

    def get_command_invocation(self, CommandId, InstanceId):
        status, out = self.results[CommandId]
        return {"Status": status, "StandardOutputContent": out, "StandardErrorContent": ""}


class FakeSession:
    def __init__(self, cf, ssm=None):
        self.cf, self.ssm = cf, ssm

    def client(self, name, **_kw):
        if name == "cloudfront":
            return self.cf
        if name == "ssm":
            return self.ssm
        return MagicMock()


def _db_conn():
    cursor = MagicMock(rowcount=1)
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=cursor)
    conn.commit = AsyncMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    return conn


def _stored_secrets(update_mock: AsyncMock) -> list:
    return [c.kwargs["origin_secret"] for c in update_mock.call_args_list
            if "origin_secret" in c.kwargs]


# ── new Hive: user data + distribution ────────────────────────────────────


async def test_user_data_and_distribution_carry_secret(caplog):
    caplog.set_level(logging.DEBUG)
    events: list = []
    cf = FakeCloudFront(events)
    p = HiveProvisioner(Path("/tmp/unused.db"))
    instance = {"id": "inst-1", "name": "demo", "account_ref": "acc-1",
                "region": "us-east-1", "version": "2.4.4", "instance_type": "m7g.xlarge"}
    update = AsyncMock(side_effect=lambda _id, **kw: events.append(("db", kw)))
    launch = AsyncMock(side_effect=lambda *a, **kw: events.append("ec2.launch") or "i-1")
    p._get_instance = AsyncMock(return_value=instance)
    p._get_account = AsyncMock(return_value={"account_id": "123456789012"})
    p._update_instance = update
    p._get_session = MagicMock(return_value=FakeSession(cf))
    p._resolve_version = AsyncMock(return_value="2.4.4")
    p._ensure_s3_bucket = AsyncMock(return_value="swarmai-hive-123456789012-us-east-1")
    p._sync_release_to_s3 = AsyncMock()
    p._create_iam_role = AsyncMock(return_value="arn:role")
    p._create_instance_profile = AsyncMock(return_value="arn:profile")
    p._create_security_group = AsyncMock(return_value="sg-1")
    p._launch_ec2 = launch
    p._allocate_elastic_ip = AsyncMock(return_value=("eipalloc-1", "1.2.3.4"))
    p._wait_healthy_via_tag = AsyncMock(return_value=True)
    p._get_ec2_public_dns = AsyncMock(return_value="ec2-1-2-3-4.compute-1.amazonaws.com")
    p._cleanup_resources = AsyncMock()

    with patch("hive.provisioner.aiosqlite.connect", return_value=_db_conn()), \
         patch("hive.user_data.caddy_hash_password", return_value=FAKE_HASH), \
         patch("hive.provisioner.asyncio.sleep", new=AsyncMock()):
        await p.deploy("inst-1")

    assert not any(isinstance(e, tuple) and e[1].get("status") == "error" for e in events), events
    stored = _stored_secrets(update)
    assert len(stored) == 1, "deploy stores the origin secret once with the instance record"
    secret = stored[0]
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", secret), "secrets.token_urlsafe(32)"

    # Generated and stored before the instance launches: user data is fixed at launch.
    store_i = next(i for i, e in enumerate(events) if isinstance(e, tuple)
                   and "origin_secret" in e[1])
    assert store_i < events.index("ec2.launch")

    # The Caddyfile the instance writes on first boot.
    caddy = _caddyfile_of(launch.call_args.args[4])
    rule = re.search(r"@foreign_origin \{\n(.*?)\n    \}\n    respond @foreign_origin 403\n"
                     r"    request_header -X-Swarm-Origin\n", caddy, re.S)
    assert rule, caddy
    # /health is exempt only as the literal path (the path matcher would clean /x/../health)
    assert 'not expression `{http.request.uri.path} == "/health"`' in rule.group(1)
    assert f"not header X-Swarm-Origin {secret}" in rule.group(1)
    # respond is ordered before basic_auth (Caddy's default order runs basic_auth first).
    assert re.search(r"^\{\n    order respond before basic_auth\n", caddy, re.M)
    assert caddy.index("respond @foreign_origin 403") < caddy.index("basic_auth @protected")
    # /health stays outside both the guard and basic auth.
    assert caddy.index("handle /health") < caddy.index("@foreign_origin")
    log_block = caddy[caddy.index("    log {"):]
    assert "format filter {" in log_block
    assert "request>headers>X-Swarm-Origin delete" in log_block
    # ... and the default logger (Caddy's error log, which also writes request headers).
    global_block = caddy[:caddy.index(":80 {")]
    assert re.search(r"log default \{\n\s+format filter \{.*?request>headers>X-Swarm-Origin "
                     r"delete", global_block, re.S)

    # The distribution built for this Hive sends the same value.
    origin = cf.created["Origins"]["Items"][0]
    assert origin["CustomHeaders"] == {
        "Quantity": 1, "Items": [{"HeaderName": "X-Swarm-Origin", "HeaderValue": secret}]}

    assert _secret_in_logs(caplog, secret) == []
    assert all(secret not in str(e[1].get("error_message") or "")
               for e in events if isinstance(e, tuple))


def test_render_without_secret_keeps_the_legacy_caddyfile():
    """No secret → no guard at all (never a guard nobody can pass); bad shapes refused."""
    legacy = _caddyfile_of(_render())
    assert "ORIGIN-GUARD" not in legacy and "X-Swarm-Origin" not in legacy
    assert "order respond before basic_auth" not in legacy
    for bad in ("short", "a" * 31, "x" * 40 + " ", "x" * 40 + "'", "x" * 40 + "}"):
        with pytest.raises(ValueError) as ei:
            _render(bad)
        assert bad not in str(ei.value)


def test_guard_editor_turns_a_legacy_caddyfile_into_a_fresh_one(tmp_path):
    """The SSM editor (what update runs on an existing Hive) produces exactly the
    Caddyfile a new Hive gets, and its removal restores the legacy file byte for byte."""
    from hive.user_data import origin_guard_editor_source

    secret = "S" * 43
    legacy, fresh = _caddyfile_of(_render()), _caddyfile_of(_render(secret))
    f = tmp_path / "Caddyfile"
    f.write_text(legacy)
    editor = origin_guard_editor_source()
    run = lambda mode, stdin="": subprocess.run(  # noqa: E731
        ["python3", "-c", editor, mode, str(f)], input=stdin, capture_output=True,
        text=True, timeout=30)
    assert run("apply", secret).returncode == 0
    assert f.read_text() == fresh
    assert run("apply", "T" * 43).returncode == 0  # idempotent: replaces, never duplicates
    assert f.read_text() == fresh.replace(secret, "T" * 43)
    assert run("remove").returncode == 0
    assert f.read_text() == legacy
    # An anchor that is missing leaves the file untouched.
    f.write_text(legacy.replace("    @protected not path /health\n", ""))
    before = f.read_text()
    r = run("apply", secret)
    assert r.returncode != 0 and f.read_text() == before and secret not in r.stdout + r.stderr


@pytest.mark.skipif(shutil.which("caddy") is None, reason="no caddy binary on PATH")
def test_rendered_caddyfile_passes_caddy_validate(tmp_path):
    caddy = _caddyfile_of(_render("V" * 43))
    # Only the log path moves, so validate never needs /var/log/caddy.
    caddy = caddy.replace("/var/log/caddy/hive-access.log", str(tmp_path / "access.log"))
    f = tmp_path / "Caddyfile"
    f.write_text(caddy)
    r = subprocess.run(["caddy", "validate", "--config", str(f), "--adapter", "caddyfile"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-2000:]
    adapted = subprocess.run(["caddy", "adapt", "--config", str(f), "--adapter", "caddyfile"],
                             capture_output=True, text=True, timeout=60)
    routes = json.loads(adapted.stdout)["apps"]["http"]["servers"]["srv0"]["routes"]
    handlers = [r["handle"][0]["handler"] for r in routes]
    guard_i = handlers.index("static_response")
    assert guard_i < handlers.index("authentication") < handlers.index("subroute")
    guard = routes[guard_i]
    assert guard["handle"][0]["status_code"] == 403
    # not(raw path == /health OR header == secret)
    negated = guard["match"][0]["not"]
    assert {"header": {"X-Swarm-Origin": ["V" * 43]}} in negated
    assert {"expression": '{http.request.uri.path} == "/health"'} in negated


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _http(port: int, path: str, headers: dict) -> int:
    import http.client
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request("GET", path, headers=headers)  # the path is sent as is: no cleaning
        return conn.getresponse().status
    finally:
        conn.close()


@pytest.mark.skipif(shutil.which("caddy") is None, reason="no caddy binary on PATH")
def test_rendered_caddyfile_on_a_live_caddy(tmp_path):
    """The rendered Caddyfile on a real Caddy: 403 without the secret (also for a dot
    segment alias of /health), basic auth after it, and no log line holds the secret,
    including Caddy's error log on a 502."""
    import base64
    import bcrypt

    secret = "L" * 40 + "x_-"
    pw_hash = bcrypt.hashpw(b"pw", bcrypt.gensalt(rounds=4)).decode()
    port, dead = _free_port(), _free_port()  # nothing listens on *dead*: upstream gives 502
    ud = render_user_data(s3_bucket="b", version="2.4.4", auth_user="admin", auth_hash=pw_hash,
                          region="us-east-1", origin_secret=secret)
    caddy = (_caddyfile_of(ud)
             .replace("    order respond before basic_auth\n",
                      "    order respond before basic_auth\n    admin off\n")
             .replace(":80 {\n", f":{port} {{\n", 1)
             .replace("127.0.0.1:18321", f"127.0.0.1:{dead}")
             .replace("/var/log/caddy/hive-access.log", str(tmp_path / "access.log")))
    (tmp_path / "Caddyfile").write_text(caddy)
    err = open(tmp_path / "caddy.err", "w")
    proc = subprocess.Popen(["caddy", "run", "--config", str(tmp_path / "Caddyfile"),
                             "--adapter", "caddyfile"], stdout=err, stderr=err)
    try:
        deadline = time.monotonic() + 15
        while True:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=1).close()
                break
            except OSError:
                assert proc.poll() is None and time.monotonic() < deadline, \
                    (tmp_path / "caddy.err").read_text()[-2000:]
                time.sleep(0.2)
        good = {"X-Swarm-Origin": secret}
        auth = {"Authorization": "Basic " + base64.b64encode(b"admin:pw").decode()}
        assert _http(port, "/", {}) == 403
        assert _http(port, "/api/x", auth) == 403  # basic auth alone is not enough
        assert _http(port, "/", {"X-Swarm-Origin": secret + "x"}) == 403
        assert _http(port, "/x/../health", {}) == 403
        assert _http(port, "/api/x/../../health", {}) == 403
        assert _http(port, "/health", {}) == 502  # exempt: reaches the (dead) backend
        assert _http(port, "/", good) == 401
        assert _http(port, "/api/x", {**good, **auth}) == 502  # error log entry
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        err.close()
    logs = (tmp_path / "caddy.err").read_text() + (tmp_path / "access.log").read_text()
    assert secret not in logs
    assert '"status":502' in logs.replace(" ", ""), "the 502 was logged (the leak case ran)"


# ── existing Hive: update adds the lock, rollback removes it ──────────────


def _existing_hive_provisioner(events, cf, ssm, instance):
    p = HiveProvisioner(Path("/tmp/unused.db"))
    p._get_instance = AsyncMock(return_value=instance)
    p._get_account = AsyncMock(return_value={})
    p._update_instance = AsyncMock(side_effect=lambda _id, **kw: events.append(("db", kw)))
    p._sync_release_to_s3 = AsyncMock()
    p._wait_healthy_via_ssm = AsyncMock(return_value=True)
    p._get_session = MagicMock(return_value=FakeSession(cf, ssm))
    return p


def _first(events, name):
    return next(i for i, e in enumerate(events) if e == name)


async def _pre_secret_dist_config() -> dict:
    """The config of a distribution made before the origin secret: the one
    _create_cloudfront builds today, without its custom headers."""
    cf = FakeCloudFront([])
    await HiveProvisioner(Path("/tmp/unused.db"))._create_cloudfront(
        FakeSession(cf), "ec2.example", "demo", "s" * 43)
    config = copy.deepcopy(cf.created)
    del config["Origins"]["Items"][0]["CustomHeaders"]
    return config


async def test_update_and_rollback_order(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    legacy = _caddyfile_of(_render())
    caddyfile = tmp_path / "Caddyfile"
    caddyfile.write_text(legacy)
    events: list = []
    dist_config = await _pre_secret_dist_config()
    cf = FakeCloudFront(events, dist_config)
    ssm = ScriptRunningSSM(events, tmp_path, caddyfile)
    instance = {"id": "inst-1", "name": "demo", "account_ref": "acc-1", "region": "us-east-1",
                "ec2_instance_id": "i-1", "s3_bucket": "swarmai-hive-123456789012-us-east-1",
                "cloudfront_dist_id": "EDIST"}  # no origin_secret: an existing Hive
    p = _existing_hive_provisioner(events, cf, ssm, instance)

    # 1. A failed caddy validate restores the previous file, stores no secret, and
    #    leaves the (harmless) header on the distribution.
    ssm.fail_flag.write_text("1")
    with patch("hive.provisioner.aiosqlite.connect", return_value=_db_conn()), \
         patch("hive.provisioner.asyncio.sleep", new=AsyncMock()):
        with pytest.raises(RuntimeError):
            await p.update("inst-1", "2.4.4")
    assert caddyfile.read_text() == legacy
    assert not (tmp_path / "Caddyfile.origin-bak").exists()
    assert _stored_secrets(p._update_instance) == []
    first_value = cf.header_value()
    assert first_value and re.fullmatch(r"[A-Za-z0-9_-]{43}", first_value)
    assert "restart caddy" not in (ssm.systemctl_log.read_text()
                                   if ssm.systemctl_log.exists() else "")
    final = [e[1] for e in events if isinstance(e, tuple) and "error_message" in e[1]][-1]
    assert final["status"] == "running" and "origin" in final["error_message"].lower()
    version_i = next(i for i, e in enumerate(events) if e == ("db", {"version": "2.4.4"}))
    assert version_i < _first(events, "ssm.origin_apply"), "the new version is saved first"

    # 1b. The post-check after the restart fails (a request carrying the secret is refused,
    #     i.e. the value Caddy got is not the one CloudFront sends): previous file back,
    #     Caddy restarted again.
    ssm.fail_flag.unlink()
    ssm.curl_code.write_text("403")
    with patch("hive.provisioner.aiosqlite.connect", return_value=_db_conn()), \
         patch("hive.provisioner.asyncio.sleep", new=AsyncMock()):
        with pytest.raises(RuntimeError):
            await p.update("inst-1", "2.4.4")
    assert caddyfile.read_text() == legacy
    assert ssm.restarts() == 2, "restart with the new file, then again with the previous one"
    final = [e[1] for e in events if isinstance(e, tuple) and "error_message" in e[1]][-1]
    assert "with the secret got 403" in final["error_message"]
    assert _stored_secrets(p._update_instance) == []
    ssm.curl_code.unlink()
    ssm.systemctl_log.unlink()

    # 2. The retry: distribution first, Deployed, only then the SSM Caddyfile edit.
    events.clear()
    p._update_instance.reset_mock()
    with patch("hive.provisioner.aiosqlite.connect", return_value=_db_conn()), \
         patch("hive.provisioner.asyncio.sleep", new=AsyncMock()):
        await p.update("inst-1", "2.4.4")
    stored = _stored_secrets(p._update_instance)
    assert stored == [first_value], "the retry reuses the value the distribution already sends"
    assert cf.header_value() == first_value
    assert caddyfile.read_text() == _caddyfile_of(_render(first_value))
    edit = _first(events, "ssm.origin_apply")
    assert "cf.get_distribution:Deployed" in events[:edit], \
        f"the Caddyfile edit ran before the distribution was seen Deployed: {events}"
    assert "restart caddy" in ssm.systemctl_log.read_text()

    # Ordering when the header is new: update_distribution → Deployed → SSM edit.
    events.clear()
    cf2 = FakeCloudFront(events, dist_config, deploy_reads=3)
    caddyfile.write_text(legacy)
    p2 = _existing_hive_provisioner(events, cf2, ssm, instance)
    with patch("hive.provisioner.aiosqlite.connect", return_value=_db_conn()), \
         patch("hive.provisioner.asyncio.sleep", new=AsyncMock()):
        await p2.update("inst-1", "2.4.4")
    upd, edit = _first(events, "cf.update_distribution"), _first(events, "ssm.origin_apply")
    assert upd < edit
    assert "cf.get_distribution:Deployed" in events[upd:edit], \
        f"update_distribution → Deployed → SSM edit, got {events}"
    secret = cf2.header_value()
    assert _stored_secrets(p2._update_instance) == [secret]
    # Every InProgress poll is followed by a heartbeat, so release_stale_updates never
    # takes the long wait for an interrupted update.
    polls = [i for i, e in enumerate(events) if e == "cf.get_distribution:InProgress"]
    assert len(polls) == 3
    for i in polls:
        assert events[i + 1] == ("db", {"status": "updating"}), events

    # 3. Rollback: Caddy rule out and reloaded first, then the header off the distribution.
    events.clear()
    instance_locked = {**instance, "origin_secret": secret}
    p3 = _existing_hive_provisioner(events, cf2, ssm, instance_locked)
    with patch("hive.provisioner.aiosqlite.connect", return_value=_db_conn()) as connect:
        await p3.remove_origin_secret("inst-1")
    assert connect.called, "the rollback claims the running → updating gate"
    assert _first(events, "ssm.origin_remove") < _first(events, "cf.update_distribution")
    assert caddyfile.read_text() == legacy
    assert cf2.header_value() is None
    assert _stored_secrets(p3._update_instance) == [None]
    assert events[-1] == ("db", {"status": "running", "error_message": None})

    # A rollback whose Caddy edit fails leaves the distribution alone.
    caddyfile.write_text(_caddyfile_of(_render(secret)))
    cf3 = FakeCloudFront(events, cf2.config)
    from hive.provisioner import _set_origin_header
    _set_origin_header(cf3.config, secret)
    ssm.fail_flag.write_text("1")
    events.clear()
    p4 = _existing_hive_provisioner(events, cf3, ssm, instance_locked)
    with patch("hive.provisioner.aiosqlite.connect", return_value=_db_conn()):
        with pytest.raises(RuntimeError):
            await p4.remove_origin_secret("inst-1")
    assert "cf.update_distribution" not in events
    assert events[-1][1]["status"] == "running" and events[-1][1]["error_message"]
    assert cf3.header_value() == secret
    assert caddyfile.read_text() == _caddyfile_of(_render(secret))

    for s in (first_value, secret):
        assert _secret_in_logs(caplog, s) == []


@pytest.mark.asyncio
async def test_migration_v19_adds_origin_secret_column(tmp_path):
    """A database at v18 gains hive_instances.origin_secret (nullable) at start, rows
    untouched; the provisioner no longer adds the column itself."""
    import aiosqlite
    from database.sqlite import SQLiteDatabase, CURRENT_SCHEMA_VERSION
    import hive.provisioner as prov
    assert not hasattr(prov.HiveProvisioner, "_ensure_origin_secret_column")
    db = SQLiteDatabase(str(tmp_path / "m.db"))
    await db.initialize()
    async with aiosqlite.connect(str(tmp_path / "m.db")) as conn:
        cols = {r[1] for r in await (await conn.execute("PRAGMA table_info(hive_instances)")).fetchall()}
        assert "origin_secret" in cols
        assert (await (await conn.execute("PRAGMA user_version")).fetchone())[0] == CURRENT_SCHEMA_VERSION == 19
