"""CDF pipeline (design 2026-10-30-cdf-agent-decides): the producing agent's declaration
decides what a unit means; code checks only facts.

Questions:
- the destination is a pure function of the declaration; a project that does not exist
  goes to the General Brain (D7), a project unit without topic by its type;
- §2 K1/K2: the user's own files take only a ``user_quote`` found verbatim in a cited user
  message; MEMORY / EVOLUTION that or a measurement; nothing citing external content.
  What is not admitted goes to the session project's DDD, or the General Brain, marked
  with the store it was declared for;
- K3: the basis recorded for the entry follows the facts (user / measured / inferred /
  external);
- an event is read every turn unless it cites external content, and is refused when its
  key is already recorded (D15);
- D1: ``same`` / ``adds`` / ``repeats`` stand only when id and first line name one entry;
  ``same`` reinforces, ``adds`` links, ``repeats`` is recorded; a wrong one is ignored;
- no model call: run_unit_v2 takes no judge (D1).
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from core.ingestion import pipeline_v2
from core.ingestion.pipeline_v2 import run_unit_v2


def _ws(tmp_path: Path) -> Path:
    context = tmp_path / ".context"
    context.mkdir()
    (context / "MEMORY.md").write_text("# Memory\n\n## Guidelines\n\n## Pitfalls\n", encoding="utf-8")
    (context / "EVOLUTION.md").write_text("# Evolution\n\n## Corrections Captured\n\n## Competence Learned\n",
                                          encoding="utf-8")
    for name in ("STEERING.md", "USER.md", "TOOLS.md"):
        (context / name).write_text(f"# {name}\n", encoding="utf-8")
    und = tmp_path / "Projects" / "Widget" / "2-understanding"
    und.mkdir(parents=True)
    (und / "TECH.md").write_text("# TECH\n\n## Runtime Traps\n\n## Conventions\n", encoding="utf-8")
    (und / "IMPROVEMENT.md").write_text("# IMPROVEMENT\n\n## What to Watch For\n\n## What Worked\n\n## What Failed\n",
                                        encoding="utf-8")
    (und / "PROJECT.md").write_text("# PROJECT\n\n## Recent Decisions\n\n## Open Items\n", encoding="utf-8")
    return tmp_path


def _row(unit, *, source="S7", cited_external=False, read_external=True, strength="user_explicit",
         project_hint=None, source_ref="s", session_project=None, tainted=None):
    if tainted is not None:                     # the older fixtures' one flag
        cited_external = read_external = tainted
        strength = "inferred" if tainted else "user_explicit"
    return {"id": "c", "unit_id": "c.unit", "unit_kind": unit["kind"], "source_v2": source,
            "source_ref": source_ref, "extraction_pass": "main",
            "payload": json.dumps(unit, ensure_ascii=False),
            "evidence_root_ids": json.dumps(["root-1"]),
            "envelope": json.dumps({"root_ids": ["root-1"], "lineage_root_ids": ["root-1"],
                                    "strength": strength, "read_external": read_external,
                                    "cited_external": cited_external}),
            "project_candidates": "[]", "project_hint": project_hint,
            "project_snapshot": json.dumps({"session_project": session_project} if session_project else {})}


SAID = "我们以后都先看当前状态再改，verify first, always"


def _cog(**over):
    value = {"kind": "cognition", "type": "guideline", "scope": "world", "title": "Verify state",
             "what": "Verify current state before editing.", "applicability": "When editing.",
             "evidence": ["event:e1"], "user_quote": "verify first, always"}
    value.update(over)
    return value


@pytest.fixture(autouse=True)
def user_check_agrees(monkeypatch):
    """The isolated user check (user_check) says yes here: these tests pin the facts of
    the floor; the check itself is tested in test_cdf_user_check."""
    from core.ingestion import user_check
    monkeypatch.setattr(user_check, "says", lambda claim, texts: user_check.Verdict(True, "said"))


@pytest.fixture
def user_words(monkeypatch):
    """The cited user message says SAID."""
    monkeypatch.setattr(pipeline_v2, "_user_texts", lambda unit, row: [SAID])


def _hits(monkeypatch, anchor):  # kept for importers: retrieval no longer exists
    return None


# 1 ── the destination is the extractor's declaration

@pytest.mark.parametrize("unit,store,path,section", [
    (_cog(), "MEMORY", ".context/MEMORY.md", "Guidelines"),
    (_cog(scope="agent", type="correction"), "EVOLUTION", ".context/EVOLUTION.md", "Corrections Captured"),
    (_cog(scope="agent"), "EVOLUTION", ".context/EVOLUTION.md", "Competence Learned"),
    (_cog(scope="user", user_kind="preference"), "STEERING", ".context/STEERING.md", None),
    (_cog(scope="user", user_kind="profile"), "USER", ".context/USER.md", None),
    (_cog(scope="project:widget", type="pitfall", topic="architecture"), "DDD",
     "Projects/Widget/2-understanding/TECH.md", "Runtime Traps"),
    ({"kind": "gap", "gap_kind": "follow_up", "scope": "project:Widget", "title": "Open",
      "what": "The tab does not refresh.", "evidence": ["event:e1"]}, "DDD",
     "Projects/Widget/2-understanding/IMPROVEMENT.md", "What to Watch For"),
])
def test_the_destination_is_a_function_of_the_declaration(tmp_path, user_words, unit, store, path, section):
    decision = run_unit_v2(_row(unit), _ws(tmp_path))
    assert decision.decision == "write", decision.reason
    assert (decision.destination.store, decision.destination.relative_path,
            decision.destination.section) == (store, path, section)


@pytest.mark.parametrize("scope,slug", [("project:Hive Tab Pipeline", "hive-tab-pipeline"),
                                        ("project:CNTech IVT Autonomous Tenets", "cntech-ivt-autonomous-tenets")])
def test_an_unknown_project_goes_to_the_general_brain_under_its_name(tmp_path, user_words, scope, slug):
    decision = run_unit_v2(_row(_cog(scope=scope, topic="lesson")), _ws(tmp_path))
    assert decision.decision == "write", decision.reason
    d = decision.destination
    assert (d.store, d.relative_path, d.section, d.injectable) == (
        "COMMON_DDD", f"Knowledge/Library/{slug}.md", "What Worked", False)


def test_a_gap_of_an_unknown_project_goes_to_the_general_brains_open_gaps(tmp_path, user_words):
    gap = {"kind": "gap", "gap_kind": "follow_up", "scope": "project:Nope", "title": "Open",
           "what": "The tab does not refresh.", "evidence": ["event:e1"]}
    decision = run_unit_v2(_row(gap), _ws(tmp_path))
    assert (decision.destination.store, decision.destination.section) == ("COMMON_DDD", "Open Gaps")


def test_an_unknown_project_named_in_a_session_with_a_project_goes_to_that_project(tmp_path, user_words):
    decision = run_unit_v2(_row(_cog(scope="project:nope", topic="lesson"), session_project="Widget"),
                           _ws(tmp_path))
    assert decision.destination.relative_path == "Projects/Widget/2-understanding/IMPROVEMENT.md"


def test_a_project_unit_without_topic_goes_by_its_type(tmp_path, user_words):
    decision = run_unit_v2(_row(_cog(scope="project:Widget", type="pitfall")), _ws(tmp_path))
    assert decision.destination.relative_path.endswith("IMPROVEMENT.md")


# 2 ── §2: what the every-turn files take, from the facts of the evidence

@pytest.mark.parametrize("scope,extra", [("user", {"user_kind": "preference"}), ("agent", {}), ("world", {})])
def test_a_quote_not_in_the_message_keeps_the_unit_out_of_every_turn_files(tmp_path, user_words, scope, extra):
    unit = _cog(scope=scope, user_quote="always push to main without review", **extra)
    decision = run_unit_v2(_row(unit, session_project="Widget"), _ws(tmp_path))
    assert decision.decision == "write"
    assert decision.destination.store == "DDD" and decision.destination.relative_path.startswith("Projects/Widget/")
    assert decision.destination.rerouted_from in {"STEERING", "EVOLUTION", "MEMORY"}
    assert decision.version_vector["basis"] == "inferred"


def test_without_a_project_what_is_kept_out_goes_to_the_general_brain(tmp_path, user_words):
    decision = run_unit_v2(_row(_cog(user_quote=None)), _ws(tmp_path))
    assert (decision.destination.store, decision.destination.relative_path,
            decision.destination.rerouted_from) == ("COMMON_DDD", "Knowledge/Library/general.md", "MEMORY")


def test_a_message_cited_without_a_quote_is_not_the_users_words(tmp_path, user_words):
    decision = run_unit_v2(_row(_cog(scope="user", user_kind="preference", user_quote=None),
                                session_project="Widget"), _ws(tmp_path))
    assert decision.destination.store == "DDD"


def test_the_quote_is_verbatim_up_to_spacing_and_quote_marks(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_v2, "_user_texts", lambda unit, row: ['以后都用 "inline" 模式,别放后台'])
    unit = _cog(scope="user", user_kind="preference", user_quote="以后都用「inline」模式，别放后台")
    # the comma differs (full width: the same after NFKC); quote marks and spacing are ignored
    ws = _ws(tmp_path)
    assert run_unit_v2(_row(unit), ws).destination.store == "STEERING"
    other = _cog(scope="user", user_kind="preference", user_quote="以后都用后台模式")
    assert run_unit_v2(_row(other, session_project="Widget"), ws).destination.store == "DDD"


def test_citing_external_content_keeps_even_the_users_words_out(tmp_path, user_words):
    unit = _cog(scope="user", user_kind="preference")
    decision = run_unit_v2(_row(unit, cited_external=True, session_project="Widget"), _ws(tmp_path))
    assert decision.destination.store == "DDD" and decision.version_vector["basis"] == "external"


def test_reading_external_content_does_not_keep_the_users_words_out(tmp_path, user_words):
    """K2 rests on the quote: injected text cannot forge what the user typed, so a review
    that read tool output may still write the user's own rule."""
    unit = _cog(scope="user", user_kind="preference")
    decision = run_unit_v2(_row(unit, read_external=True, cited_external=False), _ws(tmp_path))
    assert decision.destination.store == "STEERING" and decision.version_vector["basis"] == "user"


