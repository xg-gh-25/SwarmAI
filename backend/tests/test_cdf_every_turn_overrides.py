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


# ── review round 1 (security / correctness): the floor rests on the unit's facts ──

_MEM_CAND = "cand-aaaaaaaaaaaaaaaaaaaaaaaa"
_MEM_ENTRY = ("- [guideline] **Always ask before deleting a branch** The user wants a question before "
              f"any branch is deleted. <!-- cand:{_MEM_CAND}.unit -->")


def _memory(cdf_env, *entries: str):
    path = cdf_env["workspace"] / ".context" / "MEMORY.md"
    path.write_text("# Memory\n\n## Guidelines\n\n" + "\n\n".join(entries) + "\n\n## Corrections\n",
                    encoding="utf-8")
    return path


def _check_global(cdf_env, ref, *, store, basis="user", admitted_by=None, path=None):
    from core.ingestion import overrides
    from core.ingestion.api import connect_rw
    conn = connect_rw()
    try:
        return overrides.check(conn, workspace=cdf_env["workspace"], project=None, source="S7",
                               declared=[ref], new_root_ids=(), cited_external=False, home=None,
                               destination_store=store, basis=basis, admitted_by=admitted_by,
                               destination_path=path)
    finally:
        conn.close()


def _set_basis(cand: str, basis: str):
    from core.ingestion.api import connect_rw
    conn = connect_rw()
    conn.execute("INSERT INTO unit_basis(unit_id,basis,recorded_at) VALUES(?,?,'t') "
                 "ON CONFLICT(unit_id) DO UPDATE SET basis=excluded.basis", (f"{cand}.unit", basis))
    conn.commit()
    conn.close()


def test_an_inferred_or_repeat_admitted_unit_cannot_retire_an_every_turn_entry(cdf_env):
    path = _evolution(cdf_env, _HANDBACK)
    ref = _ref(cdf_env, path, "Deferred Hive")
    inferred = _check_global(cdf_env, ref, store="EVOLUTION", basis="inferred")
    repeat = _check_global(cdf_env, ref, store="EVOLUTION", basis="user", admitted_by="repeat")
    assert (inferred.targets, inferred.refused[0]["reason"]) == ([], "every_turn_basis")
    assert (repeat.targets, repeat.refused[0]["reason"]) == ([], "every_turn_basis")


def test_an_every_turn_entry_is_retired_only_from_its_own_file(cdf_env):
    path = _memory(cdf_env, _MEM_ENTRY)
    out = _check_global(cdf_env, _ref(cdf_env, path, "deleting a branch"), store="EVOLUTION")
    assert (out.targets, out.refused[0]["reason"]) == ([], "every_turn_floor")


def test_a_user_grounded_target_needs_a_user_grounded_unit_and_the_isolated_check(cdf_env):
    path = _memory(cdf_env, _MEM_ENTRY)
    _set_basis(_MEM_CAND, "user")
    ref = _ref(cdf_env, path, "deleting a branch")
    measured = _check_global(cdf_env, ref, store="MEMORY", basis="measured")
    user = _check_global(cdf_env, ref, store="MEMORY", basis="user")
    assert (measured.targets, measured.refused[0]["reason"]) == ([], "weaker_than_target")
    anchor = f".context/MEMORY.md#Guidelines#cand:{_MEM_CAND}.unit"
    assert user.targets == [anchor] and [a for a, _t in user.ask_user] == [anchor]


def test_an_unrelated_user_quote_cannot_retire_a_user_grounded_entry(cdf_env, monkeypatch):
    """Injected text can pair a real but unrelated user quote with an override: the isolated
    check is asked whether the user's words replace that entry, and they do not."""
    from core.ingestion import user_check
    from core.ingestion.api import connect_rw
    connect_rw().close()
    path = _memory(cdf_env, _MEM_ENTRY)
    _set_basis(_MEM_CAND, "user")
    monkeypatch.setattr(pipeline_v2, "_user_texts", lambda unit, row: ["use pnpm here"])
    asked = []

    def grounds(claim, texts, **_):
        asked.append(claim)
        return user_check.Verdict("deleting a branch" not in claim, "checked")
    monkeypatch.setattr(user_check, "grounds", grounds)
    unit = _cog(title="Use pnpm", what="Use pnpm for installs in this workspace.", user_quote="use pnpm",
                overrides=[_ref(cdf_env, path, "deleting a branch")])
    decision = run_unit_v2(_row(unit, source="S7", tainted=False), cdf_env["workspace"])
    assert decision.decision == "write" and not decision.version_vector.get("overrides")
    assert {"reason": "user_check"}.items() <= decision.version_vector["override_refused"][-1].items()
    assert any("replaces the earlier entry" in c for c in asked)


