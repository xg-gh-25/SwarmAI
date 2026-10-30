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
                    "- [X] **Ship the export** Done on 10-29.\n\n"
                    "- [guideline] **Keep exports small** Still in force.\n\n"
                    "- [x] **Parent item** Done.\n  - [ ] child still open\n",
                    encoding="utf-8")
    imp = cdf_env["workspace"] / "Projects/P/2-understanding/IMPROVEMENT.md"
    imp.write_text("# Improvement\n\n## What to Watch For\n\n"
                   "- [x] **Tab refresh** fixed. gap kind: follow_up <!-- cand:cand-g1.unit -->\n",
                   encoding="utf-8")
    assert gaps.withdraw_ticked(cdf_env["workspace"], files=[path, imp]) == 2
    text = path.read_text(encoding="utf-8")
    assert "Ship the export" not in text and "Keep exports small" in text
    assert "Parent item" in text and "child still open" in text     # an open sub-item keeps it
    assert "Tab refresh" not in imp.read_text(encoding="utf-8")
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


# I2 / I3 / I10 ── a declared override, and only a verified one, retires the old entry

_OLD = ("- [decision] **Open a Hive session in the browser** The Hive session opens in a browser "
        "tab. **Why:** simplest. <!-- cand:cand-0123456789abcdef01234567.unit -->\n")
_HUMAN = "- [decision] **Keep exports under 1 MB** Written by a person.\n"


def _ddd(cdf_env, *entries):
    path = cdf_env["workspace"] / "Projects/P/2-understanding/PROJECT.md"
    path.write_text("# Project\n\n## Recent Decisions\n\n" + "\n".join(entries), encoding="utf-8")
    return path


def _id_of(title):
    from core import addressing
    return next(u.id for u in addressing.ddd_units("P").units if title in u.text)


def _check(conn, cdf_env, declared, *, source="S7", roots=(), cited_external=False, supported=True,
           home="P"):
    from core.ingestion import overrides
    return overrides.check(conn, workspace=cdf_env["workspace"], project="P", source=source,
                           declared=declared, new_root_ids=roots, cited_external=cited_external,
                           supported=supported, home=home)


def test_a_declared_override_names_the_entry_it_retires(cdf_env):
    from core.ingestion.api import connect_rw
    _ddd(cdf_env, _OLD)
    ref = f"{_id_of('browser')} | - [decision] **Open a Hive session in the browser**"
    conn = connect_rw()   # creates the store
    try:
        ok = _check(conn, cdf_env, [ref])
        wrong_source = _check(conn, cdf_env, [ref], source="S2")
        wrong_head = _check(conn, cdf_env, [f"{_id_of('browser')} | - [decision] **Something else entirely**"])
    finally:
        conn.close()
    assert ok.targets == ["Projects/P/2-understanding/PROJECT.md#Recent Decisions#cand:cand-0123456789abcdef01234567.unit"]
    assert (wrong_source.targets, wrong_source.refused[0]["reason"]) == ([], "source")
    assert (wrong_head.targets, wrong_head.refused[0]["reason"]) == ([], "not_found")


def test_a_person_written_entry_is_never_overridden(cdf_env):
    from core.ingestion.api import connect_rw
    _ddd(cdf_env, _HUMAN)
    conn = connect_rw()   # creates the store
    try:
        out = _check(conn, cdf_env, [f"{_id_of('1 MB')} | - [decision] **Keep exports under 1 MB**"])
    finally:
        conn.close()
    assert (out.targets, out.refused[0]["reason"]) == ([], "human_written")


@pytest.mark.parametrize("new_at,expect", [("2026-10-29T00:00:00", "older"),
                                            ("2026-10-30T09:00:00", "not_later"),
                                            ("2026-10-31T00:00:00", "retire")])
