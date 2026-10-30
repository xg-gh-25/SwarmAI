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
