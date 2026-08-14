"""canvas_noise — the ONE authority for "this path SHAPE never surfaces in Canvas".

## Why this exists

``is_canvas_surfaceable`` (canvas_surface.py) admits *any* file the session touched
OUTSIDE SwarmWS: in a git repo → ``external-diff``, plain FS → ``external-nodiff``.
That "universal ledger" intent is right for a repo the user is developing, but it had
NO denylist — so every piece of machine scratch the agent writes outside the tree
became a persistent OUTPUTS row that also AUTO-POPPED the Canvas
(``AUTO_POP_KINDS`` includes both external kinds). In practice that is a flood of
``/private/tmp/p1.diff``, ``/private/tmp/ev2.json``, ``mcs-telemetry-*.log``,
``~/.claude/shell-snapshots/*``, ``~/Library/Caches/**`` — none of which a human
reviews. INSIDE the tree this class of noise is already killed by
``needs_human_review`` (dot-segment + ``git check-ignore``); outside it, neither
layer applies (a plain-FS temp dir has no ``.gitignore``), so the noise needs its
own predicate.

## Why a separate module (and only ONE of it)

``run_4de279ca`` deleted the frontend ``isBookkeepingPath``/``BOOKKEEPING_DIRS``
denylist precisely because it was a second copy that DRIFTED from the backend
verdict. So this is deliberately the SOLE copy of the generic noise shapes:
``canvas_surface`` calls ``is_noise_path`` for outside-tree paths, and
``workspace_surface_watcher`` composes its cheap pre-filter from
``NOISE_SEGMENTS`` rather than re-listing them. Add a shape HERE, never inline.

Scope discipline — this module answers "is this path machine noise?", NOT "should a
human review it" (``needs_human_review``, byte-stable, untouched) and NOT "may this
path be rendered" (the ``surfaced_paths`` render gate). A denied path simply never
becomes a row, so it also never enters the render allow-set.

## Contract

``is_noise_path(path)`` is PURE — string/shape only, NO filesystem I/O per call, so
it is safe on the streaming hot path and testable without fixtures. It judges only
ABSOLUTE paths (a relative path returns False: resolve first, then ask). The root
table is built once, lazily, and cached (it reads ``$HOME``/``$TMPDIR``).

Bias: deny only what is UNAMBIGUOUSLY machine-owned. A false deny silently loses a
row the user wanted, which is worse than one extra row — so ambiguous names
(``target``, ``out``, ``coverage``, ``.vscode``, ``~/Library/CloudStorage``) are
deliberately NOT denied, and author-facing subtrees under a denied root are rescued
by ``_root_exceptions``.
"""
from __future__ import annotations

import functools
import os
import tempfile
from pathlib import Path

__all__ = ["is_noise_path", "NOISE_SEGMENTS"]

# ── Path SEGMENTS that never carry a reviewable deliverable ───────────────────
# Matched against ANY segment of the path. Shared with
# workspace_surface_watcher._SKIP_SEGMENTS (which unions its SwarmWS-specific dirs
# on top) so the two layers can never drift.
#
# Only unambiguously machine-owned names. Deliberately EXCLUDED as too generic /
# genuinely author-facing: `target` (Rust/Maven, but also a plain English dir name),
# `out`, `coverage`, `.vscode` (users review committed editor settings), `Pods`.
NOISE_SEGMENTS = frozenset({
    # VCS internals
    ".git", ".hg", ".svn",
    # Python
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox",
    ".venv", "venv", "site-packages", ".eggs", ".hypothesis",
    # JS/TS
    "node_modules", ".next", ".nuxt", ".svelte-kit", ".turbo",
    ".parcel-cache", ".vite", ".angular", "bower_components",
    # build / infra / generic caches
    "dist", "build", ".gradle", ".terraform", ".serverless", ".cache",
    ".nyc_output", "htmlcov", ".sass-cache", ".DS_Store", ".idea",
})

