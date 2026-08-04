"""Property-based tests for the workspace tree endpoint structure.

**Feature: swarmws-explorer-ux, Property: Tree endpoint returns valid nested
JSON structure**

Uses Hypothesis with ``tmp_path`` to generate random filesystem structures on
disk, then calls the ``_build_tree`` helper directly to verify the response
shape.  Key invariants checked:

- Every node has the required fields (name, path, type, children).
- Directories always have a ``children`` list; files have ``children = None``.
- Hidden files (starting with ``'.'``) are excluded except ``.project.json``
  and ``.context``.
- Nodes are sorted: directories first, then files, both alphabetically.

**Validates: Requirements 10.1, 15.1**
"""

from pathlib import Path
from uuid import uuid4

import pytest
from hypothesis import given, strategies as st

from routers.workspace_api import _build_tree, _get_git_status, _should_include, _tree_fingerprint
from tests.helpers import PROPERTY_SETTINGS


# ---------------------------------------------------------------------------
# Fix C: _tree_fingerprint — single-pass fingerprint from the in-memory tree
# (replaces the redundant _fs_fingerprint filesystem walk). run_f5ab71b5
# ---------------------------------------------------------------------------


def _node(name: str, ntype: str, children=None) -> dict:
    n: dict = {"name": name, "path": name, "type": ntype, "children": children}
    return n


def test_tree_fingerprint_is_deterministic() -> None:
    tree = [_node("a", "directory", [_node("a/x", "file")]), _node("b", "file")]
    assert _tree_fingerprint(tree) == _tree_fingerprint(tree)


def test_tree_fingerprint_changes_on_add() -> None:
    before = [_node("a", "directory", [_node("a/x", "file")])]
    after = [_node("a", "directory", [_node("a/x", "file"), _node("a/y", "file")])]
    assert _tree_fingerprint(before) != _tree_fingerprint(after)


def test_tree_fingerprint_changes_on_delete() -> None:
    before = [_node("a", "directory", [_node("a/x", "file"), _node("a/y", "file")])]
    after = [_node("a", "directory", [_node("a/x", "file")])]
    assert _tree_fingerprint(before) != _tree_fingerprint(after)


def test_tree_fingerprint_changes_on_rename() -> None:
    before = [_node("a", "directory", [_node("a/x", "file")])]
    after = [_node("a", "directory", [_node("a/renamed", "file")])]
    assert _tree_fingerprint(before) != _tree_fingerprint(after)


def test_tree_fingerprint_changes_on_type_flip() -> None:
    # Same name AND same children shape (both None) — ONLY the type token differs.
    # This isolates the type discriminator: a file and a depth-truncated dir both
    # have children=None, so if the fingerprint ignored `type`, these would collide.
    # (The M3 skeptic's exact edge case: same-name file↔dir must change the hash.)
    before = [_node("README", "file", None)]
    after = [_node("README", "directory", None)]
    assert _tree_fingerprint(before) != _tree_fingerprint(after)


def test_tree_fingerprint_stable_when_children_null_boundary_unchanged() -> None:
    # A depth-truncated dir (children=None) contributes its presence; two identical
    # trees with a null boundary must hash equal.
    t1 = [_node("deep", "directory", None)]
    t2 = [_node("deep", "directory", None)]
    assert _tree_fingerprint(t1) == _tree_fingerprint(t2)


# ---------------------------------------------------------------------------
# Fix ②: _get_git_status pathspec scoping (run_500b576e)
# ---------------------------------------------------------------------------


def _init_git_repo_with_dirty_subtree(root: Path) -> None:
    """Create a git repo under *root* with a committed baseline and one dirty
    file inside a subdirectory, plus a dirty file OUTSIDE that subdirectory."""
    import subprocess

    def run(*args: str) -> None:
        subprocess.run(
            list(args), cwd=str(root), check=True,
            capture_output=True, text=True,
        )

    run("git", "init", "-q")
    run("git", "config", "user.email", "t@t.t")
    run("git", "config", "user.name", "t")
    (root / "sub").mkdir()
    (root / "other").mkdir()
    (root / "sub" / "committed.txt").write_text("v1\n")
    (root / "other" / "committed.txt").write_text("v1\n")
    run("git", "add", "-A")
    run("git", "commit", "-q", "-m", "baseline")
    # Now dirty one file in each dir
    (root / "sub" / "committed.txt").write_text("v2-modified\n")
    (root / "other" / "committed.txt").write_text("v2-modified\n")
    (root / "sub" / "untracked.txt").write_text("new\n")


