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
