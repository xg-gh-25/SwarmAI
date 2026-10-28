"""DDD drift auto-fix r4 (design docs/design/2026-10-27-ddd-drift-auto-fix.md): only three
fixes exist, each a fixed grammar the OS checks and computes itself — drop a waiting
condition a ticked line shows is done, swap a path for the one its superseded header
names, swap a value for the one tracked code assigns. Includes the attacks two review
rounds built against free-text fixes, now unrepresentable or refused."""
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from core import ddd_drift_fix as df

PROJECT_MD = """# P — Context

## Current Focus

- [ ] **P6 (VERIFY)** — add an agent-behavioral benchmark (`agent-eval`,
      expectedTools:{required}) once `s_security-cr-review` exists — grades the reviewer.
Design doc: `assets/old-design.md` for the engine.
The handler cap is MAX_ROUTES = 42 today.

## Open Items

- [x] **P5 (REVIEW)** — the reviewer is ported into the `security-cr-review` skill
- [ ] Ship the export feature once `export-v2` lands for every customer
- [x] Wrote the `migrations` script

## Lessons

- [pitfall] **Old lesson** — the gate waits until `gate-x` is built, tracked in PROJECT.
  <!-- ref:0 | last:2026-08-01 | decay:active -->

~~~
fenced: once `security-cr-review` is ready we go
~~~
"""

DROP = {"type": "drop_condition", "doc": "PROJECT.md",
        "line": "expectedTools:{required}) once `s_security-cr-review` exists — grades the reviewer.",
        "span": "once `s_security-cr-review` exists",
        "done": "- [x] **P5 (REVIEW)** — the reviewer is ported into the `security-cr-review` skill"}
SWAP = {"type": "swap_superseded_path", "doc": "PROJECT.md",
        "path_old": "assets/old-design.md", "path_new": "assets/new-design.md"}
VALUE = {"type": "swap_code_value", "doc": "PROJECT.md", "line": "The handler cap is MAX_ROUTES = 42 today.",
         "identifier": "MAX_ROUTES", "value_old": "42", "value_new": "57", "code_file": "src/limits.py"}


@pytest.fixture()
def proj(tmp_path):
    p = tmp_path / "ws" / "Projects" / "P"
    (p / "2-understanding").mkdir(parents=True)
    (p / "2-understanding" / "PROJECT.md").write_text(PROJECT_MD)
    (p / "assets").mkdir()
    (p / "assets" / "old-design.md").write_text(
        "> ⚠️ **SUPERSEDED (v2-era).** Current canonical version: **`assets/new-design.md`**. Kept for history.\n\nold\n")
    (p / "assets" / "new-design.md").write_text("# new\n")
    return p


@pytest.fixture()
def repo(tmp_path):
    r = tmp_path / "repo"
    (r / "src").mkdir(parents=True)
    (r / "src" / "limits.py").write_text("MAX_ROUTES = 57\n")
    (r / "node_modules" / "pkg").mkdir(parents=True)
    (r / "node_modules" / "pkg" / "index.js").write_text("MAX_ROUTES = 99\n")
    (r / "src" / "test_limits.py").write_text("MAX_ROUTES = 99\n")
    (r / "src" / "untracked.py").write_text("MAX_ROUTES = 99\n")
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q", str(r)], check=True, env=env)
    subprocess.run(["git", "-C", str(r), "add", "-f", "src/limits.py", "src/test_limits.py", "node_modules/pkg/index.js"],
                   check=True, env=env)
    subprocess.run(["git", "-C", str(r), "commit", "-qm", "x"], check=True, env=env)
    return r


def commit(repo, rel, body):
    (repo / rel).parent.mkdir(parents=True, exist_ok=True)
    (repo / rel).write_text(body)
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "-C", str(repo), "add", "-f", rel], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "c"], check=True, env=env)


def ws(proj):
    return proj.parent.parent


def doc(proj):
    return proj / "2-understanding" / "PROJECT.md"


def check(proj, fix, repo=None):
    return df.check_fix(fix, proj, ws(proj), repo)


