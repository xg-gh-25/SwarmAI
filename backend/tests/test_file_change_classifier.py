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

from core.file_change_classifier import (
    parse_bash_write_targets,
    parse_bash_delete_targets,
)


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


# ── Layer 3 (run_6ebe2d09): _clean_target rejects shell-illegal filename tokens ──
# The Canvas showed `L4(top-right)` because a bare word after `>` was accepted as a
# file. An UNQUOTED redirect target cannot legally contain shell metacharacters
# (parens/space/glob/pipe/…) — bash would syntax-error. Reject them at the source.
@pytest.mark.parametrize("cmd", [
    "echo Ladder > L4(top-right)",   # parens — the real reported garbage
    "echo x > a*b",                  # glob
    "echo x > a{b",                  # brace
    "echo x > 'a b'",                # space (blanked by _blank_quoted, but guard too)
])
def test_rejects_shell_illegal_target_tokens(cmd):
    # These bare words can't be real unquoted redirect targets → no write target.
    # (NB: `> a|b` is NOT here — in bash that redirects to file `a` then pipes to
    # `b`, so `a` is a legit target; the regex correctly stops at `|`.)
    assert parse_bash_write_targets(cmd) == []


@pytest.mark.parametrize("cmd,expected", [
    ("cat > report.html", ["report.html"]),               # plain
    ("cat > my-file_v2.html", ["my-file_v2.html"]),       # dash + underscore
    ("cat > dir/sub.file.html", ["dir/sub.file.html"]),   # slashes + dots
    ("cat > 报告.html", ["报告.html"]),                    # CJK filename
    ("cat > Makefile", ["Makefile"]),                     # no extension, legit
    ("cat > .env", [".env"]),                             # leading dot
])
def test_legal_filenames_still_pass_layer3(cmd, expected):
    # Layer 3 must NOT over-reject: normal filename chars survive.
    assert parse_bash_write_targets(cmd) == expected


# ── Layer 2 (run_6ebe2d09): heredoc bodies are blanked (docstring already claims
# this — the code now makes it true). A `>` inside <<EOF..EOF is not a redirect. ──
@pytest.mark.parametrize("cmd", [
    "python3 - <<PY\nif a > L4: pass\nPY",          # unquoted delimiter
    "cat <<'EOF'\nx > y\nEOF",                        # quoted delimiter
    "cat <<-EOF\n\tif a > b: pass\n\tEOF",            # dash (tab-strip) form
])
def test_heredoc_body_redirect_not_a_write_target(cmd):
    # A `>` inside a heredoc body must NOT be read as a shell redirect.
    assert parse_bash_write_targets(cmd) == []


def test_heredoc_does_not_swallow_a_real_trailing_redirect():
    # Conservative safe-direction: if a REAL redirect follows the heredoc close,
    # we prefer to still catch it — but per design a MISS is acceptable, never a
    # false pop. This asserts the heredoc body's `>` is ignored AND, when the close
    # is found, a trailing real redirect is still seen.
    cmd = "cat <<EOF > real.html\nbody > notafile\nEOF"
    assert parse_bash_write_targets(cmd) == ["real.html"]


# ── Gate-2 findings (run_6ebe2d09 adversarial) — heredoc close-rule edge cases ──
def test_heredoc_nondash_indented_delimiter_stays_in_body():
    # Gate-2 F3: for a PLAIN `<<` heredoc, an INDENTED delimiter is NOT a close in
    # bash — it stays in the body. So the `>` after it is still inside the heredoc
    # and must NOT be extracted (was a false-positive when we used .strip()).
    cmd = "gen <<EOF\nline1\n\tEOF\necho done > report.html"
    assert parse_bash_write_targets(cmd) == []


def test_heredoc_dash_form_strips_leading_tabs_on_close():
    # The `<<-` form DOES strip leading tabs before matching the delimiter — a
    # tab-indented delimiter closes it, so the trailing redirect IS seen.
    cmd = "gen <<-EOF\n\tline1\n\tEOF\necho done > report.html"
    assert parse_bash_write_targets(cmd) == ["report.html"]


def test_herestring_is_not_a_heredoc():
    # Gate-2 F4: `<<<` is a herestring (single-line input), NOT a heredoc body — the
    # opener regex must not match the trailing `<<WORD` inside `<<<WORD`, else the
    # NEXT line's real redirect gets blanked and missed.
    cmd = "bash <<<SCRIPT\necho hi > out.md"
    assert parse_bash_write_targets(cmd) == ["out.md"]


def test_bang_in_filename_is_allowed():
    # Gate-2 F5: `!` is a literal in non-interactive shells (the runtime here) and a
    # legal filename char — it must NOT be rejected as a shell metacharacter.
    assert parse_bash_write_targets("cat > report!draft.html") == ["report!draft.html"]
