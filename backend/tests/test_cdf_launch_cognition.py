"""The shape of what CDF keeps in the files injected into every session
(2026-10-29-cdf-launch §2 C1-C7).

- C1 (superseded by D11): there is no daily write quota; a unit is written whatever
  was written today.
- C3 (superseded by D11): MEMORY and EVOLUTION are injected whole; nothing is scored.
- C4: a relation hides no entry.
- C6: per-turn entries are one line; STEERING/USER/TOOLS metadata stays readable (a
  comment marker inside an entry is written as entities: test_cdf_refactor).
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


def test_a_unit_is_written_however_much_was_written_today(cdf_env):
    """No daily write quota (design 2026-10-30-cdf-agent-decides D11): an old quota row
    of today changes nothing, and no new one is written."""
    from core.ingestion import api
    conn = api.connect_rw()
    today = datetime.now(timezone.utc).date().isoformat()
    conn.execute("INSERT INTO ingestion_quota VALUES ('earlier','MEMORY',NULL,?,4096,'committed',?)",
                 (today, (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()))
    conn.commit(); conn.close()
    _memory_unit(cdf_env)
    rows = _drain(cdf_env)
    assert rows[-1]["state"] == "written", rows[-1]
    assert "Check the retry rate weekly" in (cdf_env["workspace"] / ".context/MEMORY.md").read_text(encoding="utf-8")
    conn = api.connect_rw()
    assert conn.execute("SELECT COUNT(*) FROM ingestion_quota").fetchone()[0] == 1
    conn.close()


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


def test_a_supersedes_relation_never_hides_an_entry(tmp_path, monkeypatch):
    """An entry in the file is in force: a relation only links (brain-driven §2.1, I4);
    an entry another one replaces leaves the file by a declared override."""
    from core.memory_injection import select_context_file
    path, conn = _memory_file(tmp_path, monkeypatch, (
        "# Memory\n\n## Guidelines\n"
        "- [guideline] **Old rule** Use tabs. <!-- cand:old.1 -->\n"
        "- [guideline] **New rule** Use four spaces, not tabs. <!-- cand:new.1 -->\n"))
    conn.execute("INSERT INTO ingestion_relations(subject_anchor,predicate,object_anchor,created,inactive) "
                 "VALUES ('.context/MEMORY.md#Guidelines#cand:new.1','supersedes',"
                 "'.context/MEMORY.md#Guidelines#cand:old.1','now',0)")
    conn.commit()
    sel = select_context_file(path, estimate=len, conn=conn)
    conn.close()
    assert "New rule" in sel.text and "Old rule" in sel.text


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


def test_memory_is_injected_whole_whatever_its_size(tmp_path, monkeypatch):
    """No budget and no score (design 2026-10-30-cdf-agent-decides D11): every unit is in
    the prompt, the metadata comments are not, and the carried units are recorded."""
    from core.memory_injection import select_context_file
    body = "# Memory\n\n## Guidelines\n" + "".join(
        f"- [guideline] **Rule {i}** {'word ' * 400} <!-- cand:r{i}.1 -->\n" for i in range(40))
    path, conn = _memory_file(tmp_path, monkeypatch, body)
    sel = select_context_file(path, estimate=len, conn=conn)
    conn.close()
    assert all(f"Rule {i}" in sel.text for i in range(40)) and "cand:" not in sel.text
    assert sel.omitted == 0 and len(sel.anchors) == 40