class TestDropCondition:
    def test_drops_the_condition_a_ticked_line_settles(self, proj):
        r = check(proj, DROP)
        assert r["ok"] is True, r
        assert r["new_line"] == "      expectedTools:{required}) — grades the reviewer."

    @pytest.mark.parametrize("span", ["not", "never", "without a backup", "once it exists",
                                      "once `s_security-cr-review` exists — grades", "only once `x` exists"])
    def test_only_the_waiting_condition_grammar(self, proj, span):
        """Free deletion flipped meaning ("not run migrations" → "run migrations"); now the span
        must BE a waiting condition, so a negation or limit cannot be removed."""
        assert check(proj, {**DROP, "span": span})["ok"] is False

    def test_the_span_must_be_in_the_line_once(self, proj):
        doc(proj).write_text(PROJECT_MD + "\nRun it once `security-cr-review` exists, retry once `security-cr-review` exists.\n")
        f = {**DROP, "line": "Run it once `security-cr-review` exists, retry", "span": "once `security-cr-review` exists"}
        assert "exactly once" in check(proj, f)["reason"]

    def test_the_ticked_line_must_name_the_same_thing(self, proj):
        assert "does not name" in check(proj, {**DROP, "done": "- [x] Wrote the `migrations` script"})["reason"]

    def test_the_done_line_must_be_ticked_and_in_the_projects_ddd_docs(self, proj):
        r = check(proj, {**DROP, "done": "- [ ] Ship the export feature once `export-v2` lands for every customer"})
        assert "not a ticked" in r["reason"]
        (proj / "assets" / "notes.md").write_text("- [x] ported into the `security-cr-review` skill\n")
        assert "not a ticked" in check(proj, {**DROP, "done": "- [x] ported into the `security-cr-review` skill"})["reason"]

    def test_a_ticked_line_is_not_its_own_evidence(self, proj):
        doc(proj).write_text(PROJECT_MD + "\n- [x] Moved it once `job-runner` lands to the new host\n")
        f = {**DROP, "line": "- [x] Moved it once `job-runner` lands to the new host",
             "span": "once `job-runner` lands", "done": "- [x] Moved it once `job-runner` lands to the new host"}
        assert "own evidence" in check(proj, f)["reason"]

    def test_names_match_only_as_whole_qualified_names(self):
        assert df._names_same("s_security-cr-review", "security-cr-review")
        assert df._names_same("pkg/review-gate", "review-gate")
        assert not df._names_same("superreview", "review")
        assert not df._names_same("s_abc", "abc")  # too short to be a name

    def test_cjk_condition(self, proj):
        doc(proj).write_text(PROJECT_MD + "\n下一步：等 `security-cr-review` 完成后再加基准测试。\n")
        f = {**DROP, "line": "下一步：等 `security-cr-review` 完成后再加基准测试。", "span": "等 `security-cr-review` 完成后"}
        r = check(proj, f)
        assert r["ok"] is True and r["new_line"] == "下一步：再加基准测试。"


