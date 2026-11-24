"""The one-time backfill (scripts/cdf_backfill_user_words_timeline.py) on a temp store.

The store is first filled by the live pipeline as it decided before the change: user_check
refusing (so a quoted correction citing tool output went to the project DDD) and the
third-party answer unknown (so every event citing tool output went under "Not injected").
Then, with the new rules:

- the dry run reports the unit to decide again and the events to move, calls no model and
  leaves both stores and every workspace file byte-identical;
- ``--apply`` backs up both stores and the files it touches first (verified), writes the
  correction to EVOLUTION through the writer and withdraws its DDD copy (recorded),
  moves the own-work event under its month without its taint mark, leaves the
  third-party and unknown ones, never lowers a row count or removes a file, and a second
  ``--apply`` changes nothing;
- a backup that fails stops the run before any write.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sqlite3
import uuid
from pathlib import Path
from typing import Any

import pytest

from tests.test_cdf_v2_e2e import SESSION, _drain, _record_person_message, cdf_env  # noqa: F401

SAID = "不对，改之前先看当前状态"


def _enqueue(unit: dict[str, Any]) -> None:
    from core.ingestion import api_v2
    from core.ingestion.contracts_v2 import SOURCE_UNIT
    result = api_v2.enqueue_unit("S7", SESSION, SOURCE_UNIT.validate_python(unit), session_id=SESSION,
                                 run_id=None, project_snapshot={"session_project": "P"},
                                 attempt_id=f"attempt:{uuid.uuid4().hex}")
    assert result.status == "queued", result


def _legacy_tool(env, ref: str, text: str) -> str:
    """A tool result recorded as before the third-party flag (kind ``tool_result``)."""
    from core import session_inputs
    return session_inputs.record_tool_result(SESSION, text, ref=ref)[0]


def _event(title: str, evidence: list[str]) -> dict[str, Any]:
    return {"kind": "event", "when": "2026-11-18", "title": title, "what": f"{title}, and why.",
            "impact": "Later work follows it.", "event_kind": "event", "evidence": evidence}


@pytest.fixture
def before(cdf_env, monkeypatch):
    """A store as the pipeline wrote it before the change."""
    from core.ingestion import provenance, user_check
    user = _record_person_message(cdf_env, "m1", SAID)[0]
    asyncio.run(cdf_env["db"].messages.put({"id": "a1", "session_id": SESSION, "role": "assistant", "content": [
        {"type": "tool_use", "id": "tu-bash", "name": "Bash", "display": {"object": "git log -3"}},
        {"type": "tool_use", "id": "tu-web", "name": "WebFetch", "display": {"object": "https://x.org"}}]}))
    own = _legacy_tool(cdf_env, "tu-bash", "commit 4c77b68 test")
    web = _legacy_tool(cdf_env, "tu-web", "<html>outside</html>")
    lost = _legacy_tool(cdf_env, "tu-unknown", "no call recorded for this one")
    old = pytest.MonkeyPatch()               # the pipeline as it decided before the change
    old.setattr(user_check, "grounds", lambda c, t, **_: user_check.Verdict(False, "old rule"))
    old.setattr(provenance, "lineage_third_party", lambda *a, **k: None)
    _enqueue({"kind": "cognition", "type": "correction", "scope": "agent",
              "title": "Read the current state before editing",
              "what": "Before changing anything, read the current state instead of memory.",
              "applicability": "Before any edit.", "evidence": [f"event:{user}", f"event:{own}"],
              "user_quote": "改之前先看当前状态"})
    _enqueue({"kind": "cognition", "type": "correction", "scope": "agent", "title": "An unquoted lesson",
              "what": "Something the agent concluded on its own from the log.",
              "applicability": "Sometimes.", "evidence": [f"event:{own}"]})
    _enqueue(_event("Chose the tab design", [f"event:{user}", f"event:{own}"]))
    _enqueue(_event("Read an outside page", [f"event:{user}", f"event:{web}"]))
    _enqueue(_event("Something of unknown origin", [f"event:{user}", f"event:{lost}"]))
    try:
        rows = _drain(cdf_env)
    finally:
        old.undo()                            # the new rules from here on
    assert [r["state"] for r in rows] == ["written"] * 5, [(r["state"], r["reason"]) for r in rows]
    timeline = (cdf_env["workspace"] / ".context/TIMELINE.md").read_text(encoding="utf-8")
    assert timeline.count("taint:external") == 3 and not re.search(r"^## 2026-11$", timeline, re.M)
    asked: list[str] = []
    monkeypatch.setattr(user_check, "grounds",
                        lambda c, t, **_: asked.append(c) or user_check.Verdict(True, "said"))
    return {**cdf_env, "asked": asked}


def _snapshot(env) -> dict[str, str]:
    out = {}
    for path in sorted(p for p in env["workspace"].rglob("*") if p.is_file()):
        out[str(path.relative_to(env["workspace"]))] = hashlib.sha256(path.read_bytes()).hexdigest()
    for name in ("ingestion.db", "data.db"):
        out[name] = hashlib.sha256((env["data"] / name).read_bytes()).hexdigest()
    return out


def _counts(env) -> dict[str, int]:
    out = {}
    for name in ("ingestion.db", "data.db"):
        with sqlite3.connect(env["data"] / name) as conn:
            for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                         "AND name NOT LIKE 'sqlite_%'"):
                out[f"{name}:{table}"] = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    return out


def _run(env, *, apply: bool):
    from scripts.cdf_backfill_user_words_timeline import run
    return run(env["workspace"], apply=apply)


def _sections(env) -> dict[str, str]:
    text = (env["workspace"] / ".context/TIMELINE.md").read_text(encoding="utf-8")
    parts = re.split(r"^## (.+)$", text, flags=re.MULTILINE)
    return {parts[i].strip(): parts[i + 1] for i in range(1, len(parts), 2)}


def test_the_dry_run_reports_and_writes_nothing(before):
    snapshot = _snapshot(before)
    report = _run(before, apply=False)
    assert [u["title"] for u in report["units"]["redecide"]] == ["Read the current state before editing"]
    assert {u["reason"] for u in report["units"]["skipped"]} == {"no_user_quote"}
    assert [e["title"] for e in report["events"]["move"]] == ["2026-11-18 Chose the tab design"]
    assert sorted(e["reason"] for e in report["events"]["stay"]) == ["cannot_tell", "third_party"]
    assert _snapshot(before) == snapshot              # both stores and every file byte-identical
    assert before["asked"] == []                      # no model call
    assert not (before["data"] / "backups").exists()


def test_apply_backs_up_first_is_idempotent_and_never_deletes(before):
    from core.ingestion.store import connect_rw
    moving = _run(before, apply=False)["events"]["move"][0]
    conn = connect_rw()
    conn.execute("INSERT INTO ingestion_relations VALUES (?,?,?,?,0)",
                 (moving["anchor"], "derived_from", "Projects/P/2-understanding/PROJECT.md#Recent Decisions#x",
                  "2026-11-18T00:00:00+00:00"))
    conn.commit()
    conn.close()
    files_before, counts_before = set(_snapshot(before)), _counts(before)
    improvement = before["ddd"] / "IMPROVEMENT.md"
    assert "Read the current state before editing" in improvement.read_text(encoding="utf-8")
    report = _run(before, apply=True)

    backup = Path(report["backup"]["dir"])
    for name in ("ingestion.db", "data.db"):
        with sqlite3.connect(backup / name) as conn:
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert {".context/TIMELINE.md", ".context/EVOLUTION.md",
            "Projects/P/2-understanding/IMPROVEMENT.md"} <= set(report["backup"]["files"])
    # the backed-up TIMELINE is the one before the move
    assert (backup / "files/.context/TIMELINE.md").read_text(encoding="utf-8").count("taint:external") == 3

    (unit,) = report["units"]["redecide"]
    assert unit["outcome"].startswith("written to EVOLUTION"), unit["outcome"]
    assert before["asked"]                            # user_check was asked, with --apply only
    evolution = (before["workspace"] / ".context/EVOLUTION.md").read_text(encoding="utf-8")
    assert "Read the current state before editing" in evolution
    assert "Read the current state before editing" not in improvement.read_text(encoding="utf-8")
    from core.ingestion.store import connect_ro
    from core.ingestion import withdrawn
    conn = connect_ro()
    try:
        records = withdrawn.records(conn)
    finally:
        conn.close()
    assert [(r.status, r.path) for r in records] == [("overridden", "Projects/P/2-understanding/IMPROVEMENT.md")]

    sections = _sections(before)
    assert "Chose the tab design" in sections["2026-11"] and "taint:external" not in sections["2026-11"]
    assert "Read an outside page" in sections["Not injected"]
    assert "Something of unknown origin" in sections["Not injected"]
    assert sections["Not injected"].count("taint:external") == 2
    # the moved event's records follow it: its relation carried (the old one ended, kept),
    # its unit injectable, the move recorded
    new_anchor = f".context/TIMELINE.md#2026-11#cand:{moving['unit_id']}"
    conn = connect_ro()
    try:
        relations = {(r[0], r[1]) for r in conn.execute(
            "SELECT subject_anchor, inactive FROM ingestion_relations WHERE predicate='derived_from'")}
        destination = json.loads(conn.execute("SELECT destination FROM ingestion_units WHERE unit_id=?",
                                              (moving["unit_id"],)).fetchone()[0])
        moved = [json.loads(r[0]) for r in conn.execute(
            "SELECT detail FROM ingestion_events WHERE kind='backfill_timeline'")]
    finally:
        conn.close()
    assert {(moving["anchor"], 1), (new_anchor, 0)} <= relations
    assert destination["injectable"] is True
    assert [m["to"] for m in moved] == [new_anchor]

    counts_after = _counts(before)
    assert all(counts_after.get(k, 0) >= v for k, v in counts_before.items()), \
        {k: (v, counts_after.get(k)) for k, v in counts_before.items() if counts_after.get(k, 0) < v}
    assert files_before - {"ingestion.db", "data.db"} <= set(_snapshot(before))   # no file removed

    snapshot, counts = _snapshot(before), _counts(before)
    again = _run(before, apply=True)
    assert again["units"]["redecide"] == [] and again["events"]["move"] == []
    assert "backup" not in again                      # nothing to do: nothing written
    assert _snapshot(before) == snapshot and _counts(before) == counts


def test_a_failed_backup_stops_before_any_write(before, monkeypatch):
    from scripts import cdf_backfill_user_words_timeline as backfill
    snapshot = _snapshot(before)

    def corrupt(src, dst, **_):
        Path(dst).write_bytes(b"not the same bytes")

    monkeypatch.setattr(backfill.shutil, "copy2", corrupt)
    with pytest.raises(backfill.BackupFailed):
        _run(before, apply=True)
    after = _snapshot(before)
    # only the backup directory was written, under the data dir; nothing in the stores or files
    assert after == snapshot
    assert before["asked"] == []