# ── File EXTENSIONS that are compiled/ephemeral machine output ────────────────
# NOTE `.lock` is deliberately ABSENT: `Cargo.lock` / `uv.lock` / `poetry.lock` are
# committed files a user legitimately reviews in a diff. Real process locks live
# under already-denied roots (`~/.swarm-ai/*.lock`).
_NOISE_EXTENSIONS = frozenset({
    # compiled / linked
    ".pyc", ".pyo", ".pyd", ".so", ".dylib", ".dll", ".o", ".obj", ".a", ".class",
    # ephemeral runtime / editor / download scratch
    ".log", ".pid", ".sock", ".swp", ".swo", ".tmp", ".temp",
    ".bak", ".orig", ".rej", ".crdownload", ".part", ".partial",
})

# ── Exact BASENAMES that are pure filesystem/OS bookkeeping ───────────────────
_NOISE_BASENAMES = frozenset({
    ".DS_Store", "Thumbs.db", "desktop.ini", ".localized", ".gitkeep", ".keep",
})

# ── Root PREFIXES: whole subtrees that are machine-owned ──────────────────────
# Absolute, platform-shaped. `/tmp` and `/private/tmp` are BOTH listed: on macOS
# `/tmp` is a symlink to `/private/tmp`, so a resolved path arrives in the
# `/private/...` form, but a caller that skipped `.resolve()` must still be caught.
_ABS_NOISE_ROOTS: tuple[str, ...] = (
    # OS temp — the reported flood (`/private/tmp/p1.diff`, `ev2.json`, telemetry logs)
    "/tmp", "/private/tmp", "/var/tmp", "/private/var/tmp", "/dev/shm",
    # macOS per-user darwin dir: $TMPDIR + system caches live here, never user content
    "/var/folders", "/private/var/folders",
    # pseudo / runtime / system filesystems
    "/dev", "/proc", "/sys", "/run",
    "/var/run", "/private/var/run", "/var/log", "/private/var/log",
    "/var/db", "/private/var/db", "/var/cache", "/private/var/cache",
    # OS-owned program + config trees
    "/etc", "/private/etc", "/usr", "/bin", "/sbin", "/opt",
    "/System", "/Library", "/Applications",
)

# ── $HOME-relative noise roots ────────────────────────────────────────────────
# Package-manager caches, toolchain state, and app-internal state. macOS
# `~/Library/CloudStorage` and `~/Library/Mobile Documents` (iCloud/Dropbox/OneDrive
# mounts) are deliberately NOT here — those hold real user documents.
_HOME_NOISE_ROOTS: tuple[str, ...] = (
    # generic + package-manager caches
    ".cache", ".npm", ".yarn", ".pnpm-store", ".bun", ".deno",
    ".cargo", ".rustup", ".gradle", ".m2", ".ivy2", ".sbt",
    ".pyenv", ".nvm", ".rbenv", ".rvm", ".conda", ".docker", ".Trash",
    ".local/share", ".local/state", ".local/pipx", "go/pkg",
    # the Claude SDK's own state: history.jsonl, sessions/, shell-snapshots/, statsig/
    ".claude",
    # editor/extension payloads (NOT `.vscode` itself — see NOISE_SEGMENTS note)
    ".vscode/extensions", ".cursor/extensions", ".kiro/logs",
    # macOS app-internal state
    "Library/Caches", "Library/Logs", "Library/Containers",
    "Library/Group Containers", "Library/Application Support",
    "Library/Saved Application State", "Library/Developer",
    "Library/WebKit", "Library/HTTPStorages", "Library/Cookies",
    # Windows
    "AppData/Local/Temp", "AppData/Local/Microsoft", "AppData/Roaming/npm-cache",
)

# ── SwarmAI's own state dir ───────────────────────────────────────────────────
# `~/.swarm-ai` is app plumbing (data.db, *.json state, logs/, daemon/) — noise. But
# it is NOT denied wholesale: skill sources under it ARE author-facing deliverables a
# user reviews, and SwarmWS is the workspace itself. Those are rescued below.
_SWARM_STATE_ROOT = ".swarm-ai"
_SWARM_AUTHOR_FACING: tuple[str, ...] = (
    ".swarm-ai/SwarmWS",
    ".swarm-ai/skills",
    ".swarm-ai/built-in-skills",
    ".swarm-ai/plugin-skills",
)


