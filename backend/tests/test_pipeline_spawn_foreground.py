"""Pipeline sub-agent spawns are foreground, and REVIEW does not re-run Gate-2's security lens.

WHAT IS TESTED
- Every pipeline doc that instructs a sub-agent spawn (an "Agent tool" / "spawn …
  sub-agent" sentence) also carries the literal directive ``run_in_background: false``.
  The Agent tool defaults to background; a background spawn ends the parent's visible
  stream, so the user sees nothing running and no status. The directive has to sit in
  each doc that spawns, because each stage reads only its own card.
- The detector is a discovery sweep over INSTRUCTIONS.md + stages/*.md, so a doc that
  starts spawning later goes RED without editing this test.
- review.md's fan-out no longer lists a standing Security & Safety Agent (the security
  owner is computed by stage-start), and its spawn instructions require the
  ``EXPERT-ID:`` anchor line the profiler and D7 read.

METHODOLOGY: reads the real skill docs from backend/skills (the source of truth).
"""
import re
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent / "skills" / "s_autonomous-pipeline"
SPAWN_RE = re.compile(
    r"Agent tool|Agent\(\{|[Ss]pawn (?:a |ONE |one |the )?(?:fresh[- ]context )?sub-agent")
DIRECTIVE = "run_in_background: false"
DOCS = [SKILL / "INSTRUCTIONS.md", *sorted((SKILL / "stages").glob("*.md"))]


def _spawning_docs():
    return [p for p in DOCS if SPAWN_RE.search(p.read_text(encoding="utf-8"))]


def test_sweep_finds_the_known_spawning_docs():
    names = {p.name for p in _spawning_docs()}
    # guards the detector itself: if it stops matching, the per-doc test passes vacuously
    assert {"INSTRUCTIONS.md", "evaluate.md", "build.md", "review.md", "deliver.md",
            "goal_cycle.md"} <= names


@pytest.mark.parametrize("doc", _spawning_docs(), ids=lambda p: p.name)
def test_every_spawning_doc_requires_foreground(doc):
    assert DIRECTIVE in doc.read_text(encoding="utf-8"), (
        f"{doc.name} instructs a sub-agent spawn but never says `{DIRECTIVE}` — the Agent "
        "tool defaults to background, which ends the visible chat stream")


def test_review_fanout_has_no_standing_security_agent():
    text = (SKILL / "stages" / "review.md").read_text(encoding="utf-8")
    fanout = text[text.index("## Parallel Fan-Out Review"):text.index("### Phase B")]
    assert "Security & Safety Agent (backend/experts/code/security.md)" not in fanout
    assert "review_experts" in fanout  # the list comes from stage-start, not from prose
    assert "EXPERT-ID:" in fanout
