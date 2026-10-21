"""Gate false positives of 2026-10-20, pinned in both directions.

Every command shape below was denied, or sent for approval, during one day of work
while doing nothing dangerous. Each allow case is paired with the true positives
that must stay caught, so a fix that only loosens a gate turns a test red.

What is tested:
- history rewrite (dangerous_command_gate): a heredoc body is judged by what reads it
  (a file being written is data, a shell or code that can run something is not), and
  a branch-local rewrite inside this session's own run worktree or a temp repo is not
  gated (_rewrites_only_scratch); another session's worktree, a ref-moving rewrite, a
  git target set through the environment, or an unfollowed directory change still is;
- runner loop (pipeline_test_gate): a ``for`` in a heredoc body is not a shell loop
  unless a shell runs it;
- run owner token (run_owner_gate._token_binding): the CLI call may be followed by
  output plumbing and preceded by quoted ``cat > FILE <<'EOF'`` writes;
- pytest pipe guard: only a pipe carrying pytest's own output counts;
- python code judgment (_code_can_execute): ``re.compile(`` is a method, not the builtin.
"""
from __future__ import annotations

import pytest

from core.run_owner_gate import _token_binding
from core.security_hooks import (
    _code_can_execute,
    _is_commit_amend,
    _is_history_rewrite,
    _rewrites_only_scratch,
    _runner_loop_kind,
)

RUNNER = ("/repo/backend/.venv/bin/python /repo/backend/scripts/pipeline_test.py "
          "--project SwarmAI --run-id run_x --mode tdd --files backend/tests/test_a.py")
CLI = ("/repo/backend/.venv/bin/python /repo/backend/scripts/artifact_cli.py stage-start "
       "--project SwarmAI --run-id run_x --stage deliver")

# An edit script whose text names rebase and has an odd apostrophe (the shlex split of
# the old naive segmenter failed on it and the raw-text fallback matched "rebase").
EDIT_BODY = ('import sys\ns = open(sys.argv[1]).read()\n'
             's = s.replace("a", """Name of an in-progress merge / rebase / cherry-pick;\n'
             "it's one rev-parse.\"\"\")\nopen(sys.argv[1], 'w').write(s)\n")


# ── history rewrite ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("cmd", [
    "cat > /tmp/e1.py <<'PYEOF'\n" + EDIT_BODY + "PYEOF\npython3 -I /tmp/e1.py /x",
    "cat >> notes.md <<'EOF'\nwe rebase, then reset --hard, then commit --amend\nEOF",
    "python3 -I - <<'EOF'\nfrom pathlib import Path\n# after a rebase; don't\n"
    "Path('a').write_text('x')\nEOF",
    "git commit -F - <<'EOF'\nUndo the rebase and the --amend\nEOF",
])
def test_a_heredoc_body_that_is_data_is_not_a_rewrite(cmd):
    assert not _is_history_rewrite(cmd)
    assert not _is_commit_amend(cmd)


@pytest.mark.parametrize("cmd", [
    "git rebase -i HEAD~3",
    "echo hi && git reset --hard HEAD~1",
    "git -C /repo rebase main",
    "bash <<'EOF'\ncd /x\ngit rebase main\nEOF",
    "sh -s <<'EOF'\ngit reset --hard\nEOF",
    "python3 - <<'EOF'\nimport subprocess\nsubprocess.run(['git', 'rebase', 'main'])\nEOF",
    "perl - <<'EOF'\nsystem('git rebase main');\nEOF",
    # option values are not a script operand; -s and stdin paths read stdin
    "bash -euo pipefail <<'EOF'\ngit reset --hard\nEOF",
    "bash -o pipefail <<'EOF'\ngit reset --hard\nEOF",
    "bash +x <<'EOF'\ngit reset --hard\nEOF",
    "bash -s x y <<'EOF'\ngit reset --hard\nEOF",
    "bash --rcfile /dev/null <<'EOF'\ngit reset --hard\nEOF",
    "bash /dev/stdin <<'EOF'\ngit reset --hard\nEOF",
    "sh /dev/fd/0 <<'EOF'\ngit rebase main\nEOF",
    "source /dev/stdin <<'EOF'\ngit reset --hard\nEOF",
    ". /dev/stdin <<'EOF'\ngit reset --hard\nEOF",
    "python3 /dev/stdin <<'EOF'\nimport os\nos.system('git rebase main')\nEOF",
])
def test_a_real_rewrite_is_still_caught(cmd):
    assert _is_history_rewrite(cmd)


def test_amend_is_still_caught():
    assert _is_commit_amend("git add -A && git commit --amend --no-edit")
    assert _is_commit_amend("bash <<'EOF'\ngit commit --amend -m x\nEOF")


