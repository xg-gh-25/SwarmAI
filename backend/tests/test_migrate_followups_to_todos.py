"""The one-time move of open follow-ups into ToDos (scripts/migrate_followups_to_todos,
design docs/design/2026-10-31-continue-lookback-todo.md §3).

- every open follow-up of the 7-day window, every follow_up gap and every event
  follow-up gets one recorded decision; the older unresolved ones are recorded expired;
- a done that cites nothing shown stays open and becomes a ToDo; a same must name a
  shown ToDo;
- a still-open gap becomes a ToDo and leaves its document; a done one is closed;
- a second run changes nothing; the window is rendered again without follow-ups.

Mock boundaries: the model (``llm``), the daemon's ToDo API (``FakeApi``) and the
governed withdrawal (``withdraw``). The day files are real, in tmp_path.
"""
from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta

import pytest

from scripts import migrate_followups_to_todos as M

TODAY = date(2026, 10, 31)


class FakeApi:
    def __init__(self, todos=()):
        self.todos = [dict(t) for t in todos]

    def all(self):
        return list(self.todos)

    def open(self):
        return [t for t in self.todos if t["status"] in ("pending", "overdue", "in_discussion")]

    def create(self, title, description, linked):
        todo = {"id": f"new-{len(self.todos)}", "title": title, "description": description,
                "status": "pending", "linked_context": json.dumps(linked)}
        self.todos.append(todo)
        return todo


