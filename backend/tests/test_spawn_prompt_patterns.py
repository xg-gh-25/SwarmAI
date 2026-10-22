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
The ``*`` row count is pinned (<= 12) so always-on rows cannot creep back; a
hook change with a subprocess diff pulls in the command-injection and the
hook-relevant agent-safety rows; a README change pulls in only ``*`` rows plus
doc rows; every agent-safety row (RP65-RP80) is reachable from the agent code
paths it targets, by path alone (the diff is optional on the REVIEW card).
"""
from __future__ import annotations

import fnmatch
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


# ── always-on budget + targeted selection of the narrowed rows ───────────────
_MAX_STAR_ROWS = 12
_HOOKS = "backend/core/security_hooks.py"
_PROMPT = "backend/core/prompt_builder.py"
_SLACK = "backend/channels/adapters/slack.py"
_CTX = "backend/core/context_injector.py"
_SUBPROCESS_DIFF = (
    "--- a/backend/core/security_hooks.py\n"
    "+++ b/backend/core/security_hooks.py\n"
    "@@ -210,6 +210,10 @@ async def bash_syntax_guard(input_data, tool_use_id, context):\n"
    '     command = input_data.get("tool_input", {}).get("command", "")\n'
    "+    proc = subprocess.run(\n"
    '+        ["bash", "-n", "-c", command],\n'
    "+        capture_output=True, text=True, timeout=5,\n"
    "+    )\n"
    "     if proc.returncode != 0:\n"
    '         return _deny(f"Bash syntax error: {proc.stderr.strip()[:200]}")\n'
)


def _star_ids():
    return {r["id"] for r in _rows() if r["always"]}


def test_always_on_row_count_is_pinned():
    star = sorted(_star_ids(), key=lambda s: int(s[2:]))
    assert 0 < len(star) <= _MAX_STAR_ROWS, (
        f"{len(star)} `match: *` rows (cap {_MAX_STAR_ROWS}): {star} -- give the "
        "new row a glob:/kw: match instead of always-on")


def test_hook_change_with_subprocess_diff_selects_injection_and_agent_rows():
    got = _selected([_HOOKS], _SUBPROCESS_DIFF)
    # subprocess lifecycle + shell/command injection + process-tree lifetime
    assert {"RP1", "RP2", "RP17", "RP37"} <= got
    # agent-safety rows whose deterministic gate lives in the PreToolUse hooks
    hook_agent = {"RP65", "RP66", "RP68", "RP69", "RP75", "RP76", "RP77"}
    assert hook_agent <= got
    # the same agent-safety rows hold by path alone (the diff is optional)
    assert hook_agent <= _selected([_HOOKS])
    assert got < {r["id"] for r in _rows()}


def test_readme_change_selects_only_star_rows_and_doc_rows():
    rows = {r["id"]: r for r in _rows()}
    got = _selected(["README.md"])
    extra = got - _star_ids()
    for rid in extra:   # every non-star hit must be a row that targets docs
        assert any(fnmatch.fnmatchcase("readme.md", g) for g in rows[rid]["globs"]), rid
    assert extra == {"RP34"}, sorted(extra)


# Which representative agent path each agent-safety row is ABOUT (chosen per
# row, not "all four"): prompt assembly + skill/MCP loading (prompt_builder),
# the PreToolUse gate layer (security_hooks), an external channel holding a bot
# token and acting for a user (slack adapter), persisted context read back into
# the next session (context_injector).
_AGENT_ROW_PATHS = {
    "RP65": [_PROMPT, _SLACK, _CTX],   # untrusted content -> prompt
    "RP66": [_HOOKS, _PROMPT],         # tool-set composition
    "RP67": [_PROMPT, _HOOKS],         # policy in prose vs enforced in code
    "RP68": [_HOOKS],                  # model output -> shell interpreter
    "RP69": [_HOOKS, _SLACK],          # side-effecting action scope
    "RP70": [_CTX],                    # persisted state read back as trusted
    "RP71": [_SLACK],                  # standing broad credentials
    "RP72": [_SLACK],                  # acting on behalf of a user identity
    "RP73": [_SLACK],                  # async entrypoint bypassing API authz
    "RP74": [_SLACK, _HOOKS],          # URL fetch / egress control
    "RP75": [_HOOKS],                  # model-chosen tool/sub-agent params
    "RP76": [_HOOKS],                  # confused deputy on destructive calls
    "RP77": [_PROMPT, _HOOKS, _SLACK, _CTX],  # secret in code: always-on
    "RP78": [_PROMPT, _HOOKS],         # skill load without provenance
    "RP79": [_PROMPT],                 # transitive / drifting skill trust
    "RP80": [_PROMPT],                 # cross-platform skill semantics
}


def test_agent_row_map_covers_rp65_to_rp80_exactly():
    assert set(_AGENT_ROW_PATHS) == {f"RP{n}" for n in range(65, 81)}
    assert set(_AGENT_ROW_PATHS) <= {r["id"] for r in _rows()}


@pytest.mark.parametrize("rid", sorted(_AGENT_ROW_PATHS, key=lambda s: int(s[2:])))
def test_agent_safety_row_is_selected_by_the_paths_it_targets(rid):
    for path in _AGENT_ROW_PATHS[rid]:
        assert rid in _selected([path]), (rid, path)


def test_agent_safety_rows_are_not_all_always_on():
    # the agent rows were narrowed from `*`; only the secret-in-code row stays
    agent_star = {f"RP{n}" for n in range(65, 81)} & _star_ids()
    assert agent_star == {"RP77"}


# Browser-automation / a11y-perception rows (RP85, RP87, RP88) and the request-identity,
# async-blocking and grouped-LIMIT rows (RP52-RP54) used to match every `*.py` file.
_BROWSER_ROWS = {"RP85", "RP87", "RP88"}


def test_plain_backend_change_skips_browser_rows():
    got = _selected(["backend/core/session_unit.py"])
    assert not got & _BROWSER_ROWS, sorted(got & _BROWSER_ROWS)
    assert "RP53" in got, "async core code must still get the sync-I/O-on-event-loop row"


def test_e2e_spec_gets_browser_rows():
    assert _BROWSER_ROWS <= _selected(["desktop/e2e/chat.spec.ts"])


def test_narrowed_rows_still_fire_on_their_concern():
    assert "RP52" in _selected(["backend/routers/chat.py"])        # request handlers
    assert "RP54" in _selected(["backend/core/recall_multi.py"])   # ranked recall queries
    assert "RP53" in _selected(["backend/x.py"], "+    conn = sqlite3.connect(p)\n")


def test_typical_backend_change_is_a_small_subset():
    rows = {r["id"] for r in _rows()}
    got = _selected(["backend/core/session_unit.py"])
    assert len(got) <= len(rows) // 3, (len(got), len(rows))


# ── compact listing + full rows on demand (run_a8fe815b) ──────────────────────
# REVIEW read every applying row in full (35-70K chars) while recording 3-14
# verdicts. --patterns-for now lists the same rows as one compact line each; the
# full row of any row whose trigger is present comes from --patterns-rows.
_SETS = {  # the three change sets measured at EVALUATE
    "cli": ["backend/scripts/artifact_cli.py", "backend/scripts/pipeline_validator.py",
            "backend/skills/s_autonomous-pipeline/stages/plan.md",
            "backend/tests/test_stage_complete_reentry.py"],
    "daemon": ["backend/core/surface_marker.py", "backend/core/ui_actions.py",
               "backend/core/streaming_orchestrator.py", "backend/scripts/pipeline_run_profile.py"],
    "frontend": ["desktop/src/pages/chat/components/WorkGroup.tsx", "desktop/src/pages/chat/turnView.ts"],
}


def test_compact_listing_keeps_selection_and_drops_long_columns():
    res = _cli("--patterns-for", _PY)
    assert res.returncode == 0, res.stderr
    lines = res.stdout.splitlines()
    assert lines[0].replace(" ", "") == "|#|Pattern|Trigger|"
    printed = [l for l in lines if l.startswith("| RP")]
    assert {l.split("|")[1].strip() for l in printed} == _selected([_PY])
    assert all(l.count("|") == 4 for l in printed), "compact rows have exactly 3 cells"
    assert "What to verify" not in res.stdout and "Example bug" not in res.stdout
    assert "`match:" not in res.stdout
    assert "--patterns-rows" in res.stdout


@pytest.mark.parametrize("name", sorted(_SETS))
def test_compact_listing_is_at_most_40_percent_of_full_rows(name):
    text = _PATTERNS.read_text(encoding="utf-8")
    full = sp.render_patterns_for(text, _SETS[name])
    compact = sp.render_patterns_compact(text, _SETS[name])
    assert len(compact) <= 0.4 * len(full), (name, len(compact), len(full))


def test_patterns_rows_prints_full_rows_in_order_case_insensitive():
    res = _cli("--patterns-rows", "rp7", "RP2")
    assert res.returncode == 0, res.stderr
    lines = res.stdout.splitlines()
    assert lines[0].startswith("| # |") and "What to verify" in lines[0]
    ids = [l.split("|")[1].strip() for l in lines if l.startswith("| RP")]
    assert ids == ["RP7", "RP2"]
    full = {r["id"]: r["line"] for r in _rows()}
    assert full["RP7"] in res.stdout and full["RP2"] in res.stdout


@pytest.mark.parametrize("bad", ["RP999", "RPx", "7"])
def test_patterns_rows_unknown_or_malformed_id_exits_1(bad):
    res = _cli("--patterns-rows", "RP2", bad)
    assert res.returncode == 1 and res.stdout == ""
    assert bad in res.stderr


def test_patterns_rows_reads_patterns_source(tmp_path):
    src = tmp_path / "rp.md"
    src.write_text(
        "| # | Pattern | Trigger | Verify | Example |\n|---|---|---|---|---|\n"
        "| RP1 | a | t `match: *` | v1 | e |\n", encoding="utf-8")
    res = _cli("--patterns-rows", "RP1", "--patterns-source", str(src))
    assert res.returncode == 0 and "| v1 |" in res.stdout


def test_compact_cells_split_from_the_left_with_pipes_in_later_columns(tmp_path):
    src = tmp_path / "rp.md"
    src.write_text(
        "| # | Pattern | Trigger | Verify | Example |\n|---|---|---|---|---|\n"
        "| RP1 | name | trig `match: *` | a \\| b | c | d |\n", encoding="utf-8")
    res = _cli("--patterns-for", "x.py", "--patterns-source", str(src))
    assert res.returncode == 0, res.stderr
    assert "| RP1 | name | trig |" in res.stdout


# v13 §C3: the REVIEW card names checks and commands only; the RP rows reach the
# reviewer through the correctness handout (test_correctness_handout_* below).
@pytest.mark.parametrize("doc", [
    "stages/ref/review/checks-core.md", "REVIEW_PATTERNS.md",
])
def test_review_docs_say_how_to_fetch_full_rows(doc):
    """The rule, not just the flag: verdict every listed row, and fetch the full row
    before judging one whose trigger is present."""
    text = " ".join((_SKILL_ROOT / doc).read_text(encoding="utf-8").split())
    assert "--patterns-rows" in text, doc
    assert "trigger is present" in text, doc
    assert "pass / N/A / fail" in text, doc


def test_code_quality_expert_uses_the_compact_listing():
    text = (_BACKEND / "experts" / "code" / "code-quality.md").read_text(encoding="utf-8")
    assert "--patterns-rows" in text and "Read REVIEW_PATTERNS.md and apply" not in text
    assert "only if that command fails" in text  # a fallback when the cwd is not the repo root


def test_correctness_handout_command_path_exists_from_repo_root():
    """The --patterns-rows command the engine puts in the correctness handout runs
    from the repo root (a shortened path sends the reviewer to the whole file)."""
    import re as _re
    from core.run_protocol import review
    block = review.patterns_block(review.review_patterns(
        ["backend/core/x.py"], "+ subprocess.Popen(['git'])"))
    paths = _re.findall(r"python (\S+spawn_prompt\.py) --patterns-rows", block)
    assert paths, block[:300]
    assert all((_BACKEND.parent / p).is_file() for p in paths), paths
    assert "| RP1 |" in block and "pass / N/A / fail" not in block