"""bootstrap-apply: a newborn brain's first entries, checked before they are written.

Design docs/design/2026-10-27-new-brain-initial-ddd.md §5.4–§5.6, invariants I4 and
I10 (§10). The model decides what to write; this code decides whether it may be
written, where, in what shape, and on what evidence.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

from core import ddd_skeleton as sk
from core.brain_bootstrap import apply as ap
from core.brain_bootstrap import survey
from core.ddd_entry_lifecycle import parse_entries
from core.ddd_paths import ddd_path
from core.ddd_setup_state import write_setup_state


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=t",
                    "-c", "commit.gpgsign=false", "-C", str(root), *args],
                   check=True, capture_output=True)


@pytest.fixture
def brain(tmp_path: Path, monkeypatch):
    """A freshly provisioned brain (setting-up) owning one small repo."""
    repo = tmp_path / "acme"
    (repo / "docs").mkdir(parents=True)
    (repo / "README.md").write_text("# Acme\n\nSigns tokens.\n\n## Usage\n\nCall sign().\n", encoding="utf-8")
    (repo / "docs" / "design.md").write_text("# Design\n\nHMAC with SHA-1 by default.\n", encoding="utf-8")
    (repo / "src.py").write_text("def sign(): pass\n", encoding="utf-8")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    from core.swarm_workspace_manager import SwarmWorkspaceManager
    proj = tmp_path / "Projects" / "Acme"
    proj.mkdir(parents=True)
    # A private loop, not asyncio.run(): asyncio.run() sets the current event loop to
    # None when it finishes, and later tests in the same process that use the current
    # loop (test_chat_scenarios_e2e) then fail with "no current event loop".
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(
            SwarmWorkspaceManager().provision_project_ddd("Acme", workspace_path=str(tmp_path)))
    finally:
        loop.close()
    write_setup_state(proj, "setting-up")
    monkeypatch.setattr(survey, "owned_repos",
                        lambda d: [{"name": "acme", "root": str(repo.resolve()), "db_path": None}])
    return proj


def _run(proj: Path, entries: list, **kw) -> dict:
    p = proj / ap.ENTRIES_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(entries), encoding="utf-8")
    return ap.apply_entries(proj, **kw)


GOOD = [
    {"kind": "cognition", "type": "principle", "section": "Vision",
     "title": "Acme signs data so it can be trusted later",
     "what": "A library for signing payloads with a shared secret.", "sources": ["README.md#Acme"]},
    {"kind": "cognition", "type": "pitfall", "section": "Runtime Traps",
     "title": "Changing the default digest invalidates issued tokens",
     "what": "The signer defaults to SHA-1; a different digest rejects every old signature.",
     "applicability": "upgrading or configuring the digest", "sources": ["docs/design.md"]},
    {"kind": "cognition", "type": "decision", "section": "Recent Decisions",
     "title": "Keep HMAC as the signing primitive", "what": "Signatures are HMAC.",
     "why": "symmetric keys, no PKI", "rejected_alternatives": "none", "sources": ["user"]},
    {"kind": "cognition", "type": "process", "section": "Conventions",
     "title": "Run the tests with pytest", "what": "The suite is pytest.",
     "how": "pytest", "sources": ["src.py:1"]},
    {"kind": "gap", "section": "What to Watch For",
     "title": "Non-Goals: no evidence yet", "what": "Nothing in the repo says what Acme will not do.",
     "proposed_next": "ask the maintainer"},
]


# ── I4: what is refused ──────────────────────────────────────────────────────

@pytest.mark.parametrize("entry, reason", [
    ({"kind": "cognition", "type": "decision", "section": "Recent Decisions", "title": "t",
      "what": "w", "rejected_alternatives": "x", "sources": ["user"]}, "needs why"),
    ({"kind": "cognition", "type": "decision", "section": "Recent Decisions", "title": "t",
      "what": "w", "why": "y", "sources": ["user"]}, "needs rejected_alternatives"),
    ({"kind": "cognition", "type": "pitfall", "section": "Runtime Traps", "title": "t",
      "what": "w", "sources": ["user"]}, "needs applicability"),
    ({"kind": "cognition", "type": "process", "section": "Conventions", "title": "t",
      "what": "w", "sources": ["user"]}, "needs how"),
    ({"kind": "cognition", "type": "model", "section": "Vision", "title": "t",
      "what": "w", "sources": ["user"]}, "does not belong"),
    ({"kind": "cognition", "type": "guideline", "section": "Current Focus", "title": "t",
      "what": "w", "sources": ["user"]}, "live state"),
    ({"kind": "cognition", "type": "guideline", "section": "No Such", "title": "t",
      "what": "w", "sources": ["user"]}, "not a skeleton section"),
    ({"kind": "cognition", "type": "model", "section": "Key Subsystems", "title": "t",
      "what": "w", "sources": ["nope.md"]}, "not a tracked file"),
    ({"kind": "cognition", "type": "model", "section": "Key Subsystems", "title": "t",
      "what": "w", "sources": []}, "at least one source"),
    ({"kind": "cognition", "type": "model", "section": "Key Subsystems", "title": "t",
      "what": "w", "sources": ["url:ftp://x"]}, "http(s)"),
    ({"kind": "cognition", "type": "model", "section": "Key Subsystems", "title": "t" * 201,
      "what": "w", "sources": ["user"]}, "split the entry"),
    ({"kind": "cognition", "type": "model", "section": "Key Subsystems", "title": "t",
      "what": "w", "sources": ["user"], "rejected": "x"}, "unknown field"),
    ({"kind": "gap", "section": "Vision", "title": "t", "what": "w"}, "a gap goes to"),
    ({"kind": "cognition", "type": "model", "section": "Key Subsystems", "title": "a | b",
      "what": "w", "sources": ["user"]}, "title"),
])
def test_each_rule_refuses_with_its_reason(brain: Path, entry, reason) -> None:
    report = _run(brain, [entry])
    assert report["rejected"] and reason in report["rejected"][0]["reason"]
    assert not report["written"]


def test_only_a_newborn_brain_is_written(brain: Path) -> None:
    write_setup_state(brain, "ready")
    assert _run(brain, GOOD)["status"] == "refused"
    assert sk.is_skeleton("PRODUCT.md", ddd_path(brain, "PRODUCT.md").read_text(), "Acme")


def test_a_doc_someone_else_changed_is_left_alone(brain: Path) -> None:
    p = ddd_path(brain, "TECH.md")
    edited = p.read_text(encoding="utf-8") + "\nMy own notes.\n"
    p.write_text(edited, encoding="utf-8")
    report = _run(brain, GOOD)
    assert "TECH.md" in report["refused_docs"]
    assert p.read_text(encoding="utf-8") == edited
    assert report["written"].get("PRODUCT.md") == 1      # the other docs still go in


# ── what is written, and how it reads back ───────────────────────────────────

def test_written_entries_parse_back_with_their_taint(brain: Path) -> None:
    report = _run(brain, GOOD)
    assert report["rejected"] == [] and report["refused_docs"] == {}
    tech = parse_entries(ddd_path(brain, "TECH.md").read_text(encoding="utf-8"))
    trap = next(e for e in tech if e.title.startswith("Changing the default digest"))
    assert trap.entry_type == "pitfall" and trap.section == "Runtime Traps"
    assert trap.source == "bootstrap" and trap.taint == "external"
    proj = parse_entries(ddd_path(brain, "PROJECT.md").read_text(encoding="utf-8"))
    dec = next(e for e in proj if e.section == "Recent Decisions")
    assert dec.taint == ""                                # the person's own answer
    imp = parse_entries(ddd_path(brain, "IMPROVEMENT.md").read_text(encoding="utf-8"))
    gap = next(e for e in imp if e.section == "What to Watch For")
    assert gap.entry_type == "guideline" and "ask the maintainer" in gap.raw_text
    applied = json.loads((brain / ap.APPLIED_FILE).read_text())
    assert {a["title"] for a in applied} >= {"Keep HMAC as the signing primitive"}
    assert next(a for a in applied if a["section"] == "Conventions")["sources"] == ["src.py:1"]


def _only_inserts(old: str, new: str) -> bool:
    import difflib
    ops = difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes()
    return all(tag in ("equal", "insert") for tag, *_ in ops)


def test_existing_bytes_never_change(brain: Path) -> None:
    before = {d: ddd_path(brain, d).read_bytes() for d in sk.DOCS}
    _run(brain, GOOD)
    for d, old in before.items():
        assert _only_inserts(old.decode(), ddd_path(brain, d).read_bytes().decode()), d


def test_a_rerun_writes_nothing_twice_and_a_later_run_appends(brain: Path) -> None:
    _run(brain, GOOD)
    snap = ddd_path(brain, "TECH.md").read_text(encoding="utf-8")
    again = _run(brain, GOOD)
    assert again["written"] == {} and again["already_present"] == len(GOOD)
    assert ddd_path(brain, "TECH.md").read_text(encoding="utf-8") == snap
    more = [{"kind": "cognition", "type": "model", "section": "Key Subsystems",
             "title": "The signer", "what": "sign() in src.py.", "sources": ["src.py"]}]
    assert _run(brain, more)["written"] == {"TECH.md": 1}   # earlier entries do not block


def test_entries_land_inside_their_section(brain: Path) -> None:
    _run(brain, GOOD)
    tech = ddd_path(brain, "TECH.md").read_text(encoding="utf-8")
    conv = tech.split("## Conventions", 1)[1].split("## Runtime Traps", 1)[0]
    assert "Run the tests with pytest" in conv
    assert "Changing the default digest" in tech.split("## Runtime Traps", 1)[1]


def test_dry_run_writes_nothing(brain: Path) -> None:
    before = ddd_path(brain, "PRODUCT.md").read_text(encoding="utf-8")
    report = _run(brain, GOOD, dry_run=True)
    assert report["written"]["PRODUCT.md"] == 1
    assert ddd_path(brain, "PRODUCT.md").read_text(encoding="utf-8") == before
    assert not (brain / ap.REJECTED_FILE).exists()


def test_the_report_names_what_needs_a_gap_and_asks_nothing(brain: Path) -> None:
    report = _run(brain, GOOD)
    # Vision has an entry, Non-Goals has a gap; Success Criteria has neither. Nobody is
    # asked: every PRODUCT and TECH section without evidence gets its gap
    assert "ask" not in report
    product = {"What Makes It Different", "Strategic Priorities", "Success Criteria"}
    assert product <= set(report["need_gap"])
    tech = set(report["need_gap"]) - product
    assert tech <= set(sk.skeleton_sections("TECH.md"))


def test_section_table_matches_the_skeleton() -> None:
    for (doc, section) in [*sk.SECTION_TYPES, sk.GAP_SECTION]:
        assert section in sk.skeleton_sections(doc), (doc, section)
    for doc, live in sk.USER_ONLY_SECTIONS.items():
        assert not any((doc, s) in sk.SECTION_TYPES for s in live)


# ── I10: fixture repo → survey → fixed entries → apply → P6 ─────────────────

def test_end_to_end_a_fixed_entries_file_makes_p6_pass_knowledge(brain: Path) -> None:
    survey.survey_project(brain)
    assert (brain / survey.SURVEY_REL).is_file()
    # every PRODUCT and TECH section the entries leave empty gets its gap
    missing = _run(brain, GOOD, dry_run=True)["need_gap"]
    assert {"What Makes It Different", "Strategic Priorities", "Success Criteria"} <= set(missing)
    gaps = [{"kind": "gap", "section": "What to Watch For", "title": f"{s}: no evidence yet",
             "what": "The repo says nothing about it."} for s in missing]
    report = _run(brain, GOOD + gaps)
    assert report["rejected"] == [] and report["need_gap"] == []
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "s_project-manager" / "scripts"))
    import verify_ddd_complete as v
    checks = {c[0]: c[1] for c in v._check_knowledge(brain)}
    assert checks["② Knowledge/structure"] == "PASS"
    assert checks["② Knowledge/content"] == "PASS", checks
    assert checks["② Knowledge/format"] == "PASS", checks


def test_classify_starter(tmp_path: Path, capsys) -> None:
    import scripts.artifact_cli as cli
    import subprocess
    (tmp_path / "r").mkdir()
    (tmp_path / "r" / "pyproject.toml").write_text("[project]\nname='r'\n")
    subprocess.run(["git", "init", "-q", str(tmp_path / "r")], check=True)
    # a repo marker without git: bind --local cannot read it in place
    (tmp_path / "nogit").mkdir()
    (tmp_path / "nogit" / "pyproject.toml").write_text("[project]\nname='n'\n")
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "a.md").write_text("# a\n")
    (tmp_path / "one.md").write_text("# one\n")

    class A:
        path = ""

    roles = {}
    for name in ("r", "notes", "one.md", "nogit"):
        A.path = str(tmp_path / name)
        cli.cmd_classify_starter(A(), None)
        roles[name] = json.loads(capsys.readouterr().out)["role"]
    assert roles == {"r": "GOVERN", "notes": "SHELF", "one.md": "DISTILL", "nogit": "SHELF"}



# ── review round 1 (correctness, ux) ─────────────────────────────────────────

def test_an_edited_bootstrap_entry_makes_the_doc_the_persons(brain: Path) -> None:
    _run(brain, GOOD)
    p = ddd_path(brain, "TECH.md")
    p.write_text(p.read_text(encoding="utf-8").replace("Run the tests with pytest", "Run tests via tox"),
                 encoding="utf-8")
    report = _run(brain, GOOD)
    assert "TECH.md" in report["refused_docs"]
    assert p.read_text(encoding="utf-8").count("Run the tests with pytest") == 0


def test_a_note_inside_an_entry_span_is_noticed(brain: Path) -> None:
    _run(brain, GOOD)
    p = ddd_path(brain, "TECH.md")
    lines = p.read_text(encoding="utf-8").split("\n")
    i = next(k for k, ln in enumerate(lines) if "source:bootstrap" in ln)
    lines.insert(i + 1, "  my own private note")
    p.write_text("\n".join(lines), encoding="utf-8")
    more = [{"kind": "cognition", "type": "model", "section": "Key Subsystems",
             "title": "The signer", "what": "sign() in src.py.", "sources": ["src.py"]}]
    assert "TECH.md" in _run(brain, more)["refused_docs"]


def test_crlf_docs_stay_writable_and_keep_their_endings(brain: Path) -> None:
    p = ddd_path(brain, "TECH.md")
    p.write_bytes(p.read_bytes().replace(b"\n", b"\r\n"))
    assert _run(brain, GOOD)["written"].get("TECH.md") == 2
    more = [{"kind": "cognition", "type": "model", "section": "Key Subsystems",
             "title": "The signer", "what": "sign() in src.py.", "sources": ["src.py"]}]
    assert _run(brain, more)["written"] == {"TECH.md": 1}
    raw = p.read_bytes()
    assert b"\n" not in raw.replace(b"\r\n", b"")


def test_a_unicode_line_break_in_a_title_is_refused(brain: Path) -> None:
    entry = dict(GOOD[0], title="Acme signs\u2028data")
    assert "title" in _run(brain, [entry])["rejected"][0]["reason"]


def test_a_broken_file_source_is_a_refusal_not_a_crash(brain: Path) -> None:
    for src in ("file:~nosuchuser_zz/a.md", "file:/" + "a" * 5000):
        entry = dict(GOOD[0], sources=[src])
        assert "file:" in _run(brain, [entry])["rejected"][0]["reason"]


def test_a_refused_run_says_why_in_rejected_json(brain: Path) -> None:
    write_setup_state(brain, "ready")
    _run(brain, GOOD)
    assert json.loads((brain / ap.REJECTED_FILE).read_text())[0]["reason"].startswith("the brain is not")


def test_the_same_line_meant_for_another_section_is_written(brain: Path) -> None:
    # a guideline in What Worked and a gap in What to Watch For render the same line
    a = {"kind": "cognition", "type": "guideline", "section": "What Worked", "title": "Same",
         "what": "w", "sources": ["src.py"]}
    b = {"kind": "gap", "section": "What to Watch For", "title": "Same", "what": "w",
         "sources": ["src.py"]}
    _run(brain, [a])
    assert _run(brain, [b])["written"] == {"IMPROVEMENT.md": 1}
    imp = ddd_path(brain, "IMPROVEMENT.md").read_text(encoding="utf-8")
    assert "Same" in imp.split("## What to Watch For", 1)[1]


def test_a_crash_between_log_and_doc_write_is_recovered(brain: Path, monkeypatch) -> None:
    real = ap._write_atomic
    calls = {"n": 0}

    def boom(path, text):
        # crash on the second write of the first doc's pair (log + doc): whichever of
        # the two comes second is lost, which is the window that matters
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("crash")
        return real(path, text)

    monkeypatch.setattr(ap, "_write_atomic", boom)
    with pytest.raises(RuntimeError):
        _run(brain, GOOD)
    monkeypatch.setattr(ap, "_write_atomic", real)
    report = _run(brain, GOOD)
    assert report["refused_docs"] == {} and report["rejected"] == []


# ── r12: one entry contract, generated from the rules it describes ──────────

def _valid_cognition(etype: str, section: str) -> dict:
    e = {"kind": "cognition", "type": etype, "section": section, "title": "A real trap",
         "what": "Something the repo states.", "sources": ["user"]}
    for key in ap.entry_contract()["kinds"]["cognition"]["required_by_type"].get(etype, []):
        e[key] = "stated"
    return e


def _verdict(entry: dict):
    """None when the entry passes, else the refusal's kind."""
    try:
        ap.check_entry(entry, ap.Evidence({}), "2026-10-28")
        return None
    except ap.Rejected as exc:
        return exc.kind