SID = "sid-own"


@pytest.fixture()
def wt_root(tmp_path, monkeypatch):
    """Two run worktrees: run_x belongs to SID, run_y to another session."""
    root = tmp_path / "worktrees"
    ws = tmp_path / "ws"
    for rid, sid in (("run_x", SID), ("run_y", "sid-other")):
        (root / rid / "swarmai").mkdir(parents=True)
        rd = ws / "Projects" / "SwarmAI" / ".artifacts" / "runs" / rid
        rd.mkdir(parents=True)
        (rd / "run.json").write_text('{"session_id": "%s"}' % sid)
    monkeypatch.setenv("SWARM_WORKTREE_ROOT", str(root))
    monkeypatch.setenv("SWARM_WORKSPACE", str(ws))
    return root


def test_a_rewrite_in_a_run_worktree_or_temp_repo_is_not_gated(wt_root):
    wt = wt_root / "run_x" / "swarmai"
    assert _rewrites_only_scratch(f"cd {wt} && git rebase main", "", SID)
    assert _rewrites_only_scratch("git rebase main", str(wt), SID)
    assert _rewrites_only_scratch("git -C /tmp/scratch rebase main", "/somewhere", SID)
    assert _rewrites_only_scratch(f"git -C {wt} reset --hard HEAD~1", "", SID)
    assert _rewrites_only_scratch("git commit --amend --no-edit", str(wt), SID)
    assert _rewrites_only_scratch("git rebase --onto main HEAD~2", str(wt), SID)


def test_a_rewrite_anywhere_else_or_unknown_stays_gated(wt_root):
    ws = "/Users/u/.swarm-ai/SwarmWS"   # not under a temp dir (tmp_path would be)
    wt = wt_root / "run_x" / "swarmai"
    assert not _rewrites_only_scratch("git rebase main", ws, SID)                # shared workspace
    assert not _rewrites_only_scratch("cd $X && git rebase main", "/tmp", SID)   # unknown dir
    assert not _rewrites_only_scratch("git rebase main", "", SID)                # no cwd
    assert not _rewrites_only_scratch(f"git -C {ws} rebase main", str(wt_root / "run_x"), SID)
    assert not _rewrites_only_scratch("git --git-dir=/elsewhere/.git rebase main", "/tmp", SID)
    assert not _rewrites_only_scratch("cd /tmp && git rebase main; cd -; git rebase x", ws, SID)
    # a rewrite a shell runs from a heredoc: its directory is not followed
    assert not _rewrites_only_scratch("bash <<'EOF'\ngit rebase main\nEOF", "/tmp", SID)
    # no session id: even the own worktree is not known to be own
    assert not _rewrites_only_scratch("git rebase main", str(wt), "")


@pytest.mark.parametrize("cmd,cwd", [
    # git retargeted through the environment
    ("GIT_DIR=/Users/u/.swarm-ai/SwarmWS/.git git reset --hard HEAD~3", "/tmp"),
    ("env GIT_DIR=/Users/u/.swarm-ai/SwarmWS/.git git reset --hard", "/tmp"),
    ("export GIT_DIR=/Users/u/.swarm-ai/SwarmWS/.git; git rebase main", "/tmp"),
    ("GIT_WORK_TREE=/x GIT_COMMON_DIR=/y git reset --hard", "/tmp"),
    # a directory change that is not followed
    ("echo `cd /tmp`; git reset --hard", "/Users/u/.swarm-ai/SwarmWS"),
    ('eval "cd $WS" && git reset --hard', "/tmp"),
    ("source ./go.sh && git reset --hard", "/tmp"),
    # after `;` a failed cd leaves the old directory
    ("cd /tmp/x; git reset --hard", "/Users/u/.swarm-ai/SwarmWS"),
])
def test_an_unfollowed_directory_or_git_target_stays_gated(wt_root, cmd, cwd):
    assert not _rewrites_only_scratch(cmd, cwd, SID)


@pytest.mark.parametrize("cmd", [
    "git filter-branch -- --all",
    "git update-ref refs/heads/main HEAD~3",
    "git rebase main feature",                 # names a branch to check out and move
    "git rebase --update-refs main",
    "git branch -f main HEAD~3",
    "git checkout -B main HEAD~3",
])
def test_a_rewrite_that_moves_shared_refs_stays_gated_in_the_own_worktree(wt_root, cmd):
    """A run worktree shares refs with the main checkout and every other run."""
    wt = wt_root / "run_x" / "swarmai"
    assert not _rewrites_only_scratch(cmd, str(wt), SID)


