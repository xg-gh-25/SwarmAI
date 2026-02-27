"""Single-workspace filesystem manager for SwarmAI.

This module was refactored from a multi-workspace model to a single-workspace
+ projects model centred on the ``SwarmWS`` workspace.  It is responsible for:

- ``SwarmWorkspaceManager``          — Main class managing workspace filesystem
- ``FOLDER_STRUCTURE``               — Hierarchical folder layout (Knowledge, Projects)
- ``SYSTEM_MANAGED_*`` constants     — Sets of paths that cannot be deleted/renamed
- ``DEPTH_LIMITS``                   — Per-section folder-depth guardrails
- ``CONTEXT_L0_TEMPLATE`` / ``CONTEXT_L1_TEMPLATE`` — Layered context templates
- ``SYSTEM_PROMPTS_TEMPLATE``        — Default agent instruction template
- Project CRUD methods               — create / delete / get / list projects

The global singleton ``swarm_workspace_manager`` is created at module level.
"""
import asyncio
import copy
import json
import logging
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4

import anyio

from core.project_schema_migrations import CURRENT_SCHEMA_VERSION, migrate_if_needed

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Module-level constants
# ─────────────────────────────────────────────────────────────────────────────

# New hierarchical folder structure
FOLDER_STRUCTURE = [
    "Knowledge",
    "Knowledge/Knowledge Base",
    "Knowledge/Notes",
    "Knowledge/Memory",
    "Projects",
]

SYSTEM_MANAGED_FOLDERS = {
    "Knowledge", "Knowledge/Knowledge Base", "Knowledge/Notes",
    "Knowledge/Memory", "Projects",
}

SYSTEM_MANAGED_ROOT_FILES = {
    "system-prompts.md", "context-L0.md", "context-L1.md",
}

SYSTEM_MANAGED_SECTION_FILES = {
    "Knowledge/context-L0.md", "Knowledge/context-L1.md",
    "Knowledge/index.md", "Knowledge/knowledge-map.md",
    "Projects/context-L0.md", "Projects/context-L1.md",
}

PROJECT_SYSTEM_FILES = {
    ".project.json", "context-L0.md", "context-L1.md", "instructions.md",
}

PROJECT_SYSTEM_FOLDERS = {
    "chats", "research", "reports",
}

DEPTH_LIMITS = {
    "knowledge": 3,
    "project_system": 2,
    "project_user": 3,
}

KNOWLEDGE_SECTIONS = {"Knowledge", "Knowledge/Knowledge Base", "Knowledge/Notes", "Knowledge/Memory"}

# Section type keys for DEPTH_LIMITS (avoids string-key typos)
SECTION_KNOWLEDGE = "knowledge"
SECTION_PROJECT_SYSTEM = "project_system"
SECTION_PROJECT_USER = "project_user"

DEFAULT_WORKSPACE_CONFIG = {
    "name": "SwarmWS",
    "file_path": "{app_data_dir}/SwarmWS",
    "icon": "🏠",
}

# Project name validation: 1–100 chars, alphanumeric + spaces/hyphens/underscores/periods
_PROJECT_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9 _.\-]{0,99}$")

# Reserved filesystem names (Windows)
_RESERVED_NAMES = frozenset({
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
})

# ─────────────────────────────────────────────────────────────────────────────
# Context file templates
# ─────────────────────────────────────────────────────────────────────────────

CONTEXT_L0_TEMPLATE = """# Context L0 — {section_name}

<!-- Ultra-concise semantic abstract (~1000 tokens). Used by agents for fast relevance detection. -->

## Purpose
[One-sentence description of what this section/project contains]

## Key Topics
- [Topic 1]
- [Topic 2]

## Current Status
[Brief status summary]
"""

CONTEXT_L1_TEMPLATE = """# Context L1 — {section_name}

<!-- Structured overview (~4k tokens). Used by agents for deeper understanding. -->

## Scope
[What is in scope for this section/project]

## Goals
- [Goal 1]
- [Goal 2]

## Key Knowledge
[Important facts, decisions, and context]

## Relationships
[How this relates to other sections/projects]

## Recent Activity
[Summary of recent changes]
"""

SYSTEM_PROMPTS_TEMPLATE = """# SwarmWS System Prompts

<!-- Default system prompt template for agent customization. -->
<!-- Edit this file to customize how agents interact with your workspace. -->

## Agent Instructions
You are working within the SwarmWS workspace. This workspace is organized into:

- **Knowledge/** — Shared knowledge domain
  - **Knowledge Base/** — Durable, reusable knowledge assets
  - **Notes/** — Ongoing research notes and working documents
  - **Memory/** — Persistent semantic memory distilled from interactions
- **Projects/** — Active project containers with their own context

## Guidelines
- Always check context files (context-L0.md, context-L1.md) before starting work
- Store durable knowledge outputs in Knowledge/Knowledge Base/
- Store working notes in Knowledge/Notes/
- Update context files when significant changes occur
"""

# ─────────────────────────────────────────────────────────────────────────────
# Sample data content constants
# ─────────────────────────────────────────────────────────────────────────────

SAMPLE_SECTION_READMES = {
    "Signals": "# Signals\n\nIncoming tasks, requests, and triggers that need attention.\n\nDrop new signals here for triage and processing.\n",
    "Plan": "# Plan\n\nPrioritized work items and sprint planning.\n\nOrganize your upcoming work and set priorities here.\n",
    "Execute": "# Execute\n\nActive task execution and progress tracking.\n\nTrack ongoing work and execution status here.\n",
    "Communicate": "# Communicate\n\nStatus updates and team coordination.\n\nDraft communications, updates, and coordination notes here.\n",
    "Reflection": "# Reflection\n\nRetrospectives and learning capture.\n\nCapture lessons learned, retrospectives, and insights here.\n",
}

SAMPLE_KNOWLEDGE_BASE_CONTENT = """# API Design Guidelines

## Purpose
Standard guidelines for designing REST APIs across all SwarmAI projects.

## Conventions
- Use snake_case for JSON field names in API responses
- Use camelCase in frontend TypeScript interfaces
- Always version APIs with a /v1/ prefix for breaking changes
- Return 201 for resource creation, 200 for updates, 204 for deletes

## Error Response Format
All errors follow a consistent structure:
```json
{
  "detail": "Human-readable error message",
  "code": "MACHINE_READABLE_CODE"
}
```

## Status
Active — last reviewed 2025-01
"""

SAMPLE_NOTES_CONTENT = """# Meeting Notes — Weekly Planning

## Date
2025-01-20

## Attendees
- Product lead, Engineering lead, Design lead

## Key Decisions
- Prioritize workspace redesign for Q1 release
- Defer multi-agent orchestration to Q2
- Knowledge Base structure approved as proposed

## Action Items
- [ ] Draft project brief for workspace redesign
- [ ] Set up research folder with competitor analysis
- [ ] Schedule design review for next Thursday

## Open Questions
- How should we handle migration from the old folder structure?
- What's the right depth limit for Knowledge subfolders?
"""

