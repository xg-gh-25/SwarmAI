"""ddd_paths — the six-section DDD layout resolver (single source of truth).

The P1 indirection layer of the DDD six-section self-explaining tree redesign
(design 2026-07-21). Before this module, ~18 sites hardcoded
`project_dir / "TECH.md"` and `base / "Knowledge"`; the physical layout was
smeared across the codebase, so a paradigm change meant a hunt-every-file edit
(the GUI10/OT07 bug class) and the tree could not be renamed to be
self-explaining without breaking loaders.

This resolver centralizes the mapping from a logical KEY (a canonical doc name
or a section name) to its PHYSICAL path under the numbered six-section tree:

    <project>/
    ├── AGENTS.md            ← ① Identity (stays at ROOT — external door-plate)
    ├── 2-understanding/     ← ② Understanding: 4 canonical docs + knowledge/ corpus
    │   ├── PRODUCT.md TECH.md IMPROVEMENT.md PROJECT.md
    │   └── knowledge/       ← recall corpus (was Projects/<x>/Knowledge/)
    ├── 3-gates/             ← ③ Gates (was gates/)
    ├── 4-capabilities/      ← ④ Capabilities (was skills/)
    ├── aim.json             ← ① manifest (root) · bindings.yaml ⑤ + REFRESHER.md ⑥
    │                          are single well-known files kept at ROOT for now
    │                          (bindings.yaml has 67 consumers — relocating it is a
    │                          separate contract migration, not bundled here).
    └── .artifacts/          ← pipeline working memory (not a section)

**Strangler migration semantics (design H, run Gate-1=B):**
  - READ  (``ddd_path``): new-then-old fallback. If the NEW path is absent but
    the file/dir exists at its OLD (pre-migration) location, return the OLD path
    so un-migrated projects keep resolving. This is what makes per-DDD rollout
    safe (no big-bang).
  - WRITE (``ddd_write_path``): ALWAYS the new path. Never dual-writes — a write
    that could land at old-or-new is the split-brain bug class this design kills.

The 4 canonical doc NAMES remain ``project_registry.DDD_CANONICAL_DOCS`` (their
SSOT is unchanged); this module maps their LOCATION only.
"""

from __future__ import annotations

from pathlib import Path

from core.project_registry import DDD_CANONICAL_DOCS

# ─── Layout constants (the ONE place the physical tree is described) ─────────

# Section directory names, numbered so the file tree reads ①→⑥ top-to-bottom.
UNDERSTANDING_DIR = "2-understanding"
GATES_DIR = "3-gates"
CAPABILITIES_DIR = "4-capabilities"
# ⑤ Delivery (bindings.yaml) + ⑥ Refresher (REFRESHER.md) are single well-known
# files kept at ROOT for now — bindings.yaml has 67 consumers, so relocating it
# into a 5-delivery/ dir is a separate contract migration deliberately NOT bundled
# into this run. These constants name the FUTURE dirs (reserved) but the active
# key map resolves ⑤⑥ to root until that migration happens.
DELIVERY_DIR = "5-delivery"     # reserved (not yet materialized)
REFRESHER_DIR = "6-refresher"   # reserved (not yet materialized)

# The recall corpus lives UNDER ② (was the per-DDD Projects/<x>/Knowledge/).
KNOWLEDGE_CORPUS_DIR = f"{UNDERSTANDING_DIR}/knowledge"

# Governed assets (0..N, kind-open) live under an UN-NUMBERED assets/<kind>/ dir
# — deliberately outside the ①→⑥ numbering (it sorts after ⑥ like .artifacts/;
# "numbering is cosmetic, read by path never by sort" — design 2026-07-21 §6).
# A data-agent DDD's moat (the unified SDK: client+catalog+validate_sql+
# data-contract) is the `data-source` asset. This is DISTINCT from the misc
# `assets/` loose-files meaning (diagrams/decks) — governed assets are keyed by
# <kind> subdir; loose misc files (if any) are not.
ASSETS_DIR = "assets"

# ① Identity file — stays at project ROOT (the external "this is a DDD" marker;
# Claude Code / Kiro / Quick read it at a fixed root path, design H4).
IDENTITY_FILE = "AGENTS.md"

