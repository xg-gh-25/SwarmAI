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
from hypothesis import given, strategies as st, settings, HealthCheck

from routers.workspace_api import _build_tree, _should_include


PROPERTY_SETTINGS = settings(
    max_examples=100,
    
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


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
    def test_hidden_dirs_excluded_but_dotfiles_shown(
        self,
        tmp_path: Path,
        tree: list[dict],
    ):
        """All dot-files and dot-directories are shown in the tree (like Kiro IDE),
        except entries in _HIDDEN_DIRS (.git, chats) which are excluded.

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
        (workspace / "visible.md").write_text("hello")

        result = _build_tree(workspace, workspace, depth=3)
        names = [n["name"] for n in result]

        assert "chats" not in names
        assert "Knowledge" in names
        assert "visible.md" in names

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
        """Normal directory names are still included."""
        assert _should_include("Knowledge") is True
        assert _should_include("Projects") is True
        assert _should_include("reports") is True
        assert _should_include("research") is True

    def test_git_directory_excluded(self):
        """The '.git' directory is excluded from the tree."""
        assert _should_include(".git") is False

    def test_dotfiles_are_visible(self):
        """Dot-files and dot-directories are shown (like Kiro IDE)."""
        assert _should_include(".context") is True
        assert _should_include(".gitignore") is True
        assert _should_include(".project.json") is True
        assert _should_include(".claude") is True
        assert _should_include(".env") is True
