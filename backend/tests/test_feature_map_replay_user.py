"""The replay user: on a machine whose instance metadata service answers, every process of a
replay runs as a user the host's firewall keeps off it (core/feature_map/replay_user.py).

Questions: does a machine without a metadata service replay as the person, with no sudo; is a
machine with one refused (not_run, nothing of the repo started) when the replay user cannot be
used; with the replay user, does every start of the sandbox go through sudo as that user, its
environment in a file (never on a command line), and only after the probe; are the processes
of a start stopped by their tag, also one that left its process group; is a folder the replay
user wrote removed in full; are the caches that user's own; and does the probe report the
metadata service and paths out of reach?

A fake sudo (it runs the command as the same user, logging its argv) stands in for the real
one: the uid switch, the firewall and real sudo are checked on a Hive in a user namespace (the
run's manual check) and after the next update.
"""
from __future__ import annotations

import json
import os
import pwd
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.feature_map import files, replay_user, sandbox
from core.feature_map.replay import Selection, replay
from tests.feature_map_fixtures import make_repo

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"),
                                reason="the replay user exists on Linux")


@pytest.fixture
def fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.delenv("SWARMAI_MODE", raising=False)
    monkeypatch.setattr(replay_user, "_current", None)
    monkeypatch.setattr(replay_user, "_lock_fd", None)


def _fake_sudo(tmp_path: Path) -> tuple[Path, Path]:
    """A sudo that checks the `-n -u <user> --` form, logs its argv and runs the command as
    this same user."""
    log = tmp_path / "sudo.log"
    fake = tmp_path / "bin" / "sudo"
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "a = sys.argv[1:]\n"
        "assert a[0] == '-n' and a[1] == '-u' and a[3] == '--', a\n"
        f"open({str(log)!r}, 'a').write(json.dumps(a) + '\\n')\n"
        "os.execv(a[4], a[4:])\n")
    fake.chmod(0o755)
    return fake, log


def _user(tmp_path: Path, sudo: Path) -> replay_user.ReplayUser:
    home = tmp_path / "replay-home"
    home.mkdir(exist_ok=True)
    me = pwd.getpwuid(os.getuid())
    return replay_user.ReplayUser(me.pw_name, os.getuid(), home, str(sudo), sys.executable)


class _HelperOs:
    """The helper module's `os`, with some calls replaced; every other name is the real os.
    Set on the helper module only, so the real os module (pytest's own) is never patched."""

    def __init__(self, **calls):
        self.__dict__.update(calls)

    def __getattr__(self, name):
        return getattr(os, name)


def _mode(call: list[str]) -> str:
    """The helper mode a logged sudo call ran (exec, kill, rm, probe)."""
    return call[call.index(str(replay_user.HELPER)) + 1]


def _sudo_calls(log: Path) -> list[list[str]]:
    return [json.loads(ln) for ln in log.read_text().splitlines()] if log.exists() else []


WALKS = ("schema: 1\nfeatures:\n"
         "  - {id: cmd-a1, title: Writes, target: web, run: [sh, -c, 'echo \"$HOME|$FEATURE_MAP_SPAWN|$FM_VALUE\" > \"$OUT\"']}\n")
TARGET = """id: web
kind: command
prepare:
  - [sh, -c, 'echo prepared > "{sandbox}/prepared"']
env:
  FM_VALUE: "only-in-the-env-file"
  OUT: "{sandbox}/walk.out"
"""


def test_no_metadata_service_runs_as_the_person(tmp_path, fresh, monkeypatch):
    monkeypatch.setattr(replay_user, "imds_answers", lambda *a, **k: False)
    monkeypatch.setattr(replay_user, "_user_exists", lambda: False)

    def no_sudo(*_a, **_k):
        raise AssertionError("sudo was called on a machine without a metadata service")
    monkeypatch.setattr(replay_user.ReplayUser, "command", no_sudo)
    root = make_repo(tmp_path / "r", specs={}, target=TARGET, features=WALKS)
    fmap = files.load_folder(root / ".swarmai/features")
    results, sbdir = replay(fmap, root, Selection(list(fmap.features)))
    assert [r["result"] for r in results] == ["pass"], results
    assert replay_user.current() is None
    replay_user.remove_tree(sbdir)


