"""Fixes from the launch review round (2026-10-29-cdf-launch §6 review, §7).

- An abandoned plan is not a live plan: a unit with one is deferred on error and
  requeued by recovery, never left in ``writing`` or retried in a tight loop.
- A plan whose entry is in the file is finished by recovery, never abandoned (the
  retry of an abandoned one appended the entry twice).
- The daily recovery leaves alone a unit that moved in the last minutes; startup
  recovery runs before the run lane starts.
- The session-job thread survives a failing recovery.
- A unit refused for quota is not judged again while its store's budget is spent.
- Refine keeps the old entry's taint; an installed package's file cannot be moved
  out of its project; a rename refuses a change made after the copy.
- Extraction: the list decides a unit's kind, an undecodable reply is called once
  more, and one undecodable window does not fail the session (all of them do).
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from tests.test_cdf_v2_e2e import Judge, _drain, cdf_env  # noqa: F401
from tests.test_cdf_launch_state_machine import _gap_to, _owned, _worker


def _past(conn):
    conn.execute("UPDATE ingestion_units SET next_attempt_at=?",
                 ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),))
    conn.commit()


def _race_first_write(monkeypatch):
    import core.ingestion.writer as writer
    real = writer._execute_plan
    calls = []

    def race_once(conn, plan):
        calls.append(1)
        if len(calls) == 1:
            writer._defer(conn, plan, "user_race")
            return "deferred"
        return real(conn, plan)
    monkeypatch.setattr(writer, "_execute_plan", race_once)


def test_an_error_after_an_abandoned_plan_defers_the_unit(cdf_env, monkeypatch):
    _gap_to(True, cdf_env)
    _race_first_write(monkeypatch)
    _drain(cdf_env)
    import core.ingestion.writer as writer
    monkeypatch.setattr(writer, "_build_plan", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
    from core.ingestion import api
    conn = api.connect_rw(); _past(conn); conn.close()
    rows = _drain(cdf_env)
    assert (rows[-1]["state"], rows[-1]["reason"]) == ("deferred", "error:OSError"), rows[-1]


def test_recovery_requeues_a_writing_unit_whose_only_plan_was_abandoned(cdf_env, monkeypatch):
    _gap_to(True, cdf_env)
    _race_first_write(monkeypatch)
    _drain(cdf_env)
    from core.ingestion import api
    from core.ingestion.writer import recover
    conn = api.connect_rw()
    old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    conn.execute("UPDATE ingestion_units SET state='writing',updated_at=?", (old,))
    conn.commit()
    recover(conn)
    assert conn.execute("SELECT state FROM ingestion_units").fetchone()[0] == "queued"
    conn.close()


def test_a_plan_whose_entry_is_written_is_finished_not_abandoned(cdf_env, monkeypatch):
    """Relations failing after the entry is in the file, then the user editing the file:
    recovery finishes the plan (relations and baseline) and never re-judges the unit,
    whose retry appended the entry a second time."""
    _gap_to(True, cdf_env)
    import core.ingestion.writer as writer
    real = writer._write_relations
    monkeypatch.setattr(writer, "_relations_postcondition", lambda *a, **k: False)
    monkeypatch.setattr(writer, "_write_relations",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("database is locked")))
    rows = _drain(cdf_env)
    assert rows[-1]["state"] == "writing", rows[-1]          # the live plan belongs to recovery
    from core.ingestion import api
    conn = api.connect_rw()
    assert conn.execute("SELECT step FROM ingestion_writes").fetchone()[0] == "target_applied"
    writer.recover(conn)                                     # still failing: kept, not abandoned
    assert conn.execute("SELECT step FROM ingestion_writes").fetchone()[0] == "target_applied"
    improvement = cdf_env["ddd"] / "IMPROVEMENT.md"
    improvement.write_text(improvement.read_text(encoding="utf-8") + "\nuser note\n", encoding="utf-8")
    monkeypatch.setattr(writer, "_write_relations", real)
    writer.recover(conn)
    assert conn.execute("SELECT step FROM ingestion_writes").fetchone()[0] == "done"
    assert conn.execute("SELECT state FROM ingestion_units").fetchone()[0] == "written"
    conn.close()
    assert improvement.read_text(encoding="utf-8").count("Retry rate remains high") == 1


def test_the_daily_recovery_leaves_a_unit_that_just_moved(cdf_env):
    _gap_to(True, cdf_env)
    owner, conn = _owned(cdf_env)
    try:
        _worker(cdf_env, owner)       # the unit row exists once a pass ran
        from core.ingestion.worker import REQUEUE_GRACE
        from core.ingestion.writer import recover
        _worker(cdf_env, owner).run_once(conn)
        conn.execute("DELETE FROM ingestion_writes")
        conn.execute("UPDATE ingestion_units SET state='writing',updated_at=?",
                     (datetime.now(timezone.utc).isoformat(),))
        conn.commit()
        recover(conn, requeue_grace=REQUEUE_GRACE)
        assert conn.execute("SELECT state FROM ingestion_units").fetchone()[0] == "writing"
        recover(conn)
        assert conn.execute("SELECT state FROM ingestion_units").fetchone()[0] == "queued"
    finally:
        conn.close(); owner.release()


def test_the_run_lane_starts_after_startup_recovery(cdf_env, monkeypatch):
    from core.ingestion import worker as w
    owner, conn = _owned(cdf_env)
    conn.close()
    try:
        worker = _worker(cdf_env, owner)
        order = []
        monkeypatch.setattr(worker, "_maintain", lambda c, startup=False: order.append(("maintain", startup)))
        worker.on_ready = lambda: order.append("run lane")

        def run_once(c):
            worker.stop()
            return False
        monkeypatch.setattr(worker, "run_once", run_once)
        asyncio.run(worker.run())
        assert order == [("maintain", True), "run lane"]
        handle = w.WorkerHandle.__new__(w.WorkerHandle)
        handle.run_executor = w.RunLaneExecutor(owner, cdf_env["workspace"])
        handle.run_executor.stop_and_join(1)          # never started: no error
    finally:
        owner.release()


def test_the_session_job_thread_survives_a_failing_recovery(cdf_env, monkeypatch):
    from core.ingestion import api, s2_jobs
    from core.ingestion.worker import S2JobExecutor
    monkeypatch.setattr(s2_jobs, "recover", lambda c: (_ for _ in ()).throw(RuntimeError("database is locked")))
    conn = api.connect_rw()
    S2JobExecutor._recover(conn)      # logged, not raised
    conn.close()


def test_a_unit_refused_for_quota_is_not_judged_again_while_the_budget_is_spent(cdf_env):
    from core.ingestion import api
    from tests.test_cdf_launch_cognition import _memory_unit
    conn = api.connect_rw()
    today = datetime.now(timezone.utc).date().isoformat()
    conn.execute("INSERT INTO ingestion_quota VALUES ('earlier','MEMORY',NULL,?,4096,'committed',?)",
                 (today, (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()))
    conn.commit(); conn.close()
    _memory_unit(cdf_env)
    assert _drain(cdf_env)[-1]["reason"] == "quota"
    conn = api.connect_rw(); _past(conn); conn.close()
    import core.ingestion.pipeline_v2 as pv2
    judged = []
    real = pv2.run_unit_v2
    import core.ingestion.worker  # noqa: F401
    pv2.run_unit_v2 = lambda *a, **k: judged.append(1) or real(*a, **k)
    try:
        rows = _drain(cdf_env)
    finally:
        pv2.run_unit_v2 = real
    assert judged == [] and (rows[-1]["state"], rows[-1]["reason"]) == ("deferred", "quota")


def test_refine_keeps_the_old_entrys_taint(tmp_path, monkeypatch):
    from tests.test_cdf_writer_core import (ClaimVerdict, HitJudgment, _connect, _decision, _unit,
                                            corpus as _corpus)
    from core.ingestion.units import parse_units
    from core.ingestion.writer import _unit_external, apply_unit
    corpus = _corpus.__wrapped__(tmp_path, monkeypatch)
    target = corpus["workspace"] / "Projects/P/2-understanding/TECH.md"
    target.parent.mkdir(parents=True)
    target.write_text("# T\n\n## Conventions\n\n- [guideline] **Old** Fetched advice. "
                      "<!-- cand:old.1 | taint: external -->\n", encoding="utf-8")
    anchor = "Projects/P/2-understanding/TECH.md#Conventions#cand:old.1"
    judgment = HitJudgment(anchor=anchor, overlap="related", same_topic=True,
                           claim_verdicts=[ClaimVerdict(verdict="refines", existing_sentence="Fetched advice.")],
                           scope_relation="equal", candidate_evidence="measured", existing_evidence="inferred")
    conn = _connect(corpus["db"])
    row = _unit(conn, "new.1", "Add the trigger.", title="Refined")
    decision = _decision("new.1", "DDD", "Projects/P/2-understanding/TECH.md", "Conventions",
                         action="refine", judgments=[judgment])
    assert apply_unit(decision, row, conn) == "done"
    conn.close()
    new = next(u for u in parse_units(target, root=corpus["workspace"]) if "new.1" in u.identity)
    assert "Fetched advice." in new.text and _unit_external(new)


def test_an_installed_package_file_cannot_leave_its_project(cdf_env, monkeypatch):
    from fastapi import HTTPException
    from tests.test_cdf_launch_explorer import api_ws
    api, ws = api_ws.__wrapped__(cdf_env, monkeypatch)
    pkg = ws / "Projects/Pkg"
    (pkg / ".swarmai").mkdir(parents=True)
    (pkg / ".swarmai/installed-package.json").write_text('{"name": "pkg", "version": "1"}')
    (pkg / "2-understanding").mkdir()
    (pkg / "2-understanding/TECH.md").write_text("# TECH\n")
    (ws / "Projects/Mine/2-understanding").mkdir(parents=True)
    other = ws / "Projects/Other"
    (other / ".swarmai").mkdir(parents=True)
    (other / ".swarmai/installed-package.json").write_text('{"name": "other", "version": "1"}')
    (other / "2-understanding").mkdir()
    for dest in ("Projects/Mine/2-understanding/TECH.md", "Projects/Other/2-understanding/TECH.md"):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(api.rename_item(api.FolderRenameRequest(
                old_path="Projects/Pkg/2-understanding/TECH.md", new_path=dest)))
        assert exc.value.status_code == 409 and (pkg / "2-understanding/TECH.md").exists()


def test_a_rename_refuses_a_change_made_after_the_copy(cdf_env, monkeypatch):
    from fastapi import HTTPException
    from tests.test_cdf_launch_explorer import api_ws
    import core.ingestion.api as ingestion_api
    api, ws = api_ws.__wrapped__(cdf_env, monkeypatch)
    note = ws / "Knowledge/Notes/a.md"
    note.write_text("first\n", encoding="utf-8")
    real = ingestion_api.apply_user_edit
    calls = []

    def edit(path, data, **kw):
        calls.append(path)
        out = real(path, data, **kw)
        if len(calls) == 1:
            note.write_text("changed after the copy\n", encoding="utf-8")
        return out
    monkeypatch.setattr(ingestion_api, "apply_user_edit", edit)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.rename_item(api.FolderRenameRequest(
            old_path="Knowledge/Notes/a.md", new_path="Knowledge/Notes/b.md")))
    assert exc.value.status_code == 409 and note.read_text() == "changed after the copy\n"


def test_the_list_decides_a_units_kind():
    from core.ingestion.sources.extract_llm import units_from_answer
    out = units_from_answer({"cognition_units": [{"kind": "reference", "title": "T"}]}, ["cognition"])
    assert out == [{"title": "T", "kind": "cognition"}]


def test_an_undecodable_reply_is_called_once_more(monkeypatch):
    from core.ingestion.sources import extract_llm
    from core.ingestion.prompts import UntypedOutput
    from jobs.bedrock import StructuredOutputError
    replies = [StructuredOutputError("invalid", "x", "$.cognition_units: expected array, got str"),
               {"cognition_units": []}]

    def structured(*a, **k):
        r = replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r
    monkeypatch.setattr(extract_llm, "structured", structured)
    assert extract_llm.BedrockExtractor(["cognition"]).extract("p").value == []
    replies[:] = [StructuredOutputError("invalid", "x", "bad"), StructuredOutputError("invalid", "x", "bad")]
    with pytest.raises(UntypedOutput):
        extract_llm.BedrockExtractor(["cognition"]).extract("p")


def test_one_undecodable_window_does_not_fail_the_session():
    from core.ingestion.prompts import UntypedOutput
    from core.ingestion.sources import s2_session

    def bad():
        raise UntypedOutput("bad")
    assert s2_session._windows_extracted([bad, lambda: ["u"]], "s") == ["u"]
    with pytest.raises(UntypedOutput):                       # every window: not one reply
        s2_session._windows_extracted([bad, bad], "s")
    with pytest.raises(RuntimeError):
        s2_session._windows_extracted([lambda: (_ for _ in ()).throw(RuntimeError("down")),
                                       lambda: ["u"]], "s")


# ── found in the rehearsal on a copy of real data ──────────────────────────────

def test_memory_and_evolution_entries_read_as_text_not_field_names():
    from core.ingestion.writer import render_unit
    group = {"title": "Guard paths", "what": "The guard reads paths inside content.",
             "applicability": "When a heredoc holds a path.", "source": "S2"}
    evo = render_unit({"destination": {"store": "EVOLUTION"},
                       "card1": {"entry_type": "correction", "agent_kind": "correction"}}, "u.1", group)
    mem = render_unit({"destination": {"store": "MEMORY"}, "card1": {"entry_type": "guideline"}},
                      "u.2", group)
    for out in (evo, mem):
        assert "what:" not in out and "applicability:" not in out
        assert "**Applies when:** When a heredoc holds a path." in out
    assert "- **Correction**: **Guard paths** The guard reads" in evo


def test_refine_of_a_timeline_event_does_not_copy_the_earlier_event(tmp_path, monkeypatch):
    from tests.test_cdf_writer_core import (ClaimVerdict, HitJudgment, _connect, _decision, _unit,
                                            corpus as _corpus)
    from core.ingestion.writer import apply_unit
    corpus = _corpus.__wrapped__(tmp_path, monkeypatch)
    timeline = corpus["workspace"] / ".context/TIMELINE.md"
    timeline.parent.mkdir(parents=True, exist_ok=True)
    timeline.write_text("# Timeline\n\n## Not injected\n\n### 2026-10-26 Earlier run\n"
                        "- what: The earlier run landed.\n<!-- cand:old.1 | event_key: | source:S2 -->\n",
                        encoding="utf-8")
    anchor = ".context/TIMELINE.md#Not injected#cand:old.1"
    judgment = HitJudgment(anchor=anchor, overlap="related", same_topic=True,
                           claim_verdicts=[ClaimVerdict(verdict="refines", existing_sentence="The earlier run landed.")],
                           scope_relation="equal", candidate_evidence="measured", existing_evidence="inferred")
    conn = _connect(corpus["db"])
    row = _unit(conn, "new.1", "The design was updated.", title="Design updated")
    decision = _decision("new.1", "TIMELINE", ".context/TIMELINE.md", "Not injected",
                         action="refine", judgments=[judgment])
    assert apply_unit(decision, row, conn) == "done"
    conn.close()
    text = timeline.read_text(encoding="utf-8")
    start = text.index("Design updated")
    new = text[start:text.index("cand:new.1", start)]
    assert "The design was updated" in new and "earlier run landed" not in new


def test_a_refined_block_entry_carries_its_text_without_heading_or_labels():
    from core.ingestion.writer import _plain_unit_body
    block = ("### C067 | 2026-10-29\n- **Correction**: **Guard** The guard reads content.\n"
             "<!-- cand:x.unit -->")
    assert _plain_unit_body(block) == "**Guard** The guard reads content."
    assert _plain_unit_body("- [pitfall] **T** Body. <!-- cand:y -->") == "Body."


def test_units_are_extracted_in_one_language():
    from core.ingestion.sources import extract_llm
    assert "Write every unit in English" in extract_llm.QUOTE_RULE
