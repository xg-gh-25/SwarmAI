"""Skill-path attribution in detect_active_project is DECLARED, never name-derived.

A file under ``backend/skills/<skill>/`` maps to the one project whose
``bindings.yaml`` has a ``kind: skill-set`` asset that lists the skill in
``members`` or covers it with a family ``prefix``. A project's name decides
nothing: ``Acme_SalesIntel`` / ``Foo_BIZ`` / ``Bar_ISV`` without a declaration
own no skill, exactly like any other project.

Synthetic project names only (this file is public).
"""
from __future__ import annotations

import pytest

SKILL_SET_PREFIX = """\
governed_assets:
  - kind: skill-set
    name: family
    prefix: s_acme-
"""

SKILL_SET_MEMBERS = """\
governed_assets:
  - kind: skill-set
    name: tools
    members: [s_widget-tools, s_other]
"""


@pytest.fixture
def projects(tmp_path, monkeypatch):
    """A fake Projects/ dir; ``make(name, bindings_text_or_None)`` adds a project."""
    import core.project_registry as pr

    monkeypatch.setattr(pr, "get_projects_dir", lambda: tmp_path)

    def make(name: str, bindings: str | None = None) -> str:
        d = tmp_path / name
        d.mkdir()
        if bindings is not None:
            (d / "bindings.yaml").write_text(bindings, encoding="utf-8")
        return name

    return make


def _detect(path: str, cands: list[str], query: str | None = None):
    from core.recall_multi import detect_active_project
    return detect_active_project(editor_file_path=path, query=query, candidates=cands)


def test_suffix_name_without_declaration_owns_nothing(projects):
    """The old <DOMAIN>_<SalesIntel|BIZ|ISV> rule decides nothing any more."""
    cands = [projects("Acme_SalesIntel"), projects("Acme_BIZ"), projects("Acme_ISV"),
             projects("Plain")]
    for skill in ("s_acme-weekly-report", "s_acme-x"):
        assert _detect(f"backend/skills/{skill}/SKILL.md", cands) == (None, "no_signal")


def test_suffix_name_with_unrelated_bindings_owns_nothing(projects):
    """A bindings.yaml without a skill-set asset is not a declaration."""
    cands = [projects("Acme_SalesIntel", "governed_assets:\n  - kind: data-source\n    name: d\n")]
    assert _detect("backend/skills/s_acme-weekly-report/SKILL.md", cands) == (None, "no_signal")


def test_declared_prefix_maps_skill_family(projects):
    cands = [projects("Anything", SKILL_SET_PREFIX), projects("Other")]
    assert _detect("/repo/backend/skills/s_acme-weekly-report/SKILL.md", cands) == (
        "Anything", "signal1_skill_path")
    # the prefix stops at its family boundary
    assert _detect("/repo/backend/skills/s_acmecorp-x/SKILL.md", cands) == (None, "no_signal")


def test_declared_member_maps_skill_path(projects):
    """Exact members, any project name (no suffix, single token)."""
    cands = [projects("Widgets", SKILL_SET_MEMBERS), projects("Acme_SalesIntel")]
    assert _detect("backend/skills/s_widget-tools/scripts/run.py", cands) == (
        "Widgets", "signal1_skill_path")
    assert _detect("backend/skills/s_widget-other/SKILL.md", cands) == (None, "no_signal")


def test_two_owners_is_ambiguous_and_falls_through(projects):
    """Two declarers → nobody from signal-1; the keyword signal still decides."""
    cands = [projects("Alpha", SKILL_SET_PREFIX), projects("Beta", SKILL_SET_PREFIX)]
    path = "backend/skills/s_acme-weekly-report/SKILL.md"
    assert _detect(path, cands) == (None, "no_signal")
    assert _detect(path, cands, query="what does beta say") == ("Beta", "signal3_keyword")


@pytest.mark.parametrize("bindings", [
    "governed_assets: [unclosed\n",                                    # malformed YAML
    "- just\n- a list\n",                                              # not a mapping
    "governed_assets: not-a-list\n",
    "governed_assets:\n  - kind: skill-set\n    members: s_acme-weekly-report\n",  # not a list
    "governed_assets:\n  - kind: skill-set\n    members: [1, null, '']\n",
    "governed_assets:\n  - kind: skill-set\n    prefix: s_\n",          # matches everything
    "governed_assets:\n  - kind: skill-set\n    prefix: s_acme\n",      # no family boundary
    "governed_assets:\n  - kind: skill-set\n    prefix: acme-\n",       # not a skill prefix
    "governed_assets:\n  - kind: skill-set\n    prefix: [s_acme-]\n",
])
def test_bad_declarations_own_nothing(projects, bindings):
    cands = [projects("Acme_SalesIntel", bindings)]
    assert _detect("backend/skills/s_acme-weekly-report/SKILL.md", cands) == (None, "no_signal")


def test_undecodable_bindings_owns_nothing(projects, tmp_path):
    """Bytes that are not UTF-8: the read raises, the project owns nothing."""
    name = projects("Acme_SalesIntel")
    (tmp_path / name / "bindings.yaml").write_bytes(
        b"governed_assets:\n  - kind: skill-set\n    prefix: s_acme-\n# \xff\xfe\n")
    assert _detect("backend/skills/s_acme-x/SKILL.md", [name]) == (None, "no_signal")


def test_unreadable_bindings_owns_nothing(projects, tmp_path):
    name = projects("Acme_SalesIntel")
    (tmp_path / name / "bindings.yaml").mkdir()  # a directory: reading it fails
    assert _detect("backend/skills/s_acme-x/SKILL.md", [name]) == (None, "no_signal")


def test_orchestrator_has_no_name_rule():
    """The name-to-prefix rule is gone from its old home."""
    import core.ddd_orchestrator as orch
    assert not hasattr(orch, "_BUSINESS_PROJECT_SUFFIXES")
    assert not hasattr(orch, "_derive_skill_prefix")
