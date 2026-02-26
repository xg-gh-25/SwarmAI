"""Unit tests for the ContextAssembler layer loading methods.

Tests the 8-layer context assembly engine defined in
``backend/core/context_assembler.py``, focusing on the individual layer
loaders implemented in Task 3.3 and the 3-stage progressive truncation
and assembly orchestration implemented in Task 3.4:

- ``_load_layer_1_system_prompt``   — Reads system-prompts.md
- ``_load_layer_2_live_work``       — Loads chat thread, tasks, todos from DB
- ``_summarize_layer_2``            — Bounded summary within token limit
- ``_load_layer_3_instructions``    — Reads project instructions.md
- ``_load_layer_4_project_semantic``— L0 tag-based filter then L1 load
- ``_load_layer_5_knowledge_semantic`` — Knowledge L0/L1 semantic loading
- ``_load_layer_6_memory``          — Loads all .md from Knowledge/Memory/
- ``_load_layer_7_workspace_semantic`` — Workspace L0/L1 semantic loading
- ``_load_layer_8_scoped_retrieval``— Placeholder returning None
- ``_truncate_within_layer``        — Stage 1: keep headers + top N tokens
- ``_remove_snippets_from_layer``   — Stage 2: remove least important sections
- ``_enforce_token_budget``         — 3-stage progressive truncation
- ``_build_truncation_summary``     — Human-readable truncation summary
- ``assemble``                      — Full assembly orchestration

Testing methodology: unit tests with filesystem fixtures (tmp_path) and
mocked DB access for Layer 2.

Key invariants verified:
- Each loader returns Optional[ContextLayer] with correct layer_number
- Missing/empty files return None gracefully
- source_path is always workspace-relative
- Layer 2 content is bounded to LAYER_2_TOKEN_LIMIT
- Memory files are loaded in sorted order (determinism)
- L0 tag-based filtering gates L1 loading correctly
- Truncation preserves headers and respects stage ordering
- Budget enforcement never exceeds configured token budget
- Layers 1-2 are never dropped (stage 3 protection)
- Truncation summary is non-empty when truncation occurs
- Assembly is deterministic: same inputs → identical output
- Stable project pathing: project_id (UUID) resolves correctly after rename
"""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from core.context_assembler import (
    AssembledContext,
    ContextAssembler,
    ContextLayer,
    TruncationInfo,
    LAYER_SYSTEM_PROMPT,
    LAYER_LIVE_WORK,
    LAYER_PROJECT_INSTRUCTIONS,
    LAYER_PROJECT_SEMANTIC,
    LAYER_KNOWLEDGE_SEMANTIC,
    LAYER_MEMORY,
    LAYER_WORKSPACE_SEMANTIC,
    LAYER_SCOPED_RETRIEVAL,
    LAYER_2_TOKEN_LIMIT,
    DEFAULT_TOKEN_BUDGET,
)


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def ws_path(tmp_path: Path) -> Path:
    """Create a minimal SwarmWS workspace directory."""
    return tmp_path


@pytest.fixture
def assembler(ws_path: Path) -> ContextAssembler:
    """Create a ContextAssembler with the test workspace."""
    return ContextAssembler(str(ws_path))


# ── Layer 1: System Prompt ─────────────────────────────────────────────


class TestLoadLayer1SystemPrompt:
    """Tests for _load_layer_1_system_prompt."""

    def test_loads_system_prompt(self, assembler: ContextAssembler, ws_path: Path):
        (ws_path / "system-prompts.md").write_text("You are a helpful assistant.", encoding="utf-8")
        result = asyncio.get_event_loop().run_until_complete(
            assembler._load_layer_1_system_prompt()
        )
        assert result is not None
        assert result.layer_number == LAYER_SYSTEM_PROMPT
        assert result.name == "System Prompt"
        assert "helpful assistant" in result.content
        assert result.token_count > 0
        # source_path should be workspace-relative
        assert not result.source_path.startswith("/")
        assert result.source_path == "system-prompts.md"

    def test_returns_none_when_missing(self, assembler: ContextAssembler):
        result = asyncio.get_event_loop().run_until_complete(
            assembler._load_layer_1_system_prompt()
        )
        assert result is None

    def test_returns_none_when_empty(self, assembler: ContextAssembler, ws_path: Path):
        (ws_path / "system-prompts.md").write_text("   \n  ", encoding="utf-8")
        result = asyncio.get_event_loop().run_until_complete(
            assembler._load_layer_1_system_prompt()
        )
        assert result is None


# ── Layer 2: Live Work Context ─────────────────────────────────────────


class TestLoadLayer2LiveWork:
    """Tests for _load_layer_2_live_work and _summarize_layer_2."""

    def test_returns_none_when_no_thread_id(self, assembler: ContextAssembler):
        result = asyncio.get_event_loop().run_until_complete(
            assembler._load_layer_2_live_work("proj-1", None)
        )
        assert result is None

    @patch("database.db")
    def test_returns_none_when_thread_not_found(self, mock_db, assembler: ContextAssembler):
        mock_db.chat_threads.get = AsyncMock(return_value=None)
        result = asyncio.get_event_loop().run_until_complete(
            assembler._load_layer_2_live_work("proj-1", "thread-999")
        )
        assert result is None

    @patch("database.db")
    def test_loads_thread_with_task_and_todo(self, mock_db, assembler: ContextAssembler):
        mock_db.chat_threads.get = AsyncMock(return_value={
            "id": "t1", "title": "Test Thread", "mode": "explore",
            "task_id": "task-1", "todo_id": "todo-1",
        })
        mock_db.tasks.get = AsyncMock(return_value={
            "id": "task-1", "title": "Build API", "status": "wip",
        })
        mock_db.todos.get = AsyncMock(return_value={
            "id": "todo-1", "title": "Review PR", "status": "pending",
        })
        mock_db.chat_messages.list_by_thread = AsyncMock(return_value=[
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ])

        result = asyncio.get_event_loop().run_until_complete(
            assembler._load_layer_2_live_work("proj-1", "t1")
        )
        assert result is not None
        assert result.layer_number == LAYER_LIVE_WORK
        assert "Test Thread" in result.content
        assert "Build API" in result.content
        assert "Review PR" in result.content
        assert "Hello" in result.content
        assert "Hi there" in result.content

    @patch("database.db")
    def test_layer_2_no_bound_task_or_todo(self, mock_db, assembler: ContextAssembler):
        mock_db.chat_threads.get = AsyncMock(return_value={
            "id": "t1", "title": "General Chat", "mode": "explore",
            "task_id": None, "todo_id": None,
        })
        mock_db.chat_messages.list_by_thread = AsyncMock(return_value=[
            {"role": "user", "content": "What's up?"},
        ])

        result = asyncio.get_event_loop().run_until_complete(
            assembler._load_layer_2_live_work("proj-1", "t1")
        )
        assert result is not None
        assert "General Chat" in result.content
        assert "Bound Tasks" not in result.content

    def test_summarize_layer_2_bounded(self, assembler: ContextAssembler):
        """Verify _summarize_layer_2 stays within LAYER_2_TOKEN_LIMIT."""
        # Create a thread with many long messages to exceed the limit
        long_msg = "word " * 500  # ~500 words → ~667 tokens
        thread_data = {
            "title": "Big Thread",
            "mode": "execute",
            "_messages": [
                {"role": "user", "content": long_msg},
                {"role": "assistant", "content": long_msg},
                {"role": "user", "content": long_msg},
            ],
        }
        tasks = [{"title": "Task A", "status": "wip"}]
        todos = [{"title": "Todo B", "status": "pending"}]

        content = assembler._summarize_layer_2(thread_data, tasks, todos)
        token_count = ContextAssembler.estimate_tokens(content)
        assert token_count <= LAYER_2_TOKEN_LIMIT

    def test_summarize_layer_2_includes_title_and_last_user_msg(self, assembler: ContextAssembler):
        thread_data = {
            "title": "My Thread",
            "mode": "explore",
            "_messages": [
                {"role": "user", "content": "First message"},
                {"role": "assistant", "content": "Response"},
                {"role": "user", "content": "Second message"},
            ],
        }
        content = assembler._summarize_layer_2(thread_data, [], [])
        assert "My Thread" in content
        assert "Second message" in content

    def test_summarize_layer_2_older_messages_summary(self, assembler: ContextAssembler):
        """When messages exceed LAYER_2_MAX_MESSAGES, older ones are summarized."""
        messages = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"Message {i}"}
            for i in range(20)
        ]
        thread_data = {
            "title": "Long Thread",
            "mode": "explore",
            "_messages": messages,
        }
        content = assembler._summarize_layer_2(thread_data, [], [])
        assert "earlier message(s) summarized" in content


