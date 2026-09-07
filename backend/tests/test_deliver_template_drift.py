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
# Scope-budget contract (run_90eb848b)
# ---------------------------------------------------------------------------
# Every Agent-spawn prompt template must state an explicit SCOPE budget.
#
# WHY a budget at all: measured over 5537 recorded subagent transcripts, a review
# sub-agent's duration and its severe-finding count rise TOGETHER (0.42 findings
# under 1min → 1.87 at 3-6min), but the marginal return COLLAPSES past ~6 minutes
# (+0.07 going from 6-10min to >10min) while 27.8h = 10.8% of all subagent
# wall-clock sits past that knee. The waste comes from UNBOUNDED task framing —
# "prove no problem exists" has no termination condition, so the agent searches
# until it gives up.
#
# WHY scope and NOT a timeout: a wall-clock kill would truncate a live review that
# is still finding real issues (STEERING #2 — a control that truncates
# in-progress real work is banned), and the correlation above shows these runs are
# progressing, not hung. Bounding SCOPE terminates by construction while leaving
# the productive 3-6min band intact.
#
# WHY a test and not just prose: the prose lived in these very templates and did
# not hold. A missing budget line is invisible at review time and only surfaces as
# a 30-minute stall, so the contract needs a RED, not a reminder.
_SKILL_ROOT = Path(__file__).resolve().parents[1] / "skills" / "s_autonomous-pipeline"

_BUDGET_MARKER = "SCOPE BUDGET"

_SPAWN_TEMPLATES = (
    _SKILL_ROOT / "review-agents" / "security-safety.md",
    _SKILL_ROOT / "review-agents" / "code-quality.md",
    _SKILL_ROOT / "review-agents" / "ux-test.md",
    _SKILL_ROOT / "stages" / "specialists" / "red-team.md",
    _SKILL_ROOT / "stages" / "deliver.md",
    _SKILL_ROOT / "stages" / "evaluate.md",
    _SKILL_ROOT / "stages" / "build.md",  # Gate-1 Skeptic + SSA spawn template
)

# A hand-written list is exactly how the 7th template got missed: the first
# version of this test hardcoded 6 paths, and Gate-2 found that stages/build.md
# spawns the Gate-1 Skeptic+SSA sub-agent with no budget at all — invisible to a
# curated list (R27: enumerate the SINK, don't curate the members). So the list
# above is cross-checked against a DISCOVERY sweep below: any file carrying a
# second-person spawn-prompt opening must be either bounded or explicitly
# accounted for here.
_SPAWN_OPENING = re.compile(
    r"^(?:You are (?:a|an|NOT)\b|You receive\b)", re.MULTILINE
)


def test_every_spawn_template_states_a_scope_budget():
    """Every Agent-spawn prompt template must carry the SCOPE BUDGET marker."""
    missing = [
        str(p.relative_to(_SKILL_ROOT))
        for p in _SPAWN_TEMPLATES
        if _BUDGET_MARKER not in p.read_text()
    ]
    assert not missing, (
        f"spawn template(s) lost the {_BUDGET_MARKER!r} line: {missing}. "
        "Without it the sub-agent has no termination condition and reverts to "
        "unbounded search (measured: 27.8h of subagent wall-clock spent past the "
        "6-minute marginal-return knee for +0.07 findings). Restore an explicit "
        "scope budget — files to read / tool calls / answer length."
    )


def test_no_undiscovered_spawn_template_is_unbounded():
    """Discover spawn templates by their prompt shape — don't trust the curated list.

    Gate-2 (run_90eb848b) caught the first version of this module hardcoding 6
    paths while `stages/build.md` spawned a 7th, entirely unbounded sub-agent. A
    curated list inherits the same blind spot that caused the miss, so sweep the
    skill tree for second-person spawn-prompt openings and require each hit to be
    either budgeted or listed above. A NEW spawn template added later goes RED
    here instead of silently running unbounded.
    """
    known = {p.resolve() for p in _SPAWN_TEMPLATES}
    unbounded: list[str] = []
    for path in sorted(_SKILL_ROOT.rglob("*.md")):
        if path.resolve() in known:
            continue
        text = path.read_text()
        if _SPAWN_OPENING.search(text) and _BUDGET_MARKER not in text:
            unbounded.append(str(path.relative_to(_SKILL_ROOT)))
    assert not unbounded, (
        "file(s) carry a spawn-prompt opening ('You are a…' / 'You receive…') but "
        f"no {_BUDGET_MARKER!r} and are absent from _SPAWN_TEMPLATES: {unbounded}. "
        "Either add a scope budget (if it really spawns a sub-agent) or add it to "
        "_SPAWN_TEMPLATES so the omission is a recorded decision, not a blind spot."
    )


