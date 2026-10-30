"""Correction ledger (design docs/design/2026-10-30-autonomy-tenets-gaps.md §2, I5/I6).

The person, never a model, says which class a correction belongs to, and the
repeat-correction rate is a count over those choices.
"""
from datetime import datetime, timedelta, timezone

import pytest

from core.ingestion import correction_ledger as cl
from core.ingestion import store

NOW = datetime(2026, 10, 30, 6, 0, tzinfo=timezone.utc)


@pytest.fixture
def ws(tmp_path, monkeypatch):
    db = tmp_path / "data" / "ingestion.db"
    monkeypatch.setattr(store, "ingestion_db_path", lambda: db)
    w = tmp_path / "ws"
    (w / "Projects" / "Billing" / "2-understanding").mkdir(parents=True)
    (w / ".context").mkdir(parents=True)
    (w / "Projects" / "Billing" / "2-understanding" / "IMPROVEMENT.md").write_text(
        "# IMPROVEMENT\n\n## What Failed\n\n"
        "- [pitfall] **Refunds counted by order date** refund totals must use the settlement date, "
        "not the order date, or the monthly refund report is wrong.\n\n"
        "- [decision] **Invoices are monthly** invoices close on the last day of the month.\n",
        encoding="utf-8")
    (w / ".context" / "EVOLUTION.md").write_text(
        "# EVOLUTION\n\n## Corrections Captured\n\n"
        "### C001 | 2026-10-01\n- **Correction**: Agent reported a fix as deployed without "
        "checking the running binary.\n\n"
        "## Competence Learned\n\n### K001 | 2026-10-02\n- **Competence**: deployed binary checks.\n",
        encoding="utf-8")
    (w / ".context" / "MEMORY.md").write_text(
        "# MEMORY\n\n## Corrections\n\n"
        "- [correction] **Timezone is ICT** report times use the user's timezone, never UTC.\n",
        encoding="utf-8")
    monkeypatch.setattr("core.project_registry.get_swarmws", lambda: w)
    return w


def _open(text, client, **kw):
    return cl.open_row(surface="chat", text=text, session_id="s1", client_id=client,
                       now=kw.pop("now", NOW), **kw)


def test_detect_takes_a_stated_mistake_not_a_redirect():
    assert cl.detect("不对，退款应该按到账日算")
    assert cl.detect("that's wrong, use the settlement date")
    assert not cl.detect("去查一下 TECH.md 再说")       # steering, not a mistake
    assert not cl.detect("")
    assert not cl.detect(None)  # type: ignore[arg-type]


def test_candidates_are_the_entries_a_correction_can_point_at(ws):
    found = cl.candidates("不对 refund report uses the order date, should be settlement date",
                          brain="Billing", workspace=ws, now=NOW)
    titles = [c["title"] for c in found]
    assert "Refunds counted by order date" in titles
    assert all(c["kind"] == "entry" for c in found)
    # a decision is not something a correction repeats; other EVOLUTION sections are not either
    everything = cl.candidates("invoices monthly last day competence deployed binary checks",
                               brain="Billing", workspace=ws, now=NOW)
    assert "Invoices are monthly" not in [c["title"] for c in everything]
    assert not any("K001" in c["key"] for c in everything)
    # the agent's own corrections and the world corrections are in the pool
    keys = " ".join(c["key"] for c in cl.candidates(
        "deployed running binary timezone UTC", brain=None, workspace=ws, now=NOW))
    assert "EVOLUTION.md" in keys and "MEMORY.md" in keys


def test_a_resent_message_does_not_add_a_second_row(ws):
    first = _open("不对，按到账日", "c1", brain="Billing", workspace=ws)
    assert _open("不对，按到账日", "c1", brain="Billing", workspace=ws) == first
    assert len(cl.pending("s1")) == 1


def test_the_class_can_only_be_one_the_person_was_shown(ws):
    row = _open("不对 refund report order date settlement", "c1", brain="Billing", workspace=ws)
    offered = cl.pending("s1")[0]["candidates"]
    assert offered
    with pytest.raises(cl.LedgerError):
        cl.confirm(row, "Projects/Other/2-understanding/TECH.md#X#made-up")
    got = cl.confirm(row, offered[0]["key"], now=NOW)
    assert got["status"] == "confirmed" and got["class_kind"] == "entry" and got["repeat"] is True
    assert cl.pending("s1") == []
    # a choice is never overwritten (not by a second click, not by anyone else)
    with pytest.raises(cl.AlreadyAnswered):
        cl.confirm(row, "not_correction", now=NOW)
    assert cl.rate(28, now=NOW)["repeat"] == 1


def test_the_row_keeps_what_the_person_typed_not_injected_blocks(ws, monkeypatch):
    monkeypatch.setattr(cl, "session_brain", lambda sid, text: None)
    monkeypatch.setattr("core.injected_blocks.strip_injected_blocks",
                        lambda text: text.replace("[RECALLED] old notes say that's wrong", ""))
    cl.record_chat_message("s1", "c9", "[RECALLED] old notes say that's wrong 不对，按到账日")
    assert cl.pending("s1")[0]["text"] == "不对，按到账日"


def test_pending_returns_the_newest_when_many_wait(ws, monkeypatch):
    monkeypatch.setattr(cl, "PENDING_LIMIT", 2)
    for i in range(3):
        _open(f"错了 {i}", f"c{i}", now=NOW + timedelta(minutes=i))
    assert [r["client_id"] for r in cl.pending("s1", now=NOW + timedelta(minutes=5))] == ["c1", "c2"]


