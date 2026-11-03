"""What an extractor writes is shaped where it is extracted (design
2026-11-02-cdf-extraction-input; EVOLUTION K007): every knowledge unit says when it
applies, every gap names its next step, and the structured-output schema every model
extractor answers in requires both, so an entry without them is never produced. The
session review also sees the exchange just before its window, as context it may not cite."""
from __future__ import annotations

import asyncio
import json

import pytest

from core.ingestion import contracts_v2
from core.ingestion.sources import extract_llm


def _cognition(**extra):
    return {"kind": "cognition", "type": "guideline", "scope": "project:P", "title": "T",
            "what": "Do X.", "evidence": ["event:e1"], **extra}


def _gap(**extra):
    return {"kind": "gap", "gap_kind": "debt", "scope": "project:P", "title": "T",
            "what": "X is open.", "evidence": ["event:e1"], **extra}


def test_a_knowledge_unit_says_when_it_applies():
    with pytest.raises(ValueError):
        contracts_v2.SOURCE_UNIT.validate_python(_cognition())
    with pytest.raises(ValueError):
        contracts_v2.SOURCE_UNIT.validate_python(_cognition(applicability=""))
    assert contracts_v2.SOURCE_UNIT.validate_python(_cognition(applicability="When changing X.")).applicability


def test_a_gap_names_its_next_step():
    with pytest.raises(ValueError):
        contracts_v2.SOURCE_UNIT.validate_python(_gap())
    assert contracts_v2.SOURCE_UNIT.validate_python(_gap(proposed_next="Measure X first.")).proposed_next


def test_every_model_extractor_is_asked_for_both():
    schema = extract_llm.unit_schema(("cognition", "gap", "event"))
    item = lambda kind: schema["properties"][f"{kind}_units"]["items"]
    assert "applicability" in item("cognition")["required"]
    assert "proposed_next" in item("gap")["required"]


def test_the_pipeline_report_template_carries_them():
    from core.run_protocol.templates import template
    report = template("reflect")["document"]
    assert report["decisions"] and all("applicability" in row for row in report["decisions"])
    from core.run_protocol import reflect
    from core.run_protocol.templates import fill
    filled = fill(report, {"<ANCHOR>": "event:a1", "<PROJECT>": "P"})
    assert reflect.prepare_lenient(filled, allowed_anchors={"event:a1"})["dropped"] == []


def test_a_run_report_row_the_contract_no_longer_accepts_costs_only_itself(monkeypatch):
    """A report prepared before the contract changed may hold a row without the new
    fields: that row is recorded invalid, the others are still enqueued."""
    from core.ingestion import api_v2
    from core.ingestion.sources import s3_run
    good = _cognition(applicability="When changing X.")
    old = _cognition()                                   # prepared under the old contract
    enqueued, attempts = [], []
    monkeypatch.setattr(s3_run, "_roots", lambda engine: ((), ()))
    monkeypatch.setattr(s3_run, "_snapshot", lambda engine: {})
    monkeypatch.setattr(api_v2, "enqueue_stored", lambda *a, **k: enqueued.append(a[2]) or "ok")
    monkeypatch.setattr(api_v2, "record_extraction_attempt", lambda attempt, **k: attempts.append(k))

    class Engine:
        run_id = "run_x"
        def read_reflect_report(self):
            return {"delivered": [], "decisions": [], "corrections": [], "practices": [good, old], "gaps": []}
    assert s3_run.enqueue_report(Engine()) == ["ok"]
    assert [u.title for u in enqueued] == ["T"] and len(attempts[0]["invalid_units"]) == 1


def test_a_report_prepared_before_the_contract_changed_does_not_fail_delivery():
    """RF-1..4 re-read the prepared report: a row the contract has since come to refuse
    does not fail the run's delivery (REFLECT never blocks landing)."""
    from types import SimpleNamespace
    from core.run_protocol import checks
    old = _cognition()                                   # no applicability: prepared before
    report = {"delivered": [], "decisions": [], "corrections": [], "practices": [old], "gaps": []}
    assert checks._evaluate_check("RF-1", SimpleNamespace(run={"reflect_report": report}))[0] is True
    broken = {k: v for k, v in report.items() if k != "gaps"}      # not the five-part form
    assert checks._evaluate_check("RF-1", SimpleNamespace(run={"reflect_report": broken}))[0] is False


def test_when_each_destination_is_written_is_one_standard_for_every_door():
    """The write condition of each destination lives once, in the knowledge standard: the
    session review renders it, a new Brain's entry contract carries it."""
    from core import knowledge_standard as ks
    from core.brain_bootstrap import apply
    from core.ingestion.sources import s7_review
    assert len(ks.WRITE_WHEN) == 4 and set(ks.WRITE_WHEN) <= set(ks.sentences())
    prompt = " ".join(s7_review.build_prompt(s7_review.ALLOWED_KINDS, {}, "", "").split())
    contract = apply.entry_contract("P")
    for sentence in ks.WRITE_WHEN:
        assert " ".join(sentence.split()) in prompt, sentence
        assert sentence in contract["knowledge_standard"], sentence
    import inspect
    assert "exactly one problem" not in inspect.getsource(s7_review)    # no second copy