def test_budget_line_does_not_displace_the_adversarial_phrase():
    """The budget must sit AFTER the adversarial framing in deliver.md's template.

    Gate-1 CHECK 4 (run_90eb848b): the sibling test above asserts the template HEAD
    carries a phrase `_is_adversarial_intent` recognizes. If the budget line were
    inserted AHEAD of that phrasing it could displace it, silently breaking the
    pipeline's own commit gate. Pin the ORDER, not just the presence.
    """
    head = _extract_specialist_template_head(_DELIVER_MD.read_text())
    assert _BUDGET_MARKER in head, (
        f"{_BUDGET_MARKER} must appear in the specialist template HEAD (before the "
        "first '## ' section), so the spawned sub-agent actually reads it."
    )
    adversarial_idx = head.lower().find("adversarially review")
    budget_idx = head.find(_BUDGET_MARKER)
    assert adversarial_idx != -1, "template head lost its 'Adversarially review' phrasing"
    assert adversarial_idx < budget_idx, (
        "the SCOPE BUDGET line must come AFTER the adversarial framing — placing it "
        "first risks displacing the phrase _is_adversarial_intent matches, which "
        "would block the pipeline's own commits."
    )


def test_specialist_budget_caps_agree_across_templates():
    """deliver.md's spawn template and the specialist files must state the SAME caps.

    Two-source drift (R27): deliver.md carries the template the orchestrator copies
    into the spawn prompt, while each specialist .md carries its own budget. If the
    numbers disagree, the spawned agent is told two different limits depending on
    which document it read — the exact silent-inconsistency class this run exists to
    remove. Caught by REVIEW (CHECK 3) after the first BUILD shipped 3-files/12-calls
    in deliver.md against 4-files/14-calls in the specialists.
    """
    # NOTE: the caps are prose wrapped at ~78 cols, so a newline can fall ANYWHERE
    # inside the phrase ("At most 14\ntool calls"). Match on whitespace-insensitive
    # runs (`\s+`), never a literal space — a space-only pattern silently found no
    # match and made this test look like a template defect (self-caught, first run).
    caps_re = re.compile(
        r"at\s+most\s+(\d+)\s+files.*?at\s+most\s+(\d+)\s+tool\s+calls",
        re.I | re.S,
    )
    specialists = (
        _SKILL_ROOT / "review-agents" / "security-safety.md",
        _SKILL_ROOT / "review-agents" / "code-quality.md",
        _SKILL_ROOT / "review-agents" / "ux-test.md",
        _SKILL_ROOT / "stages" / "specialists" / "red-team.md",
    )
    seen: dict[str, tuple[str, str]] = {}
    for path in (_DELIVER_MD, *specialists):
        m = caps_re.search(path.read_text())
        assert m, f"{path.name} states no 'at most N files / at most N tool calls' caps"
        seen[path.name] = (m.group(1), m.group(2))
    distinct = set(seen.values())
    assert len(distinct) == 1, (
        f"spawn-budget caps disagree across templates: {seen}. The orchestrator "
        "copies deliver.md's template while each specialist file states its own "
        "budget — divergent numbers mean the sub-agent is told two different "
        "limits. Keep them identical."
    )


def test_no_hardcoded_model_directive_in_spawn_config():
    """deliver.md must not hardcode a model for review sub-agents.

    The retired line read "Use default model (opus) — adversarial review needs
    strongest reasoning". Measured per-model over the same 5537 transcripts that
    premise does not hold: claude-sonnet-4-5 returned a 92s median with 2.19 avg
    severe findings, while the then-current session default (claude-opus-5)
    returned 413s median with 1.57. Model identity and era/diff are CONFOUNDED in
    that corpus, so the evidence licenses DELETING the unsupported claim — not
    asserting a new "best" model, which would decay identically at the next
    default change.
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
        "said 'opus' and silently became wrong when the default moved to opus-5)."
    )
