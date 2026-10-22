"""Pipeline v1 safe-probe tests (v13 §A2.5, §D4).

Methodology: enumerate every allowed and explicitly forbidden option plus structural
attack shapes. No command executes. Tests lock argv-only execution, path confinement,
sanitary environment/resource caps and pass/fail/error result semantics.
"""
from __future__ import annotations

import os

import pytest

from core.run_protocol import probe


def grep(*before, paths=("backend/",), expect=None):
    argv = ["git", "grep", *before, "--", *paths]
    return probe.validate(argv, {"rc": 0, "stdout": "nonempty"} if expect is None else expect)


def ls(*before, paths=("backend/",), expect=None):
    argv = ["git", "ls-files", *before, "--", *paths]
    return probe.validate(argv, {"rc": 0, "lines": 1} if expect is None else expect)


@pytest.mark.parametrize("flag", sorted(probe.GREP_FLAGS - {"-e"}))
def test_every_allowed_grep_flag(flag):
    out = grep(flag, "-e", "needle")
    assert out.subcommand == "grep" and flag in out.options


@pytest.mark.parametrize("flag", sorted(probe.LS_FLAGS))
def test_every_allowed_ls_files_flag(flag):
    assert ls(flag).subcommand == "ls-files"


def test_grep_supports_multiple_explicit_patterns():
    out = grep("-n", "-e", "one", "-e", "two")
    assert out.patterns == ("one", "two")


@pytest.mark.parametrize("argv", [
    ["grep", "x", "backend"],
    ["git", "-c", "core.pager=cat", "grep", "-e", "x", "--", "backend"],
    ["git", "status", "--", "backend"],
    ["env", "git", "grep", "-e", "x", "--", "backend"],
    ["sh", "-c", "git grep x"],
])
def test_only_exact_git_subcommands_without_wrappers_or_global_options(argv):
    with pytest.raises(probe.ProbeError, match="only git grep"):
        probe.validate(argv, {"rc": 0, "stdout": "empty"})


@pytest.mark.parametrize("flag", sorted(probe.FORBIDDEN))
def test_every_explicitly_forbidden_option(flag):
    with pytest.raises(probe.ProbeError, match="forbidden"):
        grep(flag, "-e", "x")


@pytest.mark.parametrize("argv,part", [
    (["git", "grep", "-e", "x", "backend"], "separator"),
    (["git", "grep", "-e", "x", "--"], "pathspec"),
    (["git", "grep", "x", "--", "backend"], "not allowed"),
    (["git", "grep", "-e", "--", "backend"], "requires a pattern"),
    (["git", "ls-files", "-z", "--", "backend"], "not allowed"),
    (["git", "grep", "-e", "x", "-O", "--", "backend"], "forbidden"),
])
def test_command_shape_errors(argv, part):
    with pytest.raises(probe.ProbeError, match=part):
        probe.validate(argv, {"rc": 0, "stdout": "empty"})


@pytest.mark.parametrize("path", [
    "/etc/passwd", "../secret", "backend/../../secret", ":(glob)**", "C:\\secret",
    "a\x00b",
])
def test_pathspecs_are_repo_relative_without_traversal_or_magic(path):
    with pytest.raises(probe.ProbeError, match="pathspec"):
        grep("-e", "x", paths=(path,))


@pytest.mark.parametrize("expect", [
    {}, {"rc": 2, "stdout": "empty"}, {"rc": True, "stdout": "empty"},
    {"rc": 0}, {"rc": 0, "stdout": "empty", "lines": 0},
    {"rc": 0, "stdout": "some"}, {"rc": 0, "lines": -1},
    {"rc": 0, "stdout": "empty", "extra": True},
])
def test_expect_shape_is_exact(expect):
    with pytest.raises(probe.ProbeError):
        grep("-e", "x", expect=expect)


def test_expect_accepts_rc_zero_or_one_and_stdout_or_lines():
    assert grep("-e", "x", expect={"rc": 1, "stdout": "empty"}).expect_rc == 1
    assert ls(expect={"rc": 0, "lines": 0}).expect_lines == 0



def test_grep_pattern_rejects_nul():
    with pytest.raises(probe.ProbeError, match="NUL-free"):
        grep("-e", "a\x00b")