def test_every_section_admits_exactly_the_types_the_contract_lists() -> None:
    contract = ap.entry_contract()
    every = set(contract["kinds"]["cognition"]["required_by_type"]) | {
        t for s in contract["sections"] for t in s.get("cognition_types", [])}
    assert len(contract["sections"]) == sum(len(sk.skeleton_sections(d)) for d in sk.DOCS)
    for s in contract["sections"]:
        listed = set(s.get("cognition_types", []))
        for etype in every:
            got = _verdict(_valid_cognition(etype, s["section"]))
            assert (got is None) == (etype in listed), (s, etype, got)
            if got is not None:
                assert got == ap.SHAPE
        gap = {"kind": "gap", "section": s["section"], "title": "Vision: no evidence yet",
               "what": "Nothing says it."}
        assert (_verdict(gap) is None) == bool(s.get("gaps_only")), s


def test_every_required_field_in_the_contract_is_enforced() -> None:
    contract = ap.entry_contract()
    for etype, keys in contract["kinds"]["cognition"]["required_by_type"].items():
        section = next(s["section"] for s in contract["sections"]
                       if etype in s.get("cognition_types", []))
        for key in keys:
            e = _valid_cognition(etype, section)
            del e[key]
            assert _verdict(e) == ap.SHAPE, (etype, key)
    for kind, spec in contract["kinds"].items():
        base = (_valid_cognition("model", "Key Subsystems") if kind == "cognition" else
                {"kind": "gap", "section": "What to Watch For", "title": "t", "what": "w"})
        assert _verdict(base) is None
        for key in spec["required"]:
            e = {k: v for k, v in base.items() if k != key}
            assert _verdict(e) is not None, (kind, key)
        assert _verdict({**base, "no_such_field": "x"}) == ap.SHAPE
        assert set(base) <= set(spec["fields"])


