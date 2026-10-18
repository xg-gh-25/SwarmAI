"""The DELIVER Meta-Review prompt is emitted by code, like gate0/gate1/gate2.

Design §4-C1 moved the ~4.7K inline Meta-Review prompt out of
``stages/deliver.md`` into ``data/gate-prompts/meta-review.md``. ``spawn_prompt.py``
registers it as gate ``meta-review`` so it passes the same contract as the other gates:
numeric SCOPE BUDGET (exit 2 without one), project-knowledge slot (exit 3 without
``--project``), stdout empty on every refusal.

Methodology: drive the real emitter as a subprocess (the exit code is half the
contract) and assert against the prompt FILE on disk, never against the emitter's own
output, so the emitter cannot pass by agreeing with itself.

Properties:
- the gate id is registered and the file exists;
- without ``--project`` it refuses (exit 3, empty stdout);
- with ``--project`` it emits exit 0, carries a numeric cap, and changes only the
  filled placeholders;
- the six analysis questions and the verdict format survived the move;
- the DELIVER card points at the emitter and no longer carries the prompt body.
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_SKILL = Path(__file__).resolve().parents[1] / "skills" / "s_autonomous-pipeline"
_SCRIPTS = _SKILL / "scripts"
_PROMPT = _SKILL / "data" / "gate-prompts" / "meta-review.md"
_CARD = _SKILL / "stages" / "deliver.md"


def _spawn_budget():
    spec = importlib.util.spec_from_file_location("spawn_budget", _SCRIPTS / "spawn_budget.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _emit(*args):
    proc = subprocess.run([sys.executable, str(_SCRIPTS / "spawn_prompt.py"), *args],
                          capture_output=True, text=True, timeout=60)
    return proc.returncode, proc.stdout, proc.stderr


def test_meta_review_is_a_registered_gate():
    rc, out, err = _emit("--gate", "no-such-gate")
    assert rc == 1 and out == ""
    assert "meta-review" in err, f"meta-review missing from the known gates: {err!r}"
    assert _PROMPT.is_file()


def test_refuses_without_project():
    rc, out, err = _emit("--gate", "meta-review")
    assert rc == 3, f"expected exit 3 (knowledge slot needs --project), got {rc}: {err!r}"
    assert out == "", "stdout must be EMPTY on a refusal"


def test_emits_bounded_prompt_with_only_placeholders_filled():
    sb = _spawn_budget()
    raw = _PROMPT.read_text(encoding="utf-8")
    rc, out, err = _emit("--gate", "meta-review", "--project", "NoSuchProject",
                         "--files", "backend/core/a.py", "backend/hooks/b.py")
    assert rc == 0, err
    assert sb.has_numeric_cap(out), "emitted meta-review prompt lost its numeric cap"
    assert out.startswith("EXPERT-ID: meta-review\n\n")  # attribution line, then the file
    out = out[len("EXPERT-ID: meta-review\n\n"):]
    assert out.startswith(raw[: raw.index("<PROJECT>")])
    tail = raw[raw.index(sb.KNOWLEDGE_SLOT) + len(sb.KNOWLEDGE_SLOT):]
    assert out.endswith(tail), "text after the knowledge slot was altered"
    assert "Project: NoSuchProject" in out
    assert "- backend/core/a.py" in out and "- backend/hooks/b.py" in out


@pytest.mark.parametrize("section", [
    "1. DEPLOYMENT CONTEXT", "2. OPERATIONAL SCALING", "3. CROSS-BOUNDARY FORMAT",
    "4. FIRST-RUN vs STEADY-STATE", "5. ARCHITECTURAL INTEGRITY (No-Patch Gate)",
    "6. CROSS-FIX INTERACTION", '"verdict": "CLEAR" | "RISKS_IDENTIFIED"',
])
def test_analysis_sections_survived_the_move(section):
    assert section in _PROMPT.read_text(encoding="utf-8")


def test_card_points_at_emitter_and_drops_the_body():
    card = _CARD.read_text(encoding="utf-8")
    assert "spawn_prompt.py --gate meta-review" in card
    assert "You are NOT reviewing the code for bugs" not in card, (
        "the Meta-Review prompt body is back in stages/deliver.md; it belongs in "
        "data/gate-prompts/meta-review.md")
