"""Completion summary inputs: resume-class acks and the DELIVER ``outcome`` shape.

Two defects in what the completion summary reads, tested at the place each one is
decided:

- ``core.run_delivery_summary._is_ack`` decides which user messages are not the run's
  request. ``resume`` and similar control words were missing from ``_ACKS``, so a run
  created right after "resume" recorded "resume" as its intent.
- ``pipeline_validator.validate_artifact_data('deliver', ...)`` never checked
  ``outcome``. ``review_summary`` reads only ``{problem_before, result_now,
  approach[]}``, so a plain-string outcome passed DELIVER and the summary showed 未记录
  with no error. The deliver template also lacked ``outcome``, so authors had no shape
  to copy. An ABSENT outcome stays valid: report-and-commit.md defines it as a visible
  未记录.
"""
import copy
import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest

_scripts_dir = str(Path(__file__).resolve().parent.parent / "scripts")
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

from core import run_delivery_summary as rds  # noqa: E402
from pipeline_validator import STAGE_TEMPLATES, validate_artifact_data  # noqa: E402


# ── bug 1: resume-class acks ────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "resume", "Resume.", "RESUME", "resume please", "please continue", "continue please",
    "keep going", "go ahead", "carry on", "继续做", "接着做", "请继续", "接着来",
])
def test_resume_class_words_are_acks(text):
    assert rds._is_ack(text) is True


@pytest.mark.parametrize("text", [
    "resume the upload feature", "go ahead with the migration", "keep going on the export bug",
    "修复登录bug",
])
def test_requests_starting_with_ack_words_are_not_acks(text):
    assert rds._is_ack(text) is False


@pytest.fixture
def shanghai_tz(monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def _msg_db(path: Path, rows):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE messages (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, role TEXT NOT NULL,"
                 " content TEXT NOT NULL, created_at TEXT NOT NULL, sent INTEGER)")
    for i, (text, created) in enumerate(rows):
        conn.execute("INSERT INTO messages VALUES (?,?,?,?,?,?)",
                     (str(i), "s1", "user",
                      json.dumps([{"type": "text", "text": text}], ensure_ascii=False), created, 1))
    conn.commit()
    conn.close()
    return path


def test_capture_intent_skips_trailing_resume(tmp_path, shanghai_tz):
    real = "起pipeline 让 PLAN 阶段直接拒绝目录引用"
    db = _msg_db(tmp_path / "m.db", [
        (real, "2026-10-17T16:50:00"),
        ("resume", "2026-10-17T16:58:00"),
        ("Resume.", "2026-10-17T16:59:00"),
    ])
    before = "2026-10-17T09:02:48+00:00"  # 17:02:48 local
    assert rds.capture_intent("s1", before, db_path=db) == (real, "user_message")


# ── bug 2: DELIVER outcome shape ────────────────────────────────────────────

def _deliver(**overrides):
    data = copy.deepcopy(STAGE_TEMPLATES["deliver"])
    data.update(overrides)
    return data


def _outcome_errors(data, tmp_path):
    errs = validate_artifact_data("deliver", data, profile="full", repo_root=str(tmp_path))
    return [e for e in errs if "outcome" in e]


@pytest.mark.parametrize("bad", [
    "a plain string outcome",
    ["problem", "result"],
    {"problem_before": 1, "result_now": "ok", "approach": ["x"]},
    {"problem_before": "was broken", "result_now": None, "approach": ["x"]},
    {"problem_before": "was broken", "result_now": "works", "approach": "one move"},
    {"problem_before": "was broken", "result_now": "works", "approach": ["x", 2]},
])
def test_malformed_outcome_is_rejected(bad, tmp_path):
    errs = _outcome_errors(_deliver(outcome=bad), tmp_path)
    assert errs, f"malformed outcome passed: {bad!r}"
    assert any("problem_before" in e or "approach" in e or "result_now" in e for e in errs)


def test_absent_outcome_still_passes(tmp_path):
    data = _deliver()
    data.pop("outcome", None)
    assert _outcome_errors(data, tmp_path) == []


def test_well_formed_outcome_passes(tmp_path):
    good = {"problem_before": "the summary showed 未记录", "result_now": "it shows the outcome",
            "approach": ["checked the shape at DELIVER"]}
    assert _outcome_errors(_deliver(outcome=good), tmp_path) == []


def test_extra_outcome_keys_are_allowed(tmp_path):
    good = {"problem_before": "a", "result_now": "b", "approach": [], "notes": "extra"}
    assert _outcome_errors(_deliver(outcome=good), tmp_path) == []


def test_deliver_template_carries_an_instructional_outcome(tmp_path):
    out = STAGE_TEMPLATES["deliver"].get("outcome")
    assert isinstance(out, dict)
    assert isinstance(out.get("problem_before"), str) and isinstance(out.get("result_now"), str)
    assert isinstance(out.get("approach"), list) and out["approach"]
    # Copied verbatim, the text must read as an unfilled instruction, never as a result.
    assert "in plain words" in out["problem_before"]
    assert validate_artifact_data("deliver", copy.deepcopy(STAGE_TEMPLATES["deliver"]),
                                  profile="full", repo_root=str(tmp_path)) == []


def test_contract_doc_states_the_rejection():
    doc = (Path(__file__).resolve().parent.parent / "skills" / "s_autonomous-pipeline"
           / "stages" / "ref" / "deliver" / "report-and-commit.md").read_text(encoding="utf-8")
    assert "rejected at DELIVER" in doc


def test_an_outcome_the_validator_accepts_is_one_the_renderer_reads(tmp_path):
    """Validator/renderer parity: the shape the DELIVER check accepts must be the shape
    review_summary reads, or a key rename on one side reopens the silent 未记录."""
    good = {"problem_before": "the summary showed 未记录", "result_now": "it shows the outcome",
            "approach": ["checked the shape at DELIVER"]}
    assert _outcome_errors(_deliver(outcome=good), tmp_path) == []
    parts = rds.review_summary({}, {"outcome": good})
    assert parts["solved"] == (good["problem_before"], good["result_now"])
    assert parts["approach"] == good["approach"]
