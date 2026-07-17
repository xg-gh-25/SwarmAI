"""Graded incremental re-analysis for code-intel.

Adopted from Understand-Anything's `change-classifier.ts` (research report
2026-07-17), but bound to OUR node/edge shape and OUR "merge never silently
drops" guards (UA hit 4+ data-loss bugs — #402/#546/#484 — on exactly this
incremental path, so every skip here is *leave-untouched*, never *remove*).

Two pure functions (NO IO, NO DB — signatures are passed in; the caller reads
them from the graph). Purity is deliberate: it makes the risky COSMETIC-skip
decision unit- and mutation-testable in isolation.

Layer map (verified against source, run_4602932d):
  - `freshness.suggest_full_rebuild` (freshness.py:141) stays the OUTER coarse
    gate (>100 changed files or >50 commits → full rebuild).
  - THIS module is the INNER per-file + per-changeset grading beneath it.

Grades (per file):
  NONE       — byte-identical (content hash unchanged). Nothing to do.
  COSMETIC   — bytes differ BUT the code SIGNATURE is identical (comment /
               whitespace / reformat only). Safe to skip re-store: the graph's
               node/edge topology is unchanged. Line numbers MAY drift — that is
               the documented tradeoff (a signature deliberately excludes
               line_start/line_end so a comment edit is not STRUCTURAL).
  STRUCTURAL — signature differs, OR the file is not tree-sitter-live (regex
               fallback misses nested/method defs → "signature identical" would
               NOT mean "semantically identical", Gate-1 #3), OR the file has an
               id collision (two defs share name+path → INSERT OR REPLACE keeps
               one, so a real change to the other could look COSMETIC, Gate-1 #1).
               STRUCTURAL is the fail-UP verdict: when in doubt, re-store.

Changeset ladder (aggregate over all file grades — mirrors UA
change-classifier.ts:46-77 thresholds):
  SKIP                — every file is NONE/COSMETIC (no structural change at all).
  PARTIAL_UPDATE      — ≥1 STRUCTURAL file, below the architecture thresholds.
  ARCHITECTURE_UPDATE — a top-level dir was added/removed, OR structural count
                        exceeds ARCH_STRUCTURAL_THRESHOLD.
  FULL_UPDATE         — structural count exceeds FULL_FILE_THRESHOLD, OR the
                        structural share of the graph exceeds FULL_PCT.

The changeset verdict is the honest domain-staleness signal (design §7.4): a
SKIP verdict means the deterministic graph topology did not change, so the
expensive downstream re-analysis (routes/FTS/prefix resolution, and later the
LLM domain layer) can be short-circuited.
"""

from __future__ import annotations

from typing import Iterable

# ── Node/edge signature element types ────────────────────────────────────
# A node signature element captures identity + role, NEVER line position:
#   (id, node_type, is_export, is_entry_point)
# An edge signature element is the topological triple:
#   (source_id, target_id, edge_type)
# Line numbers are DELIBERATELY excluded (a comment edit shifts lines but must
# not read as STRUCTURAL). node id = name+path (parser.py:_qualify), also
# line-free — verified run_4602932d.

# ── Changeset ladder thresholds (named — no magic numbers) ────────────────
# FULL mirrors freshness.py:141's existing >100 coarse gate for consistency.
FULL_FILE_THRESHOLD = 100          # >this many STRUCTURAL files → FULL_UPDATE
FULL_PCT = 0.5                     # STRUCTURAL share of graph >this → FULL_UPDATE
ARCH_STRUCTURAL_THRESHOLD = 10     # >this many STRUCTURAL files → ARCHITECTURE

# Grade constants (module-level so callers compare against names, not literals).
NONE = "NONE"
COSMETIC = "COSMETIC"
STRUCTURAL = "STRUCTURAL"

# Changeset verdict constants.
SKIP = "SKIP"
PARTIAL_UPDATE = "PARTIAL_UPDATE"
ARCHITECTURE_UPDATE = "ARCHITECTURE_UPDATE"
FULL_UPDATE = "FULL_UPDATE"

