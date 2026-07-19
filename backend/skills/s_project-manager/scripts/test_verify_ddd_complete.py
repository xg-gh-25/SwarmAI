"""Tests for verify_ddd_complete.py — the asset-aware DDD completeness gate.

Methodology: each test drives the REAL gate (verify_project) against a synthetic
DDD built in tmp_path (or a real project on disk), asserting the per-check status
AND the overall exit semantics. The load-bearing invariant — a data-agent /
pure-knowledge brain must NOT be failed for a missing code-intel.json — is pinned
by test_data_agent_passes_without_code_intel + test_pure_knowledge_passes, and is
mutation-provable: forcing code-intel to be required unconditionally turns those
green tests RED.

Key properties tested:
- six-section skeleton + 4 non-placeholder docs = PASS
- governed_assets parsed WITHOUT core.ddd_bindings.load_bindings (which raises on
  the governed_assets-only bindings.yaml shape)
- code-intel.json required ONLY when a kind=code-repo governed asset exists; absent
  code-repo asset → N/A (never FAIL) — the XG constraint
- a code-repo asset with no code-intel.json → PENDING, never FAIL (Gate-1 refinement)
- missing DDD doc / placeholder doc → FAIL naming the doc
- broken bindings.yaml → fail-open (classifies no-repo, never crashes)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Import the gate under test (same dir).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify_ddd_complete as gate  # noqa: E402


# ── Synthetic DDD factory ────────────────────────────────────────────────────

CANONICAL_DOCS = ("PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md")


def _make_ddd(
    root: Path,
    name: str = "TestDDD",
    *,
    docs: bool = True,
    doc_placeholder: bool = False,
    aim: dict | None = None,
    bindings_yaml: str | None = None,
    skills: list[str] | None = None,
    code_intel: bool = False,
) -> Path:
    """Build a synthetic DDD dir. Returns the project dir."""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    # ② KNOWLEDGE — 4 docs
    if docs:
        for doc in CANONICAL_DOCS:
            if doc_placeholder:
                (d / doc).write_text(f"# {name} — {doc}\n\n_What is this?_\n")
            else:
                (d / doc).write_text(
                    f"# {name} — {doc}\n\nReal content line one describing the domain.\n"
                    "A second substantive paragraph so it is not a placeholder stub.\n"
                )
    # ① IDENTITY manifests
    (d / ".project.json").write_text(json.dumps({"name": name, "id": "x", "ddd_spec_version": "1.0"}))
    (d / "AGENTS.md").write_text(f"# {name} — DDD Agent Guide\n\nreal readme content.\n")
    (d / ".crux_template.md").write_text("template\n")
    if aim is None:
        aim = {"name": name, "ddd_spec_version": "1.0", "plugins": {"native_skills": []}}
    (d / "aim.json").write_text(json.dumps(aim))
    # ③ GATES + ④ CAPABILITIES + ⑥ REFRESHER dirs
    (d / "gates").mkdir(exist_ok=True)
    (d / "Knowledge").mkdir(exist_ok=True)
    (d / "REFRESHER.md").write_text("# Refresher\n")
    skdir = d / "skills"
    skdir.mkdir(exist_ok=True)
    for s in (skills or []):
        (skdir / s).mkdir(exist_ok=True)
        (skdir / s / "SKILL.md").write_text(f"---\nname: {s}\ndescription: test\n---\n# {s}\n")
    # ⑤ DELIVERY CONTRACT
    if bindings_yaml is not None:
        (d / "bindings.yaml").write_text(bindings_yaml)
    # ⑥ derived projection
    if code_intel:
        (d / "code-intel.json").write_text(json.dumps({"modules": []}))
    return d


def _status_of(report: dict, check_name_substr: str) -> str:
    """Find the status of the first check whose name contains the substring."""
    for c in report["checks"]:
        if check_name_substr.lower() in c["name"].lower():
            return c["status"]
    raise AssertionError(f"no check matching {check_name_substr!r} in {[c['name'] for c in report['checks']]}")


# ── Core invariant: data-agent / pure-knowledge NOT failed for missing code-intel ──

def test_data_agent_passes_without_code_intel(tmp_path):
    """A data-agent brain (skill-set + data-source, NO code-repo) PASSES; code-intel = N/A.

    THE load-bearing test. Mutating the gate to require code-intel unconditionally
    turns this RED (mutation-proven).
    """
    bindings = """
ddd_spec_version: "1.0"
brain_kind: data-agent
governed_assets:
  - kind: skill-set
    name: my-skills
    members: [s_foo, s_bar]
  - kind: data-source
    name: my-data