def test_an_override_goes_by_when_it_was_said(cdf_env, new_at, expect):
    from core.ingestion.api import connect_rw
    _ddd(cdf_env, _OLD)
    conn = connect_rw()
    try:
        for root, at in (("r-old", "2026-10-30T09:00:00"), ("r-new", new_at)):
            conn.execute("INSERT INTO evidence_roots(root_id,root_kind,locator,content_hash,trust,"
                         "parent_root_ids,observed_at,created_at) VALUES(?,'user_message',?,'h','user','[]',?,?)",
                         (root, root, at, at))
        conn.execute("INSERT INTO ingestion_inbox(id,idem_key,source,source_ref,lane,payload,payload_schema,"
                     "content_hash,envelope,project_snapshot,created_at,evidence_root_ids) VALUES "
                     "('cand-0123456789abcdef01234567','k','S7','s','session','{}','v','h','{}','{}','t',?)",
                     (json.dumps(["r-old"]),))
        conn.commit()
        out = _check(conn, cdf_env, [f"{_id_of('browser')} | - [decision] **Open a Hive session in the browser**"],
                     roots=["r-new"])
    finally:
        conn.close()
    got = "older" if out.older else ("retire" if out.targets else out.refused[0]["reason"])
    assert got == expect


def test_a_written_unit_retires_what_it_overrides_once(cdf_env):
    from core.ingestion import overrides, queries, withdrawn
    from core.ingestion.api import connect_rw
    path = _ddd(cdf_env, _OLD, "- [decision] **Open a Hive session in an in-app overlay** body\n")
    anchor = "Projects/P/2-understanding/PROJECT.md#Recent Decisions#cand:cand-0123456789abcdef01234567.unit"
    conn = connect_rw()
    try:
        _unit(conn, "n", title="Open a Hive session in an in-app overlay")
        conn.execute("UPDATE ingestion_units SET decision=? WHERE unit_id='n.u'",
                     (json.dumps({"version_vector": {"overrides": [anchor]}}),))
        conn.commit()
        assert overrides.apply(conn, "n.u") == 1
        from core.ingestion import governed_edit
        tried = []
        real = governed_edit.withdraw
        governed_edit.withdraw = lambda *a, **k: tried.append(a) or real(*a, **k)
        try:
            assert overrides.apply(conn) == 0                              # recorded once
        finally:
            governed_edit.withdraw = real
        assert tried == []                                                 # not even tried again
        recs = withdrawn.records(conn)
        kinds = [c["kind"] for c in queries.brain_changes(conn, "P", "2000-01-01")]
    finally:
        conn.close()
    text = path.read_text(encoding="utf-8")
    assert "in the browser" not in text and "in-app overlay" in text
    assert [(r.status, r.reason) for r in recs] == [("overridden", "overridden by: Open a Hive session in an in-app overlay")]
    assert kinds == ["override"]


@pytest.mark.parametrize("source,retires", [("S7", True), ("S2", False)])
def test_the_pipeline_records_a_verified_override_for_the_writer(cdf_env, monkeypatch, source, retires):
    from tests.test_cdf_refactor import Judge
    from core.ingestion.api import connect_rw
    connect_rw().close()
    _ddd(cdf_env, _OLD)
    monkeypatch.setattr(pipeline_v2, "_grounding", lambda anchors: "user: open it in an in-app overlay, not the browser")
    ref = f"{_id_of('browser')} | - [decision] **Open a Hive session in the browser**"
    unit = _cog(type="decision", scope="project:P", topic="decision", title="Open a Hive session in an in-app overlay",
                what="The Hive session opens in an in-app overlay.", why="The user asked for it.",
                rejected_alternatives="a browser tab", overrides=[ref])
    decision = run_unit_v2(_row(unit, source=source, tainted=False, session_project="P"), cdf_env["workspace"], Judge())
    assert decision.decision == "write"
    got = decision.version_vector.get("overrides") or []
    assert bool(got) is retires
    if not retires:
        assert decision.version_vector["override_refused"][0]["reason"] == "source"


