"""Tests for ``spawn_prompt.py --patterns-for`` (REVIEW_PATTERNS on-demand rows).

What is tested: every REVIEW_PATTERNS.md row carries exactly one parseable
``match:`` token; the selector picks a strict, file-type-specific subset; ``*``
rows always print; keywords read the diff; usage errors exit 1.

Methodology: the REAL REVIEW_PATTERNS.md is parsed (no fixture copy), so a row
appended without a token, or a malformed token, fails here. The CLI is driven
as a subprocess for the exit-code contract; the parser is imported by path
(the skill directory name has a hyphen, so a dotted import is impossible).

Key invariants: selection never hides a row it cannot judge (a missing token
reads as always-on); a backend .py path and a desktop .tsx path select
different strict subsets; ``N of M patterns apply`` reports the real totals.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1]
_SKILL_ROOT = _BACKEND / "skills" / "s_autonomous-pipeline"
_SCRIPT = _SKILL_ROOT / "scripts" / "spawn_prompt.py"
_PATTERNS = _SKILL_ROOT / "REVIEW_PATTERNS.md"

_PY = "backend/core/session_unit.py"
_TSX = "desktop/src/pages/ChatPage.tsx"


def _load():
    spec = importlib.util.spec_from_file_location("spawn_prompt_under_test", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sp = _load()


def _rows():
    return sp.parse_review_patterns(_PATTERNS.read_text(encoding="utf-8"))[1]


def _selected(paths, diff=""):
    return {r["id"] for r in _rows() if sp.row_applies(r, paths, diff)}


def _cli(*args, stdin=None):
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args], input=stdin,
        capture_output=True, text=True, timeout=60,
    )


def test_real_review_patterns_parses_with_header_and_every_row():
    text = _PATTERNS.read_text(encoding="utf-8")
    header, rows = sp.parse_review_patterns(text)
    raw = [l for l in text.splitlines() if l.startswith("| RP")]
    assert len(rows) == len(raw) >= 80
    assert header[0].startswith("| # |") and set(header[1]) <= set("|-: ")


def test_every_row_has_exactly_one_match_token():
    rows = _rows()
    missing = [r["id"] for r in rows if r["missing"]]
    assert missing == [], f"rows without a `match:` token: {missing}"
    for r in rows:
        assert len(sp._MATCH_TOKEN_RE.findall(r["line"])) == 1, r["id"]
        assert r["always"] or r["globs"] or r["keywords"], r["id"]


def test_star_rows_and_specific_rows_both_exist():
    rows = _rows()
    star = [r for r in rows if r["always"]]
    assert 0 < len(star) < len(rows)


def test_backend_python_path_selects_a_strict_subset():
    all_ids = {r["id"] for r in _rows()}
    got = _selected([_PY])
    assert got < all_ids
    assert {"RP1", "RP19", "RP53"} <= got        # Python-only patterns
    assert not {"RP3", "RP6", "RP57"} & got      # React-only patterns


def test_frontend_tsx_path_selects_a_different_strict_subset():
    all_ids = {r["id"] for r in _rows()}
    py, tsx = _selected([_PY]), _selected([_TSX])
    assert tsx < all_ids and tsx != py
    assert {"RP3", "RP6", "RP57", "RP61"} <= tsx
    assert not {"RP19", "RP53"} & tsx


def test_star_rows_are_present_for_any_path():
    star = {r["id"] for r in _rows() if r["always"]}
    for paths in ([_PY], [_TSX], ["docs/notes.txt"], ["x"]):
        assert star <= _selected(paths), paths


def test_keyword_in_diff_content_selects_a_row():
    # RP5 is frontend-scoped by glob, but a FormData use in the diff of a file
    # outside those globs must still bring it in.
    assert "RP5" not in _selected(["docs/notes.txt"])
    assert "RP5" in _selected(["docs/notes.txt"], diff="+ const f = new FormData()")


def test_union_of_paths_is_the_union_of_selections():
    assert _selected([_PY, _TSX]) == _selected([_PY]) | _selected([_TSX])


@pytest.mark.parametrize("spec", ["glob", "glob:", "regex:x", "kw"])
def test_malformed_match_term_raises(spec):
    with pytest.raises(sp.PatternsError):
        sp.parse_match_spec(spec)


def test_missing_token_reads_as_always_on_and_is_warned(tmp_path):
    src = tmp_path / "rp.md"
    src.write_text(
        "| # | Pattern | Trigger | Verify | Example |\n|---|---|---|---|---|\n"
        "| RP1 | a | t `match: glob:*.py` | v | e |\n"
        "| RP2 | b | no token here | v | e |\n", encoding="utf-8")
    res = _cli("--patterns-for", "a.tsx", "--patterns-source", str(src))
    assert res.returncode == 0, res.stderr
    assert "| RP2 |" in res.stdout and "| RP1 |" not in res.stdout
    assert "1 of 2 patterns apply" in res.stdout and "WARNING" in res.stdout


def test_cli_prints_header_rows_and_count_line():
    res = _cli("--patterns-for", _PY)
    assert res.returncode == 0, res.stderr
    lines = res.stdout.splitlines()
    assert lines[0].startswith("| # |")
    printed = [l for l in lines if l.startswith("| RP")]
    assert f"{len(printed)} of {len(_rows())} patterns apply" in res.stdout
    assert {l.split("|")[1].strip() for l in printed} == _selected([_PY])


def test_cli_stdin_matches_args_form():
    a = _cli("--patterns-for", _PY, _TSX)
    b = _cli("--patterns-for-stdin", stdin=f"{_PY}\n{_TSX}\n\n")
    assert a.returncode == b.returncode == 0
    assert a.stdout == b.stdout


def test_cli_diff_file_feeds_keywords(tmp_path):
    diff = tmp_path / "c.diff"
    diff.write_text("+ body = new FormData()\n", encoding="utf-8")
    res = _cli("--patterns-for", "docs/notes.txt", "--diff-file", str(diff))
    assert res.returncode == 0 and "| RP5 |" in res.stdout


@pytest.mark.parametrize("args,stdin", [
    (["--patterns-for", _PY, "--bogus"], None),        # unknown flag
    (["--patterns-for"], None),                         # no path
    (["--patterns-for-stdin"], "\n  \n"),               # empty stdin
    (["--gate", "gate1-skeptic", "--diff-file", "x"], None),  # flag misuse
    (["--patterns-for", _PY, "--patterns-source", "/nonexistent/rp.md"], None),
])
def test_cli_usage_errors_exit_1_with_empty_stdout(args, stdin):
    res = _cli(*args, stdin=stdin)
    assert res.returncode == 1, (res.returncode, res.stderr)
    assert res.stdout == ""
