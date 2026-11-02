"""TIMELINE entrances and the month summary.

What is tested here, against the real CDF chain where it matters:
- the commit scan (S4) writes no events, and an event titled like a commit subject is
  refused at entry, while a plain title with a colon is written;
- the event standard every extraction prompt carries says one event per deliverable and
  no status;
- a run's ``pipeline-v1 events`` lists the closest recorded TIMELINE events as refs an
  event can declare ``same`` with;
- the month summary is stored in a real store (the inbox's lane constraint once refused
  every summary, silently), written with content from the month's events, kept within the
  injection budget, and a refused or unwritten summary is a counted failure;
- health and comments describe TIMELINE as read on demand.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_cdf_v2_e2e import (  # noqa: F401  (cdf_env is a fixture)
    SESSION, _drain, _enqueue_s4, _record_person_message, cdf_env,
)

NOW = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)
CONTENT = ("- Decided to keep one writer per store because two writers overwrote each other.\n"
           "- The October release shipped with the new review flow.")


def _event(title: str, what: str, evidence: list[str], *, when: str = "2026-10-20") -> dict:
    return {"kind": "event", "when": when, "title": title, "what": what,
            "outcome": "The change is in use.", "evidence": evidence}


def _timeline(env) -> str:
    return (env["workspace"] / ".context" / "TIMELINE.md").read_text(encoding="utf-8")


def _external_root() -> str:
    from core.ingestion import api_v2
    return api_v2.register_root("file_revision", "repo:bound@abc", "a commit record", "external")


def _october_event(env, title: str, what: str) -> None:
    """One October event written through the real session review (user-based, so it is
    placed in the month section the summary reads)."""
    from core.ingestion.sources import s7_review
    message = f"msg-{abs(hash(title))}"
    event = _record_person_message(env, message, what)[0]
    unit = _event(title, what, [f"event:{event}"])
    out = s7_review.review_session(SESSION, sdk_session_id=None, watermark=None,
                                   runner=lambda *a, **k: ({"event_units": [unit]}, None))
    assert out["units"] == 1, out
    rows = _drain(env)
    assert any(r["state"] == "written" for r in rows), [(r["state"], r["reason"]) for r in rows]


# ── R1: the commit scan writes no events ─────────────────────────────────────

def test_s4_answer_with_event_enqueues_no_event(monkeypatch):
    from core.ingestion import api_v2
    from core.ingestion.sources import s4_code

    class BothKinds:
        def extract(self, prompt):
            return SimpleNamespace(value=[
                {"kind": "cognition", "type": "model", "scope": "project:P",
                 "applicability": "When this comes up.", "title": "One writer per store",
                 "what": "Sources only enqueue typed units.", "evidence": ["commit:manual"]},
                {"kind": "event", "when": "2026-10-27", "scope": "project:P",
                 "title": "Source boundary merged", "what": "The boundary changed.",
                 "impact": "One path.", "evidence": ["commit:manual"]}], raw="raw")

    calls = []
    monkeypatch.setattr(api_v2, "register_root", lambda *a: "root:r")
    monkeypatch.setattr(api_v2, "enqueue_unit",
                        lambda *a, **k: calls.append(a) or SimpleNamespace(status="queued"))
    monkeypatch.setattr(api_v2, "record_extraction_attempt", lambda *a, **k: "a")
    s4_code.extract_commits([{"sha": "manual", "repository": "demo", "record": "patch"}],
                            run_landed_commits=set(), project_hint="P", llm=BothKinds())
    kinds = [call[2].kind for call in calls]
    assert kinds == ["cognition"]


# ── R2: a commit-shaped event title is refused at entry ──────────────────────

def test_commit_shaped_event_title_is_rejected(cdf_env):
    root = _external_root()
    before = _timeline(cdf_env)
    _enqueue_s4(_event("fix(brain): review round 1 on repository placement",
                       "The placement checks before acting.", [f"doc:{root}"]),
                source_ref="commit-shaped", project="P")
    rows = _drain(cdf_env)
    assert [(r["state"], r["reason"]) for r in rows] == [("rejected", "commit_title")]
    assert _timeline(cdf_env) == before


def test_plain_title_with_colon_passes_commit_check(cdf_env):
    from core.ingestion.contracts_v2 import commit_shaped as _commit_shaped

    for title in ("fix(brain): x", "test: y", "feat!: z", "test(conftest): the stub",
                  "release: v2.4.2", "brain(placement): undo what fails"):
        assert _commit_shaped(title), title
    for title in ("v2: memory system shipped", "e2e: the loop passed",
                  "Release notes: what v2.4 changed"):
        assert not _commit_shaped(title), title
    title = "Swarm Run design updated to r7.11: dispatcher answers for its runs"
    assert not _commit_shaped(title)
    root = _external_root()
    _enqueue_s4(_event(title, "Whoever dispatches a run answers for it.", [f"doc:{root}"]),
                source_ref="plain-colon", project="P")
    rows = _drain(cdf_env)
    assert [r["state"] for r in rows] == ["written"], [(r["state"], r["reason"]) for r in rows]
    assert title in _timeline(cdf_env)


# ── R3: one event standard, and a run sees the recorded events ───────────────

def test_event_rule_states_one_event_per_deliverable_and_no_status():
    from core.ingestion import contracts_v2
    from core.ingestion.sources import extract_llm

    rule = contracts_v2.EVENT_RULE
    assert "The steps of one deliverable are one event" in rule
    assert "on local main" in rule and "is a status" in rule
    # both extraction prompts read this one rule (extract_llm's prompt, the S7 review's)
    assert extract_llm.EVENT_RULE is rule
    assert rule in extract_llm.build_prompt("S2", "source text", ("event:e1",), ("event",))
    card = (Path(__file__).resolve().parents[1] / "skills" / "s_autonomous-pipeline"
            / "stages" / "deliver.md").read_text(encoding="utf-8")
    # the card is at its size limit: the rule travels in `pipeline-v1 events` (event_rule)
    assert "event_rule" in card and "timeline_events" in card and '"same"' in card


def _write_timeline(ws: Path, events: list[tuple[str, str]]) -> None:
    blocks = [f"### 2026-10-2{n} {title}\n- what: {what}\n<!-- cand:cand-{n:024d}.unit | source:S7 | event_kind:event -->"
              for n, (title, what) in enumerate(events)]
    (ws / ".context" / "TIMELINE.md").write_text(
        "# Timeline\n\n## 2026-10\n\n" + "\n\n".join(blocks) + "\n", encoding="utf-8")


def test_run_events_lists_closest_timeline_events_resolvable(tmp_path, monkeypatch):
    from tests.pipeline_v1_rig import Rig
    from core.ingestion import relations_v2

    rig = Rig(tmp_path, monkeypatch, profile="direct")
    (rig.ws / ".context").mkdir(parents=True, exist_ok=True)
    _write_timeline(rig.ws, [
        ("Month summary lane fixed so October summary is stored",
         "The summary lane stores the month summary with its content."),
        ("Settings tabs merged", "The settings screen has two tabs."),
    ])
    rig.create(requirement="Fix the month summary lane so the October summary is stored")
    out = rig.v1("events")
    from core.ingestion.contracts_v2 import EVENT_RULE
    assert out["event_rule"] == EVENT_RULE.strip()
    refs = out["timeline_events"]
    assert refs, out
    entry, why = relations_v2.resolve(refs[0], relations_v2.timeline_entries(rig.ws))
    assert entry is not None, why
    assert "Month summary lane fixed" in entry.text
    assert relations_v2.closest_events("", rig.ws) == []


# ── R4 / R5: the month summary is stored, has content, fails visibly ─────────

def test_month_summary_is_stored_in_a_real_store(cdf_env, monkeypatch):
    from core.ingestion import summary_lane
    from core.ingestion.store import connect_rw
    from core.ingestion.worker import LANE_WEIGHTS

    _october_event(cdf_env, "One writer per store decided", "We keep one writer per store.")
    monkeypatch.setattr(summary_lane, "model_writer", lambda month, kind, events: CONTENT)
    conn = connect_rw()
    try:
        assert summary_lane.SummaryLane(cdf_env["workspace"]).run_due(conn, now=NOW) is True
        rows = conn.execute("SELECT lane FROM ingestion_inbox WHERE source_ref='summary:2026-10'").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1 and rows[0][0] in LANE_WEIGHTS


def test_month_summary_carries_written_content_and_all_anchors(cdf_env, monkeypatch):
    from core.context_directory_loader import ContextDirectoryLoader
    from core.ingestion import summary_lane
    from core.ingestion.store import connect_rw
    from core.memory_injection import TIMELINE_BUDGET, select_timeline

    _october_event(cdf_env, "One writer per store decided", "We keep one writer per store.")
    _october_event(cdf_env, "October release shipped", "The release shipped with the review flow.")
    seen = {}
    long_content = CONTENT + "\n" + "\n".join(
        f"- Item {n}: a decision with its reason, stated in one plain line." for n in range(8))

    def writer(month, kind, events):
        seen.update(month=month, kind=kind, events=events)
        return long_content

    monkeypatch.setattr(summary_lane, "model_writer", writer)
    conn = connect_rw()
    try:
        summary_lane.SummaryLane(cdf_env["workspace"]).run_due(conn, now=NOW)
        payload = json.loads(conn.execute(
            "SELECT payload FROM ingestion_inbox WHERE source_ref='summary:2026-10'").fetchone()[0])
    finally:
        conn.close()
    assert seen["month"] == "2026-10" and seen["kind"] == "month_summary"
    assert len(seen["events"]) == 2 and any("One writer per store" in e for e in seen["events"])
    assert len(payload["evidence"]) == 2 and all(e.startswith("unit:") for e in payload["evidence"])
    assert payload["what"] == long_content and "traceable event anchors" not in payload["what"]

    rows = _drain(cdf_env)
    assert any(r["state"] == "written" for r in rows), [(r["state"], r["reason"]) for r in rows]
    shown = select_timeline(cdf_env["workspace"] / ".context" / "TIMELINE.md",
                            today=NOW.date(), estimate=ContextDirectoryLoader.estimate_tokens,
                            budget=TIMELINE_BUDGET)
    assert "Decided to keep one writer per store" in shown.text
    assert shown.omitted == 0


def test_run_report_refuses_a_commit_titled_event_where_its_author_sees_it():
    """A run's delivered event titled as a commit is dropped at reflect-prepare, with the
    reason, so the author retitles it; refused later by CDF, it would take the gaps it
    closes with it."""
    from core.run_protocol import reflect

    row = {"kind": "event", "when": "2026-11-02", "scope": "project:P",
           "title": "feat(cdf): month summaries carry content", "what": "x",
           "outcome": "y", "evidence": ["run_event:r:e1"], "closes": ["cand-0123abcd"]}
    out = reflect.prepare_lenient({"delivered": [row]}, allowed_anchors={"run_event:r:e1"},
                                  open_gap_ids={"cand-0123abcd"})
    assert out["sections"]["delivered"] == []
    assert "commit subject" in out["dropped"][0]["reason"]
    row["title"] = "Month summaries now carry the month's key events"
    out = reflect.prepare_lenient({"delivered": [row]}, allowed_anchors={"run_event:r:e1"},
                                  open_gap_ids={"cand-0123abcd"})
    assert len(out["sections"]["delivered"]) == 1 and out["dropped"] == []


def test_summary_and_supplements_are_not_written_again(tmp_path, monkeypatch):
    """Events a summary or a supplement already rests on are not sent to the model again."""
    from core.ingestion import api_v2, summary_lane

    (tmp_path / ".context").mkdir()
    _write_timeline(tmp_path, [("First decision", "a"), ("Late decision", "b")])
    anchors = summary_lane._month_anchors(tmp_path / ".context" / "TIMELINE.md", "2026-10")
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE ingestion_inbox(source_ref TEXT, payload TEXT, created_at TEXT)")
    conn.execute("INSERT INTO ingestion_inbox VALUES (?,?,?)", (
        "summary:2026-10", json.dumps({"evidence": [f"unit:{anchors[0]}"]}), "t1"))
    conn.execute("INSERT INTO ingestion_inbox VALUES (?,?,?)", (
        "summary:2026-10:supplement:abc", json.dumps({"evidence": [f"unit:{anchors[1]}"]}), "t2"))
    calls = []
    monkeypatch.setattr(summary_lane, "model_writer", lambda *a: calls.append(a) or CONTENT)
    monkeypatch.setattr(api_v2, "enqueue_stored", lambda *a, **k: calls.append(a))
    assert summary_lane.enqueue_month_summary(conn, tmp_path, "2026-10") == "unchanged"
    assert calls == []


def test_summary_size_is_held_to_what_the_prompt_shows_whole(tmp_path, monkeypatch):
    """The limit is the injection's own measure: the largest accepted summary is shown
    whole by select_timeline, and one past it is refused."""
    from core.context_directory_loader import ContextDirectoryLoader
    from core.ingestion import summary_lane
    from core.memory_injection import TIMELINE_BUDGET, select_timeline

    (tmp_path / ".context").mkdir()
    _write_timeline(tmp_path, [("One writer per store decided", "We keep one writer.")])
    line = "- Decided to keep one writer per store because two writers overwrote each other."
    lines: list[str] = []
    while summary_lane._shown_tokens("2026-10-01", "2026-10 key events", "\n".join(lines + [line]),
                                     "Drawn from 1 events of the month, each cited in its evidence."
                                     ) <= summary_lane.SUMMARY_MAX_TOKENS:
        lines.append(line)
    largest, over = "\n".join(lines), "\n".join(lines + [line])
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE ingestion_inbox(source_ref TEXT, payload TEXT, created_at TEXT)")
    monkeypatch.setattr(summary_lane, "model_writer", lambda *a: over)
    with pytest.raises(summary_lane.SummaryNotWritten, match="tokens as shown"):
        summary_lane.enqueue_month_summary(conn, tmp_path, "2026-10")
    block = (f"### 2026-10-01 2026-10 key events\n- what: {largest}\n"
             "- impact: Drawn from 1 events of the month, each cited in its evidence.\n"
             "<!-- cand:cand-x.unit | event_kind:month_summary -->")
    path = tmp_path / ".context" / "TIMELINE.md"
    path.write_text(path.read_text(encoding="utf-8") + "\n" + block + "\n", encoding="utf-8")
    shown = select_timeline(path, today=NOW.date(), estimate=ContextDirectoryLoader.estimate_tokens,
                            budget=TIMELINE_BUDGET)
    assert largest in shown.text and shown.omitted == 0


def test_rejected_summary_enqueue_is_a_failure(tmp_path, monkeypatch):
    from core.ingestion import api_v2, summary_lane

    (tmp_path / ".context").mkdir()
    _write_timeline(tmp_path, [("One writer per store decided", "We keep one writer.")])
    monkeypatch.setattr(summary_lane, "model_writer", lambda *a: CONTENT)
    monkeypatch.setattr(api_v2, "enqueue_stored", lambda *a, **k: SimpleNamespace(
        status="rejected", candidate_id=None, detail="CHECK constraint failed: lane"))
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE ingestion_inbox(source_ref TEXT, payload TEXT, created_at TEXT)")
    conn.execute("CREATE TABLE ingestion_events(at TEXT, kind TEXT, detail TEXT)")
    lane = summary_lane.SummaryLane(tmp_path)
    for _ in range(3):
        with pytest.raises(summary_lane.SummaryNotWritten, match="store refused"):
            lane.run_due(conn, now=NOW)
    assert lane.consecutive_failures == 3
    kind, detail = conn.execute("SELECT kind, detail FROM ingestion_events").fetchone()
    assert kind == "summary_failed" and "CHECK constraint" in detail


@pytest.mark.parametrize("writer", [
    lambda *a: (_ for _ in ()).throw(RuntimeError("model down")),
    lambda *a: "   ",
    lambda *a: "- a decision with its reason, stated in one plain line\n" * 400,
])
def test_summary_writer_failure_enqueues_nothing(tmp_path, monkeypatch, writer):
    from core.ingestion import api_v2, summary_lane

    (tmp_path / ".context").mkdir()
    _write_timeline(tmp_path, [("One writer per store decided", "We keep one writer.")])
    enqueued = []
    monkeypatch.setattr(summary_lane, "model_writer", writer)
    monkeypatch.setattr(api_v2, "enqueue_stored", lambda *a, **k: enqueued.append(a))
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE ingestion_inbox(source_ref TEXT, payload TEXT, created_at TEXT)")
    lane = summary_lane.SummaryLane(tmp_path)
    with pytest.raises(summary_lane.SummaryNotWritten):
        lane.run_due(conn, now=NOW)
    assert enqueued == [] and lane.consecutive_failures == 1


def test_repeated_summary_failure_waits_a_day(tmp_path, monkeypatch):
    """Each retry of a failing summary is a model call: after the third failure in a row
    the worker tries again the next day, not every five minutes."""
    from core.ingestion import worker as worker_module

    monkeypatch.setattr(worker_module, "ingestion_admission",
                        lambda lane, **k: SimpleNamespace(run=True, llm=True))
    worker = worker_module.IngestionWorker.__new__(worker_module.IngestionWorker)
    worker._summary_due = NOW
    worker._maintenance_due = NOW + timedelta(days=1)

    class Failing:
        consecutive_failures = 0

        def run_due(self, conn, now=None):
            self.consecutive_failures += 1
            raise RuntimeError("model down")

    worker._summary_lane = Failing()
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE ingestion_inbox(lane TEXT, created_at TEXT, state TEXT)")
    monkeypatch.setattr(worker_module, "_utcnow", lambda: NOW)
    monkeypatch.setattr(worker_module, "_READY", "? IS NULL")   # no candidate is ready
    worker.scheduler = worker_module.WeightedLaneScheduler()
    delays = []
    for _ in range(3):
        worker._summary_due = NOW
        worker.run_once(conn)
        delays.append(worker._summary_due - NOW)
    assert delays == [timedelta(minutes=5), timedelta(minutes=5), timedelta(days=1)]


# ── R6: health and comments say what TIMELINE is ─────────────────────────────

def test_health_and_comments_describe_timeline_as_on_demand():
    from core.ingestion import health

    group, _target, text = health.DESTINATIONS["TIMELINE"]
    assert group == "on_demand"
    assert "commit line" in text and "status" in text and "no other event" in text
    root = Path(health.__file__).resolve().parent
    stale = re.compile(r"TIMELINE is injected every turn|events? (?:are|is) injected every turn",
                       re.IGNORECASE)
    hits = [f"{p.relative_to(root)}:{n}" for p in root.rglob("*.py")
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
            if stale.search(line)]
    assert hits == []
