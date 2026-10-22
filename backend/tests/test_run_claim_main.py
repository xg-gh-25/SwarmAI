"""Claiming uncommitted main-checkout edits into a run's worktree (run_3b9b1515).

What is tested: ``run_worktree.claim_main`` / ``main_dirty`` / ``_three_way``, the
``run-claim-main`` CLI (owner token, run lock, run.json record), the worktree-creation
report, the blocked-landing error, the post-tool reporter
(``core.main_dirty_scan``) and the approval predicate in ``dangerous_command_gate``.

Methodology: real git repos and worktrees in tmp; the CLI as a subprocess through the
``flow`` harness of test_pipeline_worktree (temp SwarmWS, in-process token service).
Invariants (plan I1–I6): every main edit gets an owner decision; a claim never loses
bytes; refusal touches nothing; main is restored only when its bytes and index entry
are unchanged, else the worktree file is put back; landing and claiming share one
3-way rule; only the owner can claim, only listed files, and only with the user's
approval (never from the approval cache).
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from core import hook_token
from core import run_worktree as rw
from tests.test_run_worktree import env  # noqa: F401 — fixture


def _g(cwd, *args):
    proc = __import__("subprocess").run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


@pytest.fixture(autouse=True)
def _hook_token_oracle():
    hook_token.stop_oracle()
    assert hook_token.start_oracle(50000 + os.getpid() % 10000) or not hook_token.oracle_supported()
    yield
    hook_token.stop_oracle()

RUN = "run_claim01"


# ── rw-level helpers ─────────────────────────────────────────────────────────
def _wt(env):
    e = rw.create(RUN, "swarmai", env["main"])
    return e, rw.wt_path(RUN, "swarmai")


def _ledger_files(run_id=RUN):
    root = rw.ledger_root() / run_id / "main-claimed"
    return sorted(p for p in root.rglob("*") if p.is_file()) if root.exists() else []


# ── _three_way (I5) ──────────────────────────────────────────────────────────
def test_three_way_shared_by_landing_and_claim():
    assert rw._three_way(b"a\nb\nc\n", b"a\nb\nc\n", b"a\nB\nc\n") == b"a\nB\nc\n"
    assert rw._three_way(b"A\nb\nc\nd\ne\n", b"a\nb\nc\nd\ne\n", b"a\nb\nc\nd\nE\n") == \
        b"A\nb\nc\nd\nE\n"
    assert rw._three_way(b"x\n", b"a\n", b"y\n") is None          # conflict
    assert rw._three_way(b"\0bin", b"a\n", b"a\n") is None        # binary
    import inspect
    assert "_three_way(" in inspect.getsource(rw._dirty_merge_plan)


# ── main_dirty ───────────────────────────────────────────────────────────────
def test_main_dirty_lists_modified_untracked_staged_deleted(env):
    m = env["main"]
    (m / "a.py").write_text("a = 2\n")
    (m / "n.py").write_text("n = 1\n")
    (m / ".gitignore").write_text("ign.txt\n")
    _g(m, "add", ".gitignore")
    _g(m, "commit", "-q", "-m", "ignore")
    (m / "b.py").write_text("b = 2\n")
    _g(m, "add", "b.py")
    (m / "c.py").unlink()
    (m / "ign.txt").write_text("x")
    got = {d["path"]: d for d in rw.main_dirty(m)}
    assert set(got) == {"a.py", "n.py", "b.py", "c.py"}
    assert got["n.py"]["status"] == "untracked" and got["c.py"]["status"] == "deleted"
    assert got["b.py"]["staged"] is True and got["a.py"]["staged"] is False


# ── claim_main (AC2, I2–I4, F1, F2, F7, F9) ──────────────────────────────────
def test_claim_untouched_file_moves_and_main_restored(env):
    e, wt = _wt(env)
    (env["main"] / "a.py").write_text("a = 'mine'\n")
    res = rw.claim_main(RUN, e, env["main"], ["a.py"])
    assert res["claimed"] == ["a.py"] and res["main_left"] == []
    assert (wt / "a.py").read_text() == "a = 'mine'\n"
    assert (env["main"] / "a.py").read_text() == "a = 1\n"
    assert _g(env["main"], "status", "--porcelain") == ""
    backup = Path(res["backup"])
    assert (backup / "main" / "a.py").read_text() == "a = 'mine'\n"
    assert (backup / "worktree" / "a.py").read_text() == "a = 1\n"
    assert json.loads((backup / "manifest.json").read_text())["files"][0]["path"] == "a.py"


def test_claim_merges_into_a_file_the_run_changed(env):
    m = env["main"]
    (m / "a.py").write_text("one\ntwo\nthree\nfour\nfive\n")
    _g(m, "commit", "-qam", "five lines")
    e, wt = _wt(env)
    (wt / "a.py").write_text("ONE\ntwo\nthree\nfour\nfive\n")
    (m / "a.py").write_text("one\ntwo\nthree\nfour\nFIVE\n")
    rw.claim_main(RUN, e, m, ["a.py"])
    assert (wt / "a.py").read_text() == "ONE\ntwo\nthree\nfour\nFIVE\n"
    assert (m / "a.py").read_text() == "one\ntwo\nthree\nfour\nfive\n"


def test_claim_refuses_conflict_touching_nothing(env):
    e, wt = _wt(env)
    (wt / "a.py").write_text("a = 'run'\n")
    (env["main"] / "a.py").write_text("a = 'main'\n")
    (env["main"] / "b.py").write_text("b = 'main'\n")
    with pytest.raises(rw.WorktreeError) as ei:
        rw.claim_main(RUN, e, env["main"], ["b.py", "a.py"])
    assert ei.value.code == "claim_refused" and ei.value.data["files"] == ["a.py"]
    assert "keep-worktree" in ei.value.message and "take-main" in ei.value.message
    assert (wt / "a.py").read_text() == "a = 'run'\n" and (wt / "b.py").read_text() == "b = 1\n"
    assert (env["main"] / "a.py").read_text() == "a = 'main'\n"
    assert (env["main"] / "b.py").read_text() == "b = 'main'\n"
    assert _ledger_files() == []


@pytest.mark.parametrize("resolve,wt_text", [("keep-worktree", "a = 'run'\n"),
                                              ("take-main", "a = 'main'\n")])
def test_claim_resolves_a_conflict_by_choice(env, resolve, wt_text):
    e, wt = _wt(env)
    (wt / "a.py").write_text("a = 'run'\n")
    (env["main"] / "a.py").write_text("a = 'main'\n")
    res = rw.claim_main(RUN, e, env["main"], ["a.py"], resolve=resolve)
    assert (wt / "a.py").read_text() == wt_text
    assert (env["main"] / "a.py").read_text() == "a = 1\n"
    assert (Path(res["backup"]) / "main" / "a.py").read_text() == "a = 'main'\n"


def test_claim_new_file_and_deletion(env):
    e, wt = _wt(env)
    (env["main"] / "n.py").write_text("n = 1\n")
    (env["main"] / "c.py").unlink()
    rw.claim_main(RUN, e, env["main"], ["n.py", "c.py"])
    assert (wt / "n.py").read_text() == "n = 1\n" and not (wt / "c.py").exists()
    assert "n.py" in _g(wt, "ls-files")                    # added: landing needs no git add
    assert rw.untracked_new_files(wt, RUN) == []
    assert not (env["main"] / "n.py").exists() and (env["main"] / "c.py").read_text() == "c = 1\n"
    assert _g(env["main"], "status", "--porcelain") == ""


def test_claim_fully_staged_ok_partially_staged_refused(env):
    e, wt = _wt(env)
    m = env["main"]
    (m / "a.py").write_text("a = 2\n")
    _g(m, "add", "a.py")
    (m / "b.py").write_text("b = 2\n")
    _g(m, "add", "b.py")
    (m / "b.py").write_text("b = 3\n")
    with pytest.raises(rw.WorktreeError) as ei:
        rw.claim_main(RUN, e, m, ["a.py", "b.py"])
    assert ei.value.data["files"] == ["b.py"]
    rw.claim_main(RUN, e, m, ["a.py"])
    assert (wt / "a.py").read_text() == "a = 2\n"
    assert _g(m, "diff", "--cached", "--name-only") == "b.py"   # b's index untouched


@pytest.mark.parametrize("bad", ["../x.py", ".git/config", "zz.py", "link.py"])
def test_claim_refuses_bad_paths(env, bad):
    e, wt = _wt(env)
    if bad == "link.py":
        os.symlink(env["main"] / "a.py", env["main"] / "link.py")
    with pytest.raises(rw.WorktreeError) as ei:
        rw.claim_main(RUN, e, env["main"], [bad])
    assert ei.value.code == "claim_refused"
    assert _ledger_files() == []


def test_claim_accepts_absolute_paths(env):
    e, wt = _wt(env)
    (env["main"] / "a.py").write_text("a = 'mine'\n")
    res = rw.claim_main(RUN, e, env["main"], [str(env["main"] / "a.py")])
    assert res["claimed"] == ["a.py"]


def test_claim_rerun_after_kill_restores_main(env, monkeypatch):
    """F1: killed after the worktree write, before main is restored."""
    e, wt = _wt(env)
    (env["main"] / "a.py").write_text("a = 'mine'\n")
    real = rw._restore_main_file

    def boom(*a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(rw, "_restore_main_file", boom)
    with pytest.raises(KeyboardInterrupt):
        rw.claim_main(RUN, e, env["main"], ["a.py"])
    assert (wt / "a.py").read_text() == "a = 'mine'\n"
    monkeypatch.setattr(rw, "_restore_main_file", real)
    res = rw.claim_main(RUN, e, env["main"], ["a.py"])
    assert res["claimed"] == ["a.py"] and (env["main"] / "a.py").read_text() == "a = 1\n"
    assert (wt / "a.py").read_text() == "a = 'mine'\n"


def test_cas_failure_rolls_worktree_back(env, monkeypatch):
    """F2 / I4: a writer changes main between the read and the restore."""
    e, wt = _wt(env)
    (env["main"] / "a.py").write_text("a = 'mine'\n")
    real = rw._write_worktree_file

    def and_then_someone_writes(*a, **k):
        real(*a, **k)
        (env["main"] / "a.py").write_text("a = 'third writer'\n")
    monkeypatch.setattr(rw, "_write_worktree_file", and_then_someone_writes)
    res = rw.claim_main(RUN, e, env["main"], ["a.py"])
    assert res["claimed"] == [] and res["main_left"] == ["a.py"]
    assert (env["main"] / "a.py").read_text() == "a = 'third writer'\n"
    assert (wt / "a.py").read_text() == "a = 1\n"
    assert (Path(res["backup"]) / "main" / "a.py").read_text() == "a = 'mine'\n"


def test_cas_checks_the_index_entry_too(env, monkeypatch):
    """Another session stages the file between read and restore: its index entry survives."""
    e, wt = _wt(env)
    m = env["main"]
    (m / "a.py").write_text("a = 'mine'\n")
    real = rw._write_worktree_file

    def and_then_someone_stages(*a, **k):
        real(*a, **k)
        _g(m, "add", "a.py")
    monkeypatch.setattr(rw, "_write_worktree_file", and_then_someone_stages)
    res = rw.claim_main(RUN, e, m, ["a.py"])
    assert res["main_left"] == ["a.py"]
    assert _g(m, "diff", "--cached", "--name-only") == "a.py"
    assert (wt / "a.py").read_text() == "a = 1\n"


def test_claim_waits_on_the_integrate_lock_and_reports_busy(env, monkeypatch):
    """F3/F4: a claim and a landing (or a second claim) into the same repo serialize on
    the repo's integrate lock; while another thread holds it, the claim reports busy and
    touches neither tree."""
    import threading
    e, wt = _wt(env)
    (env["main"] / "a.py").write_text("a = 'mine'\n")
    monkeypatch.setattr(rw, "INTEGRATE_LOCK_TIMEOUT_S", 0.3)
    held, release = threading.Event(), threading.Event()

    def holder():
        with rw._integrate_lock(e):
            held.set()
            release.wait(10)

    t = threading.Thread(target=holder)
    t.start()
    try:
        assert held.wait(5)
        with pytest.raises(rw.WorktreeError) as ei:
            rw.claim_main(RUN, e, env["main"], ["a.py"])
        assert ei.value.code == "busy"
        assert (env["main"] / "a.py").read_text() == "a = 'mine'\n"
        assert (wt / "a.py").read_text() == "a = 1\n"
        assert _ledger_files() == []
    finally:
        release.set()
        t.join(10)


def test_claim_untouched_file_after_main_head_moved(env):
    """AC2 (review finding): main HEAD moved past the run's base for a file the run
    never changed; the main edit on top of it is still an untouched-file claim (the
    worktree gets main's bytes), not a 3-way merge that refuses."""
    m = env["main"]
    e, wt = _wt(env)
    (m / "a.py").write_text("a = 2\n")
    _g(m, "commit", "-qam", "main moved on")
    (m / "a.py").write_text("a = 3\n")
    res = rw.claim_main(RUN, e, m, ["a.py"])
    assert res["claimed"] == ["a.py"]
    assert (wt / "a.py").read_text() == "a = 3\n"
    assert (m / "a.py").read_text() == "a = 2\n"


def test_resolve_applies_to_every_listed_file(env):
    """AC2 (review round 2): --resolve keep-worktree on a file the run did not change
    still drops main's edit (backed up) instead of moving it; take-main always moves."""
    m = env["main"]
    e, wt = _wt(env)
    (m / "a.py").write_text("a = 'drop me'\n")
    res = rw.claim_main(RUN, e, m, ["a.py"], resolve="keep-worktree")
    assert res["claimed"] == ["a.py"] and res["worktree_changed"] == []
    assert (wt / "a.py").read_text() == "a = 1\n" and (m / "a.py").read_text() == "a = 1\n"
    assert (Path(res["backup"]) / "main" / "a.py").read_text() == "a = 'drop me'\n"
    (m / "a.py").write_text("a = 'take me'\n")
    res = rw.claim_main(RUN, e, m, ["a.py"], resolve="take-main")
    assert res["worktree_changed"] == ["a.py"] and (wt / "a.py").read_text() == "a = 'take me'\n"


def test_dry_run_plans_without_writing(env):
    m = env["main"]
    e, wt = _wt(env)
    (m / "a.py").write_text("a = 'mine'\n")
    assert rw.claim_main(RUN, e, m, ["a.py"], dry_run=True) == {"planned": ["a.py"]}
    assert (m / "a.py").read_text() == "a = 'mine'\n" and (wt / "a.py").read_text() == "a = 1\n"
    assert _ledger_files() == []
    with pytest.raises(rw.WorktreeError):
        rw.claim_main(RUN, e, m, ["c.py"], dry_run=True)       # not dirty


def test_claim_carries_a_mode_only_change(env):
    """K4: chmod +x in main (same bytes) moves into the worktree instead of being lost
    when main goes back to HEAD."""
    m = env["main"]
    e, wt = _wt(env)
    os.chmod(m / "a.py", 0o755)
    res = rw.claim_main(RUN, e, m, ["a.py"])
    assert res["claimed"] == ["a.py"] and res["worktree_changed"] == ["a.py"]
    assert os.stat(wt / "a.py").st_mode & 0o100
    assert not os.stat(m / "a.py").st_mode & 0o100
    assert _g(m, "status", "--porcelain") == ""


def test_claim_intent_to_add_file_leaves_main_clean(env):
    """K4: a `git add -N` file is claimed as a new file and main ends clean (its
    intent-to-add index entry is removed, not left as a deletion)."""
    m = env["main"]
    e, wt = _wt(env)
    (m / "ita.py").write_text("i = 1\n")
    _g(m, "add", "-N", "ita.py")
    assert {d["path"]: d["status"] for d in rw.main_dirty(m)}["ita.py"] == "added"
    rw.claim_main(RUN, e, m, ["ita.py"])
    assert (wt / "ita.py").read_text() == "i = 1\n"
    assert _g(m, "status", "--porcelain") == ""


def test_symlink_out_of_the_repo_is_refused_as_a_symlink(env, tmp_path):
    """K4: plan_claim resolves the parent only, so a symlinked file is refused as 'a
    symlink', not misreported as outside the run's repos."""
    m = env["main"]
    e, wt = _wt(env)
    outside = tmp_path / "outside.py"
    outside.write_text("o\n")
    os.symlink(outside, m / "ln.py")
    with pytest.raises(rw.WorktreeError) as ei:
        rw.plan_claim(RUN, [(e, m)], [str(m / "ln.py")])
    assert "a symlink" in ei.value.message


def test_dry_run_takes_no_lock(env, monkeypatch):
    """K2: planning (the approval hook's dry run) never waits on a landing's lock."""
    import threading
    e, wt = _wt(env)
    (env["main"] / "a.py").write_text("a = 'mine'\n")
    monkeypatch.setattr(rw, "INTEGRATE_LOCK_TIMEOUT_S", 0.3)
    held, release = threading.Event(), threading.Event()

    def holder():
        with rw._integrate_lock(e):
            held.set()
            release.wait(10)
    t = threading.Thread(target=holder)
    t.start()
    try:
        assert held.wait(5)
        assert rw.claim_main(RUN, e, env["main"], ["a.py"], dry_run=True) == {"planned": ["a.py"]}
    finally:
        release.set()
        t.join(10)


def test_rollback_takes_a_new_file_out_of_the_index_again(env, monkeypatch):
    """K4: a worktree file that was untracked (not in the index) is git-added by the
    claim; when main changes mid-claim, the rollback leaves it untracked again."""
    m = env["main"]
    e, wt = _wt(env)
    (wt / "n.py").write_text("n = 'run'\n")
    (m / "n.py").write_text("n = 'main'\n")
    real = rw._write_worktree_file

    def and_then_someone_writes(*a, **k):
        real(*a, **k)
        (m / "n.py").write_text("n = 'third'\n")
    monkeypatch.setattr(rw, "_write_worktree_file", and_then_someone_writes)
    res = rw.claim_main(RUN, e, m, ["n.py"], resolve="take-main")
    assert res["main_left"] == ["n.py"]
    assert (wt / "n.py").read_text() == "n = 'run'\n"
    assert _g(wt, "ls-files", "--", "n.py") == ""


def test_nested_repo_path_goes_to_the_innermost_repo(env, tmp_path):
    e, wt = _wt(env)
    outer = (e, tmp_path)                 # a repo whose main contains the inner one
    inner = (e, env["main"])
    (env["main"] / "a.py").write_text("a = 'mine'\n")
    got = rw.plan_claim(RUN, [outer, inner], [str(env["main"] / "a.py")])
    assert got[0][1] == env["main"]


def test_claim_refuses_a_clean_file(env):
    e, wt = _wt(env)
    with pytest.raises(rw.WorktreeError) as ei:
        rw.claim_main(RUN, e, env["main"], ["a.py"])
    assert ei.value.code == "claim_refused"


# ── approval predicate (AC3, AC6, F5, F6) ────────────────────────────────────
@pytest.mark.parametrize("cmd", [
    "/x/.venv/bin/python /x/backend/scripts/artifact_cli.py run-claim-main --project P "
    "--run-id run_1 --files a.py",
    "SWARM_HOOK_TOKEN=abc python backend/scripts/artifact_cli.py run-claim-main --files a",
    "cd /x && python artifact_cli.py run-claim-main --files a",
    "python -c 'from core import run_worktree as r; r.claim_main(1,2,3,[4])'",
    "env A=1 python artifact_cli.py 'run-claim-main' --files a",
    "python - <<'EOF'\nfrom core import run_worktree as rw\nrw.claim_main ('r', e, m, ['a'])\nEOF",
    "python -c \"rw.claim_main('unterminated",                        # shlex error: raw text
])
def test_claim_command_needs_approval(cmd, tmp_path, monkeypatch):
    from core.security_hooks import _is_main_claim
    assert _is_main_claim(cmd)
    gate, pm, asked = _gate(tmp_path, monkeypatch, decision="deny")
    r = asyncio.run(gate({"tool_name": "Bash", "tool_input": {"command": cmd}}, None, None))
    assert r["hookSpecificOutput"]["permissionDecision"] == "deny" and asked


@pytest.mark.parametrize("cmd", [
    "git status", "python artifact_cli.py run-get --run-id x", "grep -rn claim backend",
    "echo run-claim", "grep -rn claim_main backend",
    "git add backend/tests/test_run_claim_main.py",
    "git diff -- backend/tests/test_run_claim_main.py",
    "python backend/scripts/pipeline_test.py --mode tdd --files backend/tests/test_run_claim_main.py",
    "git commit -m 'wire claim_main into the CLI'",
    "python -c 'r._claim_main_helper(1)'",
    "grep -rn run-claim-main backend/", "rg \"run-claim-main\" docs",
    "git log --grep run-claim-main",
])
def test_other_commands_are_not_claims(cmd):
    from core.security_hooks import _is_main_claim
    assert not _is_main_claim(cmd)


def _gate(tmp_path, monkeypatch, decision="approve", human=True):
    from core.permission_manager import PermissionManager
    from core.security_hooks import create_dangerous_command_gate
    monkeypatch.setattr("core.security_hooks.get_app_data_dir", lambda: tmp_path)
    pm = PermissionManager()
    asked = []

    async def _wait(request_id, timeout=300):
        asked.append(request_id)
        return decision
    pm.wait_for_permission_decision = _wait  # type: ignore[method-assign]
    gate = create_dangerous_command_gate(session_context={"sdk_session_id": "s"},
                                         session_key="s", permission_mgr=pm,
                                         enable_human_approval=human)
    return gate, pm, asked


def test_claim_gate_independent_of_dangerous_commands_json(tmp_path, monkeypatch):
    (tmp_path / "dangerous_commands.json").write_text(json.dumps({"patterns": ["rm -rf /*"]}))
    gate, _pm, asked = _gate(tmp_path, monkeypatch, decision="deny")
    cmd = "python artifact_cli.py run-claim-main --project P --run-id run_1 --files a.py"
    r = asyncio.run(gate({"tool_name": "Bash", "tool_input": {"command": cmd}}, None, None))
    assert r["hookSpecificOutput"]["permissionDecision"] == "deny" and asked


def test_claim_is_denied_when_approval_disabled(tmp_path, monkeypatch):
    gate, _pm, asked = _gate(tmp_path, monkeypatch, human=False)
    cmd = "python artifact_cli.py run-claim-main --files a.py"
    r = asyncio.run(gate({"tool_name": "Bash", "tool_input": {"command": cmd}}, None, None))
    assert r["hookSpecificOutput"]["permissionDecision"] == "deny" and not asked


def test_claim_approval_is_never_served_from_the_cache(tmp_path, monkeypatch):
    gate, pm, asked = _gate(tmp_path, monkeypatch, decision="approve")
    cmd = "python artifact_cli.py run-claim-main --project P --run-id run_1 --files a.py"
    pm.approve_command("s", cmd)
    asyncio.run(gate({"tool_name": "Bash", "tool_input": {"command": cmd}}, None, None))
    asyncio.run(gate({"tool_name": "Bash", "tool_input": {"command": cmd}}, None, None))
    assert len(asked) == 2


def test_owner_gate_issues_claim_token():
    from core import run_owner_gate as og
    assert "run-claim-main" in og.TOKEN_SUBCOMMANDS
    assert og._needs_token({"tool": "artifact_cli", "sub": "run-claim-main"}) == "run-claim-main"
    from scripts import pipeline_worktree as pw
    assert "run-claim-main" in pw.LOCKED_COMMANDS


# ── CLI + creation report + landing (AC1, AC2, AC5, AC6, I6) ──────────────────
# ── post-tool reporter (AC4, F8, S3) ─────────────────────────────────────────
def test_reporter_registered():
    import inspect

    from core import hook_builder
    src = inspect.getsource(hook_builder.build_hooks)
    assert "create_main_dirty_reporter" in src and '"PostToolUse"' in src


def test_approved_phase_is_registered_right_after_the_approval_gate(monkeypatch):
    from core import hook_builder
    from core.permission_manager import permission_manager
    recorded = []
    original = hook_builder.HookRegistry.register

    def _spy(self, event, hook, name, matcher=None, no_timeout=False):
        recorded.append((event, name, matcher))
        return original(self, event, hook, name, matcher=matcher, no_timeout=no_timeout)

    monkeypatch.setattr(hook_builder.HookRegistry, "register", _spy)
    asyncio.run(hook_builder.build_hooks(
        agent_config={"id": "default"}, enable_skills=False, enable_mcp=False,
        resume_session_id="test-sess", session_context={"sdk_session_id": "test-sess"},
        permission_manager=permission_manager,
    ))
    bash = [n for e, n, m in recorded if e == "PreToolUse" and m == "Bash"]
    i = bash.index("dangerous_command_gate")
    assert bash[i + 1] == "run_owner_gate_approved"
    assert bash.index("run_owner_gate") < i
    assert ("PostToolUse", "main_dirty_reporter", "Bash") in recorded


def test_token_env_not_leaked():
    assert hook_token.TOKEN_ENV not in os.environ


# ── Gate-2 fixing spec (run_3b9b1515, groups K1–K6) ──────────────────────────
@pytest.mark.parametrize("cmd", [
    'grep -n "def claim_main(" backend/core/run_worktree.py',
    "sed -n '/def claim_main(/,/^def /p' backend/core/run_worktree.py",
    'git commit -m "fix claim_main() refusal"',
])
def test_k2_a_mention_of_the_call_is_not_a_claim(cmd):
    from core.security_hooks import _is_main_claim
    assert not _is_main_claim(cmd)


@pytest.mark.parametrize("cmd", [
    'python3 -c "from core import run_worktree as rw; rw.claim_main(r, e, m, [p])"',
    "cd backend && .venv/bin/python - <<'EOF'\nrw.claim_main(r, e, m, [p])\nEOF",
])
def test_k2_a_python_call_is_a_claim(cmd):
    from core.security_hooks import _is_main_claim
    assert _is_main_claim(cmd)


def test_k3_approved_phase_needs_the_recorded_approval():
    """K3: the token phase never trusts its position after the approval gate."""
    from core import run_owner_gate as og
    from core import security_hooks as sh
    gate = og.create_run_owner_gate({"sdk_session_id": "s1"}, phase="approved")
    cmd = ("python backend/scripts/artifact_cli.py run-claim-main --project P "
           "--run-id run_1 --files a.py")
    ti = {"tool_name": "Bash", "tool_input": {"command": cmd}}
    r = asyncio.run(gate(ti, "tu1", None))
    assert r["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "not approved" in r["hookSpecificOutput"]["permissionDecisionReason"]
    sh.record_claim_approval("tu1", cmd)
    r = asyncio.run(gate(ti, "tu1", None))
    assert "not approved" not in json.dumps(r)
    assert not sh.consume_claim_approval("tu1", cmd)   # one use


def test_k3_dangerous_gate_records_the_approval(tmp_path, monkeypatch):
    from core import security_hooks as sh
    gate, _pm, _asked = _gate(tmp_path, monkeypatch, decision="approve")
    cmd = "python artifact_cli.py run-claim-main --project P --run-id run_1 --files a.py"
    asyncio.run(gate({"tool_name": "Bash", "tool_input": {"command": cmd}}, "tu9", None))
    assert sh.consume_claim_approval("tu9", cmd)


def test_k4_glob_characters_are_literal(env):
    e, wt = _wt(env)
    m = env["main"]
    (m / "app").mkdir()
    (m / "app" / "i.py").write_text("other\n")
    _g(m, "add", "app/i.py")                    # another session's staged file
    (m / "app" / "[i].py").write_text("mine\n")
    res = rw.claim_main(RUN, e, m, ["app/[i].py"])
    assert res["claimed"] == ["app/[i].py"]
    assert (wt / "app" / "[i].py").read_text() == "mine\n"
    assert _g(m, "diff", "--cached", "--name-only") == "app/i.py"   # untouched
    assert not (wt / "app" / "i.py").exists()


def test_k4_status_takes_no_optional_lock(env, monkeypatch):
    seen = {}
    real = rw.git

    def spy(cwd, *a, **k):
        if "status" in a:
            seen.update(k.get("env") or {})
        return real(cwd, *a, **k)
    monkeypatch.setattr(rw, "git", spy)
    rw.main_dirty(env["main"])
    assert seen.get("GIT_OPTIONAL_LOCKS") == "0"


def test_k4_error_mid_claim_rolls_back_the_file_and_reports_partial(env, monkeypatch):
    e, wt = _wt(env)
    m = env["main"]
    (m / "a.py").write_text("a = 'mine'\n")
    (m / "n1.py").write_text("n1\n")
    (m / "n2.py").write_text("n2\n")
    real = rw._restore_main_file
    calls = []

    def flaky(main, rel, in_head):
        calls.append(rel)
        if len(calls) == 2:
            raise rw.WorktreeError("git_failed", "index.lock exists")
        return real(main, rel, in_head)
    monkeypatch.setattr(rw, "_restore_main_file", flaky)
    res = rw.claim_main(RUN, e, m, ["a.py", "n1.py", "n2.py"])
    assert res["claimed"] == ["a.py"] and "index.lock" in res["error"]
    assert res["main_left"] == ["n1.py", "n2.py"]
    assert not (wt / "n1.py").exists() and not (wt / "n2.py").exists()   # rolled back
    assert (m / "n1.py").read_text() == "n1\n" and (m / "n2.py").read_text() == "n2\n"
    assert (m / "a.py").read_text() == "a = 1\n"


def test_k6_partially_staged_moves_under_take_main(env):
    e, wt = _wt(env)
    m = env["main"]
    (m / "b.py").write_text("b = 2\n")
    _g(m, "add", "b.py")
    (m / "b.py").write_text("b = 3\n")
    with pytest.raises(rw.WorktreeError) as ei:
        rw.claim_main(RUN, e, m, ["b.py"])
    assert "take-main" in ei.value.message
    res = rw.claim_main(RUN, e, m, ["b.py"], resolve="take-main")
    assert res["claimed"] == ["b.py"] and (wt / "b.py").read_text() == "b = 3\n"
    assert _g(m, "status", "--porcelain") == ""
    keep = Path(res["backup"])
    assert (keep / "index" / "b.py").read_text() == "b = 2\n"   # the staged version kept


def test_k1_creation_hint_does_not_prefill_paths(monkeypatch):
    """K1: the copyable claim command names no file; the files are listed apart."""
    from scripts import pipeline_worktree as pw
    files = ["/m/a.py", "/m/b.py"]
    extra: dict = {}
    monkeypatch.setattr(rw, "main_dirty", lambda m: [{"path": "a.py", "status": "modified",
                                                     "staged": False},
                                                    {"path": "b.py", "status": "modified",
                                                     "staged": False}])
    from core import main_dirty_scan
    monkeypatch.setattr(main_dirty_scan, "record_baseline", lambda *a, **k: None)
    pw._main_uncommitted({"project": "P"}, "run_1", [{"main": "/m", "repo_name": "r"}],
                         extra, Path("/nonexistent"))
    hint = extra["claim_hint"]
    assert "--files <path ...>" in hint
    for f in files:
        assert f in hint.split("Claim,")[0]


def test_blob_of_a_glob_named_path_absent_at_rev_is_none(env):
    m = env["main"]
    (m / "app").mkdir()
    (m / "app" / "i.py").write_text("other\n")
    _g(m, "add", "app/i.py")
    assert rw._blob(m, "HEAD", "app/[i].py") is None
    assert rw._blob(m, "", "app/i.py") == b"other\n"


def test_report_is_returned_with_no_await_after_the_save(monkeypatch):
    """Meta-review MED: once the seen-map is saved the hook returns the report without
    awaiting again, so a chain timeout cannot cancel it between save and report."""
    import ast
    import inspect
    import textwrap
    from core import main_dirty_scan as mds
    tree = ast.parse(textwrap.dedent(inspect.getsource(mds.create_main_dirty_reporter)))
    hook = next(n for n in ast.walk(tree)
                if isinstance(n, ast.AsyncFunctionDef) and n.name == "_hook")
    saves = [n.lineno for n in ast.walk(hook) if isinstance(n, ast.Call)
             and getattr(n.func, "id", None) == "_save_seen"]
    awaits = [n.lineno for n in ast.walk(hook) if isinstance(n, ast.Await)]
    assert saves and awaits and max(awaits) < min(saves)