SAMPLE_PROJECT_INSTRUCTIONS = """# Website Redesign

## Overview
Redesign the company marketing website to improve conversion rates and
align with the updated brand guidelines released in Q4 2024.

## Goals
- Increase landing page conversion rate from 2.1% to 3.5%
- Reduce page load time to under 2 seconds on mobile
- Implement new brand color palette and typography

## Instructions for Agents
- Check context-L0.md for a quick overview of project scope
- Check context-L1.md for detailed context and constraints
- Store competitor analysis and user research in research/
- Store deliverables (mockups, reports, copy drafts) in reports/
- Chat transcripts are saved automatically in chats/

## Key Constraints
- Must maintain SEO rankings during migration
- Budget: 40 engineering hours for frontend, 20 for backend
- Launch target: end of Q1 2025
"""

SAMPLE_RESEARCH_CONTENT = """# Competitor Analysis — Landing Pages

## Competitors Reviewed
1. **Notion** — Clean, minimal hero with single CTA
2. **Linear** — Dark theme, animated product demo above fold
3. **Figma** — Community-driven social proof, interactive examples

## Key Takeaways
- All top performers use a single primary CTA above the fold
- Social proof (logos, testimonials) appears within first viewport
- Page load times are consistently under 1.5s on mobile
- Video/animation is used sparingly — only when it demonstrates the product

## Recommendations
- Simplify our hero to one headline + one CTA
- Add customer logos bar below the fold
- Replace stock imagery with actual product screenshots
"""

SAMPLE_REPORT_CONTENT = """# Performance Audit — Current Website

## Summary
Current marketing site scores 62/100 on Lighthouse mobile performance.
Main bottlenecks are unoptimized images and render-blocking JavaScript.

## Findings
| Issue | Impact | Effort |
|-------|--------|--------|
| Unoptimized hero image (2.4MB) | High | Low |
| Render-blocking third-party scripts | High | Medium |
| No lazy loading on below-fold images | Medium | Low |
| Missing font-display: swap | Low | Low |

## Next Steps
- [ ] Convert images to WebP with responsive srcset
- [ ] Defer non-critical JavaScript
- [ ] Implement intersection observer for lazy loading
"""

SAMPLE_MEMORY_CONTENT = """# User Preference — Communication Style

## Memory Type
User Preference

## Extracted From
Recurring patterns observed across multiple chat interactions.

## Content
The user prefers concise, actionable responses over lengthy explanations.
When presenting options, use numbered lists with brief pros/cons rather than
detailed paragraphs. Code examples are preferred over abstract descriptions.

## Confidence
High — observed consistently across 5+ interactions.

## Last Validated
2025-01-15

## Notes
This memory item demonstrates how SwarmAI distills persistent semantic memory
from user interactions. Memory items capture preferences, recurring themes,
and accumulated insights that help agents provide more personalized assistance.
"""


# ─────────────────────────────────────────────────────────────────────────────
# Manager class
# ─────────────────────────────────────────────────────────────────────────────