@pytest.mark.parametrize("change", [
    {"title": "t" * (ap.MAX_TITLE + 1)}, {"what": "w" * (ap.MAX_FIELD + 1)},
    {"title": "a ** b"}, {"title": "a | b"}, {"title": "two\nlines"}, {"what": "x <!-- y"},
])
def test_the_value_rules_in_the_contract_are_enforced(change) -> None:
    contract = ap.entry_contract()
    assert str(ap.MAX_TITLE) in contract["values"]["title"]
    assert str(ap.MAX_FIELD) in contract["values"]["other_fields"]
    assert _verdict({**_valid_cognition("model", "Key Subsystems"), **change}) == ap.SHAPE


def test_rejected_alternatives_takes_a_list_written_as_one_sentence() -> None:
    e = _valid_cognition("decision", "Recent Decisions")
    e["rejected_alternatives"] = ["gRPC", " Redis "]
    block = ap.check_entry(e, ap.Evidence({}), "2026-10-28")["block"]
    assert "gRPC; Redis" in block
    for bad in ([], ["x", ""], ["x", 3], 7):
        assert _verdict({**e, "rejected_alternatives": bad}) == ap.SHAPE, bad
    assert _verdict({**e, "why": ["a list where text is required"]}) == ap.SHAPE


@pytest.mark.parametrize("source, kind", [
    ("user", None), ("url:https://example.com/x", None), ("url:ftp://x", ap.EVIDENCE),
    ("nope.md", ap.EVIDENCE), ("file:/no/such/file.md", ap.EVIDENCE), ("file:relative.md", ap.EVIDENCE),
])
def test_each_source_form_in_the_contract(source, kind) -> None:
    assert _verdict({**_valid_cognition("model", "Key Subsystems"), "sources": [source]}) == kind


