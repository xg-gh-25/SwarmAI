"""A lesson compounds only when its next occurrence can find it (2026-10-31 review).

Questions:
- is a General Brain document CDF writes into part of the pool a unit is declared against
  (same / adds / repeats), and a Library document CDF never wrote into not?
- are the corrections the General Brain keeps among the corrections a new correction is
  shown (``relations_v2.corrections``)?
- does a correction about the agent that repeats a recorded correction go to EVOLUTION
  even without the user's words (two conversations with the same mistake are a
  measurement), and only then: not a correction declaring nothing, not one citing external
  content, not one about the world?
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_cdf_refactor import _cog, _row, _ws, user_check_agrees  # noqa: F401

GENERAL = "Knowledge/Library/agent-self-knowledge.md"
EARLIER = ("- [correction] **Called a constant dead after grepping only production code** "
           "a test imported it. <!-- cand:cand-111111111111111111111111.unit -->")


def _general(ws: Path) -> None:
    """The earlier correction where production files it: route_general's section."""
    from core.ingestion import contracts_v2
    first = contracts_v2.SOURCE_UNIT.validate_python(_cog(scope="agent", type="correction",
                                                          user_quote=None))
    home = contracts_v2.route_general(first, "Agent self-knowledge")
    assert home.relative_path == GENERAL
    lib = ws / "Knowledge" / "Library"
    lib.mkdir(parents=True, exist_ok=True)
    from core.ingestion.writer import _multi_unit_frontmatter
    (lib / "agent-self-knowledge.md").write_text(
        _multi_unit_frontmatter("Agent Self Knowledge", "the writer's own header")
        + f"# Agent Self Knowledge\n\n## {home.section}\n\n" + EARLIER + "\n", encoding="utf-8")
    (lib / "org-chart.md").write_text("# Org chart\n\n## People\n\n- [model] **Bo** manager\n",
                                      encoding="utf-8")
    (lib / "style-guide-cand-222222222222222222222222-unit.md").write_text(
        "---\ntitle: Style\nreference_kind: spec\nsource: S1\n"
        "ingestion_candidate: cand-222222222222222222222222.unit\n---\n\n# Style\n\n- [guideline] **Plain words** "
        "always. <!-- cand:cand-222222222222222222222222.unit -->\n", encoding="utf-8")


def _earlier(ws: Path):
    from core.ingestion import relations_v2
    return next(e for e in relations_v2.pool(ws, None) if "grepping only production" in e.text)


def test_the_general_brain_cdf_writes_into_is_in_the_pool(tmp_path):
    from core.ingestion import relations_v2
    ws = _ws(tmp_path)
    _general(ws)
    paths = {e.path for e in relations_v2.pool(ws, "Widget")}
    assert GENERAL in paths
    assert "Knowledge/Library/org-chart.md" not in paths
    assert not any("style-guide" in p for p in paths)          # a reference, not the Brain
    assert _earlier(ws) in relations_v2.corrections(relations_v2.pool(ws, None))


def _repeat(ws, **over):
    from core.ingestion.pipeline_v2 import run_unit_v2
    unit = _cog(scope="agent", type="correction", user_quote=None,
                title="Grepped only production code before removing a constant",
                what="A test still imported it.", **over)
    return run_unit_v2(_row(unit), ws)


@pytest.fixture
def inbox(tmp_path, monkeypatch):
    """The CDF store's inbox, holding the earlier correction's candidate from another
    session (the test sets source_ref): what _repeat_opens reads."""
    import sqlite3
    from core.ingestion import store
    db = tmp_path / "ingestion.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE ingestion_inbox (id TEXT, source_ref TEXT)")
        conn.execute("INSERT INTO ingestion_inbox VALUES ('cand-111111111111111111111111', 'other')")
    monkeypatch.setattr(store, "connect_ro", lambda: sqlite3.connect(f"file:{db}?mode=ro", uri=True))

    def set_ref(ref):
        with sqlite3.connect(db) as conn:
            conn.execute("UPDATE ingestion_inbox SET source_ref=?", (ref,))
    return set_ref


def test_a_repeated_correction_goes_to_evolution(tmp_path, inbox):
    ws = _ws(tmp_path)
    _general(ws)
    earlier = _earlier(ws)
    got = _repeat(ws, repeats=earlier.shown())
    assert got.decision == "write", got
    assert (got.destination.store, got.destination.section) == ("EVOLUTION", "Corrections Captured")
    assert got.version_vector["repeats"] == earlier.anchor
    assert got.version_vector["admitted_by"] == "repeat"


def test_only_a_repeat_opens_evolution(tmp_path, inbox):
    from core.ingestion.pipeline_v2 import run_unit_v2
    ws = _ws(tmp_path)
    _general(ws)
    earlier = _earlier(ws)
    assert _repeat(ws).destination.relative_path == GENERAL
    external = run_unit_v2(_row(_cog(scope="agent", type="correction", user_quote=None,
                                     title="Grepped only production code", what="Again.",
                                     repeats=earlier.shown()), cited_external=True), ws)
    assert external.destination.store != "EVOLUTION"
    world = run_unit_v2(_row(_cog(scope="world", type="correction", user_quote=None,
                                  title="Grepped only production code", what="Again.",
                                  repeats=earlier.shown())), ws)
    assert world.destination.store not in ("EVOLUTION", "MEMORY")


def test_the_review_is_told_what_is_no_lesson_and_no_reference():
    from core.ingestion.sources import s7_review
    prompt = " ".join(s7_review.build_prompt(("cognition", "event", "gap", "reference"), {}, "", "r").split())
    for rule in ("A bug found and fixed within this record is not a pitfall",
                 "A problem still open is a gap, never a lesson",
                 "never a copy or summary of one of its versions"):
        assert rule in prompt, rule
    assert "is a reference" not in prompt


def test_a_repeat_of_this_conversations_own_correction_stays_in_the_general_brain(tmp_path, inbox):
    """The earlier correction's candidate came from this session (source_ref "s", the
    row's): the same mistake once, recorded twice, is no second occurrence. An unreadable
    store keeps it in the General Brain too."""
    from core.ingestion import store
    ws = _ws(tmp_path)
    _general(ws)
    earlier = _earlier(ws)
    inbox("s")
    assert _repeat(ws, repeats=earlier.shown()).destination.relative_path == GENERAL

    def unreadable():
        raise FileNotFoundError("no store")
    store.connect_ro = unreadable
    assert _repeat(ws, repeats=earlier.shown()).destination.relative_path == GENERAL