# ── Layer 3: Instructions ──────────────────────────────────────────────


class TestLoadLayer3Instructions:
    """Tests for _load_layer_3_instructions."""

    def test_loads_instructions(self, assembler: ContextAssembler, ws_path: Path):
        project_dir = ws_path / "Projects" / "proj-1"
        project_dir.mkdir(parents=True)
        (project_dir / "instructions.md").write_text("# Build a REST API\nUse FastAPI.", encoding="utf-8")

        result = asyncio.get_event_loop().run_until_complete(
            assembler._load_layer_3_instructions(project_dir)
        )
        assert result is not None
        assert result.layer_number == LAYER_PROJECT_INSTRUCTIONS
        assert "REST API" in result.content
        assert not result.source_path.startswith("/")

    def test_returns_none_when_missing(self, assembler: ContextAssembler, ws_path: Path):
        project_dir = ws_path / "Projects" / "proj-1"
        project_dir.mkdir(parents=True)
        result = asyncio.get_event_loop().run_until_complete(
            assembler._load_layer_3_instructions(project_dir)
        )
        assert result is None

    def test_returns_none_when_empty(self, assembler: ContextAssembler, ws_path: Path):
        project_dir = ws_path / "Projects" / "proj-1"
        project_dir.mkdir(parents=True)
        (project_dir / "instructions.md").write_text("  \n  ", encoding="utf-8")
        result = asyncio.get_event_loop().run_until_complete(
            assembler._load_layer_3_instructions(project_dir)
        )
        assert result is None


# ── Layer 4: Project Semantic ──────────────────────────────────────────


class TestLoadLayer4ProjectSemantic:
    """Tests for _load_layer_4_project_semantic."""

    def test_loads_when_tags_overlap(self, assembler: ContextAssembler, ws_path: Path):
        project_dir = ws_path / "Projects" / "proj-1"
        project_dir.mkdir(parents=True)
        (project_dir / "context-L0.md").write_text(
            "---\ntags: [python, fastapi]\nactive_domains: [backend]\n---\n# Abstract",
            encoding="utf-8",
        )
        (project_dir / "context-L1.md").write_text("# Detailed context", encoding="utf-8")

        result = asyncio.get_event_loop().run_until_complete(
            assembler._load_layer_4_project_semantic(project_dir, {"python", "testing"})
        )
        assert result is not None
        assert result.layer_number == LAYER_PROJECT_SEMANTIC
        assert "Abstract" in result.content
        assert "Detailed context" in result.content

    def test_skips_when_tags_disjoint(self, assembler: ContextAssembler, ws_path: Path):
        project_dir = ws_path / "Projects" / "proj-1"
        project_dir.mkdir(parents=True)
        (project_dir / "context-L0.md").write_text(
            "---\ntags: [java, spring]\nactive_domains: [frontend]\n---\n# Abstract",
            encoding="utf-8",
        )
        (project_dir / "context-L1.md").write_text("# Detailed context", encoding="utf-8")

        result = asyncio.get_event_loop().run_until_complete(
            assembler._load_layer_4_project_semantic(project_dir, {"python", "testing"})
        )
        assert result is None

    def test_returns_none_when_l0_missing(self, assembler: ContextAssembler, ws_path: Path):
        project_dir = ws_path / "Projects" / "proj-1"
        project_dir.mkdir(parents=True)
        result = asyncio.get_event_loop().run_until_complete(
            assembler._load_layer_4_project_semantic(project_dir, {"python"})
        )
        assert result is None

    def test_loads_l0_only_when_l1_missing(self, assembler: ContextAssembler, ws_path: Path):
        project_dir = ws_path / "Projects" / "proj-1"
        project_dir.mkdir(parents=True)
        (project_dir / "context-L0.md").write_text(
            "---\ntags: [python]\n---\n# Abstract only",
            encoding="utf-8",
        )
        result = asyncio.get_event_loop().run_until_complete(
            assembler._load_layer_4_project_semantic(project_dir, {"python"})
        )
        assert result is not None
        assert "Abstract only" in result.content


# ── Layer 5: Knowledge Semantic ────────────────────────────────────────


class TestLoadLayer5KnowledgeSemantic:
    """Tests for _load_layer_5_knowledge_semantic."""

    def test_loads_when_tags_overlap(self, assembler: ContextAssembler, ws_path: Path):
        knowledge_dir = ws_path / "Knowledge"
        knowledge_dir.mkdir(parents=True)
        (knowledge_dir / "context-L0.md").write_text(
            "---\ntags: [architecture, patterns]\n---\n# Knowledge abstract",
            encoding="utf-8",
        )
        (knowledge_dir / "context-L1.md").write_text("# Knowledge details", encoding="utf-8")

        result = asyncio.get_event_loop().run_until_complete(
            assembler._load_layer_5_knowledge_semantic({"architecture", "design"})
        )
        assert result is not None
        assert result.layer_number == LAYER_KNOWLEDGE_SEMANTIC
        assert "Knowledge abstract" in result.content
        assert "Knowledge details" in result.content

    def test_skips_when_no_overlap(self, assembler: ContextAssembler, ws_path: Path):
        knowledge_dir = ws_path / "Knowledge"
        knowledge_dir.mkdir(parents=True)
        (knowledge_dir / "context-L0.md").write_text(
            "---\ntags: [devops, ci-cd]\n---\n# Abstract",
            encoding="utf-8",
        )
        result = asyncio.get_event_loop().run_until_complete(
            assembler._load_layer_5_knowledge_semantic({"python", "testing"})
        )
        assert result is None

    def test_returns_none_when_knowledge_dir_missing(self, assembler: ContextAssembler):
        result = asyncio.get_event_loop().run_until_complete(
            assembler._load_layer_5_knowledge_semantic({"python"})
        )
        assert result is None


# ── Layer 6: Memory ────────────────────────────────────────────────────


class TestLoadLayer6Memory:
    """Tests for _load_layer_6_memory."""

    def test_loads_md_files_sorted(self, assembler: ContextAssembler, ws_path: Path):
        memory_dir = ws_path / "Knowledge" / "Memory"
        memory_dir.mkdir(parents=True)
        (memory_dir / "b_preferences.md").write_text("Prefers dark mode", encoding="utf-8")
        (memory_dir / "a_patterns.md").write_text("Uses TDD approach", encoding="utf-8")
        (memory_dir / "c_history.md").write_text("Worked on API project", encoding="utf-8")

        result = asyncio.get_event_loop().run_until_complete(
            assembler._load_layer_6_memory()
        )
        assert result is not None
        assert result.layer_number == LAYER_MEMORY
        assert result.name == "Persistent Memory"
        # Verify sorted order: a_ before b_ before c_
        a_pos = result.content.index("TDD approach")
        b_pos = result.content.index("dark mode")
        c_pos = result.content.index("API project")
        assert a_pos < b_pos < c_pos

    def test_returns_none_when_dir_missing(self, assembler: ContextAssembler):
        result = asyncio.get_event_loop().run_until_complete(
            assembler._load_layer_6_memory()
        )
        assert result is None

    def test_returns_none_when_no_md_files(self, assembler: ContextAssembler, ws_path: Path):
        memory_dir = ws_path / "Knowledge" / "Memory"
        memory_dir.mkdir(parents=True)
        (memory_dir / "notes.txt").write_text("Not a markdown file", encoding="utf-8")
        result = asyncio.get_event_loop().run_until_complete(
            assembler._load_layer_6_memory()
        )
        assert result is None

    def test_skips_empty_md_files(self, assembler: ContextAssembler, ws_path: Path):
        memory_dir = ws_path / "Knowledge" / "Memory"
        memory_dir.mkdir(parents=True)
        (memory_dir / "empty.md").write_text("  \n  ", encoding="utf-8")
        (memory_dir / "real.md").write_text("Real content", encoding="utf-8")
        result = asyncio.get_event_loop().run_until_complete(
            assembler._load_layer_6_memory()
        )
        assert result is not None
        assert "Real content" in result.content

    def test_workspace_relative_path(self, assembler: ContextAssembler, ws_path: Path):
        memory_dir = ws_path / "Knowledge" / "Memory"
        memory_dir.mkdir(parents=True)
        (memory_dir / "mem.md").write_text("Memory content", encoding="utf-8")
        result = asyncio.get_event_loop().run_until_complete(
            assembler._load_layer_6_memory()
        )
        assert result is not None
        assert not result.source_path.startswith("/")