@pytest.mark.parametrize("why", ["no user", "probe refuses"])
def test_missing_boundary_is_not_run_and_starts_nothing(tmp_path, fresh, monkeypatch, why):
    monkeypatch.setattr(replay_user, "needs_replay_user", lambda: True)
    if why == "no user":
        monkeypatch.setattr(replay_user, "REPLAY_USER", "swarm-replay-absent-user")
    else:
        fake, _log = _fake_sudo(tmp_path)
        me = pwd.getpwuid(os.getuid())
        monkeypatch.setattr(replay_user, "REPLAY_USER", me.pw_name)
        monkeypatch.setattr(replay_user, "_find_sudo", lambda: str(fake))
        monkeypatch.setattr(replay_user.ReplayUser, "kill_all", lambda self: 0)
        monkeypatch.setattr(replay_user.ReplayUser, "probe", lambda self, paths=(): [
            "the instance metadata service answers this user at 169.254.169.254:80"])
    started = tmp_path / "started"
    root = make_repo(tmp_path / "r", specs={}, target=TARGET.replace(
        "prepare:\n", f"prepare:\n  - [touch, {started}]\n"), features=WALKS)
    fmap = files.load_folder(root / ".swarmai/features")
    results, sbdir = replay(fmap, root, Selection(list(fmap.features)))
    assert sbdir is None
    assert [(r["result"], r["reason"]) for r in results] == [("not_run", "runtime unavailable")]
    assert ("has no swarm-replay-absent-user" if why == "no user" else "metadata service answers") \
        in results[0]["error"]
    assert not started.exists(), "a repo command ran without the boundary"


def test_every_start_runs_as_the_replay_user(tmp_path, fresh, monkeypatch):
    fake, log = _fake_sudo(tmp_path)
    user = _user(tmp_path, fake)
    probed = []
    monkeypatch.setattr(replay_user, "needs_replay_user", lambda: True)
    monkeypatch.setattr(replay_user.ReplayUser, "kill_all", lambda self: 0)
    monkeypatch.setattr(replay_user, "REPLAY_USER", user.name)
    monkeypatch.setattr(replay_user.pwd, "getpwnam", lambda name: SimpleNamespace(
        pw_uid=user.uid, pw_dir=str(user.home)))
    monkeypatch.setattr(replay_user, "_find_sudo", lambda: str(fake))

    def probe(self, paths=()):
        probed.append(len(_sudo_calls(log)))
        assert self.sudo == str(fake)
        return []                              # this machine's own metadata service: closed
    monkeypatch.setattr(replay_user.ReplayUser, "probe", probe)
    root = make_repo(tmp_path / "r", specs={}, target=TARGET, features=WALKS)
    fmap = files.load_folder(root / ".swarmai/features")
    results, sbdir = replay(fmap, root, Selection(list(fmap.features)))
    try:
        _check_every_start(results, sbdir, probed, log, user)
    finally:
        replay_user.remove_tree(sbdir)


def _check_every_start(results, sbdir, probed, log, user):
    assert [r["result"] for r in results] == ["pass"], results
    assert probed == [0], "probed before anything started"
    calls = _sudo_calls(log)
    execs = [c for c in calls if _mode(c) == "exec"]
    assert len(execs) == 2, calls                       # the prepare step and the walk
    for c in calls:
        assert c[:4] == ["-n", "-u", user.name, "--"] and c[4:6] == ["/usr/bin/env", "-i"]
        assert "only-in-the-env-file" not in " ".join(c), "an environment value on a command line"
    # the walk saw the sandbox's environment, from the file, with its spawn tag
    home, tag, value = (Path(sbdir) / "walk.out").read_text().strip().split("|")
    assert home == str(Path(sbdir) / "home") and value == "only-in-the-env-file"
    assert tag.startswith(Path(sbdir).name + "/")
    assert not list(Path(sbdir).glob(".spawn.*")), "env files are deleted once read"
    assert (Path(sbdir) / "prepared").read_text().strip() == "prepared"
    replay_user.remove_tree(sbdir)
    assert not Path(sbdir).exists()


