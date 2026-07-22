"""L3 shared lane — non-owner read-only access to shareable-project DDD docs.

Feature: run_c220f153 (L3 shared lane, D3-A + D4-A).

Two mechanisms under test, both driven through the REAL code (no mock of the
symbol under change — GUI32/PIT13):

1. ``create_file_access_permission_handler(..., readonly_files=[...])``
   (security_hooks.py) — the EXACT-match, read-only file grant:
     * Read/Glob/Grep on a readonly_file  -> allow
     * Write/Edit on a readonly_file      -> deny (cannot corrupt shared docs)
     * a sibling under the same dir        -> deny (no recursion → .artifacts/
                                             pipeline internals stay hidden)
     * Bash cat of a readonly_file         -> deny (Bash branch ignores
                                             readonly_files → fail-closed narrower)

2. ``PromptBuilder._collect_shareable_ddd_paths()`` (prompt_builder.py) — the
   fail-closed scanner: only ``shareable: true`` projects contribute, only the 4
   canonical DDD docs that ACTUALLY EXIST, any error → ``[]`` (deny, never open).

Security posture: fail-closed. A private project, a non-existent doc, a bad
.project.json, or an IO error must NEVER widen the grant.
"""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from core.security_hooks import create_file_access_permission_handler


_CTX: dict = {}  # can_use_tool context arg — unused by the handler


async def _decide(handler, tool_name: str, path: str) -> str:
    """Invoke the real async handler, return 'allow' | 'deny'."""
    path_param = "path" if tool_name in ("Glob", "Grep") else "file_path"
    res = await handler(tool_name, {path_param: path}, _CTX)
    return res["behavior"]


