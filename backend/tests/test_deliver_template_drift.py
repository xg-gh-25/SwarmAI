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
_INSTRUCTIONS_MD = _SKILL_ROOT / "INSTRUCTIONS.md"

# The three rules the caller-side authority must state. Each entry is
# (label, regex) — whitespace-insensitive because the source is prose wrapped at
# ~78 cols, so a newline can fall anywhere inside a phrase.
_AUTHORITY_RULES = (
    ("finite item list", r"[Ff]inite\s+item\s+list"),
    ("cap on item count", r"N\s*(?:<=|\u2264)\s*3-4"),
    ("never prove a negative", r"prove\s+a\s+negative"),
    ("do not delegate your grep", r"delegate\s+your\s+own\s+grep"),
    ("state the scope budget", r"State\s+the\s+scope\s+budget"),
    ("bound scope not the clock", r"[Bb]ound\s+SCOPE,\s+never\s+the\s+clock"),
    ("N/A wording", r"`N/A:"),
    ("UNCHECKED wording", r"UNCHECKED"),
    ("budget never authorizes skipping", r"[Nn]ever\s+let\s+the\s+budget\s+authorize\s+skipping"),
)

# Templates whose budget text is INSIDE a fenced code block — i.e. literally
# pasted into a spawn prompt, so their concrete numbers do reach the sub-agent.
_IN_FENCE_TEMPLATES = (
    _SKILL_ROOT / "stages" / "deliver.md",
    _SKILL_ROOT / "stages" / "evaluate.md",
    _SKILL_ROOT / "stages" / "build.md",
)

# Files that are referenced by PATH only (never pasted). They must not carry a
# second, differently-worded copy of the contract.
_PATH_REFERENCED_TEMPLATES = (
    _SKILL_ROOT / "review-agents" / "security-safety.md",
    _SKILL_ROOT / "review-agents" / "code-quality.md",
    _SKILL_ROOT / "review-agents" / "ux-test.md",
    _SKILL_ROOT / "stages" / "specialists" / "red-team.md",
)


def test_caller_side_authority_states_every_rule():
    """INSTRUCTIONS.md is the single authority for the bounded-spawn contract."""
    text = _INSTRUCTIONS_MD.read_text()
    start = text.find("[MUST] Every spawn prompt is BOUNDED")
    assert start != -1, (
        "INSTRUCTIONS.md lost the caller-side bounded-spawn rule. This is the ONLY "
        "layer the orchestrator reliably reads when it hand-writes a spawn prompt; "
        "without it nothing bounds the prompts that actually run."
    )
    end = text.find("**Spawn REJECTION", start)
    block = text[start: end if end != -1 else start + 4000]
    missing = [label for label, pat in _AUTHORITY_RULES
               if not re.search(pat, block, re.S)]
    assert not missing, (
        f"the caller-side authority no longer states: {missing}. Restore it in "
        "INSTRUCTIONS.md — a rule that lives only in a template file does not "
        "reach a hand-written spawn prompt."
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
    caps_re = re.compile(
        r"(?:at\s+most|no\s+more\s+than|up\s+to|max(?:imum)?(?:\s+of)?|limit(?:ed)?\s+to|"
        r"\u2264|<=)\s*(\d+)\s*(?:more\s+)?(?:files?|tool)",
        re.I | re.S,
    )
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
    for path in sorted(_SKILL_ROOT.rglob("*.md")):
        for i, line in enumerate(path.read_text().splitlines(), 1):
            if banned.search(line):
                offenders.append(f"{path.relative_to(_SKILL_ROOT)}:{i}: {line.strip()[:70]}")
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


def test_no_hardcoded_model_directive_in_spawn_config():
    """deliver.md must not hardcode a model for review sub-agents.

    The retired line read "Use default model (opus) — adversarial review needs
    strongest reasoning". Measured across the recorded sub-agent corpus that
    premise did not hold, and model identity was confounded with era and diff
    size — so the evidence licensed DELETING the unsupported claim, not naming a
    replacement, which would decay identically at the next default change.
    Review quality comes from bounded scope plus fresh context, not a model name.
    """
    text = _DELIVER_MD.read_text()
    assert "default model (opus)" not in text, (
        "deliver.md re-introduced the exact retired model directive. Sub-agent "
        "review quality is governed by bounded scope + fresh context, not by "
        "pinning a model name that silently decays when the session default moves."
    )
    # Guard the INTENT, not just the one retired wording (REVIEW finding, CHECK 2):
    # a reworded 'Use model X' / 'default model (sonnet)' would sail past a literal
    # substring check. Scope the scan to the Sub-agent configuration block so the
    # surrounding PROSE explaining WHY the directive was retired (which necessarily
    # names opus and sonnet) does not self-trigger.
    cfg_start = text.find("**Sub-agent configuration:**")
    assert cfg_start != -1, "deliver.md lost its Sub-agent configuration block"
    cfg_end = text.find("\n**", cfg_start + 10)
    cfg_block = text[cfg_start: cfg_end if cfg_end != -1 else len(text)]
    # The negative lookahead is load-bearing (Gate-2 finding): without it a
    # NEGATED bullet — "- Do NOT use model opus" — false-triggers, since the
    # pattern only sees the verb, not its polarity. The current block genuinely
    # contains such a bullet ("Do NOT pin a model"), so this guard must
    # distinguish a prohibition from a directive or it blocks the correct state.
    directive = re.compile(
        r"^\s*[-*]\s+(?![^\n]*\b(?:do not|don't|never|no longer|avoid)\b)"
        r"(?:use|set|pin|prefer)\b[^\n]*\b(opus|sonnet|haiku|fable)\b",
        re.IGNORECASE | re.MULTILINE,
    )
    hit = directive.search(cfg_block)
    assert hit is None, (
        "deliver.md's Sub-agent configuration block pins a model again: "
        f"{hit.group(0).strip()!r}. Omit `model` and inherit the session default; "
        "a hardcoded tier decays at the next default change (the retired directive "
        "named a tier and silently became wrong when the default moved)."
    )
