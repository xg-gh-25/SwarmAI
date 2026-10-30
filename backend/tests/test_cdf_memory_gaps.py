"""Memory gaps found 2026-10-31.

Questions:
- is a withdrawal whose entry was put back (status ``restored``) left out of what CDF
  treats as withdrawn, of the "user withdrew it" rate, and of the brain's change feed?
- does the "user withdrew it" rate count only what the person withdrew, not a cleanup the
  agent did (actor other than ``user``)?
- does an agent or world unit that no user words or measurement back go to the General
  Brain (read in every project), not to the DDD of the session's project?
"""
from __future__ import annotations

from tests.test_brain_driven import _unit, _write, cdf_env  # noqa: F401
from tests.test_cdf_refactor import _cog, _row, _ws, user_words  # noqa: F401

DOC = "Projects/P/2-understanding/TECH.md"


def test_an_undone_withdrawal_is_no_longer_a_withdrawal(cdf_env):
    from core.ingestion import queries, withdrawn
    from core.ingestion.api import connect_rw
    conn = connect_rw()
    try:
        _write(conn, "r-kept", "user_redact", DOC, absent=["- [pitfall] **Gone** body"],
               relations=[{"redact_anchor": f"{DOC}#C#cand:a", "status": "withdrawn", "reason": "r"}])
        _write(conn, "r-back", "user_redact", DOC, absent=["- [pitfall] **Back** body"],
               relations=[{"redact_anchor": f"{DOC}#C#cand:b", "status": "restored", "reason": "r"}])
        recs = withdrawn.records(conn)
        changes = queries.brain_changes(conn, "P", "2000-01-01")
    finally:
        conn.close()
    assert [r.op_id for r in recs] == ["r-kept"]
    assert [c["head"] for c in changes if c["kind"] == "withdraw"] == ["- [pitfall] **Gone** body"]


def test_the_user_withdrew_rate_counts_only_the_persons_withdrawals(cdf_env):
    from core.ingestion import queries
    from core.ingestion.api import connect_rw
    conn = connect_rw()
    try:
        for uid in ("p1", "a1", "b1"):
            conn.execute("INSERT INTO ingestion_inbox(id,idem_key,source,source_ref,lane,payload,"
                         "payload_schema,content_hash,envelope,project_snapshot,created_at,source_v2,"
                         "extraction_pass) VALUES (?,?,'S7','s','session','{}','v','h','{}','{}',"
                         "'2099-01-01T00:00:00','S7','main')", (uid, uid))
            conn.execute("INSERT INTO ingestion_units(unit_id,candidate_id,claim_group,state,"
                         "destination,updated_at) VALUES (?,?,'{}','written','{\"store\": \"MEMORY\"}',"
                         "'2099-01-01T00:00:00')", (uid + ".u", uid))
            _write(conn, "op-" + uid, "ingestion_unit", ".context/MEMORY.md", unit=uid + ".u",
                   present=[f"cand:cand-{uid}.unit"])
        anchor = ".context/MEMORY.md#Guidelines#cand:cand-{}.unit"
        _write(conn, "r-person", "user_redact", ".context/MEMORY.md",
               relations=[{"redact_anchor": anchor.format("p1"), "actor": "user", "status": "withdrawn"}])
        _write(conn, "r-agent", "user_redact", ".context/MEMORY.md",
               relations=[{"redact_anchor": anchor.format("a1"), "actor": "agent", "status": "withdrawn"}])
        _write(conn, "r-undone", "user_redact", ".context/MEMORY.md",
               relations=[{"redact_anchor": anchor.format("b1"), "actor": "user", "status": "restored"}])
        out = queries.review_health(conn, "2000-01-01")
    finally:
        conn.close()
    assert (out["agent_self_written"], out["agent_self_withdrawn"]) == (3, 1)


def test_an_unbacked_agent_or_world_unit_is_read_in_every_project(tmp_path, user_words):
    from core.ingestion.pipeline_v2 import run_unit_v2
    ws = _ws(tmp_path)
    for scope, path in (("agent", "Knowledge/Library/agent-self-knowledge.md"),
                        ("world", "Knowledge/Library/general-knowledge.md")):
        d = run_unit_v2(_row(_cog(scope=scope, user_quote=None), session_project="Widget"), ws)
        assert (d.destination.store, d.destination.relative_path) == ("COMMON_DDD", path)
