"""B-e three-way re-audit fixes (fixing log §32) that still apply after S6-3 (the
daemon execution and its tokens are gone):

- an owner change between a command's decision and its write changes nothing;
- a create killed before run_create leaves an intent startup proves is its own and
  removes; a created run and a create in THIS process are never touched;
- ``--artifact <file>`` / ``--repo name=<relative>`` resolve in the agent's cwd.
Each test fails on the code before §32.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest











# ── queued commands never run after their client gave up ──────────────────────


# ── create killed before the run exists ───────────────────────────────────────
def _create(tmp_path, **kw):
    from core.run_protocol.engine import RunEngine
    args = dict(data_dir=tmp_path / "data", workspace=tmp_path / "ws", project="P",
                profile="research", session_id="s1", repos={})
    args.update(kw)
    return RunEngine.create(**args)


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
    from core.run_protocol import cli_main as pv1
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


def test_a_command_decided_before_a_transfer_changes_nothing_after_it(tmp_path, monkeypatch):
    from tests.pipeline_v1_rig import SESSION
    from core.run_protocol import cli_commands, request_ctx
    r = _rig(tmp_path, monkeypatch)
    try:
        before = len(_engine(r).store.read_events())
        assert _engine(r).transfer_owner(OTHER, reason="test")
        args = cli_commands.parser().parse_args(["abandon", *r._run_args(),
                                                 "--reason", "late"])
        with request_ctx.active(request_ctx.Request(session_id=SESSION)):
            out, rc = cli_commands.run(args)
        assert out.get("code") == "not_owner" and rc == 1, out
        assert len(_engine(r).store.read_events()) == before + 1   # the transfer only
    finally:
        r.close()


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
                session_id=SESSION, transcript_ids=("cccccccc-0000-4000-8000-000000000003",))):
            out, _rc = cli_commands.run(args)
        assert out.get("code") == "not_owner"
        assert (tfile.read_text() if tfile.exists() else None) == before
    finally:
        r.close()


def _pv1():
    from core.run_protocol import cli_main
    return cli_main


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