def test_rate_counts_repeats_over_confirmed_rows_only(ws):
    a = _open("不对 refund order date settlement", "c1", brain="Billing", workspace=ws)
    cl.confirm(a, cl.pending("s1")[0]["candidates"][0]["key"], now=NOW)      # repeat (entry)
    b = _open("不对 the colour should be blue", "c2", brain="Billing", workspace=ws)
    cl.confirm(b, "new", now=NOW)                                            # a new class
    c = _open("不对 something else", "c3", brain="Billing", workspace=ws)
    cl.confirm(c, "not_correction", now=NOW)
    _open("错了 still waiting", "c4", brain="Billing", workspace=ws)           # unconfirmed
    r = cl.rate(28, now=NOW)
    assert (r["total"], r["confirmed"], r["unconfirmed"], r["not_correction"]) == (4, 2, 1, 1)
    assert r["repeat"] == 1 and r["rate"] == 0.5
    assert r["by_kind"]["entry"] == 1 and r["by_kind"]["new"] == 1
    assert r["small_sample"] is True
    assert cl.rate(28, brain="Other", now=NOW)["total"] == 0
    # outside the window
    assert cl.rate(28, now=NOW + timedelta(days=29))["total"] == 0


def test_an_earlier_unsedimented_correction_is_a_class_a_later_one_repeats(ws):
    first = _open("不对 the export must keep the currency symbol", "c1", workspace=ws)
    cl.confirm(first, "new", now=NOW)
    later = _open("错了 export dropped the currency symbol again", "c2", workspace=ws,
                  now=NOW + timedelta(days=3))
    offered = {c["key"]: c["kind"] for c in cl.pending("s1")[0]["candidates"]}
    assert offered.get(first) == "ledger"
    got = cl.confirm(later, first, now=NOW + timedelta(days=3))
    assert got["class_kind"] == "ledger" and got["repeat"] is True
    assert cl.rate(28, now=NOW + timedelta(days=3))["by_kind"]["ledger"] == 1


def test_chat_entry_needs_a_session_a_client_id_and_a_stated_mistake(ws, monkeypatch):
    monkeypatch.setattr(cl, "session_brain", lambda sid, text: "Billing")
    assert cl.record_chat_message(None, "c1", "不对") is None
    assert cl.record_chat_message("s1", None, "不对") is None
    assert cl.record_chat_message("s1", "c1", "looks good, thanks") is None
    rid = cl.record_chat_message("s1", "c1", "不对，退款按到账日", reply="refund by order date")
    row = cl.pending("s1")[0]
    assert row["id"] == rid and row["brain"] == "Billing" and row["surface"] == "chat"


def test_readers_never_create_the_store(tmp_path, monkeypatch):
    db = tmp_path / "none" / "ingestion.db"
    monkeypatch.setattr(store, "ingestion_db_path", lambda: db)
    assert cl.pending("s1") == []
    r = cl.rate(28, now=NOW)
    assert r["total"] == 0 and r["rate"] is None
    assert not db.exists()


def test_last_reply_is_the_newest_assistant_text():
    msgs = [{"role": "assistant", "content": [{"type": "text", "text": "old"}]},
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": [{"type": "tool_use", "name": "Read"},
                                              {"type": "text", "text": "new answer"}]}]
    assert cl.last_reply(msgs) == "new answer"
    assert cl.last_reply([]) == ""


def test_every_row_has_its_sources_key(ws):
    with pytest.raises(cl.LedgerError):
        cl.open_row(surface="escalation", text="x", client_id="")


def test_a_sent_back_hive_delivery_is_a_correction_of_that_run(ws, monkeypatch):
    rows = {"so_1": {"dispatch_id": "so_1", "session_id": "hs1", "title": "fix the refund report",
                     "project_agent": {"brain": "Billing"}}}
    monkeypatch.setattr("core.swarm_out._log_row_of", lambda d: rows[d])
    rid = cl.record_swarm_out_reject("so_1")
    assert cl.record_swarm_out_reject("so_1") == rid          # one row per dispatch
    row = cl.pending(brain="Billing")[0]
    assert (row["surface"], row["run_id"], row["session_id"]) == ("swarm_out_reject", "so_1", "hs1")


def test_an_escalation_is_a_correction_only_against_swarms_pick(ws):
    from core.escalation import Escalation, Option

    def esc(resolution, by="user", rec="Use the settlement date"):
        return Escalation(id=f"esc-{resolution}-{by}", level=1, trigger="t", title="Refund date",
                          situation="s", options=[Option("Use the settlement date", "d", is_recommendation=True),
                                                  Option("Use the order date", "d")],
                          recommendation=rec, project="Billing", status="resolved",
                          resolved_by=by, resolution=resolution)
    assert cl.record_escalation(esc("use the settlement  date")) is None     # took the pick
    assert cl.record_escalation(esc("Use the order date", by="timeout")) is None
    assert cl.record_escalation(esc("Use the order date", rec=None)) is None  # no pick: an open question
    rid = cl.record_escalation(esc("Use the order date"))
    assert cl.record_escalation(esc("Use the order date")) == rid   # no session: still one row
    row = [r for r in cl.pending(brain="Billing") if r["id"] == rid][0]
    assert row["surface"] == "escalation" and "not the recommended" in row["text"]


def test_pending_keeps_to_the_window(ws):
    _open("错了 old one", "c-old", now=NOW - timedelta(days=40))
    _open("错了 new one", "c-new", now=NOW)
    assert [r["client_id"] for r in cl.pending(now=NOW)] == ["c-new"]