# ── Layer 7: Workspace Semantic ────────────────────────────────────────


class TestLoadLayer7WorkspaceSemantic:
    """Tests for _load_layer_7_workspace_semantic."""

    def test_loads_when_tags_overlap(self, assembler: ContextAssembler, ws_path: Path):
        (ws_path / "context-L0.md").write_text(
            "---\ntags: [workspace, global]\nactive_domains: [all]\n---\n# WS abstract",
            encoding="utf-8",
        )
        (ws_path / "context-L1.md").write_text("# WS details", encoding="utf-8")

        result = asyncio.get_event_loop().run_until_complete(
            assembler._load_layer_7_workspace_semantic({"workspace", "config"})
        )
        assert result is not None
        assert result.layer_number == LAYER_WORKSPACE_SEMANTIC
        assert "WS abstract" in result.content
        assert "WS details" in result.content

    def test_skips_when_no_overlap(self, assembler: ContextAssembler, ws_path: Path):
        (ws_path / "context-L0.md").write_text(
            "---\ntags: [devops]\n---\n# Abstract",
            encoding="utf-8",
        )
        result = asyncio.get_event_loop().run_until_complete(
            assembler._load_layer_7_workspace_semantic({"python"})
        )
        assert result is None

    def test_returns_none_when_l0_missing(self, assembler: ContextAssembler):
        result = asyncio.get_event_loop().run_until_complete(
            assembler._load_layer_7_workspace_semantic({"python"})
        )
        assert result is None


# ── Layer 8: Scoped Retrieval ──────────────────────────────────────────


class TestLoadLayer8ScopedRetrieval:
    """Tests for _load_layer_8_scoped_retrieval (placeholder)."""

    def test_returns_none(self, assembler: ContextAssembler):
        result = asyncio.get_event_loop().run_until_complete(
            assembler._load_layer_8_scoped_retrieval("proj-1")
        )
        assert result is None


# ── Path Safety ────────────────────────────────────────────────────────


class TestPathSafety:
    """Verify all layers produce workspace-relative source_path values."""

    def test_layer_1_relative_path(self, assembler: ContextAssembler, ws_path: Path):
        (ws_path / "system-prompts.md").write_text("Prompt content", encoding="utf-8")
        result = asyncio.get_event_loop().run_until_complete(
            assembler._load_layer_1_system_prompt()
        )
        assert result is not None
        assert not Path(result.source_path).is_absolute()

    def test_layer_3_relative_path(self, assembler: ContextAssembler, ws_path: Path):
        project_dir = ws_path / "Projects" / "proj-1"
        project_dir.mkdir(parents=True)
        (project_dir / "instructions.md").write_text("Instructions", encoding="utf-8")
        result = asyncio.get_event_loop().run_until_complete(
            assembler._load_layer_3_instructions(project_dir)
        )
        assert result is not None
        assert not Path(result.source_path).is_absolute()

    def test_layer_6_relative_path(self, assembler: ContextAssembler, ws_path: Path):
        memory_dir = ws_path / "Knowledge" / "Memory"
        memory_dir.mkdir(parents=True)
        (memory_dir / "mem.md").write_text("Memory", encoding="utf-8")
        result = asyncio.get_event_loop().run_until_complete(
            assembler._load_layer_6_memory()
        )
        assert result is not None
        assert not Path(result.source_path).is_absolute()


# ── Stage 1: Truncate Within Layer ─────────────────────────────────────


class TestTruncateWithinLayer:
    """Tests for _truncate_within_layer (Stage 1 truncation)."""

    def test_preserves_headers(self, assembler: ContextAssembler):
        """Headers (lines starting with #) are always preserved."""
        content = "# Main Title\nSome content here with many words.\n## Section\nMore content."
        layer = ContextLayer(
            layer_number=6, name="Memory", source_path="Knowledge/Memory",
            content=content, token_count=assembler.estimate_tokens(content),
        )
        result = assembler._truncate_within_layer(layer, target_tokens=5)
        assert "# Main Title" in result.content
        assert "## Section" in result.content
        assert result.truncated is True
        assert result.truncation_stage == 1

    def test_no_truncation_when_within_target(self, assembler: ContextAssembler):
        """Layer returned as-is when already within target."""
        content = "Short content."
        layer = ContextLayer(
            layer_number=3, name="Instructions", source_path="instructions.md",
            content=content, token_count=assembler.estimate_tokens(content),
        )
        result = assembler._truncate_within_layer(layer, target_tokens=100)
        assert result.content == content
        assert result.truncated is False

    def test_reduces_token_count(self, assembler: ContextAssembler):
        """Truncation reduces token count toward target."""
        content = "# Header\n" + " ".join(["word"] * 200)
        layer = ContextLayer(
            layer_number=7, name="Workspace", source_path="context-L0.md",
            content=content, token_count=assembler.estimate_tokens(content),
        )
        result = assembler._truncate_within_layer(layer, target_tokens=50)
        assert result.token_count < layer.token_count

    def test_content_only_headers(self, assembler: ContextAssembler):
        """When content is only headers, they are preserved."""
        content = "# Title\n## Subtitle\n### Sub-sub"
        layer = ContextLayer(
            layer_number=4, name="Project", source_path="context-L0.md",
            content=content, token_count=assembler.estimate_tokens(content),
        )
        result = assembler._truncate_within_layer(layer, target_tokens=2)
        assert "# Title" in result.content


# ── Stage 2: Remove Snippets ──────────────────────────────────────────


class TestRemoveSnippetsFromLayer:
    """Tests for _remove_snippets_from_layer (Stage 2 truncation)."""

    def test_keeps_first_section_for_non_memory(self, assembler: ContextAssembler):
        """Non-memory layers keep the first section, drop subsequent."""
        content = "## Section 1\nFirst content.\n## Section 2\nSecond content.\n## Section 3\nThird."
        layer = ContextLayer(
            layer_number=4, name="Project Semantic", source_path="context-L0.md",
            content=content, token_count=assembler.estimate_tokens(content),
        )
        result = assembler._remove_snippets_from_layer(layer, target_tokens=5)
        assert "Section 1" in result.content
        assert "Section 3" not in result.content
        assert result.truncated is True
        assert result.truncation_stage == 2

    def test_memory_keeps_most_recent(self, assembler: ContextAssembler):
        """Memory (layer 6) keeps most recent sections, drops oldest."""
        content = "## Old Memory\nOld stuff.\n## Recent Memory\nRecent stuff."
        layer = ContextLayer(
            layer_number=LAYER_MEMORY, name="Memory", source_path="Knowledge/Memory",
            content=content, token_count=assembler.estimate_tokens(content),
        )
        result = assembler._remove_snippets_from_layer(layer, target_tokens=5)
        assert "Recent Memory" in result.content
        assert result.truncated is True

    def test_single_section_returns_as_is(self, assembler: ContextAssembler):
        """Single-section content can't be snippet-removed."""
        content = "Just a single block of content with no markdown headers."
        layer = ContextLayer(
            layer_number=5, name="Knowledge", source_path="Knowledge/context-L0.md",
            content=content, token_count=assembler.estimate_tokens(content),
        )
        result = assembler._remove_snippets_from_layer(layer, target_tokens=2)
        # Can't remove snippets from single section — returned as-is
        assert result.content == content

    def test_no_truncation_when_within_target(self, assembler: ContextAssembler):
        """Layer returned as-is when already within target."""
        content = "## Section\nShort."
        layer = ContextLayer(
            layer_number=4, name="Project", source_path="context-L0.md",
            content=content, token_count=assembler.estimate_tokens(content),
        )
        result = assembler._remove_snippets_from_layer(layer, target_tokens=100)
        assert result.content == content