def _variants(p: Path) -> set[Path]:
    """A root's literal form PLUS its symlink-resolved form.

    Both are valid arrival shapes: the emit gate resolves (``/tmp/x`` →
    ``/private/tmp/x`` on macOS) but a caller that skipped ``.resolve()`` must still
    be caught. Non-absolute or unresolvable entries are dropped.
    """
    out: set[Path] = set()
    if p.is_absolute():
        out.add(p)
    try:
        r = p.resolve()
        if r.is_absolute():
            out.add(r)
    except (OSError, RuntimeError):
        pass
    return out


@functools.lru_cache(maxsize=1)
def _noise_roots() -> tuple[Path, ...]:
    """Build the root table once (reads ``$HOME``/``$TMPDIR``; cached for the hot path)."""
    roots: set[Path] = set()
    for raw in _ABS_NOISE_ROOTS:
        roots |= _variants(Path(raw))

    # Platform temp: Linux `/tmp`, macOS `/var/folders/**/T`, Windows `%TEMP%`.
    for env_key in ("TMPDIR", "TEMP", "TMP"):
        val = os.environ.get(env_key)
        if val:
            roots |= _variants(Path(val))
    try:
        roots |= _variants(Path(tempfile.gettempdir()))
    except Exception:  # noqa: BLE001 — a broken temp env must not break classification
        pass

    # Windows OS-owned trees (no-ops elsewhere — the env vars are absent).
    for env_key in ("WINDIR", "SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
        val = os.environ.get(env_key)
        if val:
            roots |= _variants(Path(val))

    try:
        home = Path.home()
    except (RuntimeError, OSError):
        home = None
    if home is not None:
        for rel in _HOME_NOISE_ROOTS:
            roots |= _variants(home / rel)
        roots |= _variants(home / _SWARM_STATE_ROOT)

    # Longest first: purely cosmetic for a boolean answer, but makes a debug log of
    # the matched root show the most specific one.
    return tuple(sorted(roots, key=lambda r: len(str(r)), reverse=True))


@functools.lru_cache(maxsize=1)
def _root_exceptions() -> tuple[Path, ...]:
    """Author-facing subtrees that a denied root must NOT swallow (see
    ``_SWARM_AUTHOR_FACING``). Checked BEFORE the root table."""
    try:
        home = Path.home()
    except (RuntimeError, OSError):
        return ()
    out: set[Path] = set()
    for rel in _SWARM_AUTHOR_FACING:
        out |= _variants(home / rel)
    return tuple(out)


def clear_noise_cache() -> None:
    """Drop the cached root tables (tests that repoint ``$HOME``/``$TMPDIR``)."""
    _noise_roots.cache_clear()
    _root_exceptions.cache_clear()


def is_noise_path(path: "str | Path") -> bool:
    """True if ``path``'s SHAPE marks it machine noise that must never surface.

    Pure (no per-call filesystem I/O) and never raises. Judges ABSOLUTE paths only —
    a relative path returns False (resolve it first, then ask).

    Precedence: basename/extension → segment → root-exception → root. The
    exception list rescues an author-facing subtree from a denied ROOT only; it does
    NOT rescue a ``__pycache__``/``.pyc`` inside that subtree (correct: a compiled
    artifact under ``~/.swarm-ai/skills`` is still noise).

    Fail direction is OPEN (return False → the path still surfaces). Per the module
    bias, silently losing a row the user wanted is worse than one extra row.
    """
    try:
        p = Path(os.path.expanduser(str(path)))
        if not p.is_absolute():
            return False

        name = p.name
        if name in _NOISE_BASENAMES:
            return True
        if p.suffix.lower() in _NOISE_EXTENSIONS:
            return True
        # editor/office scratch: `foo.py~` (vim/emacs), `.#foo.py` (emacs lock),
        # `~$report.docx` (Word lock file)
        if name.endswith("~") or name.startswith(".#") or name.startswith("~$"):
            return True

        # parts[0] is the anchor ("/" or "C:\\") — never a real segment.
        for seg in p.parts[1:]:
            if seg in NOISE_SEGMENTS or seg.endswith(".egg-info"):
                return True

        for exc in _root_exceptions():
            if p.is_relative_to(exc):
                return False
        for root in _noise_roots():
            if p.is_relative_to(root):
                return True
        return False
    except Exception:  # noqa: BLE001 — hot-path fail-safe, fail OPEN (see docstring)
        return False
