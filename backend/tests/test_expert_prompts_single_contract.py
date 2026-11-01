"""One output contract (design 2026-11-01-pipeline-delivery-friction D4): an expert
prompt says what to check; the format comes only from the caller (a pipeline handout's
OUTPUT CONTRACT, the native review's reporting line). A prompt that defines its own
format contradicts the handout and its answers fail to parse."""
from pathlib import Path

import pytest

from core import pipeline_expert_agent as pea
from core.run_protocol import review

EXPERTS = sorted((Path(__file__).resolve().parents[1] / "experts").rglob("*.md"))
FORMAT_MARKS = ('"specialist"', '"confidence"', '"fingerprint"', "Output: JSON",
                "```json", "## Output Format", "verdict PARTIAL")


def test_there_are_expert_prompts():
    assert len(EXPERTS) >= 9


@pytest.mark.parametrize("path", EXPERTS, ids=lambda p: p.name)
def test_no_expert_prompt_defines_its_own_output_format(path):
    text = path.read_text(encoding="utf-8")
    assert [mark for mark in FORMAT_MARKS if mark in text] == []


def test_the_pipeline_handout_carries_the_engine_contract():
    prompt = review.build_prompt("correctness", (EXPERTS[0]).read_text(), "diff")
    assert review.OUTPUT_CONTRACT in prompt
    assert review.lint_prompt(prompt, expert_id="correctness", round=1) == []


def test_the_expert_system_prompt_and_budget_message_ask_no_verdict():
    assert "VERDICT" not in pea._SYSTEM_PROMPT.upper().replace("VERDICT LINE", "")