# ── Budget Enforcement ─────────────────────────────────────────────────


class TestEnforceTokenBudget:
    """Tests for _enforce_token_budget (3-stage progressive truncation)."""

    def test_within_budget_no_truncation(self, assembler: ContextAssembler):
        """When total is within budget, no truncation occurs."""
        layers = [
            ContextLayer(1, "System", "system.md", "Short prompt.", 3),
            ContextLayer(3, "Instructions", "inst.md", "Short instructions.", 3),
        ]
        result = assembler._enforce_token_budget(layers)
        assert result.budget_exceeded is False
        assert len(result.truncation_log) == 0
        assert result.total_token_count == 6
        assert len(result.layers) == 2

    def test_truncation_starts_from_highest_layer(self):
        """Truncation proceeds from layer 8 downward."""
        assembler = ContextAssembler("/tmp/ws", token_budget=50)
        layers = [
            ContextLayer(1, "System", "system.md", " ".join(["word"] * 20), 27),
            ContextLayer(6, "Memory", "mem", " ".join(["word"] * 20), 27),
            ContextLayer(8, "Retrieval", "ret", " ".join(["word"] * 20), 27),
        ]
        result = assembler._enforce_token_budget(layers)
        assert result.budget_exceeded is True
        # Layer 8 should be truncated first
        assert any(t.layer_number == 8 for t in result.truncation_log)

    def test_layers_1_2_never_dropped(self):
        """Stage 3 (drop) is never applied to layers 1 and 2."""
        assembler = ContextAssembler("/tmp/ws", token_budget=10)
        layers = [
            ContextLayer(1, "System", "system.md", " ".join(["word"] * 50), 67),
            ContextLayer(2, "Live Work", "thread/1", " ".join(["word"] * 50), 67),
            ContextLayer(6, "Memory", "mem", " ".join(["word"] * 50), 67),
        ]
        result = assembler._enforce_token_budget(layers)
        # Layers 1 and 2 should still be present (not dropped)
        layer_numbers = [l.layer_number for l in result.layers]
        assert 1 in layer_numbers
        assert 2 in layer_numbers
        # No stage-3 truncation on layers 1 or 2
        for t in result.truncation_log:
            if t.layer_number <= 2:
                assert t.stage != 3

    def test_budget_exceeded_flag(self):
        """budget_exceeded is True when any truncation occurs."""
        assembler = ContextAssembler("/tmp/ws", token_budget=5)
        layers = [
            ContextLayer(1, "System", "system.md", " ".join(["word"] * 30), 40),
        ]
        result = assembler._enforce_token_budget(layers)
        assert result.budget_exceeded is True
        assert len(result.truncation_log) > 0

    def test_total_token_count_after_truncation(self):
        """total_token_count reflects post-truncation state."""
        assembler = ContextAssembler("/tmp/ws", token_budget=30)
        layers = [
            ContextLayer(1, "System", "system.md", " ".join(["word"] * 10), 14),
            ContextLayer(7, "Workspace", "ws.md", " ".join(["word"] * 30), 40),
        ]
        result = assembler._enforce_token_budget(layers)
        actual_total = sum(l.token_count for l in result.layers)
        assert result.total_token_count == actual_total

    def test_droppable_layer_removed_from_output(self):
        """Dropped layers (stage 3) are removed from the final layers list."""
        assembler = ContextAssembler("/tmp/ws", token_budget=15)
        layers = [
            ContextLayer(1, "System", "system.md", "Short.", 2),
            ContextLayer(8, "Retrieval", "ret", " ".join(["word"] * 100), 134),
        ]
        result = assembler._enforce_token_budget(layers)
        layer_numbers = [l.layer_number for l in result.layers]
        # Layer 8 should be dropped since it's huge and droppable
        assert 8 not in layer_numbers or result.layers[-1].token_count < 134

    def test_progressive_stages_applied_in_order(self):
        """Stages are applied 1→2→3 within each layer before moving up."""
        assembler = ContextAssembler("/tmp/ws", token_budget=20)
        content = "## Section 1\n" + " ".join(["word"] * 40) + "\n## Section 2\n" + " ".join(["word"] * 40)
        layers = [
            ContextLayer(1, "System", "system.md", "Prompt.", 2),
            ContextLayer(7, "Workspace", "ws.md", content, 108),
        ]
        result = assembler._enforce_token_budget(layers)
        assert result.budget_exceeded is True
        # Should have truncation entries for layer 7
        layer_7_stages = [t.stage for t in result.truncation_log if t.layer_number == 7]
        # Stages should be in ascending order (1 before 2 before 3)
        assert layer_7_stages == sorted(layer_7_stages)


# ── Truncation Summary ────────────────────────────────────────────────


class TestBuildTruncationSummary:
    """Tests for _build_truncation_summary."""

    def test_empty_log_returns_empty(self, assembler: ContextAssembler):
        """No truncation → empty summary."""
        assert assembler._build_truncation_summary([]) == ""

    def test_single_stage_1_entry(self, assembler: ContextAssembler):
        """Summary describes a single stage-1 truncation."""
        log = [TruncationInfo(stage=1, layer_number=6, original_tokens=800, truncated_tokens=400, reason="test")]
        summary = assembler._build_truncation_summary(log)
        assert "[Context truncated:" in summary
        assert "Memory" in summary
        assert "800→400" in summary
        assert "Use tools to access full content.]" in summary

    def test_stage_3_shows_dropped(self, assembler: ContextAssembler):
        """Stage 3 entries show 'dropped' in summary."""
        log = [TruncationInfo(stage=3, layer_number=8, original_tokens=500, truncated_tokens=0, reason="test")]
        summary = assembler._build_truncation_summary(log)
        assert "dropped" in summary
        assert "Scoped Retrieval" in summary

    def test_multiple_entries(self, assembler: ContextAssembler):
        """Summary includes all affected layers."""
        log = [
            TruncationInfo(stage=1, layer_number=6, original_tokens=800, truncated_tokens=400, reason="test"),
            TruncationInfo(stage=3, layer_number=7, original_tokens=300, truncated_tokens=0, reason="test"),
        ]
        summary = assembler._build_truncation_summary(log)
        assert "Memory" in summary
        assert "Workspace Semantic" in summary


# ── Assembly Orchestration ─────────────────────────────────────────────


