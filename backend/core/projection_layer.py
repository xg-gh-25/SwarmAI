"""Copy-based projection layer for skill discovery by the Claude SDK.

This module was extracted from ``agent_sandbox_manager.py`` to isolate
skill projection concerns.  ``AgentSandboxManager`` retains its
non-skill responsibilities (template copying, ``TEMPLATE_FILES``,
``ensure_templates_in_directory``).  ``ProjectionLayer`` is a new class
that owns *only* skill projection into the Claude SDK's discovery
directory (``SwarmWS/.claude/skills/``).

Skills are projected as real directory copies (via ``shutil.copytree``)
rather than symlinks, so that git tracks actual file content and detects
modifications.  Legacy symlinks from prior versions are cleaned up
transparently.

Key public symbols:

- ``ProjectionLayer``  — Singleton that projects skill copies into a
  workspace, respecting tier precedence and allowed-skills lists.

Lifecycle:
    ``ProjectionLayer`` is instantiated once at app startup (singleton),
    receiving the ``SkillManager`` singleton.  Both are created during
    ``InitializationManager.run_full_initialization`` and shared across
    the application via dependency injection.
"""

import logging
import os
import shutil
from pathlib import Path

from core.manifest_loader import ManifestLoader
from core.skill_manager import SkillManager

logger = logging.getLogger(__name__)

# Directories inside a skill that are provisioned separately (npm) and must NOT
# be walked for the freshness check — they are large, machine-generated, and
# not part of the source-vs-dest content contract.
_FRESHNESS_SKIP_DIRS = {"node_modules", "__pycache__", ".git"}

# Bytecode + OS cruft that must never be COPIED into the projected .claude/skills/
# tree — a source skill's __pycache__/*.pyc would otherwise land in the workspace
# and trip chat-brain-check Q3.2's "binary in skills" gate (run_6eaee58a). Matches
# the house convention at swarm_workspace_manager.py:1028 ("a DDD never ships
# bytecode"). Scoped to bytecode+.DS_Store only — never source (.py/.md/.yaml).
# NOTE: this excludes __pycache__ at COPY time; the freshness check
# (_skill_fingerprint) independently excludes __pycache__ from BOTH src+dst walks,
# so the two are symmetric and a real source edit still re-projects.
# PUBLIC (no leading underscore): shared cross-module — plugin_manager imports it
# for its own skill-install copytree sites, so it carries a stability contract.
_COPY_IGNORE_PATTERNS = ("__pycache__", "*.pyc", "*.pyo", ".DS_Store")
COPY_IGNORE = shutil.ignore_patterns(*_COPY_IGNORE_PATTERNS)


def make_untrusted_copy_ignore(source_root):
    """A ``shutil.copytree`` ``ignore`` callable for copying an UNTRUSTED tree.

    Composes ``COPY_IGNORE`` (bytecode/cruft) with an escaping-symlink drop:
    any entry that is a symlink whose ``realpath`` resolves OUTSIDE
    ``source_root`` is excluded, so an untrusted plugin cannot smuggle a symlink
    (e.g. ``leak -> ~/.ssh/id_rsa``) that ``copytree(symlinks=False)`` would
    dereference — copying host-file CONTENT into the agent-discoverable
    ``.claude/skills`` tree (run_0e5f1969, empirically reproduced exfil).

    INTERNAL symlinks (target inside ``source_root``, e.g. ``node_modules/.bin``)
    are preserved — a blanket symlink ban would break legitimate skills. This is
    for the UNTRUSTED plugin-install path ONLY; trusted built-in projection keeps
    plain ``COPY_IGNORE`` (s_persist relies on an escaping symlink being deref'd).

    Fail-closed: an unresolvable ``realpath`` (OSError) → treat as escaping (drop).
    """
    real_root = Path(os.path.realpath(source_root))
    base = shutil.ignore_patterns(*_COPY_IGNORE_PATTERNS)

    def _ignore(dirpath, names):
        ignored = set(base(dirpath, names))
        for name in names:
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                try:
                    target = Path(os.path.realpath(full))
                    if not target.is_relative_to(real_root):
                        ignored.add(name)  # escapes the tree → drop (exfil guard)
                except OSError:
                    ignored.add(name)  # can't resolve → fail-closed, drop
        return ignored

    return _ignore