def test_a_file_that_is_not_one_json_document_says_where(brain: Path) -> None:
    p = brain / ap.ENTRIES_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('[\n{"kind": "gap"}\n{"kind": "gap"}\n]', encoding="utf-8")
    report = ap.apply_entries(brain)
    assert report["status"] == "refused" and report["kind"] == ap.SHAPE
    assert "line 3, column 1" in report["reason"] and "separated by commas" in report["reason"]
    assert json.loads((brain / ap.REJECTED_FILE).read_text())[0]["kind"] == ap.SHAPE


def test_every_refusal_in_the_report_has_its_kind(brain: Path) -> None:
    report = _run(brain, [{**GOOD[0], "sources": ["nope.md"]}, {**GOOD[0], "type": "model"}])
    assert [r["kind"] for r in report["rejected"]] == [ap.EVIDENCE, ap.SHAPE]


def test_the_contract_is_written_for_every_brain(brain: Path, tmp_path: Path, monkeypatch) -> None:
    # a brain that owns a repo: written by bootstrap-apply (not by a dry run)
    ap.apply_entries(brain, dry_run=True)
    assert not (brain / ap.CONTRACT_FILE).exists()
    _run(brain, GOOD)
    written = json.loads((brain / ap.CONTRACT_FILE).read_text())
    assert written == ap.entry_contract(brain) and written["repos"]["owned"] == ["acme"]
    # a pure-knowledge brain (no repo, no survey): written by the command
    from scripts import artifact_cli as cli
    lone = tmp_path / "Projects" / "Notes"
    lone.mkdir(parents=True)
    monkeypatch.setattr(cli, "_get_workspace", lambda: tmp_path)
    import argparse
    monkeypatch.setattr(survey, "owned_repos", lambda d: [])
    cli.cmd_entry_contract(argparse.Namespace(project="Notes"), None)
    written = json.loads((lone / ap.CONTRACT_FILE).read_text())
    assert written == ap.entry_contract(lone)
    assert written["repos"]["owned"] == [] and "file:, url: or user" in written["repos"]["note"]