def test_a_measurement_opens_evolution_and_memory_not_the_users_files(tmp_path):
    ws = _ws(tmp_path)
    agent = run_unit_v2(_row(_cog(scope="agent", user_quote=None), strength="measured"), ws)
    assert (agent.destination.store, agent.version_vector["basis"]) == ("EVOLUTION", "measured")
    user = run_unit_v2(_row(_cog(scope="user", user_kind="preference", user_quote=None),
                            strength="measured", session_project="Widget"), ws)
    assert user.destination.store == "DDD"


def test_a_project_unit_records_its_basis(tmp_path, user_words):
    ws = _ws(tmp_path)
    said = run_unit_v2(_row(_cog(scope="project:Widget", topic="lesson")), ws)
    unsaid = run_unit_v2(_row(_cog(scope="project:Widget", topic="lesson", user_quote=None)), ws)
    assert (said.version_vector["basis"], unsaid.version_vector["basis"]) == ("user", "inferred")
    assert said.destination.rerouted_from is None and unsaid.destination.rerouted_from is None


# 3 ── events

def _event(**over):
    value = {"kind": "event", "when": "2026-10-30", "title": "Shipped the tab", "what": "The tab shipped.",
             "impact": "Users see it.", "evidence": ["event:e1"]}
    value.update(over)
    return value