jobs: []
"""
    d = _make_ddd(tmp_path, "DataAgent", bindings_yaml=bindings, skills=["s_foo", "s_bar"],
                  aim={"name": "DataAgent", "plugins": {"native_skills": [], "domain_skills": ["s_foo", "s_bar"]}})
    report = gate.verify_project(d)
    assert _status_of(report, "code-intel") == "N/A"
    assert report["overall"] == "PASS", report
    assert report["exit_code"] == 0


def test_pure_knowledge_passes(tmp_path):
    """A 0-asset pure-knowledge brain (no bindings.yaml) is COMPLETE; code-intel = N/A."""
    d = _make_ddd(tmp_path, "PureKnowledge", bindings_yaml=None)
    report = gate.verify_project(d)
    assert _status_of(report, "code-intel") == "N/A"
    assert report["overall"] == "PASS", report
    assert report["exit_code"] == 0


# ── code-repo asset logic (PENDING, never FAIL) ──────────────────────────────

def test_code_repo_asset_without_code_intel_is_pending_not_fail(tmp_path):
    """A kind=code-repo governed asset with NO code-intel.json → PENDING, never FAIL."""
    bindings = """
ddd_spec_version: "1.0"
brain_kind: code-repo
governed_assets:
  - kind: code-repo
    name: my-service
jobs: []
"""
    d = _make_ddd(tmp_path, "CodeRepoBrain", bindings_yaml=bindings, code_intel=False)
    report = gate.verify_project(d)
    assert _status_of(report, "code-intel") == "PENDING"
    # PENDING must NOT fail the overall gate.
    assert report["overall"] == "PASS", report
    assert report["exit_code"] == 0


def test_code_repo_asset_with_code_intel_passes(tmp_path):
    """A kind=code-repo asset WITH code-intel.json → PASS."""
    bindings = """
ddd_spec_version: "1.0"
governed_assets:
  - kind: code-repo
    name: my-service
