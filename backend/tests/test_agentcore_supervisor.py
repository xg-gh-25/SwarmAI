"""The AgentCore supervisor, its image and its release job (design
docs/design/2026-11-20-swarm-out-multi-carrier.md §6.3, §6.5).

- a start whose bridge/<sid>/started already exists writes state.json "replaced" and starts
  no backend (a task never runs twice on a replaced microVM);
- a first start writes the marker with IfNoneMatch, hashes the password in the backend venv
  (stdin, never argv), starts uvicorn with the carrier env, and never puts the password in
  that env; a second start answers "already running";
- /ping is HealthyBusy during a start and while the backend says busy (or does not answer),
  Healthy otherwise; time_of_last_update moves only when the status changes;
- the Dockerfile exposes 8080 and 8081, the backend binds 0.0.0.0:8081, build_image.sh
  builds linux/arm64 with docker or finch, and the release job is skipped without its settings.

The supervisor is imported by path (it ships in hive/, not in the backend package); every
subprocess and HTTP call is faked.
"""
from __future__ import annotations

import importlib.util
import json
import os
import secrets
import subprocess
import threading
import time
import types
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
AC = REPO / "hive" / "agentcore"
PLAINTEXT = secrets.token_urlsafe(12)  # the run credential a Host would send


@pytest.fixture
def sup(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("agentcore_supervisor_under_test", AC / "supervisor.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(mod, "BACKEND_LOG", str(tmp_path / "logs" / "backend.log"))
    # the module's own clock, so a test can move it without touching the process's
    monkeypatch.setattr(mod, "time", types.SimpleNamespace(time=time.time, sleep=lambda s: None))
    return mod


class FakeS3:
    """subprocess.run for the venv helpers: an S3 put (honouring IfNoneMatch) and bcrypt."""

    def __init__(self, existing=()):
        self.objects = {k: b"{}" for k in existing}
        self.calls = []

    def __call__(self, argv, input=None, **kw):
        self.calls.append((argv, input))
        script = argv[2]
        if "bcrypt" in script and "put_object" not in script:
            return subprocess.CompletedProcess(argv, 0, b"$2b$14$fakehashfakehashfakehash\n", b"")
        bucket, key, conditional = argv[3], argv[4], argv[5] == "1"
        if conditional and key in self.objects:
            return subprocess.CompletedProcess(argv, 3, b"", b"")
        self.objects[key] = input
        return subprocess.CompletedProcess(argv, 0, b"", b"")


class FakeProc:
    def __init__(self, argv, **kw):
        self.argv, self.kw = argv, kw
        self.code = None

    def poll(self):
        return self.code

    def terminate(self):
        self.code = -15

    def wait(self, timeout=None):
        return self.code

    def kill(self):
        self.code = -9


class Popens(list):
    def __call__(self, argv, **kw):
        p = FakeProc(argv, **kw)
        self.append(p)
        return p


def _healthy(url, headers=None, timeout=3.0):
    if url.endswith("/health"):
        return 200, {"status": "ok"}
    if url.endswith("/api/hive/busy"):
        return 200, {"busy": False}
    raise AssertionError(url)


START = {"action": "start", "sid": "so-run-1234567890abcdef-round-0001", "bucket": "swarmai-hive-bucket",
         "hive_user": "admin", "hive_password": PLAINTEXT}
#: the session the platform names on every request it forwards (X-Amzn-...-Session-Id)
SID = START["sid"]


def test_second_start_refused(sup, monkeypatch):
    s3 = FakeS3(existing=[f"bridge/{START['sid']}/started"])
    popens = Popens()
    monkeypatch.setattr(sup.subprocess, "run", s3)
    monkeypatch.setattr(sup.subprocess, "Popen", popens)
    monkeypatch.setattr(sup, "http_get_json", _healthy)

    out = sup.start(dict(START), platform_sid=SID)

    assert out == {"started": False, "why": "replaced", "state_written": True}
    assert json.loads(s3.objects[f"bridge/{START['sid']}/state.json"]) == {"state": "replaced"}
    assert popens == []                                   # backend not started
    assert s3.objects[f"bridge/{START['sid']}/started"] == b"{}"  # the marker is not rewritten
    assert sup.ping()["status"] == "Healthy"


def test_first_start_claims_the_session_and_starts_the_backend_once(sup, monkeypatch):
    s3 = FakeS3()
    popens = Popens()
    monkeypatch.setattr(sup.subprocess, "run", s3)
    monkeypatch.setattr(sup.subprocess, "Popen", popens)
    monkeypatch.setattr(sup, "http_get_json", _healthy)

    out = sup.start(dict(START), platform_sid=SID)

    assert out["started"] is True and isinstance(out["backend_ready_s"], float)
    put = next(c for c in s3.calls if c[0][4:5] == [f"bridge/{START['sid']}/started"])
    assert put[0][5] == "1"  # IfNoneMatch
    hashed = next(c for c in s3.calls if "bcrypt" in c[0][2])
    assert hashed[1] == PLAINTEXT.encode() and PLAINTEXT not in " ".join(hashed[0])  # stdin only
    assert len(popens) == 1
    proc = popens[0]
    assert proc.argv == sup.backend_command()
    env = proc.kw["env"]
    for k, v in {"HOME": "/home/swarm", "SWARMAI_MODE": "hive", "SWARMAI_CARRIER": "agentcore",
                 "SWARMAI_AC_SID": START["sid"], "SWARMAI_AC_BUCKET": START["bucket"],
                 "HIVE_USER": "admin", "HIVE_PASS_HASH": "$2b$14$fakehashfakehashfakehash",
                 "CLAUDE_CODE_USE_BEDROCK": "1", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
                 "SWARM_RESUME_VIA_QUERY": "true"}.items():
        assert env[k] == v, k
    assert PLAINTEXT not in json.dumps(env)

    again = sup.start(dict(START), platform_sid=SID)
    assert again["started"] is False and again["why"] == "already running"
    assert len(popens) == 1 and len([c for c in s3.calls if c[0][5:6] == ["1"]]) == 1


def test_a_failed_marker_write_starts_nothing(sup, monkeypatch):
    def run(argv, input=None, **kw):
        if "put_object" not in argv[2]:
            return subprocess.CompletedProcess(argv, 0, b"$2b$14$x\n", b"")
        return subprocess.CompletedProcess(argv, 1, b"", b"Traceback ...\nAccessDenied\n")
    popens = Popens()
    monkeypatch.setattr(sup.subprocess, "run", run)
    monkeypatch.setattr(sup.subprocess, "Popen", popens)

    out = sup.start(dict(START), platform_sid=SID)

    assert out["started"] is False and out["why"].endswith(": AccessDenied") and popens == []
    assert "Traceback" not in out["why"]
    assert sup.ping()["status"] == "Healthy"


def test_a_start_without_its_fields_is_refused(sup, monkeypatch):
    popens = Popens()
    monkeypatch.setattr(sup.subprocess, "Popen", popens)
    assert sup.start({"action": "start", "sid": "x"})["started"] is False
    assert sup.start(dict(START, sid="../other"))["started"] is False
    assert popens == []


@pytest.mark.parametrize("change,why", [
    ({"hive_user": "ad:min"}, "hive_user"),
    ({"hive_password": "x" * 73}, "72 bytes"),
    ({"bucket": "Not_A_Bucket"}, "valid name"),
])
def test_a_start_that_cannot_work_is_refused_before_anything_is_claimed(sup, monkeypatch, change, why):
    s3 = FakeS3()
    monkeypatch.setattr(sup.subprocess, "run", s3)
    out = sup.start(dict(START, **change), platform_sid=SID)
    assert out["started"] is False and why in out["why"]
    assert s3.calls == []


def test_a_start_for_another_session_is_refused(sup, monkeypatch):
    s3 = FakeS3()
    monkeypatch.setattr(sup.subprocess, "run", s3)
    out = sup.start(dict(START), platform_sid="so-another-session-000000000000000")
    assert out == {"started": False, "why": "sid is not this session's id"} and s3.calls == []


def test_a_hash_failure_leaves_the_session_unclaimed(sup, monkeypatch):
    s3 = FakeS3()
    broken = {"v": True}

    def run(argv, input=None, **kw):
        if broken["v"] and "put_object" not in argv[2]:
            return subprocess.CompletedProcess(argv, 1, b"", b"ValueError\n")
        return s3(argv, input=input, **kw)
    popens = Popens()
    monkeypatch.setattr(sup.subprocess, "run", run)
    monkeypatch.setattr(sup.subprocess, "Popen", popens)
    monkeypatch.setattr(sup, "http_get_json", _healthy)

    out = sup.start(dict(START), platform_sid=SID)
    assert out["started"] is False and "hashed" in out["why"]
    assert s3.objects == {} and popens == []
    broken["v"] = False
    assert sup.start(dict(START), platform_sid=SID)["started"] is True and len(popens) == 1


def test_a_backend_that_cannot_launch_is_reported_and_never_relaunched(sup, monkeypatch):
    monkeypatch.setattr(sup.subprocess, "run", FakeS3())

    def popen(argv, **kw):
        raise FileNotFoundError(argv[0])
    monkeypatch.setattr(sup.subprocess, "Popen", popen)

    out = sup.start(dict(START), platform_sid=SID)
    assert out == {"started": False, "why": "the backend could not be launched: FileNotFoundError"}
    again = sup.start(dict(START), platform_sid=SID)
    assert again["why"] == "already started" and again["report"] == out
    assert sup.ping()["status"] == "Healthy"


def test_ping_is_busy_during_a_start(sup, monkeypatch):
    release = threading.Event()
    in_start = threading.Event()
    monkeypatch.setattr(sup.subprocess, "run", FakeS3())
    monkeypatch.setattr(sup.subprocess, "Popen", Popens())

    def slow_health(url, headers=None, timeout=3.0):
        if url.endswith("/health"):
            in_start.set()
            if not release.wait(5):
                raise AssertionError("never released")
            return 200, {}
        return 200, {"busy": False}
    monkeypatch.setattr(sup, "http_get_json", slow_health)

    t = threading.Thread(target=sup.start, args=(dict(START), SID))
    t.start()
    try:
        assert in_start.wait(5)
        assert sup.ping()["status"] == "HealthyBusy"
    finally:
        release.set()
        t.join(5)
    assert sup.ping()["status"] == "Healthy"


def test_ping_follows_the_backends_busy_answer(sup, monkeypatch):
    sup.STATE["proc"] = FakeProc([])
    sup.STATE["auth"] = "Basic abc"
    answers = {"v": {"busy": True}}
    seen = []

    def get(url, headers=None, timeout=3.0):
        assert url == "http://127.0.0.1:8081/api/hive/busy"
        seen.append(headers)
        if isinstance(answers["v"], Exception):
            raise answers["v"]
        return 200, answers["v"]
    monkeypatch.setattr(sup, "http_get_json", get)

    assert sup.ping()["status"] == "HealthyBusy"
    assert seen[-1]["X-Swarm-Hive-Auth"] == "Basic abc"
    answers["v"] = {"busy": False}
    assert sup.ping()["status"] == "Healthy"
    answers["v"] = ConnectionRefusedError()     # started, not answering yet: busy
    assert sup.ping()["status"] == "HealthyBusy"
    answers["v"] = {"unexpected": 1}            # an answer that is not "not busy": busy
    assert sup.ping()["status"] == "HealthyBusy"
    err = lambda code: urllib.error.HTTPError("u", code, "x", {}, None)  # noqa: E731
    answers["v"] = err(503)                     # hive_auth busy checking: transient, busy
    assert sup.ping()["status"] == "HealthyBusy"
    answers["v"] = err(404)                     # the backend cannot say: never 8 h of busy
    assert sup.ping()["status"] == "Healthy"
    sup.STATE["proc"].code = 1                  # the backend exited: nothing more to produce
    assert sup.ping()["status"] == "Healthy"


def test_ping_is_healthy_before_any_start(sup, monkeypatch):
    def get(*a, **k):
        raise AssertionError("no backend to ask before a start")
    monkeypatch.setattr(sup, "http_get_json", get)
    assert sup.ping()["status"] == "Healthy"


def test_time_of_last_update_moves_only_with_the_status(sup, monkeypatch):
    clock = {"t": 1_000_000.0}
    sup.time.time = lambda: clock["t"]
    sup.STATE["changed"] = 999_000
    busy = {"v": False}
    monkeypatch.setattr(sup, "busy", lambda: busy["v"])

    first = sup.ping()
    clock["t"] += 30
    assert sup.ping() == first == {"status": "Healthy", "time_of_last_update": 999_000}
    busy["v"] = True
    clock["t"] += 30
    b1 = sup.ping()
    assert b1 == {"status": "HealthyBusy", "time_of_last_update": 1_000_060}
    clock["t"] += 30
    assert sup.ping() == b1
    busy["v"] = False
    clock["t"] += 30
    assert sup.ping() == {"status": "Healthy", "time_of_last_update": 1_000_120}


def test_the_http_contract(sup, monkeypatch):
    monkeypatch.setattr(sup, "busy", lambda: False)
    server = sup.serve("127.0.0.1", 0)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urllib.request.urlopen(f"{base}/ping", timeout=5) as r:
            body = json.loads(r.read())
        assert body["status"] == "Healthy" and isinstance(body["time_of_last_update"], int)
        req = urllib.request.Request(f"{base}/invocations", data=b'{"action": "probe"}', method="POST")
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=5)
        assert e.value.code == 400
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        conn.putrequest("POST", "/invocations")
        conn.putheader("Content-Length", "-1")
        conn.endheaders()
        assert conn.getresponse().status == 400   # answered at once, not a read until EOF
        conn.close()
    finally:
        server.shutdown()
        server.server_close()


def test_the_supervisor_creates_no_aws_client_and_uses_only_the_standard_library():
    import ast
    import sys
    tree = ast.parse((AC / "supervisor.py").read_text())
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            mods.add(node.module.split(".")[0])
    assert mods <= set(sys.stdlib_module_names), mods - set(sys.stdlib_module_names)


def test_image_exposes_both_ports_and_the_backend_binds_all_interfaces(sup):
    text = (AC / "Dockerfile").read_text()
    lines = [ln.strip() for ln in text.splitlines()]
    assert "EXPOSE 8080 8081" in lines
    assert any(ln.startswith("FROM public.ecr.aws/amazonlinux/amazonlinux:2023") for ln in lines)
    for pkg in ("python3.12", "nodejs20", "git", "tar", "gzip", "shadow-utils", "procps-ng", "util-linux"):
        assert f" {pkg} " in text or f" {pkg}\n" in text, pkg
    assert "--strip-components=1 -C /opt/swarmai" in text and "ensure-node.sh" in text
    assert "pip install -q -e ." in text and "USER swarm" in lines
    assert '/opt/swarmai/hive/agentcore/supervisor.py"]' in text
    cmd = sup.backend_command()
    assert cmd[:4] == ["/opt/swarmai/backend/.venv/bin/python", "-m", "uvicorn", "main:app"]
    assert cmd[cmd.index("--host") + 1] == "0.0.0.0" and cmd[cmd.index("--port") + 1] == "8081"
    assert (sup.LISTEN_HOST, sup.LISTEN_PORT) == ("0.0.0.0", 8080)


def _fake_tool(bin_dir: Path, name: str, log: Path) -> None:
    p = bin_dir / name
    p.write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\n')
    p.chmod(0o755)


@pytest.mark.parametrize("tool", ["docker", "finch"])
def test_build_image_builds_arm64_and_pushes_only_the_version(tmp_path, tool):
    dist = tmp_path / "dist"
    dist.mkdir()
    name = "swarmai-hive-v9.9.9-linux-arm64.tar.gz"
    (dist / name).write_bytes(b"package")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    _fake_tool(bin_dir, tool, log)
    for t in ("bash", "mktemp", "cp", "rm", "dirname", "grep", "sha256sum", "shasum", "cat"):
        for d in ("/usr/bin", "/bin"):
            if os.path.exists(f"{d}/{t}"):
                os.symlink(f"{d}/{t}", bin_dir / t)
                break
    env = {"PATH": str(bin_dir), "DIST_DIR": str(dist), "HOME": str(tmp_path)}
    r = subprocess.run(["bash", str(AC / "build_image.sh"), "v9.9.9", "--push", "public.ecr.aws/x/hive"],
                       env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    calls = log.read_text().splitlines()
    assert calls[0].startswith(f"{tool} build --platform linux/arm64 -f {AC}/Dockerfile")
    assert "-t public.ecr.aws/x/hive:9.9.9" in calls[0] and ":latest" not in calls[0]
    assert calls[1:] == [f"{tool} push public.ecr.aws/x/hive:9.9.9"]        # no moving tag


def test_build_image_refuses_a_package_that_does_not_match_its_checksum(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    name = "swarmai-hive-v9.9.9-linux-arm64.tar.gz"
    (dist / name).write_bytes(b"package")
    (dist / f"{name}.sha256").write_text(f"{'0' * 64}  {name}\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    _fake_tool(bin_dir, "docker", log)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "DIST_DIR": str(dist), "HOME": str(tmp_path)}
    r = subprocess.run(["bash", str(AC / "build_image.sh"), "9.9.9"], env=env,
                       capture_output=True, text=True, timeout=30)
    assert r.returncode != 0 and "does not match" in r.stderr
    assert not log.exists()


def test_build_image_refuses_a_checksum_file_without_the_package(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    name = "swarmai-hive-v9.9.9-linux-arm64.tar.gz"
    (dist / name).write_bytes(b"package")
    (dist / f"{name}.sha256").write_text(f"{'0' * 64}  other.tar.gz\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    _fake_tool(bin_dir, "docker", log)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "DIST_DIR": str(dist), "HOME": str(tmp_path)}
    r = subprocess.run(["bash", str(AC / "build_image.sh"), "9.9.9"], env=env,
                       capture_output=True, text=True, timeout=30)
    assert r.returncode != 0 and "has no line" in r.stderr and not log.exists()


def test_build_image_accepts_its_own_checksum(tmp_path):
    import hashlib
    dist = tmp_path / "dist"
    dist.mkdir()
    name = "swarmai-hive-v9.9.9-linux-arm64.tar.gz"
    (dist / name).write_bytes(b"package")
    (dist / f"{name}.sha256").write_text(f"{hashlib.sha256(b'package').hexdigest()}  {name}\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    _fake_tool(bin_dir, "docker", log)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "DIST_DIR": str(dist), "HOME": str(tmp_path)}
    r = subprocess.run(["bash", str(AC / "build_image.sh"), "9.9.9"], env=env,
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert log.read_text().startswith("docker build --platform linux/arm64")


def test_release_job_is_skipped_without_its_settings_and_never_holds_the_release():
    wf = yaml.safe_load((REPO / ".github" / "workflows" / "release.yml").read_text())
    jobs = wf["jobs"]
    job = jobs["agentcore-image"]
    assert set(job["needs"]) >= {"prepare", "build-hive"}
    assert job["if"] == "needs.prepare.outputs.agentcore == 'true' && !inputs.dry_run"
    assert job["runs-on"] == "ubuntu-24.04-arm"
    assert job["permissions"]["id-token"] == "write"
    assert "agentcore-image" not in jobs["publish"]["needs"]
    step = next(s for s in jobs["prepare"]["steps"] if s.get("id") == "agentcore")
    assert step["env"] == {"REPO": "${{ vars.AGENTCORE_IMAGE_REPO }}",
                           "ROLE": "${{ secrets.AGENTCORE_PUSH_ROLE_ARN }}"}
    assert jobs["prepare"]["outputs"]["agentcore"] == "${{ steps.agentcore.outputs.enabled }}"
    for repo, role, want in (("", "", "false"), ("r", "", "false"), ("", "a", "false"), ("r", "a", "true")):
        out = Path(os.environ.get("TMPDIR", "/tmp")) / f"gho-{os.getpid()}-{repo}{role}"
        out.write_text("")
        try:
            subprocess.run(["bash", "-c", step["run"]], check=True, capture_output=True, timeout=10,
                           env={"PATH": "/usr/bin:/bin", "REPO": repo, "ROLE": role, "GITHUB_OUTPUT": str(out)})
            assert out.read_text().strip() == f"enabled={want}"
        finally:
            out.unlink()
    runs = "\n".join(s.get("run", "") for s in job["steps"])
    assert 'bash hive/agentcore/build_image.sh "$VERSION" --push "$REPO"' in runs
    creds = next(s for s in job["steps"] if "configure-aws-credentials" in s.get("uses", ""))
    assert creds["with"]["role-to-assume"] == "${{ secrets.AGENTCORE_PUSH_ROLE_ARN }}"
    login = next(s for s in job["steps"] if "amazon-ecr-login" in s.get("uses", ""))
    assert login["with"]["registry-type"] == "public"


def test_a_start_the_platform_did_not_name_is_refused(sup, monkeypatch):
    s3 = FakeS3()
    monkeypatch.setattr(sup.subprocess, "run", s3)
    out = sup.start(dict(START))                                  # no session header
    assert out == {"started": False, "why": "sid is not this session's id"} and s3.calls == []


@pytest.mark.parametrize("sid", ["short-session-id", "so.run.1234567890abcdef.round.0001"])
def test_a_session_id_the_backend_would_not_serve_is_refused(sup, monkeypatch, sid):
    from core import hive_agentcore
    assert not hive_agentcore.SID_RE.fullmatch(sid)
    s3 = FakeS3()
    monkeypatch.setattr(sup.subprocess, "run", s3)
    out = sup.start(dict(START, sid=sid), platform_sid=sid)
    assert out["started"] is False and "valid name" in out["why"] and s3.calls == []


def test_the_session_id_rule_is_the_backends(sup):
    from core import hive_agentcore
    assert sup._SID_RE.pattern == "^" + hive_agentcore.SID_RE.pattern + "$"