def test_get_git_status_full_scan_sees_all_dirty(tmp_path: Path) -> None:
    """Without a pathspec, _get_git_status reports dirty files across the whole
    repo (baseline behavior — must be preserved)."""
    _init_git_repo_with_dirty_subtree(tmp_path)
    status = _get_git_status(tmp_path)
    assert status.get("sub/committed.txt") == "modified"
    assert status.get("other/committed.txt") == "modified"
    # keys are workspace-relative, forward-slashed
    assert all("\\" not in k for k in status)


def test_get_git_status_pathspec_scopes_to_subtree(tmp_path: Path) -> None:
    """With a pathspec, _get_git_status only reports files under that path, and
    keys remain workspace-relative (so _build_tree prefix-matching still works)."""
    _init_git_repo_with_dirty_subtree(tmp_path)
    status = _get_git_status(tmp_path, pathspec="sub")
    # The subtree's dirty + untracked files are present, workspace-relative keys
    assert status.get("sub/committed.txt") == "modified"
    assert status.get("sub/untracked.txt") == "untracked"
    # The OUT-OF-SCOPE dirty file is NOT reported (this is the scoping win)
    assert "other/committed.txt" not in status






# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Safe filename characters — letters, digits, underscore, hyphen, dot
_safe_char = st.characters(
    whitelist_categories=("L", "N"),
    whitelist_characters="_-.",
)

# A valid filename: 1–30 chars, never starts with '.' (visible files)
_visible_name = st.text(
    alphabet=_safe_char, min_size=1, max_size=30,
).filter(lambda n: not n.startswith(".") and n.strip() != "")

# A hidden filename (starts with '.', length >= 2)
_hidden_name = st.text(
    alphabet=_safe_char, min_size=1, max_size=20,
).map(lambda n: "." + n.lstrip(".")).filter(lambda n: len(n) >= 2)


@st.composite
def _filesystem_tree(draw: st.DrawFn) -> list[dict]:
    """Generate a random filesystem tree description.

    Returns a list of dicts, each with:
    - ``"name"``: str
    - ``"type"``: ``"file"`` or ``"directory"``
    - ``"children"``: list (for directories) or absent (for files)

    Generates a mix of visible files, hidden files, and directories
    (up to 2 levels deep) to exercise filtering and sorting logic.
    """
    items: list[dict] = []
    seen_names: set[str] = set()

    # Visible files
    num_files = draw(st.integers(min_value=0, max_value=6))
    for _ in range(num_files):
        name = draw(_visible_name)
        lower = name.lower()
        if lower not in seen_names:
            seen_names.add(lower)
            items.append({"name": name, "type": "file"})

    # Hidden files (should be excluded by _should_include)
    num_hidden = draw(st.integers(min_value=0, max_value=3))
    for _ in range(num_hidden):
        name = draw(_hidden_name)
        lower = name.lower()
        if lower not in seen_names:
            seen_names.add(lower)
            items.append({"name": name, "type": "file"})

    # Optionally add .project.json (should be included despite being hidden)
    if draw(st.booleans()) and ".project.json" not in seen_names:
        seen_names.add(".project.json")
        items.append({"name": ".project.json", "type": "file"})

    # Directories with optional children
    num_dirs = draw(st.integers(min_value=0, max_value=4))
    for _ in range(num_dirs):
        name = draw(_visible_name)
        lower = name.lower()
        if lower not in seen_names:
            seen_names.add(lower)
            child_items: list[dict] = []
            child_seen: set[str] = set()
            num_children = draw(st.integers(min_value=0, max_value=4))
            for _ in range(num_children):
                child_name = draw(_visible_name)
                child_lower = child_name.lower()
                if child_lower not in child_seen:
                    child_seen.add(child_lower)
                    child_type = draw(st.sampled_from(["file", "directory"]))
                    child_items.append({"name": child_name, "type": child_type})
            items.append({"name": name, "type": "directory", "children": child_items})

    # Hidden directories (should be excluded)
    num_hidden_dirs = draw(st.integers(min_value=0, max_value=2))
    for _ in range(num_hidden_dirs):
        name = draw(_hidden_name)
        lower = name.lower()
        if lower not in seen_names:
            seen_names.add(lower)
            items.append({"name": name, "type": "directory", "children": []})

    return items


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def materialize_tree(root: Path, tree: list[dict]) -> None:
    """Create actual files and directories on disk from a tree description."""
    for item in tree:
        item_path = root / item["name"]
        if item["type"] == "directory":
            item_path.mkdir(parents=True, exist_ok=True)
            children = item.get("children", [])
            for child in children:
                child_path = item_path / child["name"]
                if child["type"] == "directory":
                    child_path.mkdir(parents=True, exist_ok=True)
                else:
                    child_path.write_text(f"content of {child['name']}")
        else:
            item_path.write_text(f"content of {item['name']}")