def test_caches_are_the_replay_users_own(tmp_path, monkeypatch):
    fake, _log = _fake_sudo(tmp_path)
    user = _user(tmp_path, fake)
    monkeypatch.setenv("npm_config_cache", "/the/persons/npm-cache")
    monkeypatch.setenv("XDG_CACHE_HOME", "/the/persons/cache")
    sb_dir = tmp_path / "sb"
    sb_dir.mkdir()
    sb = sandbox.Sandbox(tmp_path, user=user)
    sb.dir = sb_dir
    env = sb.env(SimpleNamespace(env={}))
    assert env["npm_config_cache"] == str(user.home / ".npm")
    assert env["UV_CACHE_DIR"] == str(user.home / ".cache" / "uv")
    assert all(not v.startswith("/the/persons") for v in env.values())
    # the person's own replay keeps the person's caches
    person = sandbox.Sandbox(tmp_path)
    person.dir = sb_dir
    assert person.env(SimpleNamespace(env={}))["npm_config_cache"] == "/the/persons/npm-cache"


def _sandbox_with(tmp_path: Path, user, name: str = "swarm-feature-map-a") -> sandbox.Sandbox:
    sb = sandbox.Sandbox(tmp_path, user=user)
    sb.dir = tmp_path / name                 # a tag's prefix: the sandbox folder's own name
    sb.dir.mkdir(parents=True)
    return sb


def test_kill_by_tag_stops_a_process_that_left_its_group(tmp_path):
    fake, _log = _fake_sudo(tmp_path)
    sb = _sandbox_with(tmp_path, _user(tmp_path, fake))
    pids = tmp_path / "pids"
    child = ("import os, subprocess, sys, time\n"
             "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True)\n"
             f"open({str(pids)!r}, 'w').write(f'{{os.getpid()}} {{c.pid}}')\n"
             "time.sleep(60)\n")
    proc = sb.popen([sys.executable, "-c", child], cwd=tmp_path, env={"PATH": os.environ["PATH"]})
    deadline = time.monotonic() + 15
    while not pids.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    parent, escaped = map(int, pids.read_text().split())
    assert os.getpgid(escaped) != os.getpgid(parent), "the child left the group"
    sandbox._kill_group(proc)

    def alive(pid):
        try:
            with open(f"/proc/{pid}/stat") as fh:
                return fh.read().rsplit(")", 1)[-1].split()[0] != "Z"
        except OSError:
            return False
    deadline = time.monotonic() + 10
    while (alive(parent) or alive(escaped)) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not alive(parent) and not alive(escaped)


def test_close_stops_every_start_of_the_sandbox(tmp_path):
    fake, _log = _fake_sudo(tmp_path)
    sb = _sandbox_with(tmp_path, _user(tmp_path, fake))
    sb.keep = True
    a = sb.popen([sys.executable, "-c", "import time; time.sleep(60)"], cwd=tmp_path,
                 env={"PATH": os.environ["PATH"]})
    other = _sandbox_with(tmp_path, _user(tmp_path, fake), "swarm-feature-map-b")
    b = other.popen([sys.executable, "-c", "import time; time.sleep(60)"], cwd=tmp_path,
                    env={"PATH": os.environ["PATH"]})
    time.sleep(1)
    sb.close()                      # a was not in sb.procs: only its tag finds it
    a.wait(timeout=10)
    assert b.poll() is None, "another sandbox's process was stopped"
    sandbox._kill_group(b)


def test_remove_tree_removes_what_the_replay_user_wrote(tmp_path, monkeypatch):
    fake, log = _fake_sudo(tmp_path)
    user = _user(tmp_path, fake)
    folder = tmp_path / "sbx"
    (folder / "locked" / "deep").mkdir(parents=True)
    (folder / "locked" / "deep" / "f").write_text("x")
    (folder / "locked" / "deep").chmod(0o500)
    (folder / "locked").chmod(0o500)
    monkeypatch.setattr(replay_user, "_current", user)
    replay_user.remove_tree(folder)
    assert not folder.exists()
    assert any(_mode(c) == "rm" and c[-1] == str(folder) for c in _sudo_calls(log))


