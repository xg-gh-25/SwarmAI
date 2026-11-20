"""Feature Map sandbox: once a process of the sandbox has run, the person's own process never
writes there by a name that process could have set up (core/feature_map/sandbox.py).

On a machine with a replay user the sandbox folder is shared with that user, and the person's
process (on a Hive: the Hive user, who can reach the instance's credentials) writes into it. A
link the replayed app plants at a name that process later writes would turn the write into one
anywhere the person can write: a log becomes a ``.pth`` file in the backend's venv, the empty
AWS file empties any file. Questions: is a log, a spawn's environment file, the generated
Playwright config and the empty AWS file each written without following a planted link (and
the file outside left as it was); is a start state's folder emptied and made, and a fixture
copied, by the replay user and never by the person's process; and are the proxy variables kept
from the replayed app when another user runs it? The uid switch itself is the replay user's
own test (test_feature_map_replay_user.py, Linux).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.feature_map import files, sandbox
from core.feature_map.sandbox import Sandbox, SandboxError, allowed_env
from tests.feature_map_fixtures import git
from tests.test_feature_map_replay_user import _fake_sudo, _mode, _sudo_calls, _user

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX links")


def _repo(tmp_path: Path, target: str) -> tuple[Path, files.FeatureMap]:
    root = tmp_path / "repo"
    (root / ".swarmai/features/targets").mkdir(parents=True)
    (root / ".swarmai/features/fixtures").mkdir()
    (root / ".swarmai/features/fixtures/seed.txt").write_text("seed")
    git(root, "init", "-q")
    (root / ".swarmai/features/targets/web.yaml").write_text(target)
    (root / ".swarmai/features/features.yaml").write_text(
        "schema: 1\nfeatures:\n  - {id: f-a1, title: F, target: web, run: [\"true\"]}\n")
    git(root, "add", "-A")
    return root, files.load_folder(root / ".swarmai/features")


def _victim(tmp_path: Path) -> Path:
    victim = tmp_path / "outside" / "victim.txt"
    victim.parent.mkdir(parents=True, exist_ok=True)
    victim.write_text("the person's file")
    return victim


def test_a_log_name_a_step_planted_is_refused_and_nothing_outside_is_written(tmp_path):
    victim = _victim(tmp_path)
    root, fmap = _repo(tmp_path, f"""id: web
kind: command
prepare:
  - [ln, -s, "{victim}", "{{sandbox}}/logs/web-prepare-1.log"]
  - [sh, -c, "echo written-through-the-link"]
""")
    with Sandbox(root) as sb:
        with pytest.raises(SandboxError) as err:
            sb.prepare(fmap, fmap.targets["web"])
        assert err.value.reason == "launch failed" and "exists before the sandbox made it" in err.value.detail
    assert victim.read_text() == "the person's file"


def test_a_service_log_planted_before_it_starts_is_refused(tmp_path):
    victim = _victim(tmp_path)
    root, fmap = _repo(tmp_path, f"""id: web
kind: command
prepare:
  - [ln, -s, "{victim}", "{{sandbox}}/logs/web-ui.log"]
services:
  - id: ui
    argv: [sh, -c, "echo written-through-the-link; sleep 30"]
    ready: "http://127.0.0.1:{{port.ui}}/"
""")
    with Sandbox(root) as sb:
        with pytest.raises(SandboxError) as err:
            sb.start(fmap, fmap.targets["web"])
        assert "exists before the sandbox made it" in err.value.detail
    assert victim.read_text() == "the person's file"


def test_a_log_is_one_file_for_every_start_and_its_tail_is_read_from_it(tmp_path):
    root, fmap = _repo(tmp_path, """id: web
kind: command
prepare:
  - [sh, -c, "echo first-line; exit 3"]
""")
    with Sandbox(root) as sb:
        with pytest.raises(SandboxError) as err:
            sb.prepare(fmap, fmap.targets["web"])
        assert "first-line" in err.value.detail
        log = sb.log_path("web-prepare-0.log")
        # replaced by a link once written: the tail still comes from the sandbox's own file
        log.rename(log.with_suffix(".moved"))
        log.symlink_to(_victim(tmp_path))
        assert sb._tail("web-prepare-0.log") == ["first-line"]


def test_the_empty_aws_file_and_home_are_made_once_and_never_written_again(tmp_path):
    victim = _victim(tmp_path)
    root, fmap = _repo(tmp_path, f"""id: web
