"""Gate false positives of 2026-10-20, pinned in both directions.

Every command shape below was denied, or sent for approval, during one day of work
while doing nothing dangerous. Each allow case is paired with the true positives
that must stay caught, so a fix that only loosens a gate turns a test red.

What is tested:
- working-tree discard (dangerous_command_gate): a heredoc body is judged by what
  reads it (a file being written is data, a shell or code that can run something is
  not); the directory each discard runs in is followed through ``cd X &&`` and
  ``git -C`` (_discard_dirs), and a git target set through the environment or an
  unfollowed directory change gives None (cannot tell). Whose files would be lost is
  tested in test_discard_gate.py;
- run owner session hand-over (run_owner_gate._token_binding): the CLI call may be
  followed by output plumbing and preceded by quoted ``cat > FILE <<'EOF'`` writes;
- python code judgment (_code_can_execute): ``re.compile(`` is a method, not the builtin.
"""
from __future__ import annotations

import pytest

from core.run_owner_gate import _token_binding
from core.security_hooks import (
    _code_can_execute,
    _is_commit_amend,
    _discard_dirs,
    _discards_work,
)

CLI = ("python-backend pipeline-v1 land "
       "--project SwarmAI --run-id run_x --stage deliver")

# An edit script whose text names rebase and has an odd apostrophe (the shlex split of
# the old naive segmenter failed on it and the raw-text fallback matched "rebase").
EDIT_BODY = ('import sys\ns = open(sys.argv[1]).read()\n'
             's = s.replace("a", """Name of an in-progress merge / rebase / cherry-pick;\n'
             "it's one rev-parse.\"\"\")\nopen(sys.argv[1], 'w').write(s)\n")


# ── working-tree discard ─────────────────────────────────────────────────────
@pytest.mark.parametrize("cmd", [
    "cat > /tmp/e1.py <<'PYEOF'\n" + EDIT_BODY + "PYEOF\npython3 -I /tmp/e1.py /x",
    "cat >> notes.md <<'EOF'\nwe git reset --hard, then git checkout -- .\nEOF",
    "python3 -I - <<'EOF'\nfrom pathlib import Path\n# after git checkout; don't\n"
    "Path('a').write_text('x')\nEOF",
    "git commit -F - <<'EOF'\nUndo the git reset --hard and the --amend\nEOF",
])
def test_a_heredoc_body_that_is_data_is_not_a_discard(cmd):
    assert not _discards_work(cmd)
    assert not _is_commit_amend(cmd)


@pytest.mark.parametrize("cmd", [
    "git reset --hard HEAD~1",
    "echo hi && git reset --hard HEAD~1",
    "git -C /repo checkout -- a.py",
    "bash <<'EOF'\ncd /x\ngit reset --hard\nEOF",
    "sh -s <<'EOF'\ngit clean -fd\nEOF",
    "python3 - <<'EOF'\nimport subprocess\nsubprocess.run(['git', 'reset', '--hard'])\nEOF",
    "perl - <<'EOF'\nsystem('git checkout .');\nEOF",
    # option values are not a script operand; -s and stdin paths read stdin
    "bash -euo pipefail <<'EOF'\ngit reset --hard\nEOF",
    "bash -o pipefail <<'EOF'\ngit reset --hard\nEOF",
    "bash +x <<'EOF'\ngit reset --hard\nEOF",
    "bash -s x y <<'EOF'\ngit reset --hard\nEOF",
    "bash --rcfile /dev/null <<'EOF'\ngit reset --hard\nEOF",
    "bash /dev/stdin <<'EOF'\ngit reset --hard\nEOF",
    "sh /dev/fd/0 <<'EOF'\ngit restore a.py\nEOF",
    "source /dev/stdin <<'EOF'\ngit reset --hard\nEOF",
    ". /dev/stdin <<'EOF'\ngit reset --hard\nEOF",
    "python3 /dev/stdin <<'EOF'\nimport os\nos.system('git reset --hard')\nEOF",
    'git reset --hard "unbalanced',
])
def test_a_real_discard_is_still_caught(cmd):
    assert _discards_work(cmd)


@pytest.mark.parametrize("cmd", [
    "git rebase -i HEAD~3",
    "git rebase -q main",
    "git commit --amend --no-edit",
    "git reset --soft HEAD~1",
    "git reset --keep HEAD~1",          # git refuses to drop local changes
    "git reset -q -- a.py",             # unstage-only
    "git branch -f main HEAD~3",
    "git branch -D feature",
    "git update-ref refs/heads/main HEAD~3",
    "git filter-branch -- --all",
    "git checkout -b feature",
    "git checkout -B main HEAD~3",
    "git restore --staged a.py",
    "git clean -n",
    "git clean -fdn",
    "git worktree remove /x",           # git refuses a dirty worktree without --force
    "git stash drop",
    "echo git reset --hard",
])
def test_a_ref_move_or_safe_op_is_not_a_discard(cmd):
    assert not _discards_work(cmd)


def test_amend_is_still_caught():
    assert _is_commit_amend("git add -A && git commit --amend --no-edit")
    assert _is_commit_amend("bash <<'EOF'\ngit commit --amend -m x\nEOF")


def test_the_directory_of_each_discard_is_followed(tmp_path):
    (tmp_path / "a.py").write_text("x")
    t = str(tmp_path)
    assert _discard_dirs("git rebase main && git status", t) == []
    assert _discard_dirs("git reset --hard", t) == [t]
    assert _discard_dirs("cd /r && git reset --hard", t) == ["/r"]
    assert _discard_dirs("git -C /r checkout -- a.py", t) == ["/r"]
    assert _discard_dirs("cd /r; git reset --hard", t) == [""]       # a failed cd stays put
    assert _discard_dirs("(cd /r && git reset --hard) && git clean -f", t) == ["/r", t]
    assert _discard_dirs("git worktree remove --force /w", t) == ["/w"]
    assert _discard_dirs("git worktree remove -f w", t) == [t + "/w"]
    assert _discard_dirs("git checkout a.py", t) == [t]              # names a file here
    assert _discard_dirs("git checkout main", t) == []               # a branch switch
    assert _discard_dirs("git --git-dir=/e/.git reset --hard", t) == [""]


@pytest.mark.parametrize("cmd", [
    # git retargeted through the environment
    "GIT_DIR=/Users/u/.swarm-ai/SwarmWS/.git git reset --hard HEAD~3",
    "env GIT_DIR=/Users/u/.swarm-ai/SwarmWS/.git git reset --hard",
    "export GIT_DIR=/Users/u/.swarm-ai/SwarmWS/.git; git reset --hard",
    "GIT_WORK_TREE=/x GIT_COMMON_DIR=/y git reset --hard",
    # a directory change that is not followed
    "echo `cd /tmp`; git reset --hard",
    'eval "cd $WS" && git reset --hard',
    "source ./go.sh && git reset --hard",
    # a discard a shell runs from a heredoc
    "bash <<'EOF'\ngit reset --hard\nEOF",
    'git reset --hard "unbalanced',
])
def test_an_unfollowed_directory_or_git_target_cannot_be_told(cmd):
    assert _discard_dirs(cmd, "/tmp") is None


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
    assert rest.startswith("python-backend pipeline-v1")
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
])
def test_anything_else_before_the_cli_gets_no_session_id(cmd):
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
