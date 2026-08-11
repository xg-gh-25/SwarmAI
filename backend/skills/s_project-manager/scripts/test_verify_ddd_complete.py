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
    numbered_layout: bool = False,
) -> Path:
    """Build a synthetic DDD dir. Returns the project dir.

    numbered_layout=False (default) → OLD bare layout: the 4 docs live at the
    project root. numbered_layout=True → NEW six-section layout: the 4 docs live
    under 2-understanding/ (the shape s_project-manager CREATE actually scaffolds
    since commit ad7f6623). The gate MUST resolve both — regression coverage for
    the _check_knowledge root-only probe bug that false-FAILed every real DDD.
    """
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    # ② KNOWLEDGE — 4 docs, at root (old) or under 2-understanding/ (new)
    doc_dir = (d / "2-understanding") if numbered_layout else d
    if docs:
        doc_dir.mkdir(parents=True, exist_ok=True)
        for doc in CANONICAL_DOCS:
            if doc_placeholder:
                (doc_dir / doc).write_text(f"# {name} — {doc}\n\n_What is this?_\n")
            else:
                (doc_dir / doc).write_text(
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


def _detail_of(report: dict, check_name_substr: str) -> str:
    """Find the detail string of the first check whose name contains the substring."""
    for c in report["checks"]:
        if check_name_substr.lower() in c["name"].lower():
            return c["detail"]
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


def test_delivery_detail_shows_kind_and_name(tmp_path):
    """⑤ detail lists each asset as kind:name — two distinct code-repos are
    distinguishable, not a phantom 'code-repo, code-repo' duplicate."""
    bindings = """
ddd_spec_version: "1.0"
brain_kind: knowledge-primary
governed_assets:
  - kind: code-repo
    name: adlc-workflows
  - kind: code-repo
    name: GCRAIDLCPreset
"""
    d = _make_ddd(tmp_path, "TwoRepos", bindings_yaml=bindings, code_intel=True)
    detail = _detail_of(gate.verify_project(d), "Delivery")
    assert "code-repo:adlc-workflows" in detail, detail
    assert "code-repo:GCRAIDLCPreset" in detail, detail
    # the old kind-only phantom-duplicate must be gone
    assert "code-repo, code-repo" not in detail, detail


def test_delivery_detail_falls_back_to_kind_when_name_absent(tmp_path):
    """An asset with no name field falls back to bare kind (no dangling colon)."""
    bindings = """
ddd_spec_version: "1.0"
governed_assets:
  - kind: skill-set
    name: my-skills
  - kind: data-source
"""
    d = _make_ddd(tmp_path, "MixedNames", bindings_yaml=bindings, skills=["s_foo"],
                  aim={"name": "MixedNames", "plugins": {"native_skills": [], "domain_skills": ["s_foo"]}})
    detail = _detail_of(gate.verify_project(d), "Delivery")
    assert "skill-set:my-skills" in detail, detail
    assert "data-source" in detail and "data-source:" not in detail, detail


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


# ── Numbered-layout (six-section) resolution — regression for the root-only probe ──
# commit ad7f6623 moved the 4 docs under 2-understanding/; _check_knowledge kept
# probing the root and false-FAILed every real DDD. These pin BOTH layouts so the
# regression can't return. Mutation-provable: reverting _check_knowledge to
# `(d / doc)` turns test_numbered_layout_docs_pass RED while the old-layout tests
# stay green — proving the coverage actually exercises the new path.

def test_numbered_layout_docs_pass(tmp_path):
    """4 substantive docs under 2-understanding/ (the shape CREATE scaffolds) → ② PASS."""
    d = _make_ddd(tmp_path, "NumberedDDD", numbered_layout=True)
    # sanity: docs really are under 2-understanding/, NOT at root
    assert (d / "2-understanding" / "PRODUCT.md").exists()
    assert not (d / "PRODUCT.md").exists()
    report = gate.verify_project(d)
    assert _status_of(report, "knowledge") == "PASS", report


def test_numbered_layout_missing_doc_still_fails_naming_it(tmp_path):
    """Fail-closed is preserved on the new layout: a truly-missing doc still FAILs."""
    d = _make_ddd(tmp_path, "NumberedMissing", numbered_layout=True)
    (d / "2-understanding" / "TECH.md").unlink()
    report = gate.verify_project(d)
    assert _status_of(report, "knowledge") == "FAIL"
    assert report["overall"] == "FAIL"
    kdetail = next(c["detail"] for c in report["checks"] if "knowledge" in c["name"].lower())
    assert "TECH.md" in kdetail


def test_numbered_layout_placeholder_doc_fails(tmp_path):
    """A placeholder doc under 2-understanding/ still FAILs (no false-green on new layout)."""
    d = _make_ddd(tmp_path, "NumberedPlaceholder", numbered_layout=True, doc_placeholder=True)
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


# ── Real-project dog-food (environment-robust) ───────────────────────────────

REAL_PROJECTS = Path.home() / ".swarm-ai" / "SwarmWS" / "Projects"


def _real_ddd_projects() -> list[Path]:
    """Real DDD project dirs present in THIS workspace.

    Projects/ is gitignored + machine-specific, so both the SET of projects and
    each project's asset shape differ per host (e.g. IVTHub is a pure data-agent
    on one machine but has bound a code-repo on another). The dogfood test below
    must therefore assert only PORTABLE properties — never a hardcoded per-project
    shape (the old test asserted "IVTHub → code-intel N/A" and false-failed the
    moment IVTHub bound a code-repo asset).
    """
    if not REAL_PROJECTS.is_dir():
        return []
    return [p for p in sorted(REAL_PROJECTS.iterdir())
            if p.is_dir() and (p / "aim.json").exists()]


@pytest.mark.skipif(not _real_ddd_projects(), reason="no real DDD projects in this workspace")
@pytest.mark.parametrize("project_dir", _real_ddd_projects(), ids=lambda p: p.name)
def test_dogfood_real_project_code_intel_consistent(project_dir):
    """DOG-FOOD (environment-robust): drive the REAL gate against whatever real DDDs
    exist on THIS machine, asserting only properties that hold for ANY asset shape.

    This exercises the gate end-to-end on real bindings.yaml files / real on-disk
    layouts (the value of a dogfood) WITHOUT encoding a machine-specific assumption.
    Portable properties (true for every DDD regardless of its governed assets):
      1. the gate never raises and returns a well-formed report;
      2. the ⑥ code-intel status is a valid status and is NEVER FAIL — the
         load-bearing invariant: a brain is never failed for its projection;
      3. that status is CONSISTENT with the project's REAL governed-asset inventory:
           governs a code-repo asset → PASS or PENDING (never N/A)
           governs NO code-repo asset → N/A.
    Notably it does NOT assert overall==PASS: a genuinely half-built real project
    should not turn this test red — that is a real incompleteness, not a gate bug.
    """
    report = gate.verify_project(project_dir)  # 1. must not raise
    assert set(report) >= {"checks", "overall", "exit_code", "counts"}, report
    ci = _status_of(report, "code-intel")
    # 2. valid status, and code-intel is never itself a FAIL
    assert ci in (gate.STATUS_PASS, gate.STATUS_PENDING, gate.STATUS_NA), report
    # 3. consistent with the project's REAL governed-asset inventory
    assets = gate._governed_assets(gate._read_bindings_raw(project_dir))
    if gate._has_code_repo_asset(assets):
        assert ci in (gate.STATUS_PASS, gate.STATUS_PENDING), report
    else:
        assert ci == gate.STATUS_NA, report
