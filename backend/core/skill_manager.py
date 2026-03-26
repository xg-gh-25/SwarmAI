"""Filesystem-based skill discovery, metadata extraction, and management.

This module replaces the former database-backed ``SkillManager`` and
``LocalSkillManager`` with a single filesystem-only implementation.
Skills are identified by folder name (kebab-case) rather than DB UUIDs.

Public symbols (Task 1 — parsing utilities):

- ``SkillInfo``             — Immutable dataclass for skill metadata
- ``parse_skill_md``        — Parse a SKILL.md file into ``SkillInfo``
- ``format_skill_md``       — Format metadata + content into SKILL.md string
- ``validate_folder_name``  — Validate a folder name against security rules

The full ``SkillManager`` class (scan, cache, CRUD) will be added in Task 2.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from fastapi import HTTPException

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SkillParseError(Exception):
    """Raised when a SKILL.md file cannot be parsed.

    The message always includes the file path and a description of the
    malformation so callers can surface actionable diagnostics.
    """


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_FOLDER_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
_MAX_FOLDER_NAME_LEN = 128
_FRONTMATTER_DELIM = "---"


# ---------------------------------------------------------------------------
# SkillInfo dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SkillInfo:
    """Immutable skill metadata extracted from a SKILL.md file.

    ``content`` is ``None`` when loaded in cache/list mode and populated
    on demand for detail requests.

    ``consumes_artifacts`` and ``produces_artifact`` are optional metadata
    from the SKILL.md YAML frontmatter.  When present, the artifact
    registry auto-discovers upstream artifacts for skills that declare
    ``consumes_artifacts``, and auto-publishes output for skills that
    declare ``produces_artifact``.
    """

    folder_name: str
    name: str
    description: str
    version: str
    source_tier: Literal["built-in", "user", "plugin"]
    path: Path
    content: str | None = None
    consumes_artifacts: tuple[str, ...] = ()
    produces_artifact: str | None = None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_folder_name(name: str) -> None:
    """Validate a skill folder name against security and format rules.

    Accepts names matching ``^[a-zA-Z0-9][a-zA-Z0-9_-]*$`` with a maximum
    length of 128 characters.  Rejects path separators, ``..``, and null
    bytes.

    Raises:
        ValueError: If the name is invalid, with a descriptive message.
    """
    if not name:
        raise ValueError("Folder name must not be empty")

    if "\x00" in name:
        raise ValueError("Folder name must not contain null bytes")

    if ".." in name:
        raise ValueError(
            "Folder name must not contain parent directory references (..)"
        )

    if "/" in name or "\\" in name:
        raise ValueError("Folder name must not contain path separators")

    if len(name) > _MAX_FOLDER_NAME_LEN:
        raise ValueError(
            f"Folder name must not exceed {_MAX_FOLDER_NAME_LEN} characters"
        )

    if not _FOLDER_NAME_RE.match(name):
        raise ValueError(
            "Invalid folder name: must match [a-zA-Z0-9][a-zA-Z0-9_-]*"
        )


# ---------------------------------------------------------------------------
# SKILL.md parsing
# ---------------------------------------------------------------------------


def parse_skill_md(
    path: Path,
    folder_name: str,
    source_tier: Literal["built-in", "user", "plugin"],
    load_content: bool = True,
) -> SkillInfo:
    """Parse a SKILL.md file into a ``SkillInfo`` instance.

    The file is expected to have YAML frontmatter delimited by ``---``
    markers, followed by markdown content.  Missing ``name`` falls back
    to *folder_name*; missing ``description`` falls back to
    ``"Skill: {folder_name}"``.

    Args:
        path: Absolute path to the SKILL.md file.
        folder_name: The skill directory name (used as fallback).
        source_tier: Which tier the skill belongs to.
        load_content: If ``False``, ``content`` is set to ``None``
            (cache/list mode).

    Returns:
        A populated ``SkillInfo`` instance.

    Raises:
        SkillParseError: If the frontmatter is malformed (includes file
            path and malformation description in the message).
        FileNotFoundError: If *path* does not exist.
    """
    raw = path.read_text(encoding="utf-8")

    name: str | None = None
    description: str | None = None
    version: str = "1.0.0"
    meta: dict | None = None
    body = raw  # default: entire file is content if no frontmatter

    # --- Frontmatter extraction ---
    stripped = raw.lstrip()
    if stripped.startswith(_FRONTMATTER_DELIM):
        # Find the closing delimiter
        after_open = stripped[len(_FRONTMATTER_DELIM) :]
        # Must start with newline after opening ---
        if not after_open.startswith("\n"):
            raise SkillParseError(
                f"Malformed frontmatter in {path}: "
                "opening delimiter must be followed by a newline"
            )
        after_open = after_open[1:]  # skip the newline

        close_idx = after_open.find(f"\n{_FRONTMATTER_DELIM}")
        if close_idx == -1:
            raise SkillParseError(
                f"Malformed frontmatter in {path}: "
                "missing closing '---' delimiter"
            )

        yaml_block = after_open[:close_idx]
        # Content starts after the closing --- and its trailing newline
        rest = after_open[close_idx + len(f"\n{_FRONTMATTER_DELIM}") :]
        body = rest.lstrip("\n") if rest else ""

        try:
            meta = yaml.safe_load(yaml_block)
        except yaml.YAMLError as exc:
            raise SkillParseError(
                f"Malformed frontmatter in {path}: {exc}"
            ) from exc

        if not isinstance(meta, dict):
            raise SkillParseError(
                f"Malformed frontmatter in {path}: "
                "expected a YAML mapping, got "
                f"{type(meta).__name__}"
            )

        name = meta.get("name")
        description = meta.get("description")
        version = str(meta.get("version", "1.0.0"))

    # --- Fallbacks for missing fields ---
    if not name:
        logger.warning(
            "SKILL.md at %s missing 'name'; falling back to folder name",
            path,
        )
        name = folder_name

    if not description:
        logger.warning(
            "SKILL.md at %s missing 'description'; using default",
            path,
        )
        description = f"Skill: {folder_name}"

    # --- Artifact metadata (optional) ---
    consumes_raw = meta.get("consumes_artifacts", []) if meta else []
    if isinstance(consumes_raw, str):
        consumes_raw = [consumes_raw]
    consumes = tuple(str(t).strip() for t in consumes_raw if t)

    produces_raw = meta.get("produces_artifact") if meta else None
    produces = str(produces_raw).strip() if produces_raw else None

    return SkillInfo(
        folder_name=folder_name,
        name=str(name),
        description=str(description),
        version=version,
        source_tier=source_tier,
        path=path.parent,
        content=body if load_content else None,
        consumes_artifacts=consumes,
        produces_artifact=produces,
    )


# ---------------------------------------------------------------------------
# SKILL.md formatting
# ---------------------------------------------------------------------------


def format_skill_md(
    name: str,
    description: str,
    version: str,
    content: str,
) -> str:
    """Format skill metadata and content into a valid SKILL.md string.

    Produces a file with YAML frontmatter (``name``, ``description``,
    ``version``) followed by the markdown body.

    Args:
        name: Human-readable skill name.
        description: Short description of the skill.
        version: Semantic version string.
        content: Markdown body after the frontmatter.

    Returns:
        A complete SKILL.md string ready to be written to disk.
    """
    meta = {
        "name": name,
        "description": description,
        "version": version,
    }
    frontmatter = yaml.dump(
        meta,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    ).rstrip("\n")

    return f"---\n{frontmatter}\n---\n\n{content}"


# ---------------------------------------------------------------------------
# Artifact context injection for skills
# ---------------------------------------------------------------------------


def discover_artifacts_for_skill(
    skill_info: SkillInfo,
    project: str | None,
    workspace_root: Path | None = None,
) -> str:
    """Discover upstream artifacts and format them as context for a skill.

    If the skill declares ``consumes_artifacts`` and a project is active,
    reads the artifact registry and returns a markdown block summarizing
    each discovered artifact.  Returns empty string if no artifacts found
    or no project context.

    This is designed to be prepended to a skill's content when injecting
    into the agent's prompt.

    Args:
        skill_info: Parsed skill metadata (must have consumes_artifacts).
        project: Active project name, or None (L0 — returns "").
        workspace_root: Path to SwarmWS root. Auto-detected if None.

    Returns:
        Markdown string with artifact summaries, or "".
    """
    if not skill_info.consumes_artifacts or not project:
        return ""

    if workspace_root is None:
        workspace_root = Path.home() / ".swarm-ai" / "SwarmWS"

    try:
        from core.artifact_registry import ArtifactRegistry
        reg = ArtifactRegistry(workspace_root)
        artifacts = reg.discover(project, *skill_info.consumes_artifacts)
    except Exception:
        return ""

    if not artifacts:
        return ""

    lines = [
        "---",
        f"## Upstream Artifacts (auto-discovered for {project})",
        "",
    ]
    for a in artifacts:
        lines.append(f"### {a.type} — {a.summary}")
        lines.append(f"- **Producer:** {a.producer}")
        lines.append(f"- **ID:** {a.id}")
        if a.data:
            # Include key fields from data (truncated for context budget)
            for key in ("summary", "key_findings", "recommendation",
                        "acceptance_criteria", "scope", "decisions",
                        "files_changed", "findings", "passed", "failed"):
                if key in a.data:
                    val = a.data[key]
                    if isinstance(val, list) and len(val) > 5:
                        val = val[:5] + [f"... ({len(a.data[key])} total)"]
                    lines.append(f"- **{key}:** {val}")
        lines.append("")

    lines.append("---")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# SkillManager (placeholder — scan/cache/CRUD added in Tasks 2 and 3)
# ---------------------------------------------------------------------------


class SkillManager:
    """Filesystem-based skill discovery, cache management, and CRUD.

    Replaces both the former ``SkillManager`` (DB-backed) and
    ``LocalSkillManager``.  Skills are identified by folder name
    (kebab-case) and discovered from three tiers:

    - **built-in** — ships with the app (``backend/skills/``)
    - **user** — created by the user (``~/.swarm-ai/skills/``)
    - **plugin** — installed via plugins (``~/.swarm-ai/plugin-skills/``)

    This placeholder exposes only ``__init__``.  The ``scan_all``,
    ``get_cache``, ``invalidate_cache``, and CRUD methods will be added
    in Tasks 2.1 and 3.1.
    """

    def __init__(
        self,
        builtin_path: Path | None = None,
        user_skills_path: Path | None = None,
        plugin_skills_path: Path | None = None,
    ) -> None:
        """Initialise with configurable tier paths.

        Args:
            builtin_path: Path to built-in skills.  Defaults to
                ``backend/skills/`` relative to the application root.
            user_skills_path: Path to user-created skills.  Defaults to
                ``~/.swarm-ai/skills/``.
            plugin_skills_path: Path to plugin-installed skills.
                Defaults to ``~/.swarm-ai/plugin-skills/``.
        """
        if builtin_path is not None:
            self.builtin_path = builtin_path
        else:
            # Default: backend/skills/ relative to this file's grandparent.
            # In PyInstaller bundles, __file__ resolves inside the temp
            # extraction dir — use sys._MEIPASS instead.
            if getattr(sys, 'frozen', False):
                self.builtin_path = Path(sys._MEIPASS) / "skills"
            else:
                self.builtin_path = (
                    Path(__file__).resolve().parent.parent / "skills"
                )

        if user_skills_path is not None:
            self.user_skills_path = user_skills_path
        else:
            self.user_skills_path = (
                Path.home() / ".swarm-ai" / "skills"
            )

        if plugin_skills_path is not None:
            self.plugin_skills_path = plugin_skills_path
        else:
            self.plugin_skills_path = (
                Path.home() / ".swarm-ai" / "plugin-skills"
            )

        # Cache state — populated by scan_all(), invalidated on CRUD ops.
        # The lock serialises cache rebuilds so concurrent invalidations
        # don't race.  Readers see either the old or new dict (atomic
        # reference swap), never a partial state.
        self._cache: dict[str, SkillInfo] = {}
        self._cache_lock: asyncio.Lock = asyncio.Lock()
        self._cache_valid: bool = False

    # ------------------------------------------------------------------
    # Scan / cache (Task 2)
    # ------------------------------------------------------------------

    async def scan_all(self) -> dict[str, SkillInfo]:
        """Scan all three tiers, apply precedence, return unified dict.

        Scans built-in, user, and plugin directories in that order.
        First-seen folder name wins (built-in > user > plugin).  Logs
        warnings for shadowed skills, missing SKILL.md, and parse errors.
        Creates user and plugin directories if they don't exist.

        Returns:
            Dict keyed by folder_name, sorted alphabetically.
        """
        result: dict[str, SkillInfo] = {}

        # Ensure user and plugin directories exist
        for dir_path in (self.user_skills_path, self.plugin_skills_path):
            try:
                dir_path.mkdir(parents=True, exist_ok=True)
            except (PermissionError, OSError) as exc:
                logger.error(
                    "Cannot create directory %s: %s", dir_path, exc
                )

        # Scan tiers in precedence order: built-in first, then user, then plugin
        tiers: list[tuple[Path, Literal["built-in", "user", "plugin"]]] = [
            (self.builtin_path, "built-in"),
            (self.user_skills_path, "user"),
            (self.plugin_skills_path, "plugin"),
        ]

        for tier_path, tier_name in tiers:
            self._scan_tier(tier_path, tier_name, result)

        # Return sorted by folder_name for deterministic ordering
        return dict(sorted(result.items()))

    def _scan_tier(
        self,
        tier_path: Path,
        tier_name: Literal["built-in", "user", "plugin"],
        result: dict[str, SkillInfo],
    ) -> None:
        """Scan a single tier directory and merge into *result*.

        First-seen folder name wins — if a name already exists in
        *result*, the new entry is treated as shadowed and a warning
        is logged.

        Args:
            tier_path: Root directory of the tier.
            tier_name: Tier label for ``source_tier``.
            result: Accumulator dict (mutated in place).
        """
        if not tier_path.exists():
            if tier_name == "built-in":
                logger.warning(
                    "Built-in skills directory does not exist: %s",
                    tier_path,
                )
            else:
                logger.debug(
                    "Tier directory does not exist: %s (tier: %s)",
                    tier_path,
                    tier_name,
                )
            return

        try:
            entries = sorted(tier_path.iterdir())
        except PermissionError as exc:
            logger.error(
                "Permission denied reading tier directory %s: %s",
                tier_path,
                exc,
            )
            return
        except FileNotFoundError:
            logger.warning(
                "Tier directory disappeared during scan: %s", tier_path
            )
            return

        for entry in entries:
            # Skip non-directories and symlinks
            if entry.is_symlink():
                continue
            if not entry.is_dir():
                logger.debug(
                    "Skipping symlink directory %s in tier %s",
                    entry,
                    tier_name,
                )
                continue

            folder_name = entry.name

            # Skip hidden directories (starting with '.')
            if folder_name.startswith("."):
                logger.debug(
                    "Skipping hidden directory %s in tier %s",
                    entry,
                    tier_name,
                )
                continue
            skill_md = entry / "SKILL.md"

            if not skill_md.exists():
                logger.warning(
                    "Directory %s in tier '%s' has no SKILL.md — skipping",
                    entry,
                    tier_name,
                )
                continue

            # Check precedence: first-seen wins
            if folder_name in result:
                existing = result[folder_name]
                logger.warning(
                    "Skill '%s' in tier '%s' is shadowed by '%s' "
                    "in tier '%s' — skipping",
                    folder_name,
                    tier_name,
                    folder_name,
                    existing.source_tier,
                )
                continue

            # Verify the resolved path stays within the tier directory
            try:
                resolved = skill_md.resolve()
                tier_resolved = tier_path.resolve()
                if not str(resolved).startswith(str(tier_resolved) + "/") and resolved.parent != tier_resolved:
                    logger.warning(
                        "SKILL.md at %s resolves outside tier %s — skipping",
                        skill_md,
                        tier_path,
                    )
                    continue
            except OSError as exc:
                logger.warning(
                    "Cannot resolve path %s: %s — skipping",
                    skill_md,
                    exc,
                )
                continue

            try:
                info = parse_skill_md(
                    path=skill_md,
                    folder_name=folder_name,
                    source_tier=tier_name,
                    load_content=False,
                )
                result[folder_name] = info
            except SkillParseError as exc:
                logger.warning(
                    "Malformed SKILL.md in %s: %s — skipping",
                    entry,
                    exc,
                )
            except FileNotFoundError:
                logger.warning(
                    "SKILL.md disappeared during scan: %s — skipping",
                    skill_md,
                )
            except PermissionError as exc:
                logger.warning(
                    "Permission denied reading %s: %s — skipping",
                    skill_md,
                    exc,
                )

    async def get_cache(self) -> dict[str, SkillInfo]:
        """Return cached skills, rebuilding if invalidated.

        If the cache is valid, returns immediately.  Otherwise acquires
        ``_cache_lock`` (with a 5-second timeout) and rebuilds.  A
        double-check after acquiring the lock avoids redundant rescans
        when multiple coroutines race.

        On lock timeout:
        - If a stale cache exists, return it with a warning.
        - If this is the first scan (empty cache), block until complete.
        """
        if self._cache_valid:
            return self._cache

        # First scan with empty cache — must block until complete
        is_first_scan = not self._cache

        try:
            await asyncio.wait_for(
                self._rebuild_cache(), timeout=5.0
            )
        except asyncio.TimeoutError:
            if is_first_scan:
                # No fallback — block until the rebuild finishes
                logger.warning(
                    "Cache lock timeout on first scan — "
                    "blocking until rebuild completes"
                )
                await self._rebuild_cache()
            else:
                logger.warning(
                    "Cache lock timeout — returning stale cache"
                )

        return self._cache

    async def _rebuild_cache(self) -> None:
        """Acquire the lock and rebuild the cache if still invalid.

        Double-checks ``_cache_valid`` after acquiring the lock so that
        only one coroutine performs the actual rescan.
        """
        async with self._cache_lock:
            # Double-check: another coroutine may have rebuilt while
            # we were waiting for the lock.
            if self._cache_valid:
                return

            new_cache = await self.scan_all()
            # Atomic swap — readers see old or new, never partial
            self._cache = new_cache
            self._cache_valid = True

    def invalidate_cache(self) -> None:
        """Mark cache as stale.  Next ``get_cache()`` triggers rescan.

        This is synchronous — it only flips the flag.
        """
        self._cache_valid = False

    # ------------------------------------------------------------------
    # CRUD (Task 3)
    # ------------------------------------------------------------------

    async def get_skill(self, folder_name: str) -> SkillInfo | None:
        """Look up a single skill by folder name, loading content from disk.

        Returns ``None`` if the skill is not found in any tier.  When
        found, re-reads the SKILL.md with ``load_content=True`` so the
        caller gets the full markdown body.

        Args:
            folder_name: Kebab-case skill directory name.

        Returns:
            ``SkillInfo`` with content populated, or ``None``.
        """
        cache = await self.get_cache()
        cached = cache.get(folder_name)
        if cached is None:
            return None

        # Re-read from disk with content loaded
        skill_md = cached.path / "SKILL.md"
        try:
            return parse_skill_md(
                path=skill_md,
                folder_name=cached.folder_name,
                source_tier=cached.source_tier,
                load_content=True,
            )
        except (FileNotFoundError, SkillParseError, PermissionError) as exc:
            logger.warning(
                "Failed to load content for skill '%s': %s",
                folder_name,
                exc,
            )
            return None

    async def create_skill(
        self,
        folder_name: str,
        name: str,
        description: str,
        content: str,
    ) -> SkillInfo:
        """Create a new user skill in ``~/.swarm-ai/skills/``.

        Validates the folder name, checks for name collisions across all
        tiers, writes the SKILL.md file, and invalidates the cache.

        Args:
            folder_name: Kebab-case directory name for the new skill.
            name: Human-readable skill name.
            description: Short description.
            content: Markdown body for the SKILL.md.

        Returns:
            The newly created ``SkillInfo`` with content populated.

        Raises:
            HTTPException(409): If the folder name collides with an
                existing skill in any tier.
            HTTPException(400): If the folder name is invalid or path
                traversal is detected.
        """
        # Validate folder name format
        try:
            validate_folder_name(folder_name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        # Path containment check
        target_dir = (self.user_skills_path / folder_name).resolve()
        user_resolved = self.user_skills_path.resolve()
        if not str(target_dir).startswith(str(user_resolved) + "/") and target_dir != user_resolved:
            logger.warning(
                "Path traversal attempt detected: %s escapes %s",
                target_dir,
                user_resolved,
            )
            raise HTTPException(
                status_code=400, detail="Invalid path: traversal detected"
            )

        # Check for name collisions across ALL tiers
        cache = await self.get_cache()
        if folder_name in cache:
            existing = cache[folder_name]
            if existing.source_tier == "built-in":
                raise HTTPException(
                    status_code=409,
                    detail=f"Name '{folder_name}' is reserved by a built-in skill",
                )
            raise HTTPException(
                status_code=409,
                detail=f"Skill '{folder_name}' already exists",
            )

        # Create directory and write SKILL.md
        skill_dir = self.user_skills_path / folder_name
        skill_dir.mkdir(parents=True, exist_ok=False)

        skill_md_path = skill_dir / "SKILL.md"
        md_content = format_skill_md(
            name=name,
            description=description,
            version="1.0.0",
            content=content,
        )
        skill_md_path.write_text(md_content, encoding="utf-8")

        self.invalidate_cache()

        # Return the newly created skill with content loaded
        return parse_skill_md(
            path=skill_md_path,
            folder_name=folder_name,
            source_tier="user",
            load_content=True,
        )

    async def update_skill(
        self,
        folder_name: str,
        name: str | None = None,
        description: str | None = None,
        content: str | None = None,
    ) -> SkillInfo:
        """Update an existing user skill's SKILL.md.

        Only user-tier skills can be updated.  Non-``None`` fields
        override the current values; ``None`` fields are left unchanged.

        Args:
            folder_name: Kebab-case directory name of the skill.
            name: New name, or ``None`` to keep current.
            description: New description, or ``None`` to keep current.
            content: New markdown body, or ``None`` to keep current.

        Returns:
            The updated ``SkillInfo`` with content populated.

        Raises:
            HTTPException(404): If the skill does not exist.
            HTTPException(403): If the skill is built-in or plugin.
            HTTPException(400): If path traversal is detected.
        """
        cache = await self.get_cache()
        cached = cache.get(folder_name)

        if cached is None:
            raise HTTPException(
                status_code=404,
                detail=f"Skill '{folder_name}' not found",
            )

        if cached.source_tier == "built-in":
            raise HTTPException(
                status_code=403,
                detail="Built-in skills are read-only",
            )

        if cached.source_tier == "plugin":
            raise HTTPException(
                status_code=403,
                detail="Plugin skills are managed by the plugin system",
            )

        # Path containment check
        skill_md_path = (cached.path / "SKILL.md").resolve()
        user_resolved = self.user_skills_path.resolve()
        if not str(skill_md_path).startswith(str(user_resolved) + "/"):
            logger.warning(
                "Path traversal attempt detected: %s escapes %s",
                skill_md_path,
                user_resolved,
            )
            raise HTTPException(
                status_code=400, detail="Invalid path: traversal detected"
            )

        # Read current SKILL.md to get existing values
        current = parse_skill_md(
            path=cached.path / "SKILL.md",
            folder_name=folder_name,
            source_tier="user",
            load_content=True,
        )

        # Merge: only override non-None fields
        merged_name = name if name is not None else current.name
        merged_desc = description if description is not None else current.description
        merged_content = content if content is not None else (current.content or "")

        md_text = format_skill_md(
            name=merged_name,
            description=merged_desc,
            version=current.version,
            content=merged_content,
        )
        (cached.path / "SKILL.md").write_text(md_text, encoding="utf-8")

        self.invalidate_cache()

        # Return updated skill with content loaded
        return parse_skill_md(
            path=cached.path / "SKILL.md",
            folder_name=folder_name,
            source_tier="user",
            load_content=True,
        )

    async def delete_skill(self, folder_name: str) -> None:
        """Delete a user skill directory.

        Only user-tier skills can be deleted.  The resolved path is
        verified to stay within ``user_skills_path`` before removal.

        Args:
            folder_name: Kebab-case directory name of the skill.

        Raises:
            HTTPException(404): If the skill does not exist.
            HTTPException(403): If the skill is built-in or plugin.
            HTTPException(400): If path traversal is detected.
        """
        cache = await self.get_cache()
        cached = cache.get(folder_name)

        if cached is None:
            raise HTTPException(
                status_code=404,
                detail=f"Skill '{folder_name}' not found",
            )

        if cached.source_tier == "built-in":
            raise HTTPException(
                status_code=403,
                detail="Built-in skills are read-only",
            )

        if cached.source_tier == "plugin":
            raise HTTPException(
                status_code=403,
                detail="Plugin skills must be uninstalled via the plugin system",
            )

        # Path containment check — resolve to canonical form
        target_dir = cached.path.resolve()
        user_resolved = self.user_skills_path.resolve()
        if not str(target_dir).startswith(str(user_resolved) + "/"):
            logger.warning(
                "Path traversal attempt detected on delete: %s escapes %s",
                target_dir,
                user_resolved,
            )
            raise HTTPException(
                status_code=400, detail="Invalid path: traversal detected"
            )

        shutil.rmtree(target_dir)
        self.invalidate_cache()


# Global singleton — importable as ``from core.skill_manager import skill_manager``
skill_manager = SkillManager()