def collect_all_nodes(tree: list[dict]) -> list[dict]:
    """Flatten a nested tree response into a list of all nodes."""
    result: list[dict] = []
    for node in tree:
        result.append(node)
        if node.get("children"):
            result.extend(collect_all_nodes(node["children"]))
    return result


# ---------------------------------------------------------------------------
# Property Tests
# ---------------------------------------------------------------------------


class TestTreeEndpointStructure:
    """Property: Tree endpoint returns valid nested JSON structure.

    **Feature: swarmws-explorer-ux**

    **Validates: Requirements 10.1, 15.1**
    """

    @given(tree=_filesystem_tree())
    @PROPERTY_SETTINGS
    def test_every_node_has_required_fields(
        self,
        tmp_path: Path,
        tree: list[dict],
    ):
        """Every node in the response has name, path, type,
        and children fields.

        **Validates: Requirements 10.1, 15.1**
        """
        workspace = tmp_path / str(uuid4())
        workspace.mkdir()
        materialize_tree(workspace, tree)

        result = _build_tree(workspace, workspace, depth=3)
        all_nodes = collect_all_nodes(result)

        for node in all_nodes:
            assert "name" in node, f"Node missing 'name': {node}"
            assert "path" in node, f"Node missing 'path': {node}"
            assert "type" in node, f"Node missing 'type': {node}"
            assert "children" in node, f"Node missing 'children': {node}"
            assert node["type"] in ("file", "directory"), (
                f"Invalid type '{node['type']}' for node {node['name']}"
            )

    @given(tree=_filesystem_tree())
    @PROPERTY_SETTINGS
    def test_directories_have_children_list_files_have_none(
        self,
        tmp_path: Path,
        tree: list[dict],
    ):
        """Directories have a children list (possibly empty); files have
        children = None.

        **Validates: Requirements 10.1, 15.1**
        """
        workspace = tmp_path / str(uuid4())
        workspace.mkdir()
        materialize_tree(workspace, tree)

        result = _build_tree(workspace, workspace, depth=3)
        all_nodes = collect_all_nodes(result)

        for node in all_nodes:
            if node["type"] == "directory":
                assert isinstance(node["children"], list), (
                    f"Directory '{node['name']}' should have children list, "
                    f"got {type(node['children'])}"
                )
            else:
                assert node["children"] is None, (
                    f"File '{node['name']}' should have children=None, "
                    f"got {node['children']}"
                )

    @given(tree=_filesystem_tree())
    @PROPERTY_SETTINGS
    def test_hidden_dirs_excluded_and_root_files_hidden(
        self,
        tmp_path: Path,
        tree: list[dict],
    ):
        """Hidden dirs (_HIDDEN_DIRS) are excluded at all levels.
        Root-level files are hidden unless they are in _SYSTEM_ITEMS.

        **Validates: Requirements 10.1**
        """
        workspace = tmp_path / str(uuid4())
        workspace.mkdir()
        materialize_tree(workspace, tree)

        result = _build_tree(workspace, workspace, depth=3)
        all_nodes = collect_all_nodes(result)

        hidden_dirs = {"chats", ".git"}
        for node in all_nodes:
            name = node["name"]
            assert name not in hidden_dirs, (
                f"Hidden dir '{name}' should be excluded from the tree"
            )

        # Root-level files must be system items only (others hidden)
        system_items = {".context", ".claude", "config.json", "proactive_state.json"}
        for node in result:
            if node["type"] == "file":
                assert node["name"] in system_items, (
                    f"Root-level file '{node['name']}' should be hidden "
                    f"(only system items allowed at root)"
                )

    @given(tree=_filesystem_tree())
    @PROPERTY_SETTINGS
    def test_sorting_directories_first_then_files_alphabetically(
        self,
        tmp_path: Path,
        tree: list[dict],
    ):
        """Nodes are sorted: directories first (alphabetically), then files
        (alphabetically).

        **Validates: Requirements 10.1**
        """
        workspace = tmp_path / str(uuid4())
        workspace.mkdir()
        materialize_tree(workspace, tree)

        result = _build_tree(workspace, workspace, depth=3)
        self._assert_sorted(result)

    def _assert_sorted(self, nodes: list[dict]) -> None:
        """Recursively verify sorting: dirs first, then files, both alpha."""
        dirs = [n for n in nodes if n["type"] == "directory"]
        files = [n for n in nodes if n["type"] == "file"]

        # All directories should come before all files
        dir_indices = [i for i, n in enumerate(nodes) if n["type"] == "directory"]
        file_indices = [i for i, n in enumerate(nodes) if n["type"] == "file"]
        if dir_indices and file_indices:
            assert max(dir_indices) < min(file_indices), (
                "Directories must come before files. Got order: "
                + ", ".join(f"{n['name']}({n['type']})" for n in nodes)
            )

        # Directories sorted alphabetically (case-insensitive)
        dir_names = [d["name"].lower() for d in dirs]
        assert dir_names == sorted(dir_names), (
            f"Directories not sorted alphabetically: {[d['name'] for d in dirs]}"
        )

        # Files sorted alphabetically (case-insensitive)
        file_names = [f["name"].lower() for f in files]
        assert file_names == sorted(file_names), (
            f"Files not sorted alphabetically: {[f['name'] for f in files]}"
        )

        # Recurse into directory children
        for d in dirs:
            if d.get("children"):
                self._assert_sorted(d["children"])

    @given(tree=_filesystem_tree())
    @PROPERTY_SETTINGS
    def test_paths_are_relative_to_workspace_root(
        self,
        tmp_path: Path,
        tree: list[dict],
    ):
        """All node paths are relative (no leading slash, no absolute path)
        and use forward slashes.

        **Validates: Requirements 10.1, 15.1**
        """
        workspace = tmp_path / str(uuid4())
        workspace.mkdir()
        materialize_tree(workspace, tree)

        result = _build_tree(workspace, workspace, depth=3)
        all_nodes = collect_all_nodes(result)

        for node in all_nodes:
            path = node["path"]
            assert not path.startswith("/"), (
                f"Path '{path}' should be relative, not absolute"
            )
            assert "\\" not in path, (
                f"Path '{path}' should use forward slashes"
            )
            # The name should be the last segment of the path
            assert node["name"] == path.split("/")[-1], (
                f"Node name '{node['name']}' doesn't match last segment "
                f"of path '{path}'"
            )

    @given(tree=_filesystem_tree())
    @PROPERTY_SETTINGS
    def test_depth_limiting_respected(
        self,
        tmp_path: Path,
        tree: list[dict],
    ):
        """When depth=1, only top-level entries are returned with no children
        expanded (children is None for directories at the boundary).

        **Validates: Requirements 15.1**
        """
        workspace = tmp_path / str(uuid4())
        workspace.mkdir()
        materialize_tree(workspace, tree)

        result = _build_tree(workspace, workspace, depth=1)

        for node in result:
            if node["type"] == "directory":
                # At depth=1, directories should have children=None
                # because depth-1 = 0 means no further expansion
                assert node["children"] is None, (
                    f"Directory '{node['name']}' at depth=1 should have "
                    f"children=None (depth limit reached)"
                )