class TestSwapSupersededPath:
    def test_swaps_to_the_path_the_header_names(self, proj):
        r = check(proj, SWAP)
        assert r["ok"] is True and r["new_line"] == "Design doc: `assets/new-design.md` for the engine."

    def test_a_header_that_says_not_superseded_does_not_count(self, proj):
        (proj / "assets" / "old-design.md").write_text("This is not superseded by `assets/new-design.md`, a draft.\n")
        assert "does not declare" in check(proj, SWAP)["reason"]

    def test_the_declaration_must_be_in_the_header(self, proj):
        (proj / "assets" / "old-design.md").write_text("\n" * 12 + "> SUPERSEDED by `assets/new-design.md`\n")
        assert "does not declare" in check(proj, SWAP)["reason"]

    def test_status_front_matter_counts(self, proj):
        (proj / "assets" / "old-design.md").write_text("---\nstatus: superseded by `assets/new-design.md`\n---\n")
        assert check(proj, SWAP)["ok"] is True

    def test_the_new_path_must_exist_and_be_a_path(self, proj):
        (proj / "assets" / "old-design.md").write_text("> SUPERSEDED by `assets/gone.md`\n")
        assert "does not exist" in check(proj, {**SWAP, "path_new": "assets/gone.md"})["reason"]
        assert "not two paths" in check(proj, {**SWAP, "path_new": "assets/new design.md"})["reason"]

    def test_the_old_path_must_appear_once_and_never_resolve_into_artifacts(self, proj):
        doc(proj).write_text(PROJECT_MD + "\nsee assets/old-design.md again\n")
        assert "2 times" in check(proj, SWAP)["reason"]
        doc(proj).write_text(PROJECT_MD.replace("assets/old-design.md", ".artifacts/old.md"))
        (proj / ".artifacts").mkdir()
        (proj / ".artifacts" / "old.md").write_text("> SUPERSEDED by `assets/new-design.md`\n")
        assert check(proj, {**SWAP, "path_old": ".artifacts/old.md"})["ok"] is False


class TestSwapCodeValue:
    def test_swaps_to_the_value_tracked_code_assigns(self, proj, repo):
        r = check(proj, VALUE, repo)
        assert r["ok"] is True and r["new_line"] == "The handler cap is MAX_ROUTES = 57 today."

    @pytest.mark.parametrize("file", ["node_modules/pkg/index.js", "src/test_limits.py", "src/untracked.py",
                                      "src/limits.md", "../repo/src/limits.py"])
    def test_only_tracked_source_files(self, proj, repo, file):
        assert check(proj, {**VALUE, "code_file": file, "value_new": "99"}, repo)["ok"] is False

    def test_the_code_must_assign_that_value(self, proj, repo):
        assert "declares MAX_ROUTES = 57, not 58" in check(proj, {**VALUE, "value_new": "58"}, repo)["reason"]

    def test_no_repo_no_value_swap(self, proj):
        assert "no bound source repo" in check(proj, VALUE, None)["reason"]

    def test_values_are_numbers_and_the_line_names_the_identifier(self, proj, repo):
        (repo / "src" / "limits.py").write_text('MAX_ROUTES = "skip review"\n')
        r = check(proj, {**VALUE, "value_new": "skip review"}, repo)
        assert "numeric" in r["reason"]  # an assignment of words is not a value to copy
        commit(repo, "src/limits.py", "MAX_ROUTES = 57\nOTHER_CAP = 57\n")
        assert "does not state" in check(proj, {**VALUE, "identifier": "OTHER_CAP"}, repo)["reason"]


class TestOnePlainLine:
    def test_unsupported_condition_and_fences(self, proj):
        f = {**DROP, "line": "the gate waits until `gate-x` is built, tracked", "span": "until `gate-x` is built"}
        assert "not a waiting condition" in check(proj, f)["reason"]
        f = {**DROP, "line": "fenced: once `security-cr-review` is ready we go", "span": "once `security-cr-review` is ready"}
        assert "code block" in check(proj, f)["reason"]

    def test_front_matter_and_one_dash_setext(self, proj):
        doc(proj).write_text("---\ntitle: waits once `security-cr-review` exists here\n---\n\n"
                             "Plan: once `security-cr-review` exists we start\n-\n\n"
                             "- [x] **P5** — ported into the `security-cr-review` skill\n")
        f = {**DROP, "line": "title: waits once `security-cr-review` exists here", "span": "once `security-cr-review` exists",
             "done": "- [x] **P5** — ported into the `security-cr-review` skill"}
        assert "front matter" in check(proj, f)["reason"]
        f["line"] = "Plan: once `security-cr-review` exists we start"
        assert "heading" in check(proj, f)["reason"]

    @pytest.mark.parametrize("char", ["\u2028", "\u200b", "\u202e", "\x0c"])
    def test_a_doc_with_odd_separators_or_invisible_characters_is_left_alone(self, proj, char):
        doc(proj).write_text(PROJECT_MD.replace("Design doc:", f"Design{char}doc:"))
        assert "not edited automatically" in check(proj, DROP)["reason"]

    def test_unknown_types_and_free_text_are_refused(self):
        assert df.parse_fix({"fix": {"doc": "PROJECT.md", "old": "a" * 20, "new": "b" * 20, "evidence": []}}) is None
        assert df.parse_fix({"fix": {**DROP, "type": "rewrite"}}) is None
        assert df.parse_fix({"fix": {**DROP, "span": ""}}) is None
        assert df.parse_fix({"fix": DROP})["type"] == "drop_condition"