def test_a_unit_older_than_the_entry_it_overrides_is_not_written(cdf_env, monkeypatch):
    from tests.test_cdf_refactor import Judge
    from core.ingestion import overrides
    from core.ingestion.api import connect_rw
    connect_rw().close()
    _ddd(cdf_env, _OLD)
    monkeypatch.setattr(pipeline_v2, "_grounding", lambda anchors: "user: open it in an in-app overlay")
    monkeypatch.setattr(overrides, "check", lambda conn, **kw: overrides.Checked(older=True))
    unit = _cog(type="decision", scope="project:P", topic="decision", title="Overlay",
                what="The Hive session opens in an in-app overlay.", why="asked",
                rejected_alternatives="none", overrides=["0123abcd | - [decision] **Open a Hive session**"])
    decision = run_unit_v2(_row(unit, source="S7", tainted=False, session_project="P"), cdf_env["workspace"], Judge())
    assert (decision.decision, decision.reason) == ("reject", "older_than_existing")


# I9 ── no age decay, no weekly self-audit, no usage bookkeeping for decay, in production code
_GONE = ("ddd_self_audit", "ddd_drift_signal", "ddd_drift_fix", ".ddd-usage.json",
         "record_ddd_hit", "assess_decay(", "ddd_maturity", "enqueue_refine_unit",
         "_filter_dormant_entries", "_filter_inactive_memory")


def test_production_code_has_no_decay_audit_or_usage_writer():
    from pathlib import Path
    backend = Path(__file__).resolve().parents[1]
    hits = []
    for top in ("core", "jobs", "routers", "hooks", "scripts", "channels", "utils"):
        for py in sorted((backend / top).rglob("*.py")) if (backend / top).is_dir() else ():
            if "/tests/" in str(py):
                continue
            text = py.read_text(encoding="utf-8", errors="replace")
            hits += [f"{py.relative_to(backend)}: {name}" for name in _GONE if name in text]
    assert hits == []


# I4 ── no reader hides an entry for a mark or a relation
def test_no_selection_skips_an_entry_for_a_superseded_mark(tmp_path):
    import sqlite3
    from core.memory_injection import select_context_file
    path = tmp_path / ".context" / "MEMORY.md"
    path.parent.mkdir()
    path.write_text("# Memory\n\n## Guidelines\n"
                    "- [guideline] **MARKED_ENTRY_b7** still in the file. (2026-10-01)\n"
                    "  <!-- ref:0 | last:none | decay:active | superseded_by:Other -->\n\n"
                    "## Processes\n"
                    f"- [process] **too long** {'y' * 1500} (2026-10-01)\n", encoding="utf-8")
    for budget in (100_000, 500):             # under budget the file goes whole; over, units compete
        sel = select_context_file(path, budget=budget, estimate=len, conn=sqlite3.connect(":memory:"))
        assert "MARKED_ENTRY_b7" in sel.text, budget


# review round 1 ── the fixes, each with the failure it prevents

def test_an_external_unsupported_or_foreign_project_unit_retires_nothing(cdf_env):
    from core.ingestion.api import connect_rw
    _ddd(cdf_env, _OLD)
    ref = f"{_id_of('browser')} | - [decision] **Open a Hive session in the browser**"
    conn = connect_rw()
    try:
        ok = _check(conn, cdf_env, [ref])
        external = _check(conn, cdf_env, [ref], cited_external=True)
        unsupported = _check(conn, cdf_env, [ref], supported=False)
        foreign = _check(conn, cdf_env, [ref], home="OtherProject")
        nohome = _check(conn, cdf_env, [ref], home=None)
    finally:
        conn.close()
    assert ok.targets
    assert (external.targets, external.refused[0]["reason"]) == ([], "cited_external")
    assert (unsupported.targets, unsupported.refused[0]["reason"]) == ([], "not_supported")
    assert (foreign.targets, foreign.refused[0]["reason"]) == ([], "outside_home_project")
    assert nohome.targets == []