class TestHiddenDirsFilter:
    """Unit tests for _HIDDEN_DIRS filtering in _should_include.

    Verifies that internal runtime directories (e.g. ``chats/``) are
    excluded from the workspace tree API response even though they are
    not dotfiles.
    """

    def test_chats_directory_excluded_by_should_include(self):
        """The 'chats' directory name is rejected by _should_include."""
        assert _should_include("chats") is False

    def test_chats_directory_excluded_from_build_tree(self, tmp_path):
        """A 'chats/' directory on disk does not appear in _build_tree output."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "chats").mkdir()
        (workspace / "chats" / "thread-1").mkdir()
        (workspace / "Knowledge").mkdir()

        result = _build_tree(workspace, workspace, depth=3)
        names = [n["name"] for n in result]

        assert "chats" not in names
        assert "Knowledge" in names

    def test_chats_inside_project_also_excluded(self, tmp_path):
        """chats/ inside Projects/{name}/ is also filtered out."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        project = workspace / "Projects" / "MyProject"
        project.mkdir(parents=True)
        (project / "chats").mkdir()
        (project / "instructions.md").write_text("hello")

        result = _build_tree(workspace, workspace, depth=4)
        # Find the project node
        projects_node = next(n for n in result if n["name"] == "Projects")
        project_node = next(
            n for n in projects_node["children"] if n["name"] == "MyProject"
        )
        child_names = [c["name"] for c in project_node["children"]]

        assert "chats" not in child_names
        assert "instructions.md" in child_names

    def test_visible_dirs_not_affected(self):
        """Normal directory names are still included at root."""
        assert _should_include("Knowledge", is_root=True, is_dir=True) is True
        assert _should_include("Projects", is_root=True, is_dir=True) is True
        assert _should_include("Attachments", is_root=True, is_dir=True) is True
        assert _should_include("channel_files", is_root=True, is_dir=True) is True

    def test_git_directory_excluded(self):
        """The '.git' directory is excluded from the tree."""
        assert _should_include(".git") is False

    def test_root_infrastructure_files_hidden(self):
        """Infrastructure files at workspace root are hidden."""
        assert _should_include("swarm.db", is_root=True, is_dir=False) is False
        assert _should_include("hook_stats.json", is_root=True, is_dir=False) is False
        assert _should_include(".DS_Store", is_root=True, is_dir=False) is False
        assert _should_include("CLAUDE.md", is_root=True, is_dir=False) is False
        assert _should_include("AGENTS.md", is_root=True, is_dir=False) is False
        assert _should_include("skill_health.json", is_root=True, is_dir=False) is False

    def test_root_system_items_visible(self):
        """Frontend System section items are visible at root."""
        assert _should_include(".context", is_root=True, is_dir=True) is True
        assert _should_include(".claude", is_root=True, is_dir=True) is True
        assert _should_include("config.json", is_root=True, is_dir=False) is True
        assert _should_include("proactive_state.json", is_root=True, is_dir=False) is True

    def test_root_system_dirs_hidden(self):
        """Non-system infrastructure directories at root are hidden."""
        assert _should_include(".pytest_cache", is_root=True, is_dir=True) is False
        assert _should_include("config-backup", is_root=True, is_dir=True) is False
        assert _should_include("db-export", is_root=True, is_dir=True) is False
        assert _should_include("output", is_root=True, is_dir=True) is False

    def test_non_root_files_visible(self):
        """Files below root are always visible."""
        assert _should_include("README.md", is_root=False, is_dir=False) is True
        assert _should_include(".DS_Store", is_root=False, is_dir=False) is True
        assert _should_include("notes.txt", is_root=False, is_dir=False) is True


