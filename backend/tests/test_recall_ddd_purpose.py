"""DDD recall by purpose: ``recall_multi.recall_ddd`` and its ``DDD_PURPOSES`` table.

run-knowledge §3.6.2. Fixture DDDs under tmp_path (no one's real DDD), one test per
rule of the table:

- each doc is ranked on its own, so a big doc cannot crowd a small one out (the
  one shared-corpus ranking of all docs is shown losing the same entry);
- ``- [x]`` done items are never recalled;
- typed entries are soft-weighted: ranked up, the untyped ones are not dropped;
- pinned template sections come whatever the query, a pointer section is named only,
  a missing template section is skipped;
- the doc's character budget holds, metadata comments are not spent from it, and
  ``content`` keeps the whole text the ids are taken from;
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
    assert "Not a chatbot" not in nongoals["clipped"] and "PRODUCT.md#Non-Goals" in nongoals["clipped"]


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
    assert "costs nothing" in ranked[0]["clipped"]
    assert "rivers" not in ranked[0]["content"]          # not the section around it


def test_budget_holds_and_metadata_is_not_spent(ddd):
    meta = "  <!-- ref:0 | last:none | decay:active | source:manual -->\n"
    long = "".join(f"- [pitfall] quokka trap {i}: " + "word " * 120 + ".\n" + meta for i in range(6))
    ddd("TECH.md", "# TECH\n\n## Traps\n\n" + long)
    hits = rm.recall_ddd("quokka trap", P, "design")
    cap = rm.DDD_PURPOSES["design"]["docs"]["TECH.md"][2]
    assert sum(len(h["clipped"]) for h in hits if h["doc"] == "TECH.md") <= cap + 200
    assert all("<!--" not in h["clipped"] for h in hits)
    assert all("<!--" in h["content"] for h in hits)            # ids come from the whole


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