kind: command
prepare:
  - [sh, -c, 'rm -f "{{sandbox}}/empty" && ln -s "{victim}" "{{sandbox}}/empty"']
  - [sh, -c, 'test -L "$AWS_CONFIG_FILE"']
""")
    with Sandbox(root) as sb:
        assert (sb.dir / "empty").read_text() == "" and (sb.dir / "home").is_dir()
        sb.prepare(fmap, fmap.targets["web"])       # the second step computes the env again
        sb.env(fmap.targets["web"])
    assert victim.read_text() == "the person's file"


def test_allowed_env_writes_nothing(tmp_path):
    env = allowed_env(tmp_path / "sb", tmp_path / "home")
    assert env["HOME"] == str(tmp_path / "sb" / "home") and not (tmp_path / "sb").exists()


def test_write_new_never_follows_a_link_and_is_readable_by_the_group(tmp_path, monkeypatch):
    victim = _victim(tmp_path)
    root, _fmap = _repo(tmp_path, "id: web\nkind: command\n")
    with Sandbox(root) as sb:
        a = sb.write_new("playwright.config", ".mjs", "a")
        b = sb.write_new("playwright.config", ".mjs", "b")
        assert a != b and a.parent == sb.dir and a.read_text() == "a"
        assert a.stat().st_mode & 0o777 == 0o640
        monkeypatch.setattr(sandbox.secrets, "token_hex", lambda n: "guessed")
        (sb.dir / "playwright.config.guessed.mjs").symlink_to(victim)
        with pytest.raises(FileExistsError):
            sb.write_new("playwright.config", ".mjs", "through the link")
        link = sb.dir / "report.json"
        link.symlink_to(victim)
        with pytest.raises(OSError):
            sb.read_new(link)
    assert victim.read_text() == "the person's file"


def test_the_spawn_environment_is_a_new_file_never_a_planted_one(tmp_path, monkeypatch):
    fake, _log = _fake_sudo(tmp_path)
    victim = _victim(tmp_path)
    root, _fmap = _repo(tmp_path, "id: web\nkind: command\n")
    sb = Sandbox(root, user=_user(tmp_path, fake))
    monkeypatch.setattr(sb.user.__class__, "grant", lambda self, folder: None)
    with sb:
        monkeypatch.setattr(sandbox.secrets, "token_hex", lambda n: "guessed")
        (sb.dir / ".spawn.guessed.json").symlink_to(victim)
        with pytest.raises(FileExistsError):
            sb.popen(["true"], cwd=sb.dir, env={"PATH": os.environ["PATH"]})
    assert victim.read_text() == "the person's file"


def test_with_a_replay_user_the_state_folder_and_fixture_are_that_users_work(tmp_path, monkeypatch):
    fake, log = _fake_sudo(tmp_path)
    root, fmap = _repo(tmp_path, """id: web
kind: command
start_states:
  a: {fixture: fixtures/seed.txt, to: "{state_dir}/seed.txt"}
""")
    sb = Sandbox(root, user=_user(tmp_path, fake))
    monkeypatch.setattr(sb.user.__class__, "grant", lambda self, folder: None)
    monkeypatch.setattr(sb.user.__class__, "kill_all", lambda self: 0)
    with sb:
        sb.features = sb.checkout / ".swarmai/features"

        def person_writes(*_a, **_k):
            raise AssertionError("the person's process wrote in the sandbox after a start")
        (sb.dir / "state" / "a").mkdir()
        (sb.dir / "state" / "a" / "leftover").write_text("x")
        with monkeypatch.context() as m:
            m.setattr(sandbox.shutil, "rmtree", person_writes)
            m.setattr(sandbox.shutil, "copytree", person_writes)
            m.setattr(sandbox.shutil, "copy2", person_writes)
            sb.fresh_start(fmap, fmap.targets["web"], "a")
            sb.setup(fmap, fmap.targets["web"], "a")
        folder = sb.dir / "state" / "a"
        assert sorted(p.name for p in folder.iterdir()) == ["seed.txt"]
        assert (folder / "seed.txt").read_text() == "seed"
        modes = [(_mode(c), c[c.index(_mode(c)) + 1:]) for c in _sudo_calls(log)]
        assert ("rm", [str(folder)]) in modes and ("mkdir", [str(folder)]) in modes
        assert ("copy", [str(sb.features / "fixtures/seed.txt"), str(folder / "seed.txt")]) in modes


def test_a_fixture_copy_that_fails_as_the_replay_user_is_launch_failed(tmp_path, monkeypatch):
    fake, _log = _fake_sudo(tmp_path)
    root, fmap = _repo(tmp_path, """id: web