class TestAssemble:
    """Tests for the assemble() orchestration method."""

    def test_returns_empty_when_project_not_found(self, assembler: ContextAssembler):
        """Returns empty AssembledContext when project_id doesn't resolve."""
        result = asyncio.get_event_loop().run_until_complete(
            assembler.assemble("nonexistent-project-id")
        )
        assert isinstance(result, AssembledContext)
        assert len(result.layers) == 0
        assert result.total_token_count == 0

    def test_assembles_layers_in_order(self, assembler: ContextAssembler, ws_path: Path):
        """Assembled layers are in ascending layer_number order."""
        # Set up project directory
        project_dir = ws_path / "Projects" / "proj-1"
        project_dir.mkdir(parents=True)
        (project_dir / ".project.json").write_text('{"id": "proj-1"}', encoding="utf-8")

        # Set up system prompt
        (ws_path / "system-prompts.md").write_text("System prompt content.", encoding="utf-8")

        # Set up instructions
        (project_dir / "instructions.md").write_text("Project instructions.", encoding="utf-8")

        # Set up memory
        memory_dir = ws_path / "Knowledge" / "Memory"
        memory_dir.mkdir(parents=True)
        (memory_dir / "mem.md").write_text("Memory content.", encoding="utf-8")

        result = asyncio.get_event_loop().run_until_complete(
            assembler.assemble("proj-1")
        )
        assert len(result.layers) > 0
        layer_numbers = [l.layer_number for l in result.layers]
        assert layer_numbers == sorted(layer_numbers)

    def test_budget_enforcement_applied(self, ws_path: Path):
        """Assembly enforces token budget when layers exceed it."""
        assembler = ContextAssembler(str(ws_path), token_budget=20)

        project_dir = ws_path / "Projects" / "proj-1"
        project_dir.mkdir(parents=True)
        (project_dir / ".project.json").write_text('{"id": "proj-1"}', encoding="utf-8")

        # Large system prompt
        (ws_path / "system-prompts.md").write_text(
            " ".join(["word"] * 100), encoding="utf-8"
        )

        # Large memory
        memory_dir = ws_path / "Knowledge" / "Memory"
        memory_dir.mkdir(parents=True)
        (memory_dir / "mem.md").write_text(
            " ".join(["word"] * 100), encoding="utf-8"
        )

        result = asyncio.get_event_loop().run_until_complete(
            assembler.assemble("proj-1")
        )
        assert result.budget_exceeded is True
        assert result.total_token_count <= 20

    def test_truncation_summary_injected(self, ws_path: Path):
        """Truncation summary is set when truncation occurs."""
        assembler = ContextAssembler(str(ws_path), token_budget=20)

        project_dir = ws_path / "Projects" / "proj-1"
        project_dir.mkdir(parents=True)
        (project_dir / ".project.json").write_text('{"id": "proj-1"}', encoding="utf-8")

        (ws_path / "system-prompts.md").write_text(
            " ".join(["word"] * 50), encoding="utf-8"
        )
        memory_dir = ws_path / "Knowledge" / "Memory"
        memory_dir.mkdir(parents=True)
        (memory_dir / "mem.md").write_text(
            " ".join(["word"] * 50), encoding="utf-8"
        )

        result = asyncio.get_event_loop().run_until_complete(
            assembler.assemble("proj-1")
        )
        assert result.truncation_summary != ""
        assert "[Context truncated:" in result.truncation_summary

    def test_no_truncation_summary_when_within_budget(self, assembler: ContextAssembler, ws_path: Path):
        """No truncation summary when everything fits within budget."""
        project_dir = ws_path / "Projects" / "proj-1"
        project_dir.mkdir(parents=True)
        (project_dir / ".project.json").write_text('{"id": "proj-1"}', encoding="utf-8")
        (ws_path / "system-prompts.md").write_text("Short.", encoding="utf-8")

        result = asyncio.get_event_loop().run_until_complete(
            assembler.assemble("proj-1")
        )
        assert result.truncation_summary == ""
        assert result.budget_exceeded is False

    def test_deterministic_assembly(self, assembler: ContextAssembler, ws_path: Path):
        """Two calls with identical inputs produce identical output."""
        project_dir = ws_path / "Projects" / "proj-1"
        project_dir.mkdir(parents=True)
        (project_dir / ".project.json").write_text('{"id": "proj-1"}', encoding="utf-8")
        (ws_path / "system-prompts.md").write_text("System prompt.", encoding="utf-8")
        (project_dir / "instructions.md").write_text("Instructions.", encoding="utf-8")

        result1 = asyncio.get_event_loop().run_until_complete(
            assembler.assemble("proj-1")
        )
        result2 = asyncio.get_event_loop().run_until_complete(
            assembler.assemble("proj-1")
        )

        assert len(result1.layers) == len(result2.layers)
        for l1, l2 in zip(result1.layers, result2.layers):
            assert l1.layer_number == l2.layer_number
            assert l1.content == l2.content
            assert l1.token_count == l2.token_count
        assert result1.total_token_count == result2.total_token_count


# ── Property-Based Tests ───────────────────────────────────────────────
#
# Property tests use the ``hypothesis`` library to verify universal
# correctness properties across randomized inputs.  Each test references
# its design-document property with a tag comment.
# ───────────────────────────────────────────────────────────────────────

from hypothesis import given, settings, HealthCheck
import hypothesis.strategies as st


# Strategy: generate a random ContextLayer for a given layer number.
# Content is random text whose length drives the token count.
def _context_layer_strategy(layer_number: int) -> st.SearchStrategy[ContextLayer]:
    """Build a Hypothesis strategy that produces a ``ContextLayer`` for *layer_number*.

    Content is a random ASCII string (1–500 words) so that
    ``ContextAssembler.estimate_tokens`` returns a realistic positive count.
    """
    return st.text(
        alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz "),
        min_size=1,
        max_size=2000,
    ).map(
        lambda content: ContextLayer(
            layer_number=layer_number,
            name=f"Layer {layer_number}",
            source_path=f"layer_{layer_number}.md",
            content=content,
            token_count=ContextAssembler.estimate_tokens(content),
        )
    )


# Strategy: generate a random non-empty subset of layers 1–8, each with
# random content.  Returns a list of ContextLayer objects in arbitrary order.
@st.composite
def random_context_layers(draw: st.DrawFn) -> list[ContextLayer]:
    """Draw a random non-empty subset of layers 1–8 with random content sizes."""
    layer_numbers = draw(
        st.lists(
            st.integers(min_value=1, max_value=8),
            min_size=1,
            max_size=8,
            unique=True,
        )
    )
    layers: list[ContextLayer] = []
    for num in layer_numbers:
        layer = draw(_context_layer_strategy(num))
        layers.append(layer)
    # Shuffle to ensure the assembler sorts correctly regardless of input order
    draw(st.randoms()).shuffle(layers)
    return layers


class TestPropertyContextAssemblyPriorityOrdering:
    """Property 1: Context assembly priority ordering.

    *For any* project with context files at any subset of the 8 layers,
    the assembled context layers SHALL appear in strictly ascending
    ``layer_number`` order.  No layer with a higher number shall precede
    a layer with a lower number in the output list.

    Feature: swarmws-intelligence, Property 1: Context assembly priority ordering

    **Validates: Requirements 16.1**
    """

    @given(layers=random_context_layers())
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_assembled_layers_in_ascending_order(
        self, layers: list[ContextLayer]
    ) -> None:
        """Assembled layers must appear in strictly ascending layer_number order.

        **Validates: Requirements 16.1**

        Creates a ContextAssembler with a dummy workspace path (the method
        under test — ``_enforce_token_budget`` — is a pure function that
        only reads ``self._token_budget`` and does not touch the filesystem).
        """
        assembler = ContextAssembler("/tmp/dummy-ws")
        result: AssembledContext = assembler._enforce_token_budget(layers)

        # All output layers must be in strictly ascending layer_number order
        output_numbers = [layer.layer_number for layer in result.layers]
        for i in range(1, len(output_numbers)):
            assert output_numbers[i] > output_numbers[i - 1], (
                f"Layer ordering violated: layer_number {output_numbers[i]} "
                f"is not greater than {output_numbers[i - 1]}. "
                f"Full order: {output_numbers}"
            )


# ── Strategies for Property 2 ─────────────────────────────────────────


def _lowercase_alpha_tag() -> st.SearchStrategy[str]:
    """Generate a lowercase alphabetic tag string (1–15 chars)."""
    return st.text(
        alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz"),
        min_size=1,
        max_size=15,
    )


def _tag_list() -> st.SearchStrategy[list[str]]:
    """Generate a list of 0–8 unique lowercase tags."""
    return st.lists(_lowercase_alpha_tag(), min_size=0, max_size=8, unique=True)


