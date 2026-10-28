"""The skill linter's DDD path rules (knowledge addressing §2.2, §6.3 test 3).

Prose that names DDD files drifted twice: after the 2026-07-21 renumbering a skill kept
reading ``Projects/<project>/TECH.md`` (gone for every migrated DDD), and skills named
one machine's projects. The linter now flags (1) a pre-numbering DDD path in any
shipped text, by the layout's own rule (``ddd_paths.stale_layout_refs``), and (2) a path
to a concrete project's DDD in a skill.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import lint_skills  # noqa: E402


def _lint(tmp_path, text, rel="backend/skills/s_x/INSTRUCTIONS.md"):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return lint_skills.lint_body(p)


def test_the_old_s_qa_wording_is_red(tmp_path):
    out = _lint(tmp_path, "Read `Projects/<project>/TECH.md`:\nRead `Projects/<project>/IMPROVEMENT.md`:\n")
    assert len(out) == 2 and all("before the numbered layout" in f for f in out)


def test_the_numbered_layout_and_placeholders_pass(tmp_path):
    assert _lint(tmp_path, "write to `Projects/<X>/2-understanding/<doc>` or Projects/<P>/2-understanding/TECH.md\n") == []


def test_a_marked_example_of_the_wrong_path_passes(tmp_path):
    assert _lint(tmp_path, "`Projects/<X>/TECH.md` <!-- layout-example --> creates an orphan\n") == []


def test_a_concrete_project_in_a_skill_is_red_but_not_elsewhere(tmp_path):
    line = "see `Projects/AIDLC/2-understanding/knowledge/x.md`\n"
    assert any("one machine's project" in f for f in _lint(tmp_path, line))
    assert _lint(tmp_path, line, rel="backend/context/NOTES.md") == []


def test_the_shipped_skills_are_clean():
    from lint_skills import corpus_files
    findings = [f for p in corpus_files() for f in lint_skills.lint_body(p)
                if "numbered layout" in f or "one machine's project" in f]
    assert findings == []
