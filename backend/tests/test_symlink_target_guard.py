"""symlink_target_guard: an `ln` whose destination is already a symlink to a directory.

Questions these tests answer: does the guard deny the shape that wrote a self-link into
a shared node_modules (relative destination, through `VAR=…; cd $VAR/x && ln -s …`, and
`ln -sf`), and does it let through everything that does not write into a linked
directory: `ln -sfn`/`-h`/`-T`, a destination that does not exist, a real directory as
destination, `-t DIR`, three operands, an unresolvable destination, other commands.
Real files in tmp_path; the hook is called the way the SDK calls it.
"""
import asyncio
import os

import pytest

from core.symlink_target_guard import link_into_dir, symlink_target_guard


@pytest.fixture
def tree(tmp_path):
    main = tmp_path / "main" / "desktop" / "node_modules"
    main.mkdir(parents=True)
    wt = tmp_path / "wt" / "desktop"
    wt.mkdir(parents=True)
    os.symlink(main, wt / "node_modules")          # what the worktree tool creates
    (tmp_path / "realdir").mkdir()
    return tmp_path, main, wt


def _deny(command, cwd):
    r = asyncio.run(symlink_target_guard(
        {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)}, None, None))
    return r.get("hookSpecificOutput", {}).get("permissionDecision") == "deny", r


def test_relinking_a_linked_directory_is_denied_and_explained(tree):
    tmp, main, wt = tree
    denied, r = _deny(f"ln -s {main} node_modules", wt)
    assert denied
    reason = r["hookSpecificOutput"]["permissionDecisionReason"]
    assert "ln -sfn" in reason and str(main) in reason
    assert not (main / "node_modules").exists()     # the guard only reads


def test_the_recorded_incident_shape_is_followed_through_a_variable_and_cd(tree):
    tmp, main, wt = tree
    cmd = (f"W={tmp}/wt; cd $W/desktop && ln -s {main} node_modules && ls src | head")
    assert _deny(cmd, tmp / "elsewhere")[0]
    assert link_into_dir(f"cd {wt} && ln -sf {main} node_modules/", "") == (
        str(wt / "node_modules"), os.path.realpath(main))


@pytest.mark.parametrize("cmd", [
    "ln -sfn {main} node_modules",
    "ln -sh {main} node_modules",
    "ln -sT {main} node_modules",
    "ln -s --no-dereference {main} node_modules",
    "ln -s {main} fresh_link",
    "ln -s {main} {tmp}/realdir",
    "ln -s -t node_modules {main}",
    "ln -s {main} {tmp}/realdir/a node_modules",
    "ln -s {main} $UNKNOWN/node_modules",
    "ls -d node_modules || echo ln -s missing",
    "cd {tmp}/wt; ln -s {main} desktop/x",
])
def test_everything_that_does_not_write_into_a_linked_directory_passes(tree, cmd):
    tmp, main, wt = tree
    assert not _deny(cmd.format(main=main, tmp=tmp), wt)[0]


def test_after_a_semicolon_a_failed_cd_is_not_assumed(tree):
    """`cd X; ln …` may run in the old directory: the destination is unknown there."""
    tmp, main, wt = tree
    assert not _deny(f"cd {wt}; ln -s {main} node_modules", tmp / "nowhere")[0]
    assert _deny(f"cd {wt} && ln -s {main} node_modules", tmp / "nowhere")[0]


def test_other_tools_and_unparseable_commands_pass(tree):
    tmp, main, wt = tree
    r = asyncio.run(symlink_target_guard({"tool_name": "Read", "tool_input": {}}, None, None))
    assert r == {"decision": "approve"}
    assert not _deny("ln -s 'unterminated", wt)[0]
