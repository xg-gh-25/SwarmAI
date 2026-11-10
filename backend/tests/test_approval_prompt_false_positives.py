"""Approval-prompt false positives of 2026-10-22..26 (design
docs/design/2026-10-26-approval-prompt-false-positives.md §4), pinned both ways.

What is tested:
- A: a recursive rm whose target is relative or a variable is resolved from what the
  command itself says (the last absolute ``cd`` before it, else the hook's cwd; a
  variable assigned once from ``mktemp -d``). Everything else stays dangerous.
- B: the discard signature matches only a git call shape, so English prose naming
  "git ... checkout" is not a discard; a discard in a script that does not change
  directory is judged in the command's directory, not in every repo.
- C: the approval reason names the rule and the part of the command it matched, and
  every prompt (approve / deny / timeout) leaves exactly one row in
  gate_decisions.jsonl that gate_audit counts per rule.
- C2: that part is the line (and ``&&`` / ``;`` part) that tripped the rule, not the
  command's first line; with no such line it falls back to the first line. Display
  only: an approved multi-line command stays approved.

Each allow case is paired with true positives that must stay caught.
"""
from __future__ import annotations

import json

import pytest

from core.security_hooks import (
    _DISCARD_SIG_RE,
    _discard_dirs,
    _discards_work,
    _is_dangerous_rm,
)

REPO = "/Users/u/repo"


# ── A: rm targets resolved from the command ─────────────────────────────────
@pytest.mark.parametrize("cmd,cwd", [
    ("cd /tmp/x && rm -rf y", ""),
    ("mkdir -p /tmp/xgrev && cd /tmp/xgrev && rm -rf x && ls", ""),
    ("rm -rf y", "/var/folders/ab/T/s"),
    ("T=$(mktemp -d); rm -rf $T", ""),
    ('T=$(mktemp -d); echo hi > "$T/f"; rm -rf "$T/sub"', ""),
    ('T="$(mktemp -d)"\nrm -rf "${T}"', REPO),
    ("T=$(mktemp -d -t probe); rm -rf $T", ""),
    ("cd /tmp/x && T=$(mktemp -d) && rm -rf y", ""),
])
def test_a_resolvable_temp_target_is_not_dangerous(cmd, cwd):
    assert not _is_dangerous_rm(cmd, cwd=cwd)


@pytest.mark.parametrize("cmd,cwd", [
    ("cd /tmp/x && cd .. && rm -rf etc", ""),
    ("rm -rf src", REPO),
    ("rm -rf y", ""),
    ("cd /tmp/x; rm -rf y", ""),                    # a failed cd stays put
    ("cd /tmp/x || rm -rf y", ""),
    ("pushd /tmp/x && rm -rf y", ""),
    ("(cd /tmp/x) && rm -rf y", REPO),               # a subshell's cd does not leak
    ("cd /tmp/x && cd $D && rm -rf y", ""),
    ("cd /tmp/x && rm -rf ../y", ""),
    ("T=$(mktemp -d); T=/; rm -rf $T", ""),
    ("T=$(mktemp -d ~/x.XXX); rm -rf $T", ""),
    ("T=$(mktemp -d /etc/x.XXX); rm -rf $T", ""),
    ("rm -rf $T; T=$(mktemp -d)", ""),
    ("(T=$(mktemp -d)); rm -rf $T/sub", ""),
    ("T=$(mktemp -d) cmd; rm -rf $T", ""),           # an env prefix, not a shell variable
    ("T=$(mktemp -d); export T=/x; rm -rf $T", ""),
    ("T=$(mktemp -d); read T; rm -rf $T", ""),
    ("T=$(mktemp -d); rm -rf $T/../etc", ""),
    ("rm -rf $HOME/x", ""),
    ("rm -rf $TMPDIR/x", ""),
    ("rm -rf $KIROCREW_SCRATCH/x", ""),
])
def test_an_unresolvable_target_stays_dangerous(cmd, cwd):
    assert _is_dangerous_rm(cmd, cwd=cwd)


def test_a_relative_cwd_from_the_hook_is_not_trusted():
    assert _is_dangerous_rm("rm -rf y", cwd="tmp/x")


# ── B: discard signature and directory ───────────────────────────────────────
# The shape of the 10-25 12:34 command: an edit script whose docstring mentions git
# and checkout in prose.
PROSE_EDIT = ("python3 - <<'EOF'\nimport sys\nfrom pathlib import Path\n"
              "p = Path(sys.argv[0])\n"
              "s = '''Resolved from the recorded git identities, not from a guessed\n"
              "checkout location or the switch the user made.'''\n"
              "Path('doc.py').write_text(s)\nEOF")


def test_prose_naming_git_and_checkout_is_not_a_discard():
    assert not _discards_work(PROSE_EDIT)
    assert _discard_dirs(PROSE_EDIT, REPO) == []