def test_a_run_that_landed_nothing_declares_no_override():
    from types import SimpleNamespace
    from core.ingestion.sources import s3_run
    unit = SimpleNamespace(overrides=["0123abcd | - [decision] **X**"],
                           model_copy=lambda update: SimpleNamespace(**{"overrides": update["overrides"]}))
    landed = SimpleNamespace(run_id="r", projection=lambda: {"landing": "landed"})
    unlanded = SimpleNamespace(run_id="r", projection=lambda: {"landing": "not_landed"})
    assert s3_run._drop_overrides_unless_landed(landed, [unit])[0].overrides
    assert s3_run._drop_overrides_unless_landed(unlanded, [unit])[0].overrides is None


def test_an_automated_withdrawal_reaches_only_cdf_entries_of_main_files(cdf_env):
    from core.ingestion import governed_edit
    from core.ingestion.api import connect_rw
    connect_rw().close()
    ws = cdf_env["workspace"]
    (ws / ".context/STEERING.md").write_text(
        "# Steering\n\n## Standing Rules\n\n- [guideline] **Rule** text. <!-- cand:cand-s1.unit -->\n",
        encoding="utf-8")
    (cdf_env["ddd"] / "TECH.md").write_text(
        "# Tech\n\n## Conventions\n\n- [guideline] **Human rule** written by a person.\n", encoding="utf-8")
    from core.ingestion.units import parse_units
    steering = parse_units(ws / ".context/STEERING.md", root=ws)[0].anchor
    human = parse_units(cdf_env["ddd"] / "TECH.md", root=ws)[0].anchor
    with pytest.raises(PermissionError):
        governed_edit.withdraw(steering, "x", actor="cdf:override", status="overridden")
    with pytest.raises(PermissionError):
        governed_edit.withdraw(human, "x", actor="cdf:override", status="overridden")
    assert "Human rule" in (cdf_env["ddd"] / "TECH.md").read_text(encoding="utf-8")


def test_a_withdrawal_takes_the_entrys_detail_tail_with_it(cdf_env):
    from core.ingestion import governed_edit
    from core.ingestion.api import connect_rw
    from core.ingestion.units import parse_units
    connect_rw().close()
    mem = cdf_env["workspace"] / ".context/MEMORY.md"
    mem.write_text("# Memory\n\n## Guidelines\n\n"
                   "- [guideline] **Gone** old. <!-- cand:cand-m1.unit -->\n"
                   "  <!-- ref:0 | last:none | decay:active | source:auto -->\n"
                   "  Detail: DailyActivity/2026-10-25.md.\n"
                   "  <!-- valid_from: 2026-10-25 | superseded_by: null | confidence: high -->\n\n"
                   "- [guideline] **Stays** kept.\n", encoding="utf-8")
    anchor = next(u.anchor for u in parse_units(mem, root=cdf_env["workspace"]) if "Gone" in u.text)
    governed_edit.withdraw(anchor, "x", actor="cdf:maintenance", status="done")
    text = mem.read_text(encoding="utf-8")
    assert "Gone" not in text and "Detail:" not in text and "valid_from" not in text
    assert "**Stays** kept." in text


def test_a_withdrawal_planned_on_a_file_that_changed_is_stale(cdf_env, monkeypatch):
    from core.ingestion import governed_edit
    from core.ingestion.api import connect_rw
    from core.ingestion.units import parse_units
    connect_rw().close()
    mem = cdf_env["workspace"] / ".context/MEMORY.md"
    mem.write_text("# Memory\n\n## Guidelines\n\n- [guideline] **Gone** old. <!-- cand:cand-m2.unit -->\n",
                   encoding="utf-8")
    anchor = parse_units(mem, root=cdf_env["workspace"])[0].anchor
    real = governed_edit._redaction_span

    def span_then_user_saves(target, a):
        out = real(target, a)
        target.write_text(target.read_text(encoding="utf-8") + "\n- [guideline] **User save** new.\n",
                          encoding="utf-8")
        return out
    monkeypatch.setattr(governed_edit, "_redaction_span", span_then_user_saves)
    with pytest.raises(governed_edit.StaleRevision):
        governed_edit.withdraw(anchor, "x", actor="cdf:maintenance", status="done")
    assert "User save" in mem.read_text(encoding="utf-8")
