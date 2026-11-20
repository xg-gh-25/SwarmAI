"""Feature Map sandbox: environment, isolation, cleanup (R3.1, R3.2) and environment
failures reported as not run (R3.4)."""
from __future__ import annotations

import json
import os
import socket
import sys
import time
from pathlib import Path

import pytest

from core.feature_map import files
from core.feature_map.sandbox import Sandbox, SandboxError
from tests.feature_map_fixtures import git

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")

# a service that serves HTTP, writes its environment, and leaves a grandchild holding a
# port in its group: cleanup must kill the whole group
SERVICE = r'''
import http.server, json, os, socket, subprocess, sys, threading
port, keep = int(sys.argv[1]), int(sys.argv[2])
json.dump({"env": dict(os.environ), "cwd": os.getcwd()}, open(sys.argv[3], "w"))
subprocess.Popen([sys.executable, "-c",
    "import socket,time;s=socket.socket();s.bind(('127.0.0.1',%d));s.listen();time.sleep(600)" % keep])
http.server.ThreadingHTTPServer(("127.0.0.1", port), http.server.SimpleHTTPRequestHandler).serve_forever()
'''


def _free() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _answers(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def _repo(tmp_path: Path, target: str) -> tuple[Path, files.FeatureMap]:
    root = tmp_path / "repo"
    (root / ".swarmai/features/targets").mkdir(parents=True)
    root.joinpath("svc.py").write_text(SERVICE)
    root.joinpath("tracked.txt").write_text("tracked")
    git(root, "init", "-q")
    (root / ".swarmai/features/targets/web.yaml").write_text(target)
    (root / ".swarmai/features/features.yaml").write_text(
        "schema: 1\nfeatures:\n  - {id: f-a1, title: F, target: web, run: [\"true\"]}\n")
    git(root, "add", "-A")
    root.joinpath("untracked.txt").write_text("u")      # untracked, not ignored: copied
    return root, files.load_folder(root / ".swarmai/features")


def test_env_home_aws_and_group_cleanup(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_TOKEN", "do-not-leak")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA-leak")
    keep = _free()
    dump = tmp_path / "env.json"
    root, fmap = _repo(tmp_path, f"""id: web
kind: web
services:
  - {{id: ui, argv: [{sys.executable}, svc.py, "{{port.ui}}", "{keep}", "{dump}"], ready: "http://127.0.0.1:{{port.ui}}/"}}
env: {{EXTRA: "{{port.ui}}"}}
""")
    before = sorted(p.name for p in root.iterdir())
    with Sandbox(root) as sb:
        sb.start(fmap, fmap.targets["web"])
        seen = json.loads(dump.read_text())
        assert _answers(sb.ports["ui"]) and _answers(keep)
        sandbox_dir = sb.dir
        assert (sb.checkout / "untracked.txt").is_file() and (sb.checkout / "tracked.txt").is_file()
    env = seen["env"]
    # R3.1: only allowlisted variables, a HOME inside the sandbox, empty AWS files
    assert "SECRET_TOKEN" not in env and "AWS_ACCESS_KEY_ID" not in env
    assert Path(env["HOME"]).is_relative_to(sandbox_dir)
    assert env["AWS_CONFIG_FILE"] == env["AWS_SHARED_CREDENTIALS_FILE"]
    assert env["AWS_EC2_METADATA_DISABLED"] == "true"
    assert env["EXTRA"].isdigit() and env["EXTRA"] != "{port.ui}"   # placeholder expanded
    assert Path(seen["cwd"]).resolve().is_relative_to(sandbox_dir.resolve()), "services run in the copy"
    # R3.2: the whole group is gone (the grandchild's port too), the sandbox removed
    deadline = time.monotonic() + 5
    while _answers(keep) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not _answers(keep), "a grandchild in the service's group outlived cleanup"
    assert not sandbox_dir.exists()
    # the person's working tree was never written
    assert sorted(p.name for p in root.iterdir()) == before


def test_cleanup_never_signals_the_callers_group(tmp_path):
    root, fmap = _repo(tmp_path, f"""id: web
kind: web
services:
  - {{id: ui, argv: [{sys.executable}, -m, http.server, "{{port.ui}}", --bind, 127.0.0.1], ready: "http://127.0.0.1:{{port.ui}}/"}}
""")
    with Sandbox(root) as sb:
        sb.start(fmap, fmap.targets["web"])
        pid = sb.procs[0][1].pid
        assert os.getpgid(pid) != os.getpgid(0), "the service runs in its own group"
    assert os.getpgid(0) == os.getpgid(0)      # we are still alive to say so


def test_environment_failures_are_not_run(tmp_path):
    cases = {
        "prepare fails": ('prepare: [["false"]]\n', "dependencies missing"),
        "missing command": ("prepare: [[no-such-command-xyz]]\n", "needs no-such-command-xyz"),
        "service exits": (f'services:\n  - {{id: ui, argv: [{sys.executable}, -c, "raise SystemExit(3)"], ready: "http://127.0.0.1:{{port.ui}}/"}}\n',
                          "launch failed"),
        "never ready": (f'ready_timeout_s: 2\nservices:\n  - {{id: ui, argv: [{sys.executable}, -c, "import time; time.sleep(30)"], ready: "http://127.0.0.1:{{port.ui}}/"}}\n',
                        "not ready"),
    }
    for i, (name, (extra, reason)) in enumerate(cases.items()):
        root, fmap = _repo(tmp_path / str(i), "id: web\nkind: web\n" + extra)
        with Sandbox(root) as sb:
            with pytest.raises(SandboxError) as exc:
                sb.start(fmap, fmap.targets["web"])
        assert exc.value.reason == reason, (name, exc.value)


def test_port_taken_is_refused(tmp_path, monkeypatch):
    import core.feature_map.sandbox as sbmod
    held = socket.socket()
    held.bind(("127.0.0.1", 0))
    held.listen()
    port = held.getsockname()[1]
    monkeypatch.setattr(sbmod, "_free_port", lambda taken=(): port)
    try:
        root, fmap = _repo(tmp_path, f"""id: web
kind: web
services:
  - {{id: ui, argv: [{sys.executable}, -m, http.server, "{{port.ui}}"], ready: "http://127.0.0.1:{{port.ui}}/"}}
""")
        with Sandbox(root) as sb:
            with pytest.raises(SandboxError) as exc:
                sb.start(fmap, fmap.targets["web"])
        assert exc.value.reason == "port taken"
    finally:
        held.close()


def test_ready_and_base_urls_must_be_loopback(tmp_path):
    from core.feature_map.sandbox import loopback_url
    for good in ("http://127.0.0.1:5/", "https://localhost/x", "http://[::1]:9/"):
        assert loopback_url(good, "ready") == good
    for bad in ("http://169.254.169.254/latest/meta-data/", "file:///etc/passwd",
                "ftp://127.0.0.1/", "http://127.0.0.1.evil.example/", "http://10.0.0.1/"):
        with pytest.raises(SandboxError) as err:
            loopback_url(bad, "ready")
        assert err.value.reason == "bad file"
    # refused before the service starts: nothing runs, nothing polled
    root, fmap = _repo(tmp_path, f"""id: web
kind: web
services:
  - {{id: ui, argv: [{sys.executable}, -c, "import time; time.sleep(60)"], ready: "http://169.254.169.254/"}}
""")
    with Sandbox(root) as sb:
        with pytest.raises(SandboxError) as err:
            sb.start(fmap, fmap.targets["web"])
        assert err.value.reason == "bad file" and sb.procs == []


def test_package_caches_are_the_tools_own_folders_per_platform(tmp_path):
    from core.feature_map.sandbox import cache_dirs
    home = tmp_path / "h"
    mac = cache_dirs(home, "darwin", {})
    assert mac["UV_CACHE_DIR"] == str(home / "Library/Caches/uv")
    assert mac["PIP_CACHE_DIR"] == str(home / "Library/Caches/pip")
    assert mac["npm_config_cache"] == str(home / ".npm")
    linux = cache_dirs(home, "linux", {"XDG_CACHE_HOME": "/x"})
    assert linux["UV_CACHE_DIR"] == "/x/uv" and linux["PIP_CACHE_DIR"] == "/x/pip"
    assert cache_dirs(home, "linux", {})["UV_CACHE_DIR"] == str(home / ".cache/uv")
    win = cache_dirs(home, "win32", {"LOCALAPPDATA": str(tmp_path / "L")})
    assert win["UV_CACHE_DIR"] == str(tmp_path / "L" / "uv" / "cache")
    assert win["npm_config_cache"] == str(tmp_path / "L" / "npm-cache")
    # a variable the person set wins
    assert cache_dirs(home, "darwin", {"UV_CACHE_DIR": "/mine"})["UV_CACHE_DIR"] == "/mine"


def test_ports_come_from_below_the_ephemeral_range_and_differ():
    from core.feature_map.sandbox import PORT_RANGE, _free_port
    low, high = PORT_RANGE
    assert high <= 32768, "below Linux's ephemeral range, so no bind(0) can take it"
    ports = []
    for _ in range(30):
        ports.append(_free_port(ports))
    assert all(low <= p < high for p in ports) and len(set(ports)) == len(ports)
    # a port someone listens on is never chosen
    busy = socket.socket()
    busy.bind(("127.0.0.1", ports[0]))
    busy.listen()
    try:
        import random
        orig = random.randrange
        seq = iter([ports[0], ports[1]])
        random.randrange = lambda *_a: next(seq)
        try:
            assert _free_port() == ports[1]
        finally:
            random.randrange = orig
    finally:
        busy.close()