_SKIPPABLE_GRADES = frozenset({NONE, COSMETIC})

# Mirror of parser.QUALIFIED_SEPARATOR ("::"). Kept local (not imported) so this
# pure module has zero dependency on the parser. A node id / resolved edge target
# is "qualified" when it contains this separator (e.g. "x.py::helper"); a bare
# target ("helper") is an unresolved Layer-1 emission — see compute_signature.
_QUALIFIED_SEPARATOR = "::"


def compute_signature(
    nodes: Iterable,
    edges: Iterable,
) -> tuple[frozenset, frozenset]:
    """Build a line-agnostic (node_sig, edge_sig) pair from nodes + edges.

    Accepts BOTH dataclass-style objects (CodeNode/CodeEdge, attribute access)
    AND dict rows (graph_store.get_nodes_by_file / get_edges_by_file). This lets
    the OLD signature (read from the DB as dicts) and the NEW signature (fresh
    ParseResult dataclasses) be compared apples-to-apples.

    node element = (id, node_type, bool(is_export), bool(is_entry_point))
    edge element = (source_id, target_id, edge_type)

    Booleans are coerced with `bool()` because SQLite stores them as 0/1 ints
    while the parser emits real bools — without coercion the OLD (int) and NEW
    (bool) signatures would never compare equal and every file would be
    STRUCTURAL (the silent-no-op failure mode Gate-1 warned about).

    ⚠️ RESOLVED-EDGES-ONLY (Review HIGH #3): only edges whose `target_id` is a
    fully-qualified id (contains QUALIFIED_SEPARATOR "::") enter the signature.
    Rationale: the STORED (old) edges went through the full-rebuild's
    `resolve_bare_targets` + orphan-prune (graph_store.bulk_insert), so a
    cross-file call is stored as `x.py::helper`; but an incremental single-file
    re-parse (parser Layer-1 only) emits the SAME call as a BARE `helper`. Keeping
    bare targets would make OLD (`x.py::helper`) ≠ NEW (`helper`) for every
    cross-file-call file → false STRUCTURAL → the COSMETIC optimization silently
    no-ops on a large fraction of real files. Excluding unresolved targets on BOTH
    sides compares only the stable resolved topology. Safe direction: if a genuine
    NEW cross-file call appears, its SOURCE-side node/edge or a resolved sibling
    still shifts the signature; the worst case of dropping a bare edge is a missed
    COSMETIC→still-STRUCTURAL (never a false COSMETIC skip).
    """
    node_sig = frozenset(
        (
            _get(n, "id"),
            _get(n, "node_type"),
            bool(_get(n, "is_export")),
            bool(_get(n, "is_entry_point")),
        )
        for n in nodes
    )
    edge_sig = frozenset(
        (
            _get(e, "source_id"),
            _get(e, "target_id"),
            _get(e, "edge_type"),
        )
        for e in edges
        if _QUALIFIED_SEPARATOR in (_get(e, "target_id") or "")
    )
    return node_sig, edge_sig


def has_id_collision(nodes: Iterable) -> bool:
    """True if two nodes share the same `id` (name+path collision).

    Gate-1 #1: node id = _qualify(name, path) does NOT disambiguate overloads or
    same-named nested defs, and `store_file_nodes_edges` uses INSERT OR REPLACE
    on the id PK — so a colliding pair collapses to one stored node AND one
    signature element. A real change to the *other* colliding def would then look
    COSMETIC → false skip → silent stale graph. When a file's fresh parse shows
    an id collision, we fail UP to STRUCTURAL rather than trust the signature.
    """
    seen: set = set()
    for n in nodes:
        nid = _get(n, "id")
        if nid in seen:
            return True
        seen.add(nid)
    return False