class SwarmWorkspaceManager:
    """Manages the single SwarmWS workspace filesystem operations.

    Provides path helpers, system-managed path checks, depth validation,
    folder structure creation, integrity verification, and project CRUD.
    All async filesystem operations use ``anyio.to_thread.run_sync()``.

    Module-level constants are re-exported as class attributes for backward
    compatibility (e.g. ``SwarmWorkspaceManager.DEFAULT_WORKSPACE_CONFIG``).
    """

    # Re-export module-level constants as class attributes for backward compat
    FOLDER_STRUCTURE = FOLDER_STRUCTURE
    SYSTEM_MANAGED_FOLDERS = SYSTEM_MANAGED_FOLDERS
    SYSTEM_MANAGED_ROOT_FILES = SYSTEM_MANAGED_ROOT_FILES
    SYSTEM_MANAGED_SECTION_FILES = SYSTEM_MANAGED_SECTION_FILES
    PROJECT_SYSTEM_FILES = PROJECT_SYSTEM_FILES
    PROJECT_SYSTEM_FOLDERS = PROJECT_SYSTEM_FOLDERS
    DEPTH_LIMITS = DEPTH_LIMITS
    KNOWLEDGE_SECTIONS = KNOWLEDGE_SECTIONS
    DEFAULT_WORKSPACE_CONFIG = DEFAULT_WORKSPACE_CONFIG

    def __init__(self):
        """Initialize the SwarmWorkspaceManager.

        Sets up per-project concurrency locks and the in-memory UUID→Path
        index used by ``_find_project_dir`` for fast project lookups.
        """
        self._project_locks: dict[str, asyncio.Lock] = {}
        self._uuid_index: dict[str, Path] = {}

    # ── Path helpers ─────────────────────────────────────────────────────

    def expand_path(self, file_path: str) -> str:
        """Expand path placeholders to actual filesystem paths.

        Handles the following expansions:
        - ~ : User home directory
        - {app_data_dir} : Platform-specific application data directory

        Args:
            file_path: Path that may contain ~ or {app_data_dir} placeholders.

        Returns:
            Expanded absolute path string.
        """
        from config import get_app_data_dir

        if "{app_data_dir}" in file_path:
            app_data_path = str(get_app_data_dir())
            file_path = file_path.replace("{app_data_dir}", app_data_path)

        return os.path.expanduser(file_path)

    def validate_path(self, file_path: str) -> bool:
        """Validate that a file path is safe and properly formatted.

        Validates:
        - Path does not contain path traversal sequences (..)
        - Path is either absolute, starts with ~, or starts with {app_data_dir}

        Args:
            file_path: The file path to validate.

        Returns:
            True if path is valid, False otherwise.
        """
        if not file_path:
            logger.warning("Path validation failed: empty path")
            return False

        if ".." in file_path:
            logger.warning("Path validation failed: path traversal detected in '%s'", file_path)
            return False

        is_absolute = os.path.isabs(file_path)
        starts_with_tilde = file_path.startswith("~")
        starts_with_app_data_dir = file_path.startswith("{app_data_dir}")

        if not is_absolute and not starts_with_tilde and not starts_with_app_data_dir:
            logger.warning(
                "Path validation failed: path must be absolute, start with ~, "
                "or use {app_data_dir}: '%s'", file_path
            )
            return False

        return True

    # ── System-managed checks ────────────────────────────────────────────

    def is_system_managed(self, relative_path: str) -> bool:
        """Check if a path relative to workspace root is system-managed.

        System-managed items cannot be deleted or renamed by users.

        Checks against:
        - SYSTEM_MANAGED_FOLDERS (top-level section folders)
        - SYSTEM_MANAGED_ROOT_FILES (root-level system files)
        - SYSTEM_MANAGED_SECTION_FILES (section-level context files)
        - PROJECT_SYSTEM_FILES and PROJECT_SYSTEM_FOLDERS (per-project system items)

        Args:
            relative_path: Path relative to the workspace root directory.

        Returns:
            True if the path is system-managed, False otherwise.
        """
        normalized = relative_path.strip("/").replace("\\", "/")
        if not normalized:
            return False

        if normalized in SYSTEM_MANAGED_ROOT_FILES:
            return True

        # Check against system-managed folders (supports both top-level
        # like "Projects" and nested like "Knowledge/Memory")
        if normalized in SYSTEM_MANAGED_FOLDERS:
            return True

        if normalized in SYSTEM_MANAGED_SECTION_FILES:
            return True

        # Check project-level system items: Projects/{name}/{item}
        if normalized.startswith("Projects/"):
            parts = normalized.split("/")
            if len(parts) >= 3:
                item_name = parts[2]
                if item_name in PROJECT_SYSTEM_FILES:
                    return True
                if item_name in PROJECT_SYSTEM_FOLDERS:
                    return True

        return False

    def validate_depth(self, target_path: str) -> tuple[bool, str]:
        """Check whether creating a folder at target_path would exceed depth guardrails.

        Args:
            target_path: The path to validate (relative to workspace root).

        Returns:
            (is_valid, error_message) tuple. error_message is empty string when valid.
        """
        normalized = target_path.strip("/").replace("\\", "/")
        parts = normalized.split("/")

        if not parts or not parts[0]:
            return (True, "")

        first = parts[0]
        section_type: Optional[str] = None
        depth = 0

        if first == "Knowledge" or normalized.startswith("Knowledge/"):
            section_type = SECTION_KNOWLEDGE
            depth = len(parts) - 1
        elif first == "Projects":
            if len(parts) < 3:
                return (True, "")
            sub_path = parts[2:]
            if sub_path and sub_path[0] in PROJECT_SYSTEM_FOLDERS:
                section_type = SECTION_PROJECT_SYSTEM
                depth = len(sub_path) - 1
            else:
                section_type = SECTION_PROJECT_USER
                depth = len(sub_path) - 1
        else:
            return (True, "")

        limit = DEPTH_LIMITS.get(section_type, 999)
        if depth > limit:
            return (
                False,
                f"Maximum folder depth of {limit} exceeded for {section_type}. "
                f"Current depth: {depth}.",
            )
        return (True, "")

    # ── Filesystem helpers ───────────────────────────────────────────────

    def _write_file_if_missing(self, file_path: Path, content: str) -> bool:
        """Write content to file only if it doesn't already exist.

        This is a synchronous method intended to be called inside
        ``anyio.to_thread.run_sync()``.

        Args:
            file_path: Absolute path to the file.
            content: Text content to write.

        Returns:
            True if the file was written, False if it already existed.
        """
        if not file_path.exists():
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            return True
        return False

    # ── Folder structure creation ────────────────────────────────────────

    async def create_folder_structure(self, workspace_path: str) -> None:
        """Create the standard folder structure for the workspace.

        Creates the root directory, all section folders from FOLDER_STRUCTURE,
        root-level system files (system-prompts.md, context-L0.md, context-L1.md),
        and section-level context/system files for Knowledge/ and Projects/.

        Files are only created if they do not already exist (idempotent).

        Args:
            workspace_path: Path to the workspace root directory.
                Can contain ~ or {app_data_dir} placeholders.

        Raises:
            ValueError: If the path is invalid.
            OSError: If folder creation fails.
        """
        if not self.validate_path(workspace_path):
            raise ValueError(
                f"Invalid workspace path: '{workspace_path}'. "
                "Path must be absolute or start with ~ and cannot contain '..'"
            )

        expanded_path = self.expand_path(workspace_path)
        root = Path(expanded_path)

        # Create root directory
        try:
            await anyio.to_thread.run_sync(
                lambda: root.mkdir(parents=True, exist_ok=True)
            )
            logger.info("Created workspace root directory: %s", root)
        except OSError as e:
            logger.error("Failed to create workspace root directory '%s': %s", root, e)
            raise OSError(f"Failed to create workspace root directory: {e}") from e

        # Create all section folders
        for folder_name in FOLDER_STRUCTURE:
            folder_path = root / folder_name
            try:
                def _ensure_dir(fp=folder_path):
                    if fp.exists() and not fp.is_dir():
                        fp.unlink()  # Remove conflicting file; system folders take precedence
                    fp.mkdir(parents=True, exist_ok=True)
                await anyio.to_thread.run_sync(_ensure_dir)
                logger.debug("Created subdirectory: %s", folder_path)
            except OSError as e:
                logger.error("Failed to create subdirectory '%s': %s", folder_path, e)
                raise OSError(
                    f"Failed to create workspace folder '{folder_name}': {e}"
                ) from e

        # Create root-level system files
        await anyio.to_thread.run_sync(
            lambda: self._write_file_if_missing(
                root / "system-prompts.md", SYSTEM_PROMPTS_TEMPLATE
            )
        )
        await anyio.to_thread.run_sync(
            lambda: self._write_file_if_missing(
                root / "context-L0.md",
                CONTEXT_L0_TEMPLATE.format(section_name="SwarmWS"),
            )
        )
        await anyio.to_thread.run_sync(
            lambda: self._write_file_if_missing(
                root / "context-L1.md",
                CONTEXT_L1_TEMPLATE.format(section_name="SwarmWS"),
            )
        )

        # Create section-level context and system files for Knowledge/ and Projects/
        for section in ("Knowledge", "Projects"):
            await anyio.to_thread.run_sync(
                lambda s=section: self._write_file_if_missing(
                    root / s / "context-L0.md",
                    CONTEXT_L0_TEMPLATE.format(section_name=s),
                )
            )
            await anyio.to_thread.run_sync(
                lambda s=section: self._write_file_if_missing(
                    root / s / "context-L1.md",
                    CONTEXT_L1_TEMPLATE.format(section_name=s),
                )
            )

        # Knowledge-specific system files
        await anyio.to_thread.run_sync(
            lambda: self._write_file_if_missing(
                root / "Knowledge" / "index.md",
                "# Knowledge Index\n\n[Auto-generated index of knowledge assets]\n",
            )
        )
        await anyio.to_thread.run_sync(
            lambda: self._write_file_if_missing(
                root / "Knowledge" / "knowledge-map.md",
                "# Knowledge Map\n\n[Visual map of knowledge relationships]\n",
            )
        )

        logger.info(
            "Successfully created folder structure for workspace at '%s'", expanded_path
        )

    # ── Context reading (backward compat) ────────────────────────────────

    async def read_context_files(self, workspace_path: str) -> str:
        """Read and combine context files from a workspace.

        Reads the following files from the ContextFiles subdirectory for
        backward compatibility:
        - context.md: Main workspace context template
        - compressed-context.md: Compressed context for long-term memory

        The contents are combined with a separator between them.
        Missing files are handled gracefully.

        Args:
            workspace_path: Path to the workspace root directory.

        Returns:
            Combined content of both context files as a single string.
            Returns empty string if both files are missing or unreadable.
        """
        expanded_path = self.expand_path(workspace_path)
        context_dir = Path(expanded_path) / "ContextFiles"

        contents = []

        context_path = context_dir / "context.md"
        try:
            context_content = await anyio.to_thread.run_sync(
                lambda: context_path.read_text(encoding="utf-8")
                if context_path.exists()
                else ""
            )
            if context_content:
                contents.append(context_content)
        except Exception as e:
            logger.warning("Failed to read context.md: %s", e)

        compressed_context_path = context_dir / "compressed-context.md"
        try:
            compressed_content = await anyio.to_thread.run_sync(
                lambda: compressed_context_path.read_text(encoding="utf-8")
                if compressed_context_path.exists()
                else ""
            )
            if compressed_content:
                contents.append(compressed_content)
        except Exception as e:
            logger.warning("Failed to read compressed-context.md: %s", e)

        return "\n\n---\n\n".join(contents) if contents else ""

    # ── Workspace lifecycle ──────────────────────────────────────────────

    async def ensure_default_workspace(self, db) -> dict:
        """Ensure the default SwarmWS workspace exists, creating it if necessary.

        If no workspace_config row exists, inserts one, creates the folder
        structure on disk, and populates sample data for first-time users.
        If a row already exists, runs ``verify_integrity()`` to heal any
        missing system-managed items.

        **Startup ordering guarantee (Req 24.4, 1.7):**
        Legacy data cleanup runs BEFORE this method is called.  The cleanup
        lives in ``SQLiteDatabase._run_migrations()`` which executes during
        ``db.initialize()`` in the app lifespan.  ``main.py`` calls
        ``initialize_database()`` first, then
        ``initialization_manager.run_full_initialization()`` which invokes
        this method.  Therefore the ``swarm_workspaces`` table (if it
        existed) has already been dropped and legacy workspace directories
        removed by the time we reach here, ensuring a clean-slate init.

        Args:
            db: Database instance with ``db.workspace_config`` accessor
                providing ``get_config()`` and ``put()`` methods.

        Returns:
            dict with workspace configuration (id, name, file_path, icon,
            created_at, updated_at).
        """
        existing = await db.workspace_config.get_config()

        if existing:
            logger.info("Default workspace config already exists, verifying integrity")
            file_path = existing.get("file_path", DEFAULT_WORKSPACE_CONFIG["file_path"])
            expanded = self.expand_path(file_path)
            recreated = await self.verify_integrity(expanded)
            if recreated:
                logger.info(
                    "Integrity check recreated %d items: %s", len(recreated), recreated
                )
            # Populate sample data if workspace was freshly scaffolded
            # (seed DB has config row but filesystem was empty).
            # _populate_sample_data is idempotent — skips existing files.
            try:
                await self._populate_sample_data(file_path)
            except Exception as e:
                logger.warning("Failed to populate sample data on integrity path: %s", e)
            return existing

        logger.info("Creating default workspace for the first time")

        now = datetime.now(timezone.utc).isoformat()
        config = {
            "id": "swarmws",
            "name": DEFAULT_WORKSPACE_CONFIG["name"],
            "file_path": DEFAULT_WORKSPACE_CONFIG["file_path"],
            "icon": DEFAULT_WORKSPACE_CONFIG["icon"],
            "created_at": now,
            "updated_at": now,
        }

        # Persist to database
        await db.workspace_config.put(config)
        logger.info("Inserted workspace config with id: %s", config['id'])

        # Create folder structure on disk
        try:
            await self.create_folder_structure(config["file_path"])
            logger.info("Created folder structure at %s", config['file_path'])
        except Exception as e:
            logger.error(
                "Failed to create folder structure for default workspace: %s", e
            )
            raise

        # Populate sample data for first-time experience
        try:
            await self._populate_sample_data(config["file_path"])
            logger.info("Populated sample data for default workspace")
        except Exception as e:
            logger.warning("Failed to populate sample data: %s", e)

        return config

    async def verify_integrity(self, workspace_path: str) -> list[str]:
        """Verify all system-managed items exist, recreating missing ones.

        Checks folders from FOLDER_STRUCTURE, root-level system files,
        section-level context files, and per-project system items.
        Does NOT overwrite existing files.

        Args:
            workspace_path: Expanded absolute path to the workspace root.

        Returns:
            List of recreated item paths (relative to workspace root).
        """
        root = Path(workspace_path)
        recreated: list[str] = []

        # 1. Ensure all section folders exist
        for folder_name in FOLDER_STRUCTURE:
            folder_path = root / folder_name
            if not folder_path.exists():
                await anyio.to_thread.run_sync(
                    lambda fp=folder_path: fp.mkdir(parents=True, exist_ok=True)
                )
                recreated.append(folder_name)
                logger.info("Recreated missing folder: %s", folder_name)

        # 2. Ensure root-level system files exist
        root_file_templates = {
            "system-prompts.md": SYSTEM_PROMPTS_TEMPLATE,
            "context-L0.md": CONTEXT_L0_TEMPLATE.format(section_name="SwarmWS"),
            "context-L1.md": CONTEXT_L1_TEMPLATE.format(section_name="SwarmWS"),
        }
        for filename, template in root_file_templates.items():
            written = await anyio.to_thread.run_sync(
                lambda fp=root / filename, t=template: self._write_file_if_missing(fp, t)
            )
            if written:
                recreated.append(filename)
                logger.info("Recreated missing root file: %s", filename)

        # 3. Ensure section-level context files exist
        section_file_templates = {
            "Knowledge/context-L0.md": CONTEXT_L0_TEMPLATE.format(section_name="Knowledge"),
            "Knowledge/context-L1.md": CONTEXT_L1_TEMPLATE.format(section_name="Knowledge"),
            "Knowledge/index.md": "# Knowledge Index\n\n[Auto-generated index of knowledge assets]\n",
            "Knowledge/knowledge-map.md": "# Knowledge Map\n\n[Visual map of knowledge relationships]\n",
            "Projects/context-L0.md": CONTEXT_L0_TEMPLATE.format(section_name="Projects"),
            "Projects/context-L1.md": CONTEXT_L1_TEMPLATE.format(section_name="Projects"),
        }
        for rel_path, content in sorted(section_file_templates.items()):
            written = await anyio.to_thread.run_sync(
                lambda fp=root / rel_path, c=content: self._write_file_if_missing(fp, c)
            )
            if written:
                recreated.append(rel_path)
                logger.info("Recreated missing section file: %s", rel_path)

        # 4. Verify per-project system items
        projects_dir = root / "Projects"
        if projects_dir.exists():

            def _scan_projects():
                return [
                    d
                    for d in projects_dir.iterdir()
                    if d.is_dir() and (d / ".project.json").exists()
                ]

            project_dirs = await anyio.to_thread.run_sync(_scan_projects)

            for project_dir in project_dirs:
                project_name = project_dir.name

                # Check system files
                for sys_file in sorted(PROJECT_SYSTEM_FILES):
                    file_path = project_dir / sys_file
                    if not file_path.exists():
                        if sys_file == "context-L0.md":
                            content = CONTEXT_L0_TEMPLATE.format(
                                section_name=project_name
                            )
                        elif sys_file == "context-L1.md":
                            content = CONTEXT_L1_TEMPLATE.format(
                                section_name=project_name
                            )
                        elif sys_file == "instructions.md":
                            content = (
                                f"# {project_name} Instructions\n\n"
                                "[Add project instructions here]\n"
                            )
                        else:
                            # .project.json — skip, it must exist (filtered on it)
                            continue
                        written = await anyio.to_thread.run_sync(
                            lambda fp=file_path, c=content: self._write_file_if_missing(
                                fp, c
                            )
                        )
                        if written:
                            rel = f"Projects/{project_name}/{sys_file}"
                            recreated.append(rel)
                            logger.info("Recreated missing project file: %s", rel)

                # Check system folders
                for sys_folder in sorted(PROJECT_SYSTEM_FOLDERS):
                    folder_path = project_dir / sys_folder
                    if not folder_path.exists():
                        await anyio.to_thread.run_sync(
                            lambda fp=folder_path: fp.mkdir(parents=True, exist_ok=True)
                        )
                        rel = f"Projects/{project_name}/{sys_folder}"
                        recreated.append(rel)
                        logger.info("Recreated missing project folder: %s", rel)

        return recreated

    # ── Sample data population ───────────────────────────────────────────

    async def _populate_sample_data(self, workspace_path: str) -> None:
        """Populate sample data for first-time workspace initialization.

        Creates sample Knowledge Base asset, Notes file, Memory item,
        and a sample project with full scaffold.

        Args:
            workspace_path: Workspace root path (may contain placeholders).
        """
        expanded = self.expand_path(workspace_path)
        root = Path(expanded)

        # 1. Sample Knowledge Base asset
        await anyio.to_thread.run_sync(
            lambda: self._write_file_if_missing(
                root / "Knowledge" / "Knowledge Base" / "sample-knowledge-asset.md",
                SAMPLE_KNOWLEDGE_BASE_CONTENT,
            )
        )

        # 2. Sample Notes file
        await anyio.to_thread.run_sync(
            lambda: self._write_file_if_missing(
                root / "Knowledge" / "Notes" / "sample-note.md",
                SAMPLE_NOTES_CONTENT,
            )
        )

        # 3. Sample memory item
        await anyio.to_thread.run_sync(
            lambda: self._write_file_if_missing(
                root / "Knowledge" / "Memory" / "communication-style.md",
                SAMPLE_MEMORY_CONTENT,
            )
        )

        # 4. Sample project
        try:
            await self._create_sample_project(expanded)
            logger.info("Created sample project")
        except ValueError:
            logger.debug("Sample project already exists, skipping")
        except Exception as e:
            logger.warning("Failed to create sample project: %s", e)

    async def _create_sample_project(self, workspace_path: str) -> None:
        """Create the sample project under Projects/.

        Args:
            workspace_path: Expanded absolute workspace root path.
        """
        project_dir = Path(workspace_path) / "Projects" / "Website Redesign"

        def _check_exists():
            return project_dir.exists()

        if await anyio.to_thread.run_sync(_check_exists):
            raise ValueError("Sample project already exists")

        now = datetime.now(timezone.utc).isoformat()
        project_id = str(uuid4())
        metadata = {
            "id": project_id,
            "name": "Website Redesign",
            "description": "Redesign the marketing website to improve conversion rates and align with updated brand guidelines.",
            "created_at": now,
            "updated_at": now,
            "status": "active",
            "tags": ["marketing", "frontend", "q1-2025"],
            "priority": "high",
            "schema_version": CURRENT_SCHEMA_VERSION,
            "version": 1,
            "update_history": [
                {
                    "version": 1,
                    "timestamp": now,
                    "action": "created",
                    "changes": {},
                    "source": "system",
                }
            ],
        }

        # Create project directory
        await anyio.to_thread.run_sync(
            lambda: project_dir.mkdir(parents=True, exist_ok=True)
        )

        # Write .project.json via the shared helper
        await anyio.to_thread.run_sync(
            lambda: self._write_project_metadata(project_dir, metadata)
        )

        # Create context and instruction files
        await anyio.to_thread.run_sync(
            lambda: self._write_file_if_missing(
                project_dir / "context-L0.md",
                CONTEXT_L0_TEMPLATE.format(section_name="Website Redesign"),
            )
        )
        await anyio.to_thread.run_sync(
            lambda: self._write_file_if_missing(
                project_dir / "context-L1.md",
                CONTEXT_L1_TEMPLATE.format(section_name="Website Redesign"),
            )
        )
        await anyio.to_thread.run_sync(
            lambda: self._write_file_if_missing(
                project_dir / "instructions.md", SAMPLE_PROJECT_INSTRUCTIONS
            )
        )

        # Create system folders
        for folder in sorted(PROJECT_SYSTEM_FOLDERS):
            folder_path = project_dir / folder
            await anyio.to_thread.run_sync(
                lambda fp=folder_path: fp.mkdir(parents=True, exist_ok=True)
            )

        # Populate sample research and report content
        await anyio.to_thread.run_sync(
            lambda: self._write_file_if_missing(
                project_dir / "research" / "competitor-analysis.md",
                SAMPLE_RESEARCH_CONTENT,
            )
        )
        await anyio.to_thread.run_sync(
            lambda: self._write_file_if_missing(
                project_dir / "reports" / "performance-audit.md",
                SAMPLE_REPORT_CONTENT,
            )
        )

        # Update in-memory UUID index
        self._uuid_index[project_id] = project_dir

    def _resolve_workspace_path(self, workspace_path: Optional[str]) -> str:
        """Resolve workspace_path to an expanded absolute path.

        Args:
            workspace_path: Workspace root path, or None to use default.

        Returns:
            Expanded absolute path string.
        """
        if workspace_path is None:
            return self.expand_path(DEFAULT_WORKSPACE_CONFIG["file_path"])
        return self.expand_path(workspace_path)

    @staticmethod
    def _scan_all_project_metadata(projects_dir: Path) -> list[tuple[Path, dict]]:
        """Scan Projects/ directory and read all .project.json files.

        Synchronous method intended for use inside ``anyio.to_thread.run_sync()``.

        Args:
            projects_dir: Absolute path to the Projects/ directory.

        Returns:
            List of (project_dir, metadata_dict) tuples for valid projects.
        """
        results = []
        if not projects_dir.exists():
            return results
        for candidate in projects_dir.iterdir():
            if not candidate.is_dir():
                continue
            meta_file = candidate / ".project.json"
            if not meta_file.exists():
                continue
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                results.append((candidate, meta))
            except (json.JSONDecodeError, OSError):
                logger.warning(
                    "Skipping project with invalid .project.json: %s", candidate.name
                )
        return results

    # ── Private helper methods (Cadence 2) ─────────────────────────────

    def _read_project_metadata(self, project_dir: Path) -> dict:
        """Read ``.project.json`` from a single project directory, applying migration.

        Reads the JSON file, calls ``migrate_if_needed()`` from the schema
        migrations module, and writes back if a migration was applied.

        This is a synchronous method intended for use inside
        ``anyio.to_thread.run_sync()``.

        Args:
            project_dir: Absolute path to the project directory.

        Returns:
            Parsed (and possibly migrated) metadata dict.

        Raises:
            FileNotFoundError: If ``.project.json`` does not exist.
            json.JSONDecodeError: If the file contains invalid JSON.
        """
        meta_file = project_dir / ".project.json"
        raw = json.loads(meta_file.read_text(encoding="utf-8"))
        migrated, was_migrated = migrate_if_needed(raw)
        if was_migrated:
            self._write_project_metadata(project_dir, migrated)
        return migrated

    @staticmethod
    def _write_project_metadata(project_dir: Path, metadata: dict) -> None:
        """Serialize metadata to ``.project.json`` with 2-space indent.

        This is a synchronous method intended for use inside
        ``anyio.to_thread.run_sync()``.

        Args:
            project_dir: Absolute path to the project directory.
            metadata: The metadata dict to write.
        """
        meta_file = project_dir / ".project.json"
        meta_file.write_text(
            json.dumps(metadata, indent=2), encoding="utf-8"
        )

    def _find_project_dir(self, project_id: str, workspace_path: str) -> Path:
        """Look up a project directory by UUID.

        First checks the in-memory ``_uuid_index``.  On a cache miss, falls
        back to a full ``Projects/`` scan via ``_rebuild_uuid_index`` and
        retries.  Raises ``ValueError`` if the project is not found.

        This is a synchronous method intended for use inside
        ``anyio.to_thread.run_sync()``.

        Args:
            project_id: The UUID of the project.
            workspace_path: Expanded absolute workspace root path.

        Returns:
            Absolute ``Path`` to the project directory.

        Raises:
            ValueError: If no project with the given UUID exists.
        """
        # Fast path: check in-memory index
        if project_id in self._uuid_index:
            cached = self._uuid_index[project_id]
            if cached.exists() and (cached / ".project.json").exists():
                return cached

        # Cache miss or stale entry — rebuild index and retry
        self._rebuild_uuid_index(workspace_path)

        if project_id in self._uuid_index:
            return self._uuid_index[project_id]

        raise ValueError(f"Project not found with id: {project_id}")

    @staticmethod
    def _compute_action_type(changes: dict) -> str:
        """Determine the history action type from a changes dict.

        Uses a priority mapping — first match wins:
        ``name`` → ``renamed``, ``status`` → ``status_changed``,
        ``tags`` → ``tags_modified``, ``priority`` → ``priority_changed``,
        otherwise → ``updated``.

        Args:
            changes: Dict of field names that were changed.

        Returns:
            Action type string for the history entry.
        """
        if "name" in changes:
            return "renamed"
        if "status" in changes:
            return "status_changed"
        if "tags" in changes:
            return "tags_modified"
        if "priority" in changes:
            return "priority_changed"
        return "updated"

    @staticmethod
    def _compute_changes_diff(old: dict, new_updates: dict) -> dict:
        """Compute a diff of changed fields between old metadata and new updates.

        Only includes fields whose values actually differ.

        Args:
            old: The current metadata dict.
            new_updates: Dict of field→new_value to apply.

        Returns:
            Dict of ``{field: {"from": old_value, "to": new_value}}`` for
            fields that changed.
        """
        diff: dict = {}
        for field, new_value in new_updates.items():
            old_value = old.get(field)
            if old_value != new_value:
                diff[field] = {"from": old_value, "to": new_value}
        return diff

    @staticmethod
    def _enforce_history_cap(metadata: dict, cap: int = 50) -> None:
        """Trim ``update_history`` to the most recent *cap* entries in-place.

        Args:
            metadata: The project metadata dict (modified in-place).
            cap: Maximum number of history entries to retain.
        """
        history = metadata.get("update_history")
        if history is not None and len(history) > cap:
            metadata["update_history"] = history[-cap:]

    def _get_project_lock(self, project_id: str) -> asyncio.Lock:
        """Return (or create) the ``asyncio.Lock`` for a project UUID.

        Uses ``dict.setdefault`` for atomic insertion, avoiding a TOCTOU
        race if this code is ever called from multiple threads.

        Args:
            project_id: The UUID of the project.

        Returns:
            The ``asyncio.Lock`` associated with this project.
        """
        return self._project_locks.setdefault(project_id, asyncio.Lock())

    def _rebuild_uuid_index(self, workspace_path: str) -> None:
        """Scan ``Projects/`` subdirs and populate the in-memory UUID index.

        Reads each ``.project.json`` to extract the ``id`` field and maps
        it to the project directory path.  Replaces the entire index.

        This is a synchronous method intended for use inside
        ``anyio.to_thread.run_sync()`` or from other synchronous helpers.

        Args:
            workspace_path: Expanded absolute workspace root path.
        """
        projects_dir = Path(workspace_path) / "Projects"
        new_index: dict[str, Path] = {}
        if projects_dir.exists():
            for candidate in projects_dir.iterdir():
                if not candidate.is_dir():
                    continue
                meta_file = candidate / ".project.json"
                if not meta_file.exists():
                    continue
                try:
                    meta = json.loads(meta_file.read_text(encoding="utf-8"))
                    pid = meta.get("id")
                    if pid:
                        new_index[pid] = candidate
                except (json.JSONDecodeError, OSError):
                    logger.warning(
                        "Skipping project with invalid .project.json: %s",
                        candidate.name,
                    )
        self._uuid_index = new_index

    # ── Project CRUD ─────────────────────────────────────────────────────

    async def create_project(
        self, project_name: str, workspace_path: str = None, source: str = "user"
    ) -> dict:
        """Create a new project under Projects/.

        Sets up the full project scaffold including metadata file, context
        files, instructions, and system folders (chats/, research/, reports/).

        Args:
            project_name: Display name for the project (used as directory name).
            workspace_path: Expanded absolute workspace root path. If None,
                uses DEFAULT_WORKSPACE_CONFIG path (expanded).
            source: Who initiated the creation — "user", "agent", "system",
                or "migration". Recorded in the initial update_history entry.

        Returns:
            dict with enriched project metadata: id, name, description,
            status, tags, priority, schema_version, version, update_history,
            created_at, updated_at.

        Raises:
            ValueError: If a project with the same name already exists.
        """
        workspace_path = self._resolve_workspace_path(workspace_path)

        project_dir = Path(workspace_path) / "Projects" / project_name

        # Validate name (length, characters, reserved names, case-insensitive collision)
        def _validate():
            self._validate_project_name(project_name, workspace_path)

        await anyio.to_thread.run_sync(_validate)

        now = datetime.now(timezone.utc).isoformat()
        project_id = str(uuid4())

        metadata = {
            "id": project_id,
            "name": project_name,
            "description": "",
            "created_at": now,
            "updated_at": now,
            "status": "active",
            "tags": [],
            "priority": None,
            "schema_version": CURRENT_SCHEMA_VERSION,
            "version": 1,
            "update_history": [
                {
                    "version": 1,
                    "timestamp": now,
                    "action": "created",
                    "changes": {},
                    "source": source,
                }
            ],
        }

        # Create project directory
        await anyio.to_thread.run_sync(
            lambda: project_dir.mkdir(parents=True, exist_ok=True)
        )

        # Write .project.json via the shared helper
        await anyio.to_thread.run_sync(
            lambda: self._write_project_metadata(project_dir, metadata)
        )

        # Create system files
        await anyio.to_thread.run_sync(
            lambda: self._write_file_if_missing(
                project_dir / "context-L0.md",
                CONTEXT_L0_TEMPLATE.format(section_name=project_name),
            )
        )
        await anyio.to_thread.run_sync(
            lambda: self._write_file_if_missing(
                project_dir / "context-L1.md",
                CONTEXT_L1_TEMPLATE.format(section_name=project_name),
            )
        )
        await anyio.to_thread.run_sync(
            lambda: self._write_file_if_missing(
                project_dir / "instructions.md",
                f"# {project_name} Instructions\n\n[Add project instructions here]\n",
            )
        )

        # Create system folders
        for folder in sorted(PROJECT_SYSTEM_FOLDERS):
            folder_path = project_dir / folder
            await anyio.to_thread.run_sync(
                lambda fp=folder_path: fp.mkdir(parents=True, exist_ok=True)
            )

        # Update in-memory UUID index
        self._uuid_index[project_id] = project_dir

        logger.info("Created project '%s' with id %s", project_name, project_id)
        return metadata

    async def update_project(
        self,
        project_id: str,
        updates: dict,
        source: str = "user",
        workspace_path: str = None,
    ) -> dict:
        """Update project metadata and record change in update_history.

        Acquires a per-project ``asyncio.Lock`` to serialise concurrent
        updates.  When a name change is requested, follows an atomic rename
        strategy: (1) write updated metadata to the existing directory,
        (2) rename the directory, (3) revert metadata on rename failure.

        Args:
            project_id: UUID of the project.
            updates: Dict of fields to update (name, status, tags, priority,
                description).
            source: Who initiated the change — ``"user"``, ``"agent"``,
                ``"system"``, or ``"migration"``.
            workspace_path: Workspace root. If None, uses default.

        Returns:
            Updated project metadata dict.

        Raises:
            ValueError: If project not found, name invalid, or name conflict
                on rename.
        """
        workspace_path = self._resolve_workspace_path(workspace_path)
        lock = self._get_project_lock(project_id)

        async with lock:
            # ── Read current metadata (sync, inside lock) ────────────
            def _read():
                project_dir = self._find_project_dir(project_id, workspace_path)
                metadata = self._read_project_metadata(project_dir)
                return project_dir, metadata

            project_dir, metadata = await anyio.to_thread.run_sync(_read)

            # ── Compute diff ─────────────────────────────────────────
            changes = self._compute_changes_diff(metadata, updates)
            if not changes:
                # Nothing actually changed — return as-is
                return metadata

            # ── Validate new name if renaming ────────────────────────
            new_name = updates.get("name")
            old_name = metadata.get("name")
            renaming = new_name is not None and new_name != old_name

            if renaming:
                self._validate_project_name(new_name, workspace_path, exclude_dir=project_dir.name)

            # Save original for revert on rename failure
            original_metadata = copy.deepcopy(metadata) if renaming else None

            # ── Apply updates to metadata ────────────────────────────
            now = datetime.now(timezone.utc).isoformat()
            for field, new_value in updates.items():
                if field in changes:
                    metadata[field] = new_value

            metadata["version"] = metadata.get("version", 1) + 1
            metadata["updated_at"] = now

            # ── Append history entry ─────────────────────────────────
            action = self._compute_action_type(changes)
            history_entry = {
                "version": metadata["version"],
                "timestamp": now,
                "action": action,
                "changes": changes,
                "source": source,
            }
            if "update_history" not in metadata:
                metadata["update_history"] = []
            metadata["update_history"].append(history_entry)
            self._enforce_history_cap(metadata)

            # ── Write & optionally rename ────────────────────────────
            if renaming:
                await self._atomic_rename_project(
                    project_id, project_dir, metadata, new_name,
                    old_name, workspace_path, original_metadata,
                )
            else:
                await anyio.to_thread.run_sync(
                    lambda: self._write_project_metadata(project_dir, metadata)
                )

        return metadata

    async def _atomic_rename_project(
        self,
        project_id: str,
        project_dir: Path,
        metadata: dict,
        new_name: str,
        old_name: str,
        workspace_path: str,
        original_metadata: dict,
    ) -> None:
        """Perform an atomic rename: write metadata, rename dir, revert on failure.

        Args:
            project_id: UUID of the project.
            project_dir: Current project directory path.
            metadata: Updated metadata dict (already has new name).
            new_name: The new project name.
            old_name: The previous project name.
            workspace_path: Expanded workspace root path.
            original_metadata: Snapshot of metadata before any updates,
                used to fully revert on rename failure.
        """
        new_dir = Path(workspace_path) / "Projects" / new_name

        # Step 1: Write updated metadata to existing directory
        await anyio.to_thread.run_sync(
            lambda: self._write_project_metadata(project_dir, metadata)
        )

        # Step 2: Rename directory
        def _rename():
            project_dir.rename(new_dir)

        try:
            await anyio.to_thread.run_sync(_rename)
        except OSError as exc:
            # Step 3: Revert to original metadata on rename failure
            logger.error(
                "OS error renaming project '%s' → '%s': %s", old_name, new_name, exc
            )
            await anyio.to_thread.run_sync(
                lambda: self._write_project_metadata(project_dir, original_metadata)
            )
            raise ValueError(
                f"Failed to rename project directory from '{old_name}' to '{new_name}'"
            ) from exc

        # Update in-memory UUID index to point to new directory
        self._uuid_index[project_id] = new_dir

    @staticmethod
    def _validate_project_name(name: str, workspace_path: str, exclude_dir: str = None) -> None:
        """Validate a project name against naming rules.

        Checks length, allowed characters, reserved names, and
        case-insensitive collision with existing project directories.

        Args:
            name: The proposed project name.
            workspace_path: Expanded workspace root path.
            exclude_dir: Directory name to skip during collision check
                (used during rename to exclude the project's own directory).

        Raises:
            ValueError: If the name is invalid or collides with an existing
                project.
        """
        # Strip leading/trailing whitespace
        stripped = name.strip()
        if stripped != name:
            raise ValueError(
                "Project name must not have leading or trailing whitespace."
            )

        if not _PROJECT_NAME_RE.match(name):
            raise ValueError(
                "Project name must be 1-100 characters: alphanumeric, "
                "spaces, hyphens, underscores, or periods."
            )

        # Check reserved filesystem names (case-insensitive)
        base = name.split(".")[0].upper()
        if base in _RESERVED_NAMES:
            raise ValueError(
                f"'{name}' is a reserved filesystem name and cannot be used."
            )

        # Check case-insensitive collision with existing projects
        projects_dir = Path(workspace_path) / "Projects"
        if projects_dir.exists():
            lower_name = name.lower()
            for candidate in projects_dir.iterdir():
                if candidate.is_dir() and candidate.name.lower() == lower_name:
                    # Skip the project's own directory during rename
                    if exclude_dir and candidate.name == exclude_dir:
                        continue
                    raise ValueError(
                        f"A project named '{name}' already exists."
                    )

    async def delete_project(
        self, project_id: str, workspace_path: str = None
    ) -> bool:
        """Delete a project by UUID.

        Acquires the per-project ``asyncio.Lock`` before removing the
        directory to prevent races with concurrent reads/writes.

        Args:
            project_id: The UUID of the project to delete.
            workspace_path: Workspace root path. If None, uses default.

        Returns:
            True if the project was deleted.

        Raises:
            ValueError: If no project with the given ID is found.
        """
        workspace_path = self._resolve_workspace_path(workspace_path)
        lock = self._get_project_lock(project_id)

        async with lock:
            def _find_and_delete():
                project_dir = self._find_project_dir(project_id, workspace_path)
                name = project_dir.name
                shutil.rmtree(project_dir)
                return name

            deleted_name = await anyio.to_thread.run_sync(_find_and_delete)

        # Clean up in-memory caches (outside lock — lock object itself is being removed)
        self._uuid_index.pop(project_id, None)
        self._project_locks.pop(project_id, None)

        logger.info("Deleted project '%s' (id: %s)", deleted_name, project_id)
        return True

    async def get_project(
        self, project_id: str, workspace_path: str = None
    ) -> dict:
        """Get project metadata by UUID, applying schema migration on read.

        Uses ``_find_project_dir()`` for fast UUID lookup and
        ``_read_project_metadata()`` which applies ``migrate_if_needed()``.
        Acquires a per-project ``asyncio.Lock`` to serialise concurrent
        reads/writes to the same ``.project.json``.

        Args:
            project_id: The UUID of the project.
            workspace_path: Workspace root path. If None, uses default.

        Returns:
            dict with project metadata (migrated to current schema version).

        Raises:
            ValueError: If no project with the given ID is found.
        """
        workspace_path = self._resolve_workspace_path(workspace_path)
        lock = self._get_project_lock(project_id)
        async with lock:
            def _read():
                project_dir = self._find_project_dir(project_id, workspace_path)
                return self._read_project_metadata(project_dir)

            return await anyio.to_thread.run_sync(_read)

    async def list_projects(self, workspace_path: str = None) -> list[dict]:
        """List all projects with metadata, applying schema migration on read.

        Scans the Projects/ directory for subdirectories containing a
        ``.project.json`` file, reads each via ``_read_project_metadata()``
        (which applies ``migrate_if_needed()``), and returns their metadata
        sorted by ``created_at`` descending.

        Args:
            workspace_path: Workspace root path. If None, uses default.

        Returns:
            List of project metadata dicts, sorted by created_at descending.
        """
        workspace_path = self._resolve_workspace_path(workspace_path)
        projects_dir = Path(workspace_path) / "Projects"

        def _scan_dirs():
            """Return list of project directories that contain .project.json."""
            dirs = []
            if not projects_dir.exists():
                return dirs
            for candidate in projects_dir.iterdir():
                if candidate.is_dir() and (candidate / ".project.json").exists():
                    dirs.append(candidate)
            return dirs

        project_dirs = await anyio.to_thread.run_sync(_scan_dirs)

        results = []
        for project_dir in project_dirs:
            try:
                meta = await anyio.to_thread.run_sync(
                    lambda d=project_dir: self._read_project_metadata(d)
                )
                results.append(meta)
            except (json.JSONDecodeError, OSError):
                logger.warning(
                    "Skipping project with invalid .project.json: %s",
                    project_dir.name,
                )

        results.sort(key=lambda m: m.get("created_at", ""), reverse=True)
        return results

    async def get_project_by_name(
        self, name: str, workspace_path: str = None
    ) -> dict:
        """Find a project by display name (case-insensitive directory scan).

        Scans the ``Projects/`` directory for a subdirectory whose name
        matches *name* case-insensitively, then reads and returns its
        metadata via ``_read_project_metadata()`` (which applies schema
        migration on read).

        Args:
            name: The project display name to search for.
            workspace_path: Workspace root path. If None, uses default.

        Returns:
            dict with project metadata for the matching project.

        Raises:
            ValueError: If no project with the given name is found.
        """
        workspace_path = self._resolve_workspace_path(workspace_path)
        projects_dir = Path(workspace_path) / "Projects"

        def _find_by_name():
            if not projects_dir.exists():
                raise ValueError(f"No project found with name: {name}")
            target = name.lower()
            for candidate in projects_dir.iterdir():
                if candidate.is_dir() and candidate.name.lower() == target:
                    meta_file = candidate / ".project.json"
                    if meta_file.exists():
                        return self._read_project_metadata(candidate)
            raise ValueError(f"No project found with name: {name}")

        return await anyio.to_thread.run_sync(_find_by_name)

    async def get_project_history(
        self, project_id: str, workspace_path: str = None
    ) -> list[dict]:
        """Return the ``update_history`` array for a project.

        Locates the project by UUID via ``_find_project_dir()``, reads its
        metadata via ``_read_project_metadata()`` (applying migration if
        needed), and returns just the ``update_history`` list.

        Args:
            project_id: The UUID of the project.
            workspace_path: Workspace root path. If None, uses default.

        Returns:
            List of update_history entry dicts, most recent last.

        Raises:
            ValueError: If no project with the given ID is found.
        """
        workspace_path = self._resolve_workspace_path(workspace_path)

        def _read_history():
            project_dir = self._find_project_dir(project_id, workspace_path)
            metadata = self._read_project_metadata(project_dir)
            return metadata.get("update_history", [])

        return await anyio.to_thread.run_sync(_read_history)



# Global instance
swarm_workspace_manager = SwarmWorkspaceManager()
