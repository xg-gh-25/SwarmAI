"""Approval-prompt false positives, round 2 (design
docs/design/2026-11-25-approval-prompt-false-positives-r2.md), pinned both ways.

Of the 41 approvals one owner gave in two weeks, about 5 needed a person. The rest
came from seven causes, each fixed where it starts and each tested here:

- A: a repo that no longer exists (a sibling's removed session worktree) read as "git
  cannot tell", so every discard with an unknown directory asked. Gone = clean; a real
  git failure on a live work tree still asks.
- B: a variable the command itself assigns a literal path (``W=/abs; git -C $W ...``)
  was not used. One resolver (``_known_vars`` / ``_subst_vars``) now serves the discard
  directory walk, the recursive-rm check and the store check; any rebinding, any
  conditional or subshell assignment, any quoting it cannot see through → unknown.
- C: ``N=$(mktemp -d)/sub``, regenerable tool caches, and the fd number of a redirect
  (``2>/dev/null`` read as an operand ``2``) made temp cleanups ask.
- D: ``git checkout --detach <x>`` is a branch move, not a discard.
- E: prose in a Python body ("not a git checkout") matched the discard signature; the
  body is now parsed, and only a git call counts.
- F: ``mv`` into a store gated its destination even when nothing is overwritten.
- G: external_approval_gate wrote no gate_decisions row for its prompts.

Methodology: real git repos under tmp_path and a fake session router holding hook
contexts with ``_files_written`` (as test_discard_gate.py), the real gate with the
approval wait faked to "deny" (an ask shows as a deny, an approve as approve), and the
pure predicates for the rest. Each "not asked" case is paired with the cases that must
keep asking.
"""
from __future__ import annotations

import json
import os
import subprocess
from types import SimpleNamespace

import pytest

from core import repo_guidance as rg
from core import security_hooks as sh
from core import shared_checkout as sc

ME, SIB = "sess-me", "sess-sib"
REPO = "/Users/u/repo"


def _g(cwd, *a):
    return subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True,
                          text=True).stdout


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    sc._dirty_cache.clear()
    rg._root_cache.clear()
    monkeypatch.setattr("core.security_hooks.get_app_data_dir", lambda: tmp_path / "appdata")
    (tmp_path / "appdata").mkdir()
    for k, v in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                 "GIT_COMMITTER_EMAIL": "t@t"}.items():
        monkeypatch.setenv(k, v)


def _init(path):
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    for n in ("a.py", "b.py"):
        (path / n).write_text(n + "\n")
    _g(path, "add", "-A")
    _g(path, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "init")
    return path.resolve()


@pytest.fixture
def repos(tmp_path):
    base = tmp_path.resolve()          # macOS: /var → /private/var
    main = _init(base / "repo")
    other = _init(base / "other")
    return SimpleNamespace(main=main, other=other, base=base)


@pytest.fixture
def router(monkeypatch):
    from core import session_registry
    units = {}

    def add(sid):
        ctx = {"sdk_session_id": sid, "_files_written": set()}
        units[sid] = SimpleNamespace(_hook_session_context=ctx, session_id=sid, is_alive=True)
        return ctx
    monkeypatch.setattr(session_registry, "session_router",
                        SimpleNamespace(_units=units, list_units=lambda: list(units.values())),
                        raising=False)
    return add


def _gate(ctx):
    from core.permission_manager import PermissionManager
    pm = PermissionManager()

    async def _deny(request_id, timeout=300):
        return "deny"
    pm.wait_for_permission_decision = _deny  # type: ignore[method-assign]
    return sh.create_dangerous_command_gate(ctx, ME, pm, enable_human_approval=True)


async def _run(ctx, command, cwd=""):
    return await _gate(ctx)({"tool_name": "Bash", "cwd": str(cwd),
                             "tool_input": {"command": command}}, "tu-1", None)


def _asked(result) -> bool:
    return result != {"decision": "approve"}


def _sibling_dirty(router, path, sid=SIB):
    """A sibling session wrote *path* and left it uncommitted."""
    path.write_text("edited\n")
    router(sid)["_files_written"].add(str(path.resolve()))


def _gone_sibling(router, repos, me):
    """A sibling wrote into a session worktree that has since been removed (the
    reproduction of cause A): the root was cached while it existed."""
    wt = repos.base / "session-worktrees" / "x" / "repo"
    _g(repos.main, "worktree", "add", "-q", "-b", "session/x", str(wt), "main")
    router("sess-gone")["_files_written"].add(str(wt.resolve() / "x.py"))
    assert sh._foreign_uncommitted([""], me) == ""
    _g(repos.main, "worktree", "remove", "--force", str(wt))
    assert not wt.exists()
    return wt


# ── A: a repo that is gone holds no uncommitted work ─────────────────────────
def test_a_removed_sibling_worktree_reads_clean(repos, router):
    me = router(ME)
    _gone_sibling(router, repos, me)
    assert sh._foreign_uncommitted([""], me) == ""