def test_popen_needs_the_program_on_path(tmp_path):
    fake, _log = _fake_sudo(tmp_path)
    sb = _sandbox_with(tmp_path, _user(tmp_path, fake))
    with pytest.raises(FileNotFoundError):
        sb.popen(["no-such-program-fm"], cwd=tmp_path, env={"PATH": "/usr/bin:/bin"})


def _listener() -> tuple[socket.socket, int]:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    return s, s.getsockname()[1]


def test_imds_answers_is_a_tcp_connect(monkeypatch):
    s, port = _listener()
    try:
        monkeypatch.setattr(replay_user, "METADATA", (("127.0.0.1", port),))
        assert replay_user.imds_answers()
    finally:
        s.close()
    monkeypatch.setattr(replay_user, "METADATA", (("127.0.0.1", port),))
    assert not replay_user.imds_answers()


def test_the_helper_probe_reports_the_metadata_service_and_unreachable_paths(tmp_path, monkeypatch, capsys):
    import importlib.util
    spec = importlib.util.spec_from_file_location("fm_helper", replay_user.HELPER)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    s, port = _listener()
    secret = tmp_path / "secret"
    secret.write_text("x")
    secret.chmod(0o000)
    try:
        monkeypatch.setattr(helper, "METADATA", (("127.0.0.1", port), ("127.0.0.1", 1)))
        with pytest.raises(SystemExit) as end:
            helper._probe([str(tmp_path / "missing"), str(secret), sys.executable])
    finally:
        s.close()
        secret.chmod(0o600)
    out = json.loads(capsys.readouterr().out)
    assert end.value.code == 3 and out["uid"] == os.getuid()
    problems = out["problems"]
    assert any(f"127.0.0.1:{port}" in p for p in problems)
    assert not any(p.endswith("127.0.0.1:1") for p in problems)      # a closed port is no answer
    assert any("missing" in p for p in problems)
    if os.geteuid() != 0:
        assert any("cannot read" in p and "secret" in p for p in problems)
    assert not any(sys.executable in p for p in problems)
    monkeypatch.setattr(helper, "METADATA", (("127.0.0.1", 1),))
    with pytest.raises(SystemExit) as end:
        helper._probe([sys.executable])
    assert end.value.code == 0


def test_probe_reads_the_helpers_answer_and_its_uid(tmp_path):
    """ReplayUser.probe: the helper's JSON decides, and a probe that ran as another uid (sudo
    misconfigured) is refused."""
    def user_with(answer: str, code: int) -> replay_user.ReplayUser:
        fake = tmp_path / f"sudo-{code}"
        fake.write_text(f"#!/bin/sh\necho '{answer}'\nexit {code}\n")
        fake.chmod(0o755)
        return replay_user.ReplayUser("swarm-replay", 4242, tmp_path, str(fake), sys.executable)
    assert user_with('{"uid": 4242, "problems": []}', 0).probe() == []
    assert user_with('{"uid": 4242, "problems": ["the instance metadata service answers"]}', 3).probe() \
        == ["the instance metadata service answers"]
    assert "not swarm-replay" in user_with('{"uid": 1001, "problems": []}', 0).probe()[0]
    assert "failed" in user_with("sudo: a password is required", 1).probe()[0]