@st.composite
def l0_with_frontmatter(draw: st.DrawFn) -> tuple[str, set[str]]:
    """Draw L0 content with YAML frontmatter and return (content, all_tags).

    Generates random ``tags`` and ``active_domains`` lists, builds valid
    YAML frontmatter, and appends a body.  Returns the full L0 string
    together with the union of tags + domains as a set.
    """
    tags = draw(_tag_list())
    domains = draw(_tag_list())
    body = draw(st.text(
        alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz "),
        min_size=1,
        max_size=100,
    ))

    # Build YAML frontmatter — quote each value so YAML-reserved words
    # like ``null``, ``true``, ``false`` are treated as strings.
    tags_yaml = "[" + ", ".join(f'"{t}"' for t in tags) + "]" if tags else "[]"
    domains_yaml = "[" + ", ".join(f'"{d}"' for d in domains) + "]" if domains else "[]"
    content = (
        f"---\ntags: {tags_yaml}\nactive_domains: {domains_yaml}\n---\n"
        f"# Abstract\n{body}"
    )

    all_tags = {t.strip().lower() for t in tags + domains if t.strip()}
    return content, all_tags


@st.composite
def live_context_keywords_strategy(draw: st.DrawFn) -> set[str]:
    """Draw a set of 0–10 lowercase keyword strings."""
    kws = draw(st.lists(_lowercase_alpha_tag(), min_size=0, max_size=10, unique=True))
    return {k.lower() for k in kws}


class TestPropertyTagBasedL0FastFilterGating:
    """Property 2: Tag-based L0 fast-filter gating.

    *For any* L0 context file with YAML frontmatter containing ``tags``
    and ``active_domains``, and *for any* set of live context keywords
    extracted from Layer 2, the L1 file SHALL be loaded if and only if
    the intersection of (L0 tags ∪ active_domains) and live context
    keywords is non-empty.  If the L0 file lacks YAML frontmatter, the
    L1 file SHALL be loaded if the L0 content is non-empty and
    non-template (backward compatibility).

    Feature: swarmws-intelligence, Property 2: Tag-based L0 fast-filter gating

    **Validates: Requirements 16.2**
    """

    @given(
        fm=l0_with_frontmatter(),
        keywords=live_context_keywords_strategy(),
    )
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_l1_loaded_iff_tag_intersection_nonempty(
        self,
        fm: tuple[str, set[str]],
        keywords: set[str],
    ) -> None:
        """L1 is loaded iff (L0 tags ∪ active_domains) ∩ keywords ≠ ∅.

        **Validates: Requirements 16.2**
        """
        l0_content, all_tags = fm
        assembler = ContextAssembler("/tmp/dummy-ws")

        result = assembler._is_l0_relevant(l0_content, keywords)
        expected_overlap = all_tags & keywords

        if all_tags:
            # When frontmatter has tags, relevance == non-empty intersection
            assert result == (len(expected_overlap) > 0), (
                f"Tag-based filter mismatch: tags={all_tags}, "
                f"keywords={keywords}, overlap={expected_overlap}, "
                f"result={result}"
            )
        else:
            # Empty tags in frontmatter → _extract_l0_tags returns empty set
            # → falls through to legacy fallback (non-empty, non-template)
            # The content has a body after frontmatter, so legacy check
            # should return True (content is non-empty, non-template)
            assert result is True, (
                "Legacy fallback should return True for non-empty, "
                f"non-template content when tags are empty. "
                f"content preview: {l0_content[:80]!r}"
            )

    @given(
        body=st.text(
            alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz "),
            min_size=5,
            max_size=200,
        ),
        keywords=live_context_keywords_strategy(),
    )
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_legacy_fallback_no_frontmatter(
        self,
        body: str,
        keywords: set[str],
    ) -> None:
        """Without YAML frontmatter, L1 loaded if content is non-empty and non-template.

        **Validates: Requirements 16.2**
        """
        # Content without frontmatter — just a heading + body
        l0_content = f"# Context Abstract\n{body}"
        assembler = ContextAssembler("/tmp/dummy-ws")

        result = assembler._is_l0_relevant(l0_content, keywords)

        # No frontmatter → legacy fallback.  Content has a heading + body
        # text, so it is non-empty and non-template → should be True.
        assert result is True, (
            "Legacy fallback should return True for non-empty, "
            f"non-template content without frontmatter. "
            f"content preview: {l0_content[:80]!r}"
        )

    @given(keywords=live_context_keywords_strategy())
    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_empty_content_returns_false(self, keywords: set[str]) -> None:
        """Empty or whitespace-only L0 content always returns False.

        **Validates: Requirements 16.2**
        """
        assembler = ContextAssembler("/tmp/dummy-ws")

        assert assembler._is_l0_relevant("", keywords) is False
        assert assembler._is_l0_relevant("   \n  ", keywords) is False

    @given(fm=l0_with_frontmatter())
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_extract_l0_tags_matches_frontmatter(
        self, fm: tuple[str, set[str]]
    ) -> None:
        """_extract_l0_tags returns the union of tags + active_domains.

        **Validates: Requirements 16.2**
        """
        l0_content, expected_tags = fm
        assembler = ContextAssembler("/tmp/dummy-ws")

        extracted = assembler._extract_l0_tags(l0_content)
        assert extracted == expected_tags, (
            f"Extracted tags {extracted} != expected {expected_tags}"
        )


class TestPropertyTokenBudgetInvariant:
    """Property 3: Token budget invariant.

    *For any* assembled context with a configured token budget B, the
    ``total_token_count`` of the result SHALL be less than or equal to B.
    This holds regardless of the number of layers, the size of individual
    layer content, or the token budget value.

    Feature: swarmws-intelligence, Property 3: Token budget invariant

    **Validates: Requirements 16.3**
    """

    @given(
        layers=random_context_layers(),
        budget=st.integers(min_value=100, max_value=50_000),
    )
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_total_token_count_never_exceeds_budget(
        self,
        layers: list[ContextLayer],
        budget: int,
    ) -> None:
        """total_token_count <= budget for any random layers and budget.

        **Validates: Requirements 16.3**
        """
        assembler = ContextAssembler("/tmp/dummy-ws", token_budget=budget)
        result: AssembledContext = assembler._enforce_token_budget(layers)

        assert result.total_token_count <= budget, (
            f"Token budget violated: total_token_count={result.total_token_count} "
            f"exceeds budget={budget}. "
            f"Input layers: {[(l.layer_number, l.token_count) for l in layers]}, "
            f"Output layers: {[(l.layer_number, l.token_count) for l in result.layers]}"
        )


# ── Strategies for Property 4 ─────────────────────────────────────────