def test_a_named_directory_in_a_removed_worktree_reads_clean(repos, router):
    me = router(ME)
    wt = _gone_sibling(router, repos, me)
    assert sh._foreign_uncommitted([str(wt)], me) == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("cmd", [
    # representative 3: W comes from a substitution, so the directory is unknown
    """W=$(python3 -c "import json;print(json.load(open('/tmp/x/vwt4.json'))['path'])"); """
    """git -C $W reset -q --hard main && $S py -m scripts.session_worktree remove $W""",
    # representative 7: cd through `;` and a mktemp dir, so the directory is unknown
    "set -e; d=$(mktemp -d); cd $d; git init -q r; cd r; git commit -q --allow-empty -m x; "
    "git checkout -q main 2>/dev/null || git checkout -q master",
])
async def test_an_unknown_directory_with_only_a_gone_sibling_is_not_asked(repos, router, cmd):
    me = router(ME)
    _gone_sibling(router, repos, me)
    assert not _asked(await _run(me, cmd))


@pytest.mark.asyncio
async def test_a_live_siblings_uncommitted_file_still_asks_beside_a_gone_one(repos, router):
    me = router(ME)
    _gone_sibling(router, repos, me)
    _sibling_dirty(router, repos.other / "a.py")
    assert _asked(await _run(me, "W=$(cat f); git -C $W reset --hard"))


@pytest.mark.asyncio
async def test_git_failing_on_a_live_work_tree_still_asks(repos, router, monkeypatch):
    me = router(ME)
    _sibling_dirty(router, repos.main / "a.py")
    monkeypatch.setattr(sc, "_git_status", lambda root, rels: None)
    assert _asked(await _run(me, "git reset --hard", repos.main))
    with pytest.raises(sc.GitUnknown):
        sh._foreign_uncommitted([str(repos.main)], me)


# ── B: a variable the command assigns a literal path is used ─────────────────
@pytest.mark.asyncio
@pytest.mark.parametrize("make_cmd", [
    # representatives 1, 2, 5, 4
    lambda r: f"W={r.other}; git -C $W checkout HEAD -- a.py",
    lambda r: (f"W={r.other}; git -C $W diff --quiet HEAD HEAD && git -C $W reset -q --hard main"
               ' && "$SWARM_BACKEND_BIN" py -m scripts.session_worktree remove $W'),
    lambda r: (f"W={r.other}; git -C $W checkout -q session/f2fe701fb3d1-20261125-160801-f5e8"
               " && git -C $W status --short"),
    lambda r: (f"W={r.other}; cd $W && git checkout -q --detach "
               "refs/hive/so_634e894171f0/87011c6ce4da3a40 && git log -1"),
    lambda r: f'W="{r.other}"\ngit -C "${{W}}" reset --hard',
])
async def test_a_resolved_directory_is_judged_where_it_is(repos, router, make_cmd):
    """A sibling's uncommitted file is in another repo: judged against every repo (the
    directory unknown) these asked; judged where they run, they do not."""
    me = router(ME)
    _sibling_dirty(router, repos.main / "a.py")
    assert not _asked(await _run(me, make_cmd(repos)))


@pytest.mark.asyncio
@pytest.mark.parametrize("make_cmd", [
    lambda r: f"W={r.main}; git -C $W reset --hard",
    lambda r: f"W={r.main}; cd $W && git checkout -- a.py",
    lambda r: f"W={r.other}; W=$(cat f); git -C $W reset --hard",
    lambda r: f"W={r.other}; W={r.main}; git -C $W reset --hard",
    lambda r: f"W={r.other}; export W={r.main}; git -C $W reset --hard",
    lambda r: f"(W={r.other}); git -C $W reset --hard",
    lambda r: f"W={r.other} true; git -C $W reset --hard",
    lambda r: f"false && W={r.other}; git -C $W reset --hard",
    lambda r: f"W={r.other}; source ./env.sh; git -C $W reset --hard",
    lambda r: f"for W in {r.other}; do :; done; git -C $W reset --hard",
    lambda r: f"W={r.other}; read W; git -C $W reset --hard",
    lambda r: f"W={r.other}; git -C '$W' reset --hard",
])
async def test_a_discard_over_a_siblings_file_through_a_variable_still_asks(repos, router,
                                                                          make_cmd):
    me = router(ME)
    _sibling_dirty(router, repos.main / "a.py")
    assert _asked(await _run(me, make_cmd(repos)))


