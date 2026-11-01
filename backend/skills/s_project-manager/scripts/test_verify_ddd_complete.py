"""Tests for verify_ddd_complete.py — the asset-aware DDD completeness gate.

Methodology: each test drives the REAL gate (verify_project) against a synthetic
DDD built in tmp_path (or a real project on disk), asserting the per-check status
AND the overall exit semantics. The load-bearing invariant — a data-agent /
pure-knowledge brain must NOT be failed for a missing code graph — is pinned
by test_data_agent_passes_without_code_intel + test_pure_knowledge_passes, and is
mutation-provable: forcing code-intel to be required unconditionally turns those
green tests RED.

Key properties tested:
- six-section skeleton + 4 non-placeholder docs = PASS
- governed_assets parsed WITHOUT core.ddd_bindings.load_bindings (which raises on
  the governed_assets-only bindings.yaml shape)
- code graph required ONLY when a kind=code-repo governed asset exists; absent
  code-repo asset → N/A (never FAIL) — the asset-aware constraint
- a code-repo asset with no code graph → PENDING, never FAIL (Gate-1 refinement)
- missing DDD doc / placeholder doc → FAIL naming the doc
- broken bindings.yaml → fail-open (classifies no-repo, never crashes)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from unittest.mock import patch

# Import the gate under test (same dir).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify_ddd_complete as gate  # noqa: E402


# ── Synthetic DDD factory ────────────────────────────────────────────────────

CANONICAL_DOCS = ("PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md")


def _filled_doc(doc: str, name: str, *, skip: tuple[str, ...] = ()) -> str:
    """The skeleton of *doc* with one entry, of the type CDF writes there, under every
    section not in *skip*."""
    from core.ddd_entry_lifecycle import render_ddd_unit
    from core.ddd_skeleton import USER_ONLY_SECTIONS, default_type, render_skeleton
    out = []
    for line in render_skeleton(doc, name).splitlines():
        out.append(line)
        if line.startswith("## ") and line[3:].strip() not in skip:
            section = line[3:].strip()
            user_only = section in USER_ONLY_SECTIONS.get(doc, ())
            out.append(render_ddd_unit(
                {"type": "model" if user_only else default_type(doc, section),
                 "title": f"{section} of {name}",
                 "what": f"What {name} records under {section}.", "sources": ["README.md"]},
                date_str="2026-10-27", taint="external"))
    return "\n".join(out) + "\n"


@pytest.fixture(autouse=True)
def _own_bindings_root(tmp_path, monkeypatch):
    """The local-roots registry and graphs of these tests live in their tmp dir."""
    monkeypatch.setattr("core.ddd_bindings._default_bindings_root",
                        lambda: tmp_path / "bindings-root")
    from core.code_intel import invalidate_cache
    invalidate_cache()
    yield
    invalidate_cache()


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
    project_agent: str | None = "schema: 2\nstatus: draft\nplacements: [chat-tab]\n"
                                "identity: {summary: a test brain}\n"
                                "intake: {handles: [answer a question about the brain]}\n"
                                "modes:\n  - {id: research, when: a question, profile: research}\n"
                                "default_mode: research\n",
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
            if doc_placeholder:  # a fresh brain: the untouched skeleton
                from core.ddd_skeleton import render_skeleton
                (doc_dir / doc).write_text(render_skeleton(doc, name))
            else:
                (doc_dir / doc).write_text(_filled_doc(doc, name))
    # ① IDENTITY manifests
    (d / ".project.json").write_text(json.dumps({"name": name, "id": "x", "ddd_spec_version": "1.0"}))
    (d / "AGENTS.md").write_text(f"# {name} — DDD Agent Guide\n\nreal readme content.\n")
    (d / ".crux_template.md").write_text("template\n")
    if aim is None:
        aim = {"name": name, "ddd_spec_version": "1.0", "plugins": {"native_skills": []}}
    aim = {"description": f"The {name} test Brain: what it knows, for whom.", **aim}
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
    # ⑤ DELIVERY CONTRACT + the Project Agent (a draft unless the test says otherwise)
    if project_agent is not None:
        (d / "project-agent.yaml").write_text(project_agent)
    if bindings_yaml is not None:
        (d / "bindings.yaml").write_text(bindings_yaml)
    # ⑥ the derived code graph: a real one, built from a local checkout a person
    # confirmed for this brain (the one ownership declaration for a local repo)
    if code_intel:
        import subprocess
        from core.code_intel import local_roots
        from core.code_intel.graph_store import GraphStore
        repo = d / "repo"
        repo.mkdir(exist_ok=True)
        (repo / "m.py").write_text("def f():\n    return 1\n")
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        local_roots.declare(d.name, repo)
        graph = GraphStore(local_roots.db_path(repo))
        graph.upsert_nodes([{"id": "m.py::f", "file_path": "m.py", "node_type": "function",
                             "name": "f", "line_start": 1, "line_end": 2, "language": "python"}])
        graph.set_meta("repo_root", str(repo))
        graph.close()
    return d


def _graph_db(d: Path) -> Path:
    """The graph db ``_make_ddd(code_intel=True)`` built for brain *d*."""
    from core.code_intel import local_roots
    return local_roots.db_path(d / "repo")


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


@pytest.mark.parametrize("desc", [None, "", "DDD package for X (six-section canonical structure)."])
def test_a_brain_without_a_real_description_fails_identity(tmp_path, desc):
    """The create-time placeholder (or none) tells the Brain Hub card nothing: the setup
    gate asks for one sentence on what the Brain is."""
    aim = {"name": "X", "ddd_spec_version": "1.0", "plugins": {"native_skills": []}, "description": desc}
    d = _make_ddd(tmp_path, "X", bindings_yaml=None, aim=aim)
    report = gate.verify_project(d)
    assert _status_of(report, "Identity") == "FAIL", report
    assert "description" in _detail_of(report, "Identity")


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
    """A kind=code-repo asset WITH a code graph → PASS."""
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


def test_code_intel_json_is_not_a_code_graph(tmp_path):
    """code-intel.json left the product (design 2026-10-27 §5.9): it no longer counts."""
    bindings = 'governed_assets:\n  - kind: code-repo\n    name: svc\n'
    d = _make_ddd(tmp_path, "OldJson", bindings_yaml=bindings, code_intel=False)
    (d / "code-intel.json").write_text("{}")
    report = gate.verify_project(d)
    assert _status_of(report, "code-intel") == "PENDING", report


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
    assert _status_of(report, "knowledge/content") == "FAIL"
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
    assert _status_of(report, "knowledge/content") == "FAIL"
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


# ── ② against the skeleton (design 2026-10-27 §5.7, invariant I2) ────────────

def test_fresh_skeleton_fails_content_for_product_tech_improvement(tmp_path):
    """I2: the untouched skeleton passes structure but never passes content (the old
    marker heuristic called a fresh PRODUCT and TECH 'filled')."""
    d = _make_ddd(tmp_path, "Fresh", doc_placeholder=True)
    report = gate.verify_project(d)
    assert _status_of(report, "knowledge/structure") == "PASS"
    assert _status_of(report, "knowledge/content") == "FAIL"
    detail = _detail_of(report, "knowledge/content")
    for doc in ("PRODUCT.md", "TECH.md", "IMPROVEMENT.md"):
        assert doc in detail
    assert "PROJECT.md" not in detail, "PROJECT may stay as the skeleton"


def test_prose_without_typed_entries_fails_content(tmp_path):
    d = _make_ddd(tmp_path, "Prose")
    for doc in CANONICAL_DOCS:
        from core.ddd_skeleton import render_skeleton
        (d / doc).write_text(render_skeleton(doc, "Prose") + "\nA long paragraph of real prose "
                             "that describes the domain in concrete terms but is no entry.\n")
    report = gate.verify_project(d)
    assert _status_of(report, "knowledge/content") == "FAIL"
    assert "no typed entry" in _detail_of(report, "knowledge/content")


def test_missing_skeleton_section_fails_structure_naming_it(tmp_path):
    d = _make_ddd(tmp_path, "Gappy")
    tech = d / "TECH.md"
    tech.write_text(tech.read_text().replace("## Runtime Traps", "## Something Else"))
    report = gate.verify_project(d)
    assert _status_of(report, "knowledge/structure") == "FAIL"
    detail = _detail_of(report, "knowledge/structure")
    assert "TECH.md: Runtime Traps" in detail and "ddd-skeleton-align" in detail


def test_heading_compare_ignores_case_and_spacing(tmp_path):
    d = _make_ddd(tmp_path, "Spacing")
    tech = d / "TECH.md"
    tech.write_text(tech.read_text().replace("## Key Subsystems", "## key   subsystems"))
    assert _status_of(gate.verify_project(d), "knowledge/structure") == "PASS"


def test_empty_product_section_needs_a_gap(tmp_path):
    from core.ddd_entry_lifecycle import render_ddd_unit
    d = _make_ddd(tmp_path, "Honest")
    (d / "PRODUCT.md").write_text(_filled_doc("PRODUCT.md", "Honest", skip=("Non-Goals",)))
    report = gate.verify_project(d)
    assert _status_of(report, "knowledge/content") == "FAIL"
    assert "Non-Goals" in _detail_of(report, "knowledge/content")
    imp = d / "IMPROVEMENT.md"
    imp.write_text(imp.read_text().rstrip() + "\n" + render_ddd_unit(
        {"type": "gap", "title": "Non-Goals: no evidence yet",
         "what": "The repo and the person's answers say nothing about non-goals."},
        date_str="2026-10-27") + "\n")
    assert _status_of(gate.verify_project(d), "knowledge/content") == "PASS"


def test_empty_code_graph_is_pending(tmp_path):
    from core.code_intel.graph_store import GraphStore
    bindings = "governed_assets:\n  - kind: code-repo\n    name: svc\n"
    d = _make_ddd(tmp_path, "EmptyGraph", bindings_yaml=bindings, code_intel=True)
    db = _graph_db(d)
    db.unlink()
    GraphStore(db).close()
    report = gate.verify_project(d)
    assert _status_of(report, "code-intel") == "PENDING"
    assert "empty" in _detail_of(report, "code-intel")


def test_foreign_code_graph_is_pending(tmp_path):
    bindings = "governed_assets:\n  - kind: code-repo\n    name: svc\n"
    from core.code_intel.graph_store import GraphStore
    d = _make_ddd(tmp_path, "Foreign", bindings_yaml=bindings, code_intel=True)
    other = tmp_path / "someone_elses_repo"
    other.mkdir()
    graph = GraphStore(_graph_db(d))
    graph.set_meta("repo_root", str(other))
    graph.close()
    report = gate.verify_project(d)
    assert _status_of(report, "code-intel") == "PENDING"
    assert "does not own" in _detail_of(report, "code-intel")


def test_a_bound_repo_counts_as_a_code_repo_asset(tmp_path):
    bindings = ("bindings:\n  - repo: lib\n    kind: external\n    clone: https://x/y.git\n"
                "    delivery_contract:\n      remote_kind: github-pr\n      branch: main\n"
                "      review_path: pr\n      auto_send: manual\n")
    d = _make_ddd(tmp_path, "Bound", bindings_yaml=bindings)
    assert _status_of(gate.verify_project(d), "code-intel") == "PENDING"


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


def test_untyped_bold_bullet_is_not_an_entry(tmp_path):
    """A leftover '- **Language:** _e.g._' line from the old inline template is prose."""
    from core.ddd_skeleton import render_skeleton
    d = _make_ddd(tmp_path, "OldTech")
    (d / "TECH.md").write_text(render_skeleton("TECH.md", "OldTech")
                               + "\n- **Language:** _e.g., Python 3.12_\n")
    report = gate.verify_project(d)
    assert "TECH.md section(s) with no entry and no gap" in _detail_of(report, "knowledge/content")


def test_structure_detail_names_the_heading_to_rename(tmp_path):
    d = _make_ddd(tmp_path, "Rename")
    tech = d / "TECH.md"
    tech.write_text(tech.read_text().replace("## Key Subsystems", "## Key Subsystems (shape)"))
    detail = _detail_of(gate.verify_project(d), "knowledge/structure")
    assert "rename heading 'Key Subsystems (shape)' to 'Key Subsystems'" in detail


def test_portable_run_keeps_both_check_names_and_catches_a_stub(tmp_path, monkeypatch):
    import builtins
    real_import = builtins.__import__

    def no_core(name, *a, **k):
        if name == "core" or name.startswith("core."):
            raise ImportError(name)
        return real_import(name, *a, **k)

    stub = _make_ddd(tmp_path, "Stub", doc_placeholder=True)
    good = _make_ddd(tmp_path, "Good")
    monkeypatch.setattr(builtins, "__import__", no_core)
    r_stub, r_good = gate.verify_project(stub), gate.verify_project(good)
    monkeypatch.setattr(builtins, "__import__", real_import)
    assert _status_of(r_stub, "knowledge/structure") == "PENDING"
    assert _status_of(r_stub, "knowledge/content") == "FAIL"
    assert _status_of(r_good, "knowledge/content") == "PASS"


def test_code_graph_check_is_read_only(tmp_path):
    bindings = "governed_assets:\n  - kind: code-repo\n    name: svc\n"
    d = _make_ddd(tmp_path, "RO", bindings_yaml=bindings, code_intel=True)
    db = _graph_db(d)
    before = db.read_bytes()
    with patch("core.code_intel.graph_store.GraphStore",
               side_effect=AssertionError("opened read-write")):
        assert _status_of(gate.verify_project(d), "code-intel") == "PASS"
    assert db.read_bytes() == before


@pytest.mark.parametrize("current,report_overall,expected", [
    ("setting-up", "FAIL", "setting-up"),
    ("setting-up", "PASS", "ready"),
    ("ready", "FAIL", "ready"),        # a later check never un-births a Brain
    (None, "FAIL", None),              # a Brain made before setup state stays without one
])
def test_record_never_downgrades_a_ready_brain(tmp_path, current, report_overall, expected):
    sj = tmp_path / ".artifacts" / "setup.json"
    if current:
        sj.parent.mkdir(parents=True)
        sj.write_text(json.dumps({"state": current}))
    gate.record_setup_state(tmp_path, {"overall": report_overall, "counts": {}})
    got = json.loads(sj.read_text())["state"] if sj.exists() else None
    assert got == expected


def test_bindings_repo_does_not_change_the_delivery_detail(tmp_path):
    bindings = ("bindings:\n  - repo: lib\n    kind: external\n    clone: https://x/y.git\n"
                "    delivery_contract:\n      remote_kind: github-pr\n      branch: main\n"
                "      review_path: pr\n      auto_send: manual\n")
    d = _make_ddd(tmp_path, "Deliv", bindings_yaml=bindings)
    assert "governed asset(s)" not in _detail_of(gate.verify_project(d), "delivery")