# ---------------------------------------------------------------------------
# committed endpoint fail-open (run_46e7b94c)
# GET /workspace/file/committed is a best-effort diff BASELINE. When it cannot
# produce one (path outside home, traversal, or binary/non-UTF-8 content) the
# correct answer is {"content": ""} (no baseline) — the SAME fail-open as the
# untracked / git-error / OSError branches — NOT an HTTP 400 the frontend must
# catch and log as a resource error. The shared _resolve_file_path guard is
# NOT weakened: real read/write endpoints still 400 (AC3).
# ---------------------------------------------------------------------------


def _call_committed(path: str, workspace_root: Path):
    """Drive the async committed endpoint synchronously with a tmp workspace."""
    import asyncio
    import routers.workspace_api as wa

    async def _run():
        orig = wa._get_workspace_path
        wa._get_workspace_path = lambda: _async_return(str(workspace_root))
        try:
            return await wa.get_workspace_file_committed(path=path)
        finally:
            wa._get_workspace_path = orig

    return asyncio.run(_run())


async def _async_return(value):
    return value


def test_committed_binary_file_fails_open(tmp_path: Path, monkeypatch) -> None:
    """A tracked binary/non-UTF-8 file returns {content:''}, not HTTP 400.

    pytest's tmp_path lives under /private/var (outside home), so the shared
    _resolve_file_path home-guard would reject it FIRST and we'd never reach the
    UnicodeDecodeError branch. Bypass ONLY the guard (return the real target) so
    this test genuinely exercises the git-show + decode binary branch.
    """
    import subprocess
    import routers.workspace_api as wa

    def run(*args: str) -> None:
        subprocess.run(list(args), cwd=str(tmp_path), check=True,
                       capture_output=True, text=True)

    run("git", "init", "-q")
    run("git", "config", "user.email", "t@t.t")
    run("git", "config", "user.name", "t")
    # A committed file with invalid UTF-8 bytes (0x80 is a lone continuation byte).
    (tmp_path / "blob.bin").write_bytes(b"\x80\x81\x82 not utf-8")
    run("git", "add", "-A")
    run("git", "commit", "-q", "-m", "add binary")

    blob = (tmp_path / "blob.bin").resolve()
    monkeypatch.setattr(wa, "_resolve_file_path", lambda p, r: (blob, True))

    result = _call_committed(str(blob), tmp_path)
    # fail-open on binary decode, NOT a 400 — but the file IS in HEAD (tracked),
    # so in_head=True keeps its badge "modified", not "new".
    assert result == {"content": "", "in_head": True}