def test_an_ordinary_builtin_or_external_program_keeps_the_value():
    """An external program cannot change the shell's variables, and these builtins bind
    nothing by an expansion they are handed."""
    reset = "git -C $W reset --hard"
    for prefix in ('export PATH="$P:$PATH"', "stat $W", "echo $W", "cd $W", "test -d $W",
                   "printf '%s' $W", "set -e", "python3 x.py $W", "mytool $W",
                   '"$SWARM_BACKEND_BIN" py -m x $W', "command -v jq", "env -i python3 x.py"):
        assert sh._discard_dirs(f"W=/abs; {prefix}; {reset}", "") == ["/abs"], prefix
    assert sh._dangerous_rm_segment("W=$(mktemp -d); stat $W; rm -rf $W") is None


def test_the_resolver_substitutes_only_a_proven_value():
    assert sh._discard_dirs("W=/abs; git -C $W checkout -q br", "/tmp") == []
    assert sh._discard_dirs("W=/abs; git -C $W reset --hard", "") == ["/abs"]
    assert sh._discard_dirs("W=/abs; git -C ${W}/sub reset --hard", "") == ["/abs/sub"]
    assert sh._discard_dirs("W=~/r; git -C $W reset --hard", "") == [
        os.path.expanduser("~/r")]


@pytest.mark.parametrize("prefix", [
    "W=/abs; W=/b",
    "export W=/abs",
    "W=/abs; export W",
    "W=/abs; unset W",
    "(W=/abs)",
    "{ W=/abs; }",
    "if true; then W=/abs; fi",
    "false && W=/abs",
    "true || W=/abs",
    "W=/abs | cat",
    "W=/abs &",
    "W=/abs true",
    "W=/a/../b",
    "W=/a*",
    "W='/a b'",
    "W='$x'",
    "W=",
    "W=/abs; IFS=/",
    "W=/abs; . ./x",
    "W=/abs; eval x",
    "W=/abs; echo `true`",
    "W=/abs; (( W = 1 ))",
    "W=/abs; let 'W = 1'",
    "W=/abs; printf -v W %s /",
    "W=/abs; declare -n R=W",
    "W=/abs; mapfile W",
    "W='~/abs'",
    "W=/abs; for i in 1; do W=/; done",
    # binders nobody listed: any other appearance of the name makes it unknown
    "W=/abs; builtin read W",
    "W=/abs; print -v W /",
    "W=/abs; set -A W /",
    "W=/abs; : ${W::=/}",
    "W=/abs; exec {W}>/dev/null",
    "W=/abs; printf -vW /",
    "W=/abs; vared W",
    'X=W; W=/abs; printf -v "$X" /',
    'W=/abs; Y=$(printf V); printf -v "$Y" /',
    'W=/abs; Y=$(printf V); builtin printf -v "$Y" /',
    'W=/abs; Y=$(printf V); declare -n R="$Y"; R=/',
    # a name spelled without its letters, code run by trap, zsh parameter flags
    "W=/abs; trap $'\\x57=~' DEBUG",
    "W=/abs; zformat -a $'\\x57' : x",
    "X=$'\\x57'; W=/abs; zformat -a $X : x",
    "X=$(printf V); W=/abs; : ${(P)X::=/}",
    "W=/abs; trap 'echo hi' EXIT",
    "W=/abs; X=$(printf V); zstat -A $X f",
    "W=/abs; X=$(printf V); zformat -a $X : x",
    'W=/abs; export "$X=/"',
    "W=/abs; stat -A $X f",
    "W=/abs; noglob read $X",
])
def test_a_variable_the_command_does_not_pin_stays_unknown(prefix):
    assert sh._discard_dirs(prefix + "; git -C $W reset --hard", "") in ([""], None)


def test_a_single_quoted_or_escaped_reference_is_not_substituted():
    assert sh._discard_dirs("W=/abs; git -C '$W' reset --hard", "") == [""]
    assert sh._discard_dirs("W=/abs; git -C \\$W reset --hard", "") == [""]


# ── C: rm of a fresh temp subdir, a tool cache, and redirect fd numbers ──────
@pytest.mark.parametrize("cmd,cwd", [
    # representatives 8, 9, 10
    ("T=$(cat /tmp/p); B=$(mktemp -d)/bad; cp -R $T $B; rm -rf $B/review $B/build", REPO),
    ('B="$(mktemp -d)/bad"; rm -rf "$B/x"', REPO),
    ("rm -rf /Users/u/.swarm-ai/skills/s_x/scripts/__pycache__ "
     "/Users/u/.swarm-ai/skills/s_x/tests/__pycache__ /Users/u/.swarm-ai/skills/s_x/.pytest_cache",
     REPO),
    ("rm -rf .mypy_cache backend/.ruff_cache */__pycache__", REPO),
    ("rm -rf backend/__pycache__/*.pyc", REPO),
    ("rm -rf /tmp/npmlink.* /tmp/gwt.* 2>/dev/null", ""),
    ("rm -rf /tmp/npmlink.* /tmp/gwt.* 2>/dev/null", REPO),
    ("rm -rf /tmp/a >/dev/null 2>&1", REPO),
    # B for rm: a literal temp path in a variable
    ('S=/tmp/x; rm -rf "$S"', REPO),
    ("set -e; W=/tmp/so_review_B/ws; rm -rf $W; mkdir -p $W/.context", REPO),
    ("W=/tmp/so; cd $W && rm -rf build", ""),
])
def test_a_provable_scratch_target_is_not_dangerous(cmd, cwd):
    assert sh._dangerous_rm_segment(cmd, cwd=cwd) is None


