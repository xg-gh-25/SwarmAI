"""core.pipeline_command — the one recogniser of pipeline commands (design
2026-10-24-swarmai-usable-by-anyone §3.2, r14).

- both forms are recognised: ``"$SWARM_BACKEND_BIN" pipeline-v1 <sub>`` (and a
  ``python-backend`` path) and the legacy ``python .../pipeline_v1.py`` until S6;
- a pipeline word that is only an argument is ``foreign`` and never gets a token;
- the program form is OURS only via the unreassigned variable or this backend's binary;
- the watchers (stage events, run-create, daily activity) see the program form;
- the command the agent is told to run is the program form;
- structural: no product module outside core/pipeline_command.py matches pipeline
  command text itself.
"""
import re
from pathlib import Path

import pytest

from core import pipeline_command as pc

PROG = '"$SWARM_BACKEND_BIN" pipeline-v1'


def _one(command):
    calls, single = pc.calls(command)
    assert len(calls) == 1, calls
    return calls[0], single


def test_program_form_is_a_v1_call():
    call, single = _one(f"{PROG} stage-complete --project P --run-id run_ab")
    assert single
    assert (call["tool"], call["sub"], call["run_id"], call["project"], call["form"]) == \
        ("pipeline_v1", "stage-complete", "run_ab", "P", "program")
    assert pc.is_ours(call)


def test_legacy_form_is_still_recognised():
    call, _ = _one("python3 -u /x/backend/scripts/pipeline_v1.py status --run-id run_q")
    assert (call["tool"], call["sub"], call["form"]) == ("pipeline_v1", "status", "script")
    # the test runner is gone (S6-1): its script is not a pipeline call any more
    assert pc.calls("perl -e 'alarm 110; exec @ARGV' python /x/scripts/pipeline_test.py "
                    "--run-id run_q")[0] == []


@pytest.mark.parametrize("command", [
    "echo pipeline-v1 status", "grep -n pipeline-v1 notes.md", "cat backend/scripts/pipeline_v1.py",
    "claude --print 'run python backend/scripts/pipeline_v1.py'",
    '"$SWARM_BACKEND_BIN" py pipeline-v1.py',
])
def test_a_mention_that_does_not_run_the_cli_is_foreign(command):
    calls, _ = pc.calls(command)
    assert all(c["tool"] == "foreign" for c in calls), calls


def test_program_form_ownership(monkeypatch, tmp_path):
    call, _ = _one(f"SWARM_BACKEND_BIN=/tmp/evil {PROG} status --run-id run_a")
    assert call["assigns"] == ["SWARM_BACKEND_BIN"] and not pc.is_ours(call)
    exe = tmp_path / "rt" / "python-backend"
    exe.parent.mkdir()
    exe.write_text("")
    from utils import backend_bin as bb
    monkeypatch.setattr(bb, "backend_bin", lambda: str(exe))
    call, _ = _one(f"{exe} pipeline-v1 status --run-id run_a")
    assert call["form"] == "program" and pc.is_ours(call)
    other = tmp_path / "python-backend"
    other.write_text("")
    call, _ = _one(f"{other} pipeline-v1 status --run-id run_a")
    assert not pc.is_ours(call)


def test_watchers_see_the_program_form():
    from core.pipeline_stage_events import is_run_create, parse_watched_run_id
    assert is_run_create(f"{PROG} create --profile direct --requirement 'x'")
    assert not is_run_create(f"{PROG} status --run-id run_ab")
    assert not is_run_create(f"{PROG} create --requirement x; {PROG} status --run-id run_ab")
    assert parse_watched_run_id(f"{PROG} status --project P --run-id run_ab12") == "run_ab12"
    assert pc.is_create("python backend/scripts/pipeline_v1.py create --requirement x")
    assert pc.is_land(f"{PROG} land --project P --run-id run_a --message m")
    assert pc.is_land(f"{PROG} run-land --request q --run-id run_a")
    assert not pc.is_land(f"{PROG} status --run-id run_a")
    from hooks.daily_activity_hook import _writes_commits
    assert _writes_commits(f"{PROG} land --run-id run_a --message m")
    assert _writes_commits("git commit -m x") and not _writes_commits("git log")


def test_the_agent_is_told_the_program_form():
    from core.worktree_write_gate import v1_command
    assert v1_command("status --run-id run_a") == f"{PROG} status --run-id run_a"
    from routers.pipelines import continue_prompt
    assert f"`{PROG} status --project P --run-id run_a`" in continue_prompt("run_a", "P")


def test_the_session_link_reader_includes_the_program():
    from core.security_hooks import _is_link_reader_program
    assert _is_link_reader_program(["/r/2.4.0/python-backend", "pipeline-v1", "status"])
    assert _is_link_reader_program(["$SWARM_BACKEND_BIN", "pipeline-v1", "status"])


_PRODUCT = Path(__file__).resolve().parents[1]
_MATCH_CONTEXT = re.compile(r"re\.compile|\bin\s+(?:command|cmd|text)\b|startswith|endswith"
                            r"|\.search\(|\.match\(|\.fullmatch\(|\.findall\(")
_CLI_WORD = re.compile(r"pipeline_v1|pipeline-v1")


def test_no_other_module_matches_pipeline_command_text():
    offenders = []
    for path in sorted(_PRODUCT.glob("**/*.py")):
        rel = path.relative_to(_PRODUCT).as_posix()
        if rel.startswith((".venv/", "tests/", "scripts/", "skills/")) or "/tests/" in rel \
                or rel == "core/pipeline_command.py":
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            code = line.split("#", 1)[0]
            if _CLI_WORD.search(code) and _MATCH_CONTEXT.search(code):
                offenders.append(f"{rel}:{n}: {line.strip()[:120]}")
    assert not offenders, "\n".join(offenders)