@pytest.mark.parametrize("text", [
    "subprocess.run(['git', 'checkout', '--', 'a.py'])",
    "subprocess.run(['git', '-C', d, 'reset', '--hard'])",
    'subprocess.run(["git", "-c", "x=y", "clean", "-fd"])',
    "git -C /r checkout -- a",
    "git reset -q --hard",
    "os.system('git worktree remove --force w')",
])
def test_a_git_call_shape_still_matches(text):
    assert _DISCARD_SIG_RE.search(text)


@pytest.mark.parametrize("text", [
    "the git identities and a guessed checkout location",
    "git log, then later we checkout",
    "we use git\ncheckout happens later",
])
def test_prose_does_not_match(text):
    assert not _DISCARD_SIG_RE.search(text)


def test_an_unparseable_reset_hard_is_still_a_discard():
    assert _discards_work('git reset --hard "unbalanced')


SCRIPT_DISCARD = ("python3 - <<'EOF'\nimport subprocess\n"
                  "subprocess.run(['git', 'checkout', '--', 'a.py'])\nEOF")


def test_a_script_discard_without_a_directory_change_runs_where_the_command_does():
    assert _discard_dirs(SCRIPT_DISCARD, REPO) == [REPO]
    assert _discard_dirs("cd /r && " + SCRIPT_DISCARD, REPO) == ["/r"]
    assert _discard_dirs(SCRIPT_DISCARD, "") == [""]


@pytest.mark.parametrize("cmd", [
    ("python3 - <<'EOF'\nimport subprocess\n"
     "subprocess.run(['git', 'checkout', '--', 'a.py'], cwd='/elsewhere')\nEOF"),
    ("python3 - <<'EOF'\nimport os, subprocess\nos.chdir('/elsewhere')\n"
     "subprocess.run(['git', 'reset', '--hard'])\nEOF"),
    ("python3 - <<'EOF'\nimport subprocess\n"
     "subprocess.run(['git', '-C', '/elsewhere', 'reset', '--hard'])\nEOF"),
])
def test_a_script_that_changes_directory_is_judged_everywhere(cmd):
    assert _discard_dirs(cmd, REPO) is None


# ── C: the reason names the rule; every prompt is logged ─────────────────────
@pytest.fixture
def gate_log(tmp_path, monkeypatch):
    path = tmp_path / "gate_decisions.jsonl"
    monkeypatch.setenv("SWARM_GATE_LOG", str(path))
    monkeypatch.setattr("core.security_hooks.get_app_data_dir", lambda: tmp_path / "app")
    (tmp_path / "app").mkdir()
    return path


def _gate(answer, captured):
    from core.permission_manager import PermissionManager
    from core.security_hooks import create_dangerous_command_gate
    pm = PermissionManager()

    async def _decide(session_id, payload):
        captured.append(payload)
        return answer
    pm.request_decision = _decide  # type: ignore[method-assign]
    return create_dangerous_command_gate({"sdk_session_id": "s-1"}, "s-1", pm, True)


def _drain():
    from core import executors
    executors.get_pool("pipeline_signal").submit(lambda: None).result(timeout=10)


def _rows(path):
    _drain()
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


@pytest.mark.asyncio
@pytest.mark.parametrize("cmd,rule,part", [
    ("echo a && rm -rf $T && echo b", "rm_unresolved_target", "rm -rf $T"),
    ("sudo ls /root", "pattern", "sudo"),
])
async def test_the_reason_names_the_rule_and_the_part(gate_log, cmd, rule, part):
    captured = []
    await _gate("approve", captured)({"tool_name": "Bash", "tool_input": {"command": cmd}},
                                      "tu-1", None)
    reason = captured[0]["reason"]
    assert rule in reason and part in reason
    assert "Matches dangerous command pattern" not in reason


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["approve", "deny", "timeout"])
async def test_every_prompt_leaves_one_row(gate_log, answer):
    from core.gate_decision_log import with_decision_log
    gate = with_decision_log("dangerous_command_gate", _gate(answer, []))
    await gate({"tool_name": "Bash", "tool_input": {"command": "rm -rf $T"}}, "tu-1", None)
    rows = [r for r in _rows(gate_log) if r["gate"] == "dangerous_command_gate"]
    assert len(rows) == 1
    assert rows[0]["decision"] == answer
    assert rows[0]["rule"] == "rm_unresolved_target"
    assert rows[0]["match"] == "rm -rf $T"


@pytest.mark.asyncio
async def test_gate_audit_counts_prompts_per_rule(gate_log, capsys):
    gate = _gate("approve", [])
    for cmd in ("rm -rf $A", "rm -rf $B", "sudo ls"):
        await gate({"tool_name": "Bash", "tool_input": {"command": cmd}}, "tu", None)
    _drain()
    from scripts import gate_audit
    gate_audit.main(["--gate", "dangerous_command_gate"])
    out = capsys.readouterr().out
    assert "total=    3" in out
    import re
    assert re.search(r"rule rm_unresolved_target\s+2\s+approve:2", out)
    assert re.search(r"rule pattern:sudo \*\s+1\s+approve:1", out)


# ── C2: the card shows the line / part that tripped the rule, not line 1 ────
# Display only: the same commands are asked as before, and an approval is still
# remembered by the whole command (its hash), so it keeps applying.
_LONG = "echo " + "x" * 200


