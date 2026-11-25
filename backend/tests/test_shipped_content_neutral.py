"""Shipped skills, seed content and scripts carry no one person's or one company's data,
and the project-manager skill deletes a project through the product's recoverable path.

Every user's agent reads these files (skills, the seeded SwarmAI brain) or runs them
(skill scripts), so an example naming the owner's topics, colleagues or one company's
internal tools is wrong for everyone else, and a delete recipe that only works on macOS
loses data elsewhere.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
SKILLS = BACKEND / "skills"
POLLINATE = SKILLS / "s_pollinate"
PROJECT_MANAGER = SKILLS / "s_project-manager" / "SKILL.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    match = re.search(rf"^### {re.escape(heading)}\b.*?(?=^### |^## |\Z)", text,
                      re.MULTILINE | re.DOTALL)
    assert match, heading
    return match.group(0)


# ── 1. deleting a project ────────────────────────────────────────────────────

def test_project_delete_goes_through_the_projects_api_with_no_permanent_fallback():
    delete = _section(_read(PROJECT_MANAGER), "Delete a Project")
    code = "\n".join(re.findall(r"```bash\n(.*?)```", delete, re.DOTALL))
    # ~/.Trash exists only on macOS: elsewhere `mv` renamed the project to ~/.Trash, and a
    # failed mv fell back to an unrecoverable rm -rf.
    assert "~/.Trash" not in delete
    assert "rm -rf" not in code and not re.search(r"^\s*mv\b", code, re.MULTILINE)
    assert "-X DELETE" in code and "/api/projects/$ID" in code
    assert "Projects/.trash/" in delete


def test_the_projects_api_delete_route_exists():
    """The recipe's route: DELETE /api/projects/{id} → swarm_workspace_manager.delete_project,
    which moves the folder to Projects/.trash/ (never rmtree)."""
    routes = _read(BACKEND / "routers" / "projects.py")
    assert re.search(r'@router\.delete\("/\{project_id\}"', routes)
    assert 'prefix="/projects"' in routes
    manager = _read(BACKEND / "core" / "swarm_workspace_manager.py")
    body = manager[manager.index("async def delete_project("):]
    body = body[:body.index("\n    async def ", 1)]
    assert '".trash"' in body and "move_tree(" in body and "shutil.rmtree(" not in body


# ── 2. no one company's internal tools in project-manager ────────────────────

_INTERNAL_TOOLS = re.compile(
    r"(?i:\b(?:brazil|crux|midway|gcraidlcpreset)\b)|code\.amazon\.com|git\.amazon\.com")


def test_project_manager_names_no_internal_tools():
    text = _read(PROJECT_MANAGER)
    hits = [m.group(0) for m in _INTERNAL_TOOLS.finditer(text)]
    assert hits == []


def test_project_manager_keeps_the_internal_capability_and_points_to_host_skills():
    text = _read(PROJECT_MANAGER)
    assert "kind: internal" in text
    assert "<internal-repo>" in text
    assert "host-provided `s_internal-*`" in text


# ── 3. s_pollinate ───────────────────────────────────────────────────────────

_POLLINATE_FILES = [
    "brand/identity.yaml", "references/platform-specs.md", "INSTRUCTIONS.md",
    "kinds/deck.md", "kinds/copy.md", "kinds/poster.md",
]


@pytest.mark.parametrize("rel", _POLLINATE_FILES)
def test_pollinate_examples_are_neutral(rel):
    text = _read(POLLINATE / rel)
    assert not re.search(r"(?i)aidlc", text), rel           # the owner's own content topic
    assert not re.search(r"\b(?:Rob|Bo)\b|\brob\b", text), rel  # colleagues' names
    assert "pollinate-v2-launch" not in text and "LPM" not in text, rel


# ── 4. the seeded SwarmAI brain describes every platform ────────────────────

def test_seed_tech_md_describes_the_backend_per_platform():
    text = _read(BACKEND / "templates" / "swarmai-seed" / "TECH.md")
    assert "launchd daemon (24/7)" not in text
    arch = text[text.index("## Architecture"):]
    arch = arch[:arch.index("```")]
    assert "macOS" in arch and "launchd" in arch
    assert "Windows and Linux a child process of the app" in arch
    assert "(AIDLC)" in text  # a public methodology name, kept


# ── 5. no instance demo page in the installer ───────────────────────────────

def test_brain_hub_demo_page_is_not_shipped():
    assert not (REPO / "desktop" / "public" / "brain-hub-demo.html").exists()