@st.composite
def oversized_context_layers(draw: st.DrawFn) -> tuple[list[ContextLayer], int]:
    """Draw random layers whose total tokens exceed a chosen budget.

    Returns ``(layers, budget)`` where ``sum(token_counts) > budget``.
    Always includes at least one droppable layer (3–8) so that the
    truncation algorithm can make meaningful progress.  The budget is set
    to at most half the total token count to guarantee truncation occurs.
    """
    # Ensure at least one droppable layer (3-8) is present so truncation
    # can proceed through all stages including stage 3 (drop).
    droppable_num = draw(st.integers(min_value=3, max_value=8))
    # Optionally include additional layers from the full 1-8 range
    extra_nums = draw(
        st.lists(
            st.integers(min_value=1, max_value=8),
            min_size=0,
            max_size=7,
            unique=True,
        )
    )
    all_nums = list({droppable_num} | set(extra_nums))

    layers: list[ContextLayer] = []
    for num in all_nums:
        layer = draw(_context_layer_strategy(num))
        layers.append(layer)

    total = sum(l.token_count for l in layers)
    # Ensure total is large enough to force truncation
    if total < 10:
        big_content = " ".join(["word"] * 200)
        # Inflate a droppable layer (not 1 or 2)
        droppable_idx = next(
            i for i, l in enumerate(layers) if l.layer_number >= 3
        )
        layers[droppable_idx] = ContextLayer(
            layer_number=layers[droppable_idx].layer_number,
            name=layers[droppable_idx].name,
            source_path=layers[droppable_idx].source_path,
            content=big_content,
            token_count=ContextAssembler.estimate_tokens(big_content),
        )
        total = sum(l.token_count for l in layers)

    # Shuffle to ensure the assembler sorts correctly regardless of input order
    draw(st.randoms()).shuffle(layers)

    # Budget is between 1 and total // 2 (guarantees exceeding)
    max_budget = max(1, total // 2)
    budget = draw(st.integers(min_value=1, max_value=max_budget))
    return layers, budget


class TestPropertyProgressiveTruncationRespectsPriorityWithSummary:
    """Property 4: Progressive truncation respects priority and produces summary.

    *For any* context that exceeds the token budget, truncation SHALL
    proceed from layer 8 upward.  Within each layer, truncation SHALL
    progress through stages (1: truncate within layer, 2: remove snippets,
    3: drop layer) before moving to the next higher-priority layer.
    Stage 3 (full drop) SHALL never be applied to layers 1 or 2.  When
    any truncation occurs, the ``truncation_summary`` field SHALL be
    non-empty and describe which layers were affected.

    Feature: swarmws-intelligence, Property 4: Progressive truncation respects priority and produces summary

    **Validates: Requirements 16.4, 16.7**
    """

    @given(data=oversized_context_layers())
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_truncation_order_stages_and_summary(
        self,
        data: tuple[list[ContextLayer], int],
    ) -> None:
        """Truncation proceeds from layer 8 upward, stages 1→2→3, never drops layers 1-2, summary non-empty.

        **Validates: Requirements 16.4, 16.7**
        """
        layers, budget = data
        assembler = ContextAssembler("/tmp/dummy-ws", token_budget=budget)
        result: AssembledContext = assembler._enforce_token_budget(layers)

        # --- Verify truncation occurred (budget was exceeded) ---
        # The budget is at most half the total, so truncation must happen
        assert result.budget_exceeded is True, (
            f"Expected budget_exceeded=True for budget={budget}, "
            f"input total={sum(l.token_count for l in layers)}"
        )
        assert len(result.truncation_log) > 0, (
            "Expected non-empty truncation_log when budget is exceeded"
        )

        # --- Verify truncation log layer_numbers are in descending order ---
        # Truncation proceeds from layer 8 (lowest priority) upward (highest priority).
        # Within the log, the first entries should be for higher layer_numbers.
        # Group by layer_number and verify the first occurrence of each layer
        # appears in descending layer_number order.
        seen_layers: list[int] = []
        for entry in result.truncation_log:
            if entry.layer_number not in seen_layers:
                seen_layers.append(entry.layer_number)
        for i in range(1, len(seen_layers)):
            assert seen_layers[i] <= seen_layers[i - 1], (
                f"Truncation order violated: layer {seen_layers[i]} appeared "
                f"after layer {seen_layers[i - 1]} (should be descending). "
                f"Full order: {seen_layers}"
            )

        # --- Verify stages within each layer are in ascending order (1→2→3) ---
        from collections import defaultdict
        stages_per_layer: dict[int, list[int]] = defaultdict(list)
        for entry in result.truncation_log:
            stages_per_layer[entry.layer_number].append(entry.stage)
        for layer_num, stages in stages_per_layer.items():
            assert stages == sorted(stages), (
                f"Stages for layer {layer_num} are not in ascending order: "
                f"{stages}. Expected sorted: {sorted(stages)}"
            )

        # --- Verify stage 3 never applied to layers 1 or 2 ---
        for entry in result.truncation_log:
            if entry.layer_number <= 2:
                assert entry.stage != 3, (
                    f"Stage 3 (drop) was applied to layer {entry.layer_number}, "
                    f"which is forbidden. Layers 1-2 must never be fully dropped."
                )

        # --- Verify truncation_summary is non-empty when truncation occurred ---
        summary = assembler._build_truncation_summary(result.truncation_log)
        assert summary != "", (
            "truncation_summary must be non-empty when truncation occurred. "
            f"truncation_log has {len(result.truncation_log)} entries."
        )
        assert "[Context truncated:" in summary, (
            f"truncation_summary should start with '[Context truncated:' "
            f"but got: {summary[:80]!r}"
        )


# ── Strategies for Property 5 ─────────────────────────────────────────


def _short_string() -> st.SearchStrategy[str]:
    """Generate a short random string (1–30 chars) for titles and similar."""
    return st.text(
        alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz "),
        min_size=1,
        max_size=30,
    ).filter(lambda s: s.strip())


def _message_content() -> st.SearchStrategy[str]:
    """Generate random message content (1–500 words)."""
    return st.text(
        alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz .,!?"),
        min_size=1,
        max_size=2000,
    ).filter(lambda s: s.strip())


@st.composite
def random_thread_data(draw: st.DrawFn) -> dict:
    """Draw random thread data with 0–1000 messages."""
    title = draw(_short_string())
    mode = draw(st.sampled_from(["explore", "execute"]))
    msg_count = draw(st.integers(min_value=0, max_value=1000))

    messages = []
    for i in range(msg_count):
        role = draw(st.sampled_from(["user", "assistant"]))
        content = draw(_message_content())
        messages.append({"role": role, "content": content})

    return {"title": title, "mode": mode, "_messages": messages}


@st.composite
def random_task_list(draw: st.DrawFn) -> list[dict]:
    """Draw 0–10 random tasks with title and status."""
    count = draw(st.integers(min_value=0, max_value=10))
    tasks = []
    for _ in range(count):
        title = draw(_short_string())
        status = draw(st.sampled_from(["wip", "done", "pending", "blocked"]))
        tasks.append({"title": title, "status": status})
    return tasks


@st.composite
def random_todo_list(draw: st.DrawFn) -> list[dict]:
    """Draw 0–10 random todos with title and status."""
    count = draw(st.integers(min_value=0, max_value=10))
    todos = []
    for _ in range(count):
        title = draw(_short_string())
        status = draw(st.sampled_from(["pending", "done", "in_progress"]))
        todos.append({"title": title, "status": status})
    return todos


class TestPropertyLayer2BoundingInvariant:
    """Property 5: Layer 2 bounding invariant.

    *For any* Layer 2 content, regardless of the number of messages in
    the chat thread or the size of bound tasks/todos, the resulting
    layer's token count SHALL not exceed ``LAYER_2_TOKEN_LIMIT``
    (default 1200 tokens).  The bounded content SHALL always include
    the thread title and the last user message.

    Feature: swarmws-intelligence, Property 5: Layer 2 bounding invariant

    **Validates: Requirements 16.5**
    """

    @given(
        thread_data=random_thread_data(),
        tasks=random_task_list(),
        todos=random_todo_list(),
    )
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_layer_2_token_count_never_exceeds_limit(
        self,
        thread_data: dict,
        tasks: list[dict],
        todos: list[dict],
    ) -> None:
        """Layer 2 token count <= LAYER_2_TOKEN_LIMIT for any input.

        **Validates: Requirements 16.5**
        """
        assembler = ContextAssembler("/tmp/dummy-ws")
        result = assembler._summarize_layer_2(thread_data, tasks, todos)
        token_count = ContextAssembler.estimate_tokens(result)

        assert token_count <= LAYER_2_TOKEN_LIMIT, (
            f"Layer 2 token count {token_count} exceeds limit "
            f"{LAYER_2_TOKEN_LIMIT}. "
            f"Messages: {len(thread_data.get('_messages', []))}, "
            f"Tasks: {len(tasks)}, Todos: {len(todos)}"
        )

    @given(
        thread_data=random_thread_data(),
        tasks=random_task_list(),
        todos=random_todo_list(),
    )
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_layer_2_always_includes_thread_title(
        self,
        thread_data: dict,
        tasks: list[dict],
        todos: list[dict],
    ) -> None:
        """Bounded content always includes the thread title.

        **Validates: Requirements 16.5**
        """
        assembler = ContextAssembler("/tmp/dummy-ws")
        result = assembler._summarize_layer_2(thread_data, tasks, todos)

        title = thread_data.get("title", "Untitled Thread")
        assert title in result, (
            f"Thread title {title!r} not found in Layer 2 output. "
            f"Output preview: {result[:200]!r}"
        )

    @given(
        thread_data=random_thread_data(),
        tasks=random_task_list(),
        todos=random_todo_list(),
    )
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_layer_2_includes_last_user_message_when_present(
        self,
        thread_data: dict,
        tasks: list[dict],
        todos: list[dict],
    ) -> None:
        """When user messages exist, the last user message appears in the result.

        The last user message content should appear in the output unless
        the content is so large that truncation to fit the token limit
        cuts into it.  We verify that either the full last user message
        is present, or the result was truncated (contains the truncation
        marker).

        **Validates: Requirements 16.5**
        """
        assembler = ContextAssembler("/tmp/dummy-ws")
        result = assembler._summarize_layer_2(thread_data, tasks, todos)

        messages = thread_data.get("_messages", [])
        user_messages = [m for m in messages if m.get("role") == "user"]

        if user_messages:
            last_user_content = user_messages[-1].get("content", "")
            # The last user message should be in the result, OR the result
            # was truncated to fit the token limit (indicated by marker)
            has_last_user = last_user_content in result
            was_truncated = "[... truncated to fit token limit]" in result

            assert has_last_user or was_truncated, (
                f"Last user message not found and result was not truncated. "
                f"Last user msg preview: {last_user_content[:100]!r}, "
                f"Result preview: {result[:200]!r}"
            )


# ── Property 13: Stable project pathing ────────────────────────────────
# Feature: swarmws-intelligence, Property 13: Stable project pathing


def _project_name_strategy() -> st.SearchStrategy[str]:
    """Generate random project display names (1–50 alphanumeric chars)."""
    return st.text(
        alphabet=st.sampled_from(
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 -_"
        ),
        min_size=1,
        max_size=50,
    ).filter(lambda s: s.strip())


def _uuid_strategy() -> st.SearchStrategy[str]:
    """Generate random UUID-like strings for project IDs."""
    return st.uuids().map(str)


@st.composite
def project_with_rename(draw: st.DrawFn) -> tuple[str, str, str]:
    """Draw a project_id, original display name, and a new display name.

    Returns (project_id, original_name, renamed_name) where the two names
    are guaranteed to be different.
    """
    project_id = draw(_uuid_strategy())
    original_name = draw(_project_name_strategy())
    renamed_name = draw(
        _project_name_strategy().filter(lambda n: n != original_name)
    )
    return project_id, original_name, renamed_name


class TestPropertyStableProjectPathing:
    """Property 13: Stable project pathing.

    Verifies that the context assembler resolves project filesystem paths
    using ``project_id`` (UUID) rather than the display name.  When a
    project is renamed (display name changed in ``.project.json``), the
    assembly still locates and loads the correct context files without
    path breakage.

    **Validates: Requirements 16.8**
    """

    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.too_slow],
    )
    @given(data=project_with_rename())
    def test_resolve_project_path_stable_after_rename(
        self,
        tmp_path_factory: pytest.TempPathFactory,
        data: tuple[str, str, str],
    ) -> None:
        """_resolve_project_path returns the same path before and after rename.

        1. Create a project directory named by project_id
        2. Write .project.json with the original display name
        3. Resolve the path — should find the directory
        4. Rename the display name in .project.json
        5. Resolve again — should still find the same directory

        **Validates: Requirements 16.8**
        """
        project_id, original_name, renamed_name = data
        ws_path = tmp_path_factory.mktemp("ws")

        # Create project directory using project_id (UUID) as dir name
        project_dir = ws_path / "Projects" / project_id
        project_dir.mkdir(parents=True)

        # Write .project.json with original display name
        import json

        meta = {"id": project_id, "name": original_name, "status": "active"}
        (project_dir / ".project.json").write_text(
            json.dumps(meta), encoding="utf-8"
        )

        assembler = ContextAssembler(str(ws_path))

        # Resolve before rename
        path_before = assembler._resolve_project_path(project_id)
        assert path_before is not None, "Project should be found before rename"
        assert path_before == project_dir

        # Rename display name in .project.json
        meta["name"] = renamed_name
        (project_dir / ".project.json").write_text(
            json.dumps(meta), encoding="utf-8"
        )

        # Resolve after rename — should still find the same directory
        path_after = assembler._resolve_project_path(project_id)
        assert path_after is not None, "Project should be found after rename"
        assert path_after == project_dir
        assert path_before == path_after, (
            f"Path changed after rename: {path_before} != {path_after}"
        )

    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.too_slow],
    )
    @given(data=project_with_rename())
    def test_assembly_loads_correct_files_after_rename(
        self,
        tmp_path_factory: pytest.TempPathFactory,
        data: tuple[str, str, str],
    ) -> None:
        """Full assembly resolves correct files via project_id after rename.

        Creates a project with instructions.md, runs assembly, renames the
        project display name, runs assembly again, and verifies the same
        instructions content is loaded both times.

        **Validates: Requirements 16.8**
        """
        project_id, original_name, renamed_name = data
        ws_path = tmp_path_factory.mktemp("ws")

        # Create project directory using project_id
        project_dir = ws_path / "Projects" / project_id
        project_dir.mkdir(parents=True)

        import json

        meta = {"id": project_id, "name": original_name, "status": "active"}
        (project_dir / ".project.json").write_text(
            json.dumps(meta), encoding="utf-8"
        )

        # Write instructions.md with identifiable content
        instructions_content = f"Instructions for project {project_id}"
        (project_dir / "instructions.md").write_text(
            instructions_content, encoding="utf-8"
        )

        assembler = ContextAssembler(str(ws_path))

        # Assemble before rename
        result_before = asyncio.get_event_loop().run_until_complete(
            assembler.assemble(project_id)
        )

        # Find layer 3 (instructions) in the result
        instructions_layers_before = [
            l for l in result_before.layers
            if l.layer_number == LAYER_PROJECT_INSTRUCTIONS
        ]
        assert len(instructions_layers_before) == 1, (
            "Instructions layer should be present before rename"
        )
        assert instructions_content in instructions_layers_before[0].content

        # Rename display name in .project.json
        meta["name"] = renamed_name
        (project_dir / ".project.json").write_text(
            json.dumps(meta), encoding="utf-8"
        )

        # Assemble after rename
        result_after = asyncio.get_event_loop().run_until_complete(
            assembler.assemble(project_id)
        )

        # Find layer 3 again — should have the same content
        instructions_layers_after = [
            l for l in result_after.layers
            if l.layer_number == LAYER_PROJECT_INSTRUCTIONS
        ]
        assert len(instructions_layers_after) == 1, (
            "Instructions layer should be present after rename"
        )
        assert instructions_layers_after[0].content == instructions_layers_before[0].content, (
            "Instructions content should be identical after rename"
        )

    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.too_slow],
    )
    @given(project_id=_uuid_strategy(), name=_project_name_strategy())
    def test_fallback_scan_finds_project_by_uuid_in_metadata(
        self,
        tmp_path_factory: pytest.TempPathFactory,
        project_id: str,
        name: str,
    ) -> None:
        """Fallback scan resolves project when dir name differs from project_id.

        When the directory is named by display name (legacy) but
        .project.json contains the correct UUID, _resolve_project_path
        should still find it via the fallback scan.

        **Validates: Requirements 16.8**
        """
        ws_path = tmp_path_factory.mktemp("ws")

        # Create project directory using display name (legacy pattern)
        project_dir = ws_path / "Projects" / name
        project_dir.mkdir(parents=True)

        import json

        meta = {"id": project_id, "name": name, "status": "active"}
        (project_dir / ".project.json").write_text(
            json.dumps(meta), encoding="utf-8"
        )

        assembler = ContextAssembler(str(ws_path))

        # Should find via fallback scan of .project.json files
        resolved = assembler._resolve_project_path(project_id)
        assert resolved is not None, (
            f"Fallback scan should find project {project_id} in dir '{name}'"
        )
        assert resolved == project_dir
