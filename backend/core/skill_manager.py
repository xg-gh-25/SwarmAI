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

    ``platform`` declares which environments the skill supports:
    ``"all"`` (default), ``"macos"`` (macOS-only, e.g. AppleScript,
    Accessibility APIs), or ``"desktop"`` (needs a display/GUI).
    Used by ``ProjectionLayer`` to filter skills in Hive (EC2 Linux).

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
    source_tier: Literal["built-in", "ddd", "user", "plugin"]
    path: Path
    platform: str = "all"  # "all" | "macos" | "desktop"
    content: str | None = None
    consumes_artifacts: tuple[str, ...] = ()
    produces_artifact: str | None = None
    # Optional frontmatter overrides for the Capabilities domain (run_b5d98151).
    # When None, the router derives category/visibility from the folder name.
    category: str | None = None
    visibility: str | None = None


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
# SKILL.md frontmatter parsing (shared by parse_skill_md + lint_skills.py)
# ---------------------------------------------------------------------------


def parse_frontmatter(path: Path) -> tuple[dict, str]:
    """Extract YAML frontmatter and body from a SKILL.md file.

    This is the **single source of truth** for SKILL.md parsing.  Both
    the runtime ``parse_skill_md()`` and the CI linter
    (``scripts/lint_skills.py``) call this function, so format changes
    are always consistent.

    Args:
        path: Absolute path to the SKILL.md file.

    Returns:
        ``(meta, body)`` — *meta* is the parsed YAML dict (may be empty
        ``{}`` if no frontmatter found), *body* is the markdown content
        after the closing ``---``.

    Raises:
        SkillParseError: If the frontmatter is malformed.
        FileNotFoundError: If *path* does not exist.
    """
    raw = path.read_text(encoding="utf-8")
    meta: dict = {}
    body = raw

    stripped = raw.lstrip()
    if not stripped.startswith(_FRONTMATTER_DELIM):
        return meta, body

    after_open = stripped[len(_FRONTMATTER_DELIM):]
    if not after_open.startswith("\n"):
        raise SkillParseError(
            f"Malformed frontmatter in {path}: "
            "opening delimiter must be followed by a newline"
        )
    after_open = after_open[1:]

    close_idx = after_open.find(f"\n{_FRONTMATTER_DELIM}")
    if close_idx == -1:
        raise SkillParseError(
            f"Malformed frontmatter in {path}: "
            "missing closing '---' delimiter"
        )

    yaml_block = after_open[:close_idx]
    rest = after_open[close_idx + len(f"\n{_FRONTMATTER_DELIM}"):]
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

    return meta, body


# ---------------------------------------------------------------------------
# SKILL.md parsing
# ---------------------------------------------------------------------------


