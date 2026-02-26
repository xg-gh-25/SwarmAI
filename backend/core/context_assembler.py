"""8-layer context assembly engine for agent runtime.

This module implements the priority-ordered context assembly pipeline
defined in Requirement 16. It replaces the legacy ``ContextManager``
approach of reading a single context file with a structured 8-layer
pipeline that reads from the SwarmWS folder hierarchy.

Key design changes from PE review:

- L0 fast-filter uses tag/keyword overlap (not just exists-check)
- 3-stage progressive truncation (not whole-layer drops)
- Layer 2 is bounded and summarized
- Deterministic assembly guarantee
- Truncation summary injection for agent awareness
- Stable project pathing via project_id (not name)

Key public symbols:

- ``ContextAssembler``       — Main assembler class
- ``ContextLayer``           — Dataclass representing one assembled layer
- ``AssembledContext``        — Dataclass for the full assembly result
- ``TruncationInfo``         — Dataclass tracking truncation decisions
- ``LAYER_*`` constants      — Layer priority numbers (1–8)
- ``DEFAULT_TOKEN_BUDGET``   — Default max token budget (10,000)
- ``LAYER_2_TOKEN_LIMIT``    — Default Layer 2 bound (1,200 tokens)
- ``LAYER_2_MAX_MESSAGES``   — Max recent messages before summarization (10)
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import json
import logging
import re

import yaml

logger = logging.getLogger(__name__)

# ── Layer priority constants (1 = highest priority) ────────────────────

LAYER_SYSTEM_PROMPT = 1
LAYER_LIVE_WORK = 2
LAYER_PROJECT_INSTRUCTIONS = 3
LAYER_PROJECT_SEMANTIC = 4
LAYER_KNOWLEDGE_SEMANTIC = 5
LAYER_MEMORY = 6
LAYER_WORKSPACE_SEMANTIC = 7
LAYER_SCOPED_RETRIEVAL = 8

# ── Budget and bounding defaults ───────────────────────────────────────

DEFAULT_TOKEN_BUDGET = 10_000
LAYER_2_TOKEN_LIMIT = 1_200   # Max tokens for Layer 2 (live work context)
LAYER_2_MAX_MESSAGES = 10     # Max recent messages before summarization
MEMORY_MAX_FILES = 50         # Max .md files loaded from Knowledge/Memory/ (PE Fix P4)


# ── Dataclasses ────────────────────────────────────────────────────────


@dataclass
class TruncationInfo:
    """Tracks a single truncation decision for observability.

    Attributes:
        stage: Truncation stage applied (1=within-layer, 2=snippet-removal,
               3=layer-drop).
        layer_number: The layer that was truncated.
        original_tokens: Token count before truncation.
        truncated_tokens: Token count after truncation.
        reason: Human-readable explanation of why truncation occurred.
    """

    stage: int  # 1=within-layer, 2=snippet-removal, 3=layer-drop
    layer_number: int
    original_tokens: int
    truncated_tokens: int
    reason: str


@dataclass
class ContextLayer:
    """A single layer in the assembled context.

    Attributes:
        layer_number: Priority number (1=highest, 8=lowest).
        name: Human-readable layer name (e.g. "System Prompt").
        source_path: Workspace-relative path (never absolute).
        content: The assembled text content for this layer.
        token_count: Estimated token count for the content.
        truncated: Whether this layer was truncated during budget enforcement.
        truncation_stage: Stage of truncation applied (0=none, 1/2/3).
    """

    layer_number: int
    name: str
    source_path: str  # workspace-relative path (never absolute)
    content: str
    token_count: int
    truncated: bool = False
    truncation_stage: int = 0  # 0=none, 1/2/3 = stage


@dataclass
class AssembledContext:
    """Result of the full context assembly pipeline.

    Attributes:
        layers: Assembled layers in ascending priority order.
        total_token_count: Sum of all layer token counts.
        budget_exceeded: True if any truncation was applied.
        token_budget: The configured token budget.
        truncation_log: Detailed log of all truncation decisions.
        truncation_summary: Human-readable summary injected into context
            when truncation occurs.
    """

    layers: list[ContextLayer] = field(default_factory=list)
    total_token_count: int = 0
    budget_exceeded: bool = False
    token_budget: int = DEFAULT_TOKEN_BUDGET
    truncation_log: list[TruncationInfo] = field(default_factory=list)
    truncation_summary: str = ""  # Injected into context when truncation occurs


# ── Context Assembler ──────────────────────────────────────────────────


class ContextAssembler:
    """Assembles context layers for agent runtime.

    Guarantees:

    - Deterministic: same inputs + same budget → identical output
    - Layers appear in strictly ascending ``layer_number`` order
    - Layer 2 is always present but bounded to ``LAYER_2_TOKEN_LIMIT``
    - Progressive truncation preserves partial context from lower layers
    - Workspace-relative paths only (no absolute paths exposed)

    Args:
        workspace_path: Absolute path to the SwarmWS root directory.
        token_budget: Maximum token budget for assembled context.
    """

    def __init__(
        self, workspace_path: str, token_budget: int = DEFAULT_TOKEN_BUDGET
    ) -> None:
        self._ws_path = Path(workspace_path)
        self._token_budget = token_budget

    # ── Public API ─────────────────────────────────────────────────────

    async def assemble(
        self,
        project_id: str,
        thread_id: Optional[str] = None,
    ) -> AssembledContext:
        """Assemble all 8 context layers for a project.

        Assembly is deterministic: same inputs produce identical output.

        1. Resolve project path from project_id
        2. Load Layer 2 first (needed for keyword extraction)
        3. Extract keywords from Layer 2 for L0 filtering
        4. Load all layers in priority order
        5. Sort by layer_number (enforce ordering)
        6. Log layer sizes (PE Enhancement B)
        7. Enforce token budget with progressive truncation
        8. Inject truncation summary if needed

        Validates: Requirement 16.1, 16.3, 16.4, 16.6, 16.7, 38.1, 38.2
        """
        # 1. Resolve project path
        project_path = self._resolve_project_path(project_id)
        if project_path is None:
            logger.info(
                "Context assembly: project %s not found, returning empty",
                project_id,
            )
            return AssembledContext(token_budget=self._token_budget)

        # 2. Load Layer 2 first (needed for keyword extraction)
        layer_2 = await self._load_layer_2_live_work(project_id, thread_id)

        # 3. Extract keywords from Layer 2 for L0 filtering
        live_keywords = self._extract_live_context_keywords(
            layer_2.content if layer_2 else ""
        )

        # 4. Load all layers
        layers: list[ContextLayer] = []
        for loader_result in [
            await self._load_layer_1_system_prompt(),
            layer_2,
            await self._load_layer_3_instructions(project_path),
            await self._load_layer_4_project_semantic(project_path, live_keywords),
            await self._load_layer_5_knowledge_semantic(live_keywords),
            await self._load_layer_6_memory(),
            await self._load_layer_7_workspace_semantic(live_keywords),
            await self._load_layer_8_scoped_retrieval(project_id),
        ]:
            if loader_result is not None:
                layers.append(loader_result)

        # 5. Sort by layer_number (should already be in order, but enforce)
        layers.sort(key=lambda l: l.layer_number)

        # 6. Log layer sizes (PE Enhancement B — observability)
        logger.info(
            "Context assembly: project=%s thread=%s budget=%d",
            project_id,
            thread_id,
            self._token_budget,
        )
        logger.info(
            "Layer sizes: %s",
            {l.name: l.token_count for l in layers},
        )

        # 7. Enforce token budget with progressive truncation
        result = self._enforce_token_budget(layers)

        # 8. Inject truncation summary if needed
        if result.truncation_log:
            result.truncation_summary = self._build_truncation_summary(
                result.truncation_log
            )

        return result

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Estimate token count using word-based heuristic.

        Uses the approximation 1 token ≈ 0.75 words, so the formula is::

            tokens = ceil(word_count / 0.75)
                   = ceil(word_count * 4 / 3)

        This is consistent with the design document specification and
        provides a fast, dependency-free estimate suitable for budget
        enforcement.

        Args:
            text: The text to estimate tokens for.

        Returns:
            Estimated token count.  Returns 0 for empty/whitespace-only text.
        """
        if not text or not text.strip():
            return 0
        word_count = len(text.split())
        # 1 token ≈ 0.75 words → tokens = words * 4 / 3
        return int(word_count * 4 / 3)

    # ── Path helpers (PE Fix #7, #8) ───────────────────────────────────

    def _to_workspace_relative(self, absolute_path: Path) -> str:
        """Convert an absolute path to a workspace-relative string.

        Resolves symlinks before computing the relative path to prevent
        symlink-based path traversal attacks (PE Fix SEC1).  If the
        resolved path is under ``self._ws_path``, returns the relative
        portion (e.g. ``Projects/abc123/instructions.md``).  If the path
        is not under the workspace after resolution, returns it as-is to
        avoid exposing absolute filesystem information.

        Validates: PE Fix #8 (no absolute paths), PE Fix SEC1 (symlink traversal)

        Args:
            absolute_path: The path to convert.

        Returns:
            Workspace-relative path string (forward-slash separated).
        """
        try:
            resolved = absolute_path.resolve()
            ws_resolved = self._ws_path.resolve()
            return str(resolved.relative_to(ws_resolved))
        except (ValueError, OSError):
            # Path is not under workspace after symlink resolution —
            # return as-is (already relative or from a different root).
            return absolute_path.as_posix()

    def _resolve_project_path(self, project_id: str) -> Optional[Path]:
        """Resolve project filesystem path from *project_id*.

        Uses ``Projects/{project_id}/`` (stable, ID-based) as the primary
        lookup.  Falls back to scanning ``Projects/*/. project.json`` files
        for a matching UUID when the ID-based directory doesn't exist
        (backward compatibility with name-based directories).

        Validates: PE Fix #7 (stable project pathing)

        Args:
            project_id: The UUID of the project.

        Returns:
            Absolute ``Path`` to the project directory, or ``None`` if the
            project cannot be found.
        """
        # Primary: direct ID-based path
        direct_path = self._ws_path / "Projects" / project_id
        if direct_path.is_dir():
            return direct_path

        # Fallback: scan .project.json files for matching UUID
        projects_dir = self._ws_path / "Projects"
        if not projects_dir.is_dir():
            logger.debug(
                "Projects directory does not exist: %s", projects_dir
            )
            return None

        for candidate in sorted(projects_dir.iterdir()):
            if not candidate.is_dir():
                continue
            meta_file = candidate / ".project.json"
            if not meta_file.is_file():
                continue
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                if meta.get("id") == project_id:
                    logger.debug(
                        "Resolved project %s via .project.json scan: %s",
                        project_id,
                        candidate,
                    )
                    return candidate
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning(
                    "Skipping unreadable .project.json in %s: %s",
                    candidate.name,
                    exc,
                )
                continue

        logger.debug("Project not found: %s", project_id)
        return None

    # ── Layer loaders (stubs — implemented in Task 3.3) ────────────────

    async def _load_layer_1_system_prompt(self) -> Optional[ContextLayer]:
        """Read system-prompts.md from workspace root.

        Returns a ContextLayer with the system prompt content, or None if
        the file is missing or unreadable.

        Validates: Requirement 16.1
        """
        file_path = self._ws_path / "system-prompts.md"
        try:
            if not file_path.is_file():
                logger.debug("Layer 1: system-prompts.md not found at %s", file_path)
                return None

            content = file_path.read_text(encoding="utf-8")
            if not content.strip():
                logger.debug("Layer 1: system-prompts.md is empty")
                return None

            token_count = self.estimate_tokens(content)
            logger.debug("Layer 1 (System Prompt): %d tokens", token_count)
            return ContextLayer(
                layer_number=LAYER_SYSTEM_PROMPT,
                name="System Prompt",
                source_path=self._to_workspace_relative(file_path),
                content=content,
                token_count=token_count,
            )
        except OSError as exc:
            logger.warning("Layer 1: Failed to read system-prompts.md: %s", exc)
            return None

    async def _load_layer_2_live_work(
        self, project_id: str, thread_id: Optional[str]
    ) -> Optional[ContextLayer]:
        """Load active chat thread, ToDos, tasks from DB.

        Layer 2 is always present but bounded and summarized.  If
        *thread_id* is ``None``, returns a minimal layer or ``None``.

        Validates: Requirement 16.1, 16.5, PE Fix #3 (Layer 2 bounding)
        """
        if thread_id is None:
            logger.debug("Layer 2: No thread_id provided, skipping")
            return None

        try:
            from database import db

            # Load thread data
            thread_data = await db.chat_threads.get(thread_id)
            if thread_data is None:
                logger.warning("Layer 2: Thread %s not found", thread_id)
                return None

            # Load tasks bound to this thread
            tasks: list[dict] = []
            task_id = thread_data.get("task_id")
            if task_id:
                task = await db.tasks.get(task_id)
                if task:
                    tasks.append(task)

            # Load todos bound to this thread
            todos: list[dict] = []
            todo_id = thread_data.get("todo_id")
            if todo_id:
                todo = await db.todos.get(todo_id)
                if todo:
                    todos.append(todo)

            # Load recent messages for the thread
            messages = await db.chat_messages.list_by_thread(thread_id)
            thread_data["_messages"] = messages

            # Produce bounded summary
            content = self._summarize_layer_2(thread_data, tasks, todos)
            if not content.strip():
                logger.debug("Layer 2: Empty summary for thread %s", thread_id)
                return None

            token_count = self.estimate_tokens(content)
            logger.debug("Layer 2 (Live Work): %d tokens", token_count)
            return ContextLayer(
                layer_number=LAYER_LIVE_WORK,
                name="Live Work Context",
                source_path=f"chat_threads/{thread_id}",
                content=content,
                token_count=token_count,
            )
        except Exception as exc:
            logger.warning("Layer 2: Failed to load live work context: %s", exc)
            return None

    def _summarize_layer_2(
        self, thread_data: dict, tasks: list, todos: list
    ) -> str:
        """Produce a bounded summary of live work context.

        Returns a string bounded to ``LAYER_2_TOKEN_LIMIT`` tokens containing:

        - Thread title
        - Last user message
        - Last assistant message
        - Task/ToDo summary (title + status)
        - Summarized older messages if present

        Validates: Requirement 16.5
        """
        parts: list[str] = []

        # Thread title
        title = thread_data.get("title", "Untitled Thread")
        mode = thread_data.get("mode", "explore")
        parts.append(f"## Thread: {title} (mode: {mode})")

        # Task/ToDo summary
        if tasks:
            task_lines = []
            for t in tasks:
                t_title = t.get("title", "Untitled")
                t_status = t.get("status", "unknown")
                task_lines.append(f"- Task: {t_title} [{t_status}]")
            parts.append("### Bound Tasks\n" + "\n".join(task_lines))

        if todos:
            todo_lines = []
            for td in todos:
                td_title = td.get("title", "Untitled")
                td_status = td.get("status", "unknown")
                todo_lines.append(f"- ToDo: {td_title} [{td_status}]")
            parts.append("### Bound ToDos\n" + "\n".join(todo_lines))

        # Messages — extract last user and assistant messages
        messages = thread_data.get("_messages", [])

        last_user_msg = None
        last_assistant_msg = None
        for msg in reversed(messages):
            role = msg.get("role", "")
            if role == "user" and last_user_msg is None:
                last_user_msg = msg.get("content", "")
            elif role == "assistant" and last_assistant_msg is None:
                last_assistant_msg = msg.get("content", "")
            if last_user_msg is not None and last_assistant_msg is not None:
                break

        if last_user_msg:
            parts.append(f"### Last User Message\n{last_user_msg}")
        if last_assistant_msg:
            parts.append(f"### Last Assistant Message\n{last_assistant_msg}")

        # Summarize older messages if there are many
        if len(messages) > LAYER_2_MAX_MESSAGES:
            older_count = len(messages) - LAYER_2_MAX_MESSAGES
            parts.append(
                f"### Older Messages\n{older_count} earlier message(s) summarized. "
                f"Thread has {len(messages)} total messages."
            )

        content = "\n\n".join(parts)

        # Enforce LAYER_2_TOKEN_LIMIT by iteratively truncating if needed
        while self.estimate_tokens(content) > LAYER_2_TOKEN_LIMIT:
            words = content.split()
            # Target word count: LAYER_2_TOKEN_LIMIT * 0.75 (inverse of token formula)
            # Subtract a small margin to account for the truncation marker itself
            target_words = int(LAYER_2_TOKEN_LIMIT * 0.75) - 10
            if target_words < 1 or len(words) <= target_words:
                break
            content = " ".join(words[:target_words]) + "\n\n[... truncated to fit token limit]"

        return content

    async def _load_layer_3_instructions(
        self, project_path: Path
    ) -> Optional[ContextLayer]:
        """Read project instructions.md.

        Validates: Requirement 16.1
        """
        file_path = project_path / "instructions.md"
        try:
            if not file_path.is_file():
                logger.debug("Layer 3: instructions.md not found at %s", file_path)
                return None

            content = file_path.read_text(encoding="utf-8")
            if not content.strip():
                logger.debug("Layer 3: instructions.md is empty")
                return None

            token_count = self.estimate_tokens(content)
            logger.debug("Layer 3 (Instructions): %d tokens", token_count)
            return ContextLayer(
                layer_number=LAYER_PROJECT_INSTRUCTIONS,
                name="Project Instructions",
                source_path=self._to_workspace_relative(file_path),
                content=content,
                token_count=token_count,
            )
        except OSError as exc:
            logger.warning("Layer 3: Failed to read instructions.md: %s", exc)
            return None

    async def _load_layer_4_project_semantic(
        self, project_path: Path, live_context_keywords: set[str]
    ) -> Optional[ContextLayer]:
        """Load project context-L0/L1 with tag-based filtering.

        Reads ``context-L0.md`` from the project directory.  If L0 tags
        overlap with *live_context_keywords*, also reads ``context-L1.md``
        and combines both into the layer content.

        Validates: Requirement 16.2, PE Fix #1
        """
        l0_path = project_path / "context-L0.md"
        l1_path = project_path / "context-L1.md"
        try:
            if not l0_path.is_file():
                logger.debug("Layer 4: context-L0.md not found at %s", l0_path)
                return None

            l0_content = l0_path.read_text(encoding="utf-8")
            if not self._is_l0_relevant(l0_content, live_context_keywords):
                logger.debug("Layer 4: Project L0 not relevant, skipping")
                return None

            # L0 is relevant — combine L0 + L1
            parts = [l0_content]
            if l1_path.is_file():
                try:
                    l1_content = l1_path.read_text(encoding="utf-8")
                    if l1_content.strip():
                        parts.append(l1_content)
                except OSError as exc:
                    logger.warning("Layer 4: Failed to read context-L1.md: %s", exc)

            content = "\n\n".join(parts)
            token_count = self.estimate_tokens(content)
            logger.debug("Layer 4 (Project Semantic): %d tokens", token_count)
            return ContextLayer(
                layer_number=LAYER_PROJECT_SEMANTIC,
                name="Project Semantic Context",
                source_path=self._to_workspace_relative(l0_path),
                content=content,
                token_count=token_count,
            )
        except OSError as exc:
            logger.warning("Layer 4: Failed to read project context files: %s", exc)
            return None

    async def _load_layer_5_knowledge_semantic(
        self, live_context_keywords: set[str]
    ) -> Optional[ContextLayer]:
        """Load Knowledge context-L0/L1 with tag-based filtering.

        Reads ``Knowledge/context-L0.md``.  If L0 tags overlap with
        *live_context_keywords*, also reads ``Knowledge/context-L1.md``
        and combines both into the layer content.

        Validates: Requirement 16.2, PE Fix #1
        """
        knowledge_dir = self._ws_path / "Knowledge"
        l0_path = knowledge_dir / "context-L0.md"
        l1_path = knowledge_dir / "context-L1.md"
        try:
            if not l0_path.is_file():
                logger.debug("Layer 5: Knowledge/context-L0.md not found")
                return None

            l0_content = l0_path.read_text(encoding="utf-8")
            if not self._is_l0_relevant(l0_content, live_context_keywords):
                logger.debug("Layer 5: Knowledge L0 not relevant, skipping")
                return None

            # L0 is relevant — combine L0 + L1
            parts = [l0_content]
            if l1_path.is_file():
                try:
                    l1_content = l1_path.read_text(encoding="utf-8")
                    if l1_content.strip():
                        parts.append(l1_content)
                except OSError as exc:
                    logger.warning("Layer 5: Failed to read Knowledge/context-L1.md: %s", exc)

            content = "\n\n".join(parts)
            token_count = self.estimate_tokens(content)
            logger.debug("Layer 5 (Knowledge Semantic): %d tokens", token_count)
            return ContextLayer(
                layer_number=LAYER_KNOWLEDGE_SEMANTIC,
                name="Knowledge Semantic Context",
                source_path=self._to_workspace_relative(l0_path),
                content=content,
                token_count=token_count,
            )
        except OSError as exc:
            logger.warning("Layer 5: Failed to read Knowledge context files: %s", exc)
            return None

    async def _load_layer_6_memory(self) -> Optional[ContextLayer]:
        """Load .md files from Knowledge/Memory/ directory.

        Memory is always loaded — no L0 filter.  Files are loaded in
        sorted order for determinism.  Capped at ``MEMORY_MAX_FILES``
        (PE Fix P4); when exceeded, the most recent files by mtime are
        kept and re-sorted alphabetically.

        Validates: Requirement 16.1
        """
        memory_dir = self._ws_path / "Knowledge" / "Memory"
        try:
            if not memory_dir.is_dir():
                logger.debug("Layer 6: Knowledge/Memory/ directory not found")
                return None

            md_files = sorted(
                f for f in memory_dir.iterdir()
                if f.is_file() and f.suffix.lower() == ".md"
            )

            # PE Fix P4: Cap memory files to prevent unbounded reads.
            # Keep the most recent files by mtime when over the limit.
            if len(md_files) > MEMORY_MAX_FILES:
                logger.info(
                    "Layer 6: Capping memory files from %d to %d (most recent by mtime)",
                    len(md_files), MEMORY_MAX_FILES,
                )
                md_files = sorted(md_files, key=lambda f: f.stat().st_mtime, reverse=True)[:MEMORY_MAX_FILES]
                md_files.sort()  # Re-sort alphabetically for determinism

            if not md_files:
                logger.debug("Layer 6: No .md files in Knowledge/Memory/")
                return None

            parts: list[str] = []
            for md_file in md_files:
                try:
                    file_content = md_file.read_text(encoding="utf-8")
                    if file_content.strip():
                        parts.append(file_content)
                except OSError as exc:
                    logger.warning(
                        "Layer 6: Failed to read %s: %s", md_file.name, exc
                    )
                    continue

            if not parts:
                logger.debug("Layer 6: All Memory files were empty or unreadable")
                return None

            content = "\n\n".join(parts)
            token_count = self.estimate_tokens(content)
            logger.debug("Layer 6 (Memory): %d tokens from %d file(s)", token_count, len(parts))
            return ContextLayer(
                layer_number=LAYER_MEMORY,
                name="Persistent Memory",
                source_path=self._to_workspace_relative(memory_dir),
                content=content,
                token_count=token_count,
            )
        except OSError as exc:
            logger.warning("Layer 6: Failed to read Knowledge/Memory/: %s", exc)
            return None

    async def _load_layer_7_workspace_semantic(
        self, live_context_keywords: set[str]
    ) -> Optional[ContextLayer]:
        """Load SwarmWS context-L0/L1 with tag-based filtering.

        Reads ``context-L0.md`` from the workspace root.  If L0 tags
        overlap with *live_context_keywords*, also reads ``context-L1.md``
        and combines both into the layer content.

        Validates: Requirement 16.2, PE Fix #1
        """
        l0_path = self._ws_path / "context-L0.md"
        l1_path = self._ws_path / "context-L1.md"
        try:
            if not l0_path.is_file():
                logger.debug("Layer 7: context-L0.md not found at workspace root")
                return None

            l0_content = l0_path.read_text(encoding="utf-8")
            if not self._is_l0_relevant(l0_content, live_context_keywords):
                logger.debug("Layer 7: Workspace L0 not relevant, skipping")
                return None

            # L0 is relevant — combine L0 + L1
            parts = [l0_content]
            if l1_path.is_file():
                try:
                    l1_content = l1_path.read_text(encoding="utf-8")
                    if l1_content.strip():
                        parts.append(l1_content)
                except OSError as exc:
                    logger.warning("Layer 7: Failed to read context-L1.md: %s", exc)

            content = "\n\n".join(parts)
            token_count = self.estimate_tokens(content)
            logger.debug("Layer 7 (Workspace Semantic): %d tokens", token_count)
            return ContextLayer(
                layer_number=LAYER_WORKSPACE_SEMANTIC,
                name="Workspace Semantic Context",
                source_path=self._to_workspace_relative(l0_path),
                content=content,
                token_count=token_count,
            )
        except OSError as exc:
            logger.warning("Layer 7: Failed to read workspace context files: %s", exc)
            return None

    async def _load_layer_8_scoped_retrieval(
        self, project_id: str
    ) -> Optional[ContextLayer]:
        """Optional scoped retrieval. Placeholder for future RAG.

        Currently returns ``None``.  Will be implemented when RAG
        integration is added in a future cadence.
        """
        logger.debug("Layer 8: Scoped retrieval placeholder (not implemented)")
        return None

    # ── L0 filtering (Requirement 16.2, PE Fix #1) ───────────────────────

    # Common English stop words filtered out during keyword extraction.
    _STOP_WORDS: set[str] = frozenset({
        "the", "and", "for", "with", "this", "that", "from", "are", "was",
        "were", "been", "have", "has", "had", "not", "but", "can", "will",
        "its", "all", "any", "each", "than", "then", "them", "they",
        "their", "there", "these", "those", "what", "when", "where",
        "which", "who", "whom", "how", "into", "over", "such", "only",
        "also", "about", "after", "before", "between", "through", "during",
        "does", "did", "doing", "would", "could", "should", "may", "might",
        "shall", "being", "some", "other", "more", "most", "very", "just",
        "here", "now", "our", "out", "own", "too", "you", "your",
    })

    # Regex to match YAML frontmatter delimited by --- on its own line.
    _FRONTMATTER_RE = re.compile(
        r"\A\s*---[ \t]*\n(.*?)\n---[ \t]*\n",
        re.DOTALL,
    )

    # Template placeholder patterns that indicate non-real content.
    _TEMPLATE_PATTERNS: tuple[str, ...] = (
        "todo:",
        "todo: ...",
        "# context abstract",
    )

    def _is_l0_relevant(
        self, l0_content: str, live_context_keywords: set[str]
    ) -> bool:
        """Determine if an L0 abstract indicates the L1 content is relevant.

        Parses YAML frontmatter from *l0_content* to extract ``tags`` and
        ``active_domains``.  Performs token intersection between these tags
        and *live_context_keywords* derived from Layer 2.

        Returns ``True`` if there is any overlap between L0 tags/domains
        and the live context keywords.  Also returns ``True`` if L0 has no
        frontmatter (backward compatibility) but has non-empty,
        non-template content.

        Validates: Requirement 16.2, PE Fix #1 (tag-based L0 filtering)

        Args:
            l0_content: Raw text of the ``context-L0.md`` file.
            live_context_keywords: Keywords extracted from Layer 2 content.

        Returns:
            ``True`` if the corresponding L1 should be loaded.
        """
        tags = self._extract_l0_tags(l0_content)

        if tags:
            # Tag-based filtering: check intersection with live keywords
            overlap = tags & live_context_keywords
            logger.debug(
                "L0 filter: tags=%s keywords=%s overlap=%s",
                tags,
                live_context_keywords,
                overlap,
            )
            return len(overlap) > 0

        # Legacy fallback: no YAML frontmatter found (or empty tags).
        # Load L1 if L0 has non-empty, non-template content.
        if not l0_content or not l0_content.strip():
            return False

        stripped_lower = l0_content.strip().lower()
        for pattern in self._TEMPLATE_PATTERNS:
            if stripped_lower == pattern or stripped_lower.endswith(pattern):
                return False

        # Check if content is just a heading with a TODO placeholder
        lines = [
            line.strip()
            for line in l0_content.strip().splitlines()
            if line.strip()
        ]
        if all(
            line.startswith("#") or line.lower().startswith("todo")
            for line in lines
        ):
            return False

        logger.debug(
            "L0 filter: no frontmatter, legacy fallback → relevant "
            "(non-empty, non-template)"
        )
        return True

    def _extract_l0_tags(self, l0_content: str) -> set[str]:
        """Extract tags and active_domains from L0 YAML frontmatter.

        Parses the YAML block between ``---`` delimiters at the start of
        *l0_content*.  Extracts the ``tags`` list and ``active_domains``
        list, returning their union as a set of lowercase strings.

        Expected L0 format::

            ---
            tags: [python, api-design, authentication]
            active_domains: [backend, security]
            ---
            # Project Context Abstract
            ...

        Args:
            l0_content: Raw text of the ``context-L0.md`` file.

        Returns:
            Set of lowercase tag strings (union of ``tags`` and
            ``active_domains``).  Returns an empty set if no frontmatter
            is found or parsing fails.
        """
        if not l0_content or not l0_content.strip():
            return set()

        match = self._FRONTMATTER_RE.search(l0_content)
        if not match:
            return set()

        try:
            frontmatter = yaml.safe_load(match.group(1))
        except yaml.YAMLError as exc:
            logger.warning("Failed to parse L0 YAML frontmatter: %s", exc)
            return set()

        if not isinstance(frontmatter, dict):
            return set()

        result: set[str] = set()

        tags = frontmatter.get("tags")
        if isinstance(tags, list):
            for tag in tags:
                if isinstance(tag, str) and tag.strip():
                    result.add(tag.strip().lower())

        domains = frontmatter.get("active_domains")
        if isinstance(domains, list):
            for domain in domains:
                if isinstance(domain, str) and domain.strip():
                    result.add(domain.strip().lower())

        return result

    def _extract_live_context_keywords(
        self, layer_2_content: str
    ) -> set[str]:
        """Extract significant keywords from Layer 2 live work context.

        Splits *layer_2_content* on whitespace, lowercases each token,
        strips non-alphanumeric characters from edges, and filters out
        tokens shorter than 3 characters and common English stop words.

        The resulting set is used for tag intersection with L0 frontmatter
        during relevance filtering.

        Args:
            layer_2_content: The assembled Layer 2 text (thread title,
                task titles, todo descriptions, recent messages, etc.).

        Returns:
            Set of lowercase keyword strings suitable for tag intersection.
        """
        if not layer_2_content or not layer_2_content.strip():
            return set()

        keywords: set[str] = set()
        for raw_token in layer_2_content.split():
            # Strip non-alphanumeric chars from edges (punctuation, markdown)
            token = raw_token.strip().lower()
            token = re.sub(r"^[^a-z0-9]+", "", token)
            token = re.sub(r"[^a-z0-9]+$", "", token)

            if len(token) < 3:
                continue
            if token in self._STOP_WORDS:
                continue

            keywords.add(token)

        return keywords

    # ── Truncation & Budget Enforcement (Req 16.4, 16.7) ─────────────

    def _enforce_token_budget(
        self, layers: list[ContextLayer]
    ) -> AssembledContext:
        """3-stage progressive truncation until within budget.

        Algorithm:
        1. Calculate total tokens from all layers
        2. If within budget, return AssembledContext directly (no truncation)
        3. If over budget, iterate from layer 8 down to layer 1:
           - Try Stage 1 (truncate within layer) first
           - If still over budget, try Stage 2 (remove snippets)
           - If still over budget, try Stage 3 (drop layer) — NEVER for layers 1-2
           - After each truncation, recalculate total and check if within budget
           - Stop as soon as total is within budget
        4. Record each truncation decision in truncation_log
        5. Set budget_exceeded = True if any truncation occurred

        Validates: Requirement 16.4, 16.7, PE Fix #2 (progressive truncation)
        """
        total = sum(l.token_count for l in layers)

        if total <= self._token_budget:
            # Sort by ascending layer_number to guarantee priority ordering
            # (Requirement 16.1: layers SHALL appear in strictly ascending order)
            sorted_layers = sorted(layers, key=lambda l: l.layer_number)
            return AssembledContext(
                layers=sorted_layers,
                total_token_count=total,
                budget_exceeded=False,
                token_budget=self._token_budget,
            )

        # Over budget — apply progressive truncation from layer 8 upward
        truncation_log: list[TruncationInfo] = []
        # Work on a mutable copy sorted by descending layer_number
        # (truncate lowest priority first)
        layers_by_priority = sorted(layers, key=lambda l: -l.layer_number)

        for i, layer in enumerate(layers_by_priority):
            if total <= self._token_budget:
                break

            original_tokens = layer.token_count
            excess = total - self._token_budget
            # Target: reduce this layer by at least `excess` tokens
            target_tokens = max(0, layer.token_count - excess)

            # Stage 1: Truncate within layer (keep headers + top N tokens)
            if layer.token_count > target_tokens and target_tokens > 0:
                truncated_layer = self._truncate_within_layer(layer, target_tokens)
                if truncated_layer.token_count < original_tokens:
                    logger.debug(
                        "Truncation: stage=1 layer=%d %d→%d tokens",
                        layer.layer_number,
                        original_tokens,
                        truncated_layer.token_count,
                    )
                    truncation_log.append(TruncationInfo(
                        stage=1,
                        layer_number=layer.layer_number,
                        original_tokens=original_tokens,
                        truncated_tokens=truncated_layer.token_count,
                        reason=f"Stage 1: truncated within layer {layer.name}",
                    ))
                    total = total - original_tokens + truncated_layer.token_count
                    layers_by_priority[i] = truncated_layer
                    layer = truncated_layer
                    original_tokens = truncated_layer.token_count

                    if total <= self._token_budget:
                        break

            # Stage 2: Remove snippets (keep first section only)
            if layer.token_count > target_tokens and target_tokens > 0:
                snippet_layer = self._remove_snippets_from_layer(layer, target_tokens)
                if snippet_layer.token_count < original_tokens:
                    logger.debug(
                        "Truncation: stage=2 layer=%d %d→%d tokens",
                        layer.layer_number,
                        original_tokens,
                        snippet_layer.token_count,
                    )
                    truncation_log.append(TruncationInfo(
                        stage=2,
                        layer_number=layer.layer_number,
                        original_tokens=original_tokens,
                        truncated_tokens=snippet_layer.token_count,
                        reason=f"Stage 2: removed snippets from {layer.name}",
                    ))
                    total = total - original_tokens + snippet_layer.token_count
                    layers_by_priority[i] = snippet_layer
                    layer = snippet_layer
                    original_tokens = snippet_layer.token_count

                    if total <= self._token_budget:
                        break

            # Stage 3: Drop entire layer (NEVER for layers 1-2)
            if total > self._token_budget and layer.layer_number > 2:
                logger.debug(
                    "Truncation: stage=3 layer=%d dropped (%d tokens)",
                    layer.layer_number,
                    layer.token_count,
                )
                truncation_log.append(TruncationInfo(
                    stage=3,
                    layer_number=layer.layer_number,
                    original_tokens=layer.token_count,
                    truncated_tokens=0,
                    reason=f"Stage 3: dropped {layer.name}",
                ))
                total -= layer.token_count
                # Mark for removal by setting token_count to 0 and content empty
                dropped = ContextLayer(
                    layer_number=layer.layer_number,
                    name=layer.name,
                    source_path=layer.source_path,
                    content="",
                    token_count=0,
                    truncated=True,
                    truncation_stage=3,
                )
                layers_by_priority[i] = dropped

        # Rebuild layers list: filter out dropped layers, sort ascending
        final_layers = [
            l for l in layers_by_priority
            if l.token_count > 0 or l.truncation_stage != 3
        ]
        final_layers.sort(key=lambda l: l.layer_number)

        final_total = sum(l.token_count for l in final_layers)

        return AssembledContext(
            layers=final_layers,
            total_token_count=final_total,
            budget_exceeded=len(truncation_log) > 0,
            token_budget=self._token_budget,
            truncation_log=truncation_log,
        )

    def _truncate_within_layer(
        self, layer: ContextLayer, target_tokens: int
    ) -> ContextLayer:
        """Stage 1: Keep markdown headers and first N tokens of content.

        Splits content into lines, preserves all lines starting with '#'
        (markdown headers), and keeps enough remaining words to fit within
        *target_tokens*.

        Args:
            layer: The layer to truncate.
            target_tokens: Desired token count after truncation.

        Returns:
            New ContextLayer with truncated content and updated token_count.
        """
        if layer.token_count <= target_tokens:
            return layer

        lines = layer.content.split("\n")
        header_lines: list[str] = []
        content_lines: list[str] = []

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#"):
                header_lines.append(line)
            else:
                content_lines.append(line)

        # Estimate tokens used by headers
        headers_text = "\n".join(header_lines)
        header_tokens = self.estimate_tokens(headers_text)

        # Remaining budget for non-header content
        remaining_budget = max(0, target_tokens - header_tokens)

        # Take words from content lines to fill remaining budget
        content_text = "\n".join(content_lines)
        if remaining_budget > 0 and content_text.strip():
            words = content_text.split()
            # target words = remaining_budget * 0.75 (inverse of token formula)
            target_words = int(remaining_budget * 0.75)
            if target_words < len(words):
                truncated_content = " ".join(words[:target_words])
            else:
                truncated_content = content_text
        else:
            truncated_content = ""

        # Reassemble: headers first, then truncated content
        if truncated_content.strip():
            new_content = headers_text + "\n\n" + truncated_content
        else:
            new_content = headers_text

        new_token_count = self.estimate_tokens(new_content)
        return ContextLayer(
            layer_number=layer.layer_number,
            name=layer.name,
            source_path=layer.source_path,
            content=new_content,
            token_count=new_token_count,
            truncated=True,
            truncation_stage=1,
        )

    def _remove_snippets_from_layer(
        self, layer: ContextLayer, target_tokens: int
    ) -> ContextLayer:
        """Stage 2: Remove least important sections within a layer.

        Splits content by markdown headers (## or ###) and keeps only the
        first section, dropping subsequent sections.  For Memory (layer 6),
        keeps the most recent content (last section) and drops the oldest.

        Args:
            layer: The layer to truncate.
            target_tokens: Desired token count after truncation.

        Returns:
            New ContextLayer with reduced content and updated token_count.
        """
        if layer.token_count <= target_tokens:
            return layer

        # Split content into sections by markdown headers (## or ###)
        # Keep the delimiter with the section that follows it
        sections = re.split(r"(?=^#{2,3}\s)", layer.content, flags=re.MULTILINE)
        sections = [s for s in sections if s.strip()]

        if len(sections) <= 1:
            # Only one section — can't remove snippets, return as-is
            return layer

        if layer.layer_number == LAYER_MEMORY:
            # Memory: keep most recent files (last sections), drop oldest
            kept_sections: list[str] = []
            running_tokens = 0
            for section in reversed(sections):
                section_tokens = self.estimate_tokens(section)
                if running_tokens + section_tokens <= target_tokens:
                    kept_sections.insert(0, section)
                    running_tokens += section_tokens
                else:
                    break
            if not kept_sections:
                # At minimum keep the last section
                kept_sections = [sections[-1]]
        else:
            # Other layers: keep the first section, drop subsequent
            kept_sections = [sections[0]]

        new_content = "\n".join(kept_sections)
        new_token_count = self.estimate_tokens(new_content)

        return ContextLayer(
            layer_number=layer.layer_number,
            name=layer.name,
            source_path=layer.source_path,
            content=new_content,
            token_count=new_token_count,
            truncated=True,
            truncation_stage=2,
        )

    def _build_truncation_summary(
        self, truncation_log: list[TruncationInfo]
    ) -> str:
        """Build a truncation summary string for agent injection.

        Produces a human-readable summary describing which layers were
        affected and what truncation stages were applied.  This is injected
        into the assembled context so the agent knows context was omitted.

        Example output::

            [Context truncated: Layers affected: Memory (stage 1, 800→400
            tokens), Workspace Semantic (stage 3, dropped). Use tools to
            access full content.]

        Validates: PE Enhancement A (truncation summary for agent),
                   Requirement 16.7

        Args:
            truncation_log: List of TruncationInfo entries from budget
                enforcement.

        Returns:
            Human-readable truncation summary string, or empty string if
            no truncation occurred.
        """
        if not truncation_log:
            return ""

        # Map layer numbers to human-readable names
        layer_names = {
            LAYER_SYSTEM_PROMPT: "System Prompt",
            LAYER_LIVE_WORK: "Live Work",
            LAYER_PROJECT_INSTRUCTIONS: "Instructions",
            LAYER_PROJECT_SEMANTIC: "Project Semantic",
            LAYER_KNOWLEDGE_SEMANTIC: "Knowledge Semantic",
            LAYER_MEMORY: "Memory",
            LAYER_WORKSPACE_SEMANTIC: "Workspace Semantic",
            LAYER_SCOPED_RETRIEVAL: "Scoped Retrieval",
        }

        stage_descriptions = {
            1: "truncated",
            2: "snippets removed",
            3: "dropped",
        }

        parts: list[str] = []
        for info in truncation_log:
            name = layer_names.get(info.layer_number, f"Layer {info.layer_number}")
            stage_desc = stage_descriptions.get(info.stage, f"stage {info.stage}")
            if info.stage == 3:
                parts.append(f"{name} (stage {info.stage}, {stage_desc})")
            else:
                parts.append(
                    f"{name} (stage {info.stage}, "
                    f"{info.original_tokens}→{info.truncated_tokens} tokens)"
                )

        return (
            "[Context truncated: Layers affected: "
            + ", ".join(parts)
            + ". Use tools to access full content.]"
        )
