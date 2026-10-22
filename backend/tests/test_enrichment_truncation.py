"""Tests for the enrichment-truncation fix in core/summarization.py.

The enrichment JSON was cut at max_tokens=512 and, having no closing brace,
was dropped without a log line. Properties checked:
- a cut response keeps every field that completed (salvage), never a guess;
- an unfinished last string / array element is dropped;
- a complete response parses unchanged;
- nothing usable -> {} WITH a warning;
- the output budget and read timeout fit a full-length generation and the
  180 s hook budget.
"""

import json
import logging

from core import summarization as S
from core.summarization import SummarizationPipeline, _salvage_truncated_json

FULL = {
    "deliverables": ["修复召回截断", "Fixed Y"],
    "key_outputs": ["code: a.py → b"],
    "lessons": ["先量再改", "Never trust char counts"],
    "lesson_types": ["guideline", "pitfall"],
    "rejected_approaches": [],
    "corrections": [],
    "process_reflection": "",
    "signal_driven_actions": [],
    "continue_from": "next",
    "validation_status": "tests green",
    "coe_signal": "",
    "coe_topic": "",
}


def _cut(at: str) -> str:
    text = json.dumps(FULL, ensure_ascii=False, indent=2)
    return text[: text.index(at)]


def test_cut_inside_a_later_field_keeps_the_complete_ones():
    data = _salvage_truncated_json(_cut('"rejected_approaches"'))
    assert data["deliverables"] == FULL["deliverables"]
    assert data["lessons"] == FULL["lessons"]
    assert data["lesson_types"] == FULL["lesson_types"]
    assert "rejected_approaches" not in data


def test_cut_inside_a_string_drops_only_the_unfinished_element():
    text = json.dumps(FULL, ensure_ascii=False)
    cut = text[: text.index("Never trust") + 5]  # inside the 2nd lesson
    data = _salvage_truncated_json(cut)
    assert data["deliverables"] == FULL["deliverables"]
    assert data["lessons"] == ["先量再改"]


def test_parse_enrichment_uses_salvage_and_logs(caplog):
    caplog.set_level(logging.WARNING)
    out = SummarizationPipeline._parse_enrichment(_cut('"continue_from"'))
    assert out["deliverables"] == FULL["deliverables"]
    assert out["lessons"] == FULL["lessons"]
    assert any("was cut" in r.message for r in caplog.records)


def test_complete_json_parses_unchanged():
    out = SummarizationPipeline._parse_enrichment(json.dumps(FULL, ensure_ascii=False))
    assert out["lessons"] == FULL["lessons"] and out["continue_from"] == "next"


def test_nothing_usable_is_logged_not_silent(caplog):
    caplog.set_level(logging.WARNING)
    assert SummarizationPipeline._parse_enrichment('{"deliverables": ["unfinish') == {}
    assert SummarizationPipeline._parse_enrichment("no json here") == {}
    assert len([r for r in caplog.records if "nrichment" in r.message]) == 2


def test_budget_fits_a_full_generation():
    assert S._ENRICHMENT_MAX_TOKENS >= 2048
    # invoke_model is not streamed: the read waits for the whole output.
    assert 60 <= S._ENRICHMENT_READ_TIMEOUT_S < 180
