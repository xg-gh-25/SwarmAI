"""Brain-driven v5 (docs/design/2026-10-30-brain-driven.md): the main cognition files are the
current valid whole; a change is an add, an override or a withdrawal, recorded outside them."""
from __future__ import annotations

import json

import pytest

from tests.test_cdf_v2_e2e import cdf_env  # noqa: F401  (fixture)


def _unit(conn, uid, *, action="add", reason="new", title="T", source="S7"):
    conn.execute("INSERT INTO ingestion_inbox(id,idem_key,source,source_ref,lane,payload,payload_schema,"
                 "content_hash,envelope,project_snapshot,created_at,source_v2,extraction_pass) VALUES "
                 "(?,?,?,'s','session',?,'v','h','{}','{}','2099-01-01T00:00:00',?,'main')",
                 (uid, uid, source, json.dumps({"title": title}), source))
    conn.execute("INSERT INTO ingestion_units(unit_id,candidate_id,claim_group,state,action,reason,"
                 "updated_at) VALUES (?,?,'{}','written',?,?,'2099-01-01T00:00:00')",
                 (uid + ".u", uid, action, reason))


def _write(conn, op, kind, path, *, unit=None, absent=(), relations=(), at="2099-01-02T00:00:00",
           present=()):
    conn.execute("INSERT INTO ingestion_writes(op_id,op_kind,unit_id,target_path,anchors_present,"
                 "anchors_absent,relations,step,created_at,updated_at) VALUES (?,?,?,?,?,?,?,'done',?,?)",
                 (op, kind, unit, path, json.dumps(list(present)), json.dumps(list(absent)),
                  json.dumps(list(relations)), at, at))


# I7 ── the statistics come from the records and match the write journal row for row

def test_brain_changes_lists_adds_overrides_withdrawals_and_conflicts(cdf_env):
    from core.ingestion import queries
    from core.ingestion.api import connect_rw
    conn = connect_rw()
    try:
        _unit(conn, "a", title="New decision")
        _unit(conn, "o", action="replace", title="Corrected decision")
        _unit(conn, "c", reason="contradicts", title="Disputed claim")
        doc = "Projects/P/2-understanding/TECH.md"
        _write(conn, "w-a", "ingestion_unit", doc, unit="a.u")
        _write(conn, "w-o", "ingestion_unit", doc, unit="o.u", absent=["- [decision] **Old** body"])
        _write(conn, "w-c", "ingestion_unit", doc, unit="c.u")
        _write(conn, "w-r", "user_redact", doc, absent=["- [pitfall] **Wrong fix** body"],
               relations=[{"redact_anchor": f"{doc}#Conventions#cand:x", "reason": "reverses the fix"}])
        _write(conn, "w-other", "ingestion_unit", "Projects/Q/2-understanding/TECH.md", unit="a.u")
        changes = queries.brain_changes(conn, "P", "2000-01-01")
    finally:
        conn.close()
    assert [c["kind"] for c in changes] == ["add", "override", "conflict", "withdraw"]
    assert changes[1]["text"] == "- [decision] **Old** body"          # the overridden original
    assert changes[3]["head"] == "- [pitfall] **Wrong fix** body"
    assert changes[3]["reason"] == "reverses the fix"
    assert queries.brain_change_summary(changes) == {"add": 1, "override": 1, "withdraw": 1,
                                                     "conflict": 1, "user_edit": 0}


def test_review_health_counts_a_withdrawal_recorded_in_production_shape(cdf_env):
    """A write records ``cand:<id>``; a redaction records the whole anchor. The count compared
    them as strings and was always 0 in production."""
    from core.ingestion import queries
    from core.ingestion.api import connect_rw
    conn = connect_rw()
    try:
        for uid in ("k1", "w1"):
            conn.execute("INSERT INTO ingestion_inbox(id,idem_key,source,source_ref,lane,payload,"
                         "payload_schema,content_hash,envelope,project_snapshot,created_at,source_v2,"
                         "extraction_pass) VALUES (?,?,'S7','s','session','{}','v','h','{}','{}',"
                         "'2099-01-01T00:00:00','S7','main')", (uid, uid))
            conn.execute("INSERT INTO ingestion_units(unit_id,candidate_id,claim_group,state,"
                         "destination,updated_at) VALUES (?,?,'{}','written','{\"store\": \"MEMORY\"}',"
                         "'2099-01-01T00:00:00')", (uid + ".u", uid))
            _write(conn, "op-" + uid, "ingestion_unit", ".context/MEMORY.md", unit=uid + ".u",
                   present=[f"cand:cand-{uid}.unit"])
        _write(conn, "r1", "user_redact", ".context/MEMORY.md",
               relations=[{"redact_anchor": ".context/MEMORY.md#Guidelines#cand:cand-w1.unit"}])
        out = queries.review_health(conn, "2000-01-01")
    finally:
        conn.close()
    assert (out["agent_self_written"], out["agent_self_withdrawn"]) == (2, 1)


# I6 ── what the user withdrew does not come back; a closed gap that recurs is written again

from tests.test_cdf_refactor import _cog, _hits, _row, _ws, user_words  # noqa: E402,F401
from core.ingestion import pipeline_v2  # noqa: E402
from core.ingestion.pipeline_v2 import run_unit_v2  # noqa: E402
from core.ingestion.withdrawn import Record  # noqa: E402

_WRONG = ("- [process] **Use a symlink guard for nested node_modules** Create a symbolic link at "
          "the nested node_modules path before npm install so the rename cannot collide.")