def _row(sid, title, sentence, *, next_items=(), resolves=(), ts="10:00", **extra):
    return {"session_id": sid, "timestamp": ts, "session_start": "", "message_count": 4,
            "enriched": True, "title": title, "summary_sentence": sentence,
            "next_items": [{"next_id": n, "text": t} for n, t in next_items],
            "resolves": list(resolves), "topics": [], "decisions": [], "files_modified": [],
            "open_questions": [], "deliverables": [], "key_outputs": [], "validation_status": "",
            "coe_signal": "", "coe_topic": "", "git_commits": [], **extra}


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
    da = tmp_path / "Knowledge" / "DailyActivity"
    da.mkdir(parents=True)
    days = [(TODAY - timedelta(days=k)).isoformat() for k in range(8, -1, -1)]   # 9 files
    rows = {d: [] for d in days}
    # outside the window (the 2 oldest files): one unresolved, one resolved later
    rows[days[0]].append(_row("s-old", "Old work", "Did old work.",
                              next_items=[("old000000001", "Rename the old flag"),
                                          ("old000000002", "Drop the old table")]))
    rows[days[1]].append(_row("s-old2", "Old cleanup", "Dropped it.", resolves=["old000000002"]))
    # in the window
    rows[days[4]].append(_row("s-a", "Parser work", "Found the crash.",
                              next_items=[("open00000001", "Write the parser tests"),
                                          ("open00000002", "git push origin main"),
                                          ("open00000003", "Fix the tab parser crash")]))
    rows[days[5]].append(_row("s-b", "Docs", "Wrote the docs.",
                              next_items=[("done00000001", "Publish the docs")],
                              deliverables=["the docs page"]))
    rows[days[6]].append(_row("s-c", "Parser fix", "Fixed the tab parser crash; tests green.",
                              ts="11:00", resolves=["done00000001"]))
    rows[days[7]].append(_row("s-d", "Same work", "Planned the ToDo board.",
                              next_items=[("same00000001", "Clean the ToDo board")]))
    for d in days:
        (da / f"{d}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows[d]))
        body = "".join(f"<!-- da:summary:{r['session_id']} -->\n## {r['timestamp']} | {r['session_id']} | "
                       f"{r['title']}\n\n**Next:**\n- None\n<!-- /da -->\n" for r in rows[d])
        (da / f"{d}.md").write_text(f"---\ndate: {d}\n---\n{body}")
    und = tmp_path / "Projects" / "Widget" / "2-understanding"
    und.mkdir(parents=True)
    (und / "IMPROVEMENT.md").write_text(
        "# IMPROVEMENT\n\n## What to Watch For\n\n"
        "- [guideline] **Tab refresh** The tab does not refresh. gap kind: follow_up "
        "<!-- cand:cand-" + "a" * 24 + ".unit -->\n"
        "  <!-- ref:0 | last:2026-10-29 | decay:active | source:S3 -->\n\n"
        "- [guideline] **Slow build** The build is slow. gap kind: debt "
        "<!-- cand:cand-" + "b" * 24 + ".unit -->\n"
        "  <!-- ref:0 | last:2026-10-29 | decay:active | source:S3 -->\n")
    (tmp_path / ".context").mkdir()
    (tmp_path / ".context" / "TIMELINE.md").write_text(
        f"### {days[6]} Parser fixed\n- what: fixed\n- follow_up: Add a fuzz test for the parser.\n")
    return tmp_path


def _llm(prompt):
    item = prompt.split("## The open item", 1)[1].split("## Later activity", 1)[0]
    if "git push" in item:
        return {"decision": "release", "cite": "", "todo_id": "", "title": "", "description": ""}
    if "Fix the tab parser crash" in item:
        cite = next(line.split("]")[0][1:] for line in prompt.splitlines()
                    if line.startswith("[r") and "Fixed the tab parser crash" in line)
        return {"decision": "done", "cite": cite, "todo_id": "", "title": "", "description": ""}
    if "parser tests" in item:          # claims done, cites nothing that was shown
        return {"decision": "done", "cite": "r999", "todo_id": "", "title": "", "description": ""}
    if "Clean the ToDo board" in item:
        return {"decision": "same", "cite": "", "todo_id": "t-board", "title": "", "description": ""}
    if "Tab refresh" in item:
        return {"decision": "open", "cite": "", "todo_id": "",
                "title": "Make the tab refresh on return", "description": "It does not refresh."}
    return {"decision": "open", "cite": "", "todo_id": "", "title": "", "description": ""}


def _go(ws, api, withdrawn, **kw):
    return asyncio.run(M.run(ws, record=ws / "record.jsonl", api=api, apply=True, max_items=100,
                             jobs=3, today=TODAY, llm=_llm,
                             withdraw=lambda a, r, status: withdrawn.append((a, r, status)), **kw))


def test_every_item_gets_one_decision_and_open_ones_become_todos(ws):
    api = FakeApi([{"id": "t-board", "title": "Clean the ToDo board", "status": "pending"}])
    withdrawn: list = []
    out = _go(ws, api, withdrawn)
    rec = {r["key"]: r for r in M._rows(ws / "record.jsonl")}
    assert out["da"] == 4 and out["gap"] == 1 and out["event"] == 1
    assert rec["da:open00000002"]["decision"] == "release"
    assert rec["da:open00000003"]["decision"] == "done"
    assert rec["da:same00000001"] == {**rec["da:same00000001"], "decision": "same", "todo_id": "t-board"}
    # an uncited done stays open and becomes a ToDo, with its source
    assert rec["da:open00000001"]["decision"] == "open" and "r999" in rec["da:open00000001"]["note"]
    titles = {t["title"] for t in api.todos}
    assert {"Write the parser tests", "Make the tab refresh on return",
            "Add a fuzz test for the parser."} <= titles
    created = next(t for t in api.todos if t["title"] == "Write the parser tests")
    assert json.loads(created["linked_context"])["migrated_from"] == "da:open00000001"
    # resolved in the window, or outside it: not migrated; the old unresolved one expired
    assert "da:done00000001" not in rec and "da:old000000002" not in rec
    assert rec["da:old000000001"]["decision"] == "expired"
    # the open gap became a ToDo and leaves the document; the debt gap is untouched
    (gap,) = withdrawn
    assert "cand:cand-" + "a" * 24 in gap[0] and gap[2] == "done"
    assert rec[f"gap:{gap[0]}"]["created_todo_id"] in gap[1]
    assert len(api.todos) == 4


def test_a_second_run_changes_nothing(ws):
    api = FakeApi([{"id": "t-board", "title": "Clean the ToDo board", "status": "pending"}])
    withdrawn: list = []
    _go(ws, api, withdrawn)
    lines = (ws / "record.jsonl").read_text().splitlines()
    before = len(api.todos)
    out = _go(ws, api, withdrawn)
    assert out["left"] == 0 and len(api.todos) == before and len(withdrawn) == 1
    assert (ws / "record.jsonl").read_text().splitlines() == lines


def test_a_bounded_run_continues_where_it_stopped(ws):
    api = FakeApi([{"id": "t-board", "title": "Clean the ToDo board", "status": "pending"}])
    withdrawn: list = []
    first = asyncio.run(M.run(ws, record=ws / "record.jsonl", api=api, apply=True, max_items=2,
                              jobs=1, today=TODAY, llm=_llm,
                              withdraw=lambda a, r, status: withdrawn.append((a, r, status))))
    assert first["left"] == 4 and "rerendered_rows" not in first      # not yet re-rendered
    out = _go(ws, api, withdrawn)
    assert out["left"] == 0 and out["already_recorded"] == 2


def test_the_window_is_rendered_again_without_followups(ws):
    _go(ws, FakeApi([{"id": "t-board", "title": "Clean the ToDo board", "status": "pending"}]), [])
    da = ws / "Knowledge" / "DailyActivity"
    for k in range(0, 7):
        day = (TODAY - timedelta(days=k)).isoformat()
        assert "**Next:**" not in (da / f"{day}.md").read_text()
        assert all("next_items" not in r for r in M._rows(da / f"{day}.jsonl"))
    oldest = (TODAY - timedelta(days=8)).isoformat()
    assert any(r.get("next_items") for r in M._rows(da / f"{oldest}.jsonl"))   # outside: kept


def test_dry_run_writes_nothing_and_calls_no_model(ws):
    def no_model(_p):
        raise AssertionError("no model call in a dry run")
    out = asyncio.run(M.run(ws, record=ws / "record.jsonl", api=FakeApi(), apply=False,
                            max_items=100, jobs=1, today=TODAY, llm=no_model))
    assert out["dry_run"] and out["left"] == 6 and not (ws / "record.jsonl").exists()


def test_a_same_naming_an_unknown_todo_is_open():
    item = M.Item(key="da:x", source="da", text="Clean up", day="2026-10-30")
    got = M.checked({"decision": "same", "todo_id": "invented"}, item, [], [], [{"id": "t1"}])
    assert got["decision"] == "open" and got["title"] == "Clean up"


def test_a_later_batch_is_shown_the_todos_an_earlier_one_created(ws):
    """Two sessions left the same work: the second batch sees the first one's ToDo and
    can answer same, so one ToDo is made, not two."""
    da = ws / "Knowledge" / "DailyActivity"
    day = (TODAY - timedelta(days=1)).isoformat()
    with (da / f"{day}.jsonl").open("a") as fh:
        fh.write(json.dumps(_row("s-e", "Parser again", "Looked again.",
                                 next_items=[("dupe00000001", "Write the parser tests too")])) + "\n")

    def llm(prompt):
        item = prompt.split("## The open item", 1)[1].split("## Later activity", 1)[0]
        todos = prompt.split("## Open ToDos (id: title)", 1)[1]
        if "parser tests" in item:
            made = [line.split(":")[0] for line in todos.splitlines() if "parser tests" in line]
            if made:
                return {"decision": "same", "cite": "", "todo_id": made[0], "title": "", "description": ""}
            return {"decision": "open", "cite": "", "todo_id": "", "title": "Write the parser tests",
                    "description": "Cover tabs."}
        return {"decision": "release", "cite": "", "todo_id": "", "title": "", "description": ""}
    api = FakeApi()
    asyncio.run(M.run(ws, record=ws / "record.jsonl", api=api, apply=True, max_items=100, jobs=1,
                      today=TODAY, llm=llm, withdraw=lambda a, r, status: None))
    assert [t["title"] for t in api.todos].count("Write the parser tests") == 1