# Bytecode artifacts that must not linger in a PROJECTED skill dir. Used by the
# freshness check to self-heal installs projected BEFORE COPY_IGNORE existed: a
# stale __pycache__/*.pyc in dst is invisible to the file-set fingerprint (which
# skips __pycache__), so without this a pre-fix bytecode leak would survive every
# boot (the exact Q3.2 recurrence). If dst carries bytecode, it is NOT fresh →
# rmtree + re-copy (now COPY_IGNORE-filtered) purges it. (run_6eaee58a, MED-8)
_BYTECODE_SUFFIXES = (".pyc", ".pyo")

# Dirs to prune while HUNTING bytecode in a projected dst. This is
# _FRESHNESS_SKIP_DIRS MINUS __pycache__ — we must NOT prune __pycache__ here
# (that's exactly where .pyc lives, the thing we're hunting), but we DO prune
# node_modules/.git so the scan stays O(skill source) not O(node_modules) on
# every boot (run_6eaee58a, MED meta-review: perf + walker consistency).
_BYTECODE_SCAN_SKIP_DIRS = _FRESHNESS_SKIP_DIRS - {"__pycache__"}


def _dst_has_bytecode(dst: Path) -> bool:
    """True iff a projected skill dir still contains bytecode.

    Prunes node_modules/.git (``_BYTECODE_SCAN_SKIP_DIRS``) so the scan stays
    O(skill source) not O(node_modules) on every boot — a skill like s_pollinate
    ships a large node_modules that must not be walked here (run_6eaee58a, MED
    meta-review: perf). Critically does NOT prune __pycache__ — that is exactly
    where .pyc lives, the thing being hunted. Short-circuits on first hit.
    """
    try:
        for root, dirnames, filenames in os.walk(dst):
            # Prune node_modules/.git in place (NOT __pycache__ — see above).
            dirnames[:] = [d for d in dirnames if d not in _BYTECODE_SCAN_SKIP_DIRS]
            for fn in filenames:
                if fn.endswith(_BYTECODE_SUFFIXES):
                    return True
    except OSError:
        return False  # can't scan → don't force a re-copy on an IO error
    return False


def _skill_fingerprint(root: Path) -> tuple[frozenset[str], float]:
    """Return (relative-file-path set, max file mtime) for a skill dir.

    Walks FILES only (never stat's the dir itself — a directory's mtime is set
    to copy-time by copytree and would always look "fresh"). node_modules et al.
    are skipped (provisioned separately, huge, not part of the content contract).
    An empty/absent tree returns (∅, -1.0).
    """
    paths: set[str] = set()
    max_mtime = -1.0
    if not root.exists():
        return frozenset(), max_mtime
    for p in root.rglob("*"):
        if any(part in _FRESHNESS_SKIP_DIRS for part in p.relative_to(root).parts):
            continue
        if p.is_file():
            paths.add(str(p.relative_to(root)))
            m = p.stat().st_mtime
            if m > max_mtime:
                max_mtime = m
    return frozenset(paths), max_mtime


def _is_skill_fresh(src: Path, dst: Path) -> bool:
    """True iff ``dst`` is a current copy of ``src`` (skip re-projection).

    Fresh requires BOTH:
      1. identical relative-file SET (catches added/deleted source files), AND
      2. dst's max file-mtime >= src's max file-mtime (catches edits — copytree
         uses copy2 which PRESERVES source mtime, so an edited source file is
         strictly newer than its stale dest copy).

    Fail-safe: a missing dst, an empty src, or any doubt → NOT fresh (re-copy).
    Correctness (never ship a stale skill) is prioritised over the skip.
    """
    if not dst.exists():
        return False
    # Self-heal (run_6eaee58a, MED-8): a dst projected before COPY_IGNORE existed
    # may carry stale __pycache__/*.pyc that the fingerprint can't see (it skips
    # __pycache__). Treat any bytecode in dst as NOT fresh → forces a clean,
    # COPY_IGNORE-filtered re-copy that purges it. Without this the pre-fix leak
    # survives forever (the Q3.2 recurrence this change must actually stop).
    if _dst_has_bytecode(dst):
        return False
    src_paths, src_mtime = _skill_fingerprint(src)
    if not src_paths:
        return False  # empty/unreadable source → don't trust a skip
    dst_paths, dst_mtime = _skill_fingerprint(dst)
    if src_paths != dst_paths:
        return False  # a file was added to or removed from the source
    return dst_mtime >= src_mtime