def test_first_seen_reads_only_earlier_real_reports(tmp_path):
    for name, body in (("2026-09-21-ddd-self-audit.md", "x All 42 mapped y"),
                       ("2026-09-28-ddd-self-audit-dryrun.md", "All 42 mapped"),
                       ("2026-10-26-ddd-self-audit.md", "All 42 mapped")):
        (tmp_path / name).write_text(body)
    assert df.first_seen("All 42 mapped", tmp_path, before="2026-10-26") == "2026-09-21"
    assert df.first_seen("All 42 mapped", tmp_path, before="2026-09-21") is None
    assert df.first_seen("short", tmp_path) is None


def line_fix(proj, text, span, done="- [x] **P5 (REVIEW)** — the reviewer is ported into the `security-cr-review` skill"):
    """Put ``text`` on its own line in the doc and build a drop_condition for ``span``."""
    doc(proj).write_text(PROJECT_MD + "\n" + text + "\n")
    return {**DROP, "line": text, "span": span, "done": done}


class TestDropConditionAttacks:
    """The two r4 reviews: a deletion that reverses, dangles or breaks the line."""

    @pytest.mark.parametrize("text,span", [
        ("Keep the legacy fallback enabled until `security-cr-review` ships.", "until `security-cr-review` ships"),
        ("Abort the run when `security-cr-review` exists, never overwrite it.", "when `security-cr-review` exists"),
    ])
    def test_until_and_when_are_not_waiting_conditions(self, proj, text, span):
        assert "not a waiting condition" in check(proj, line_fix(proj, text, span))["reason"]

    @pytest.mark.parametrize("text", [
        "Enable writes to prod, but not once `security-cr-review` exists anywhere.",
        "Do not deploy once `security-cr-review` ships to the fleet.",
        "上线前不要等 `security-cr-review` 完成后再发布到生产环境。",
    ])
    def test_a_negation_before_it_blocks(self, proj, text):
        span = "once `security-cr-review` exists" if "exists" in text else (
            "once `security-cr-review` ships" if "ships" in text else "等 `security-cr-review` 完成后")
        assert "negation" in check(proj, line_fix(proj, text, span))["reason"]

    def test_a_joined_second_condition_blocks(self, proj):
        f = line_fix(proj, "Ship it once `security-cr-review` exists and `other-thing` is ready.",
                     "once `security-cr-review` exists")
        assert "joined to another" in check(proj, f)["reason"]

    def test_a_condition_starting_the_sentence_blocks(self, proj):
        f = line_fix(proj, "Once `security-cr-review` exists, enable the benchmark.", "Once `security-cr-review` exists")
        assert "starts the sentence" in check(proj, f)["reason"]

    @pytest.mark.parametrize("text", ["**Enabled once `security-cr-review` exists** for all users",
                                      "See [enabled once `security-cr-review` exists](a.md) for details",
                                      "A nonce `security-cr-review` exists here for padding"])
    def test_markup_and_word_joins_block(self, proj, text):
        span = "once `security-cr-review` exists"
        assert check(proj, line_fix(proj, text, span))["ok"] is False

    def test_the_rest_of_the_line_is_kept_byte_for_byte(self, proj):
        text = "Call `run()` and  cache `a  b` once `security-cr-review` exists, then go.  "
        r = check(proj, line_fix(proj, text, "once `security-cr-review` exists"))
        assert r["ok"] is True
        assert r["new_line"] == "Call `run()` and  cache `a  b`, then go.  "

    def test_a_ticked_line_about_removing_it_is_not_evidence(self, proj):
        doc(proj).write_text(PROJECT_MD.replace("- [x] Wrote the `migrations` script",
                                                "- [x] Deleted the dead `security-cr-review` stub"))
        f = {**DROP, "done": "- [x] Deleted the dead `security-cr-review` stub"}
        assert "removing" in check(proj, f)["reason"]

    def test_a_ticked_line_in_a_code_block_is_not_evidence(self, proj):
        doc(proj).write_text(PROJECT_MD + "\n```\n- [x] fake `security-cr-review` done\n```\n")
        assert "not a ticked plain line" in check(proj, {**DROP, "done": "- [x] fake `security-cr-review` done"})["reason"]

    def test_a_longer_name_ending_the_same_is_another_thing(self):
        assert not df._names_same("new_router", "legacy_new_router")
        assert df._names_same("review-gate", "s_review-gate")


