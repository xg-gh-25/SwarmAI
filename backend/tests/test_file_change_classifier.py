"""Tests for file_change_classifier — the unified Canvas file-change decision layer.

Cycle 2 of run_e626e121. Two pure functions, no I/O:

- classify_relevance(path, operation) → 'deliverable' | 'incidental' | 'bookkeeping'
  WHITELIST semantics (directive #3 "别什么都 trigger 成噪音"): only a WRITE to a
  real deliverable auto-surfaces; reads/greps/lists are incidental (rail only);
  .artifacts/.git/.context/dotfiles/tmp are bookkeeping (filtered entirely).

- parse_bash_write_targets(command) → list[str]
  CONSERVATIVE (directive: a missed deliverable ≫ a false pop). Catches the common
  redirection/copy shapes; deliberately UNDER-matches exotic shells (heredoc bodies,
  command substitution, nested subshells) rather than risk a false Canvas pop.
"""
import pytest

from core.file_change_classifier import classify_relevance, parse_bash_write_targets


# ─────────────────────────── classify_relevance ───────────────────────────

@pytest.mark.parametrize("path", [
    "Projects/SwarmAI/report.html",
    "Knowledge/Notes/foo.md",
    "/Users/gawan/Desktop/thing.html",
    "desktop/src/App.tsx",
])
def test_write_to_real_file_is_deliverable(path):
    assert classify_relevance(path, "written") == "deliverable"


@pytest.mark.parametrize("path", [
    "Projects/SwarmAI/.artifacts/runs/run_x/REPORT.md",  # .artifacts anywhere
    "Projects/SwarmAI/.git/config",                       # .git
    ".context/USER.md",                                   # .context
    "Knowledge/.DS_Store",                                # dotfile basename
    "/tmp/scratch.txt",                                   # tmp
    "/private/tmp/x",                                     # macos tmp
    "foo.py.tmp",                                         # .tmp suffix
    "backup~",                                            # ~ backup
])
def test_bookkeeping_paths_filtered(path):
    assert classify_relevance(path, "written") == "bookkeeping"


@pytest.mark.parametrize("op", ["read", "searched", "listed"])
def test_reads_and_searches_are_incidental(op):
    # A real file, but only READ → rail-only, never auto-surface.
    assert classify_relevance("Projects/SwarmAI/report.html", op) == "incidental"


def test_bookkeeping_beats_incidental_and_deliverable():
    # A read of a bookkeeping path is still bookkeeping (filtered), not incidental.
    assert classify_relevance("Projects/SwarmAI/.git/HEAD", "read") == "bookkeeping"


# ─────────────────────────── parse_bash_write_targets ───────────────────────────

@pytest.mark.parametrize("cmd,expected", [
    ("cat > report.html", ["report.html"]),
    ("echo hi >> log.txt", ["log.txt"]),
    ("python gen.py > out.html", ["out.html"]),
    ("some_cmd | tee result.md", ["result.md"]),
    ("tee -a appended.txt", ["appended.txt"]),
    ("cp src.html dst.html", ["dst.html"]),
    ("mv old.md new.md", ["new.md"]),
    ("cat x>y", ["y"]),                       # no space before >
])
def test_parses_common_write_targets(cmd, expected):
    assert parse_bash_write_targets(cmd) == expected


@pytest.mark.parametrize("cmd", [
    "echo x > /dev/null",       # /dev/null is not a deliverable
    "grep -r foo . 2>&1",       # stderr redirect, not a file write
    "ls -la > /dev/null 2>&1",  # both discarded
    "cat foo.txt",              # pure read, no redirect
    "python script.py",         # internal write invisible to shell (documented gap)
    "echo 'a > b'",             # '>' inside a quoted string, not a redirect
    "diff a.py b.py",           # read-only
])
def test_no_false_write_targets(cmd):
    # Conservative: these must yield NO targets (a false pop is worse than a miss).
    assert parse_bash_write_targets(cmd) == []


def test_dev_null_filtered_even_with_real_write():
    # A real write AND a /dev/null discard → only the real file.
    assert parse_bash_write_targets("gen > real.html 2> /dev/null") == ["real.html"]


def test_escaped_quote_does_not_hide_real_redirect():
    # Gate-2 HIGH: an escaped quote inside a double-quoted arg must NOT swallow the
    # real redirect that follows the true closing quote.
    assert parse_bash_write_targets('echo "a\\"b" > out.html') == ["out.html"]


def test_quoted_redirect_still_ignored():
    # And the inverse still holds: a '>' truly inside quotes is not a redirect.
    assert parse_bash_write_targets('echo "a > b"') == []


def test_empty_and_none_safe():
    assert parse_bash_write_targets("") == []
    assert parse_bash_write_targets("   ") == []
