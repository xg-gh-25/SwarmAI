"""rerun_nudge: a slow run repeated after its output was filtered gets one reminder,
and the reminder never claims the earlier result still holds."""

from unittest.mock import MagicMock

import pytest

from core.rerun_nudge import NUDGE, create_rerun_nudge_hook, parse_run

TEST = "cd backend && perl -e 'alarm 100; exec @ARGV' python -m pytest tests/test_a.py -q"


def bash(cmd, ms=5000, cwd="/repo", **resp):
    return {"tool_name": "Bash", "tool_input": {"command": cmd}, "duration_ms": ms,
            "cwd": cwd, "tool_response": {"stdout": "", "stderr": "", "interrupted": False, **resp}}


def failed(cmd, ms=5000, cwd="/repo", **extra):
    return {"tool_name": "Bash", "tool_input": {"command": cmd}, "duration_ms": ms,
            "cwd": cwd, "error": "Exit code 1", "is_interrupt": False, **extra}


async def feed(hook, *calls):
    out = []
    for c in calls:
        r = await hook(c, "t", None)
        out.append((r.get("hookSpecificOutput") or {}).get("additionalContext"))
    return out


def test_the_reminder_makes_no_claim_about_state():
    assert "still holds" not in NUDGE and "unnecessary" not in NUDGE
    assert "> <log> 2>&1" in NUDGE


def test_output_handling_and_cap_seconds_do_not_make_a_different_run():
    key, filtered = parse_run(TEST + " 2>&1 | tail -25")
    assert filtered
    assert parse_run(TEST.replace("alarm 100", "alarm 280") + " 2>&1 | grep FAIL") == (key, True)
    assert parse_run(TEST + " > /tmp/x.log 2>&1; echo exit=$?") == (key, False)
    assert parse_run(TEST)[0] == key
    assert parse_run(TEST.replace("test_a.py", "test_b.py"))[0] != key
    assert parse_run(TEST + " -v")[0] != key


def test_a_pipe_stage_that_does_work_stays_in_the_key():
    assert parse_run("git ls-files | xargs ruff check")[0] != parse_run("git ls-files | xargs mypy")[0]
    assert parse_run("cat in.json | python a.py")[0] != parse_run("cat in.json | python b.py")[0]
    assert parse_run("git ls-files | xargs mypy | tail -3") == (parse_run("git ls-files | xargs mypy")[0], True)


def test_heredoc_and_multiline_bodies_do_not_split_the_key():
    a = "python3 - <<'EOF'\nprint(1); x = 'a|b'\nEOF"
    b = "python3 - <<'EOF'\nprint(2); x = 'a|b'\nEOF"
    ka, kb = parse_run(a)[0], parse_run(b)[0]
    assert ka.startswith("python3 - <<heredoc:") and "|" not in ka and ka != kb
    assert parse_run(a) == parse_run(a)
    assert parse_run("ls\npython gen.py | tail")[0] == "ls && python gen.py"


def test_a_pipe_on_the_heredoc_opener_line_still_counts():
    cmd = "python3 - <<EOF 2>&1 | tail -20\nprint(1)\nEOF"
    key, filtered = parse_run(cmd)
    assert filtered and key.startswith("python3 - <<heredoc:")
    leaky = "cat <<EOF\nEOF is here | x\nreal\nEOF"
    assert "real" not in parse_run(leaky)[0]


def test_tee_keeps_the_output_and_cat_cuts_nothing():
    assert parse_run("pytest 2>&1 | tee /tmp/t.log | tail -40")[1] is False
    assert parse_run("pytest | cat")[1] is False


def test_other_redirect_and_pipe_forms():
    assert parse_run("pytest &> /tmp/l")[0] == "pytest"
    assert parse_run("pytest |& tail") == ("pytest", True)


@pytest.mark.asyncio
async def test_repeat_after_a_filtered_run_is_reported():
    hook = create_rerun_nudge_hook({})
    out = await feed(hook, bash(TEST + " 2>&1 | tail -5"), bash("grep -n x f.py"),
                     bash(TEST + " 2>&1 | grep FAIL"))
    assert out == [None, None, NUDGE]