class TestSwapPathAttacks:
    def test_a_path_found_in_two_roots_is_ambiguous(self, proj):
        shadow = ws(proj) / "assets" / "old-design.md"
        shadow.parent.mkdir(parents=True)
        shadow.write_text("> SUPERSEDED by `assets/new-design.md`\n")
        assert "exactly one" in check(proj, SWAP)["reason"]

    def test_the_successor_is_the_first_path_on_the_line_exactly(self, proj):
        (proj / "assets" / "old-design.md").write_text("> SUPERSEDED by `assets/new-design.md` (mirror `assets/x/new-design.md`)\n")
        (proj / "assets" / "x").mkdir()
        (proj / "assets" / "x" / "new-design.md").write_text("m\n")
        assert "names assets/new-design.md" in check(proj, {**SWAP, "path_new": "assets/x/new-design.md"})["reason"]
        assert "names" in check(proj, {**SWAP, "path_new": "new-design.md"})["reason"]

    def test_superseded_by_key_and_a_path_ending_a_sentence(self, proj):
        (proj / "assets" / "old-design.md").write_text("---\nsuperseded_by: assets/new-design.md\n---\n")
        doc(proj).write_text(PROJECT_MD.replace("Design doc: `assets/old-design.md` for the engine.",
                                                "The design doc is assets/old-design.md."))
        r = check(proj, SWAP)
        assert r["ok"] is True and r["new_line"] == "The design doc is assets/new-design.md."


class TestSwapValueAttacks:
    def test_a_call_argument_or_a_comment_is_not_a_declaration(self, proj, repo):
        commit(repo, "src/limits.py", "MAX_ROUTES = 57\nf(MAX_ROUTES=3)\n# MAX_ROUTES = 9\n")
        assert check(proj, VALUE, repo)["ok"] is True
        assert "declares MAX_ROUTES = 57" in check(proj, {**VALUE, "value_new": "3"}, repo)["reason"]

    def test_two_declarations_are_ambiguous(self, proj, repo):
        commit(repo, "src/limits.py", "MAX_ROUTES = 57\nMAX_ROUTES = 58\n")
        assert "exactly once" in check(proj, VALUE, repo)["reason"]

    def test_a_staged_but_uncommitted_file_does_not_count(self, proj, repo):
        (repo / "src" / "staged.py").write_text("MAX_ROUTES = 57\n")
        subprocess.run(["git", "-C", str(repo), "add", "src/staged.py"], check=True)
        assert "not a committed file" in check(proj, {**VALUE, "code_file": "src/staged.py"}, repo)["reason"]

    def test_typed_constants_count(self, proj, repo):
        commit(repo, "src/limits.ts", "export const MAX_ROUTES: number = 57;\n")
        assert check(proj, {**VALUE, "code_file": "src/limits.ts"}, repo)["ok"] is True

    @pytest.mark.parametrize("text,vold", [
        ("Python 42 needed; `MAX_ROUTES` lives in cfg.", "42"),
        ("MAX_ROUTES raised in 2026-10-42 by ops", "42"),
        ("MAX_ROUTES = 42,000 now", "42"),
    ])
    def test_the_value_must_be_the_one_stated_for_the_identifier(self, proj, repo, text, vold):
        doc(proj).write_text(PROJECT_MD + "\n" + text + "\n")
        assert check(proj, {**VALUE, "line": text, "value_old": vold}, repo)["ok"] is False




