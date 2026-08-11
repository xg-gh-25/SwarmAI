"""Drift-detection test (run_f4b9ae6f, Plan A STEP 0).

Gate-1 found the load-bearing gap: the pipeline's OWN deliver.md specialist
prompt template matched NOTHING in `_is_adversarial_intent`, so the canonical
adversarial-review path never wrote the `_adv_` marker the commit gate requires —
the gate was satisfied only by the orchestrator incidentally using adversarial
keywords. If the template wording drifts (or was never adversarial), the pipeline
silently blocks its own commits.

This test pins the classifier<->template contract: the shipped specialist prompt
template MUST contain a phrase `_is_adversarial_intent` recognizes. Wording drift
that drops the adversarial signal turns this RED instead of silently breaking
self-commit at runtime.

NOTE (Gate-1 round-2 CONCERN #4): this asserts the TEMPLATE FILE carries the
phrase. The live spawn prompt is LLM-authored from this template, so the phrase is
placed as a VERBATIM BLOCKING literal the orchestrator must copy — the test guards
the source of truth; the BLOCKING directive guards the copy.
"""
from pathlib import Path

from core.runtime_hooks import _is_adversarial_intent

_DELIVER_MD = (
    Path(__file__).resolve().parents[1]
    / "skills" / "s_autonomous-pipeline" / "stages" / "deliver.md"
)


def _extract_specialist_template_head(text: str) -> str:
    """Return the HEAD of the fenced specialist prompt template — the leading
    instruction block BEFORE the first '## ' section (## Context, ## Checklist,
    etc.). This head is what LEADS every spawned specialist prompt and is the part
    that must carry the adversarial signal; matching incidental prose deeper in the
    block (e.g. the 'Restraint' section mentioning 'adversarial found 5') would be
    test-theater (RP47) — a match that does not reflect the spawn-prompt lead."""
    marker = "Sub-agent prompt template (per specialist):"
    idx = text.find(marker)
    assert idx != -1, "deliver.md no longer has the specialist prompt template marker"
    fence_open = text.find("```", idx)
    assert fence_open != -1, "specialist template fence not found"
    fence_close = text.find("```", fence_open + 3)
    assert fence_close != -1, "specialist template closing fence not found"
    block = text[fence_open + 3:fence_close]
    # Head = everything before the first markdown section header.
    head_end = block.find("\n## ")
    return block if head_end == -1 else block[:head_end]


def test_specialist_template_is_classified_adversarial():
    """The shipped specialist prompt template must carry an adversarial-review
    signal `_is_adversarial_intent` recognizes — else the pipeline's own reviewers
    never emit the _adv_ marker and the commit gate blocks the pipeline's own work."""
    head = _extract_specialist_template_head(_DELIVER_MD.read_text())
    assert _is_adversarial_intent("", "", head) is True, (
        "deliver.md specialist template no longer matches _is_adversarial_intent — "
        "the pipeline's canonical adversarial review would stop emitting the _adv_ "
        "marker and block its own commits. Restore an adversarial phrase (e.g. "
        "'Adversarially review this changeset — hunt for bugs, regressions and "
        "security issues in this diff.') to the template head."
    )