def file_grade(
    old_sig: tuple[frozenset, frozenset] | None,
    new_result,
    byte_changed: bool,
    is_supported: bool,
) -> str:
    """Grade a single changed file: NONE | COSMETIC | STRUCTURAL.

    Args:
        old_sig: the (node_sig, edge_sig) previously stored for this file, or
            None if the file is new / had no stored nodes. None → STRUCTURAL
            (can't prove it's unchanged → fail UP).
        new_result: the fresh ParseResult (has `.nodes` and `.edges`). If it has
            no nodes (parse failure / empty), we cannot prove COSMETIC → STRUCTURAL.
        byte_changed: True if the file's content hash differs from the stored
            hash. False → NONE (nothing changed at the byte level at all).
        is_supported: True ONLY if tree-sitter is live for this language
            (`_tree_sitter_live(lang)`), NOT merely "extension in LANGUAGE_MAP".
            Gate-1 #3: the regex fallback misses nested/method defs, so a real
            added method could leave the signature unchanged → false COSMETIC.
            Unsupported/regex-degraded → fail UP to STRUCTURAL.

    Fail-UP philosophy: every uncertainty (no old sig, empty parse, unsupported
    language, id collision) resolves to STRUCTURAL. A wrong STRUCTURAL costs one
    redundant re-store (cheap, idempotent). A wrong COSMETIC silently staleness
    the graph (the exact UA #402 data-loss class) — never acceptable.
    """
    if not byte_changed:
        return NONE

    # Beyond here bytes changed → must decide COSMETIC vs STRUCTURAL.
    if not is_supported:
        return STRUCTURAL  # regex fidelity too low to trust "signature identical"

    if old_sig is None:
        return STRUCTURAL  # new file / no baseline → can't prove cosmetic

    new_nodes = getattr(new_result, "nodes", None) or []
    new_edges = getattr(new_result, "edges", None) or []
    if not new_nodes:
        # Empty/failed parse of a changed file — cannot prove cosmetic. Fail UP.
        return STRUCTURAL

    if has_id_collision(new_nodes):
        return STRUCTURAL  # collision makes the signature untrustworthy (Gate-1 #1)

    new_sig = compute_signature(new_nodes, new_edges)
    return COSMETIC if new_sig == old_sig else STRUCTURAL


def classify_changeset(
    grades: list[str],
    new_or_deleted_topdirs: int,
    total_graph_files: int,
) -> str:
    """Aggregate per-file grades into a changeset verdict.

    Args:
        grades: the per-file grade for every changed file.
        new_or_deleted_topdirs: count of top-level directories added or removed
            in this changeset (an architectural signal — UA change-classifier.ts).
        total_graph_files: number of distinct files currently in the graph (the
            denominator for the FULL_PCT share test). 0 → share test disabled
            (avoid div-by-zero; fall through to count-based thresholds).

    Returns SKIP | PARTIAL_UPDATE | ARCHITECTURE_UPDATE | FULL_UPDATE.
    """
    structural = sum(1 for g in grades if g == STRUCTURAL)

    if structural == 0:
        return SKIP

    # FULL: absolute count OR share of the whole graph.
    if structural > FULL_FILE_THRESHOLD:
        return FULL_UPDATE
    if total_graph_files > 0 and (structural / total_graph_files) > FULL_PCT:
        return FULL_UPDATE

    # ARCHITECTURE: topology of the tree changed, or many structural files.
    if new_or_deleted_topdirs > 0 or structural > ARCH_STRUCTURAL_THRESHOLD:
        return ARCHITECTURE_UPDATE

    return PARTIAL_UPDATE


def is_skippable(grade: str) -> bool:
    """True for grades whose file should NOT be re-stored (NONE/COSMETIC).

    A skippable file is LEFT UNTOUCHED in the graph — never removed. This is the
    single chokepoint the reindex handler consults so the "never silently drop"
    guarantee is expressed in one testable place.
    """
    return grade in _SKIPPABLE_GRADES


def _get(obj, field: str):
    """Read `field` from either a dict row or a dataclass/attr object."""
    if isinstance(obj, dict):
        return obj.get(field)
    return getattr(obj, field, None)