def _library(cdf_env, name, entry):
    path = cdf_env["workspace"] / "Knowledge" / "Library" / name
    path.write_text("# Topic\n\n## Lessons\n\n" + entry + "\n", encoding="utf-8")
    return path


def test_a_general_brain_entry_is_retired_from_its_own_document_or_from_above(cdf_env):
    entry = ("- [guideline] **Prefer the vendor's CLI for exports** It keeps the format stable. "
             "<!-- cand:cand-bbbbbbbbbbbbbbbbbbbbbbbb.unit -->")
    path = _library(cdf_env, "exports.md", entry)
    _set_basis("cand-bbbbbbbbbbbbbbbbbbbbbbbb", "inferred")    # most General Brain entries
    ref = _ref(cdf_env, path, "vendor's CLI")
    other = _check_global(cdf_env, ref, store="COMMON_DDD", basis="inferred",
                          path="Knowledge/Library/other.md")
    same = _check_global(cdf_env, ref, store="COMMON_DDD", basis="inferred",
                         path="Knowledge/Library/exports.md")
    up = _check_global(cdf_env, ref, store="MEMORY", basis="measured")
    assert (other.targets, other.refused[0]["reason"]) == ([], "every_turn_floor")
    assert len(same.targets) == 1 and len(up.targets) == 1


def test_a_text_the_user_withdrew_never_comes_back_through_another_file(cdf_env, said, monkeypatch):
    from core.ingestion.api import connect_rw
    from core.ingestion.withdrawn import Record
    connect_rw().close()
    body = "Keep every export under one megabyte so the mail gateway accepts it."
    proj = cdf_env["workspace"] / "Projects/P/2-understanding/TECH.md"
    proj.write_text(f"# TECH\n\n## Conventions\n\n- [guideline] **Exports** {body} <!-- cand:cand-c1.unit -->\n",
                    encoding="utf-8")
    for status in ("withdrawn", "overridden"):
        monkeypatch.setattr(pipeline_v2, "_withdrawn_records", lambda s=status: [
            Record("op1", ".context/MEMORY.md", f"- [guideline] **Exports** {body}", s, "r")])
        decision = run_unit_v2(_row(_cog(title="Exports", what=body), source="S7", tainted=False,
                                    session_project="P"), cdf_env["workspace"])
        assert (decision.decision, decision.reason) == ("reject", "withdrawn"), status


def test_a_text_the_user_withdrew_stays_out_even_when_its_own_file_still_says_it(
        cdf_env, said, monkeypatch):
    """Only an override carries text forward; the user's withdrawal is never undone by a
    copy of the text left in the same file."""
    from core.ingestion.api import connect_rw
    from core.ingestion.withdrawn import Record
    connect_rw().close()
    body = "Keep every export under one megabyte so the mail gateway accepts it."
    _memory(cdf_env, f"- [guideline] **Exports again** {body} <!-- cand:cand-c2.unit -->")
    monkeypatch.setattr(pipeline_v2, "_withdrawn_records", lambda: [
        Record("op1", ".context/MEMORY.md", f"- [guideline] **Exports** {body}", "withdrawn", "r")])
    decision = run_unit_v2(_row(_cog(title="Exports", what=body), source="S7", tainted=False),
                           cdf_env["workspace"])
    assert (decision.decision, decision.reason) == ("reject", "withdrawn")