def with_done(proj, extra, done):
    """Append ``extra`` to the doc and point DROP's evidence at the ticked line ``done``."""
    doc(proj).write_text(PROJECT_MD + "\n" + extra + "\n")
    return {**DROP, "done": done}


class TestR6Sentence:
    """Round-2 reviews of r5: removals that left a sentence wrong or broken."""

    @pytest.mark.parametrize("text", [
        "Enable the flag for users, once `security-cr-review` exists, for all regions.",
        "Enable the flag — once `security-cr-review` exists — for all regions.",
        "Enable the flag for every user, once `security-cr-review` exists",
    ])
    def test_punctuation_would_dangle(self, proj, text):
        assert "dangling" in check(proj, line_fix(proj, text, "once `security-cr-review` exists"))["reason"]

    def test_after_is_not_a_waiting_condition(self, proj):
        """"two weeks after X ships" is a time limit on a destructive step, not a wait."""
        f = line_fix(proj, "Drop the old table two weeks after `security-cr-review` ships, not before.",
                     "after `security-cr-review` ships")
        assert "not a waiting condition" in check(proj, f)["reason"]

    def test_the_second_of_two_or_conditions_is_kept(self, proj):
        f = line_fix(proj, "Enable the cache once `cache_srv` lands or once `security-cr-review` lands.",
                     "once `security-cr-review` lands")
        assert "joined to another" in check(proj, f)["reason"]

    def test_two_conditions_on_one_line_block(self, proj):
        f = line_fix(proj, "Turn on A once `cache_srv` lands; turn on B once `security-cr-review` lands.",
                     "once `security-cr-review` lands")
        assert "more than one waiting condition" in check(proj, f)["reason"]

    def test_a_quoted_list_item_starting_with_it_blocks(self, proj):
        f = line_fix(proj, "> - once `security-cr-review` exists, do the rollout", "once `security-cr-review` exists")
        assert "starts the sentence" in check(proj, f)["reason"]

    def test_crlf_doc_keeps_its_line_endings(self, proj):
        body = (PROJECT_MD + "\nRun the benchmark weekly once `security-cr-review` exists\n").replace("\n", "\r\n")
        doc(proj).write_bytes(body.encode())
        f = {**DROP, "line": "Run the benchmark weekly once `security-cr-review` exists",
             "span": "once `security-cr-review` exists"}
        before = doc(proj).read_bytes()
        result = df.apply_fix(f, proj, ws(proj), None, run_id="R1")
        assert result["ok"] is True
        assert result["new_line"] == "Run the benchmark weekly\r"
        assert doc(proj).read_bytes() == before


