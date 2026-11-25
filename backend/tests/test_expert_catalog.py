"""The native expert-review skill (backend/skills/s_expert-review; design
2026-10-24-swarmai-usable-by-anyone.md §3.5).

- CATALOG.md equals what gen_expert_catalog.render() produces from the experts'
  frontmatter (drift check: adding an expert = add its file + re-run the generator);
- every public expert is listed, and each listed prompt path resolves from the skill dir
  (the same relative layout holds in the packaged bundle: _internal/skills, _internal/experts);
- the skill passes the repo's skill linter;
- the three content experts are listed under their own domain, and the pipeline's code
  review roster never picks them.
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
SKILL = BACKEND / "skills" / "s_expert-review"


def _gen():
    spec = importlib.util.spec_from_file_location(
        "gen_expert_catalog", BACKEND / "scripts" / "gen_expert_catalog.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_catalog_is_current():
    assert (SKILL / "CATALOG.md").read_text(encoding="utf-8") == _gen().render(), \
        "run python backend/scripts/gen_expert_catalog.py"


def test_every_listed_prompt_file_resolves_and_every_public_expert_is_listed():
    from core.expert_manager import ExpertManager
    text = (SKILL / "CATALOG.md").read_text(encoding="utf-8")
    experts = ExpertManager(BACKEND / "experts").scan_experts()
    public = {e for e, i in experts.items()
              if str(getattr(i, "visibility", "public")).lower() == "public"}
    assert public and all(f"| `{e}` |" in text for e in public)
    for line in text.splitlines():
        if line.startswith("| `") and "../../experts/" in line:
            rel = line.rsplit("`", 2)[-2]
            assert (SKILL / rel).resolve().is_file(), rel


def test_skill_lints_clean():
    root = BACKEND.parent
    r = subprocess.run([sys.executable, str(root / "scripts" / "lint_skills.py")],
                       capture_output=True, text=True, cwd=str(root), timeout=60)
    assert "s_expert-review" not in (r.stdout + r.stderr), r.stdout + r.stderr


CONTENT = {"content-accuracy", "content-visual", "content-copy"}


def test_content_experts_are_listed_under_the_content_domain():
    from core.expert_manager import ExpertManager
    experts = ExpertManager(BACKEND / "experts").scan_experts()
    content = {e for e, i in experts.items() if i.domain == "content"}
    assert content == CONTENT
    assert all(experts[e].is_valid and experts[e].visibility == "public" for e in CONTENT)
    text = (SKILL / "CATALOG.md").read_text(encoding="utf-8")
    for e in CONTENT:
        assert f"| `{e}` |" in text and f"`../../experts/content/{e}.md`" in text, e
    # the native skill tells the agent when to call each of them
    instructions = (SKILL / "INSTRUCTIONS.md").read_text(encoding="utf-8")
    assert all(f"`{e}`" in instructions for e in CONTENT)


def test_pipeline_code_review_never_picks_a_content_expert():
    """Adding a domain does not change how code experts are chosen: the pipeline's
    roster comes from reviewers() and its prompts from experts/code only."""
    import pytest
    from core.run_protocol.review import expert_prompt, reviewers
    changed = [{"path": "backend/skills/s_pollinate/SKILL.md", "insertions": 200,
                "deletions": 0},
               {"path": "backend/core/auth.py", "insertions": 80, "deletions": 0}]
    for profile in ("full", "bugfix", "trivial", "direct"):
        ids = {row["id"] for row in reviewers(profile=profile, changed=changed,
                                              security=["declared"])}
        assert not ids & CONTENT, (profile, ids)
    with pytest.raises(FileNotFoundError):
        expert_prompt("content-copy")
