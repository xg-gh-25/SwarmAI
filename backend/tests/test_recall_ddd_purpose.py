"""DDD recall by purpose: ``recall_multi.recall_ddd`` and its ``DDD_PURPOSES`` table.

run-knowledge §3.6.2. Fixture DDDs under tmp_path (no one's real DDD), one test per
rule of the table:

- each doc is ranked on its own, so a big doc cannot crowd a small one out (the
  one shared-corpus ranking of all docs is shown losing the same entry);
- ``- [x]`` done items are never recalled;
- typed entries are soft-weighted: ranked up, the untyped ones are not dropped;
- pinned template sections come whatever the query, a pointer section is named only,
  a missing template section is skipped;
- every unit is shown whole or named in one "omitted" row, never cut; a unit that
  does not fit is not displaced by a lower-ranked one; the budget holds, metadata
  comments are not spent from it, and ``content`` keeps the text the ids come from;
- a one-line lead-in is not a unit; pinned sections do not spend the ranked budget;
  a too-long pinned section is named, not cut; REFLECT ranks all docs in one corpus;
- one doc that fails is skipped alone; no project / unknown project is ``[]``; an
  unknown purpose is a caller bug (``KeyError``).
"""
from __future__ import annotations

import pytest

from core import recall_multi as rm

P = "Proj"


