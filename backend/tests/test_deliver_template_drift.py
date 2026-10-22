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
import re
from pathlib import Path

from core.runtime_hooks import _is_adversarial_intent

_SKILL = Path(__file__).resolve().parents[1] / "skills" / "s_autonomous-pipeline"


def _spawn_budget():
    """Load ``scripts/spawn_budget.py``, the sole owner of the cap pattern.

    Imported by path because the package directory (``s_autonomous-pipeline``)
    contains a hyphen and cannot appear in a dotted import. This module used to
    carry its own copy of the regex; two regexes for one contract drift apart
    silently, so the emitter and this test now read the same definition.
    """
    import importlib.util

    path = _SKILL / "scripts" / "spawn_budget.py"
    spec = importlib.util.spec_from_file_location("spawn_budget", path)
    assert spec and spec.loader, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

#: The Gate-2 specialist prompt. It moved out of ``stages/deliver.md`` into its own
#: whole-file prompt so that the orchestrator emits it (``scripts/spawn_prompt.py``)
#: instead of retyping a fenced block. This module follows the content, not the
#: old location — R27: a reader of a moved contract must be migrated with it.
_DELIVER_MD = _SKILL / "data" / "gate-prompts" / "gate2-adversarial.md"


def _extract_specialist_template_head(text: str) -> str:
    """Return the HEAD of the specialist prompt — the leading instruction block
    BEFORE the first '## ' section (## Context, ## Checklist, etc.).

    This head is what LEADS every spawned specialist prompt and is the part that
    must carry the adversarial signal; matching incidental prose deeper in the
    body (e.g. the 'Restraint' section mentioning 'adversarial found 5') would be
    test-theater (RP47) — a match that does not reflect the spawn-prompt lead.

    ``text`` is now the WHOLE gate-prompt file rather than a fenced block carved
    out of ``deliver.md``. No fence is located because none encloses the prompt
    any more: the Gate-1 prompt contains a nested ``` block, and a
    find-the-next-fence extractor closed on it and silently dropped the tail.
    """
    head_end = text.find("\n## ")
    return text if head_end == -1 else text[:head_end]


def test_specialist_template_is_classified_adversarial():
    """The shipped specialist prompt template must carry an adversarial-review
    signal `_is_adversarial_intent` recognizes — else the pipeline's own reviewers
    never emit the _adv_ marker and the commit gate blocks the pipeline's own work."""
    head = _extract_specialist_template_head(_DELIVER_MD.read_text())
    assert _is_adversarial_intent("", "", head) is True, (
        "data/gate-prompts/gate2-adversarial.md no longer matches "
        "_is_adversarial_intent — the pipeline's canonical adversarial review would "
        "stop emitting the _adv_ marker and block its own commits. Restore an "
        "adversarial phrase (e.g. 'Adversarially review this changeset — hunt for "
        "bugs, regressions and security issues in this diff.') to the prompt head."
    )


# ---------------------------------------------------------------------------
# Scope-budget contract — ONE authority, not one copy per template
# ---------------------------------------------------------------------------
# WHY a budget at all: an unbounded task framing ("prove no problem exists")
# has no termination condition, so the sub-agent searches until it gives up.
# Bounding SCOPE terminates by construction; bounding the CLOCK would truncate
# a live review that is still finding real issues (STEERING #2).
#
# WHY the authority is the CALLER side: review/adversarial spawn prompts are
# written by the orchestrator at spawn time, not copied from these template
# files — so the constraint has to live where the ORCHESTRATOR reads it. An
# earlier version of this module asserted the marker in each of a hardcoded
# 7-file list, which turned the duplication into a contract: consolidating to a
# single authority went RED until this test was retargeted. Guard the authority
# and the per-spawn parameters, never the number of copies.
_SKILL_ROOT = Path(__file__).resolve().parents[1] / "skills" / "s_autonomous-pipeline"

_BUDGET_MARKER = "SCOPE BUDGET"

# The prompts that reach a sub-agent VERBATIM, so their concrete numbers are what
# actually bound it. These are now whole files under data/gate-prompts/ rather than
# fenced blocks inside the stage docs: the Gate-1 prompt contains a nested ``` block,
# so any find-the-next-fence extraction truncated it — dropping the mandatory
# output-format spec while the caps survived, which left the caps-based check green.
# A whole file has no fence to mis-pair.
_IN_FENCE_TEMPLATES = (
    _SKILL_ROOT / "data" / "gate-prompts" / "gate2-adversarial.md",
    _SKILL_ROOT / "data" / "gate-prompts" / "gate0-skeptic.md",
    _SKILL_ROOT / "data" / "gate-prompts" / "gate1-skeptic.md",
)

# Files that are referenced by PATH only (never pasted). They must not carry a
# second, differently-worded copy of the contract.
#
# R1 Expert Library migration: the 12 code experts moved out of
# `s_autonomous-pipeline/{stages/specialists,review-agents}/` into the top-level
# `backend/experts/code/` asset, and `security-safety.md` merged INTO `security.md`.
# So these path-referenced templates now live under _EXPERTS_ROOT, and the
# security-safety entry is gone (its content is union-merged into security.md,
# which is NOT a contract-restating template and need not be listed).
_EXPERTS_ROOT = Path(__file__).resolve().parents[1] / "experts"