# ===================================================================
# Mechanism 1: readonly_files grant in the file-access handler
# ===================================================================
class TestReadonlyFileGrant:
    @pytest.fixture
    def shared_doc(self, tmp_path: Path) -> Path:
        proj = tmp_path / "Projects" / "SharedProj"
        proj.mkdir(parents=True)
        doc = proj / "TECH.md"
        doc.write_text("# shared domain knowledge", encoding="utf-8")
        # a sibling that must stay hidden (pipeline internals)
        artifacts = proj / ".artifacts" / "runs" / "run_x"
        artifacts.mkdir(parents=True)
        (artifacts / "REPORT.md").write_text("secret internals", encoding="utf-8")
        return doc

    @pytest.fixture
    def handler(self, tmp_path: Path, shared_doc: Path):
        # sender-scoped dir (the only recursive r/w grant) + the shared doc read-only
        sender_dir = tmp_path / "channel_files" / "U123"
        sender_dir.mkdir(parents=True)
        return create_file_access_permission_handler(
            [str(sender_dir)],
            readonly_files=[str(shared_doc)],
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tool", ["Read", "Glob", "Grep"])
    async def test_readonly_tool_allowed_on_shared_doc(self, handler, shared_doc, tool):
        assert await _decide(handler, tool, str(shared_doc)) == "allow"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tool", ["Write", "Edit"])
    async def test_write_denied_on_shared_doc(self, handler, shared_doc, tool):
        # the whole point: teammate can READ but never CORRUPT the shared doc
        assert await _decide(handler, tool, str(shared_doc)) == "deny"

    @pytest.mark.asyncio
    async def test_sibling_artifacts_denied(self, handler, shared_doc):
        # .artifacts/ under the SAME project dir must NOT be reachable —
        # readonly grant is exact-file, non-recursive (the adversarial-found leak)
        sibling = shared_doc.parent / ".artifacts" / "runs" / "run_x" / "REPORT.md"
        assert await _decide(handler, "Read", str(sibling)) == "deny"

    @pytest.mark.asyncio
    async def test_project_dir_itself_denied(self, handler, shared_doc):
        # a non-granted sibling doc in the same project dir → deny
        other = shared_doc.parent / "SECRETS.md"
        assert await _decide(handler, "Read", str(other)) == "deny"

    @pytest.mark.asyncio
    async def test_bash_cat_of_shared_doc_denied(self, handler, shared_doc):
        # Bash branch does NOT honor readonly_files → cat is denied.
        # This is fail-closed NARROWER (teammate must use Read tool), not a leak.
        # Locking it prevents a future edit from opening Bash to writable access.
        res = await handler("Bash", {"command": f"cat {shared_doc}"}, _CTX)
        assert res["behavior"] == "deny"


# ===================================================================
# Mechanism 2: _collect_shareable_ddd_paths — fail-closed scanner
# ===================================================================
class TestShareableScan:
    def _make_project(self, root: Path, name: str, *, shareable, docs):
        proj = root / "Projects" / name
        proj.mkdir(parents=True)
        meta = {"name": name}
        if shareable is not None:
            meta["shareable"] = shareable
        (proj / ".project.json").write_text(json.dumps(meta), encoding="utf-8")
        for d in docs:
            (proj / d).write_text(f"# {d}", encoding="utf-8")
        return proj

    async def _run_scan(self, ws_root: Path):
        # Import here so the module-level import cost isn't paid unless run.
        from core.prompt_builder import PromptBuilder

        pb = PromptBuilder.__new__(PromptBuilder)  # skip heavy __init__
        with patch(
            "core.initialization_manager.initialization_manager"
            ".get_cached_workspace_path",
            return_value=str(ws_root),
        ):
            return await pb._collect_shareable_ddd_paths()

    @pytest.mark.asyncio
    async def test_shareable_project_contributes_existing_docs(self, tmp_path):
        self._make_project(
            tmp_path, "Shared", shareable=True,
            docs=["PRODUCT.md", "TECH.md"],  # only 2 of 4 exist
        )
        paths = await self._run_scan(tmp_path)
        assert len(paths) == 2
        assert all(p.endswith(("PRODUCT.md", "TECH.md")) for p in paths)

    @pytest.mark.asyncio
    async def test_migrated_project_shares_docs_from_2understanding(self, tmp_path):
        """A MIGRATED shareable DDD keeps its 4 docs under 2-understanding/. The
        scanner must still surface them (via ddd_path) — the containment guard was
        'direct child of project_dir', which rejected every migrated doc until it
        was relaxed to 'inside the project tree' (run_3a636c88, Gate-2 catch)."""
        proj = tmp_path / "Projects" / "Migrated"
        und = proj / "2-understanding"
        und.mkdir(parents=True)
        (proj / ".project.json").write_text(json.dumps(
            {"name": "Migrated", "shareable": True}), encoding="utf-8")
        for d in ("PRODUCT.md", "TECH.md"):
            (und / d).write_text(f"# {d}", encoding="utf-8")
        paths = await self._run_scan(tmp_path)
        assert len(paths) == 2, f"migrated shareable docs must be shared, got {paths}"
        assert all("2-understanding" in p for p in paths)

    @pytest.mark.asyncio
    async def test_symlink_escape_still_rejected_after_migration(self, tmp_path):
        """Security intact: a 2-understanding/TECH.md -> ../../MEMORY.md symlink must
        STILL be rejected (the guard relaxation must not open a symlink escape)."""
        proj = tmp_path / "Projects" / "Evil"
        und = proj / "2-understanding"
        und.mkdir(parents=True)
        (proj / ".project.json").write_text(json.dumps(
            {"name": "Evil", "shareable": True}), encoding="utf-8")
        secret = tmp_path / "MEMORY.md"
        secret.write_text("# owner private", encoding="utf-8")
        (und / "PRODUCT.md").write_text("# real", encoding="utf-8")
        (und / "TECH.md").symlink_to(secret)  # escape attempt
        paths = await self._run_scan(tmp_path)
        assert all("MEMORY.md" not in p for p in paths), "symlink escape must be rejected"
        assert not any(str(secret) in p for p in paths)

    @pytest.mark.asyncio
    async def test_private_project_contributes_nothing(self, tmp_path):
        # shareable=False → fail-closed exclusion
        self._make_project(
            tmp_path, "Private", shareable=False,
            docs=["PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md"],
        )
        assert await self._run_scan(tmp_path) == []

    @pytest.mark.asyncio
    async def test_absent_flag_contributes_nothing(self, tmp_path):
        # shareable absent → default fail-closed (absence = private)
        self._make_project(
            tmp_path, "NoFlag", shareable=None, docs=["TECH.md"],
        )
        assert await self._run_scan(tmp_path) == []

    @pytest.mark.asyncio
    async def test_truthy_nonbool_shareable_rejected(self, tmp_path):
        # `is not True` — a truthy string/1 must NOT open the gate (strict True)
        self._make_project(tmp_path, "Sneaky", shareable="yes", docs=["TECH.md"])
        assert await self._run_scan(tmp_path) == []

    @pytest.mark.asyncio
    async def test_nonexistent_docs_not_returned(self, tmp_path):
        # shareable=True but NO docs on disk → no phantom paths
        self._make_project(tmp_path, "Empty", shareable=True, docs=[])
        assert await self._run_scan(tmp_path) == []

    @pytest.mark.asyncio
    async def test_only_canonical_docs_shared(self, tmp_path):
        # a shareable project with a non-canonical sensitive file → not shared
        proj = self._make_project(
            tmp_path, "Shared", shareable=True, docs=["TECH.md"],
        )
        (proj / "SECRETS.md").write_text("secret", encoding="utf-8")
        (proj / "CONTEXT.md").write_text("context", encoding="utf-8")
        paths = await self._run_scan(tmp_path)
        assert len(paths) == 1
        assert paths[0].endswith("TECH.md")

    @pytest.mark.asyncio
    async def test_bad_json_project_skipped(self, tmp_path):
        proj = tmp_path / "Projects" / "Corrupt"
        proj.mkdir(parents=True)
        (proj / ".project.json").write_text("{not valid json", encoding="utf-8")
        (proj / "TECH.md").write_text("# tech", encoding="utf-8")
        # bad JSON → that project fail-closed skipped, not a crash
        assert await self._run_scan(tmp_path) == []

    @pytest.mark.asyncio
    async def test_no_projects_dir_returns_empty(self, tmp_path):
        # workspace with no Projects/ → [] (no crash)
        assert await self._run_scan(tmp_path) == []

    @pytest.mark.asyncio
    async def test_symlinked_doc_escaping_project_rejected(self, tmp_path):
        # Adversarial M1: a symlink named as a canonical DDD doc, pointing at an
        # owner-private file OUTSIDE the project, must NOT be granted. is_file()
        # follows symlinks + handler realpath-resolves → would leak MEMORY.md.
        private = tmp_path / "MEMORY.md"
        private.write_text("SECRET owner memory", encoding="utf-8")
        proj = tmp_path / "Projects" / "Shared"
        proj.mkdir(parents=True)
        (proj / ".project.json").write_text(
            json.dumps({"name": "Shared", "shareable": True}), encoding="utf-8"
        )
        (proj / "TECH.md").symlink_to("../../MEMORY.md")
        paths = await self._run_scan(tmp_path)
        # the symlink must contribute nothing; NOTHING may resolve to the private file
        assert paths == []
        assert not any(
            __import__("os").path.realpath(p)
            == __import__("os").path.realpath(private)
            for p in paths
        )

    @pytest.mark.asyncio
    async def test_symlink_to_sibling_real_doc_still_rejected(self, tmp_path):
        # even a symlink to ANOTHER (non-shared) project's real DDD doc is rejected
        other = tmp_path / "Projects" / "Private"
        other.mkdir(parents=True)
        (other / "TECH.md").write_text("# private tech", encoding="utf-8")
        proj = tmp_path / "Projects" / "Shared"
        proj.mkdir(parents=True)
        (proj / ".project.json").write_text(
            json.dumps({"name": "Shared", "shareable": True}), encoding="utf-8"
        )
        (proj / "PRODUCT.md").symlink_to("../Private/TECH.md")
        assert await self._run_scan(tmp_path) == []

    @pytest.mark.asyncio
    async def test_mixed_projects_only_shareable_returned(self, tmp_path):
        self._make_project(tmp_path, "Pub", shareable=True, docs=["TECH.md"])
        self._make_project(tmp_path, "Priv", shareable=False, docs=["TECH.md"])
        self._make_project(tmp_path, "NoFlag", shareable=None, docs=["TECH.md"])
        paths = await self._run_scan(tmp_path)
        assert len(paths) == 1
        assert "Pub" in paths[0]