@pytest.fixture
def ddd(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
    und = tmp_path / "Projects" / P / "2-understanding"
    und.mkdir(parents=True)

    def write(doc: str, text: str) -> None:
        (und / doc).write_text(text, encoding="utf-8")
    return write


def _entries(n: int, word: str) -> str:
    return "".join(f"- {word} rule number {i}: keep the {word} cache warm.\n" for i in range(n))


def test_a_small_doc_is_not_crowded_out_by_a_big_one(ddd):
    ddd("TECH.md", "# TECH\n\n## Traps\n\n" + _entries(40, "quokka"))
    ddd("PROJECT.md", "# PROJECT\n\n## Recent Decisions\n\n"
                      "- [decision] **quokka tier**: the quokka gate stays observe-only.\n")
    _base, docs = rm._load_ddd_docs_text(P)
    shared = rm._ddd_entry_hits("quokka", docs, 8)
    assert all(h["doc"] == "TECH.md" for h in shared)   # what one shared corpus does
    hits = rm.recall_ddd("quokka", P, "chat")
    assert any(h["doc"] == "PROJECT.md" and "quokka tier" in h["content"] for h in hits)
    assert sum(h["doc"] == "TECH.md" for h in hits) == 2         # its own quota only


def test_done_items_are_not_recalled(ddd):
    ddd("PROJECT.md", "# PROJECT\n\n## Open Items\n\n"
                      "- [x] quokka feeder shipped\n- [ ] quokka feeder docs\n")
    hits = rm.recall_ddd("quokka feeder", P, "requirement")
    texts = [h["content"] for h in hits]
    assert any("docs" in t for t in texts) and not any("shipped" in t for t in texts)


def test_typed_entries_rank_up_but_untyped_stay(ddd):
    ddd("TECH.md", "# TECH\n\n## Traps\n\n"
                   "- quokka cache rule: warm it.\n"
                   "- [pitfall] quokka cache rule: warm it.\n"
                   "- quokka cache note: unrelated wording here.\n")
    plain = rm.recall_ddd("quokka cache rule", P, "review", weight=1.0)
    weighted = rm.recall_ddd("quokka cache rule", P, "review", weight=1.5)
    assert weighted[0]["content"].startswith("- [pitfall]")
    assert any(not h["content"].startswith("- [") for h in weighted)
    assert {h["content"] for h in plain} == {h["content"] for h in weighted}


def test_pinned_sections_come_whatever_the_query(ddd):
    ddd("PRODUCT.md", "# PRODUCT\n\n## Vision\n\nAn OS for agents.\n\n"
                      "## Non-Goals\n\nNot a chatbot.\n\n## Pricing\n\nFree.\n")
    hits = rm.recall_ddd("zzz unrelated words", P, "requirement")
    kinds = {(h["kind"], h["section"]) for h in hits}
    assert ("pinned", "Vision") in kinds and ("pointer", "Non-Goals") in kinds
    assert not any(h["section"] in ("Success Criteria", "Target Users") for h in hits)
    nongoals = next(h for h in hits if h["section"] == "Non-Goals")
    assert "Not a chatbot" not in nongoals["shown"] and "PRODUCT.md#Non-Goals" in nongoals["shown"]


def test_product_is_recalled_by_paragraph_not_by_section(ddd):
    """§3.6.9: a prose doc is ranked by its paragraphs and entries, so the hit is the
    paragraph that matches, not the whole section around it."""
    filler = "".join(f"Paragraph {i} is about rivers, hills and weather number {i}.\n\n"
                     for i in range(30))
    ddd("PRODUCT.md", "# PRODUCT\n\n## Strategy\n\n" + filler +
        "The quokka plan costs nothing; it is a paragraph, not a bullet.\n\n" + filler +
        "## Brand\n\nBlue.\n")
    hits = rm.recall_ddd("quokka plan", P, "requirement")
    ranked = [h for h in hits if h["kind"] in ("block", "entry")]
    assert ranked and ranked[0]["section"] == "Strategy" and ranked[0]["kind"] == "block"
    assert "costs nothing" in ranked[0]["shown"]
    assert "rivers" not in ranked[0]["content"]          # not the section around it


def test_units_are_whole_or_named_never_cut(ddd):
    """CDF first principle: by rank, each unit shown whole while it fits the doc's
    budget; the first that does not fit and every one after it are named in one
    "omitted" row. Metadata comments are not spent from the budget."""
    meta = "  <!-- ref:0 | last:none | decay:active | source:manual -->\n"
    long = "".join(f"- [pitfall] quokka trap {i}: " + "word " * 300 + "end.\n" + meta
                   for i in range(6))
    ddd("TECH.md", "# TECH\n\n## Traps\n\n" + long)
    hits = [h for h in rm.recall_ddd("quokka trap", P, "design") if h["doc"] == "TECH.md"]
    cap = rm.DDD_PURPOSES["design"]["docs"]["TECH.md"][2]
    shown = [h for h in hits if h["kind"] == "entry"]
    assert shown and all(h["shown"] == rm._readable(h["content"]) for h in shown)
    assert all(h["shown"].endswith("end.") for h in shown)          # nothing cut
    assert sum(len(h["shown"]) for h in hits) <= cap + 300
    assert all("<!--" not in h["shown"] for h in hits)
    assert all("<!--" in h["content"] for h in shown)               # ids come from the whole
    omitted = [h for h in hits if h["kind"] == "omitted"]
    quota = rm.DDD_PURPOSES["design"]["docs"]["TECH.md"][1]
    assert len(omitted) == 1 and hits[-1] is omitted[0]
    assert f"{quota - len(shown)} more matching" in omitted[0]["shown"]
    assert "TECH.md#Traps" in omitted[0]["shown"] and "characters)" in omitted[0]["shown"]


def test_a_unit_that_does_not_fit_is_not_displaced_by_a_lower_one():
    """The first unit that does not fit ends the doc: the shorter one ranked after it
    is named with it, not shown in its place (no rank inversion)."""
    def unit(text, section="Traps"):
        return {"doc": "TECH.md", "section": section, "kind": "entry", "score": 1.0,
                "content": text}
    group = [unit("- best rule, short."), unit("- second rule " + "x " * 3000, "Notes"),
             unit("- third rule, short.")]
    out = rm._take_whole("TECH.md", group, 1000)
    assert [h["kind"] for h in out] == ["entry", "omitted"]
    assert out[0]["shown"] == "- best rule, short."
    assert "2 more matching entries" in out[1]["shown"]
    assert "- TECH.md#Notes (" in out[1]["shown"] and "- TECH.md#Traps (" in out[1]["shown"]
    assert "x x" not in out[1]["shown"]                         # located, not excerpted


def test_an_omitted_entry_is_located_by_its_whole_title():
    long_title = "a" * (rm.TITLE_MAX + 1)
    group = [{"doc": "TECH.md", "section": "Arch", "kind": "entry", "score": 1.0,
              "content": "- [decision] **Eval policy — a case must WORK** body " + "y " * 900},
             {"doc": "TECH.md", "section": "Arch", "kind": "entry", "score": 0.9,
              "content": f"- **{long_title}** body"}]
    note = rm._take_whole("TECH.md", group, 100)[-1]["shown"]
    assert "- TECH.md#Arch: Eval policy — a case must WORK (" in note
    assert long_title not in note and "- TECH.md#Arch (" in note


def test_a_too_long_pinned_section_is_named_not_cut(ddd):
    ddd("PRODUCT.md", "# PRODUCT\n\n## Vision\n\n" + "An OS for agents. " * 200 + "\n")
    hits = rm.recall_ddd("", P, "requirement")
    vision = next(h for h in hits if h["section"] == "Vision")
    assert vision["kind"] == "pointer" and "PRODUCT.md#Vision" in vision["shown"]
    assert "An OS for agents" not in vision["shown"]


def test_a_one_line_lead_in_is_not_a_unit(ddd):
    """ "Three quokka beliefs:" says nothing alone; what it introduces stands on its
    own. A full paragraph that happens to end in a colon is still a unit."""
    ddd("PRODUCT.md", "# PRODUCT\n\n## Strategy\n\nThree quokka beliefs:\n\n"
                      "Feed the quokka beliefs at noon, never at night.\n\n"
                      "The quokka beliefs paragraph runs on.\nIts second line ends with:\n\n"
                      "A quokka.\n\n"            # the weakest match (scores 0 after min-max)
                      "## Brand\n\nBlue.\n")
    hits = rm.recall_ddd("quokka beliefs", P, "requirement")
    shown = [h["shown"] for h in hits if h["kind"] == "block"]
    assert "Three quokka beliefs:" not in shown
    assert any("never at night" in t for t in shown)
    assert any(t.endswith("ends with:") for t in shown)


def test_pinned_sections_do_not_spend_the_ranked_budget(ddd):
    """Three long pinned sections cannot push the query's own units out."""
    body = "Agents run here. " * 85                         # ~1450 characters each
    ddd("PRODUCT.md", "# PRODUCT\n\n## Vision\n\n" + body + "\n\n## Success Criteria\n\n"
                      + body + "\n\n## Target Users\n\n" + body + "\n\n## Strategy\n\n"
                      "The quokka plan costs nothing.\n")
    hits = rm.recall_ddd("quokka plan", P, "requirement")
    assert sum(h["kind"] == "pinned" for h in hits) == 3
    assert any(h["kind"] == "block" and "costs nothing" in h["shown"] for h in hits)
    assert not any(h["kind"] == "omitted" for h in hits)


def test_reflect_ranks_every_doc_together(ddd):
    """REFLECT's dedup candidates: a duplicate that is TECH.md's second-best match is
    still a candidate (a per-doc top-1 dropped it), and all docs' entries are ranked on
    one scale."""
    ddd("TECH.md", "# TECH\n\n## Traps\n\n"
                   "- quokka loop loop closes worker thread dies db read pending quokka.\n"
                   "- tests leave db reads pending when the loop closes; the worker thread dies.\n"
                   + _entries(30, "wombat"))
    ddd("PRODUCT.md", "# PRODUCT\n\n## Design Philosophy\n\n"
                      "- [principle] prefer one loop owner.\n")
    hits = rm.recall_ddd("tests leave db reads pending when the loop closes", P, "reflect")
    texts = [h["content"] for h in hits]
    assert any(t.startswith("- tests leave db reads pending") for t in texts), texts
    assert all(h["kind"] == "entry" and h["shown"] for h in hits)
    assert len(hits) <= rm.DDD_PURPOSES["reflect"]["pooled"]
    scores = [h["score"] for h in hits]
    assert scores == sorted(scores, reverse=True)                # one comparable scale


def test_one_failing_doc_is_skipped_alone(ddd, monkeypatch):
    ddd("TECH.md", "# TECH\n\n## Traps\n\n- quokka trap: tech.\n")
    ddd("IMPROVEMENT.md", "# IMPROVEMENT\n\n## What Failed\n\n- quokka trap: improvement.\n")
    real = rm._doc_entries

    def flaky(query, doc, *a, **k):
        if doc == "TECH.md":
            raise RuntimeError("parse broke")
        return real(query, doc, *a, **k)
    monkeypatch.setattr(rm, "_doc_entries", flaky)
    hits = rm.recall_ddd("quokka trap", P, "design")
    assert [h["doc"] for h in hits] == ["IMPROVEMENT.md"]


def test_no_project_unknown_project_and_unknown_purpose(ddd):
    ddd("TECH.md", "# TECH\n\n## Traps\n\n- quokka trap: tech.\n")
    assert rm.recall_ddd("quokka", None, "design") == []
    assert rm.recall_ddd("quokka", "Nope", "design") == []
    with pytest.raises(KeyError):
        rm.recall_ddd("quokka", P, "nonsense")


def test_case_set_script_compares_before_and_after(ddd):
    """scripts/ddd_recall_eval.py: the shared-corpus 'before' misses what the
    per-doc 'after' finds (the run_85e81059 shape, in a fixture)."""
    import importlib.util
    from pathlib import Path
    ddd("TECH.md", "# TECH\n\n## Traps\n\n" + _entries(40, "quokka"))
    ddd("PROJECT.md", "# PROJECT\n\n## Recent Decisions\n\n"
                      "- [decision] **quokka tier**: the quokka gate stays observe-only.\n")
    path = Path(__file__).resolve().parents[1] / "scripts" / "ddd_recall_eval.py"
    spec = importlib.util.spec_from_file_location("ddd_recall_eval", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = mod.evaluate([{"id": "c1", "purpose": "requirement", "request": "quokka",
                         "expect": ["quokka tier"]}], P, [1.0])
    assert out["summary"]["requirement"] == {"expect": 1, "before": 0, "after@1.0": 1}