# Logical section KEY → physical relative dir under the project root.
# Callers use the KEY; only this map knows the numbered layout.
_SECTION_KEY_TO_DIR: dict[str, str] = {
    "understanding": UNDERSTANDING_DIR,
    "knowledge": KNOWLEDGE_CORPUS_DIR,   # ② recall corpus
    "gates": GATES_DIR,
    "capabilities": CAPABILITIES_DIR,
    "skills": CAPABILITIES_DIR,          # legacy alias → ④
    "delivery": ".",                     # ⑤ bindings.yaml kept at root (see note above)
    "refresher": ".",                    # ⑥ REFRESHER.md kept at root (see note above)
}

# For each logical KEY, where the same content lived BEFORE the migration.
# Used by the strangler READ fallback so un-migrated DDDs keep resolving.
_SECTION_KEY_TO_OLD_DIR: dict[str, str] = {
    "understanding": ".",         # 4 docs were at project root
    "knowledge": "Knowledge",     # per-DDD Knowledge/
    "gates": "gates",
    "capabilities": "skills",
    "skills": "skills",
    "delivery": ".",              # aim.json / bindings.yaml were at root
    "refresher": ".",             # REFRESHER.md was at root
}

_CANONICAL_DOC_SET = frozenset(DDD_CANONICAL_DOCS)


def _relpath(key: str, *, new: bool) -> str:
    """Relative path (from project root) for a logical key, new or old layout.

    A canonical doc key (e.g. "TECH.md") → under ② (new) or root (old).
    A section key (e.g. "gates") → the section dir (new) or its old dir.
    A path-like key ("knowledge/foo.md") → prefixed with the corpus dir.
    """
    # Canonical doc: lives directly under ② (new) or at root (old).
    if key in _CANONICAL_DOC_SET:
        return f"{UNDERSTANDING_DIR}/{key}" if new else key

    # AGENTS.md never moves — root in both layouts.
    if key == IDENTITY_FILE:
        return key

    # Section key (possibly with a trailing sub-path: "knowledge/foo.md").
    head, _, tail = key.partition("/")
    table = _SECTION_KEY_TO_DIR if new else _SECTION_KEY_TO_OLD_DIR
    if head in table:
        base = table[head]
        if base == ".":
            return tail or ""
        return f"{base}/{tail}" if tail else base

    # Unknown key: pass through unchanged (root-relative). Lets callers resolve
    # ad-hoc files (e.g. ".artifacts") without the resolver having to enumerate
    # every possible name.
    return key


def ddd_path(project_dir, key: str) -> Path:
    """Resolve a logical KEY to its physical path under ``project_dir`` (READ).

    Strangler semantics: returns the NEW (numbered-layout) path, UNLESS the new
    path does not exist AND the OLD path does — then returns the OLD path so an
    un-migrated DDD keeps resolving. This makes per-DDD rollout safe.

    Args:
        project_dir: the DDD project root (str or Path).
        key: a canonical doc name ("TECH.md"), a section key ("gates",
             "knowledge", "capabilities", ...), or a section sub-path
             ("knowledge/rocky.md"). "AGENTS.md" always resolves to root.

    Returns:
        Absolute-or-project-relative Path. If neither new nor old exists, the
        NEW path is returned (the canonical write-forward location).
    """
    root = Path(project_dir)
    new_rel = _relpath(key, new=True)
    new_path = root / new_rel if new_rel else root

    old_rel = _relpath(key, new=False)
    old_path = root / old_rel if old_rel else root

    if new_path == old_path:
        return new_path
    if not new_path.exists() and old_path.exists():
        return old_path
    return new_path


def ddd_new_path(project_dir, key: str) -> Path:
    """Resolve a logical KEY to its NEW physical path, touching nothing on disk.

    For callers that only compute a destination (routing), possibly from a relative
    ``project_dir``: a relative path resolves against the process cwd, which for the
    daemon is ``/`` and read-only, so creating anything there fails."""
    root = Path(project_dir)
    new_rel = _relpath(key, new=True)
    return root / new_rel if new_rel else root


