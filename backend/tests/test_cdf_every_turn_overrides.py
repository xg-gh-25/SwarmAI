"""The every-turn files can be overridden (design docs/design/2026-11-02-knowledge-reaches-judgment.md ②).

MEMORY and EVOLUTION were only ever added to: ``overrides.check`` resolved a declared
target only among the session project's DDD entries, so a review that wrote the whole
current lesson of a mistake class could never retire the incident entry it replaces.
These tests pin the extended reach and the floor that comes with it: an entry read in
every turn is retired only by a unit that itself passed the every-turn floor.
"""
from __future__ import annotations

import pytest

from tests.test_cdf_v2_e2e import cdf_env  # noqa: F401  (fixture)
from tests.test_cdf_refactor import _cog, _row, user_check_agrees  # noqa: F401
from core.ingestion import pipeline_v2
from core.ingestion.pipeline_v2 import run_unit_v2

_CAND = "cand-0123456789abcdef01234567"
_HANDBACK = ("### C068 | 2026-10-29\n- **Correction**: **Deferred Hive updates to the user instead of "
             "calling the API** The agent described the steps instead of updating the Hive itself.\n"
             f"<!-- cand:{_CAND}.unit -->")
_PERSON = "### C010 | 2026-07-01\n- **Correction**: **Never push without asking** Written by a person."


def _evolution(cdf_env, *entries: str):
    path = cdf_env["workspace"] / ".context" / "EVOLUTION.md"
    path.write_text("# Evolution\n\n## Corrections Captured\n\n" + "\n\n".join(entries)
                    + "\n\n## Competence Learned\n", encoding="utf-8")
    return path


def _ref(cdf_env, path, needle: str) -> str:
    from core.ingestion import relations_v2
    entry = next(e for e in relations_v2._entries_of(path, cdf_env["workspace"]) if needle in e.text)
    return entry.shown()


def _class_lesson(**over):
    fields = dict(type="correction", scope="agent", title="Hand back only what I cannot do myself",
                  what="When one call is refused, look up the operation I actually need before "
                       "handing the task to the person.",
                  applicability="A gate refuses one call on the way to an operational task.",
                  user_quote="这事为什么我做 你不能做吗")
    fields.update(over)
    return _cog(**fields)


@pytest.fixture
def said(monkeypatch):
    monkeypatch.setattr(pipeline_v2, "_user_texts", lambda unit, row: ["你疯了了 这事为什么我做 你不能做吗"])


def test_a_class_lesson_retires_the_evolution_entry_it_replaces(cdf_env, said):
    from core.ingestion.api import connect_rw
    connect_rw().close()
    path = _evolution(cdf_env, _HANDBACK)
    unit = _class_lesson(overrides=[_ref(cdf_env, path, "Deferred Hive")])
    decision = run_unit_v2(_row(unit, source="S7", tainted=False), cdf_env["workspace"])
    assert decision.decision == "write"
    assert decision.version_vector.get("overrides") == [
        f".context/EVOLUTION.md#Corrections Captured#cand:{_CAND}.unit"]


def test_a_person_written_evolution_entry_is_never_overridden(cdf_env, said):
    from core.ingestion.api import connect_rw
    connect_rw().close()
    path = _evolution(cdf_env, _PERSON)
    unit = _class_lesson(overrides=[_ref(cdf_env, path, "Never push")])
    decision = run_unit_v2(_row(unit, source="S7", tainted=False), cdf_env["workspace"])
    assert not decision.version_vector.get("overrides")
    assert decision.version_vector["override_refused"][0]["reason"] == "human_written"


def test_a_unit_below_the_every_turn_floor_cannot_retire_an_every_turn_entry(cdf_env, said):
    """A unit going to a project's DDD did not pass the floor of the files read in every
    turn: it may not take an entry out of them."""
    from core.ingestion.api import connect_rw
    connect_rw().close()
    path = _evolution(cdf_env, _HANDBACK)
    unit = _class_lesson(type="pitfall", scope="project:P", topic="architecture",
                         overrides=[_ref(cdf_env, path, "Deferred Hive")])
    decision = run_unit_v2(_row(unit, source="S7", tainted=False, session_project="P"),
                           cdf_env["workspace"])
    assert not decision.version_vector.get("overrides")
    assert decision.version_vector["override_refused"][0]["reason"] == "every_turn_floor"


