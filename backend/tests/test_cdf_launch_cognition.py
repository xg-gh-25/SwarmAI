"""The shape of what CDF keeps in the files injected into every session
(2026-10-29-cdf-launch §2 C1-C7).

- C1: a unit bound for MEMORY waits for the next UTC day when today's MEMORY budget is
  spent (counted globally, not per project), and no attempt is counted.
- C3: value decides over age: a used 30-day-old entry outranks an unused 3-day-old one.
- C4: a superseded entry is not injected, under budget too.
- C6: per-turn entries are one line; STEERING/USER/TOOLS metadata stays readable; a
  unit carrying a comment marker is refused at card 0.
- C7: titles keep their characters ("Use C#"); a DDD title with "|" or "**" renders.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tests.test_cdf_v2_e2e import Judge, _drain, cdf_env  # noqa: F401
from tests.test_cdf_launch_state_machine import _owned, _worker


def _memory_unit(env):
    from core.ingestion import api_v2
    from core.ingestion.sources import s5_metrics
    from tests.test_cdf_v2_e2e import Extractor
    root = api_v2.register_root("metric_window", "week:retry_rate", "0.4", "measured")
    unit = {"kind": "cognition", "type": "guideline", "scope": "world",
            "title": "Check the retry rate weekly",
            "what": "Read the weekly retry rate before changing the review gate.",
            "applicability": "When tuning the review gate.", "evidence": ["metric:week:retry_rate"]}
    s5_metrics.extract_metrics("week", {"retry_rate": 0.4}, run_count=10, root_ids=(root,),
                               llm=Extractor([unit]))


def test_a_spent_daily_budget_defers_to_tomorrow_without_an_attempt(cdf_env):
    from core.ingestion import api
    conn = api.connect_rw()
    today = datetime.now(timezone.utc).date().isoformat()
    conn.execute("INSERT INTO ingestion_quota VALUES ('earlier','MEMORY','OtherProject',?,4096,'committed',?)",
                 (today, (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()))
    conn.commit(); conn.close()
    _memory_unit(cdf_env)
    rows = _drain(cdf_env)
    unit = rows[-1]
    assert (unit["state"], unit["reason"], unit["attempts"]) == ("deferred", "quota", 0), unit
    assert datetime.fromisoformat(unit["next_attempt_at"]).date() > datetime.now(timezone.utc).date()
    memory = (cdf_env["workspace"] / ".context/MEMORY.md").read_text(encoding="utf-8")
    assert "Check the retry rate weekly" not in memory


def test_with_budget_left_the_unit_is_written_and_its_bytes_counted(cdf_env):
    _memory_unit(cdf_env)
    rows = _drain(cdf_env)
    assert rows[-1]["state"] == "written", rows[-1]
    from core.ingestion import api
    conn = api.connect_rw()
    q = conn.execute("SELECT store,project,status,bytes FROM ingestion_quota").fetchone()
    conn.close()
    assert (q[0], q[1], q[2]) == ("MEMORY", None, "committed") and q[3] > 50


def _memory_file(tmp_path: Path, monkeypatch, body: str) -> tuple[Path, object]:
    from core.ingestion import api
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    monkeypatch.setenv("SWARM_DATA_DIR", str(data))
    monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path / "SwarmWS"))
    ctx = tmp_path / "SwarmWS/.context"
    ctx.mkdir(parents=True, exist_ok=True)
    path = ctx / "MEMORY.md"
    path.write_text(body, encoding="utf-8")
    return path, api.connect_rw()


def test_a_superseded_entry_is_not_injected_under_budget(tmp_path, monkeypatch):
    from core.memory_injection import select_context_file
    path, conn = _memory_file(tmp_path, monkeypatch, (
        "# Memory\n\n## Guidelines\n"
        "- [guideline] **Old rule** Use tabs. <!-- cand:old.1 -->\n"
        "- [guideline] **New rule** Use four spaces, not tabs. <!-- cand:new.1 -->\n"))
    conn.execute("INSERT INTO ingestion_relations(subject_anchor,predicate,object_anchor,created,inactive) "
                 "VALUES ('.context/MEMORY.md#Guidelines#cand:new.1','supersedes',"
                 "'.context/MEMORY.md#Guidelines#cand:old.1','now',0)")
    conn.commit()
    sel = select_context_file(path, budget=100_000, estimate=len, conn=conn)
    conn.close()
    assert "New rule" in sel.text and "Old rule" not in sel.text


def test_use_outweighs_a_few_days_of_age(tmp_path, monkeypatch):
    from core.memory_injection import _score_unit
    from core.ingestion.units import parse_units
    path, conn = _memory_file(tmp_path, monkeypatch, (
        "# Memory\n\n## Guidelines\n"
        "- [guideline] **Used** Proven guidance. <!-- cand:used.1 -->\n"
        "- [guideline] **Fresh** New guidance. <!-- cand:fresh.1 -->\n"))
    now = datetime(2026, 10, 29, tzinfo=timezone.utc)
    units = {u.title: u for u in parse_units(path, root=path.parent.parent)}
    for i in range(10):
        conn.execute("INSERT INTO unit_usage(anchor,kind,session_id,at) VALUES (?,?,?,?)",
                     (units["Used"].anchor, "read_by_agent", f"s{i}", (now - timedelta(days=30)).isoformat()))
    for name, days in (("used.1", 30), ("fresh.1", 3)):
        conn.execute("INSERT INTO ingestion_writes(op_id,op_kind,unit_id,target_path,before_hash,after_hash,"
                     "anchors_present,anchors_absent,relations,step,owner_epoch,created_at,updated_at) "
                     "VALUES (?,'ingestion_unit',?,'.context/MEMORY.md','','','[]','[]','[]','done',1,?,?)",
                     (f"ingestion:{name}", name, (now - timedelta(days=days)).isoformat(),
                      (now - timedelta(days=days)).isoformat()))
    conn.commit()
    used = _score_unit(conn, path, units["Used"], "operational", now, None).score
    fresh = _score_unit(conn, path, units["Fresh"], "operational", now, None).score
    conn.close()
    assert used > fresh


def test_per_turn_entries_are_one_line_and_steering_metadata_is_readable():
    from core.ddd_entry_lifecycle import _META_RE
    from core.ingestion.writer import render_unit
    group = {"title": "Use C#", "what": "Prefer records.\n\n## Not a heading", "source": "S1",
             "project_hint": "P"}
    memory = render_unit({"destination": {"store": "MEMORY"}, "card1": {"entry_type": "guideline"}},
                         "u.1", group)
    lead = memory.split("\n")[0]
    assert "Use C#" in lead and "## Not a heading" in lead and "\n##" not in memory
    steering = render_unit({"destination": {"store": "STEERING"}, "card1": {"entry_type": "guideline"}},
                           "u.2", group)
    meta = steering.split("\n")[1].strip()
    assert _META_RE.fullmatch(meta) or _META_RE.search(meta), meta
    assert "project_hint" in steering.split("\n")[0] and "project_hint" not in meta


def test_a_ddd_title_with_table_or_bold_characters_renders():
    from core.ingestion.writer import render_unit
    out = render_unit({"destination": {"store": "DDD"}, "card1": {"entry_type": "guideline"}},
                      "u.3", {"title": "Cache | TTL **hot**", "what": "Expire after one hour.",
                              "how": "Set the TTL.", "why": "Stale reads."})
    assert "Cache / TTL hot" in out


def test_card0_refuses_a_comment_marker_in_an_entry(tmp_path):
    from core.ingestion.pipeline_v2 import _card0
    from core.ingestion.contracts_v2 import SOURCE_UNIT
    unit = SOURCE_UNIT.validate_python({
        "kind": "cognition", "type": "guideline", "scope": "world", "title": "T",
        "what": "Keep <!-- this --> out.", "applicability": "Always.", "evidence": ["event:e"]})
    assert _card0(unit, {"envelope": "{}"}).reason == "unrenderable"


def test_heavy_use_keeps_an_old_entry_ahead_of_an_unused_new_one(tmp_path, monkeypatch):
    """A capped usage factor (at most x2) lost to four months of age; each use keeps counting."""
    from core.memory_injection import _score_unit
    from core.ingestion.units import parse_units
    path, conn = _memory_file(tmp_path, monkeypatch, (
        "# Memory\n\n## Guidelines\n"
        "- [guideline] **Old** Long-used guidance. <!-- cand:old.1 -->\n"
        "- [guideline] **New** Unused guidance. <!-- cand:new.1 -->\n"))
    now = datetime(2026, 10, 29, tzinfo=timezone.utc)
    units = {u.title: u for u in parse_units(path, root=path.parent.parent)}
    old_at = (now - timedelta(days=120)).isoformat()
    for i in range(10):
        conn.execute("INSERT INTO unit_usage(anchor,kind,session_id,at) VALUES (?,?,?,?)",
                     (units["Old"].anchor, "read_by_agent", f"s{i}", old_at))
    for name, days in (("old.1", 120), ("new.1", 1)):
        at = (now - timedelta(days=days)).isoformat()
        conn.execute("INSERT INTO ingestion_writes(op_id,op_kind,unit_id,target_path,before_hash,after_hash,"
                     "anchors_present,anchors_absent,relations,step,owner_epoch,created_at,updated_at) "
                     "VALUES (?,'ingestion_unit',?,'.context/MEMORY.md','','','[]','[]','[]','done',1,?,?)",
                     (f"ingestion:{name}", name, at, at))
    conn.commit()
    old = _score_unit(conn, path, units["Old"], "operational", now, None).score
    new = _score_unit(conn, path, units["New"], "operational", now, None).score
    conn.close()
    assert old > new
