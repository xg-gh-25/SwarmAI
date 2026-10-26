"""The worktree gate's heredoc refusal says what it read and how to get through (design
2026-10-26-pipeline-review-closure-and-visibility §5.2 point 3, step 12).

Before: a heredoc handed to an interpreter was refused when any path-like string in it
resolved into the main checkout, with a message that named neither the string nor a way
through; the agent could only guess. The refusal itself is unchanged.

What is tested: the refusal names the string read as a target and the two ways through;
each way through is then allowed; a plain redirect into the main checkout is refused
with no heredoc hint.
"""
from __future__ import annotations

from core import worktree_write_gate as wg
from tests.test_worktree_gates import (  # noqa: F401 (fixtures)
    _call, _denied, _projection_is_the_signed_view, env)

HEREDOC = ("python3 - <<'EOF'\nimport json\n"
           "json.dump({'path': 'backend/tests/test_x.py'}, open('/tmp/out.json', 'w'))\nEOF")


def _reason(v) -> str:
    return v["hookSpecificOutput"]["permissionDecisionReason"]


def test_the_refusal_names_the_string_and_the_ways_through(env):
    v = wg.judge(_call("Bash", cwd=str(env["main"]), command=HEREDOC), env["own"])
    assert _denied(v), v
    reason = _reason(v)
    assert "backend/tests/test_x.py" in reason and "heredoc" in reason, reason
    assert "python3 /tmp/" in reason and "cd " in reason, reason


def test_each_way_through_is_allowed(env):
    script = wg.judge(_call("Bash", cwd=str(env["main"]), command="python3 /tmp/x.py"),
                      env["own"])
    assert not _denied(script), script
    moved = wg.judge(_call("Bash", cwd="/tmp", command=HEREDOC), env["own"])
    assert not _denied(moved), moved


def test_a_plain_redirect_gets_no_heredoc_hint(env):
    v = wg.judge(_call("Bash", cwd=str(env["main"]), command="echo x > a.py"), env["own"])
    assert _denied(v) and "heredoc" not in _reason(v), v
