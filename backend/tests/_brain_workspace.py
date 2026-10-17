"""Shared hermetic DDD workspace for Brain Hub router tests.

Builds a real git workspace with two brains in tmp and points the router at it,
so tests assert real files / a real `git` / a real parse WITHOUT reading the
developer's live ~/.swarm-ai/SwarmWS (conftest re-roots app data off production,
so a live-tree dependency sees an empty Projects/ and goes red everywhere).
Import the fixture by name: ``from tests._brain_workspace import hermetic_workspace``.
"""

import pytest


_FIXTURE_TECH = """# TECH

## Architecture
- [guideline] **Alpha rule** — do the thing because reasons.
  <!-- ref:0 | last:2026-10-01 | decay:active -->
- [pitfall] **Beta trap** — watch out for this.
  <!-- ref:0 | last:2026-01-01 | decay:dormant -->
- [decision] **Gamma choice** — we chose X.
  <!-- ref:0 | last:2025-01-01 | decay:archived -->
"""

_FIXTURE_PRODUCT = """# PRODUCT

## Priorities
- [principle] **Delta value** — what matters most.
  <!-- ref:0 | last:2026-10-01 | decay:active -->
"""


@pytest.fixture
def hermetic_workspace(tmp_path, monkeypatch):
    """A real git workspace with two DDD brains, pointed at by the router.

    SwarmAI: identity + all four ② docs (TECH carries 1 dormant + 1 archived
    entry), a REFRESHER.md, a bindings.yaml, a code_intel.db, nothing in ③.
    Other: identity + one ② doc, NO code_intel.db (the hasCodeIntel=false case).
    """
    import subprocess

    import jobs.paths
    import routers.ddd_brain as m

    ws = tmp_path / "SwarmWS"
    projects = ws / "Projects"
    sw = projects / "SwarmAI"
    (sw / "2-understanding").mkdir(parents=True)
    (sw / ".project.json").write_text('{"name": "SwarmAI"}')
    (sw / "AGENTS.md").write_text("# SwarmAI\n")
    (sw / "2-understanding" / "TECH.md").write_text(_FIXTURE_TECH)
    for doc in ("PRODUCT.md", "IMPROVEMENT.md", "PROJECT.md"):
        (sw / "2-understanding" / doc).write_text(_FIXTURE_PRODUCT)
    (sw / "REFRESHER.md").write_text("# Refresher\n")
    (sw / "bindings.yaml").write_text("assets: []\n")
    (sw / "code_intel.db").write_bytes(b"")

    other = projects / "Other"
    (other / "2-understanding").mkdir(parents=True)
    (other / ".project.json").write_text('{"name": "Other"}')
    (other / "AGENTS.md").write_text("# Other\n")
    (other / "2-understanding" / "TECH.md").write_text(_FIXTURE_PRODUCT)

    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}
    for cmd in (["git", "init", "-q"], ["git", "add", "-A"], ["git", "commit", "-q", "-m", "init"]):
        subprocess.run(cmd, cwd=ws, env=env, check=True, capture_output=True)

    monkeypatch.setattr(m, "_workspace_root", lambda: ws)
    monkeypatch.setattr(jobs.paths, "PROJECTS_DIR", projects)
    return ws