def test_the_sandbox_folder_is_granted_to_the_replay_user(tmp_path):
    if not subprocess.run(["sh", "-c", "command -v setfacl getfacl"], capture_output=True).returncode == 0:
        pytest.skip("no setfacl")
    user = replay_user.ReplayUser("nobody", pwd.getpwnam("nobody").pw_uid, tmp_path, "/usr/bin/sudo",
                                  sys.executable)
    folder = tmp_path / "sbx"
    (folder / "sub").mkdir(parents=True)
    (folder / "sub" / "f").write_text("x")
    user.grant(folder)
    acl = subprocess.run(["getfacl", "-p", str(folder)], capture_output=True, text=True).stdout
    assert "user:nobody:rwx" in acl and "default:user:nobody:rwx" in acl
    assert "default:user:" + pwd.getpwuid(os.getuid()).pw_name + ":rwx" in acl
    acl_f = subprocess.run(["getfacl", "-p", str(folder / "sub" / "f")], capture_output=True, text=True).stdout
    assert "user:nobody:rw-" in acl_f
    (folder / "later").mkdir()         # made later: inherits the default entries
    later = subprocess.run(["getfacl", "-p", str(folder / "later")], capture_output=True, text=True).stdout
    assert "user:nobody:rwx" in later


def test_needs_replay_user_from_fixed_facts_before_any_probe(monkeypatch):
    """A Hive, or a Linux machine where the replay user exists, needs it whatever the
    metadata probe says (a slow connect must not turn the boundary off); only elsewhere does
    the probe decide."""
    probes = []
    monkeypatch.setattr(replay_user, "imds_answers", lambda *a, **k: probes.append(1) or False)
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    monkeypatch.setattr(replay_user, "_user_exists", lambda: False)
    assert replay_user.needs_replay_user() and probes == []
    monkeypatch.delenv("SWARMAI_MODE")
    monkeypatch.setattr(replay_user, "_user_exists", lambda: True)
    assert replay_user.needs_replay_user() and probes == []
    monkeypatch.setattr(replay_user, "_user_exists", lambda: False)
    assert not replay_user.needs_replay_user() and probes == [1]
    monkeypatch.setattr(replay_user, "imds_answers", lambda *a, **k: True)
    assert replay_user.needs_replay_user()


def test_a_hive_whose_probe_times_out_is_still_refused_without_the_user(tmp_path, fresh, monkeypatch):
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    monkeypatch.setattr(replay_user, "imds_answers", lambda *a, **k: False)   # a timed-out connect
    monkeypatch.setattr(replay_user, "REPLAY_USER", "swarm-replay-absent-user")
    with pytest.raises(replay_user.Unavailable) as exc:
        replay_user.resolve()
    assert "has no swarm-replay-absent-user" in exc.value.reason


def test_not_needed_is_never_remembered(tmp_path, fresh, monkeypatch):
    answers = iter([False, True])
    monkeypatch.setattr(replay_user, "needs_replay_user", lambda: next(answers))
    monkeypatch.setattr(replay_user, "REPLAY_USER", "swarm-replay-absent-user")
    assert replay_user.resolve() is None
    with pytest.raises(replay_user.Unavailable):
        replay_user.resolve()                 # asked again, not taken from the first answer


def _setup_user(tmp_path, monkeypatch):
    fake, log = _fake_sudo(tmp_path)
    user = _user(tmp_path, fake)
    monkeypatch.setattr(replay_user, "needs_replay_user", lambda: True)
    monkeypatch.setattr(replay_user, "REPLAY_USER", user.name)
    monkeypatch.setattr(replay_user.pwd, "getpwnam", lambda name: SimpleNamespace(
        pw_uid=user.uid, pw_dir=str(user.home)))
    monkeypatch.setattr(replay_user, "_find_sudo", lambda: str(fake))
    monkeypatch.setattr(replay_user.ReplayUser, "probe", lambda self, paths=(): [])
    return user


