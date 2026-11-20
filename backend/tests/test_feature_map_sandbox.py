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


# records its argv, its environment and every path it is asked for; leaves a grandchild
# holding a port in its group, and says which port in its dump
STATE_SERVICE = r'''
import http.server, json, os, socket, subprocess, sys
port, dump = int(sys.argv[1]), sys.argv[2]
s = socket.socket(); s.bind(("127.0.0.1", 0)); held = s.getsockname()[1]; s.close()
subprocess.Popen([sys.executable, "-c",
    "import socket,time;s=socket.socket();s.bind(('127.0.0.1',%d));s.listen();time.sleep(600)" % held])
json.dump({"argv": sys.argv, "env": dict(os.environ), "pid": os.getpid(), "held": held},
          open(dump, "w"))
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        open(dump + ".paths", "a").write(self.path + "\n")
        self.send_response(200); self.end_headers()
    def log_message(self, *_a):
        pass
http.server.ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
'''

STATE_TARGET = f"""id: web
kind: web
services:
  - id: ui
    argv: [{sys.executable}, svc2.py, "{{port.ui}}", "{{state_dir}}/svc.json", "{{state_dir}}"]
    ready: "http://127.0.0.1:{{port.ui}}/ready?d={{state_dir}}"
env: {{DATA: "{{state_dir}}/data"}}
start_states:
  a:
    fixture: fx.txt
    to: "{{state_dir}}/fx.txt"
    setup: [[{sys.executable}, -c, "import sys; open(sys.argv[1], 'w').write('set')", "{{state_dir}}/setup.txt"]]
  b: {{}}
"""


def _state_repo(tmp_path: Path) -> tuple[Path, files.FeatureMap]:
    root, _fmap = _repo(tmp_path, STATE_TARGET)
    root.joinpath("svc2.py").write_text(STATE_SERVICE)
    (root / ".swarmai/features/fx.txt").write_text("fixture")
    return root, files.load_folder(root / ".swarmai/features")


def test_state_dir_expands_in_argv_env_ready_and_setup(tmp_path):
    root, fmap = _state_repo(tmp_path)
    target = fmap.targets["web"]
    with Sandbox(root) as sb:
        sb.fresh_start(fmap, target, "a")
        sb.setup(fmap, target, "a")
        state = sb.dir / "state" / "a"
        assert sb.expand("{state_dir}") == str(state)
        seen = json.loads((state / "svc.json").read_text())
        assert seen["argv"][2:] == [str(state / "svc.json"), str(state)], "service argv"
        assert seen["env"]["DATA"] == f"{state}/data", "target env"
        assert f"/ready?d={state}" in (state / "svc.json.paths").read_text().split(), "ready URL"
        assert (state / "setup.txt").read_text() == "set", "setup command"
        assert (state / "fx.txt").read_text() == "fixture", "fixture to"


def test_enter_state_restarts_on_the_same_ports_and_kills_the_old_group(tmp_path):
    root, fmap = _state_repo(tmp_path)
    target = fmap.targets["web"]
    with Sandbox(root) as sb:
        sb.fresh_start(fmap, target, "a")
        first = json.loads((sb.dir / "state/a/svc.json").read_text())
        port = sb.ports["ui"]
        assert _answers(first["held"])
        sb.fresh_start(fmap, target, "b")
        second = json.loads((sb.dir / "state/b/svc.json").read_text())
        # a new process in its own group, on the same port; the old group is gone
        assert second["pid"] != first["pid"] and sb.ports["ui"] == port
        assert [p.pid for _sid, p in sb.procs] == [second["pid"]]
        assert os.getpgid(second["pid"]) != os.getpgid(0)
        deadline = time.monotonic() + 5
        while _answers(first["held"]) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not _answers(first["held"]), "the old service's group outlived the restart"
        with pytest.raises(ProcessLookupError):
            os.kill(first["pid"], 0)
        # b's folder is its own and starts empty (only what b's service wrote itself)
        assert sorted(p.name for p in (sb.dir / "state/b").iterdir()) == ["svc.json", "svc.json.paths"]
        # a state run again starts from an empty folder too
        (sb.dir / "state/a/leftover").write_text("x")
        sb.fresh_start(fmap, target, "a")
        assert not (sb.dir / "state/a/leftover").exists()


def test_prepare_never_writes_into_a_start_states_folder(tmp_path):
    """A target prepared after another target's state ran: its prepare sees
    state/.prepare as {state_dir}, and the running state's folder is left as it was."""
    root, _fmap = _state_repo(tmp_path)
    (root / ".swarmai/features/targets/api.yaml").write_text(
        f'id: api\nkind: command\nprepare: [[{sys.executable}, -c, '
        f'"import sys; open(sys.argv[1], \'w\').write(\'p\')", "{{state_dir}}/prep.txt"]]\n')
    fmap = files.load_folder(root / ".swarmai/features")
    with Sandbox(root) as sb:
        sb.fresh_start(fmap, fmap.targets["web"], "a")
        sb.prepare(fmap, fmap.targets["api"])
        assert (sb.dir / "state/.prepare/prep.txt").is_file()
        assert not (sb.dir / "state/a/prep.txt").exists()
        assert sb.state_dir == sb.dir / "state" / "a", "the running state's folder is restored"


def test_a_state_name_that_is_not_a_folder_name_is_refused(tmp_path):
    root, fmap = _state_repo(tmp_path)
    # the folder never accepts such a name ...
    (root / ".swarmai/features/targets/bad.yaml").write_text(
        "id: bad\nkind: web\nstart_states:\n  ../x: {}\n")
    with pytest.raises(files.FeatureMapError):
        files.load_folder(root / ".swarmai/features")
    (root / ".swarmai/features/targets/bad.yaml").unlink()
    # ... and fresh_start, which empties the state's folder, refuses one that arrives
    # another way, before it stops anything
    target = fmap.targets["web"]
    with Sandbox(root) as sb:
        sb.fresh_start(fmap, target, "a")
        outside = sb.dir / "x"
        outside.mkdir()
        (outside / "keep").write_text("k")
        running = [p.pid for _sid, p in sb.procs]
        for name in ("../x", str(outside), ".", "..", ".prepare", "a/b", ""):
            with pytest.raises(SandboxError) as err:
                sb.fresh_start(fmap, target, name)
            assert err.value.reason == "bad file", name
        assert (outside / "keep").is_file() and (sb.dir / "state/.prepare").is_dir()
        assert [p.pid for _sid, p in sb.procs] == running, "nothing was stopped"