def test_another_sessions_run_worktree_stays_gated(wt_root):
    other = wt_root / "run_y" / "swarmai"
    assert not _rewrites_only_scratch("git reset --hard HEAD~1", str(other), SID)
    assert not _rewrites_only_scratch(f"git -C {other} rebase main", "", SID)


def test_a_temp_worktree_of_a_shared_repo_stays_gated(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: /Users/u/.swarm-ai/SwarmWS/.git/worktrees/wt\n")
    assert not _rewrites_only_scratch("git reset --hard HEAD~3", str(wt), SID)
    own = tmp_path / "repo"
    (own / ".git").mkdir(parents=True)
    assert _rewrites_only_scratch("git reset --hard HEAD~3", str(own), SID)


# ── runner loop ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("cmd", [
    "cat >> backend/tests/t.py <<'EOF'\nfor ar in (1, 2):\n    print(ar)  # pipeline_test.py\n"
    "EOF\nperl -e 'alarm 115; exec @ARGV' " + RUNNER,
    "python3 - <<'EOF'\nfor p in ['x']:\n    s = open(p).read()  # don't\nEOF\n"
    "perl -e 'alarm 115; exec @ARGV' " + RUNNER,
    "python3 -I - <<'EOF'\nfrom pathlib import Path\nfor x in []:\n    pass\nEOF\n" + RUNNER,
])
def test_a_for_in_a_heredoc_body_is_not_a_runner_loop(cmd):
    assert _runner_loop_kind(cmd) is None


@pytest.mark.parametrize("cmd,kind", [
    ("for i in 1 2; do perl -e 'alarm 115; exec @ARGV' " + RUNNER + "; done", "loop"),
    ("bash <<'EOF'\nfor i in 1 2; do " + RUNNER + "; done\nEOF", "loop"),
    (RUNNER + "; " + RUNNER, "repeat"),
    # one run outside a shell body and one inside add up
    (RUNNER + "\nbash <<'EOF'\n" + RUNNER + "\nEOF", "repeat"),
])
def test_a_real_runner_loop_is_still_caught(cmd, kind):
    assert _runner_loop_kind(cmd) == kind


# ── run owner token binding ──────────────────────────────────────────────────
@pytest.mark.parametrize("cmd", [
    CLI + " 2>&1 | tail -c 3000",
    CLI + " 2>&1 | python3 -c \"import json,sys;print(json.load(sys.stdin).get('ok'))\"",
    CLI + " > /tmp/ds.json; python3 -I -c \"import json;print(json.load(open('/tmp/ds.json')))\"",
    "cat > /tmp/deliver.json <<'EOF'\n{\"title\": \"it's done\", \"x\": \"$(not run)\"}\nEOF\n" + CLI,
    "cat > /tmp/a <<'A'\nx\nA\ncat >> /tmp/b <<-\"B\"\n\ty\n\tB\n" + CLI + " | head",
])
def test_the_cli_call_first_gets_the_token(cmd):
    b = _token_binding(cmd)
    assert b is not None
    head, rest = b
    assert rest.startswith("/repo/backend/.venv/bin/python /repo/backend/scripts/artifact_cli.py")
    assert cmd == head + rest


@pytest.mark.parametrize("cmd", [
    "cd /repo && " + CLI,
    "echo hi; " + CLI,
    CLI + " --data $(cat f)",
    "(" + CLI + ")",
    "cat > /tmp/f <<EOF\n$(rm -rf ~)\nEOF\n" + CLI,          # unquoted: the body runs
    "cat > /tmp/f <<'EOF'\nabc\n" + CLI,                     # unterminated heredoc
    "cat > $HOME/f <<'EOF'\nx\nEOF\n" + CLI,                 # a path that expands
    "python3 - <<'EOF'\nprint(1)\nEOF\n" + CLI,              # code runs before the CLI
    "cat > /repo/backend/scripts/artifact_cli.py <<'EOF'\nprint(1)\nEOF\n" + CLI,  # rewrites the CLI
])
def test_anything_else_before_the_cli_is_refused(cmd):
    assert _token_binding(cmd) is None


# ── python code judgment ─────────────────────────────────────────────────────
@pytest.mark.parametrize("code,can", [
    ("import re\nX = re.compile(r'a')\n# pytest", False),
    ("from pathlib import Path\ny = df.eval('a+b')", False),
    ("eval('1')", True),
    ("c = compile('x', 'f', 'exec')", True),
    ("getattr(m, 'x')()", True),
    ("import subprocess", True),
])
def test_a_method_named_like_a_builtin_is_not_dynamic_execution(code, can):
    assert _code_can_execute(code) is can