def test_committed_outside_home_fails_open(tmp_path: Path, monkeypatch) -> None:
    """A resolvable path the security guard rejects (outside home) returns
    {content:''} from THIS endpoint — the observed /private/tmp/*.json 400."""
    from fastapi import HTTPException
    import routers.workspace_api as wa

    real_file = tmp_path / "payload.json"
    real_file.write_text('{"k": 1}\n')

    # Force _resolve_file_path to reject as the real outside-home guard would.
    def _reject(path, workspace_root):
        raise HTTPException(status_code=400, detail="Absolute path must be under user home directory")
    monkeypatch.setattr(wa, "_resolve_file_path", _reject)

    result = _call_committed(str(real_file), tmp_path)
    # fail-open, NOT a 400 — resolver rejected before git, so tracked-ness is
    # undetermined → in_head=None (caller renders no badge).
    assert result == {"content": "", "in_head": None}


def test_committed_tracked_utf8_returns_head_content(tmp_path: Path, monkeypatch) -> None:
    """Regression: a normal tracked UTF-8 file still returns its HEAD content.

    tmp_path is outside home; bypass ONLY the home-guard so the real git-show
    path runs (otherwise the fix would fail-open this valid file to '').
    """
    import subprocess
    import routers.workspace_api as wa

    def run(*args: str) -> None:
        subprocess.run(list(args), cwd=str(tmp_path), check=True,
                       capture_output=True, text=True)

    run("git", "init", "-q")
    run("git", "config", "user.email", "t@t.t")
    run("git", "config", "user.name", "t")
    (tmp_path / "hello.txt").write_text("committed-v1\n")
    run("git", "add", "-A")
    run("git", "commit", "-q", "-m", "baseline")
    (tmp_path / "hello.txt").write_text("dirty-v2\n")  # working copy differs

    hello = (tmp_path / "hello.txt").resolve()
    monkeypatch.setattr(wa, "_resolve_file_path", lambda p, r: (hello, True))

    result = _call_committed(str(hello), tmp_path)
    # HEAD version, not working copy; tracked text file → in_head=True.
    assert result == {"content": "committed-v1\n", "in_head": True}


def test_committed_untracked_file_is_in_head_false(tmp_path: Path, monkeypatch) -> None:
    """An untracked file (not in HEAD) returns in_head=False → a 'new' badge.

    This is the discriminator's whole point: distinguish untracked (in_head
    False → 'new') from a tracked binary (in_head True → 'upd'). Both return
    empty content; only in_head tells them apart (run_46e7b94c). Guards the
    Gate-2 MED: keying the badge off content-length mis-labeled both as 'new'.
    """
    import subprocess
    import routers.workspace_api as wa

    def run(*args: str) -> None:
        subprocess.run(list(args), cwd=str(tmp_path), check=True,
                       capture_output=True, text=True)

    run("git", "init", "-q")
    run("git", "config", "user.email", "t@t.t")
    run("git", "config", "user.name", "t")
    (tmp_path / "seed.txt").write_text("seed\n")  # need ≥1 commit so HEAD exists
    run("git", "add", "-A")
    run("git", "commit", "-q", "-m", "baseline")
    (tmp_path / "brand_new.txt").write_text("not committed\n")  # untracked

    newf = (tmp_path / "brand_new.txt").resolve()
    monkeypatch.setattr(wa, "_resolve_file_path", lambda p, r: (newf, True))

    result = _call_committed(str(newf), tmp_path)
    # git show HEAD:brand_new.txt fails (rc!=0) → definitively not in HEAD.
    assert result == {"content": "", "in_head": False}