def ddd_write_path(project_dir, key: str) -> Path:
    """Resolve a logical KEY to its NEW physical path (WRITE — always new).

    Never falls back to the old layout: a write must land deterministically at
    the new location, or reads and writes diverge (split-brain). Creates parent
    dirs so the caller can write immediately, so ``project_dir`` must be absolute;
    to only compute a path use ``ddd_new_path``.
    """
    path = ddd_new_path(project_dir, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def section_dir(project_dir, section_key: str) -> Path:
    """Resolve a section directory KEY to its path (READ, strangler-aware)."""
    return ddd_path(project_dir, section_key)


def ddd_asset_path(project_dir, kind: str, *, old_rel: str | None = None) -> Path:
    """Resolve a governed asset dir → assets/<kind>/ (READ, strangler-aware).

    A data-agent DDD's moat is the `data-source` asset (the unified SDK). Before
    migration it lived as a non-skill package under 4-capabilities/ (or the older
    skills/), e.g. ``4-capabilities/s_cmhk-data-proxy``. Pass that as ``old_rel``
    so reads fall back to the pre-move location until the package is relocated.

    Args:
        project_dir: the DDD project root.
        kind: the asset kind subdir under assets/ (e.g. "data-source").
        old_rel: project-relative path where the asset lived pre-migration. If
            None, no fallback (new-only).

    Returns:
        assets/<kind>/ if it exists (or no old_rel given), else the old path if it
        exists, else the new assets/<kind>/ path (write-forward default).
    """
    root = Path(project_dir)
    new_path = root / ASSETS_DIR / kind
    if old_rel is None:
        return new_path
    old_path = root / old_rel
    if not new_path.exists() and old_path.exists():
        return old_path
    return new_path




def stale_layout_refs(text: str) -> "list[str]":
    """Paths in *text* that name a DDD file or section dir at its pre-2026-07-21 place:
    a canonical doc right under ``Projects/<name>/`` instead of under ``2-understanding/``,
    or ``Projects/<name>/gates|skills|Knowledge/``; also a doc right under a placeholder
    (``$dir/``, ``<ddd>/``, ``{root}/``), which in shipped text stands for a project dir. Prose that describes the layout (a
    context file, a skill) drifts silently when the layout moves -- the code is held to
    ``ddd_path`` by test_ddd_paths_single_source, prose by this check."""
    import re as _re
    docs = "|".join(_re.escape(Path(d).stem) for d in DDD_CANONICAL_DOCS)
    pats = [
        _re.compile(r"Projects/[^/\s`'\")]+/(?:" + docs + r")\.md"),
        _re.compile(r"Projects/[^/\s`'\")]+/(?:gates|skills|Knowledge)/"),
        _re.compile(r"(?<!Projects/)(?:\$\{?\w+\}?|<[\w-]+>|\{[\w-]+\})/(?:" + docs + r")\.md"),
    ]
    return [m.group(0) for p in pats for m in p.finditer(text)]

# The data-source asset's legacy home, before governed assets moved under assets/.
_DATA_SOURCE_OLD_REL = "4-capabilities/s_cmhk-data-proxy"


def data_contract_dir(project_dir) -> Path:
    """The data-source asset's ``data-contract/`` dir (READ, strangler-aware): where a
    data-agent DDD keeps its data-semantic contract. One place for every reader (the
    recall leg, the knowledge unit table), so they cannot disagree about where it is."""
    return ddd_asset_path(project_dir, "data-source", old_rel=_DATA_SOURCE_OLD_REL) / "data-contract"

def ddd_asset_write_path(project_dir, kind: str) -> Path:
    """Resolve a governed asset dir → assets/<kind>/ (WRITE — always new).

    Creates the parent (assets/) so the caller can materialize the kind dir.
    """
    root = Path(project_dir)
    path = root / ASSETS_DIR / kind
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def confined_doc_path(project_dir: Path | str, target_doc: str) -> Path | None:
    """Resolve ``target_doc`` and return it ONLY if it is a regular file inside
    ``project_dir``; otherwise ``None`` (the caller turns that into a refusal).

    WHY THIS EXISTS — ``ddd_path`` deliberately passes an UNKNOWN key through
    unchanged (``ddd_paths.py`` "Unknown key: pass through unchanged") so callers
    can resolve ad-hoc names like ``.artifacts``. That pass-through also preserves
    traversal segments, so every write/read target derived from a caller-supplied
    ``target_doc`` was unbounded: ``"../.context/STEERING.md"`` resolved outside the
    project and the appliers wrote there. ``ddd_path`` itself must NOT be hardened —
    it serves READ paths and legitimate ad-hoc keys — so containment belongs at each
    sink, which is what this helper gives them.

    Confinement root is ``project_dir``, NOT ``2-understanding/``: EVOLUTION.md,
    KNOWLEDGE.md and MEMORY.md legitimately resolve to the project ROOT (they simply
    do not exist there, so those keys still fall through to the caller's
    "doc_missing" branch — a legitimate routing outcome, not an attack).

    This is PATH CONFINEMENT (where may we write), which is orthogonal to ADMISSION
    AUTHORITY (what is worth writing). It deliberately does NOT re-introduce the
    doc-identity allowlist that ``admission_band`` removed on purpose — any doc
    inside the project stays writable.

    Three failure modes are handled because each was measured, not theorised:
      * ``Path.resolve()`` raises ``RuntimeError`` on a symlink loop and
        ``ValueError`` on an embedded NUL — both outside the ``OSError`` an
        ``except OSError`` would catch, so all three are caught here.
      * BOTH sides are resolved: resolving only the target while ``project_dir``
        is itself a symlink makes every legitimate path look external.
      * ``is_file()`` is required, not just containment: a DIRECTORY-valued
        ``target_doc`` (``""``, ``"."``, or a key ``ddd_paths`` maps to ``"."``)
        is contained AND exists, so it passed a containment-only guard and then
        crashed the applier with ``IsADirectoryError``.

    Symlinks are resolved, which is the desired behaviour in both directions: an
    in-tree symlink pointing at an in-tree file stays allowed (a real DDD does
    this), while one pointing out of the tree is refused.

    A HARDLINK, however, has no link to follow — it is a second NAME for one inode, so
    resolution cannot see out of the tree and containment alone reads it as legitimate.
    That is not symmetric between the two sinks: the append applier commits through
    ``os.replace`` onto a fresh temp file, which breaks the link and leaves the other
    name untouched, but an in-place writer (the retire applier did, through
    ``ddd_entry_lifecycle`` ``write_text``) mutates the shared inode. Measured:
    an in-tree hardlink to an out-of-tree governance file let a retire proposal DELETE a
    rule from it, and the intended safety net — asserting no new sibling files appear
    next to the victim — could not see it, because the archive and lock files land
    in-tree beside the link. So a multiply-linked file is refused here, at the shared
    chokepoint, rather than by making one applier's commit strategy load-bearing:
    measured against the live corpus, all 69 real DDD documents have ``st_nlink == 1``.
    """
    try:
        root = Path(project_dir).resolve()
        candidate = ddd_path(root, target_doc).resolve()
    except (OSError, RuntimeError, ValueError):
        return None  # unresolvable → refuse (fail-closed)
    if candidate != root and root not in candidate.parents:
        # Escapes the project tree. This ALSO covers a symlink pointing out of the tree,
        # because both sides are resolved before the comparison — measured: an in-project
        # `TECH.md` symlinked to an out-of-tree file is refused here, so a separate
        # blanket `is_symlink()` refusal adds no security and only breaks the in-tree
        # symlink shape a live project actually uses. It does NOT cover a hardlink —
        # see the `st_nlink` check below.
        return None
    if not candidate.is_file():
        return None  # missing, or a directory (would raise IsADirectoryError)
    try:
        if candidate.stat().st_nlink != 1:
            return None  # multiply-linked → an in-place write reaches the other name
    except OSError:
        return None  # unstattable → refuse (fail-closed)
    if candidate.suffix.lower() != ".md":
        # These appliers write a MARKDOWN BULLET under a `## heading`. Appending that to
        # a structured in-tree file is not a smaller version of a DDD write — it is
        # corruption: measured, an append to a project's `aim.json` left it unparseable
        # (JSONDecodeError) and one to `bindings.yaml` broke the YAML. And an in-tree
        # `AGENTS.md`-style instruction file is the same class of prompt-injection target
        # as the governance file this containment exists to protect, so "inside the
        # project" is necessary but not sufficient. The format the writer produces IS
        # the constraint on what it may write to.
        return None
    return candidate
