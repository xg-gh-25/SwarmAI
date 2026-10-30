"""Design 2026-10-30-cdf-agent-decides D12 (step 8): "the user corrected the agent" has one
source — the corrections the after-session review recorded — and nothing judges text.

Questions:
- ``queries.corrections`` returns S7 correction units only (not other sources, not other
  types), with their session, the skill they name and whether the review's ``repeats``
  declaration was resolved (I9: the only input of the repeat rate);
- ``read_corrections`` says from when the record covers, and "nothing known" (None) when
  there is no store or no review yet;
- the skill-health route counts a correction against the skill it names, in the window;
- no user-intent regex is left: the detector, its patterns and the evolution cycle that
  consumed them are gone, and the chat path registers no prompt-matching hook (I6).
"""
from __future__ import annotations

import importlib.util
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

NOW = datetime.now(timezone.utc)


def _unit(conn, cid, *, source="S7", kind="cognition", type_="correction", session="sess-1",
          skill=None, repeats=None, at=None):
    payload = {"kind": kind, "type": type_, "title": "t", "what": "w"}
    if skill:
        payload["skill"] = skill
    conn.execute("""INSERT INTO ingestion_inbox(id,idem_key,source,source_ref,lane,payload,payload_schema,
        content_hash,envelope,project_snapshot,state,created_at,source_v2,unit_kind)
        VALUES(?,?,?,?,'session',?,'v','h','{}','{}','done',?,?,?)""",
                 (cid, cid, source, session, json.dumps(payload), (at or NOW).isoformat(), source, kind))
    decision = {"version_vector": {"repeats": repeats} if repeats else {}}
    conn.execute("""INSERT INTO ingestion_units(unit_id,candidate_id,claim_group,decision,state,owner_epoch,updated_at)
        VALUES(?,?,'{}',?,'written',1,?)""", (f"{cid}.unit", cid, json.dumps(decision), NOW.isoformat()))


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))
    from core.ingestion.store import connect_rw
    conn = connect_rw()
    yield conn
    conn.close()


def test_only_the_reviews_corrections_are_counted(store):
    from core.ingestion.queries import corrections
    _unit(store, "c1", skill="s_pdf", repeats="Projects/x#C#1")
    _unit(store, "c2", session="sess-2")
    _unit(store, "c3", source="S1")                     # said in chat, not the review
    _unit(store, "c4", type_="guideline")               # not a correction
    _unit(store, "c5", kind="gap", type_="correction")  # not a cognition unit
    store.commit()
    rows = corrections(store)
    assert [(r["session_id"], r["skill"], r["repeats"]) for r in rows] == [
        ("sess-1", "s_pdf", True), ("sess-2", None, False)]
    assert all(r["at"].tzinfo is not None for r in rows)


def test_read_corrections_knows_from_when_it_covers(store):
    from core.ingestion.queries import read_corrections
    assert read_corrections() == ([], None)            # a store, but no review yet
    first = NOW - timedelta(days=3)
    _unit(store, "g1", type_="guideline", at=first)     # any review record starts the coverage
    _unit(store, "c1", at=NOW)
    store.commit()
    rows, start = read_corrections()
    assert len(rows) == 1 and abs((start - first).total_seconds()) < 1


def test_no_store_is_nothing_known_not_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path / "empty"))
    from core.ingestion.queries import read_corrections
    assert read_corrections() == ([], None)
    assert not (tmp_path / "empty" / "ingestion.db").exists()     # read-only: never created


def test_skill_health_counts_a_correction_against_the_skill_it_names(store, tmp_path):
    from core.skill_metrics import SkillMetricsStore
    from routers.skills import _load_skill_success_inputs
    metrics = SkillMetricsStore(tmp_path / "data.db")
    for i in range(4):
        metrics.record("pdf", f"s{i}", "success", 1.0)
    metrics.close()
    _unit(store, "c1", skill="s_pdf")
    _unit(store, "c2", skill="s_pdf", at=NOW - timedelta(days=40))   # outside the window
    _unit(store, "c3")                                               # names no skill
    store.commit()
    runs, fixes = _load_skill_success_inputs()
    assert runs == {"pdf": 4} and fixes == {"s_pdf": 1}


def test_no_user_intent_regex_is_left():
    root = Path(__file__).resolve().parents[1]
    for mod in ("core.evolution_optimizer", "core.session_miner", "core.skill_fitness",
                "core.llm_optimizer", "core.user_observer", "jobs.run_evolution"):
        assert importlib.util.find_spec(mod) is None, mod
    import core.extraction_patterns as ep
    import core.runtime_hooks as rh
    assert not hasattr(ep, "CORRECTION_PATTERNS")
    for name in ("_CORRECTION_PATTERNS_EN", "_CORRECTION_PATTERNS_META", "_HIGH_SIGNAL_PATTERNS",
                 "create_user_correction_detector", "read_correction_stats"):
        assert not hasattr(rh, name), name
    hook = (root / "hooks" / "skill_metrics_hook.py").read_text(encoding="utf-8")
    assert "CORRECTION" not in hook and "SkillEvals" not in hook
    from jobs.system_jobs import SYSTEM_JOB_IDS
    assert "evolution-cycle" not in SYSTEM_JOB_IDS


def test_the_chat_path_registers_no_prompt_matching_hook():
    from core.hook_builder import HookRegistry
    from core.runtime_hooks import register_runtime_hooks
    registry = HookRegistry()
    register_runtime_hooks(registry, {"sdk_session_id": "s"})
    names = {name for hooks in registry._hooks.values() for name, _h, _t in hooks}
    assert names and "user_correction_detector" not in names
    assert "post_compact_injection" in names            # the registry was really filled


def test_the_review_names_the_skill_a_correction_is_about():
    """The skill a correction counts against is named by the agent that saw it used."""
    from core.ingestion import contracts_v2
    from core.ingestion.sources import extract_llm, s7_review
    item = extract_llm.unit_schema(s7_review.ALLOWED_KINDS)["properties"]["cognition_units"]["items"]
    assert item["properties"]["skill"]["description"] == contracts_v2.SKILL_RULE
    unit = contracts_v2.SOURCE_UNIT.validate_python(
        {"kind": "cognition", "type": "correction", "scope": "agent", "title": "t", "what": "w",
         "evidence": ["event:e1"], "skill": "s_pdf"})
    assert unit.skill == "s_pdf"
    prompt = s7_review.build_prompt(s7_review.ALLOWED_KINDS, {}, "", "")
    assert "name that skill in skill" in prompt
