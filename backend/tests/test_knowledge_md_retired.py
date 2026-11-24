"""KNOWLEDGE.md is retired as a governed context file.

It was a hand-kept index of ``Knowledge/`` that duplicated SWARMAI.md § My Workspace.
After the retirement:

- it is not a context file (no spec, no template, never seeded, never injected);
- a workspace that already holds the user's copy keeps it on disk, byte for byte;
- the hooks that watch context files no longer list it;
- it stays in the workspace .gitignore, so an old copy never reaches a remote.

Author: SwarmAI
"""
from __future__ import annotations

import os
from pathlib import Path

from core.context_directory_loader import (
    CONTEXT_FILES,
    RETIRED_CONTEXT_FILES,
    ContextDirectoryLoader,
)

_SENTINEL = "kn0wledge-sentinel-7f3a"
_TEMPLATES = Path(__file__).resolve().parents[1] / "context"


def _names() -> set[str]:
    return {s.filename for s in CONTEXT_FILES}


def test_not_a_context_file():
    assert "KNOWLEDGE.md" not in _names()
    assert len(CONTEXT_FILES) == 11
    assert "KNOWLEDGE.md" in RETIRED_CONTEXT_FILES
    assert not (_TEMPLATES / "KNOWLEDGE.md").exists()


def test_existing_file_is_not_injected(tmp_path: Path):
    ctx = tmp_path / ".context"
    ctx.mkdir()
    (ctx / "SWARMAI.md").write_text("# Core\nYou are SwarmAI.", encoding="utf-8")
    (ctx / "KNOWLEDGE.md").write_text(f"# Knowledge\n{_SENTINEL}\n", encoding="utf-8")
    out = ContextDirectoryLoader(context_dir=ctx)._assemble_from_sources()
    assert "You are SwarmAI." in out
    assert _SENTINEL not in out
    assert "## Knowledge" not in out


def test_new_workspace_gets_no_knowledge_md(tmp_path: Path):
    ctx = tmp_path / ".context"
    ContextDirectoryLoader(context_dir=ctx, templates_dir=_TEMPLATES).ensure_directory()
    assert (ctx / "SWARMAI.md").exists()  # seeding did run
    assert not (ctx / "KNOWLEDGE.md").exists()


def test_existing_copy_stays_byte_identical(tmp_path: Path):
    ctx = tmp_path / ".context"
    ctx.mkdir()
    old = ctx / "KNOWLEDGE.md"
    body = f"# My index\n{_SENTINEL}\n- Notes/\n".encode()
    old.write_bytes(body)
    os.chmod(old, 0o644)
    ContextDirectoryLoader(context_dir=ctx, templates_dir=_TEMPLATES).ensure_directory()
    assert old.read_bytes() == body
    assert (old.stat().st_mode & 0o777) == 0o644


def test_token_block_has_no_knowledge_row(tmp_path: Path):
    from core.context_brain import build_context_token_block

    ctx = tmp_path / ".context"
    ctx.mkdir()
    (ctx / "SWARMAI.md").write_text("core identity " * 50, encoding="utf-8")
    (ctx / "KNOWLEDGE.md").write_text("index " * 500, encoding="utf-8")
    rows = build_context_token_block(ctx)["per_file"]
    assert [r["name"] for r in rows if "KNOWLEDGE" in r["name"]] == []


def test_hooks_no_longer_watch_it():
    from core import cognition, needs_human_review, runtime_hooks
    from hooks.context_health_hook import ContextHealthHook

    assert "KNOWLEDGE.md" not in runtime_hooks._MEMORY_FILE_SUFFIXES
    assert "KNOWLEDGE.md" not in ContextHealthHook._CONTEXT_FILES
    assert "KNOWLEDGE.md" not in needs_human_review._KNOWLEDGE_BASENAMES
    assert "KNOWLEDGE.md" not in cognition.CONTEXT_FILES


def test_old_copy_stays_gitignored():
    from core.swarm_workspace_manager import (
        GITIGNORE_CONTENT,
        _PRIVACY_GITIGNORE_RULES,
        _PRIVATE_CONTEXT_FILES,
    )

    assert ".context/KNOWLEDGE.md" in GITIGNORE_CONTENT.splitlines()
    assert ".context/KNOWLEDGE.md" in _PRIVACY_GITIGNORE_RULES
    assert ".context/KNOWLEDGE.md" in _PRIVATE_CONTEXT_FILES  # a committed copy is untracked