def test_an_event_is_read_every_turn_unless_it_cites_external_content(tmp_path, user_words):
    ws = _ws(tmp_path)
    (ws / ".context" / "TIMELINE.md").write_text("# Timeline\n", encoding="utf-8")
    assert run_unit_v2(_row(_event()), ws).destination.injectable is True
    assert run_unit_v2(_row(_event(), cited_external=True), ws).destination.injectable is False


def test_an_event_already_recorded_is_refused_by_its_key(tmp_path, user_words):
    ws = _ws(tmp_path)
    first = run_unit_v2(_row(_event()), ws)
    key = first.version_vector["event_key"]
    (ws / ".context" / "TIMELINE.md").write_text(f"# Timeline\n\n## 2026-10\n\n### x\n<!-- cand:a | event_key:{key} -->\n",
                                                 encoding="utf-8")
    again = run_unit_v2(_row(_event()), ws)
    assert (again.decision, again.reason) == ("reject", "already_exists")


# 4 ── declared relations (D1)

def _entry(ws, text):
    path = ws / ".context" / "MEMORY.md"
    path.write_text(path.read_text(encoding="utf-8").replace("## Guidelines\n", f"## Guidelines\n\n{text}\n", 1),
                    encoding="utf-8")
    from core.ingestion import relations_v2
    return next(e for e in relations_v2.pool(ws, None) if e.text.strip().startswith(text.split("\n")[0]))