@pytest.mark.parametrize("cmd,rule,part", [
    ("R=~/.swarm-ai/swarm-content-repo\ncd $R\ngit push --force origin main",
     "irreversible_external", "git push --force origin main"),
    # a line that fits is shown whole (its cd / other parts are context) ...
    ("cd /tmp && git status && gh repo delete o/r --yes && echo done",
     "irreversible_external", "cd /tmp && git status && gh repo delete o/r --yes && echo done"),
    ("sudo ls; rm -rf ~/x", "pattern:sudo *", "sudo ls; rm -rf ~/x"),
    # ... a longer one is narrowed to its matching part (quotes respected)
    (f"cd /tmp && {_LONG} && git push -f origin x", "irreversible_external",
     "git push -f origin x"),
    (f"echo 'a && b' ; {_LONG} ; git push -f origin x", "irreversible_external",
     "git push -f origin x"),
    ("bash <<'EOF'\necho start\ngit push --force origin main\nEOF",
     "irreversible_external", "git push --force origin main"),
    ("R=~/.swarm-ai/x\nmv ~/.swarm-ai/SwarmWS/Projects/CMHK /tmp/old",
     "irreplaceable_store", "mv ~/.swarm-ai/SwarmWS/Projects/CMHK /tmp/old"),
    ("sudo ls /root", "pattern:sudo *", "sudo ls /root"),
])
def test_the_match_is_the_part_that_tripped_the_rule(cmd, rule, part):
    from core import security_hooks as sh
    hit = sh._danger_match(sh._command_as_run(cmd), list(sh.DEFAULT_DANGEROUS_PATTERNS), REPO)
    assert hit is not None and hit["rule"] == rule
    assert hit["match"] == part


@pytest.mark.parametrize("cmd,part", [
    ("R=~/.swarm-ai/swarm-content-repo\ngit -C $R reset --hard origin/main",
     "git -C $R reset --hard origin/main"),
    ("R=1\ncd /x && git status && git checkout -- a.py",
     "cd /x && git status && git checkout -- a.py"),
    ("python3 - <<'EOF'\nimport subprocess\nsubprocess.run(['git', 'reset', '--hard'])\nEOF",
     "subprocess.run(['git', 'reset', '--hard'])"),
    ("git reset --hard", "git reset --hard"),
    # a precise discard line wins over a line that only has a discard's call shape
    ("python3 - <<'EOF'\nsubprocess.run(['git', 'clean', '-fd'])\nEOF\ngit restore a.py",
     "git restore a.py"),
])
def test_the_discard_part_is_the_discarding_line(cmd, part):
    from core.security_hooks import _discard_part
    assert _discard_part(cmd) == part


def test_no_single_line_matching_falls_back_to_the_first_line():
    from core.security_hooks import _matched_part

    def boom(_s):
        raise RuntimeError("predicate failed")
    assert _matched_part("\n  first\nsecond", lambda s: False) == "first"
    assert _matched_part("first\nsecond", boom) == "first"
    # a \-continued line is one line; the first predicate that finds a line wins
    assert _matched_part("R=1\ngit push \\\n  -f", lambda s: "-f" in s) == "git push    -f"
    assert _matched_part("a b\nc d", lambda s: "z" in s, lambda s: "d" in s) == "c d"
    assert _matched_part("x" * 300, lambda s: True).endswith("…")
    # an oversized command is not judged line by line again
    assert _matched_part("first\n" + "y" * 30_000, lambda s: s.startswith("y")) == "first"


@pytest.mark.asyncio
async def test_the_discard_card_and_log_show_the_discarding_line(gate_log, monkeypatch):
    from core import security_hooks as sh

    def _cannot_check(*_a, **_k):
        raise RuntimeError("git timed out")   # the gate turns any failure into discard_unchecked
    monkeypatch.setattr(sh, "_foreign_uncommitted", _cannot_check)
    cmd = "R=~/.swarm-ai/swarm-content-repo\ngit -C $R reset --hard origin/main"
    captured = []
    await _gate("deny", captured)({"tool_name": "Bash", "tool_input": {"command": cmd},
                                   "cwd": REPO}, "tu-1", None)
    reason = captured[0]["reason"]
    assert reason.startswith("`git -C $R reset --hard origin/main`")
    assert "discard_unchecked" in reason and "R=~/" not in reason
    rows = [r for r in _rows(gate_log) if r["gate"] == "dangerous_command_gate"]
    assert rows[-1]["match"] == "git -C $R reset --hard origin/main"


@pytest.mark.asyncio
async def test_an_approved_multiline_command_stays_approved(gate_log):
    cmd = "R=~/.swarm-ai/x\ngit push --force origin main"
    captured = []
    gate = _gate("approve", captured)
    event = {"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": REPO}
    assert (await gate(event, "tu-1", None)) == {"decision": "approve"}
    assert "`git push --force origin main`" in captured[0]["reason"]
    assert (await gate(event, "tu-2", None)) == {"decision": "approve"}
    assert len(captured) == 1          # the second run is not asked again
