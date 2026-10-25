"""The pipeline does not tell the agent how to write or test (design
docs/design/2026-10-26-pipeline-two-ends.md §0, §3.3: "中段不自己造").

What is tested: the stage cards and every `why` text in core/run_protocol/next_step.py
name inputs and exits only. A phrase that prescribes how to write, in what order, or
how to fix is a hit. KNOWN lists today's hits (file, phrase); it may only shrink and
ends empty (§8 step 2). A new hit fails.
"""
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
STAGES = BACKEND / "skills" / "s_autonomous-pipeline" / "stages"
NEXT_STEP = BACKEND / "core" / "run_protocol" / "next_step.py"

# how-to-write phrases (case-insensitive)
PHRASES = {
    "dependency order": r"dependency order",
    "fail first": r"(see it fail|failing test first|fail before the fix|write the failing test)",
    "fix in one pass": r"(in one pass|fix once)",
    "cluster yourself": r"cluster findings",
    "tdd": r"\bTDD\b",
    "bounded cycle": r"bounded goal cycle",
}

KNOWN: set[tuple[str, str]] = set()   # emptied in step 2 and step 3 (goal_cycle.md removed)


def _texts() -> dict[str, str]:
    out = {p.name: p.read_text(encoding="utf-8") for p in sorted(STAGES.glob("*.md"))}
    out[NEXT_STEP.name] = NEXT_STEP.read_text(encoding="utf-8")
    return out


def test_cards_and_next_name_inputs_and_exits_only():
    hits = {(name, label) for name, text in _texts().items()
            for label, pat in PHRASES.items() if re.search(pat, text, re.I)}
    assert hits == KNOWN