class ProjectionLayer:
    """Project skill copies into a workspace for Claude SDK discovery.

    Merges skills from all three tiers (built-in, user, plugin) into
    ``SwarmWS/.claude/skills/`` via ``shutil.copytree()``.  Built-in
    skills are always projected unconditionally.  User and plugin skills
    are projected based on the agent's ``allowed_skills`` list or the
    ``allow_all`` flag.

    Stale entries (both legacy symlinks and real directories pointing to
    skills no longer available) are cleaned up on every projection pass.
    Skill source paths are validated to resolve within one of the three
    known tier directories.
    """

    def __init__(self, skill_manager: SkillManager) -> None:
        """Initialise with a ``SkillManager`` for skill discovery.

        Args:
            skill_manager: The application-wide ``SkillManager`` singleton
                used to query the current skill cache.
        """
        self._skill_manager = skill_manager

    async def project_skills(
        self,
        workspace_path: Path,
        allowed_skills: list[str] | None = None,
        allow_all: bool = False,
    ) -> None:
        """Project skill copies into ``workspace_path/.claude/skills/``.

        Built-in skills are **always** projected unconditionally.  For
        user and plugin skills:

        - If *allow_all* is ``True``, project everything.
        - Otherwise, project only those whose ``folder_name`` appears in
          *allowed_skills*.

        Stale entries (for skills no longer in the target set) are
        removed — both legacy symlinks and real directories.  Each skill
        source path is validated to resolve within a known tier directory
        before copying.  ``OSError`` on individual copies is caught,
        logged, and skipped so one bad entry does not block the rest.

        Args:
            workspace_path: Root of the SwarmWorkspace (e.g.
                ``<app_data>/SwarmWS``).
            allowed_skills: Folder names the current agent may access.
                Ignored when *allow_all* is ``True``.
            allow_all: If ``True``, project all skills from every tier.
        """
        skills_dir = workspace_path / ".claude" / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)

        cache = await self._skill_manager.get_cache()

        # Platform filter: Hive (EC2 Linux) excludes macOS/desktop skills
        is_hive = os.environ.get("SWARMAI_MODE") == "hive"
        _hive_excluded = {"macos", "desktop"} if is_hive else set()

        # Determine which skills to project
        allowed_set = set(allowed_skills) if allowed_skills else set()
        target_skills: dict[str, Path] = {}
        skipped_platform: list[str] = []

        for folder_name, info in cache.items():
            # Filter by platform before tier check
            if info.platform in _hive_excluded:
                skipped_platform.append(folder_name)
                continue

            if info.source_tier == "built-in":
                # Built-in skills are ALWAYS projected
                target_skills[folder_name] = info.path
            elif allow_all:
                target_skills[folder_name] = info.path
            elif folder_name in allowed_set:
                target_skills[folder_name] = info.path

        if skipped_platform:
            logger.info(
                "Hive mode: skipped %d platform-incompatible skills: %s",
                len(skipped_platform),
                ", ".join(sorted(skipped_platform)),
            )

        # Create or update copies for each target skill
        for folder_name, skill_path in target_skills.items():
            link_path = skills_dir / folder_name

            # Validate the skill source before copying
            if not self._validate_skill_source(skill_path):
                logger.warning(
                    "Skipping skill '%s': source path %s is outside "
                    "known tier directories",
                    folder_name,
                    skill_path,
                )
                continue

            # Skip-when-fresh (run_bf4cb46e): rmtree+copytree of every skill on
            # every boot was an O(all-skills) filesystem churn that timed out the
            # test TestClient fixture (221s) and slowed every daemon boot. If the
            # existing copy is byte-current with the source (same file-set AND not
            # older — copy2 preserves mtime), skip the whole re-copy + npm re-check.
            # A legacy SYMLINK is never "fresh" (always re-materialised as a copy).
            if (
                link_path.exists()
                and not link_path.is_symlink()
                and _is_skill_fresh(skill_path.resolve(), link_path)
            ):
                logger.debug("Skill '%s' unchanged — skipping re-projection", folder_name)
                continue

            # If entry already exists, remove and re-copy (clean re-copy
            # on every launch is acceptable and avoids stale content)
            if link_path.exists() or link_path.is_symlink():
                try:
                    if link_path.is_symlink():
                        # Legacy symlink — just unlink
                        link_path.unlink()
                    else:
                        shutil.rmtree(link_path)
                except OSError as exc:
                    logger.warning(
                        "Failed to remove existing entry for '%s': %s",
                        folder_name,
                        exc,
                    )
                    continue

            try:
                shutil.copytree(
                    str(skill_path.resolve()),
                    str(link_path),
                    dirs_exist_ok=True,
                    ignore=COPY_IGNORE,
                )
            except OSError as exc:
                logger.error(
                    "Failed to copy skill '%s' from %s: %s",
                    folder_name,
                    skill_path,
                    exc,
                )
                continue

            # Provision npm dependencies declared in manifest.yaml
            manifest = ManifestLoader.load(skill_path)
            if manifest and manifest.dependencies.get("npm"):
                try:
                    installed = ManifestLoader.ensure_dependencies(manifest)
                    if installed:
                        logger.info(
                            "Provisioned npm deps for '%s': %s",
                            folder_name, installed,
                        )
                except Exception as exc:
                    logger.warning(
                        "Failed to provision deps for '%s': %s",
                        folder_name, exc,
                    )

        # Project _shared/ utilities (used by skill generators for path resolution)
        import sys as _sys
        if hasattr(_sys, "_MEIPASS"):
            shared_source = Path(_sys._MEIPASS) / "skills" / "_shared"
        else:
            shared_source = Path(__file__).resolve().parent.parent / "skills" / "_shared"
        if shared_source.is_dir():
            shared_dest = skills_dir / "_shared"
            if shared_dest.exists():
                shutil.rmtree(shared_dest)
            try:
                shutil.copytree(
                    str(shared_source), str(shared_dest), ignore=COPY_IGNORE
                )
            except OSError as exc:
                logger.warning("Failed to project _shared/: %s", exc)

        # Clean up stale entries (both legacy symlinks and real directories)
        self._cleanup_stale_entries(
            skills_dir, set(target_skills.keys()) | {"_shared"},
        )

    def _cleanup_stale_entries(
        self,
        skills_dir: Path,
        target_names: set[str],
    ) -> None:
        """Remove entries in *skills_dir* not present in *target_names*.

        Handles both legacy symlinks and real directories (from the
        copytree migration).  Symlinks are unlinked; real directories
        are removed via ``shutil.rmtree()``.  A warning is logged for
        each stale entry removed.

        Args:
            skills_dir: The ``SwarmWS/.claude/skills/`` directory.
            target_names: Set of folder names that *should* have
                entries.
        """
        try:
            entries = list(skills_dir.iterdir())
        except OSError as exc:
            logger.warning(
                "Failed to list skills directory %s: %s",
                skills_dir,
                exc,
            )
            return

        for entry in entries:
            if entry.name not in target_names:
                logger.warning(
                    "Removing stale skill entry: %s",
                    entry,
                )
                try:
                    if entry.is_symlink():
                        entry.unlink()
                    elif entry.is_dir():
                        shutil.rmtree(entry)
                    else:
                        entry.unlink()
                except OSError as exc:
                    logger.error(
                        "Failed to remove stale entry %s: %s",
                        entry,
                        exc,
                    )

    def _validate_skill_source(self, source: Path) -> bool:
        """Verify *source* resolves within a known tier directory.

        Resolves the source path to its canonical form and checks that
        it falls within one of the three skill source tier directories
        managed by the ``SkillManager``.

        Args:
            source: The path to validate (typically ``SkillInfo.path``).

        Returns:
            ``True`` if the source is within a known tier directory,
            ``False`` otherwise.
        """
        try:
            resolved = source.resolve()
        except OSError:
            return False

        tier_roots = [
            self._skill_manager.builtin_path,
            self._skill_manager.user_skills_path,
            self._skill_manager.plugin_skills_path,
        ]

        for tier_root in tier_roots:
            try:
                resolved_root = tier_root.resolve()
                if resolved.is_relative_to(resolved_root):
                    return True
            except (OSError, ValueError):
                continue

        return False
