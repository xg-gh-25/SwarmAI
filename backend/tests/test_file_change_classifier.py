"""Tests for file_change_classifier — the Bash DELETE-target parser.

- parse_bash_delete_targets(command) → list[str]
  CONSERVATIVE, under-match HARD (a MISSED delete leaves a stale rail row; a FALSE
  delete removes a live row — strictly worse). Reject anything ambiguous.

(classify_relevance retired run_4de279ca; parse_bash_write_targets retired
run_a18d69f5 — surfacing is git-based via needs_human_review + the turn-end sweep.)
"""
import pytest

from core.file_change_classifier import parse_bash_delete_targets


# ─────────────────────────── parse_bash_delete_targets (G1, run_5a7be540) ───────
# Safe-direction (under-match HARD): a MISSED delete just leaves a stale rail row
# (current behavior, no regression); a FALSE delete removes a live file from the
# rail — strictly worse. So reject anything ambiguous.

@pytest.mark.parametrize("cmd,expected", [
    ("rm old.txt", ["old.txt"]),
    ("rm -f stale.md", ["stale.md"]),
    ("rm a.txt b.txt", ["a.txt", "b.txt"]),   # plain multi-file rm
    ("mv draft.md final.md", ["draft.md"]),   # SRC of a rename is "deleted" (DEST caught as write)
])
def test_parses_delete_targets(cmd, expected):
    assert parse_bash_delete_targets(cmd) == expected


@pytest.mark.parametrize("cmd", [
    "git rm --cached foo.py",   # index metadata, NOT a disk delete
    "npm rm left-pad",          # package manager, not a file
    "rm -rf build/",            # recursive DIR delete — can't enumerate, reject
    "rm -r somedir",            # recursive — reject
    "echo 'rm old.txt'",        # rm inside a quoted string, not a command
    "cat rmfile.txt",           # 'rm' is a substring of a word, not the command
    "rm *.tmp",                 # glob — can't know the real files, reject
    "trash old.txt",            # not rm
    "cat foo.txt",              # no delete at all
    "",                         # empty
])
def test_no_false_delete_targets(cmd):
    assert parse_bash_delete_targets(cmd) == []


def test_mv_multi_source_or_dir_dest_rejected():
    # mv with >2 args (multi-source or -t) or a dir dest is ambiguous → under-match.
    assert parse_bash_delete_targets("mv a b c/") == []
    assert parse_bash_delete_targets("mv -t dir a b") == []


# classify_relevance tests REMOVED (run_4de279ca Gate-2 F7): the function was
# retired — the git-based needs_human_review is now the sole surfacing authority
# (its allowlist + bookkeeping behavior is covered by needs_human_review_test.py,
# incl. test_memory_md_is_knowledge + test_artifacts_report / .context cases).


def test_chained_sed_in_place_never_reads_the_next_command_as_a_file():
    """`sed -i '' ... f; sed -i '' ... g` — the `;` glued to f hid the separator, so
    the second `sed` was read as an edited file (and the worktree gate denied a write
    to <main>/sed)."""
    from core.file_change_classifier import parse_bash_write_targets
    cmd = ("sed -i '' 's|a|b|' /x/wt/f.py; sed -i '' 's|c|d|' /x/wt/g.md; "
           "grep -rn x /x/wt/f.py && sed -i 's/e/f/' /x/wt/h.txt")
    assert parse_bash_write_targets(cmd) == ["/x/wt/f.py", "/x/wt/g.md", "/x/wt/h.txt"]


def test_redirect_operands_are_not_file_arguments():
    from core.file_change_classifier import parse_bash_write_targets
    assert parse_bash_write_targets("sed -i 's/a/b/' f.py 2>&1") == ["f.py"]
    assert parse_bash_write_targets("sed -i 's/a/b/' f.py 2>/dev/null") == ["f.py"]


# ── 2026-10-21: a cp/mv/rm argument list ends at the segment's separator ──
def test_cp_dest_and_rm_args_stop_at_the_next_separator():
    from core.file_change_classifier import parse_bash_delete_targets, parse_bash_write_targets
    # sed's script `1,80p` was read as cp's destination → worktree-gate false deny
    assert parse_bash_write_targets("cp a b; grep x f | sed -n 1,80p") == ["b"]
    assert parse_bash_write_targets("cp a b | cat") == ["b"]
    assert parse_bash_write_targets("cp -r src dst && echo ok") == ["dst"]
    # `rm a; ls b c` reported ls, b and c as deleted
    assert parse_bash_delete_targets("rm a; ls b c") == ["a"]
    assert parse_bash_delete_targets("mv a b; grep x f") == ["a"]
    assert parse_bash_delete_targets("rm -f x.log 2>/dev/null; echo") == ["x.log"]