kind: command
start_states:
  a: {fixture: fixtures/seed.txt, to: "{sandbox}/no-such/deeper/seed.txt"}
""")
    sb = Sandbox(root, user=_user(tmp_path, fake))
    monkeypatch.setattr(sb.user.__class__, "grant", lambda self, folder: None)
    monkeypatch.setattr(sb.user.__class__, "kill_all", lambda self: 0)
    monkeypatch.setattr(sb.user.__class__, "copy", lambda self, src, dest: "Permission denied")
    with sb:
        sb.features = sb.checkout / ".swarmai/features"
        sb.fresh_start(fmap, fmap.targets["web"], "a")
        with pytest.raises(SandboxError) as err:
            sb.setup(fmap, fmap.targets["web"], "a")
        assert err.value.reason == "launch failed" and "Permission denied" in err.value.detail


def test_proxy_variables_stay_with_the_person(tmp_path, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:3128")
    monkeypatch.setenv("no_proxy", "internal")
    assert allowed_env(tmp_path, tmp_path)["HTTPS_PROXY"] == "http://proxy.example:3128"
    other = allowed_env(tmp_path, tmp_path, own_caches=False)
    assert not {"HTTPS_PROXY", "no_proxy"} & set(other)
    # a target's own env still applies
    assert allowed_env(tmp_path, tmp_path, {"NO_PROXY": "127.0.0.1"}, own_caches=False)["NO_PROXY"] == "127.0.0.1"
    sb = Sandbox(tmp_path, user=SimpleNamespace(home=tmp_path))
    sb.dir = tmp_path
    assert "HTTPS_PROXY" not in sb.env(SimpleNamespace(env={}))


def test_read_new_refuses_a_pipe_and_a_file_too_large(tmp_path, monkeypatch):
    root, _fmap = _repo(tmp_path, "id: web\nkind: command\n")
    with Sandbox(root) as sb:
        fifo = sb.dir / "report-web-default.json"
        os.mkfifo(fifo)
        with pytest.raises(OSError, match="not a regular file"):
            sb.read_new(fifo)                   # never blocks waiting for a writer
        big = sb.dir / "list-web.json"
        big.write_text("x" * 20)
        monkeypatch.setattr(sandbox, "_READ_NEW_MAX", 10)
        with pytest.raises(OSError, match="larger than"):
            sb.read_new(big)


def test_the_helpers_mkdir_and_copy(tmp_path):
    helper = [sys.executable, "-I", str(Path(sandbox.__file__).with_name("replay_user_helper.py"))]
    import subprocess
    run = lambda *a: subprocess.run([*helper, *a], capture_output=True, text=True, timeout=30)
    assert run("mkdir", str(tmp_path / "made")).returncode == 0 and (tmp_path / "made").is_dir()
    r = run("mkdir", str(tmp_path / "made"))
    assert r.returncode == 1 and "mkdir" in r.stderr
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "f.txt").write_text("f")
    (src / "link").symlink_to("sub/f.txt")
    dest = tmp_path / "dest"
    (dest / "sub").mkdir(parents=True)
    (dest / "kept").write_text("k")
    assert run("copy", str(src), str(dest)).returncode == 0
    assert (dest / "sub" / "f.txt").read_text() == "f" and (dest / "kept").read_text() == "k"
    assert os.readlink(dest / "link") == "sub/f.txt", "a link is copied as a link"
    assert run("copy", str(src / "sub" / "f.txt"), str(tmp_path / "a" / "b" / "f.txt")).returncode == 0
    r = run("copy", str(tmp_path / "missing"), str(tmp_path / "x"))
    assert r.returncode == 1 and "copy" in r.stderr


def test_readiness_never_follows_a_redirect(tmp_path):
    import http.server
    import threading
    hits = []

    class Elsewhere(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_a):
            pass
    other = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Elsewhere)

    class Redirect(Elsewhere):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{other.server_port}/latest/meta-data/")
            self.end_headers()
    svc = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    for s in (other, svc):
        threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        sb = Sandbox(tmp_path)
        proc = SimpleNamespace(poll=lambda: None)
        sb._wait_ready(SimpleNamespace(ready_timeout_s=5), "ui", f"http://127.0.0.1:{svc.server_port}/", proc)
        assert hits == [], "the probe followed the redirect"
    finally:
        for s in (other, svc):
            s.shutdown()