def parse_skill_md(
    path: Path,
    folder_name: str,
    source_tier: Literal["built-in", "ddd", "user", "plugin"],
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
    meta, body = parse_frontmatter(path)

    name = meta.get("name")
    description = meta.get("description")
    version = str(meta.get("version", "1.0.0"))

    # --- Fallbacks and normalization for required fields ---
    if not name:
        logger.warning(
            "SKILL.md at %s missing 'name'; falling back to folder name",
            path,
        )
        name = folder_name
    else:
        # SDK matches slash commands case-sensitively against user input.
        # Users type lowercase (/weather), so name must be lowercase.
        normalized = str(name).lower()
        if normalized != str(name):
            logger.warning(
                "SKILL.md at %s has non-lowercase name '%s'; "
                "normalizing to '%s' for SDK command matching",
                path, name, normalized,
            )
            name = normalized

    if not description:
        logger.warning(
            "SKILL.md at %s missing 'description'; using default",
            path,
        )
        description = f"Skill: {folder_name}"

    # --- Platform metadata (optional, defaults to "all") ---
    platform_raw = meta.get("platform", "all") if meta else "all"
    platform = str(platform_raw).strip().lower()
    if platform not in ("all", "macos", "desktop"):
        logger.warning(
            "SKILL.md at %s has unknown platform '%s'; defaulting to 'all'",
            path, platform,
        )
        platform = "all"

    # --- Artifact metadata (optional) ---
    consumes_raw = meta.get("consumes_artifacts", []) if meta else []
    if isinstance(consumes_raw, str):
        consumes_raw = [consumes_raw]
    consumes = tuple(str(t).strip() for t in consumes_raw if t)

    produces_raw = meta.get("produces_artifact") if meta else None
    produces = str(produces_raw).strip() if produces_raw else None

    # --- Capabilities-domain overrides (optional, run_b5d98151) ---
    # A skill MAY declare `category:` / `visibility:` in frontmatter to override
    # the name-derived defaults. Left None here → router derives from folder name.
    category_raw = meta.get("category") if meta else None
    category = str(category_raw).strip() if category_raw else None
    visibility_raw = meta.get("visibility") if meta else None
    visibility = str(visibility_raw).strip().lower() if visibility_raw else None
    if visibility not in (None, "public", "internal"):
        logger.warning(
            "SKILL.md at %s has unknown visibility '%s'; ignoring (deriving from name)",
            path, visibility,
        )
        visibility = None

    return SkillInfo(
        folder_name=folder_name,
        name=str(name),
        description=str(description),
        version=version,
        source_tier=source_tier,
        path=path.parent,
        platform=platform,
        content=body if load_content else None,
        consumes_artifacts=consumes,
        produces_artifact=produces,
        category=category,
        visibility=visibility,
    )


# ---------------------------------------------------------------------------
# SKILL.md formatting
# ---------------------------------------------------------------------------


def format_skill_md(
    meta: dict | None,
    content: str,
) -> str:
    """Format skill frontmatter + content into a valid SKILL.md string.

    Takes the **full** frontmatter dict and re-emits it verbatim, so ALL
    keys survive a round-trip — ``tier``, ``platform``,
    ``disable-model-invocation``, ``project_scope``, ``trigger``,
    ``do_not_use``, ``consumes_artifacts``, ``produces_artifact``, and any
    future key. The only normalization applied is name-lowercasing (the SDK
    matches slash commands case-sensitively, and users type lowercase).

    Callers override ``name``/``description``/``version`` by setting them on
    ``meta`` before calling; nothing else is touched. Key order is preserved
    (``sort_keys=False`` + dict insertion order), so an update writes the file
    back in the same shape it was read.

    Args:
        meta: The complete frontmatter mapping to emit. ``None``/empty is
            treated as an empty mapping (a body-only SKILL.md).
        content: Markdown body after the frontmatter.

    Returns:
        A complete SKILL.md string ready to be written to disk.

    Note:
        Previously this hardcoded ``meta={name, description, version}`` and
        thus DROPPED every other frontmatter key on ``update_skill``
        write-back (run_3467799d). The fix preserves the full dict.
    """
    meta = dict(meta) if meta else {}

    # Enforce lowercase name — SDK matches slash commands case-sensitively.
    # Preserve the key's original position (reassignment keeps order).
    if meta.get("name") is not None:
        meta["name"] = str(meta["name"]).lower()

    frontmatter = yaml.dump(
        meta,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    ).rstrip("\n")

    return f"---\n{frontmatter}\n---\n\n{content}"


# ---------------------------------------------------------------------------

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
        workspace_root: Path | None = None,
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
            from config import get_app_data_dir
            self.user_skills_path = (
                get_app_data_dir() / "skills"
            )

        if plugin_skills_path is not None:
            self.plugin_skills_path = plugin_skills_path
        else:
            from config import get_app_data_dir as _get_app_data_dir
            self.plugin_skills_path = (
                _get_app_data_dir() / "plugin-skills"
            )

        # Workspace root for the DDD skill registry (per-workspace manifest).
        # None → ddd tier is a pure no-op (production-safe default). The daemon
        # wires the real SwarmWS root at startup (see the module singleton).
        self.workspace_root = workspace_root

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
        """Scan all tiers, apply precedence, return unified dict.

        Scans built-in, ddd (DDD skill registry), user, and plugin in that
        order.  First-seen folder name wins (built-in > ddd > user > plugin).
        Logs warnings for shadowed skills, missing SKILL.md, and parse errors.
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

        # Precedence order: built-in > ddd > user > plugin (first-seen wins).
        # Built-in is scanned FIRST so an enablement skill's official version
        # shadows any DDD-carried copy (a domain skill still living in built-in
        # pre-Run-3 is served by built-in; its ddd entry is a harmless shadow).
        self._scan_tier(self.builtin_path, "built-in", result)
        self._scan_ddd_tier(result)  # index 1 — reads the registry manifest
        self._scan_tier(self.user_skills_path, "user", result)
        self._scan_tier(self.plugin_skills_path, "plugin", result)

        # Return sorted by folder_name for deterministic ordering
        return dict(sorted(result.items()))

    def _scan_ddd_tier(self, result: dict[str, SkillInfo]) -> None:
        """Merge DOMAIN skills from the DDD skill-registry manifest (source_tier="ddd").

        Reads the per-workspace manifest (NOT a live filesystem walk) — cheap, and
        the registry engine owns freshness. First-seen wins, so a name already
        claimed by built-in is skipped here (built-in > ddd precedence).

        Fail-soft: any error is contained — a broken registry must NEVER take down
        skill discovery (this runs inside scan_all, the choke point for 29 callers).
        """
        if self.workspace_root is None:
            return  # no workspace wired → pure no-op (production-safe default)
        try:
            from core import ddd_skill_registry
            records = ddd_skill_registry.read_manifest(self.workspace_root)
        except Exception as exc:  # noqa: BLE001 — defense: registry must never break discovery
            logger.warning("ddd tier: registry read failed, skipping: %s", exc)
            return
        # Containment roots the manifest path MUST resolve within (parity with
        # _scan_tier's per-entry guard): a domain skill legitimately lives under
        # built-in (pre-Run-3) or <workspace>/Projects (the package). A manifest
        # poisoned with "../etc" / an absolute path / a symlink escape must NOT
        # enter the discovery cache (Gate-2 MED — defense in depth; projection
        # already blocks the COPY, this also blocks the DISCOVERY).
        _ddd_roots: list[Path] = []
        for _r in (self.builtin_path, self.workspace_root / "Projects"):
            try:
                _ddd_roots.append(_r.resolve())
            except (OSError, ValueError):
                continue

        for rec in records:
            folder_name = rec.get("skill", "")
            if not folder_name or folder_name in result:
                continue  # built-in (or earlier ddd) already claimed this name
            skill_dir = Path(rec["path"])
            # Reject symlinks + out-of-root paths (mirror _scan_tier's guards).
            try:
                if skill_dir.is_symlink():
                    logger.warning("ddd tier: %s is a symlink — skipped", folder_name)
                    continue
                resolved = skill_dir.resolve()
                if not any(resolved.is_relative_to(root) for root in _ddd_roots):
                    logger.warning(
                        "ddd tier: %s path %s outside known roots — skipped",
                        folder_name, skill_dir,
                    )
                    continue
            except (OSError, ValueError) as exc:
                logger.warning("ddd tier: %s path check failed: %s", folder_name, exc)
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.is_file():
                logger.info("ddd tier: %s SKILL.md missing at %s — skipped",
                            folder_name, skill_dir)
                continue
            try:
                info = parse_skill_md(
                    skill_md, folder_name, "ddd", load_content=False,
                )
                result[folder_name] = info
            except Exception as exc:  # noqa: BLE001 — one bad skill can't break the tier
                logger.warning("ddd tier: failed to parse %s: %s", folder_name, exc)

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

            # Skip underscore-prefixed helper directories (e.g. '_shared').
            # These hold resources shared across skills, are NOT skills
            # themselves, and have no SKILL.md by design — so they must not be
            # treated as malformed skill candidates. Note: real skill folders are
            # named 's_<name>' (start with 's', not '_'), so this never matches a
            # skill. Previously '_shared' logged a WARNING ("has no SKILL.md —
            # skipping") on every session start (×14/day of pure noise).
            if folder_name.startswith("_"):
                logger.debug(
                    "Skipping shared-resources directory %s in tier %s",
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
        # Fresh skill: no prior frontmatter to preserve — a 3-key meta.
        md_content = format_skill_md(
            meta={
                "name": name,
                "description": description,
                "version": "1.0.0",
            },
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

        # Read current SKILL.md — parse_frontmatter returns the FULL meta dict
        # so every existing key survives the write-back (tier, platform,
        # disable-model-invocation, project_scope, trigger, …). Reading via
        # parse_skill_md alone would only recover the curated SkillInfo view
        # and silently drop the rest (run_3467799d).
        raw_meta, raw_body = parse_frontmatter(cached.path / "SKILL.md")
        merged_meta = dict(raw_meta)

        # Override ONLY the three user-editable fields, in place (preserving
        # each key's original position; format_skill_md re-lowercases name).
        if name is not None:
            merged_meta["name"] = name
        if description is not None:
            merged_meta["description"] = description
        # version is not user-editable here; keep whatever the file had.

        # Self-heal required keys if the on-disk file omitted them, mirroring
        # the fallbacks parse_skill_md used to materialize. Without this, the
        # raw parse_frontmatter path would write a name/description/version-less
        # file back — which fails scripts/lint_skills.py (name + description are
        # CI-required). setdefault preserves any present value + key order.
        merged_meta.setdefault("name", folder_name)
        merged_meta.setdefault("description", f"Skill: {folder_name}")
        merged_meta.setdefault("version", "1.0.0")

        merged_content = content if content is not None else raw_body

        md_text = format_skill_md(
            meta=merged_meta,
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
