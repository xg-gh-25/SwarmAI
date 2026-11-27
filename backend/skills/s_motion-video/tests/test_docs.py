"""The workflow and the contracts describe the film layer, the scene-writing rules, the writer brief and how to
make a new cut; the skill corpus passes the repository's skill linter.
Run: python3 -m pytest tests/test_docs.py -q   (from the skill directory)."""
import os
import re
import subprocess
import sys

import pytest

from mv_fixtures import SKILL

REPO = os.path.dirname(os.path.dirname(os.path.dirname(SKILL)))


def text(*rel):
    return open(os.path.join(SKILL, *rel), encoding="utf-8").read()


def section(doc, heading):
    m = re.search(r"^## " + re.escape(heading) + r".*?$(.*?)(?=^## |\Z)", doc, re.M | re.S)
    assert m, f"missing section '## {heading}'"
    return m.group(1)


def test_docs_describe_layer_rules_brief_and_revision():
    sc = text("references", "scene-contract.md")
    layer = section(sc, "The film layer")
    for need in ("film.json", "film.py", "FL.setup(tl, opts)", "after every `tts.py`", "film-data.js", "--check",
                 "plate", "mask", "vignette", "kicker", "progress", "push", "the style always wins"):
        assert need in layer, need
    rules = section(sc, "Writing rules")
    for need in ("Source wording exact", "words.json", "first match", "first 0.4 s", "by 0.3 s", "Text hold",
                 "/ 4.5", "/ 15", "1.2 s", "One accent", "safe area", "30u", "36u", "Ghost, then ink",
                 "Neighbours differ", "Own files only", "at most\n  3 rounds"):
        assert need in rules, need
    assert "/skill/runtime/film-layer.js" in sc and "/project/scenes/film-data.js" in sc

    brief = text("templates", "scene-writer-brief.md")
    for need in ("Read first", "scene-contract.md", "project-contract.md", "design.md", "film.json",
                 "example scenes", "words.json", "check.py", "--preview", "At most 3 rounds", "## Report",
                 "One line per scene", "Never"):
        assert need in brief, need
    assert "Writing rules" in brief and "Text hold" not in brief      # it points to the contract, never repeats it

    ins = text("INSTRUCTIONS.md")
    assert "~/.swarm-ai/SwarmWS" not in ins
    assert ins.count("${SWARM_WORKSPACE:-$HOME/.swarm-ai/SwarmWS}") >= 3
    assert "film.py" in section(ins, "4. Voice first") and "FL.setup" in section(ins, "5. Sample scene")
    assert "scene-writer-brief.md" in section(ins, "6. All other scenes")
    rev = section(ins, "Revise or extend a film")
    for need in ("Never shorten the person's material silently", "ask before", "plan.json", "derive.py\" --from",
                 "--derived-from", "tts.py", "film.py", "derive.py --carry", "check.py", "render.py"):
        assert need in rev, need
    order = [rev.index(x) for x in ("plan.json", "derive.py\" --from", "--derived-from", "tts.py", "derive.py --carry",
                                    "Write the new scenes")]
    assert order == sorted(order), "the order of work"


def test_lint_skills_passes():
    pytest.importorskip("yaml", reason="the skill linter runs in the backend environment (it parses SKILL.md with yaml)")
    script = os.path.join(REPO, "scripts", "lint_skills.py")
    r = subprocess.run([sys.executable, script], cwd=REPO, capture_output=True, text=True, timeout=90)
    assert r.returncode == 0, (r.stdout + r.stderr)[-2000:]
