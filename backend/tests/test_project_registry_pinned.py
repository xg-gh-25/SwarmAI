"""Tests for project_registry.get_pinned_projects — the backend-owned pinned order
for the DDD gallery / Welcome Top-3.

Contract: the default project is ALWAYS first (protected primary brain); the rest are
the projects whose own ``.project.json`` says ``"pinned": true``, in name order.
No project name is written in code (usable-by-anyone P7: the list used to be a const
naming XG's own projects).
"""
import json


def _project(projects, name, meta=None):
    d = projects / name
    d.mkdir(parents=True)
    if meta is not None:
        (d / ".project.json").write_text(json.dumps(meta) if isinstance(meta, dict) else meta)
    return d


class TestGetPinnedProjects:
    def test_default_first_then_flagged_projects(self, monkeypatch, tmp_path):
        from core import project_registry as pr
        projects = tmp_path / "Projects"
        _project(projects, "SwarmAI")
        _project(projects, "Zeta", {"pinned": True})
        _project(projects, "Alpha", {"pinned": True, "name": "Alpha"})
        _project(projects, "Plain", {"name": "Plain"})
        _project(projects, "NoMeta")
        monkeypatch.setattr(pr, "get_projects_dir", lambda: projects)
        assert pr.get_pinned_projects() == ["SwarmAI", "Alpha", "Zeta"]

    def test_no_name_is_pinned_by_code(self, monkeypatch, tmp_path):
        """A project called AIDLC or CMHK_SalesIntel is not pinned unless it says so."""
        from core import project_registry as pr
        projects = tmp_path / "Projects"
        for n in ("SwarmAI", "AIDLC", "CMHK_SalesIntel"):
            _project(projects, n)
        monkeypatch.setattr(pr, "get_projects_dir", lambda: projects)
        assert pr.get_pinned_projects() == ["SwarmAI"]

    def test_bad_metadata_and_missing_default(self, monkeypatch, tmp_path):
        from core import project_registry as pr
        projects = tmp_path / "Projects"
        _project(projects, "Broken", "{not json")
        _project(projects, "Truthy", {"pinned": "yes"})      # only JSON true counts
        _project(projects, "Real", {"pinned": True})
        monkeypatch.setattr(pr, "get_projects_dir", lambda: projects)
        assert pr.get_pinned_projects() == ["Real"]          # no default dir -> not listed