# ── turn two reuses turn one (design 2026-11-02-cdf-extraction-input §9) ─────────────

def test_both_review_turns_answer_in_one_schema():
    """Structured output is a tool whose definition is the schema, and the tool block opens
    the cached prefix: one schema for both turns lets turn two read turn one's cache."""
    from core.ingestion.sources import s7_review
    schema = s7_review.review_schema()
    assert "relations" in schema["properties"] and "relations" not in schema["required"]
    assert {f"{k}_units" for k in s7_review.ALLOWED_KINDS} <= set(schema["required"])


def test_the_relations_prompt_lists_each_entry_once_and_whole():
    from core.ingestion.relations_v2 import Entry
    from core.ingestion.sources import s7_review
    head = "- [pitfall] **Retry with backoff** when the API returns 429."
    shared = Entry("abcd1234", "TECH.md#Runtime Traps#cand:cand-a.unit", "TECH.md", "Runtime Traps",
                   f"{head} <!-- cand:cand-a.unit -->\n  **Why:** quota resets per minute.")
    other = Entry("ef015678", "TECH.md#Conventions#cand:cand-b.unit", "TECH.md", "Conventions",
                  "- [guideline] **Log the request id** on every call. <!-- cand:cand-b.unit -->")
    from types import SimpleNamespace
    unit = lambda t: SimpleNamespace(kind="cognition", type="pitfall", title=t, what=t)
    prompt = s7_review.relation_prompt([unit("U1"), unit("U2")], [[shared, other], [shared]], set())
    assert prompt.count("quota resets per minute") == 1           # the entry once, though two units name it
    assert prompt.count("Retry with backoff") == 1                  # its first line once, not twice
    assert f"abcd1234 | {head}" in prompt                            # what a declaration copies
    assert prompt.count("abcd1234 | ") == 1 and prompt.count("abcd1234") >= 3   # listed once, named per unit
    assert "Log the request id" in prompt


# ── the record carries the conversation, not the store's bookkeeping (§10) ───────────

def _rows():
    meta = {"session_id": "s" * 36, "created_at": "2026-11-03T01:00:00", "updated_at": "x",
            "model": "m", "metadata": {"client_id": "c-1"}, "expires_at": None, "sent": 1,
            "pending_seq": None, "claimed_at": None}
    tool = lambda i, ok=True: [{"type": "tool_use", "id": f"t{i}", "name": "Bash", "summary": "Running: x"},
                               {"type": "tool_result", "tool_use_id": f"t{i}", "is_error": not ok, "outcome": "1"}]
    return [
        {"id": "u1", "role": "user", "content": "check it", **meta},
        {"id": "a1", "role": "assistant", "content": tool(1), **meta},
        {"id": "a2", "role": "assistant", "content": tool(2, ok=False), **meta},
        {"id": "a3", "role": "assistant", "content": [{"type": "pipeline_stage", "stage": "review", "detail": "x" * 500},
                                                      {"type": "turn_deliverables", "items": ["y" * 300]},
                                                      {"type": "text", "text": "It is done."},
                                                      {"type": "turn_outcome", "ended": "completed", "usage": {}}], **meta},
        {"id": "a4", "role": "assistant", "content": [{"type": "turn_outcome", "ended": "stopped", "subtype": "user"}], **meta},
    ]


def test_a_row_carries_only_its_id_role_and_what_it_shows():
    from core.ingestion.sources import s7_review
    view = s7_review.knowledge_view(_rows())
    assert all(set(r) == {"id", "role", "content"} for r in view), view
    shown = json.dumps(view)
    assert "pipeline_stage" not in shown and "turn_deliverables" not in shown       # display cards are not read
    assert '"completed"' not in shown and '"stopped"' in shown                    # an outcome only when it is news


def test_consecutive_tool_only_messages_are_one_line():
    from core.ingestion.sources import s7_review
    view = s7_review.knowledge_view(_rows())
    assert [r["id"] for r in view] == ["u1", "a1", "a3", "a4"]
    assert view[1]["content"] == [{"type": "tools_used", "tools": "Bash x2", "failed": 1}]


def test_the_record_cites_an_item_by_its_id_not_a_list_of_anchors():
    from core.ingestion.sources import s7_review
    window = [{"id": "a1", "role": "assistant", "content": [{"type": "text", "text": "done"}]}]
    text, anchors, aliases = s7_review._record(window, [("e1", "e2")], {})
    assert "anchors" not in text and '"a1"' in text
    assert aliases["a1"] == ["event:e1", "event:e2"] and set(anchors) == {"event:e1", "event:e2"}