@pytest.mark.parametrize("cmd,cwd", [
    ("rm -rf ~/.Trash/x", ""),
    ("rm -rf $HOME/x", ""),
    ('S=~/.swarm-ai/runtime/2.5.5.diag; rm -rf "$S"', ""),
    ("rm -rf __pycache__/../src", REPO),
    ("rm -rf build", ""),
    ("rm -rf build", REPO),
    ("T=$(mktemp -d); T=/; rm -rf $T", ""),
    ("W=/tmp/a; export W=/; rm -rf $W", ""),
    ("(W=/tmp/a); rm -rf $W", ""),
    ("false && W=/tmp/a; rm -rf $W/etc", ""),
    ("W=/tmp/a; for i in 1 2; do rm -rf $W; W=/; done", ""),
    ("B=$(mktemp -d) /bad; rm -rf $B/x", ""),
    ("B=$(mktemp -d)/../x; rm -rf $B", ""),
    ("rm -rf /Users/u/p/__pycache__/", ""),
    # zsh: $W[1] is W's first character, $W:h its dirname
    ("W=/tmp/a; rm -rf $W[1]", ""),
    ("W=/tmp/a/b; rm -rf $W:h:h", ""),
    ("W=/tmp/a; rm -rf $W.bak", ""),
    ('W=/tmp/a; rm -rf "$W"[1]', ""),
    ('W=/tmp/a/b; rm -rf "$W":h', ""),
    ("rm -rf $X/__pycache__", ""),
    ("rm -rf __pycache__/*", ""),
    ("rm -rf /Users/u/p/__pycache__.bak", ""),
    ("echo 2 > /tmp/f; rm -rf 2", REPO),
    # a recursive rm inside a compound command is judged like any other
    ("for x in 1; do rm -rf /; done", ""),
    ("if true; then rm -rf ~; fi", ""),
    ("{ rm -rf /etc; }", ""),
    ("for x in 1; do sudo rm -rf /; done", ""),
    ("if true; then env rm -rf ~; fi", ""),
    ("W=/tmp/a; builtin read W; rm -rf $W", ""),
    ("W=/tmp/a; print -v W /; rm -rf $W", ""),
    # zsh glob qualifiers: /tmp/a/b(:h:h:h) is /
    ("W=/tmp/a/b; rm -rf $W(:h:h:h)", ""),
    ("rm -rf /tmp/a/b(:h:h:h)", ""),
    ("W=/tmp/a; trap $'\\x57=~' DEBUG; rm -rf $W", ""),
    ("exec rm -rf /", ""),
    ("exec -a x rm -rf /", ""),
    ("if true; then exec sudo rm -rf /; fi", ""),
    ("exec -ca x rm -rf /", ""),
    ("exec -la x rm -rf /", ""),
    ("exec -aNAME rm -rf /", ""),
    # precommand modifiers run the next word, on the rm path too
    ("builtin exec rm -rf /", ""),
    ("builtin exec -a x rm -rf /", ""),
    ("noglob rm -rf /", ""),
    ("- rm -rf /", ""),
    # zsh sets MATCH itself on [[ =~ ]]
    ("MATCH=/tmp/a; [[ / =~ .* ]]; rm -rf $MATCH", ""),
    # clustered binding options
    ('W=$(mktemp -d); read X; print -rv "$X" /; rm -rf $W', ""),
    ('W=$(mktemp -d); read X; wait -np "$X"; rm -rf $W', ""),
    ('W=$(mktemp -d); read X; set -eA "$X" /; rm -rf $W', ""),
    # a script handed to a shell behind a keyword, exec or a precommand modifier
    ("exec sh -c 'rm -rf ~'", ""),
    ("exec -a x sh -c 'rm -rf ~'", ""),
    ("noglob bash -c 'rm -rf ~'", ""),
    ("- sh -c 'rm -rf ~'", ""),
    ("for x in a; do sh -c 'rm -rf ~'; done", ""),
    ("if true; then bash -c 'rm -rf ~'; fi", ""),
    ("! sh -c 'rm -rf ~'", ""),
    ("{ sh -c 'rm -rf ~'; }", ""),
    ("noglob bash <<EOF\nrm -rf ~\nEOF", ""),
    # wrappers with their own options
    ("command -p rm -rf /home/x/proj", ""),
    ("nice -n 5 rm -rf /home/x/proj", ""),
    ("env -i rm -rf /home/x/proj", ""),
    ("env -i sh -c 'rm -rf /home/x/proj'", ""),
    ("env -u HOME rm -rf /home/x/proj", ""),
    ("env -S 'rm -rf /home/x/proj'", ""),
    ("time -p rm -rf /home/x/proj", ""),
    ("timeout -k 5 60 env -i rm -rf /home/x/proj", ""),
    ("doas -u root rm -rf /home/x/proj", ""),
    ("env -iu FOO rm -rf /home/x/proj", ""),
    ("doas -nu root rm -rf /home/x/proj", ""),
    ('env -S"-i rm -rf /home/x/proj"', ""),
    ("env --split-string='-i rm -rf /home/x/proj'", ""),
    ("sudo -Eu root rm -rf /home/x/proj", ""),
    # a program named by an expansion that may be `source`
    ("W=/tmp/a; ${C:-source} ./x.sh; rm -rf $W", ""),
    ('W=/tmp/a; C=source; "$C" ./x.sh; rm -rf $W', ""),
    ("W=/tmp/a; command -p source ./x.sh; rm -rf $W", ""),
    ("W=/tmp/a; {source,./x.sh}; rm -rf $W", ""),
    ("W=/tmp/a; sourc[e] ./x.sh; rm -rf $W", ""),
    ('W=$(mktemp -d); read X Y; printf -$X "$Y" /; rm -rf $W', ""),
    ('W=$(mktemp -d); read X Y; print -$X "$Y" /; rm -rf $W', ""),
])
def test_an_unprovable_target_stays_dangerous(cmd, cwd):
    assert sh._dangerous_rm_segment(cmd, cwd=cwd) is not None