def test_project_ddd_overrides_still_work(cdf_env, said):
    from tests.test_brain_driven import _OLD, _ddd, _id_of
    from core.ingestion.api import connect_rw
    connect_rw().close()
    _ddd(cdf_env, _OLD)
    ref = f"{_id_of('browser')} | - [decision] **Open a Hive session in the browser**"
    unit = _cog(type="decision", scope="project:P", topic="decision", title="Open a Hive session in an overlay",
                what="The Hive session opens in an in-app overlay.", why="asked",
                rejected_alternatives="a browser tab", overrides=[ref], user_quote="不能做吗")
    decision = run_unit_v2(_row(unit, source="S7", tainted=False, session_project="P"), cdf_env["workspace"])
    assert decision.version_vector.get("overrides") == [
        "Projects/P/2-understanding/PROJECT.md#Recent Decisions#cand:cand-0123456789abcdef01234567.unit"]


def test_a_restatement_of_an_overridden_entry_that_a_live_entry_says_is_not_refused(
        cdf_env, said, monkeypatch):
    """Writing the whole current lesson and overriding the old entry carries the old text
    forward: saying it again later is the same as the live entry, not a restatement of
    something taken out."""
    from core.ingestion.api import connect_rw
    from core.ingestion.withdrawn import Record
    conn = connect_rw()
    conn.execute("INSERT INTO ingestion_inbox(id,idem_key,source,source_ref,lane,payload,payload_schema,"
                 "content_hash,envelope,project_snapshot,created_at,source_v2,extraction_pass) VALUES "
                 "('cand-live','k','S7','s0','session','{}','v','h','{}','{}','2026-11-02T00:00:00','S7','main')")
    conn.execute("INSERT INTO ingestion_units(unit_id,candidate_id,claim_group,state,action,reason,updated_at) "
                 "VALUES ('cand-live.unit','cand-live','{}','written','add','new','2026-11-02T00:00:00')")
    conn.execute("INSERT INTO evidence_roots(root_id,root_kind,locator,content_hash,trust,parent_root_ids,"
                 "observed_at,created_at) VALUES('root-1','user_message','m1','h','user','[]',"
                 "'2026-11-02T01:00:00','2026-11-02T01:00:00')")
    conn.commit()
    conn.close()
    body = ("When one call is refused, look up the operation I actually need before handing "
            "the task to the person.")
    live = (f"### C099 | 2026-11-02\n- **Correction**: **Hand back only what I cannot do myself** {body}\n"
            "<!-- cand:cand-live.unit -->")
    _evolution(cdf_env, live)
    old = f"### C068 | 2026-10-29\n- **Correction**: **Deferred Hive updates** {body} Old incident."
    monkeypatch.setattr(pipeline_v2, "_withdrawn_records", lambda: [
        Record("op1", ".context/EVOLUTION.md", old, "overridden", "overridden by: Hand back")])
    decision = run_unit_v2(_row(_class_lesson(what=body), source="S7", tainted=False), cdf_env["workspace"])
    assert (decision.decision, decision.reason) == ("reinforce", "reinforced")   # the same as the live one

    _evolution(cdf_env)                         # no live entry says it: still refused
    decision = run_unit_v2(_row(_class_lesson(what=body), source="S7", tainted=False), cdf_env["workspace"])
    assert (decision.decision, decision.reason) == ("reject", "withdrawn")


# ③ one entry is one kind of judgment that went wrong, not one incident

def test_the_review_writes_a_correction_as_the_kind_of_judgment_not_the_incident():
    from core.ingestion.sources import s7_review
    prompt = " ".join(s7_review.build_prompt(s7_review.ALLOWED_KINDS, {}, "", "record").split())
    assert "the kind of judgment that went wrong" in prompt
    assert "the incident's particulars" in prompt


def test_the_relation_turn_takes_the_kind_of_judgment_as_the_thing():
    from core.ingestion import relations_v2
    from core.ingestion.sources import s7_review
    unit = type("U", (), {"kind": "cognition", "type": "correction", "title": "Hand back only what I cannot do",
                          "what": "Look up the operation before handing a task to the person."})()
    entry = relations_v2.Entry("702305c2", ".context/EVOLUTION.md#Corrections Captured#cand:x",
                               ".context/EVOLUTION.md", "Corrections Captured", _HANDBACK)
    text = " ".join(s7_review.relation_prompt([unit], [[entry]]).split())
    assert "the same particular thing is the kind of judgment that went wrong" in text
