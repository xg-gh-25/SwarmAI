"""What a change makes untrue leaves the brain when the change is recorded (live finding
2026-10-30: a SwarmAI DDD kept eight entries about a correction ledger next to the entry
recording its removal; one unit in the whole store had ever declared an override).

Questions:
- is a unit that rests on the agent's own inference allowed to retire a CDF entry of its
  project (external content still never retires anything)?
- does the review see each candidate entry whole, and up to ten of them per unit?
- may a unit declare up to ten overrides, and is a removal named as making entries untrue?
"""
from __future__ import annotations

from types import SimpleNamespace


def test_the_relation_turn_shows_entries_whole_and_names_removal_as_untrue():
    from core.ingestion.relations_v2 import Entry
    from core.ingestion.sources import s7_review
    long_text = "- [model] **Ledger** " + " ".join(f"w{i}" for i in range(800))
    entry = Entry("0123abcd", "Projects/P/2-understanding/TECH.md#Architecture#cand:x",
                  "Projects/P/2-understanding/TECH.md", "Architecture", long_text)
    unit = SimpleNamespace(kind="cognition", type="decision", title="Ledger removed", what="Removed.")
    prompt = s7_review.relation_prompt([unit], [[entry]])
    assert "w799" in prompt                                   # not cut at 1500 characters
    assert "removed or\n  reworked in this conversation" in prompt
    assert "one current entry per thing" in prompt


def test_up_to_ten_overrides_are_kept_and_accepted():
    from core.ingestion import contracts_v2
    from core.ingestion.sources import s7_review
    refs = [f"{i:08x} | - [decision] **Entry {i}**" for i in range(12)]
    unit = contracts_v2.CognitionUnit(kind="cognition", type="decision", scope="project:P",
                                      topic="decision", title="Ledger removed", what="Removed it.",
                                      why="w", rejected_alternatives="none", applicability="a",
                                      evidence=["event:e1"])
    (out,) = s7_review.apply_relations([unit], {"relations": [{"unit": 1, "relation": "new",
                                                              "overrides": refs}]})
    assert out.overrides == refs[:10] == refs[:contracts_v2.OVERRIDES_TOP]


def test_ten_candidates_are_shown_per_unit():
    from core.ingestion.relations_v2 import Entry, candidates
    entries = [Entry(f"{i:08x}", f"a#s#cand:{i}", "a", "s", f"ledger correction row {i}") for i in range(15)]
    assert len(candidates("ledger correction", entries)) == 10