def test_a_cache_name_that_resolves_elsewhere_stays_dangerous(tmp_path, monkeypatch):
    monkeypatch.setattr(sh, "_SAFE_RM_PREFIXES", ())
    monkeypatch.setattr(sh, "_SAFE_RM_EXACT", frozenset())
    base = tmp_path.resolve()
    (base / "real").mkdir()
    (base / "p").mkdir()
    (base / "p" / "__pycache__").mkdir()
    (base / "link").mkdir()
    os.symlink(base / "real", base / "link" / "__pycache__")
    assert sh._dangerous_rm_segment(f"rm -rf {base}/p/__pycache__") is None
    assert sh._dangerous_rm_segment(f"rm -rf {base}/link/__pycache__") is not None
    assert sh._dangerous_rm_segment(f"rm -rf {base}/link/__pycache__/*") is not None


@pytest.mark.parametrize("cmd,want", [
    ("rm -rf /tmp/a 2>/dev/null", [["rm", "-rf", "/tmp/a"]]),
    ("rm -rf /tmp/a >/dev/null 2>&1", [["rm", "-rf", "/tmp/a"]]),
    ("git checkout -q main 2>/dev/null || git checkout -q master",
     [["git", "checkout", "-q", "main"], ["git", "checkout", "-q", "master"]]),
    ("make 1>>log 2>err", [["make"]]),
    ("echo 2 > /tmp/x", [["echo", "2"]]),
    ('echo "2>x" a2>b', [["echo", "2>x", "a2"]]),
])
def test_a_redirects_fd_number_is_not_a_word(cmd, want):
    assert sh._gate_shell_segments(cmd) == want


# ── D: a detached checkout is a branch move ──────────────────────────────────
@pytest.mark.parametrize("cmd,discards", [
    ("git checkout -q --detach refs/hive/x/y", False),
    ("git checkout --detach", False),
    ("git switch --detach x", False),
    ("git switch -d x", False),
    ("git checkout -f --detach x", True),
    ("git checkout --detach -f x", True),
    ("git checkout --force --detach x", True),
    ("git checkout -qf --detach x", True),
    ("git checkout --detach x -- a.py", True),
    ("git checkout -- .", True),
    ("git checkout HEAD -- path", True),
    ("git switch --detach --discard-changes x", True),
])
def test_detach_is_a_branch_move_unless_forced(cmd, discards):
    seg = sh._gate_shell_segments(cmd)[0]
    assert sh._segment_discards(seg, None) is discards


@pytest.mark.asyncio
@pytest.mark.parametrize("cmd,asked", [
    ("git checkout -q --detach refs/hive/x/y", False),
    ("git checkout -f --detach x", True),
    ("git checkout -- .", True),
    ("git checkout HEAD -- path", True),
])
async def test_an_unknown_directory_detach_is_asked_only_when_forced(repos, router, cmd, asked):
    me = router(ME)
    _sibling_dirty(router, repos.main / "a.py")
    assert _asked(await _run(me, cmd)) is asked