class _PerHitJudge:
    """Says *relation* about the withdrawn hit and "unrelated" about every other hit."""
    def __init__(self, relation):
        self.relation, self.calls = relation, 0

    def verify(self, unit, grounding, hits):
        self.calls += 1
        return {"grounded": True, "durable": True, "self_contained": True, "significant": True,
                "hits": [{"anchor": f"h{i}", "relation": self.relation
                          if h["anchor"].startswith("withdrawn:") else "unrelated"}
                         for i, h in enumerate(hits, 1)]}


def _withdrawn(monkeypatch, status="withdrawn"):
    monkeypatch.setattr(pipeline_v2, "_withdrawn_records",
                        lambda: [Record("op1", "Projects/P/2-understanding/TECH.md", _WRONG, status, "reverses the fix")])


def _cog_like(**over):
    fields = {"title": "Symlink guard for nested node_modules",
              "what": "Create a symlink at the nested node_modules path before npm install."}
    fields.update(over)
    return _cog(**fields)


def test_an_exact_restatement_of_a_withdrawn_entry_is_refused_before_the_judge(tmp_path, user_words, monkeypatch):
    _hits(monkeypatch, ".context/MEMORY.md#Guidelines#cand:old")
    _withdrawn(monkeypatch)
    judge = _PerHitJudge("unrelated")
    unit = _cog_like(what="Create a symbolic link at the nested node_modules path before npm install "
                          "so the rename cannot collide.")
    decision = run_unit_v2(_row(unit), _ws(tmp_path), judge)
    assert (decision.decision, decision.reason, judge.calls) == ("reject", "withdrawn", 0)


@pytest.mark.parametrize("relation", ["same", "adds"])
def test_a_unit_the_judge_ties_to_a_withdrawn_entry_is_refused(tmp_path, user_words, monkeypatch, relation):
    _hits(monkeypatch, ".context/MEMORY.md#Guidelines#cand:old")
    _withdrawn(monkeypatch)
    judge = _PerHitJudge(relation)
    decision = run_unit_v2(_row(_cog_like()), _ws(tmp_path), judge)
    assert (decision.decision, decision.reason, judge.calls) == ("reject", "withdrawn", 1)


@pytest.mark.parametrize("relation", ["same", "adds"])
def test_a_closed_gap_that_recurs_is_written_again(tmp_path, user_words, monkeypatch, relation):
    _hits(monkeypatch, ".context/MEMORY.md#Guidelines#cand:old")
    _withdrawn(monkeypatch, status="closed")
    decision = run_unit_v2(_row(_cog_like()), _ws(tmp_path), _PerHitJudge(relation))
    assert decision.decision == "write"


def test_a_unit_saying_the_withdrawn_entry_was_wrong_is_written(tmp_path, user_words, monkeypatch):
    _hits(monkeypatch, ".context/MEMORY.md#Guidelines#cand:old")
    _withdrawn(monkeypatch)
    decision = run_unit_v2(_row(_cog_like()), _ws(tmp_path), _PerHitJudge("contradicts"))
    assert decision.decision == "write"


def test_withdrawn_records_are_read_from_the_write_journal(cdf_env):
    from core.ingestion import withdrawn
    from core.ingestion.api import connect_rw
    conn = connect_rw()
    try:
        _write(conn, "r1", "user_redact", "Projects/P/2-understanding/TECH.md", absent=[_WRONG],
               relations=[{"redact_anchor": "x#y#cand:z", "reason": "reverses the fix", "status": "closed"}])
        recs = withdrawn.records(conn)
    finally:
        conn.close()
    assert [(r.op_id, r.text, r.status, r.reason) for r in recs] == [("r1", _WRONG, "closed", "reverses the fix")]


# I5 / I7 ── a finished item leaves the main file; CDF never withdraws an entry on its own

def test_ticked_entries_are_withdrawn_as_done_or_closed(cdf_env):
    from core.ingestion import gaps, withdrawn
    from core.ingestion.api import connect_ro
    path = cdf_env["workspace"] / "Projects/P/2-understanding/PROJECT.md"
    path.write_text("# Project\n\n## Open Items\n\n"
                    "- [x] **Ship the export** Done on 10-29.\n\n"
                    "- [guideline] **Keep exports small** Still in force.\n\n"
                    "- [x] **Tab refresh** fixed. gap kind: follow_up <!-- cand:cand-g1.unit -->\n",
                    encoding="utf-8")
    assert gaps.withdraw_ticked(cdf_env["workspace"], files=[path]) == 2
    text = path.read_text(encoding="utf-8")
    assert "- [x]" not in text and "Keep exports small" in text
    conn = connect_ro()
    try:
        got = sorted((r.status, r.text.split("**")[1]) for r in withdrawn.records(conn))
    finally:
        conn.close()
    assert got == [("closed", "Tab refresh"), ("done", "Ship the export")]
    assert gaps.withdraw_ticked(cdf_env["workspace"], files=[path]) == 0      # idempotent


def test_cdf_cannot_withdraw_an_entry_only_close_or_finish_one(cdf_env):
    from core.ingestion.governed_edit import withdraw
    with pytest.raises(PermissionError):
        withdraw("Projects/P/2-understanding/TECH.md#Conventions#cand:x", "r",
                 actor="cdf:maintenance", status="withdrawn")
    with pytest.raises(ValueError):
        withdraw("Projects/P/2-understanding/TECH.md#Conventions#cand:x", "r",
                 actor="user", status="superseded")
