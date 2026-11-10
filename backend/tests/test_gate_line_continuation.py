"""A backslash-newline continues a Bash command line, for every Bash judge.

What is tested: bash removes an unquoted backslash-newline, so ``git -C /x \\`` +
newline + ``push --force`` is ONE command. The splitters the gates share used to keep
it as a separator or a stray token, and that hid the subcommand: the irreversible
external op, the discard, the reserved push / PR, the glob list and the commit check
all missed a continued command. Methodology: each rule's own predicate, on a
continued form of a command it catches on one line. Kept as a separator (bash does
not join there): inside single quotes, after an unquoted ``#`` (it may end a comment,
so the next line runs as its own command). Bash's own reading is the oracle: a
continued ``echo`` whose next line reads like a push is one echo command.
"""
from __future__ import annotations

import pytest

from core import security_hooks as sh
from core.shell_segments import join_continuations, split_segments

BS = "\\\n"
CWD = "/Users/u/repo"


def _rule(cmd: str):
    hit = sh._danger_match(sh._command_as_run(cmd), list(sh.DEFAULT_DANGEROUS_PATTERNS), CWD)
    return hit and hit["rule"]


@pytest.mark.parametrize("cmd,rule", [
    (f"git -C /x {BS}  push --force origin main", "irreversible_external"),
    (f"git push {BS}  --force origin main", "irreversible_external"),
    (f"gh repo {BS} delete o/r --yes", "irreversible_external"),
    (f"git pu{BS}sh -f origin main", "irreversible_external"),       # a word split in two
    (f"rm {BS} -rf ~/x", "rm_unresolved_target"),
    (f"mv {BS}  ~/.swarm-ai/SwarmWS/Projects/CMHK /tmp/o", "irreplaceable_store"),
    (f"kill {BS}-9 1", "pattern:kill -9 *"),
    (f"sudo {BS}ls", "pattern:sudo *"),
    # after a comment the next line is its own command: still caught
    (f"echo x # note {BS}git push -f origin main", "irreversible_external"),
    # a `#` inside a word starts no comment: bash joins, and so does the gate; and a
    # `#` before a continuation never hides what follows it (review BLOCKER 1)
    (f"git -C a#b {BS}push --force origin main", "irreversible_external"),
    (f"echo $# {BS}&& git -C /x {BS}push --force origin main", "irreversible_external"),
    (f"echo $# {BS}; rm -rf ~/x", "rm_unresolved_target"),
    (f"rm a#b {BS}-rf ~/x", "rm_unresolved_target"),
    (f"cat <<'EOF'\nfoo {BS}EOF\ngit push -f origin main", "irreversible_external"),
])
def test_a_continued_command_is_caught_by_its_rule(cmd, rule):
    assert _rule(cmd) == rule


def test_a_continued_discard_is_a_discard_in_its_directory():
    cmd = f"git -C /x {BS}  reset --hard"
    assert sh._discards_work(cmd)
    assert sh._discard_dirs(cmd, CWD) == ["/x"]


@pytest.mark.parametrize("cmd,action", [
    (f"git {BS} push origin x", "push"),
    (f"gh pr {BS} create", "open_pr"),
])
def test_a_continued_reserved_action_is_seen(cmd, action):
    assert sh._is_reserved_action(cmd, ["push", "open_pr"]) == action


def test_a_hash_before_a_continued_discard_does_not_hide_it():
    assert sh._discards_work(f"git -C a#b {BS}reset --hard")


def test_a_continued_commit_is_a_commit():
    assert sh._command_has_git_commit(f"git {BS}  commit -m x")


def test_bash_reads_a_continued_echo_as_one_echo():
    # `echo hi git push -f origin main`: nothing is pushed (the one prompt this removes)
    assert _rule(f"echo hi {BS}git push -f origin main") is None
    assert _rule("echo hi\ngit push -f origin main") == "irreversible_external"


def test_join_continuations_keeps_quotes_and_comments_apart():
    assert join_continuations(f"a {BS}b") == "a b"
    assert join_continuations(f"a # c {BS}d") == f"a # c {BS}d"
    assert join_continuations(f"a # c {BS}d {BS}e") == f"a # c {BS}d e"   # comment ended
    assert join_continuations(f"a#b {BS}c") == "a#b c"                    # not a comment
    assert join_continuations(f"'x {BS}y'") == f"'x {BS}y'"
    assert join_continuations(f"$'x {BS}y'") == f"$'x {BS}y'"
    assert join_continuations(f'"x {BS}y"') == f'"x {BS}y"'
    # an escaped backslash before a newline does not continue; one more backslash does
    assert join_continuations("a \\\\\nb") == "a \\\\\nb"
    assert join_continuations(f"a \\\\{BS}b") == "a \\\\b"
    assert join_continuations("plain\nlines") == "plain\nlines"


def test_split_segments_reads_a_continued_line_as_one_segment():
    assert split_segments(f"git -C /x {BS}  push --force") == [["git", "-C", "/x", "push", "--force"]]
    assert split_segments(f"a # c {BS}git push") == [["a", "#", "c", " "], ["git", "push"]]