# ── E: a Python body counts as a discard only when it runs git ───────────────
_PROSE_6A = ("python3 - <<'EOF'\nimport re, sys\nfrom pathlib import Path\n"
             "p = Path('tests/t.py')\ns = p.read_text()\n"
             "s = s.replace('pass', 'pytest.skip(\"not a git checkout\")')\n"
             "p.write_text(s)\nEOF")
_PROSE_6B = ("python3 - <<'EOF'\nimport sys\nfrom pathlib import Path\n"
             "p = Path('core/x.py')\n"
             "p.write_text('def f():\\n    \"\"\"``git worktree remove`` removes it.\"\"\"\\n')\n"
             "# a comment: git reset --hard is what the old code did\nEOF")


_PROSE_DEF = ("python3 - <<'EOF'\nimport re, sys\ndef patch(path, old, new):\n"
              "    from pathlib import Path\n    p = Path(path)\n"
              "    p.write_text(p.read_text().replace(old, new))\n"
              "patch('x.py', 'a', 'git checkout -- b')\nEOF")


@pytest.mark.parametrize("cmd", [_PROSE_6A, _PROSE_6B, _PROSE_DEF,
                                 "python3 -c \"import sys; print('git checkout -- x')\""])
def test_prose_in_a_python_body_is_not_a_discard(cmd):
    assert not sh._discards_work(cmd)
    assert sh._discard_dirs(cmd, REPO) in ([], None)


@pytest.mark.asyncio
@pytest.mark.parametrize("cmd", [_PROSE_6A, _PROSE_6B])
async def test_representative_6_is_not_asked(repos, router, cmd):
    me = router(ME)
    _sibling_dirty(router, repos.main / "a.py")
    assert not _asked(await _run(me, cmd, repos.main))


def _py(body):
    return "python3 - <<'EOF'\n" + body + "\nEOF"


@pytest.mark.parametrize("cmd", [
    _py("import subprocess\nsubprocess.run(['git', 'reset', '--hard'])"),
    _py("import subprocess\nsubprocess.run(['git', '-C', d, 'checkout', '--', 'a.py'])"),
    _py("import subprocess\nsubprocess.run('git checkout -- a.py', shell=True)"),
    _py("import os\nos.system('cd x && git reset --hard')"),
    _py("import subprocess\nsubprocess.run(f'git -C {d} reset --hard', shell=True)"),
    _py("from subprocess import run as r\nr(['git', 'clean', '-fd'])"),
    _py("import subprocess\nsubprocess.run(['sh', '-c', 'git reset --hard'])"),
    # a value the parser cannot see: the signature rule holds
    _py("import subprocess\ncmd = 'git reset --hard'\nsubprocess.run(cmd, shell=True)"),
    _py("import subprocess\nexec(\"subprocess.run(['git','reset','--hard'])\")"),
    # does not parse: the signature rule holds
    _py("import subprocess\nsubprocess.run(['git', 'reset', '--hard']\nif"),
    "python3 -c \"import subprocess; subprocess.run(['git','reset','--hard'])\"",
    # a process function used as a value: what it runs is not seen
    _py("import subprocess, functools\n"
        "functools.partial(subprocess.run, shell=True)('git reset --hard')"),
    _py("import threading, os\n"
        "threading.Thread(target=os.system, args=('git reset --hard',)).start()"),
    _py("import os\nlist(map(os.system, ['git checkout -- .']))"),
    _py("import os\n{'x': os.system}['x']('git reset --hard')"),
    _py("import subprocess\nfrom concurrent.futures import ThreadPoolExecutor\n"
        "ThreadPoolExecutor().submit(subprocess.run, 'git reset --hard', shell=True)"),
    _py("import subprocess\nr = subprocess.run\nr('git reset --hard', shell=True)"),
    # reached by its name as a string, or through a computed callee
    _py("import os, operator\noperator.methodcaller('system', 'git reset --hard')(os)"),
    _py("import os, operator\noperator.attrgetter('system')(os)('git reset --hard')"),
    _py("import os\nvars(os)['system']('git reset --hard')"),
    _py("import os\nos.__dict__['system']('git reset --hard')"),
    _py("import os\nf = {'k': 1}\n[os.popen][0]('git reset --hard')"),
    # a callee that is not known to only move data: the signature rule holds
    _py("import os, inspect\nfor n, f in inspect.getmembers(os):\n"
        "    if n == 'sys' + 'tem':\n        f('git reset --hard')"),
    _py("import os, inspect\nf = dict(inspect.getmembers(os))['sys' + 'tem']\n"
        "f('git reset --hard')"),
    _py("import os\nfns = [os.getcwd]\nfns[0]('git reset --hard')"),
    # a module outside the standard library may run anything when imported
    _py("import mymod\nprint('git reset --hard')"),
    _py("from . import helper\nprint('git reset --hard')"),
    # a standard module that runs code through a data-looking call
    _py("import pickle\npickle.loads(b\"cos\\nsystem\\n(S'git reset --hard'\\ntR.\")"),
    _py("import pickle\npickle.load(open('/tmp/p', 'rb'))\n# git reset --hard"),
    _py("import webbrowser\nwebbrowser.get(\"sh -c 'git reset --hard' %s\").open('x')"),
    # the command handed to a shell on stdin or in a file the body wrote
    _py("import subprocess\nsubprocess.run('bash', input='git reset --hard', text=True)"),
    _py("import subprocess\nsubprocess.run(['sh', '-s'], input=b'git reset --hard')"),
    _py("import subprocess\n# runs git reset --hard\n"
        "subprocess.run(['xargs', 'git'], input='reset --hard', text=True)"),
    _py("import subprocess\nopen('/tmp/x.sh', 'w').write('git reset --hard')\n"
        "subprocess.run(['sh', '/tmp/x.sh'])"),
    _py("import subprocess\nopen('/tmp/x.sh', 'w').write('git reset --hard')\n"
        "subprocess.run(['/tmp/x.sh'])"),
    # exec with its options runs git too
    "exec -a x git reset --hard",
    "exec -l git reset --hard",
    "exec -ca x git reset --hard",
    "noglob git reset --hard",
])
def test_a_git_call_in_a_python_body_is_a_discard(cmd):
    assert sh._discards_work(cmd)


