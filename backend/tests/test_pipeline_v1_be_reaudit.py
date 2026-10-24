"""B-e three-way re-audit fixes (fixing log §32): the main flow across the CLI/daemon
process boundary and the daemon's failure modes.

- the packaged daemon names the SOURCE checkout's scripts and a real python, and only
  those receive a token (utils.source_repo);
- a CLI command that cannot start within the queue wait answers busy with its token
  unspent, so it cannot run later after its client gave up;
- a create killed before run_create leaves an intent startup proves is its own and
  removes; a created run and a create in THIS process are never touched;
- ``--artifact <file>`` / ``--repo name=<relative>`` resolve in the agent's cwd.
Each test fails on the code before §32.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

import pytest

from utils import source_repo


# ── packaged daemon: the source checkout's scripts ─────────────────────────────
def _checkout(root: Path) -> Path:
    for tool in ("pipeline_v1", "pipeline_test"):
        f = root / "backend" / "scripts" / f"{tool}.py"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("# script\n")
    return root


def test_frozen_bundle_names_the_checkout_it_was_built_from(tmp_path, monkeypatch):
    src = _checkout(tmp_path / "src")
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "source_repo.json").write_text(json.dumps({"root": str(src)}))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    assert source_repo.root() == src.resolve()
    assert source_repo.pipeline_script("pipeline_test") == \
        (src / "backend" / "scripts" / "pipeline_test.py").resolve()
    # the command the agent is told to run is the program form, packaged and in dev (S3)
    from core.worktree_write_gate import v1_command
    assert v1_command("status") == '"$SWARM_BACKEND_BIN" pipeline-v1 status'


def test_frozen_bundle_from_another_machine_names_no_script(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "source_repo.json").write_text(json.dumps({"root": "/nonexistent/ci/swarmai"}))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    assert source_repo.root() is None
    from core.run_owner_gate import _is_our_script
    assert _is_our_script({"tool": "pipeline_v1", "script": "/nonexistent/ci/swarmai/"
                           "backend/scripts/pipeline_v1.py", "cwd": "/"}) is False


def test_frozen_daemon_tokens_the_source_checkouts_script(tmp_path, monkeypatch):
    src = _checkout(tmp_path / "src")
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "source_repo.json").write_text(json.dumps({"root": str(src)}))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    from core.run_owner_gate import _is_our_script
    script = str(src / "backend" / "scripts" / "pipeline_v1.py")
    assert _is_our_script({"tool": "pipeline_v1", "script": script, "cwd": "/"}) is True


def test_dev_checkout_is_this_repo():
    assert source_repo.root() == Path(__file__).resolve().parents[2]


# ── queued commands never run after their client gave up ──────────────────────
def test_a_command_that_cannot_start_answers_busy_and_keeps_its_token(monkeypatch):
    from core.run_protocol import daemon_exec
    burned: list = []
    monkeypatch.setattr(daemon_exec.hook_token, "redeem", lambda t: burned.append(t))
    monkeypatch.setattr(daemon_exec, "_QUEUE_WAIT_S", 0.05)
    held = []
    for _ in range(4):
        assert daemon_exec._CONCURRENCY.acquire(blocking=False)
        held.append(1)
    try:
        out, rc = daemon_exec._execute({"token": "x", "argv": ["status"]})
    finally:
        for _ in held:
            daemon_exec._CONCURRENCY.release()
    assert (out.get("code"), rc) == ("busy", 2)
    assert burned == [], "a command that never started must not spend its token"


def test_waiting_commands_are_capped_so_runner_calls_keep_connections(monkeypatch):
    from core.run_protocol import daemon_exec
    monkeypatch.setattr(daemon_exec, "_CLI_WAITING", threading.BoundedSemaphore(0))
    out, rc = daemon_exec._execute({"token": "x", "argv": ["status"]})
    assert out.get("code") == "busy"


# ── create killed before the run exists ───────────────────────────────────────
def _create(tmp_path, **kw):
    from core.run_protocol.engine import RunEngine
    args = dict(data_dir=tmp_path / "data", workspace=tmp_path / "ws", project="P",
                profile="research", session_id="s1", repos={}, mode="shared")
    args.update(kw)
    return RunEngine._create(**args)


def test_a_create_that_dies_before_the_rename_leaves_no_run(tmp_path, monkeypatch):
    from core.run_protocol import engine as eng_mod
    from core.run_protocol.store import AuditStore
    real = AuditStore.append_new

    def die(self, *a, **k):
        real(self, *a, **k)
        raise KeyboardInterrupt("process killed")     # after run_create, before rename
    monkeypatch.setattr(AuditStore, "append_new", die)
    with pytest.raises(KeyboardInterrupt):
        _create(tmp_path, run_id="run_deadbeef")
    monkeypatch.setattr(AuditStore, "append_new", real)
    runs = tmp_path / "data/state/pipeline_agent_audit/runs"
    assert not (runs / "run_deadbeef").exists(), "a run exists only after the rename"
    # the same id is free again: the next create of it succeeds
    assert _create(tmp_path, run_id="run_deadbeef").store.read_events()
    del eng_mod


def test_startup_removes_staging_of_a_dead_process_only(tmp_path):
    from core.run_protocol import engine
    stage = tmp_path / "data/state/pipeline_agent_audit/.creating"
    (stage / "1-0-abcd/state").mkdir(parents=True)
    (stage / f"{engine._PROCESS_BOOT}-ffff").mkdir(parents=True)
    assert engine.recover_incomplete_creates(tmp_path / "data", tmp_path / "ws") == ["1-0-abcd"]
    assert not (stage / "1-0-abcd").exists() and (stage / f"{engine._PROCESS_BOOT}-ffff").exists()


def test_an_empty_leftover_run_dir_is_reused_but_a_real_run_is_not(tmp_path):
    from core.run_protocol.engine import EngineError
    (tmp_path / "ws/Projects/P/.artifacts/runs/run_0badf00d").mkdir(parents=True)
    assert _create(tmp_path, run_id="run_0badf00d").store.read_events()
    with pytest.raises(EngineError, match="already exists"):
        _create(tmp_path, run_id="run_0badf00d")


def test_create_builds_the_run_in_staging_and_renames_it_whole(tmp_path, monkeypatch):
    seen = []
    real = os.rename

    def spy(src, dst):
        seen.append((Path(src), Path(dst), (Path(src) / "events.jsonl").stat().st_size > 0))
        return real(src, dst)
    monkeypatch.setattr(os, "rename", spy)
    eng = _create(tmp_path)
    renames = [r for r in seen if r[1].parent.name == "runs"]
    assert len(renames) == 1 and renames[0][2], "the run appears with run_create signed"
    assert ".creating" in renames[0][0].parts
    assert not any((tmp_path / "data/state/pipeline_agent_audit/.creating").iterdir())
    assert eng.store.read_events()


# ── the agent's cwd decides what a relative path means ────────────────────────
def test_the_cli_reads_an_artifact_file_in_its_own_cwd(tmp_path, monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import importlib
    pv1 = importlib.import_module("pipeline_v1")
    (tmp_path / "a.json").write_text('{"x": 1}')
    monkeypatch.chdir(tmp_path)
    argv, stdin = pv1._localize(["stage-complete", "--project", "P", "--run-id", "run_1",
                                 "--artifact", "a.json"])
    assert argv[-2:] == ["--artifact", "-"] and json.loads(stdin) == {"x": 1}
    argv, stdin = pv1._localize(["create", "--project", "P", "--repo=app=."])
    assert argv[-2:] == ["--repo", f"app={os.path.abspath('.')}"] and stdin is None


def test_the_daemon_never_opens_an_artifact_path_or_takes_a_relative_repo(tmp_path,
                                                                          monkeypatch):
    from core.run_protocol import cli_commands
    (tmp_path / "a.json").write_text('{"x": 1}')
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="not JSON"):
        cli_commands._json_arg("a.json")
    with pytest.raises(ValueError, match="absolute"):
        cli_commands._repos(["app=."])


# ── an owner transfer revokes what the old owner was handed ──────────────────
OTHER = "bbbbbbbb-0000-4000-8000-000000000002"


def _rig(tmp_path, monkeypatch):
    from tests.pipeline_v1_rig import Rig
    r = Rig(tmp_path / "case", monkeypatch, profile="direct")
    r.create()
    return r


def _engine(r):
    from core.run_protocol.engine import RunEngine
    return RunEngine.load(data_dir=r.data, workspace=r.ws, project=r._run_args()[1],
                          run_id=r.run_id)


def test_a_command_tokened_before_a_transfer_changes_nothing_after_it(tmp_path, monkeypatch):
    from tests.pipeline_v1_rig import SESSION
    from core.run_protocol import cli_commands, request_ctx
    r = _rig(tmp_path, monkeypatch)
    try:
        before = len(_engine(r).store.read_events())
        assert _engine(r).transfer_owner(OTHER, reason="test")
        args = cli_commands.parser().parse_args(["abandon", *r._run_args(),
                                                 "--reason", "late"])
        with request_ctx.active(request_ctx.Request(session_id=SESSION, run_id=r.run_id,
                                                    scope="abandon")):
            out, rc = cli_commands.run(args)
        assert out.get("code") == "not_owner" and rc == 1, out
        assert len(_engine(r).store.read_events()) == before + 1   # the transfer only
    finally:
        r.close()


def test_a_lease_opened_before_a_transfer_writes_nothing_after_it(tmp_path, monkeypatch):
    from tests.pipeline_v1_rig import SESSION
    from core.run_protocol import runner_service
    r = _rig(tmp_path, monkeypatch)
    try:
        project = r._run_args()[1]
        lease = runner_service.open_for(SESSION, project, r.run_id, port=None)
        assert lease.get("ok") is True, lease
        rec = runner_service._LEASES[lease["lease"]]
        assert _engine(r).transfer_owner(OTHER, reason="test")
        before = len(_engine(r).store.read_events())
        with pytest.raises(runner_service.LeaseOwnerChanged):
            runner_service._serve(rec, "append", {
                "kind": "infrastructure_failed", "payload": {"why": "late"},
                "ordinal": "next"})
        assert len(_engine(r).store.read_events()) == before
        assert lease["lease"] not in runner_service._LEASES, "the lease is revoked"
        assert runner_service._after_tests(rec, {}).get("stage_complete") is None
    finally:
        runner_service.clear()
        r.close()


def test_a_runner_whose_start_time_cannot_be_read_gets_no_lease(monkeypatch):
    from core.run_protocol import runner_service
    monkeypatch.setattr(runner_service, "_start_time", lambda _pid: None)
    called = []
    monkeypatch.setattr(runner_service, "_open", lambda *a: called.append(a) or {"ok": True})
    out = runner_service.handle({"op": "runner_open"}, port=1, peer_pid=12345)
    assert out.get("ok") is False and not called


# ── a forged worktree row cannot settle a live run ────────────────────────────
def test_a_forged_row_neither_settles_a_live_run_nor_hides_its_worktree(tmp_path,
                                                                        monkeypatch):
    from core import run_worktree
    r = _rig(tmp_path, monkeypatch)
    try:
        run_file = r.ws / "Projects" / r._run_args()[1] / ".artifacts" / "runs" / r.run_id \
            / "run.json"
        data = json.loads(run_file.read_text())
        repo = sorted(_engine(r).contract["expected_repos"])[0]
        data["worktrees"] = [{"repo_name": repo, "state": "released"},
                             {"repo_name": repo, "state": "active"}]
        run_file.write_text(json.dumps(data))
        view = run_worktree._signed_view(run_file, data)
        assert [e["state"] for e in view["worktrees"]] == ["active"]
        assert view["_settled"] is False, "only signed facts end the run's claim"
        # a forged "released" row alone: the run still claims the session's writes
        data["worktrees"] = [{"repo_name": repo, "state": "released"}]
        run_file.write_text(json.dumps(data))
        monkeypatch.setattr(run_worktree, "_iter_runs",
                            lambda _ws, _ids=None: iter([(run_file, run_worktree._signed_view(
                                run_file, json.loads(run_file.read_text())))]))
        from tests.pipeline_v1_rig import SESSION
        own = run_worktree.own_isolated_run(SESSION, r.ws)
        assert own is not None and own["settled"] is False
    finally:
        r.close()


def test_a_forged_run_json_owner_does_not_make_a_worktree_this_sessions(tmp_path,
                                                                       monkeypatch):
    from core import run_worktree, security_hooks
    r = _rig(tmp_path, monkeypatch)
    try:
        run_file = r.ws / "Projects" / r._run_args()[1] / ".artifacts" / "runs" / r.run_id \
            / "run.json"
        data = json.loads(run_file.read_text())
        data["session_id"] = OTHER
        run_file.write_text(json.dumps(data))
        monkeypatch.setattr(run_worktree, "run_for_path", lambda _d: (r.run_id, "repo"))
        monkeypatch.setattr(security_hooks, "_pipeline_runs_root", lambda: str(r.ws))
        assert security_hooks._own_run_worktree("/any", OTHER) is False
    finally:
        r.close()


def test_packaged_daemon_source_root_comes_from_the_build_record(tmp_path, monkeypatch):
    from core import security_hooks as sh
    from utils import source_repo
    repo = _checkout(tmp_path / "swarmai")
    (repo / ".git").mkdir()
    monkeypatch.setattr(sh, "_SOURCE_REPO_ROOT", str(tmp_path / "bundle"))
    monkeypatch.setattr(sh, "_SOURCE_ROOT_CACHE", [0.0, None])
    monkeypatch.setattr(source_repo, "root", lambda: repo)
    assert sh._source_repo_root() == os.path.realpath(str(repo))


def test_a_project_name_with_a_space_still_creates(tmp_path):
    assert _create(tmp_path, project="Agent DLC").store.read_events()


# ── review round: owner recheck writes nothing; argv edge cases ───────────────
def test_a_former_owner_command_does_not_even_note_its_transcript(tmp_path, monkeypatch):
    from tests.pipeline_v1_rig import SESSION
    from core.run_protocol import cli_commands, request_ctx
    r = _rig(tmp_path, monkeypatch)
    try:
        assert _engine(r).transfer_owner(OTHER, reason="test")
        tfile = _engine(r).store.paths.run / "transcripts.json"
        before = tfile.read_text() if tfile.exists() else None
        args = cli_commands.parser().parse_args(["abandon", *r._run_args(), "--reason", "x"])
        with request_ctx.active(request_ctx.Request(
                session_id=SESSION, run_id=r.run_id, scope="abandon",
                transcript_ids=("cccccccc-0000-4000-8000-000000000003",))):
            out, _rc = cli_commands.run(args)
        assert out.get("code") == "not_owner"
        assert (tfile.read_text() if tfile.exists() else None) == before
    finally:
        r.close()


def _pv1():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import importlib
    return importlib.import_module("pipeline_v1")


def test_repeated_artifact_flags_send_only_the_last_and_read_stdin_once(tmp_path, monkeypatch):
    import io
    pv1 = _pv1()
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"a": 1}'))
    argv, stdin = pv1._localize(["stage-complete", "--project", "P", "--run-id", "run_1",
                                 "--artifact", "-", "--artifact", "-"])
    assert argv.count("--artifact") == 1 and json.loads(stdin) == {"a": 1}
    argv, stdin = pv1._localize(["stage-complete", "--project", "P", "--run-id", "run_1",
                                 "--artifact", "-", "--artifact", '{"b": 2}'])
    assert argv[-2:] == ["--artifact", '{"b": 2}'] and stdin is None


def test_inline_json_wins_over_a_same_named_file_and_tilde_expands(tmp_path, monkeypatch):
    pv1 = _pv1()
    monkeypatch.chdir(tmp_path)
    (tmp_path / '{"x":1}').write_text('{"y": 2}')
    argv, stdin = pv1._localize(["stage-complete", "--project", "P", "--run-id", "run_1",
                                 "--artifact", '{"x":1}'])
    assert argv[-1] == '{"x":1}' and stdin is None
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "a.json").write_text('{"z": 3}')
    argv, stdin = pv1._localize(["stage-complete", "--project", "P", "--run-id", "run_1",
                                 "--artifact", "~/a.json"])
    assert argv[-1] == "-" and json.loads(stdin) == {"z": 3}
    argv, _ = pv1._localize(["create", "--project", "P", "--repo", "app=~/r"])
    assert f"app={tmp_path}/r" in argv


def test_runner_identity_comes_from_the_kernel_not_ps(monkeypatch):
    import subprocess
    from core.run_protocol import runner_service
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("no subprocess for a process identity")))
    me = runner_service._start_time(os.getpid())
    assert me and me == runner_service._start_time(os.getpid())
    assert runner_service._start_time(2 ** 22 - 1) is None