def test_get_workspace_file_still_rejects_outside_home(tmp_path: Path) -> None:
    """AC3: the SHARED _resolve_file_path guard is NOT weakened — a real
    read endpoint still 400s on an outside-home absolute path."""
    from fastapi import HTTPException
    import routers.workspace_api as wa
    import asyncio

    # /tmp resolves outside /Users/<me> — the guard must still fire.
    outside = "/private/tmp/swarm_test_not_under_home.json"

    async def _run():
        orig = wa._get_workspace_path
        wa._get_workspace_path = lambda: _async_return(str(tmp_path))
        try:
            return await wa.get_workspace_file(path=outside)
        finally:
            wa._get_workspace_path = orig

    with pytest.raises(HTTPException) as exc:
        asyncio.run(_run())
    assert exc.value.status_code == 400  # guard intact


class TestListFilesHidesLockAndTmp:
    """run_419ff7d4 (Debt 3): the workspace file explorer (list_files) must NOT show
    advisory .lock / .tmp sidecars — they clutter the DDD folder beside the real docs
    (IMPROVEMENT.md.lock, .IMPROVEMENT.md.lock, atomic-write .tmp scratch). Real docs
    still appear. Every .lock in the workspace is an advisory sidecar (0 user .lock),
    and .tmp is transient atomic-write scratch."""

    def _seed(self, d: Path):
        (d / "IMPROVEMENT.md").write_text("# doc\n", encoding="utf-8")
        (d / "IMPROVEMENT.md.lock").write_text("", encoding="utf-8")
        (d / ".TECH.md.lock").write_text("", encoding="utf-8")
        (d / "IMPROVEMENT.tmp").write_text("", encoding="utf-8")
        (d / "sub.lock").mkdir()  # a DIRECTORY ending .lock — must NOT be hidden

    def test_managed_workspace_hides_sidecars(self, tmp_path: Path, monkeypatch):
        """base_path=None (managed DDD workspace): *.lock/*.tmp FILES hidden, real .md
        kept, a directory named *.lock kept (Gate-2: files-only filter)."""
        import asyncio
        import routers.workspace as wa
        from schemas.workspace import WorkspaceListRequest
        self._seed(tmp_path)
        # Force the managed-workspace path: base_path=None → root resolves to tmp_path.
        monkeypatch.setattr(wa, "get_workspace_root", lambda agent_id, base_path=None: tmp_path)

        async def _run():
            return await wa.list_files("default", WorkspaceListRequest(path="."), base_path=None)
        names = {f.name for f in asyncio.run(_run()).files}
        assert "IMPROVEMENT.md" in names, "real doc must still be listed"
        assert "IMPROVEMENT.md.lock" not in names, ".lock sidecar must be hidden"
        assert ".TECH.md.lock" not in names, "dot-prefixed .lock must be hidden"
        assert "IMPROVEMENT.tmp" not in names, ".tmp scratch must be hidden"
        assert "sub.lock" in names, "a DIRECTORY named *.lock must NOT be hidden (files-only)"

    def test_work_in_a_folder_keeps_lockfiles(self, tmp_path: Path):
        """base_path set (work-in-a-folder on an arbitrary repo): *.lock is legit user
        content (Cargo.lock, uv.lock) → must NOT be filtered (Gate-2 low #1)."""
        import asyncio
        from routers.workspace import list_files
        from schemas.workspace import WorkspaceListRequest
        (tmp_path / "Cargo.lock").write_text("", encoding="utf-8")
        (tmp_path / "main.rs").write_text("fn main(){}\n", encoding="utf-8")

        resp = asyncio.run(
            list_files("default", WorkspaceListRequest(path="."), base_path=str(tmp_path))
        )
        names = {f.name for f in resp.files}
        assert "Cargo.lock" in names, "work-in-a-folder must NOT hide a real Cargo.lock"
        assert "main.rs" in names