def test_execution_spec_has_only_the_frozen_environment_and_caps():
    spec = probe.execution_spec(
        grep("-I", "-e", "x"), repo_root="/work/repo", allowed_repo_roots=["/work/repo"], temp_home="/tmp/probe-home", temp_root="/tmp",
    )
    assert spec["argv"][:3] == ["git", "grep", "-I"]
    assert spec["argv"][-2:] == ["--", ":(literal)backend/"]
    assert spec["cwd"] == "/work/repo"
    assert spec["env"] == {
        "PATH": probe.TRUSTED_PATH, "HOME": os.path.realpath("/tmp/probe-home"),
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_PAGER": "cat", "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C",
    }
    assert spec["shell"] is False and spec["stdin"] == "/dev/null"
    assert spec["timeout_seconds"] == 30
    assert spec["stdout_limit_bytes"] == 256 * 1024


def test_each_path_is_encoded_independently_as_literal_or_star_glob():
    spec = probe.execution_spec(
        grep("-e", "x", paths=("backend/plain.py", "backend/*.py")),
        repo_root="/work/repo", allowed_repo_roots=["/work/repo"], temp_home="/tmp/h", temp_root="/tmp",
    )
    assert spec["argv"][-2:] == [":(literal)backend/plain.py", ":(glob)backend/*.py"]


def test_question_and_bracket_pathspec_metacharacters_are_rejected():
    for path in ("backend/a?.py", "backend/[x].py"):
        with pytest.raises(probe.ProbeError, match="only "):
            grep("-e", "x", paths=(path,))


@pytest.mark.parametrize("repo_root,temp_home", [
    ("/other/repo", "/tmp/h"), ("/work/repo", "/outside/h"),
])
def test_execution_spec_confines_repo_and_temp_home(repo_root, temp_home):
    with pytest.raises(probe.ProbeError):
        probe.execution_spec(grep("-e", "x"), repo_root=repo_root,
                             allowed_repo_roots=["/work/repo"],
                             temp_home=temp_home, temp_root="/tmp")



def test_execution_spec_requires_absolute_repo_root():
    with pytest.raises(probe.ProbeError, match="repo_root"):
        probe.execution_spec(grep("-e", "x"), repo_root="relative", allowed_repo_roots=["/work/repo"],
                             temp_home="/tmp/h", temp_root="/tmp")

@pytest.mark.parametrize("expect,rc,stdout", [
    ({"rc": 0, "stdout": "empty"}, 0, b""),
    ({"rc": 1, "stdout": "empty"}, 1, b""),
    ({"rc": 0, "stdout": "nonempty"}, 0, b"hit\n"),
    ({"rc": 0, "lines": 2}, 0, b"a\nb\n"),
])
def test_matching_result_passes(expect, rc, stdout):
    p = grep("-e", "x", expect=expect)
    assert probe.evaluate_result(p, rc=rc, stdout=stdout)["result"] == "pass"


@pytest.mark.parametrize("expect,rc,stdout", [
    ({"rc": 0, "stdout": "empty"}, 1, b""),
    ({"rc": 0, "stdout": "empty"}, 0, b"hit"),
    ({"rc": 0, "stdout": "nonempty"}, 0, b""),
    ({"rc": 0, "lines": 2}, 0, b"one\n"),
])
def test_non_matching_result_fails(expect, rc, stdout):
    p = grep("-e", "x", expect=expect)
    assert probe.evaluate_result(p, rc=rc, stdout=stdout)["result"] == "fail"


@pytest.mark.parametrize("kwargs,part", [
    ({"rc": None, "stdout": b""}, "no exit"),
    ({"rc": 2, "stdout": b""}, "exited 2"),
    ({"rc": 0, "stdout": b"", "timed_out": True}, "timed out"),
    ({"rc": 0, "stdout": b"", "overflow": True}, "256 KB"),
    ({"rc": 0, "stdout": b"", "tree_changed": True}, "code_tree"),
])
def test_execution_faults_are_errors(kwargs, part):
    result = probe.evaluate_result(grep("-e", "x"), **kwargs)
    assert result["result"] == "error" and part in result["reason"]


def test_result_rejects_text_stdout():
    with pytest.raises(probe.ProbeError, match="bytes"):
        probe.evaluate_result(grep("-e", "x"), rc=0, stdout="")
