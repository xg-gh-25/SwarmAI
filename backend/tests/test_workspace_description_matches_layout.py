"""SWARMAI.md § My Workspace describes exactly the layout the code uses (knowledge
addressing §2.2, §6.3 test 2).

The section is prose every session reads; nothing tied it to the layout constants, so
when 2026-07-21 renumbered the DDD sections, AGENTS.md files and a skill kept the old
paths for months. This test pins the prose to ``ddd_paths`` / ``DDD_CANONICAL_DOCS`` /
``KNOWLEDGE_SUBDIRS``: change a constant without the text (or the reverse) and it fails.
It holds no project name, count or size, so adding or removing a DDD never touches it.
"""
import re
from pathlib import Path

from core import ddd_paths
from core.project_registry import DDD_CANONICAL_DOCS
from core.swarm_workspace_manager import KNOWLEDGE_SUBDIRS

SWARMAI = Path(__file__).resolve().parents[1] / "context" / "SWARMAI.md"


def _section(text: str = None) -> str:
    text = text if text is not None else SWARMAI.read_text(encoding="utf-8")
    start = text.index("## My Workspace\n")
    end = text.index("\n## ", start + 1)
    return text[start:end]


def _bullet(section: str, lead: str) -> str:
    start = section.index(f"- **{lead}")
    nxt = section.find("\n- **", start + 1)
    return section[start:nxt if nxt != -1 else None]


def _dirs(text: str) -> set[str]:
    return set(re.findall(r"`([A-Za-z][\w.-]*)/`", text))


def test_ddd_layout_names_every_section_dir_and_doc():
    projects = _bullet(_section(), "Projects/")
    for d in (ddd_paths.GATES_DIR, ddd_paths.CAPABILITIES_DIR):
        assert f"`{d}/`" in projects, d
    assert f"`{ddd_paths.KNOWLEDGE_CORPUS_DIR}/`" in projects
    assert f"`{ddd_paths.ASSETS_DIR}/<kind>/`" in projects
    assert f"`{ddd_paths.UNDERSTANDING_DIR}/{DDD_CANONICAL_DOCS[0]}`" in projects
    for doc in DDD_CANONICAL_DOCS:
        assert f"`{doc}`" in projects or f"/{doc}`" in projects, doc
    assert _dirs(projects) <= {ddd_paths.GATES_DIR, ddd_paths.CAPABILITIES_DIR}


def test_knowledge_line_lists_exactly_the_workspace_subdirs():
    knowledge = _bullet(_section(), "Knowledge/")
    assert _dirs(knowledge) == set(KNOWLEDGE_SUBDIRS)


def test_no_old_layout_path_anywhere_in_swarmai_md():
    text = SWARMAI.read_text(encoding="utf-8")
    assert ddd_paths.stale_layout_refs(text) == []
    assert "`gates/`" not in text and "`skills/`" not in text


def test_no_instance_facts_in_the_section():
    section = _section()
    from core.addressing import projects
    from core.project_agent import GENERAL_BRAIN
    for name, _root, _has in projects():
        if name == GENERAL_BRAIN:      # built in on every install: a product fact, not this one's
            continue
        assert f"`{name}`" not in section and f" {name} " not in section, name
    assert not re.search(r"\b\d+\s+(projects|DDDs|entries|files)\b", section)


def test_the_check_has_teeth():
    good = _section()
    bad = good.replace(f"`{ddd_paths.GATES_DIR}/`", "`gates/`")
    assert ddd_paths.GATES_DIR not in _dirs(_bullet(bad, "Projects/"))
    assert ddd_paths.stale_layout_refs("read Projects/X/TECH.md") == ["Projects/X/TECH.md"]
    assert ddd_paths.stale_layout_refs(f"Projects/X/{ddd_paths.UNDERSTANDING_DIR}/TECH.md") == []