@pytest.mark.asyncio
async def test_repeat_after_a_run_kept_in_a_file_is_not_reported():
    hook = create_rerun_nudge_hook({})
    run = bash(TEST + " > /tmp/a.log 2>&1; echo exit=$?")
    assert await feed(hook, run, bash("grep -n FAILED /tmp/a.log", ms=20), run) == [None, None, None]


@pytest.mark.asyncio
async def test_a_repeat_that_now_keeps_its_output_gets_no_reminder():
    hook = create_rerun_nudge_hook({})
    out = await feed(hook, bash(TEST + " | tail"), bash(TEST + " > /tmp/a.log 2>&1"))
    assert out == [None, None]


@pytest.mark.asyncio
async def test_a_failing_run_whose_output_mentions_a_timeout_is_still_recorded():
    ctx: dict = {}
    bad = create_rerun_nudge_hook(ctx, "PostToolUseFailure")
    first = failed(TEST + " | tail")
    first["error"] = "Exit code 1\nE   asyncio.TimeoutError: timed out after 5s"
    await bad(first, "t", None)
    assert (await feed(create_rerun_nudge_hook(ctx), bash(TEST + " | tail")))[0] == NUDGE


@pytest.mark.asyncio
async def test_a_failing_run_counts_through_the_failure_event():
    ctx: dict = {}
    ok, bad = create_rerun_nudge_hook(ctx), create_rerun_nudge_hook(ctx, "PostToolUseFailure")
    assert (await feed(bad, failed(TEST + " | tail -20")))[0] is None
    r = await ok(bash(TEST + " | grep FAIL"), "t", None)
    assert r["hookSpecificOutput"]["additionalContext"] == NUDGE
    r = await bad(failed(TEST + " | tail"), "t", None)
    assert r["hookSpecificOutput"] == {"hookEventName": "PostToolUseFailure",
                                       "additionalContext": NUDGE}


@pytest.mark.asyncio
@pytest.mark.parametrize("first", [
    bash(TEST + " | tail", ms=300),
    bash(TEST + " | tail", stderr="Alarm clock"),
    bash(TEST + " | tail", interrupted=True),
    bash(TEST + " | tail", backgroundTaskId="b1"),
    bash(TEST + " | tail", timedOutAfterMs=120000),
    failed(TEST + " | tail", is_interrupt=True),
    bash(TEST + " | tail", cwd="/other"),
])
async def test_fast_unfinished_or_elsewhere_runs_are_not_a_repeat(first):
    hook = create_rerun_nudge_hook({})
    assert (await feed(hook, first, bash(TEST)))[-1] is None


@pytest.mark.asyncio
async def test_fails_open_without_injecting():
    hook = create_rerun_nudge_hook({"_rerun_nudge": "broken"})
    assert await hook(bash(TEST), "t", None) == {}


@pytest.mark.asyncio
async def test_build_hooks_registers_both_events_on_bash():
    from core.hook_builder import build_hooks

    pm = MagicMock()
    pm.is_command_approved = MagicMock(return_value=False)
    hooks, _, _ = await build_hooks(
        agent_config={"enable_tool_logging": False, "global_user_mode": True},
        enable_skills=False, enable_mcp=False, resume_session_id=None,
        session_context={"sdk_session_id": "s"}, permission_manager=pm,
    )

    async def texts(event, call):
        got = []
        for m in hooks[event]:
            if m.matcher and "Bash" in m.matcher.split("|"):
                for fn in m.hooks:
                    r = await fn({"hook_event_name": event, **call}, "t", None)
                    got.append(((r or {}).get("hookSpecificOutput") or {}).get("additionalContext") or "")
        return got

    await texts("PostToolUseFailure", failed(TEST + " | tail"))
    assert any(NUDGE in t for t in await texts("PostToolUse", bash(TEST + " | tail")))