def test_the_skills_example_follows_the_contract(tmp_path: Path) -> None:
    import re
    skill = (Path(__file__).resolve().parents[1] / "skills" / "s_project-manager" / "SKILL.md").read_text()
    part = skill[skill.index("#### Writing a new brain's first entries (P2b)"):]
    example = json.loads(re.search(r"```json\n(.*?)```", part, re.S).group(1))
    repo = tmp_path / "lib"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "concepts.rst").write_text("x")
    (repo / "CHANGES.rst").write_text("x")
    for entry in example:
        ap.check_entry(entry, ap.Evidence({"lib": repo}), "2026-10-28")


def test_every_kind_a_report_can_carry_is_explained_in_the_contract(brain: Path) -> None:
    contract = ap.entry_contract()
    assert set(contract["refusals"]) == {ap.SHAPE, ap.EVIDENCE, ap.DOC, ap.STATE}
    assert set(contract["kinds"]["cognition"]["meaning"]) == {
        k for keys in contract["kinds"]["cognition"]["required_by_type"].values() for k in keys}
    # doc: the person wrote in the doc
    prod = ddd_path(brain, "PRODUCT.md")
    prod.write_text(prod.read_text() + "\nMy own note.\n")
    assert {r["kind"] for r in _run(brain, [GOOD[0]])["rejected"]} == {ap.DOC}
    # state: the brain is no longer setting up
    write_setup_state(brain, "ready")
    report = _run(brain, GOOD)
    assert report["status"] == "refused" and report["kind"] == ap.STATE


@pytest.mark.parametrize("bad", [["model"], {"t": 1}, 3])
def test_a_type_that_is_not_text_is_refused_not_a_crash(bad) -> None:
    assert _verdict({**_valid_cognition("model", "Key Subsystems"), "type": bad}) == ap.SHAPE


def test_a_refusal_must_name_its_kind() -> None:
    with pytest.raises(TypeError):
        ap.Rejected("no kind")  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        ap.Rejected("bad kind", "doc")