def test_one_replay_at_a_time_uses_the_replay_user(tmp_path, fresh, monkeypatch):
    _setup_user(tmp_path, monkeypatch)
    monkeypatch.setattr(replay_user.ReplayUser, "kill_all", lambda self: 0)
    lock = replay_user._lock_path()
    lock.parent.mkdir(parents=True, exist_ok=True)
    holder = subprocess.Popen([sys.executable, "-c",
                               "import fcntl, os, sys, time\n"
                               f"fd = os.open({str(lock)!r}, os.O_RDWR | os.O_CREAT)\n"
                               "fcntl.flock(fd, fcntl.LOCK_EX)\nprint('held', flush=True)\ntime.sleep(30)\n"],
                              stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        with pytest.raises(replay_user.Unavailable) as exc:
            replay_user.resolve(lock_wait_s=1)
        assert "another replay" in exc.value.reason
    finally:
        holder.terminate()
        holder.wait(timeout=10)
    assert replay_user.resolve(lock_wait_s=5) is not None     # free again: taken


def test_a_process_that_outlived_an_earlier_replay_refuses_the_replay(tmp_path, fresh, monkeypatch):
    _setup_user(tmp_path, monkeypatch)
    stopped = []
    monkeypatch.setattr(replay_user.ReplayUser, "kill_all", lambda self: stopped.append(1) or 2)
    with pytest.raises(replay_user.Unavailable) as exc:
        replay_user.resolve(lock_wait_s=5)
    assert stopped == [1] and "outlived an earlier replay" in exc.value.reason
    monkeypatch.setattr(replay_user.ReplayUser, "kill_all", lambda self: None)
    with pytest.raises(replay_user.Unavailable) as exc:
        replay_user.resolve(lock_wait_s=5)
    assert "could not stop" in exc.value.reason


def test_kill_all_never_stops_the_callers_own_processes(tmp_path):
    fake, log = _fake_sudo(tmp_path)
    assert _user(tmp_path, fake).kill_all() is None
    assert _sudo_calls(log) == [], "no helper was started"
    r = subprocess.run([sys.executable, "-I", str(replay_user.HELPER), "kill", "--all", str(os.getuid())],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 2 and "refused" in r.stderr


def test_the_helper_stops_a_process_that_cleared_its_tag(tmp_path):
    """What kill --all does as the replay user, in-process: every process of the user is
    found (a cleared environment does not hide it), TERM is followed by KILL."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("fm_helper_kill", replay_user.HELPER)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    stray = subprocess.Popen(["env", "-i", sys.executable, "-c",
                              "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"],
                             start_new_session=True)
    time.sleep(0.5)
    assert stray.pid in helper._mine()
    assert stray.pid not in helper._tagged("anything", True)
    helper._stop(lambda: [p for p in helper._mine() if p == stray.pid])
    assert stray.wait(timeout=10) == -9, "TERM ignored, KILL followed"


def test_touch_updating_only_refreshes_a_row_still_updating(tmp_path):
    import asyncio
    import sqlite3
    from hive.provisioner import HiveProvisioner
    db = tmp_path / "s.db"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE hive_instances (id TEXT, status TEXT, updated_at TEXT)")
        c.execute("INSERT INTO hive_instances VALUES ('u', 'updating', '2000-01-01 00:00:00'), "
                  "('r', 'running', '2000-01-01 00:00:00')")
    p = HiveProvisioner(db)
    asyncio.run(p._touch_updating("u"))
    asyncio.run(p._touch_updating("r"))          # a cancelled update ended it: left alone
    with sqlite3.connect(db) as c:
        rows = dict((i, (s, t)) for i, s, t in c.execute("SELECT id, status, updated_at FROM hive_instances"))
    assert rows["u"][0] == "updating" and rows["u"][1] > "2000-01-01 00:00:00"
    assert rows["r"] == ("running", "2000-01-01 00:00:00")


def test_close_stops_every_process_of_the_replay_user(tmp_path):
    """Closing the sandbox stops every process of the replay user (this process holds the
    machine's lock), not only the tagged ones; the tag is the fallback when that cannot run."""
    fake, _log = _fake_sudo(tmp_path)
    calls = []

    class Recording(replay_user.ReplayUser):
        def kill_all(self):
            calls.append("all")
            return 0

        def kill(self, tag, *, prefix=False):
            calls.append(("tag", tag, prefix))
    base = _user(tmp_path, fake)
    sb = _sandbox_with(tmp_path, Recording(base.name, base.uid, base.home, base.sudo, base.python))
    sb.keep = True
    sb.close()
    assert calls == ["all"]


def test_replay_stops_every_process_of_the_replay_user_before_it_returns(tmp_path, fresh, monkeypatch):
    """replay() hands its folder to the caller without close(): it stops every process of the
    replay user itself, so a daemon a walk left behind is gone when the results come back."""
    user = _setup_user(tmp_path, monkeypatch)
    stops = []
    monkeypatch.setattr(replay_user.ReplayUser, "kill_all", lambda self: stops.append("all") or 0)
    root = make_repo(tmp_path / "r", specs={}, target=TARGET, features=WALKS)
    fmap = files.load_folder(root / ".swarmai/features")
    results, sbdir = replay(fmap, root, Selection(list(fmap.features)))
    try:
        assert [r["result"] for r in results] == ["pass"], results
        assert stops == ["all", "all"], "once before the replay (resolve), once before it returns"
        assert user.name == replay_user.current().name
    finally:
        replay_user.remove_tree(sbdir)


def test_kill_all_ends_with_kill_minus_one_and_never_runs_as_root(monkeypatch, capsys):
    """kill --all, in-process with the system calls recorded: after the TERM pass it sends
    SIGKILL to -1 (every process of the user in one pass: a fork loop cannot outrun a listing)
    until none is left; as root it is refused. (Run for real as a separate uid on a Hive.)"""
    import importlib.util
    import signal
    spec = importlib.util.spec_from_file_location("fm_helper_all", replay_user.HELPER)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    sent, alive = [], {111, 222}

    def fake_kill(pid, sig):
        sent.append((pid, sig))
        if pid == -1 and sig == signal.SIGKILL:
            alive.clear()                  # the kernel's one pass
    monkeypatch.setattr(helper, "os", _HelperOs(getuid=lambda: 4242, geteuid=lambda: 4242,
                                                kill=fake_kill))
    monkeypatch.setattr(helper, "_mine", lambda: sorted(alive))
    monkeypatch.setattr(helper, "_alive", lambda pid: pid in alive)
    clock = iter(range(0, 10_000))          # each reading one second later: no wait spins
    monkeypatch.setattr(helper, "time", SimpleNamespace(monotonic=lambda: float(next(clock)),
                                                        sleep=lambda s: None))
    helper._kill_all(1001)
    assert (111, signal.SIGTERM) in sent and (-1, signal.SIGKILL) in sent
    assert json.loads(capsys.readouterr().out) == {"left": 0}
    monkeypatch.setattr(helper, "os", _HelperOs(getuid=lambda: 0, geteuid=lambda: 0, kill=fake_kill))
    sent.clear()
    with pytest.raises(SystemExit) as end:
        helper._kill_all(1001)
    assert end.value.code == 2 and sent == []


def test_a_non_dumpable_process_is_still_found_and_kill_minus_one_is_always_sent(monkeypatch, capsys):
    """A process that made itself non-dumpable has its /proc files owned by root (on some
    kernels the /proc/<pid> folder too) and its environment unreadable, so its tag cannot be
    seen: it is still a process of the user (real uid from /proc/<pid>/status), and kill
    --all sends kill(-1) even when the listing shows nothing."""
    import importlib.util
    import signal
    spec = importlib.util.spec_from_file_location("fm_helper_dump", replay_user.HELPER)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    hidden = subprocess.Popen([sys.executable, "-c",
                               "import ctypes, time\n"
                               "ctypes.CDLL(None).prctl(4, 0, 0, 0, 0)   # PR_SET_DUMPABLE, 0\n"
                               "print('ready', flush=True)\ntime.sleep(60)\n"], stdout=subprocess.PIPE, text=True)
    try:
        assert hidden.stdout.readline().strip() == "ready"
        if os.geteuid() != 0:
            with pytest.raises(PermissionError):
                open(f"/proc/{hidden.pid}/environ", "rb").read()
            assert hidden.pid not in helper._tagged("", True), "its tag cannot be read"
        assert hidden.pid in helper._mine()
    finally:
        hidden.kill()
        hidden.wait(timeout=10)
    sent = []
    monkeypatch.setattr(helper, "os", _HelperOs(getuid=lambda: 4242, geteuid=lambda: 4242,
                                                kill=lambda pid, sig: sent.append((pid, sig))))
    monkeypatch.setattr(helper, "_mine", lambda: [])
    clock = iter(range(0, 10_000))
    monkeypatch.setattr(helper, "time", SimpleNamespace(monotonic=lambda: float(next(clock)),
                                                        sleep=lambda s: None))
    helper._kill_all(1001)
    assert sent == [(-1, signal.SIGKILL)]
    assert json.loads(capsys.readouterr().out) == {"left": 0}


def test_processes_are_listed_by_the_real_uid_in_status_not_the_folder_owner(monkeypatch):
    """Kernel-independent: a pid whose /proc folder is owned by root but whose status says it
    runs as the user is the user's; one whose folder is the user's but whose status says
    another uid is not."""
    import importlib.util
    import io
    spec = importlib.util.spec_from_file_location("fm_helper_uid", replay_user.HELPER)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    status = {"/proc/777/status": b"Name:\tdaemon\nUid:\t4242\t4242\t4242\t4242\n",
              "/proc/888/status": b"Name:\tother\nUid:\t1001\t1001\t1001\t1001\n"}
    owner = {"/proc/777": 0, "/proc/888": 4242}
    monkeypatch.setattr(helper, "os", _HelperOs(
        listdir=lambda path: ["777", "888", "self"], getpid=lambda: 1, getuid=lambda: 4242,
        stat=lambda path: SimpleNamespace(st_uid=owner[path])))
    monkeypatch.setattr(helper, "open", lambda path, mode="r": io.BytesIO(status[path]), raising=False)
    assert helper._mine() == [777]


def test_a_second_resolve_still_refuses_a_survivor(tmp_path, fresh, monkeypatch):
    """A base and a head replay in one process: the second resolve also stops that user's
    processes first and refuses when one is left."""
    fake, _log = _fake_sudo(tmp_path)
    user = _user(tmp_path, fake)
    monkeypatch.setattr(replay_user, "needs_replay_user", lambda: True)
    monkeypatch.setattr(replay_user, "REPLAY_USER", user.name)
    monkeypatch.setattr(replay_user.pwd, "getpwnam", lambda name: SimpleNamespace(
        pw_uid=user.uid, pw_dir=str(user.home)))
    monkeypatch.setattr(replay_user, "_find_sudo", lambda: str(fake))
    monkeypatch.setattr(replay_user.ReplayUser, "probe", lambda self, paths=(): [])
    left = iter([0, 1])
    monkeypatch.setattr(replay_user.ReplayUser, "kill_all", lambda self: next(left))
    assert replay_user.resolve() is not None
    with pytest.raises(replay_user.Unavailable, match="outlived an earlier replay"):
        replay_user.resolve()


def test_no_evidence_is_copied_out_while_a_replay_users_process_survives(tmp_path, fresh, monkeypatch):
    fake, _log = _fake_sudo(tmp_path)
    user = _user(tmp_path, fake)
    monkeypatch.setattr(replay_user, "needs_replay_user", lambda: True)
    monkeypatch.setattr(replay_user, "REPLAY_USER", user.name)
    monkeypatch.setattr(replay_user.pwd, "getpwnam", lambda name: SimpleNamespace(
        pw_uid=user.uid, pw_dir=str(user.home)))
    monkeypatch.setattr(replay_user, "_find_sudo", lambda: str(fake))
    monkeypatch.setattr(replay_user.ReplayUser, "probe", lambda self, paths=(): [])
    calls = iter([0])                          # resolve: none left; the replay's end: one left
    monkeypatch.setattr(replay_user.ReplayUser, "kill_all", lambda self: next(calls, 1))
    root = make_repo(tmp_path / "r", specs={}, target=TARGET, features=WALKS)
    fmap = files.load_folder(root / ".swarmai/features")
    results, sbdir = replay(fmap, root, Selection(list(fmap.features)))
    try:
        assert [r["result"] for r in results] == ["pass"], results
        assert results[0]["evidence"] == [] and "evidence dropped" in results[0]["error"]
    finally:
        replay_user.remove_tree(sbdir)