_PATH_REFERENCED_TEMPLATES = (
    _EXPERTS_ROOT / "code" / "security.md",
    _EXPERTS_ROOT / "code" / "code-quality.md",
    _EXPERTS_ROOT / "code" / "ux-test.md",
    _EXPERTS_ROOT / "code" / "red-team.md",
)




def test_in_fence_templates_state_concrete_caps():
    """Fenced templates are pasted verbatim, so they must carry real numbers.

    Guard the INTENT (a numeric ceiling on files or tool calls), never one
    phrasing. REVIEW caught this: the three templates already use four different
    wordings ("At most 14 tool", "at most 4 files", "at most 8 tool", "read at
    most 4 files"), so a literal `at most N` check passed only by luck — and a
    rewording to "up to 4 files" or "maximum 4 files" would have sailed through
    while the cap silently vanished. One contract, many wordings is exactly the
    drift class this module exists to stop.
    """
    caps_re = _spawn_budget().CAPS_RE
    for path in _IN_FENCE_TEMPLATES:
        text = path.read_text()
        idx = text.find(_BUDGET_MARKER)
        assert idx != -1, (
            f"{path.name} is pasted into a spawn prompt but states no "
            f"{_BUDGET_MARKER!r} — the sub-agent gets no termination condition."
        )
        # Scope the cap search to the budget PARAGRAPH. A whole-file scan is
        # vacuous: unrelated prose elsewhere in the same document (e.g. an
        # "≤1 file" scope note) satisfies the regex, so removing the budget's own
        # numbers stayed GREEN. Self-caught by mutation, not by reading.
        para_end = text.find("\n\n", idx)
        budget_para = text[idx: para_end if para_end != -1 else idx + 800]
        assert caps_re.search(budget_para), (
            f"{path.name} states {_BUDGET_MARKER!r} with no concrete cap in that "
            "same paragraph. A fenced template reaches the sub-agent verbatim, so "
            f"the numbers must be there. Paragraph was: {budget_para[:160]!r}"
        )


def test_contract_is_not_restated_in_path_referenced_templates():
    """No second, differently-worded copy of the contract (single authority)."""
    duplicated = [
        str(p.relative_to(_SKILL_ROOT))
        for p in _PATH_REFERENCED_TEMPLATES
        if "authorize skipping" in p.read_text()
        or "never the checklist" in p.read_text()
    ]
    assert not duplicated, (
        f"{duplicated} restate the bounded-spawn contract. These files are "
        "referenced by path and are not pasted into spawn prompts, so a copy here "
        "adds no constraint and guarantees two-source drift (already caught once). "
        "Point at the caller-side rule in INSTRUCTIONS.md instead."
    )


def test_no_volatile_measured_values_in_skill_text():
    """Product text must not embed values that expire (a model name, a corpus
    size, a run id, a percentage delta). Store the durable claim; the numbers
    belong in the pipeline run record, which is dated and reproducible."""
    banned = re.compile(
        r"\b(?:sonnet|opus|haiku|fable)-[\d.-]+\b"      # model names with a version
        r"|\bclaude-(?:opus|sonnet|haiku)-\d"
        r"|\b5537\b|\b27\.8h\b|\b10\.8%|\+0\.07",
        re.I,
    )
    offenders: list[str] = []
    # R1 Expert Library migration: the experts moved OUT of _SKILL_ROOT into
    # backend/experts/, so a sweep of _SKILL_ROOT alone would silently STOP covering
    # them (THINK falsified the "repoint-only" assumption). Scan BOTH trees so the
    # moved experts keep the volatile-value guard.
    scan_roots = [_SKILL_ROOT, _EXPERTS_ROOT]
    for root in scan_roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.md")):
            for i, line in enumerate(path.read_text().splitlines(), 1):
                if banned.search(line):
                    offenders.append(f"{path.relative_to(root)}:{i}: {line.strip()[:70]}")
    assert not offenders, (
        "skill text embeds values that will expire:\n  " + "\n  ".join(offenders) +
        "\nReplace each with a claim that stays true across a model or corpus "
        "change; keep the measurement in the run record."
    )


def test_adversarial_phrase_stays_inside_the_prompt_head_window():
    """The intent detector reads only the first 2000 chars of the spawn prompt.

    `runtime_hooks._read_subagent_prompt_head` truncates at `content[:2000]`, and
    `_is_adversarial_intent` runs on that slice. So the real constraint is a
    CHARACTER WINDOW, not the order of two lines: if the adversarial phrasing
    drifts past 2000 chars the `_adv_` marker stops being written and the
    pipeline silently blocks its own commits.
    """
    head = _extract_specialist_template_head(_DELIVER_MD.read_text())
    idx = head.lower().find("adversarially review")
    assert idx != -1, "template head lost its 'Adversarially review' phrasing"
    assert idx < 2000, (
        f"'Adversarially review' sits at char {idx} of the specialist template, "
        "past the 2000-char head the intent detector reads "
        "(runtime_hooks._read_subagent_prompt_head). Keep it near the top."
    )
    assert _BUDGET_MARKER in head, (
        f"{_BUDGET_MARKER} must appear in the specialist template HEAD (before the "
        "first '## ' section), so the spawned sub-agent actually reads it."
    )