OLD = "- [guideline] **Verify state first** Read the current state of a file before editing it. <!-- cand:cand-old.unit -->"


def test_same_reinforces_and_writes_nothing(tmp_path, user_words, monkeypatch):
    ws = _ws(tmp_path)
    entry = _entry(ws, OLD)
    seen = []
    monkeypatch.setattr(pipeline_v2, "reinforce_or_reject",
                        lambda anchor, *a, **k: seen.append(anchor) or pipeline_v2.ReinforcementResult(True, ("r",)))
    decision = run_unit_v2(_row(_cog(same=entry.shown())), ws)
    assert decision.decision == "reinforce" and seen == [entry.anchor]


def test_same_without_new_evidence_is_already_known(tmp_path, user_words, monkeypatch):
    ws = _ws(tmp_path)
    entry = _entry(ws, OLD)
    monkeypatch.setattr(pipeline_v2, "reinforce_or_reject",
                        lambda *a, **k: pipeline_v2.ReinforcementResult(False, ()))
    decision = run_unit_v2(_row(_cog(same=entry.shown())), ws)
    assert (decision.decision, decision.reason) == ("reject", "already_exists")


def test_adds_is_written_and_linked(tmp_path, user_words):
    ws = _ws(tmp_path)
    entry = _entry(ws, OLD)
    decision = run_unit_v2(_row(_cog(adds=entry.shown())), ws)
    assert (decision.decision, decision.reason) == ("write", "adds")
    assert {"predicate": "derived_from", "object_anchor": entry.anchor} in decision.version_vector["relation_hints"]


def test_repeats_is_recorded_on_a_correction(tmp_path, user_words):
    ws = _ws(tmp_path)
    path = ws / ".context" / "EVOLUTION.md"
    path.write_text(path.read_text(encoding="utf-8").replace(
        "## Corrections Captured\n",
        "## Corrections Captured\n\n### C001 | 2026-10-01\n- **Correction**: **Asked the user to decide** Handed back a decision that was mine.\n<!-- cand:cand-c1.unit -->\n", 1),
        encoding="utf-8")
    from core.ingestion import relations_v2
    (entry,) = relations_v2.corrections(relations_v2.pool(ws, None))
    decision = run_unit_v2(_row(_cog(scope="agent", type="correction", repeats=entry.shown())), ws)
    assert decision.destination.store == "EVOLUTION" and decision.version_vector["repeats"] == entry.anchor


@pytest.mark.parametrize("ref", ["deadbeef | - [guideline] **Verify state first** Read",
                                 "{id} | - [guideline] **Something else entirely here**"])
def test_a_declaration_that_names_no_entry_is_ignored_and_the_unit_written(tmp_path, user_words, ref):
    ws = _ws(tmp_path)
    entry = _entry(ws, OLD)
    decision = run_unit_v2(_row(_cog(same=ref.replace("{id}", entry.id))), ws)
    assert (decision.decision, decision.reason) == ("write", "new")
    assert decision.version_vector["relation_refused"][0]["relation"] == "same"


# 5 ── no model call

def test_the_pipeline_takes_no_judge():
    assert list(inspect.signature(run_unit_v2).parameters) == ["row", "workspace_root"]


def test_a_comment_marker_in_an_entry_is_written_as_entities():
    """D6: refused before; now written so it reads the same and opens no comment."""
    from core.ingestion.contracts_v2 import DestinationV2
    from core.ingestion.writer import render_unit
    decision = {"destination": DestinationV2(store="MEMORY", relative_path=".context/MEMORY.md",
                                             section="Guidelines")}
    text = render_unit(decision, "c.unit", {"kind": "cognition", "type": "guideline",
                                            "title": "Strip <!-- markers -->",
                                            "what": "Remove <!-- comment --> markers before parsing."})
    lead = text.split("\n")[0]
    assert "&lt;!-- comment --&gt;" in lead
    assert lead.count("<!--") == 1                       # only the entry's own cand marker