@pytest.mark.asyncio
async def test_a_python_reset_hard_over_a_siblings_file_is_asked(repos, router):
    me = router(ME)
    _sibling_dirty(router, repos.main / "a.py")
    cmd = _py("import subprocess\nsubprocess.run(['git','reset','--hard'])")
    assert _asked(await _run(me, cmd, repos.main))
    cmd = "python3 -c \"import subprocess; subprocess.run(['git','reset','--hard'])\""
    assert _asked(await _run(me, cmd, repos.main))


# ── F: mv into a store is judged like cp; temp copies are scratch ────────────
@pytest.mark.parametrize("cmd", [
    # representative 12 (nothing overwritten where it lands)
    "mv /Users/u/.swarm-ai/SwarmWS/Knowledge/Designs/2026-11-25-x-design.md "
    "/Users/u/.swarm-ai/SwarmWS/Projects/SwarmAI/2-understanding/knowledge/designs/",
    # representative 11 (a copy into /tmp; the DDD is only read)
    "W=/tmp/so/iso2/SwarmWS; p=/Users/u/.swarm-ai/SwarmWS/Projects/SwarmAI; n=SwarmAI; "
    "mkdir -p $W/Projects/$n; cp -R $p/2-understanding $W/Projects/$n/",
    "set -e; W=/tmp/so_review_B/ws; mkdir -p $W/.context; cp /tmp/so_base/c/*.md $W/.context/",
])
def test_a_move_or_copy_that_destroys_no_store_is_not_gated(cmd):
    assert not sh._targets_irreplaceable_store(cmd)


@pytest.mark.parametrize("cmd", [
    "mv Projects/CMHK /tmp",
    "mv .context/MEMORY.md x",
    "mv new.md Projects/P/PRODUCT.md",
    "mv -t Projects/P new.md",
    "mv /Users/u/.swarm-ai/SwarmWS/Projects/CMHK /tmp/old",
    "rm .context/MEMORY.md",
    "cp /dev/null Knowledge/Library/x.md",
    "W=/Users/u/ws; mv $W/Projects/CMHK /tmp",
    "W=$(pwd); mv new.md $W/Projects/P/PRODUCT.md",
    "if true; then rm .context/MEMORY.md; fi",
    "if true; then sudo rm .context/MEMORY.md; fi",
    "noglob rm .context/MEMORY.md",
    "noglob sh -c 'rm .context/MEMORY.md'",
    "for x in a; do sh -c 'rm .context/MEMORY.md'; done",
    "nice -n 5 rm .context/MEMORY.md",
    "env -i mv Projects/CMHK /tmp",
])
def test_a_store_moved_away_or_overwritten_is_gated(cmd):
    assert sh._targets_irreplaceable_store(cmd)


def test_mv_destination_is_gated_only_when_it_overwrites(tmp_path, monkeypatch):
    monkeypatch.setattr(sh, "_SAFE_RM_PREFIXES", ())
    monkeypatch.setattr(sh, "_SAFE_RM_EXACT", frozenset())
    base = tmp_path.resolve()
    proj = base / "ws" / "Projects" / "P"
    proj.mkdir(parents=True)
    (base / "new.md").write_text("x")
    assert not sh._targets_irreplaceable_store(f"mv {base}/new.md {proj}/")
    (proj / "new.md").write_text("old")
    assert sh._targets_irreplaceable_store(f"mv {base}/new.md {proj}/")
    assert sh._targets_irreplaceable_store(f"mv {base}/new.md {proj}/new.md")