"""
    d = _make_ddd(tmp_path, "CodeRepoDone", bindings_yaml=bindings, code_intel=True)
    report = gate.verify_project(d)
    assert _status_of(report, "code-intel") == "PASS"
    assert report["overall"] == "PASS"


def test_code_intel_found_recursively(tmp_path):
    """code-intel.json nested under .artifacts/ still counts (location varies — Gate-1 P4)."""
    bindings = 'governed_assets:\n  - kind: code-repo\n    name: svc\n'
    d = _make_ddd(tmp_path, "NestedCI", bindings_yaml=bindings, code_intel=False)
    nested = d / ".artifacts" / "ai-ready" / ".ai-ready"
    nested.mkdir(parents=True)
    (nested / "code-intel.json").write_text("{}")
    report = gate.verify_project(d)
    assert _status_of(report, "code-intel") == "PASS", report


# ── Structural checks (all DDDs) ─────────────────────────────────────────────

def test_missing_doc_fails_naming_it(tmp_path):
    d = _make_ddd(tmp_path, "MissingDoc")
    (d / "TECH.md").unlink()
    report = gate.verify_project(d)
    assert report["overall"] == "FAIL"
    assert report["exit_code"] != 0
    knowledge = _status_of(report, "knowledge")
    assert knowledge == "FAIL"
    # the specific missing doc is named in the detail
    kdetail = next(c["detail"] for c in report["checks"] if "knowledge" in c["name"].lower())
    assert "TECH.md" in kdetail


def test_placeholder_doc_fails(tmp_path):
    d = _make_ddd(tmp_path, "Placeholder", doc_placeholder=True)
    report = gate.verify_project(d)
    assert _status_of(report, "knowledge") == "FAIL"
    assert report["overall"] == "FAIL"


def test_skills_mismatch_aim_fails(tmp_path):
    """aim.json declares a domain skill that is not present in skills/ → FAIL."""
    d = _make_ddd(
        tmp_path, "SkillDrift",
        aim={"name": "SkillDrift", "plugins": {"native_skills": [], "domain_skills": ["s_ghost"]}},
        skills=[],  # s_ghost declared but no dir
    )
    report = gate.verify_project(d)
    assert _status_of(report, "capabilities") == "FAIL"
    assert report["overall"] == "FAIL"


def test_skills_match_aim_passes(tmp_path):
    d = _make_ddd(
        tmp_path, "SkillOk",
        aim={"name": "SkillOk", "plugins": {"native_skills": [], "domain_skills": ["s_real"]}},
        skills=["s_real"],
    )
    report = gate.verify_project(d)
    assert _status_of(report, "capabilities") == "PASS"


def test_missing_manifest_fails(tmp_path):
    d = _make_ddd(tmp_path, "NoAim")
    (d / "aim.json").unlink()
    report = gate.verify_project(d)
    assert _status_of(report, "identity") == "FAIL"
    assert report["overall"] == "FAIL"


# ── Fail-open on broken input ────────────────────────────────────────────────

def test_broken_bindings_yaml_fail_open(tmp_path):
    """A syntactically broken bindings.yaml must not crash the gate — treated no-repo."""
    d = _make_ddd(tmp_path, "BrokenYaml", bindings_yaml="governed_assets: [ this is : not : valid\n")
    report = gate.verify_project(d)  # must not raise
    # broken bindings → no governed assets discernible → code-intel N/A, still structurally checkable
    assert _status_of(report, "code-intel") == "N/A"
    assert report["overall"] in ("PASS", "FAIL")  # decided by the STRUCTURAL checks, not a crash


# ── Gate-2 adversarial regression tests (D1/D2/C1/C2) ────────────────────────

@pytest.mark.parametrize("bad_plugins", [["a"], "str", 123, None])
def test_malformed_plugins_does_not_crash(tmp_path, bad_plugins):
    """Gate-2 D1 (CRITICAL): aim.json `plugins` as a non-dict must NOT crash the gate.

    Before the fix, `aim.get("plugins", {}).get(...)` raised AttributeError on a
    list/str/int plugins → verify_project died with an uncaught traceback (no exit
    code, no report), breaking the headline fail-open guarantee. Now it degrades.
    """
    d = _make_ddd(tmp_path, "BadPlugins", aim={"name": "BadPlugins", "plugins": bad_plugins})
    report = gate.verify_project(d)  # MUST NOT raise
    # capabilities degrades to PASS (no declared skills discernible) — never a crash
    assert _status_of(report, "capabilities") == "PASS", report
    assert report["exit_code"] in (0, 1)


def test_domain_skills_as_string_does_not_iterate_chars(tmp_path):
    """Gate-2 D2 (HIGH): domain_skills as a string must not iterate characters.

    Before the fix, `for s in "s_foo"` iterated 's','_','f','o','o' → a nonsense
    FAIL naming phantom single-char skills. Now a non-list domain_skills is ignored.
    """
    d = _make_ddd(
        tmp_path, "StrSkills",
        aim={"name": "StrSkills", "plugins": {"domain_skills": "s_foo"}},
        skills=[],
    )
    report = gate.verify_project(d)
    cap = next(c for c in report["checks"] if "capabilities" in c["name"].lower())
    assert cap["status"] == "PASS", report
    # detail must NOT contain phantom single-char skill names
    assert ", _, " not in cap["detail"] and "s, _, f" not in cap["detail"]


def test_concise_one_paragraph_doc_is_not_placeholder(tmp_path):
    """Gate-2 C1 (HIGH): a substantive doc with ONE prose line must PASS, not FAIL.

    Before the fix, `< 2 real lines` false-FAILed a concise doc. Now content VOLUME
    (chars) decides, so one dense paragraph passes.
    """
    d = _make_ddd(tmp_path, "ConciseDocs")
    one_liner = (
        "# ConciseDocs — {doc}\n\n"
        "This project governs the widget pipeline and its three downstream consumers, "
        "documented here in a single dense paragraph that is unmistakably real content.\n"
    )
    for doc in CANONICAL_DOCS:
        (d / doc).write_text(one_liner.format(doc=doc))
    report = gate.verify_project(d)
    assert _status_of(report, "knowledge") == "PASS", report


def test_prose_mentioning_marker_phrase_is_not_placeholder(tmp_path):
    """Gate-2 C2 (MEDIUM): real prose that merely mentions a marker phrase mid-line
    must NOT be counted as a placeholder line (markers anchor to line start)."""
    d = _make_ddd(tmp_path, "MarkerProse")
    body = (
        "# MarkerProse — {doc}\n\n"
        "The naming convention (see _e.g. the examples below) is enforced across all "
        "modules, and recurring problems to watch for are tracked in the audit log.\n"
        "A second real line describing the domain in concrete, substantive terms.\n"
    )
    for doc in CANONICAL_DOCS:
        (d / doc).write_text(body.format(doc=doc))
    report = gate.verify_project(d)
    assert _status_of(report, "knowledge") == "PASS", report


def test_genuine_stub_still_fails(tmp_path):
    """Guard against over-correction: a genuine scaffold stub must STILL FAIL."""
    d = _make_ddd(tmp_path, "RealStub", doc_placeholder=True)
    report = gate.verify_project(d)
    assert _status_of(report, "knowledge") == "FAIL", report


# ── Real-project dog-food ────────────────────────────────────────────────────

REAL_PROJECTS = Path.home() / ".swarm-ai" / "SwarmWS" / "Projects"


@pytest.mark.skipif(not (REAL_PROJECTS / "IVTHub").exists(), reason="IVTHub project not present")
def test_dogfood_ivthub_passes_code_intel_na(tmp_path):
    """DOG-FOOD: real IVTHub (data-source + skill-set, no bound code-repo) → PASS, code-intel N/A."""
    report = gate.verify_project(REAL_PROJECTS / "IVTHub")
    assert _status_of(report, "code-intel") == "N/A", report
    assert report["overall"] == "PASS", report


@pytest.mark.skipif(not (REAL_PROJECTS / "CMHK_SalesIntel").exists(), reason="CMHK project not present")
def test_dogfood_cmhk_passes_code_intel_na(tmp_path):
    report = gate.verify_project(REAL_PROJECTS / "CMHK_SalesIntel")
    assert _status_of(report, "code-intel") == "N/A", report
    assert report["overall"] == "PASS", report
