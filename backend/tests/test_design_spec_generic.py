"""S6-4c (design 2026-10-24-swarmai-usable-by-anyone.md §3.3.4): DESIGN_SPEC test
references and requirement wording checks no longer assume Python / pytest or a word
list in two languages.

What is tested:
- a test reference is an opaque repo reference: a repo-relative path (a file or a
  directory), optionally followed by a test name after ``::``, ``#`` or a space, in
  any language's convention; the path must exist or be planned; traversal and globs
  are still refused;
- ``properties[].kind`` is optional free text, no longer ``example | pbt``;
- RQ-3 (vague words) and RQ-8b (a fixed zh/en negation table) are gone from the
  check registry; Gate-0's checklist asks the reviewer to judge the same thing in any
  language.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.run_protocol import checks
from core.run_protocol import schemas as sc

BACKEND = Path(__file__).resolve().parents[1]
GATE0 = BACKEND / "skills" / "s_autonomous-pipeline" / "data" / "gate-prompts" / "gate0-skeptic.md"


def _design(ref: str, *, kind: object = "missing") -> dict:
    prop = {"id": "P1", "statement": "s", "validates": ["R1.1"], "test": ref}
    if kind != "missing":
        prop["kind"] = kind
    return {
        "properties": [prop],
        "failure_modes": [{"id": "F1", "scenario": "s", "expected": "e", "test": ref}],
        "tasks": [{"id": "T1", "file": "pkg/a.go", "symbol": "A", "change": "c",
                   "requirements": ["R1.1"], "depends_on": [], "creates_test_files": []}],
        "exit_criteria": [{"id": "X1", "kind": "test", "ref": ref, "covers": ["R1.1", "P1"]}],
        "consumers": [], "no_contract_change_reason": "none",
    }


_FILES = {"pkg/a.go", "pkg/a_test.go", "src/lib.rs", "spec/user_spec.rb", "web/app.test.ts"}


def _issues(doc, files=_FILES):
    return [i for i in sc.validate_design_spec(doc, variant="lite", requirement_ids={"R1.1"},
                                               existing_files=files)
            if i.check_id in ("DS-2a", "DS-9a")]


@pytest.mark.parametrize("ref", [
    "pkg/a_test.go TestAdd",            # Go: file + test name
    "pkg",                               # Go: a package directory
    "src/lib.rs#tests::adds",            # Rust: module path after '#'
    "spec/user_spec.rb",                 # Ruby: a spec file
    "web/app.test.ts::adds two numbers",  # a name with spaces
])
def test_any_language_test_reference_is_accepted(ref):
    assert _issues(_design(ref)) == []


def test_a_reference_must_exist_or_be_planned():
    issues = _issues(_design("pkg/b_test.go TestB"))
    assert any("not planned" in i.message for i in issues), issues
    doc = _design("pkg/b_test.go TestB")
    doc["tasks"][0]["creates_test_files"] = ["pkg/b_test.go"]
    assert _issues(doc) == []


@pytest.mark.parametrize("ref", ["../x_test.go TestX", "/abs/x.go", "pkg/*_test.go", ""])
def test_traversal_glob_and_empty_are_refused(ref):
    assert _issues(_design(ref)), ref


def test_property_kind_is_optional_free_text():
    assert _issues(_design("pkg/a_test.go TestAdd")) == []
    assert _issues(_design("pkg/a_test.go TestAdd", kind="table-driven")) == []
    assert _issues(_design("pkg/a_test.go TestAdd", kind=7))


def test_word_list_checks_are_gone_and_gate0_judges_wording():
    assert "RQ-3" not in checks.CHECKS and "RQ-8b" not in checks.CHECKS
    assert not hasattr(checks, "RQ8B_TABLE_ZH") and not hasattr(checks, "_negative_or_universal")
    text = GATE0.read_text(encoding="utf-8").lower()
    for phrase in ("acceptance", "verifiable", "negative", "universal", "any language"):
        assert phrase in text, phrase


# --- review of 31e50759a ------------------------------------------------------------

def test_a_path_with_a_space_resolves_to_the_whole_path_not_its_first_word():
    files = {"My Tests/foo.spec.ts", "test dir/a.go", "test/other.go"}
    assert _issues(_design("My Tests/foo.spec.ts"), files) == []
    assert _issues(_design("test dir/a.go TestA"), files) == []
    # `test/` exists, but the reference names `test dir/…`, which does not: refused,
    # not accepted as the directory `test` (it used to be)
    issues = _issues(_design("test dir/b.go TestB"), files)
    assert any("not planned" in i.message for i in issues), issues


def test_resolve_test_ref_takes_the_longest_known_prefix():
    known = {"web/app.test.ts", "src/lib.rs", "pkg/a_test.go"}
    assert sc.resolve_test_ref("web/app.test.ts::adds two numbers", known) == "web/app.test.ts"
    assert sc.resolve_test_ref("src/lib.rs#tests::adds", known) == "src/lib.rs"
    assert sc.resolve_test_ref("pkg TestAdd", known) == "pkg"           # a directory
    assert sc.resolve_test_ref("pk TestAdd", known) is None             # no prefix match
    assert sc.resolve_test_ref("nope/x.go", known) is None


def test_a_backslash_path_is_refused_with_a_hint():
    issues = _issues(_design("pkg\\a_test.go TestAdd"))
    assert any("/" in i.message and "backslash" in i.message for i in issues), issues
    doc = _design("pkg/a_test.go TestAdd")
    doc["tasks"][0]["creates_test_files"] = ["pkg\\b_test.go"]
    assert any("backslash" in i.message for i in _issues(doc))


def _ts4(design: dict, files: set[str]):
    from core.run_protocol import events as ev
    context = checks.make_context(
        profile="full", requirement_variant="full", requirements={}, design_variant="full",
        design=design, authority=ev.EventAuthority(), existing_files=files)
    return checks.evaluate("TS-4", context)


def test_ts4_a_planned_test_file_must_exist_when_test_completes():
    """DS-2a accepts a reference to a file the plan says it will create; nothing
    checked later that it was created. TS-4 resolves every reference against the
    tested code tree, the plan no longer counts."""
    doc = _design("pkg/b_test.go TestB")
    doc["tasks"][0]["creates_test_files"] = ["pkg/b_test.go"]
    ok, detail = _ts4(doc, set(_FILES))
    assert not ok and "pkg/b_test.go" in detail, detail
    ok, detail = _ts4(doc, set(_FILES) | {"pkg/b_test.go"})
    assert ok, detail


def test_ts4_is_a_blocking_test_check_for_the_design_profiles():
    spec = checks.CHECKS["TS-4"]
    assert "stage" in spec.effects and spec.stages == ("test",)
    for profile in ("full", "bugfix", "goal", "trivial"):
        assert "TS-4" in {s.check_id for s in checks.stage_checks(profile, "test")}
    assert "TS-4" not in {s.check_id for s in checks.stage_checks("direct", "test")}