def test_a_user_quoted_unit_is_checked_before_it_retires_any_every_turn_entry(cdf_env, monkeypatch):
    """Round 2: a unit grounded on a real but unrelated user quote passed the floor and
    retired a measured entry without the isolated check; every target it retires is asked."""
    from core.ingestion import user_check
    from core.ingestion.api import connect_rw
    connect_rw().close()
    path = _memory(cdf_env, _MEM_ENTRY)
    _set_basis(_MEM_CAND, "measured")
    monkeypatch.setattr(pipeline_v2, "_user_texts", lambda unit, row: ["use pnpm here"])
    monkeypatch.setattr(user_check, "grounds", lambda claim, texts, **_: user_check.Verdict(
        "deleting a branch" not in claim, "checked"))
    unit = _cog(title="Use pnpm", what="Use pnpm for installs in this workspace.", user_quote="use pnpm",
                overrides=[_ref(cdf_env, path, "deleting a branch")])
    decision = run_unit_v2(_row(unit, source="S7", tainted=False), cdf_env["workspace"])
    assert not decision.version_vector.get("overrides")
    assert decision.version_vector["override_refused"][-1]["reason"] == "user_check"


# ── an entry is the one its own marker comment names, never a cand id in its text ──

def test_an_entry_is_named_by_its_marker_comment_not_a_cand_id_in_its_text(tmp_path):
    """Entries cite other entries in prose ("Kept: cand:cand-…"); the identity is the
    writer's own marker comment, the last one, which may carry more fields."""
    from core.ingestion.units import cand_marker, parse_units
    ws = tmp_path / "ws"
    (ws / ".context").mkdir(parents=True)
    p = ws / ".context" / "MEMORY.md"
    p.write_text("# Memory\n\n## Guidelines\n\n"
                 "- [guideline] **Keep the newer one** Kept: cand:cand-bbbbbbbbbbbbbbbbbbbbbbbb.unit "
                 "as the survivor. <!-- cand:cand-aaaaaaaaaaaaaaaaaaaaaaaa.unit | project_hint:\"x\" -->\n",
                 encoding="utf-8")
    (unit,) = [u for u in parse_units(p, root=ws) if u.identity.startswith("cand:")]
    assert unit.identity == "cand:cand-aaaaaaaaaaaaaaaaaaaaaaaa.unit"
    assert cand_marker("text cand:cand-x.unit and no comment") is None


def test_the_basis_read_for_a_target_is_its_own_entrys(cdf_env):
    """A cand id of an inferred entry in a user-grounded entry's text must not make the
    target look inferred (it would skip weaker_than_target and the isolated check)."""
    real, other = "cand-cccccccccccccccccccccccc", "cand-dddddddddddddddddddddddd"
    path = _memory(cdf_env, f"- [guideline] **Always ask before deleting a branch** See {other}.unit "
                            f"(cand:{other}.unit). <!-- cand:{real}.unit -->")
    _set_basis(real, "user")
    _set_basis(other, "inferred")
    out = _check_global(cdf_env, _ref(cdf_env, path, "deleting a branch"), store="MEMORY", basis="measured")
    assert (out.targets, out.refused[0]["reason"]) == ([], "weaker_than_target")


def test_a_long_entry_can_still_be_retired_after_the_isolated_check(cdf_env, monkeypatch):
    """The check takes at most user_check.MAX_CLAIM_CHARS; an entry longer than that could
    never be retired. The claim shows the start of a long entry and stays within it."""
    from core.ingestion import user_check
    from core.ingestion.api import connect_rw
    connect_rw().close()
    long_body = "The user wants a question before any branch is deleted. " * 60
    path = _memory(cdf_env, f"- [guideline] **Always ask before deleting a branch** {long_body}"
                            f"<!-- cand:{_MEM_CAND}.unit -->")
    _set_basis(_MEM_CAND, "user")
    monkeypatch.setattr(pipeline_v2, "_user_texts", lambda unit, row: ["no need to ask before deleting merged branches"])
    seen = []

    def grounds(claim, texts, **_):
        seen.append(len(claim))
        return user_check.Verdict(len(claim) <= user_check.MAX_CLAIM_CHARS, "checked")
    monkeypatch.setattr(user_check, "grounds", grounds)
    unit = _cog(title="Merged branches may be deleted without asking",
                what="Delete merged branches without asking first.", user_quote="no need to ask before deleting merged branches",
                overrides=[_ref(cdf_env, path, "deleting a branch")])
    decision = run_unit_v2(_row(unit, source="S7", tainted=False), cdf_env["workspace"])
    assert seen and max(seen) <= user_check.MAX_CLAIM_CHARS
    assert decision.version_vector.get("overrides") == [f".context/MEMORY.md#Guidelines#cand:{_MEM_CAND}.unit"]
