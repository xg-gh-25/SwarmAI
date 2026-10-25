"""Drift-detection tests for the review prompt templates.

Pins the scope-budget contract (one authority, read by the orchestrator at spawn
time) so template wording drift turns this RED instead of leaving a sub-agent with
an unbounded task. (The adversarial-classifier contract this file first pinned went
away with the adversarial commit gate, design 2026-10-24-swarmai-usable-by-anyone.)
"""
import re
from pathlib import Path

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
    _SKILL_ROOT / "data" / "gate-prompts" / "gate0-skeptic.md",
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
    _EXPERTS_ROOT / "code" / "ux.md",
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