def test_a_store_under_a_temp_dir_is_scratch_unless_a_link_leads_out(tmp_path, monkeypatch):
    base = tmp_path.resolve()
    scratch = base / "scratch"
    monkeypatch.setattr(sh, "_SAFE_RM_PREFIXES", (str(scratch) + "/",))
    monkeypatch.setattr(sh, "_SAFE_RM_EXACT", frozenset())
    ddd = scratch / "ws" / "Projects" / "P" / "2-understanding"
    ddd.mkdir(parents=True)
    (base / "src" / "2-understanding").mkdir(parents=True)
    assert not sh._targets_irreplaceable_store(
        f"cp -R {base}/src/2-understanding {scratch}/ws/Projects/P/")
    real = base / "real" / "Projects" / "P"
    real.mkdir(parents=True)
    (real / "PRODUCT.md").write_text("x")
    os.symlink(base / "real", scratch / "link")
    assert sh._targets_irreplaceable_store(f"rm {scratch}/link/Projects/P/PRODUCT.md")


# ── the representative commands through the real gate ───────────────────────
@pytest.mark.asyncio
@pytest.mark.parametrize("cmd", [
    "rm -rf /tmp/npmlink.* /tmp/gwt.* 2>/dev/null",
    "T=$(cat /tmp/p); B=$(mktemp -d)/bad; cp -R $T $B; rm -rf $B/review $B/build",
    "rm -rf /Users/u/.swarm-ai/skills/s_x/scripts/__pycache__ /Users/u/.swarm-ai/skills/s_x/.pytest_cache",
    "W=/tmp/so/iso2/SwarmWS; p=/Users/u/.swarm-ai/SwarmWS/Projects/SwarmAI; n=SwarmAI; "
    "mkdir -p $W/Projects/$n; cp -R $p/2-understanding $W/Projects/$n/",
    "mv /Users/u/.swarm-ai/SwarmWS/Knowledge/Designs/2026-11-25-x-design.md "
    "/Users/u/.swarm-ai/SwarmWS/Projects/SwarmAI/2-understanding/knowledge/designs/",
])
async def test_the_representative_commands_are_not_asked(repos, router, cmd):
    me = router(ME)
    assert not _asked(await _run(me, cmd, repos.main))


@pytest.mark.asyncio
@pytest.mark.parametrize("cmd", [
    "git push --force origin main",
    "gh api -X DELETE repos/o/r/hooks/1",
    "gh repo edit o/r --visibility public",
    "rm -rf ~/.Trash/x",
    "rm -rf $HOME/x",
    'S=~/.swarm-ai/runtime/2.5.5.diag; rm -rf "$S"',
    "rm -rf __pycache__/../src",
    "T=$(mktemp -d); T=/; rm -rf $T",
    "W=/tmp/a; export W=/; rm -rf $W",
    "(W=/tmp/a); rm -rf $W",
    "mv Projects/CMHK /tmp",
    "rm .context/MEMORY.md",
    "cp /dev/null Knowledge/Library/x.md",
])
async def test_the_must_keep_commands_are_still_asked(repos, router, cmd):
    me = router(ME)
    assert _asked(await _run(me, cmd, repos.main))


@pytest.mark.asyncio
async def test_rm_build_with_an_unknown_cwd_is_still_asked(repos, router):
    me = router(ME)
    assert _asked(await _run(me, "rm -rf build", ""))


# ── G: every external_approval_gate prompt leaves one row ────────────────────
class _PM:
    def __init__(self, answer):
        self.answer = answer

    def is_resolved(self, *a):
        return False

    def store_pending_request(self, req):
        pass

    def remove_pending_request(self, request_id):
        pass

    def pop_decided_by(self, request_id):
        return "person"

    async def request_decision(self, sid, item, timeout=None):
        return self.answer


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["approve", "deny", "timeout"])
async def test_every_external_prompt_leaves_one_row(tmp_path, monkeypatch, answer):
    from core import executors
    from core.gate_decision_log import with_decision_log
    log = tmp_path / "gate_decisions.jsonl"
    monkeypatch.setenv("SWARM_GATE_LOG", str(log))
    gate = with_decision_log("external_approval_gate", sh.create_external_approval_gate(
        {"sdk_session_id": "s-1"}, "s-1", _PM(answer), True))
    await gate({"tool_name": "mcp__slack__post_message", "tool_input": {"text": "hi"}},
               "toolu_1", None)
    executors.get_pool("pipeline_signal").submit(lambda: None).result(timeout=10)
    rows = [json.loads(x) for x in log.read_text().splitlines() if x.strip()]
    assert len(rows) == 1
    row = rows[0]
    assert row["gate"] == "external_approval_gate"
    assert row["decision"] == answer
    assert row["rule"] == "external_tool"
    assert row["match"] == "mcp__slack__post_message"
    assert row["prompted"] is True
