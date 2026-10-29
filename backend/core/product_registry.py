"""Shared PRODUCT registry — the workspace-level source both the Artifacts overlay
and the Canvas rail will project from (Artifacts B′ Run 1; design_doc art_5c8787b5).

WHAT THIS OWNS
    A durable, WORKSPACE-LEVEL store of user-facing PRODUCTS (decks, reports,
    images, design docs) at ``<SwarmWS>/.artifacts/products.json``. Both
    the Artifacts overlay (today: git-log projection) and the Canvas rail (today:
    per-tab sessionStorage) will, in Run 2, read from this ONE store instead of
    their two lossy derived views.

WHY A FLAT, WORKSPACE-LEVEL STORE
    A product can land ANYWHERE (``Knowledge/``, any ``Projects/*``, or be
    gitignored), so the store must be FLAT + workspace-level, not keyed by project.
    It is built on ``utils.file_lock`` (flock) + an atomic write-temp+rename.

THE GATE-1 FIX (the load-bearing design point)
    ``register_batch`` classifies RAW paths INDEPENDENTLY of the surface verdict.
    A gitignored deck (``Projects/AIDLC/assets/deck.html``) is
    ``needs_human_review`` → ``review_worthy=False, kind=process`` (the git-ignore
    Layer-1 drop), so it is filtered OUT of ``workspace_surface_watcher``'s
    ``verdicts.items()`` emit loop before ``abs_path`` is even assigned. If the
    product write rode that loop, gitignored decks — exactly the products that must
    surface — would NEVER register. So the write hook feeds ``register_batch`` the
    RAW batch paths, and this module does its OWN classification via a small
    positive PRODUCT_DIR_ALLOWLIST that short-circuits the gitignore drop, WITHOUT
    un-gating secrets/scratch (the allowlist is narrow: product dirs only).

DELEGATION (never a parallel walk — run_847ed9f9 / run_4de279ca)
    Location (owned vs external) → ``artifact_source.classify_source`` /
    ``needs_human_review._owning_tree``. Content-kind → ``needs_human_review.
    _classify_kind``. This module adds only the PRODUCT-role mapping + the store.

CONCURRENCY & DURABILITY
    Every ``products.json`` mutation runs under an exclusive flock on a
    ``.products.lock`` sidecar (read-modify-write serialized across processes), and
    the write is atomic (temp file + ``os.replace``).

    MECHANISM: flock is advisory + bound to the fd/inode (NOT the path — never
    unlink the lock to "release" it). Atomic replace is same-directory rename.
    utils.file_lock carries the Windows byte-range branch.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

from core.needs_human_review import _classify_kind, _owning_tree
from utils.file_lock import flock_exclusive, flock_unlock

logger = logging.getLogger(__name__)


# Roles (the overlay's grouping axis — §8 of the redesign design doc). Run 2 AC3:
# a FROZEN enum, not a bare `Role = str` alias — the role set is closed, so an
# invalid role can never be stored or projected. str-subclass so existing JSON
# round-trips unchanged (asdict → the member's str value; a stored "Deliverables"
# rehydrates identically) and every `role == "Deliverables"` comparison still holds.
class Role(str, Enum):
    DELIVERABLES = "Deliverables"
    KNOWLEDGE = "Knowledge"
    PIPELINE = "Pipeline"
    ACTIVITY = "Activity"
    OTHER = "Other"


_STORE_VERSION = 1

# Backfill schema version (Gate-2 meta-review Finding 1). The `backfilled_at` marker
# alone froze existing stores: once set, has_backfilled() short-circuits forever, so a
# code change to the backfill LOGIC (e.g. Run-3's real timestamps + fs-scan + displayLabel)
# would NEVER reach the rows a prior backfill wrote with the OLD logic. Bumping this
# constant invalidates a store whose stored backfill_version is older → has_backfilled()
# returns False once → the endpoint re-runs backfill with the new logic, correcting the
# stale data. Bump this whenever backfill/scan/classification OUTPUT changes. The
# re-backfill ADDS/refreshes via register_batch AND runs _purge_excluded, which drops
# stored rows a NEW exclusion rule now rejects — so a version bump both re-adds with the
# new logic AND removes now-excluded noise (register_batch alone never removes).
_BACKFILL_VERSION = 4  # run_cb127db1: bumped so the re-backfill re-runs once and RESTORES the Pipeline REPORT rows a prior _purge_excluded wrongly deleted (fix routes purge through derive_role, single door)

# Run 2 AC4: bounded store. Registering past this cap evicts the OLDEST entries by
# last_touched inside the same flock-guarded _upsert (never a parallel writer, P8).
# Generous but finite — the overlay is a recent-products view, not an archive.
MAX_PRODUCTS = 2000

# ── PRODUCT-dir allowlist (AC3) ──────────────────────────────────────────────
# A NARROW positive list: dirs whose gitignored contents are still user PRODUCTS
# and must surface despite the git-ignore drop. Matched on the SwarmWS-relative
# path. Deliberately small — widening this to un-gate secrets/scratch under
# Projects/* is the exact risk the PLAN security boundary escalated. A path is a
# product-dir member iff it lives UNDER one of these (never a bare prefix match on
# a sibling like "assets-backup").
_PRODUCT_DIR_SEGMENTS: tuple[str, ...] = (
    "assets",        # Projects/*/assets/** — decks, generated media
    # NOTE (run_fe228bc0): "Attachments" was REMOVED — Attachments/** is INPUT
    # (chat attachments the user sent us), NOT a product OUTPUT the user reopens.
    # The whole tree is excluded via _is_excluded_path (XG decision).
    "Library",       # Knowledge/Library/** — published decks/reports (Run-3 AC3)
    "Pollinate",     # Knowledge/Pollinate/** — Pollinate content packages (Run-3 AC3)
    "deliverables",  # Projects/*/deliverables/** — project deliverables (Run-3 AC3)
)

# Dependency / vendor trees whose nested ``assets/`` dirs are NOT user products
# (a package ships its own ``assets/`` media/creds). If any of these segments
# appears in the path, the product-dir allowlist does NOT apply — 3rd-party
# content, not a SwarmAI product. (Gate-2 Claim 3 + Red-Team.)
_DEPENDENCY_SEGMENTS: frozenset[str] = frozenset({
    "node_modules", "vendor", ".venv", "venv", "site-packages", "dist", "build",
    "__pycache__", "target", ".next", ".nuxt", "bower_components", "Pods",
    ".gradle", ".tox", ".mypy_cache", "out", "coverage",
})

# POSITIVE product-extension allowlist — the Red-Team fix (a deny-based secret
# filter guarding an allowlist is inherently leaky: aws_credentials / token.txt /
# passwords.md / config.yaml all escaped a denylist). Under assets/Attachments,
# ONLY these known media/document product extensions surface as a Deliverable;
# everything else (no-extension, .txt, .yaml, .env, .key, a markdown secret dump)
# falls through to normal kind classification (→ usually Other → dropped). This
# closes the WHOLE secret-leak class positively instead of chasing filenames.
_PRODUCT_EXTENSIONS: frozenset[str] = frozenset({
    ".html", ".htm", ".pdf", ".pptx", ".ppt", ".key",  # NOTE: .key excluded below
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".avif",
    ".mp4", ".mov", ".webm", ".gif",
    ".docx", ".xlsx", ".csv",
})
# .key is an Apple-Keynote extension BUT also an SSH/TLS private-key extension —
# the collision is a secret risk, so it is EXCLUDED from products (a Keynote deck
# is exported to .pptx/.pdf for surfacing). Remove it from the set explicitly.
_PRODUCT_EXTENSIONS = _PRODUCT_EXTENSIONS - {".key"}

# Run-3 AC4: the FS-SCAN extension gate is STRICTER than _PRODUCT_EXTENSIONS.
# scan_product_dirs() walks gitignored dirs (Projects/*/assets), bypassing the
# git-log tracked-only guarantee — so a gitignored data-export could carry a
# product extension. .csv/.xlsx are dropped here (Gate-1 F3: a gitignored
# `secrets_export.csv` under assets/ must never surface).
#
# ⚠️ SECURITY (Gate-2 HIGH, defense-in-depth): the extension gate ALONE is NOT a
# sufficient secret guard — a document-formatted secret (an exported
# `aws_credentials.pdf`, a `session_tokens.html`) carries a legit product extension.
# AC4's PURPOSE is to surface a gitignored DECK (Projects/AIDLC/assets/*.pdf), so we
# cannot drop .pdf/.html. GUARD-1 (role==Other) does NOT catch these — a product-
# extension file under assets/ classifies as Deliverables, bypassing GUARD-1/GUARD-2.
# So the fs-scan path adds a SECOND layer: _is_secretish_name() (a filename denylist)
# runs before registration. It is a denylist (Red-Team notes denylists leak), but here
# it is defense-IN-DEPTH layered ON the positive extension allowlist — not the sole
# guard — and the blast radius is bounded (products.json stores PATHS not content, and
# SwarmWS has zero git remote per C050/STEERING#5, so a surfaced path is local-only).
_SCAN_EXTENSIONS: frozenset[str] = _PRODUCT_EXTENSIONS - {".csv", ".xlsx"}

# Secret-name markers for the FS-SCAN path only (defense-in-depth on the gitignored
# walk). A basename matching any of these is not surfaced even with a product
# extension. Substring match on the lowercased basename.
_SECRET_NAME_MARKERS: tuple[str, ...] = (
    "secret", "credential", "cred_", "_creds", "password", "passwd", "token",
    "apikey", "api_key", "api-key", "private", "privkey", "id_rsa", "id_ed25519",
    ".pem", ".p12", ".pfx", "keystore", "aws_", "_aws", ".env",
)


def _is_secretish_name(basename: str) -> bool:
    """True iff a basename looks secret-bearing (FS-scan defense-in-depth, Gate-2 HIGH).

    A denylist — deliberately, and deliberately LAYERED on the positive extension
    allowlist (never the sole guard). Bounds the fs-scan's exposure of gitignored
    document-secrets that carry a legit product extension (a `.pdf`/`.html` that
    GUARD-1 cannot catch because it classifies as Deliverables under a product dir).
    """
    low = basename.lower()
    return any(m in low for m in _SECRET_NAME_MARKERS)

# Run-3 AC2: backup/temp/corrupt markers. A file carrying one of these in ANY path
# component OR its basename is never a product (it is an editor/tool byproduct).
# COMPONENT-aware, not endswith — `deck.bak.html` (a double-extension where .bak is
# mid-name) is garbage even though its final suffix is .html (Gate-1 F2).
_GARBAGE_MARKERS: tuple[str, ...] = (
    ".bak", ".broken", ".tmp", ".temp", ".corrupt", ".orig", ".swp", ".old",
)

# run_fe228bc0: image extensions that, when nested under an assets/ dir, are a deck's
# INTERMEDIATE parts (slide-2x / raw / preview), not the deck itself. The deck MAIN
# product (.html/.pdf/.pptx/.md) under assets/ is kept; only these loose images drop.
_ASSET_IMAGE_EXTENSIONS: frozenset[str] = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".avif",
})


def _is_excluded_path(rel_path: str) -> bool:
    """True iff rel_path is NOISE that must never enter the registry (run_fe228bc0).

    Three exclusion classes, checked at the shared classification seam so every door
    (live watcher, backfill, fs-scan) agrees (P8):
      (1) ANY dot-folder segment (.claude/, .git/, any `.xxx/`) — infra/internal
          resources, never a user product. Checked on every segment EXCEPT the
          basename (a dotfile that IS the product, e.g. `.env`, is handled by the
          Other/secret guards, not here; here we drop dot-DIRECTORIES).
      (2) an image file nested under an `assets/` dir — a deck's intermediate part,
          not the deliverable. The deck MAIN product (.html/.pdf/.pptx/.md) is kept.
      (3) the entire `Attachments/` tree — INPUT (chat attachments), not a product
          OUTPUT (XG decision).
    Purely SUBTRACTIVE: only ever REMOVES paths, so it cannot surface a new file and
    cannot weaken the existing secret defenses.
    """
    norm = rel_path.replace("\\", "/")
    parts = [p for p in norm.split("/") if p not in ("", ".")]
    if not parts:
        return False
    # (3) Attachments tree — first segment. Case-insensitive (macOS/APFS is
    # case-insensitive, so `attachments/` is the same dir as `Attachments/`; match
    # the intent, not the exact casing — consistent with the assets check below).
    if parts[0].lower() == "attachments":
        return True
    # (1) dot-folder DIRECTORY segment (exclude the basename at index -1).
    for seg in parts[:-1]:
        if seg.startswith("."):
            return True
    # (2) image nested under an assets/ dir.
    lower_parts = {p.lower() for p in parts[:-1]}
    if "assets" in lower_parts:
        ext = norm[norm.rfind("."):].lower() if "." in parts[-1] else ""
        if ext in _ASSET_IMAGE_EXTENSIONS:
            return True
    return False


def _is_garbage(rel_path: str) -> bool:
    """True iff rel_path is a backup/temp/corrupt byproduct — never a product.

    COMPONENT-aware (Gate-1 F2): checks every path component AND the dot-split
    tokens of the basename, so `deck.bak.html` (mid-name .bak), `x.html.bak-1788`
    (trailing epoch-bak), `notes.md.tmp`, `data.corrupt-20260930`, and `draft.html~`
    (trailing tilde) are ALL caught — not just a naive final-suffix match.
    """
    norm = rel_path.replace("\\", "/")
    # Trailing-tilde editor backup (draft.html~).
    if norm.endswith("~"):
        return True
    for comp in norm.split("/"):
        if not comp:
            continue
        # Each dot-delimited token of the component (basename included): a marker
        # appearing as ANY token — start, middle, or with a trailing -<suffix> —
        # means garbage. Lowercase for case-insensitive match.
        lowered = comp.lower()
        for marker in _GARBAGE_MARKERS:
            m = marker  # e.g. ".bak"
            # ".bak" as a standalone dotted token, OR ".bak-<anything>" (epoch/host).
            if (m + ".") in lowered or (m + "-") in lowered or lowered.endswith(m):
                return True
    return False


def _under_product_dir(rel_path: str) -> bool:
    """True iff rel_path is a real PRODUCT under an allowlisted dir.

    Three gates, ALL must hold: (1) a whitelisted product SEGMENT (``assets`` /
    ``Library`` / ``Pollinate`` / ``deliverables`` — NOT ``Attachments``, removed
    run_fe228bc0 as it is INPUT not a product) appears in the path; (2) NO dependency/vendor segment appears
    (``node_modules/.../assets/...`` is 3rd-party — Gate-2 Claim 3); (3) the basename
    has a known PRODUCT EXTENSION (media/document — the Red-Team positive-allowlist
    fix). A secret/config/no-extension file under assets/ fails gate 3 → NOT a
    product-dir file → falls to normal kind classification → dropped as Other. This
    is case-insensitive on the extension. The allowlist surfaces media/decks, never
    secrets or vendored files, by CONSTRUCTION (positive list), not by chasing
    secret filenames (a denylist, which Red-Team proved leaky).
    """
    parts = [p for p in Path(rel_path).parts if p not in (".", "")]
    # Case-insensitive segment match (macOS/APFS is case-insensitive; Assets==assets).
    lower_parts = {p.lower() for p in parts}
    if {d.lower() for d in _DEPENDENCY_SEGMENTS}.intersection(lower_parts):
        return False
    if not {s.lower() for s in _PRODUCT_DIR_SEGMENTS}.intersection(lower_parts):
        return False
    ext = Path(rel_path).suffix.lower()
    return ext in _PRODUCT_EXTENSIONS


# ── role derivation (AC2) — kind (delegated) + path sub-map ──────────────────


def derive_role(rel_path: str, repo: Optional[str]) -> Role:
    """Map a SwarmWS-relative path to its PRODUCT role.

    Two axes (design §8): CONTENT-ROLE (kind, DELEGATED to needs_human_review) +
    a PATH sub-map. Extension is only a tiebreaker WITHIN a path. Every path lands
    in exactly one role; ``Other`` is the exhaustiveness catch-all (never dropped).

    ``repo`` is the owning bound-repo name (or None for SwarmWS content) — passed
    straight to ``_classify_kind`` so a DDD ``.md`` inside a bound worktree is
    classified as knowledge, not source (matches _classify_kind's own precedence).
    """
    parts = [p for p in Path(rel_path).parts if p not in (".", "")]

    # Pipeline: a run REPORT under .artifacts/runs/ — check BEFORE the exclusion +
    # kind, because the dot-segment (.artifacts) would otherwise be dropped by the
    # dot-folder exclusion AND make _classify_kind return "process".
    if ".artifacts" in parts and "runs" in parts and parts[-1] == "REPORT.md":
        return Role.PIPELINE

    # run_fe228bc0: noise exclusion (dot-folders / assets-images / Attachments) →
    # Other, so _classify drops it. Runs AFTER the Pipeline REPORT short-circuit
    # (a REPORT under .artifacts/runs is a legit product despite the dot-folder).
    if _is_excluded_path(rel_path):
        return Role.OTHER

    # Activity: automated per-day logs.
    if "DailyActivity" in parts or "Signals" in parts or "DailyBriefs" in parts:
        return Role.ACTIVITY

    # Deliverables: an explicit product dir (decks/attachments), OR a design/report
    # authoring dir. These are the things a user makes and reopens.
    if _under_product_dir(rel_path):
        return Role.DELIVERABLES
    if "Designs" in parts or "Reports" in parts:
        return Role.DELIVERABLES

    # Otherwise fall back to the delegated content-kind.
    kind = _classify_kind(rel_path, repo)
    if kind == "knowledge":
        return Role.KNOWLEDGE
    if kind in ("content",):
        # Bare content with no product/activity path signal → treat as Knowledge-
        # adjacent only if it is a knowledge dir; else Other.
        if "Knowledge" in parts:
            return Role.KNOWLEDGE
        return Role.OTHER

    # source / source-final / process with no product-dir short-circuit → not a
    # user product surface → Other (the catch-all; never dropped).
    return Role.OTHER


# ── the store ────────────────────────────────────────────────────────────────


@dataclass
class Product:
    """One registered product (a row projected by the overlay + Canvas rail)."""

    path: str            # SwarmWS-relative, forward-slashed
    role: Role
    kind: str            # the delegated needs_human_review kind
    gitignored: bool
    first_produced: str  # ISO 8601
    last_touched: str    # ISO 8601
    # run_2b7230be: user favorite. The `= False` DEFAULT is load-bearing — list_products
    # spreads every stored row via Product(**e), and a LEGACY row (written before this
    # field existed) carries no `starred` key, so without a default that spread raises
    # TypeError on every pre-existing product. Must remain the LAST field (dataclass
    # forbids a defaulted field before a required one).
    starred: bool = False


def display_label(product, workspace_path: str, cache: dict) -> str:
    """Run-3 AC8: a human label for a product row.

    Pipeline REPORT.md rows: the task NAME from the run's run.json ``requirement``
    (truncated), not the raw run-id ("who can read run_7220ff2a?"). Every other row:
    the basename. Path-traversal-safe: the run-id is used ONLY to build a path UNDER
    ``<ws>/Projects/*/.artifacts/runs/<run-id>/run.json`` and the resolved path is
    confirmed to stay inside the workspace (Gate-1 escalated boundary). Per-request
    cache keyed by run-id avoids re-reading a run.json for sibling rows.
    """
    path = product.path
    base = path.rsplit("/", 1)[-1]
    # Robust role check (Gate-2 F2/F5): a Role is a str-Enum, and str(Role.PIPELINE)
    # is 'Role.PIPELINE' (not 'Pipeline') on Py3.11+ — so compare the .value if it's an
    # enum member, else the plain string. Works whether the Product came from JSON
    # rehydration (plain str) or a fresh classification (enum member).
    role_str = getattr(product.role, "value", product.role)
    if role_str != "Pipeline":
        return base

    parts = [seg for seg in path.replace("\\", "/").split("/") if seg]
    try:
        runs_idx = parts.index("runs")
    except ValueError:
        return base
    if runs_idx + 1 >= len(parts):
        return base
    run_id = parts[runs_idx + 1]
    if run_id in cache:
        return cache[run_id]

    label = run_id  # fallback = the run-id if run.json is unreadable
    try:
        ws_root = Path(workspace_path).resolve()
        # Rebuild the run dir from the product's OWN path prefix (up to + incl run_id),
        # never from caller input — then confine to the workspace.
        run_dir = (ws_root / Path(*parts[: runs_idx + 2])).resolve()
        if run_dir.is_relative_to(ws_root):
            run_json = run_dir / "run.json"
            if run_json.is_file():
                import json as _json
                req = _json.loads(run_json.read_text()).get("requirement", "")
                if req:
                    label = req[:60].strip()
    except (OSError, ValueError):  # JSONDecodeError subclasses ValueError
        pass
    cache[run_id] = label
    return label


class ProductRegistry:
    """Workspace-level product store. Reads are lock-free; every mutation is
    flock-serialized + atomically written."""

    def __init__(self, workspace_root: str | Path):
        self.workspace_root = Path(workspace_root).expanduser().resolve()

    # ── paths ──────────────────────────────────────────────────────────────

    def _store_path(self) -> Path:
        return self.workspace_root / ".artifacts" / "products.json"

    def _lock_path(self) -> Path:
        return self.workspace_root / ".artifacts" / ".products.lock"

    # ── read ─────────────────────────────────────────────────────────────────

    def _read(self) -> dict:
        path = self._store_path()
        if not path.is_file():
            return {"version": _STORE_VERSION, "products": []}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("ProductRegistry: unreadable store %s: %s", path, exc)
            return {"version": _STORE_VERSION, "products": []}

    def list_products(self) -> list[Product]:
        return [Product(**e) for e in self._read().get("products", [])]

    def set_starred(self, path: str, starred: bool) -> bool:
        """run_2b7230be: toggle the `starred` favorite flag on an EXISTING product row.

        SUBTRACTIVE-SAFE + no-create: matches a stored row by EXACT path equality (the
        stored path is already the sanitized SwarmWS-relative key — no filesystem
        resolution/join here, so a traversal or unknown path simply matches nothing and
        is a no-op, never appends a row). Sets ONLY the boolean; every other field is
        untouched. flock-guarded via _mutate (P8: the same single-writer primitive the
        watcher/backfill/purge use). Returns True iff a row matched (caller → 200/404).
        """
        matched = {"hit": False}

        def _toggle(data: dict) -> None:
            for e in data.get("products", []):
                if e.get("path") == path:
                    e["starred"] = bool(starred)
                    matched["hit"] = True
                    break

        self._mutate(_toggle)
        return matched["hit"]

    def has_backfilled(self) -> bool:
        """True once backfill_from_gitlog has run WITH THE CURRENT backfill version.

        The endpoint gates day-one backfill on THIS, not on an empty product list —
        otherwise a workspace whose recent git-log has no products (a code-only or
        brand-new tree) would re-run the 30s git-log on EVERY overlay open, occupying
        a shared thread-pool worker each time (Gate-2 meta-review MED / RP53-adjacent).

        VERSION GATE (Gate-2 meta-review Finding 1): returns False if the store was
        backfilled by an OLDER _BACKFILL_VERSION, so a change to the backfill LOGIC
        re-runs ONCE to correct rows the previous logic wrote (e.g. the 175 rows a v1
        backfill stamped with the fetch-instant instead of real git-commit dates).
        A store with no marker (never backfilled) also returns False. Fires at most
        once per version bump."""
        data = self._read()
        if not data.get("backfilled_at"):
            return False
        return data.get("backfill_version", 1) >= _BACKFILL_VERSION

    # ── atomic write (temp + replace) ─────────────────────────────────────────

    def _atomic_write_json(self, data: dict) -> None:
        path = self._store_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, indent=2, default=str)
        fd, tmp = tempfile.mkstemp(
            dir=str(path.parent), suffix=".tmp", prefix=".products-"
        )
        try:
            with open(fd, "w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp, path)  # atomic same-dir rename
        except BaseException:
            try:
                Path(tmp).unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def _mutate(self, mutator) -> None:
        """flock-guarded read-modify-write.

        The default store is constructed INSIDE the lock so two processes seeing
        "no store" cannot each seed a fresh empty one and lose the other's write.
        """
        lock_path = self._lock_path()
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = open(lock_path, "w")
        try:
            flock_exclusive(lock_fd)
            data = self._read()
            mutator(data)
            self._atomic_write_json(data)
        finally:
            flock_unlock(lock_fd)
            lock_fd.close()

    # ── classification (delegated) ────────────────────────────────────────────

    def _resolve(self, abs_path: str):
        """Resolve a raw path to (tree_root, rel_path, repo) or None if external.

        Split out of _classify so register_batch can group rel_paths by owning tree
        and run ONE check-ignore per tree (AC5) before classifying.
        """
        try:
            abs_resolved = Path(os.path.expanduser(abs_path)).resolve()
        except (OSError, RuntimeError):
            return None
        owning = _owning_tree(abs_resolved, self.workspace_root)
        if owning is None:
            return None  # external / $HOME / tmp — not our product (AC4)
        tree_root, rel_path, repo = owning
        if repo is not None:
            # A bound repo's or run's worktree: code-tree files are a turn's changes,
            # never a SwarmWS product, whatever their name (design
            # 2026-10-26-artifacts-one-rule.md §2.2).
            return None
        return tree_root, rel_path.replace("\\", "/"), repo

    def is_ignored_product(self, abs_path: str) -> bool:
        """Would *abs_path* be a product even though git ignores it? The product rule
        with ``ignored=True``: only a SwarmWS file under a product dir (assets/,
        Library/, ...) passes. Pure (no git call). The emit gate asks it for a SwarmWS
        path ``needs_human_review`` declined, so a gitignored deck a session made is
        a fact of its turn (design 2026-10-26-artifacts-one-rule.md §2.3). Never raises."""
        try:
            r = self._resolve(abs_path)
            return r is not None and self._classify(r[0], r[1], r[2], True) is not None
        except Exception:  # noqa: BLE001 — a display aid: unknown = not a product
            logger.debug("is_ignored_product failed for %s", abs_path, exc_info=True)
            return False

    def _classify(
        self,
        tree_root: Path,
        rel_path: str,
        repo: Optional[str],
        ignored: Optional[bool],
        ts: Optional[str] = None,
    ) -> Optional[Product]:
        """Classify an already-resolved path into a Product, or None if not a product.

        INDEPENDENT of the surface verdict (the Gate-1 fix): a gitignored deck is
        review_worthy=False/process at needs_human_review, but its PRODUCT status is
        decided HERE. ``ignored`` is the PRE-COMPUTED tri-state git verdict (True /
        False / None-unknown) from the batched check-ignore — the per-path subprocess
        is gone (AC5). Returns None for non-product content (AC3 secret guard).

        ``ts`` (Run-3 AC1) is the REAL file time (git commit date / mtime) to stamp
        both first_produced and last_touched. When None (the LIVE watcher path), we
        stamp now() — a file the watcher just saw WAS just touched. Backfill/fs-scan
        pass a real historical ts so a 2026-06 deck shows 2026-06, not the backfill
        instant. MUST set BOTH date fields from ts (Gate-1 F1: a post-hoc overwrite
        in register_batch would miss first_produced).
        """
        # AC2 (Gate-1 F2): garbage byproducts are never products — MUST run BEFORE
        # derive_role, because a Library/x-deck.bak.html passes the ext gate and
        # derive_role would (mis)classify it as a Deliverable. First line, no bypass.
        if _is_garbage(rel_path):
            return None

        role = derive_role(rel_path, repo)

        # GUARD 1 — the store holds PRODUCTS only. `Other` is the classification
        # model's exhaustiveness catch-all (§8) — a real file that is NOT a user
        # product (a tracked config, a random dotfile, a secret that slipped the
        # gitignore check). Never surface it. This is the PRIMARY secret guard: a
        # Projects/*/.env is kind=process → role Other → dropped here regardless of
        # what git check-ignore reported (RP50 fail-open defense that does not depend
        # on the gitignore subprocess succeeding).
        if role == Role.OTHER:
            return None

        # GUARD 2 — a CONFIRMED-gitignored product surfaces ONLY under an explicit
        # PRODUCT dir (AC3: a deck under Projects/*/assets YES; a gitignored draft
        # elsewhere NO — git-ignore says "not committed", only the assets/Attachments
        # allowlist overrides that). RP55 tri-state: only a DEFINITE True gates here;
        # UNKNOWN (None, git errored / non-repo) does NOT drop a legit product-role
        # file — GUARD 1 already dropped the secret/Other class independent of git, so
        # a couldn't-check on a genuine product (Designs/Reports in a non-git tree or a
        # transient error) must not lose the deliverable.
        if ignored is True and not _under_product_dir(rel_path):
            return None
        gitignored = ignored is True

        kind = _classify_kind(rel_path, repo)
        # AC1: real file time when provided (backfill/fs-scan), else now() (live).
        stamp = ts or datetime.now(timezone.utc).isoformat()
        return Product(
            path=rel_path,
            role=role,
            kind=kind,
            gitignored=gitignored,
            first_produced=stamp,
            last_touched=stamp,
        )

    def _batch_gitignored(
        self, tree_root: Path, rel_paths: list[str]
    ) -> dict[str, Optional[bool]]:
        """ONE ``git check-ignore --stdin`` for ALL rel_paths in a tree (AC5).

        Replaces the per-path subprocess. Returns a {rel_path: True|False|None} map.
        --stdin --verbose prints one line PER IGNORED path (``<source>:<line>:<pat>\\t<path>``);
        a non-ignored path prints nothing. Exit 0 = at least one match, 1 = none, other
        = git error → EVERY path is UNKNOWN (None), so the caller fails CLOSED (RP55) —
        a couldn't-check never silently becomes not-ignored.
        """
        if not rel_paths:
            return {}
        from utils.stdin_file import run_with_stdin_file   # never input= (its module doc)
        try:
            r = run_with_stdin_file(
                # -c core.quotepath=false: emit paths as raw UTF-8, NOT git's default
                # C-style octal-escaped + double-quote-wrapped form for non-ASCII names.
                # WITHOUT it, a CJK-named product (e.g. `assets/中概互联ETF.md`) comes back
                # as `"assets/\344\270\255..."` in --verbose output — that token never
                # matches the raw rel_path key, so `rp in ignored_set` is False → the file
                # is recorded gitignored=False (wrong) and a gitignored non-product-dir CJK
                # path could slip GUARD-2. The sibling /artifacts/recent uses the same flag
                # for exactly this reason (artifacts.py). (Gate-2 Correctness HIGH.)
                # Index-AWARE by default (do NOT pass the ignore-the-index flag): a
                # TRACKED file matching a broad ignore pattern (e.g. a committed
                # Designs/*.html under an `*.html` rule) correctly reports NOT-ignored,
                # because tracking overrides gitignore. Ignoring the index would falsely
                # mark that committed product gitignored=True → GUARD-2 would DROP a real
                # committed product. The gitignored DECK we DO want (AC3) is UNtracked, so
                # the default still reports it ignored. (Gate-2 Red-Team, verified.)
                ["git", "-c", "core.quotepath=false", "check-ignore",
                 "--stdin", "--verbose"],
                "\n".join(rel_paths) + "\n",
                cwd=str(tree_root),
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return {rp: None for rp in rel_paths}  # git unavailable → all UNKNOWN
        # returncode 0 (some ignored) / 1 (none ignored) are both valid, parseable
        # results. Anything else (128 not-a-repo, etc.) → all UNKNOWN, fail closed.
        if r.returncode not in (0, 1):
            return {rp: None for rp in rel_paths}
        ignored_set: set[str] = set()
        for line in r.stdout.splitlines():
            # --verbose format: "<source>:<line>:<pattern>\t<path>". The path is
            # after the LAST tab; git delimits with the tab, so do NOT .strip() the
            # tab-present branch (a filename with a legit trailing space would be
            # corrupted and miss the key match). Only the no-tab fallback strips
            # the trailing newline splitlines already removed. (Gate-2 F2.)
            if "\t" in line:
                path = line.rsplit("\t", 1)[-1]
            else:
                path = line.strip()
            if path:
                ignored_set.add(path)
        return {rp: (rp in ignored_set) for rp in rel_paths}

    # ── write (public, sync — callable from an off-loop seam, AC6) ─────────────

    def lookup(self, abs_paths: list[str]) -> dict[str, dict]:
        """What each path IS, by the same classification the store is written with
        (one door), WITHOUT writing: ``{abs_path: {role, kind, starred, product,
        displayLabel}}``. A stored row answers for its path (its role / favorite /
        first time); an unstored one is classified now (``product`` False when the
        store would not keep it: not a product, or outside SwarmWS → role Other).
        Blocking (one check-ignore per tree for the unstored ones)."""
        stored = {e.get("path"): e for e in self._read().get("products", [])}
        out: dict[str, dict] = {}
        pending: dict[Path, list[tuple[str, str, Optional[str]]]] = {}
        cache: dict[str, str] = {}
        for ap in abs_paths:
            r = self._resolve(ap)
            if r is None:
                out[ap] = {"role": Role.OTHER.value, "kind": None, "starred": False, "product": False,
                           "displayLabel": os.path.basename(ap)}
                continue
            tree_root, rel, repo = r
            row = stored.get(rel)
            if row is not None:
                prod = Product(**row)
                out[ap] = {"role": getattr(prod.role, "value", prod.role), "kind": prod.kind,
                           "starred": bool(prod.starred), "product": True,
                           "displayLabel": display_label(prod, str(self.workspace_root), cache)}
            else:
                pending.setdefault(tree_root, []).append((ap, rel, repo))
        for tree_root, items in pending.items():
            ignored = self._batch_gitignored(tree_root, [rel for _ap, rel, _repo in items])
            for ap, rel, repo in items:
                prod = self._classify(tree_root, rel, repo, ignored.get(rel))
                role = prod.role if prod else derive_role(rel, repo)
                out[ap] = {"role": getattr(role, "value", role), "kind": prod.kind if prod else None,
                           "starred": False, "product": prod is not None,
                           "displayLabel": display_label(prod, str(self.workspace_root), cache) if prod
                           else os.path.basename(rel)}
        return out

    def register_product(self, abs_path: str) -> None:
        """Register ONE product path (no-op if not a product)."""
        self.register_batch([abs_path])

    def register_batch(
        self, paths: list[str], times: Optional[dict[str, str]] = None
    ) -> None:
        """Register a batch of RAW paths. Classifies each independently of any
        surface verdict (the Gate-1 fix), then upserts under one flock.

        AC5: resolve all paths first, GROUP by owning tree, run ONE check-ignore
        --stdin per tree (not one subprocess per path), then classify. Keyed by
        relative path: re-registering refreshes ``last_touched`` (+role/kind/
        gitignored) but never duplicates. AC4: eviction inside the same flock.

        ``times`` (Run-3 AC1): optional {abs_path: iso_time} of REAL file times
        (git commit date / mtime). When present, the matching product is stamped
        with that time instead of now(). Absent → now() (the live-watcher path).
        """
        times = times or {}
        # 1. Resolve every raw path to (tree_root, rel_path, repo); drop externals.
        #    Keep the raw abs_path so we can look up its real time in `times`.
        resolved = []  # (tree_root, rel_path, repo, ts)
        for a in paths:
            r = self._resolve(a)
            if r is not None:
                resolved.append((*r, times.get(a)))
        if not resolved:
            return

        # 2. Group rel_paths per owning tree → ONE batched check-ignore per tree (AC5).
        by_tree: dict[Path, list[str]] = {}
        for tree_root, rel_path, _repo, _ts in resolved:
            by_tree.setdefault(tree_root, []).append(rel_path)
        ignore_maps: dict[Path, dict[str, Optional[bool]]] = {
            tree_root: self._batch_gitignored(tree_root, rels)
            for tree_root, rels in by_tree.items()
        }

        # 3. Classify each with its pre-computed gitignore verdict + real time.
        classified = []
        for tree_root, rel_path, repo, ts in resolved:
            ignored = ignore_maps.get(tree_root, {}).get(rel_path)
            prod = self._classify(tree_root, rel_path, repo, ignored, ts)
            if prod is not None:
                classified.append(prod)
        if not classified:
            return

        # Rel-paths that got a REAL historical time (backfill/fs-scan), so _upsert can
        # CORRECT a stale first_produced (Gate-2 meta-review Finding 1: a re-backfill
        # must overwrite the v1 fetch-instant stamp with the real git-commit date, not
        # preserve the wrong value). A live re-touch (no ts) still preserves first_produced.
        real_ts_rels = {rel for _t, rel, _r, ts in resolved if ts}

        def _upsert(data: dict) -> None:
            products = data.setdefault("products", [])
            by_path = {e["path"]: e for e in products}
            for prod in classified:
                existing = by_path.get(prod.path)
                if existing is not None:
                    # Refresh mutable fields. NOTE: starred is deliberately NOT in this
                    # refresh set — a re-touch (register_batch) mutates `existing` in
                    # place, so the user's stored `starred` survives untouched
                    # (run_2b7230be: a favorite is durable across re-registration).
                    existing["role"] = prod.role.value
                    existing["kind"] = prod.kind
                    existing["gitignored"] = prod.gitignored
                    existing["last_touched"] = prod.last_touched
                    # Correct first_produced ONLY when a real historical time was supplied
                    # (backfill/scan); a live re-touch preserves the earliest-seen value.
                    if prod.path in real_ts_rels:
                        existing["first_produced"] = prod.first_produced
                else:
                    row = asdict(prod)
                    row["role"] = prod.role.value  # store the enum VALUE, not the member
                    products.append(row)
                    by_path[prod.path] = row
            # AC4: bounded store — evict OLDEST by last_touched past the cap. Runs
            # inside the SAME flock (P8: no parallel writer). A just-refreshed live
            # product has the newest last_touched, so eviction removes only genuinely
            # stale rows; a re-touched or backfilled product re-enters on next write.
            # run_2b7230be (Gate-1 finding): a STARRED row is NEVER evicted — a favorite
            # is permanent, so a starred-but-untouched row must survive the cap even as
            # the oldest. Sort key puts starred rows LAST (unevictable); among the rest,
            # oldest-by-last_touched is evicted first. If starred rows alone exceed the
            # cap the store may exceed MAX_PRODUCTS — deliberate: never drop a favorite.
            if len(products) > MAX_PRODUCTS:
                products.sort(key=lambda e: (bool(e.get("starred", False)), e.get("last_touched", "")))
                # evict only from the unstarred, oldest-first prefix
                n_evict = len(products) - MAX_PRODUCTS
                evictable = [i for i, e in enumerate(products) if not e.get("starred", False)]
                for i in evictable[:n_evict]:
                    products[i] = None  # tombstone
                products[:] = [e for e in products if e is not None]

        self._mutate(_upsert)

    # ── backfill (AC5) ─────────────────────────────────────────────────────────

    def backfill_from_gitlog(self, days: int = 30) -> None:
        """Seed the store from the last ``days`` of git-log products (idempotent).

        Enumerates files touched in recent commits (the same source the Artifacts
        overlay uses today), classifies each, and upserts. Keyed by path → running
        twice adds no duplicate (AC5). Only tracked files appear in git-log, so a
        gitignored deck is NOT backfilled here — it registers live via the watcher
        hook (AC3). Fail-safe: any git error → no-op (never raises).

        On a SUCCESSFUL git-log run (even if it produced zero products) sets a
        ``backfilled_at`` marker so the endpoint never re-runs the 30s git-log on a
        product-less workspace (Gate-2 meta-review MED). A git ERROR does NOT set the
        marker — that stays retryable.
        """
        try:
            # AC1: --pretty=format:%x00%cI emits a header line "<NUL><iso>" before each
            # commit's file list. git log is reverse-chronological, so the FIRST time a
            # path appears (under the newest header) is its latest commit time. ONE pass,
            # no per-file subprocess (RP53). The NUL (0x00) sentinel is a byte that CANNOT
            # appear in a POSIX path, so a header can never be confused with a filename —
            # a file literally named "COMMIT foo.pdf" would have poisoned a "COMMIT "-prefix
            # parse (Gate-2 Correctness F1). Verified live.
            r = subprocess.run(
                [
                    "git", "log", f"--since={days}.days", "--diff-filter=ACMR",
                    "--name-only", "--pretty=format:%x00%cI",
                ],
                cwd=str(self.workspace_root),
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("ProductRegistry.backfill: git log failed: %s", exc)
            return
        if r.returncode != 0:
            return

        # Parse interleaved "<NUL><iso>" headers + file lists. First-seen per path
        # (newest commit) wins its timestamp. A line starting with NUL is a header;
        # anything else is a path (a path can never contain NUL).
        rel_times: dict[str, str] = {}
        cur_iso = ""
        for line in r.stdout.splitlines():
            if line.startswith("\x00"):
                cur_iso = line[1:].strip()
                continue
            rel = line.strip()
            if rel and rel not in rel_times:
                rel_times[rel] = cur_iso

        abs_paths: list[str] = []
        times: dict[str, str] = {}
        for rel, iso in rel_times.items():
            p = self.workspace_root / rel
            if p.is_file():
                a = str(p)
                abs_paths.append(a)
                if iso:
                    times[a] = iso
        if abs_paths:
            self.register_batch(abs_paths, times=times)
        # AC4: also fs-scan the gitignored product dirs git-log can NEVER list.
        self.scan_product_dirs()
        # run_fe228bc0 (Gate-2 HIGH): register_batch/_upsert only ADD or refresh rows —
        # neither removes a row that a NEW exclusion rule now drops. So a version-bump
        # re-backfill would leave the ~324 pre-existing noise rows (assets images,
        # .claude/**, Attachments/**) in the store forever, and the read path returns
        # them verbatim. Reconcile: drop any STORED row that _is_excluded_path or
        # _is_garbage now rejects. Purely SUBTRACTIVE (only removes non-products),
        # flock-guarded via _mutate, idempotent. This is what makes the _BACKFILL_VERSION
        # bump actually clean existing data (the "migration re-points but never cleans"
        # class — MEMORY: a migration that re-points at a new rule MUST populate/clean it).
        self._purge_excluded()
        # Mark backfill DONE (git-log succeeded) even when zero products were found,
        # so has_backfilled() is True and the endpoint won't re-run the git-log on the
        # next open of a product-less workspace. Stamp the CURRENT _BACKFILL_VERSION so a
        # future logic change (version bump) re-runs once to correct old data (Finding 1).
        def _mark(data: dict) -> None:
            data["backfilled_at"] = datetime.now(timezone.utc).isoformat()
            data["backfill_version"] = _BACKFILL_VERSION
        self._mutate(_mark)

    def _purge_excluded(self) -> int:
        """Drop stored rows that a current exclusion rule now rejects (run_fe228bc0).

        Reconciliation for a version-bump re-backfill: register_batch/_upsert never
        REMOVE, so a newly-excluded path (assets image, dot-folder, Attachments,
        garbage byproduct) already in the store would persist forever. This re-applies
        the SAME two drop-gates _classify uses on write — `_is_garbage` OR
        `derive_role(path, None) == Role.OTHER` — so the purge and the write path are
        ONE door (P8), never a divergent predicate.

        run_cb127db1: this MUST route through derive_role, NOT call _is_excluded_path
        directly. _is_excluded_path returns True for any .artifacts/ dot-folder path,
        but derive_role short-circuits a `.artifacts/runs/*/REPORT.md` to Role.PIPELINE
        (a kept product) BEFORE the exclusion — so calling _is_excluded_path directly
        wrongly purged every Pipeline REPORT. derive_role is the single source of truth
        for "is this a product?"; repo=None is safe because the exclusion / REPORT /
        DailyActivity / product-dir branches never read repo (repo only feeds
        _classify_kind's knowledge/source split, which never yields OTHER).

        RE-derives, does NOT trust the stored role: a stale role=Deliverables (written
        before the exclusion shipped) must not shield a noise row from purge.

        run_2b7230be (meta-review MEDIUM): a STARRED row is never purged — the same
        "a favorite is permanent" invariant the eviction cap honors (AC7). Otherwise the
        two row-dropping doors would DISAGREE about whether a favorite is sacrosanct
        (eviction protects it, purge silently removes it on the next version-bump
        re-backfill if its path later classifies to OTHER — the P8 seam inconsistency).
        A user who starred something that is genuinely noise can unstar it; the system
        never revokes a deliberate favorite behind their back.

        SUBTRACTIVE + SAFE: only removes rows the classifier no longer considers
        products; never adds, never rewrites a kept row. flock-guarded via _mutate.
        Returns the number of rows removed (for logging/tests).
        """
        removed = {"n": 0}

        def _reconcile(data: dict) -> None:
            products = data.get("products", [])
            kept = [
                e for e in products
                if bool(e.get("starred", False))  # a favorite is permanent (mirrors eviction)
                or not (
                    _is_garbage(e.get("path", ""))
                    or derive_role(e.get("path", ""), None) == Role.OTHER
                )
            ]
            removed["n"] = len(products) - len(kept)
            if removed["n"]:
                data["products"] = kept

        self._mutate(_reconcile)
        if removed["n"]:
            logger.info("ProductRegistry._purge_excluded: removed %d now-excluded rows", removed["n"])
        return removed["n"]

    def scan_product_dirs(self) -> None:
        """AC4: fs-walk known PRODUCT dirs for files git-log never lists (gitignored
        AIDLC decks under Projects/*/assets). TWO secret layers on this gitignored
        walk (Gate-2 HIGH): (1) the STRICTER ``_SCAN_EXTENSIONS`` positive allowlist
        (no .csv/.xlsx); (2) ``_is_secretish_name`` filename denylist — because a
        document-formatted secret (aws_credentials.pdf) carries a legit product
        extension AND classifies as Deliverables under a product dir, so GUARD-1
        (role==Other) does NOT catch it. Time = os.path.getmtime (the file's real
        age). Files still flow through register_batch → _classify → _batch_gitignored
        so each carries its correct ``gitignored`` flag.

        Bounded: skips _DEPENDENCY_SEGMENTS (vendored assets/). Fail-safe: an OSError
        on any dir is logged + skipped, never raised (a background/backfill caller
        must never crash on a permission or race).

        PRECEDENCE (real interaction fix): a TRACKED file already registered by
        git-log backfill carries its authoritative COMMIT date. The fs-scan must NOT
        re-register it with the (fresher) mtime — that would overwrite the commit
        date with ~now. So we skip any path already in the store; the fs-scan only
        ADDS files git-log never listed (the gitignored decks it exists to surface).
        """
        already: set[str] = {p.path for p in self.list_products()}
        # The product-dir roots to walk. Each glob yields real dirs on disk; a
        # missing dir simply yields nothing.
        scan_globs = [
            "Projects/*/assets",
            "Projects/*/deliverables",
            "Knowledge/Pollinate",
            "Knowledge/Library",
        ]
        abs_paths: list[str] = []
        times: dict[str, str] = {}
        for pattern in scan_globs:
            for root_dir in self.workspace_root.glob(pattern):
                if not root_dir.is_dir():
                    continue
                for dirpath, dirnames, filenames in os.walk(root_dir):
                    # Prune vendored/dependency subtrees + VCS/pipeline byproduct dirs
                    # in-place (Gate-2 meta-review Finding 2: a nested .git or an
                    # .artifacts/runs/** tree can be pathologically large — walking it
                    # fully would stat thousands of byproduct files on a pool worker).
                    _prune = {s.lower() for s in _DEPENDENCY_SEGMENTS} | {".git", ".artifacts"}
                    dirnames[:] = [d for d in dirnames if d.lower() not in _prune]
                    for fn in filenames:
                        if Path(fn).suffix.lower() not in _SCAN_EXTENSIONS:
                            continue
                        # Layer 2 (Gate-2 HIGH): a secret-named product-extension file
                        # under a gitignored product dir is NOT surfaced.
                        if _is_secretish_name(fn):
                            continue
                        fp = Path(dirpath) / fn
                        # run_fe228bc0: skip noise (assets-image intermediates, dot-folder
                        # nested files) at the scan too — _classify would drop them via
                        # derive_role->Other anyway, but skipping here avoids collecting
                        # a deck's hundreds of slide images only to discard them.
                        rel_for_excl = None
                        try:
                            _r = fp.resolve()
                            if _r.is_relative_to(self.workspace_root):
                                rel_for_excl = str(_r.relative_to(self.workspace_root))
                        except OSError:
                            rel_for_excl = None
                        if rel_for_excl and _is_excluded_path(rel_for_excl):
                            continue
                        try:
                            if not fp.is_file():
                                continue
                            rel_check = str(fp.resolve().relative_to(self.workspace_root)) \
                                if fp.resolve().is_relative_to(self.workspace_root) else None
                            if rel_check and rel_check in already:
                                continue  # git-log commit date wins over mtime
                            a = str(fp)
                            abs_paths.append(a)
                            times[a] = datetime.fromtimestamp(
                                os.path.getmtime(fp), tz=timezone.utc
                            ).isoformat()
                        except OSError as exc:
                            logger.warning("scan_product_dirs: stat failed %s: %s", fp, exc)
        if abs_paths:
            self.register_batch(abs_paths, times=times)