class TestR6Evidence:
    """Evidence that does not render, or does not say the thing was made."""

    @pytest.mark.parametrize("extra", [
        "```\n~~~\n- [x] Built `security-cr-review`\n```",              # a ~~~ line inside a ``` block
        "Example checklist:\n\n    - [x] Built `security-cr-review`",    # indented code, not a list
        "Example:\n\n    first line\n    - [x] Built `security-cr-review`",  # second line of indented code
        "<!--\n- [x] Built `security-cr-review`\n-->",                   # inside a multi-line comment
        "````\n```\n- [x] Built `security-cr-review`\n````",          # a shorter fence does not close
    ])
    def test_a_ticked_line_that_does_not_render_is_not_evidence(self, proj, extra):
        f = with_done(proj, extra, "- [x] Built `security-cr-review`")
        assert "not a ticked plain line" in check(proj, f)["reason"]

    def test_a_ticked_line_after_a_closed_fence_is_evidence(self, proj):
        f = with_done(proj, "```\ncode\n```\n\n- [x] Built `security-cr-review`", "- [x] Built `security-cr-review`")
        assert check(proj, f)["ok"] is True

    def test_the_item_continuation_is_read_for_removal(self, proj):
        f = with_done(proj, "- [x] **API** — `security-cr-review` shipped in v1\n      then rolled back; not live.",
                      "- [x] **API** — `security-cr-review` shipped in v1")
        assert "removing, postponing" in check(proj, f)["reason"]

    @pytest.mark.parametrize("done,why", [
        ("- [x] Filed a ticket to postpone `security-cr-review` to Q3", "removing, postponing"),
        ("- [x] Asked about `security-cr-review` in standup", "does not say it was built"),
        ("- [x] Built `tests/security-cr-review` for the demo", "does not name"),
        ("- [x] Built `demo.security-cr-review` for the offsite", "does not name"),
    ])
    def test_the_ticked_line_must_say_it_was_made(self, proj, done, why):
        text = "Run the benchmark weekly once `security-cr-review` exists — then report."
        doc(proj).write_text(PROJECT_MD + "\n" + text + "\n" + done + "\n")
        f = {**DROP, "line": text, "span": "once `security-cr-review` exists", "done": done}
        assert why in check(proj, f)["reason"]
        ok = "- [x] Built `pkg/security-cr-review` for the fleet"
        doc(proj).write_text(PROJECT_MD + "\n" + text + "\n" + ok + "\n")
        assert check(proj, {**f, "done": ok})["ok"] is True

    def test_whole_words_only_for_removal(self, proj):
        done = "- [x] Ported the cancellation API into `security-cr-review` (see notes, authz-not-prose)"
        assert check(proj, with_done(proj, done, done))["ok"] is True
        for edge in ("not-done-yet", "shipped-not"):  # a hyphen on one side only still counts
            bad = done.replace("authz-not-prose", edge)
            assert "removing" in check(proj, with_done(proj, bad, bad))["reason"]


class TestR6Value:
    def test_a_keyword_argument_of_a_split_call_is_not_a_declaration(self, proj, repo):
        commit(repo, "src/limits.py", "MAX_ROUTES = 57\nclient = Client(\n    url,\n    MAX_ROUTES=30,\n)\n")
        assert check(proj, VALUE, repo)["ok"] is True
        commit(repo, "src/limits.py", "client = Client(\nMAX_ROUTES=57\n)\n")
        assert "exactly once" in check(proj, VALUE, repo)["reason"]

    def test_an_indented_assignment_without_a_keyword_is_not_a_declaration(self, proj, repo):
        commit(repo, "src/limits.py", "def f():\n    MAX_ROUTES = 57\n")
        assert "exactly once" in check(proj, VALUE, repo)["reason"]

    def test_a_docstring_or_block_comment_example_does_not_count(self, proj, repo):
        commit(repo, "src/limits.py", '"""Usage:\nMAX_ROUTES = 9\n"""\nMAX_ROUTES = 57\n')
        assert check(proj, VALUE, repo)["ok"] is True
        commit(repo, "src/Limits.java", "/*\nMAX_ROUTES = 9;\n*/\nclass L {\n    static final int MAX_ROUTES = 57;\n}\n")
        assert check(proj, {**VALUE, "code_file": "src/Limits.java"}, repo)["ok"] is True




def test_apply_fix_returns_computed_line_and_never_writes(proj):
    before = doc(proj).read_bytes()
    result = df.apply_fix(DROP, proj, ws(proj), None, run_id="R1")
    assert result["ok"] and result["reason"] == "computed"
    assert result["new_line"].endswith("— grades the reviewer.")
    assert doc(proj).read_bytes() == before
    assert not (proj / ".artifacts" / "drift-fix-backups").exists()
    source = Path(df.__file__).read_text(encoding="utf-8")
    apply_source = source[source.index("def apply_fix"):source.index("def first_seen")]
    assert "write_" not in apply_source and "os.replace" not in apply_source
