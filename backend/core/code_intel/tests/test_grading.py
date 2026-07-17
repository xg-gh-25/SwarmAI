"""Tests for graded incremental re-analysis (grading.py).

Covers the pure functions (file_grade, classify_changeset, compute_signature,
has_id_collision) exhaustively, plus the run_36266b66-mandated integrity classes
for signature building (aliasing / idempotency / id-less). The WIRED path (reindex
handler conserve-on-skip) is covered by test_freshness.py's E2E harness.

Test taxonomy:
  - compute_signature: line-agnostic, dict/dataclass parity, bool coercion
  - file_grade: the NONE/COSMETIC/STRUCTURAL matrix incl. every fail-UP branch
  - classify_changeset: the SKIP/PARTIAL/ARCHITECTURE/FULL ladder at each boundary
  - has_id_collision: the Gate-1 #1 false-COSMETIC guard
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from core.code_intel import grading
from core.code_intel.grading import (
    NONE,
    COSMETIC,
    STRUCTURAL,
    SKIP,
    PARTIAL_UPDATE,
    ARCHITECTURE_UPDATE,
    FULL_UPDATE,
    compute_signature,
    file_grade,
    classify_changeset,
    has_id_collision,
    is_skippable,
)


# ── Test doubles mirroring parser.CodeNode / CodeEdge + graph dict rows ───


@dataclass
class _Node:
    id: str
    node_type: str = "function"
    name: str = "f"
    line_start: int = 1
    line_end: int = 5
    is_export: bool = True
    is_entry_point: bool = False


@dataclass
class _Edge:
    source_id: str
    target_id: str
    edge_type: str = "calls"
    line_number: int | None = None


@dataclass
class _ParseResult:
    nodes: list = field(default_factory=list)
    edges: list = field(default_factory=list)


def _dict_node(nid, ntype="function", exp=1, entry=0):
    """A DB-shaped node row (SQLite stores bools as 0/1 ints)."""
    return {
        "id": nid, "node_type": ntype, "name": "f",
        "line_start": 1, "line_end": 5,
        "is_export": exp, "is_entry_point": entry,
    }


# ── compute_signature ─────────────────────────────────────────────────────


class TestComputeSignature:
    def test_excludes_line_numbers(self):
        """A comment edit shifts line_start/end but the signature is identical."""
        n1 = _Node(id="a::f", line_start=1, line_end=5)
        n2 = _Node(id="a::f", line_start=40, line_end=44)  # shifted by a comment
        assert compute_signature([n1], []) == compute_signature([n2], [])

    def test_dict_and_dataclass_parity(self):
        """OLD sig (dict rows from DB) must equal NEW sig (dataclasses) for the
        same logical content — else every file would grade STRUCTURAL."""
        obj = _Node(id="a::f", node_type="function", is_export=True, is_entry_point=False)
        row = _dict_node("a::f", "function", exp=1, entry=0)
        assert compute_signature([obj], []) == compute_signature([row], [])

    def test_bool_coercion_int_vs_bool(self):
        """SQLite 1 and Python True must produce the same signature element."""
        int_row = _dict_node("a::f", exp=1, entry=0)
        bool_obj = _Node(id="a::f", is_export=True, is_entry_point=False)
        assert compute_signature([int_row], []) == compute_signature([bool_obj], [])

    def test_edge_triple_captured_line_agnostic(self):
        e1 = _Edge("a::f", "b::g", "calls", line_number=10)
        e2 = _Edge("a::f", "b::g", "calls", line_number=99)  # line shift only
        assert compute_signature([], [e1]) == compute_signature([], [e2])

    def test_edge_type_change_alters_signature(self):
        e1 = _Edge("a::f", "b::g", "calls")
        e2 = _Edge("a::f", "b::g", "imports")
        assert compute_signature([], [e1]) != compute_signature([], [e2])

    def test_new_symbol_alters_signature(self):
        base = [_Node(id="a::f")]
        added = [_Node(id="a::f"), _Node(id="a::g")]  # a real new function
        assert compute_signature(base, []) != compute_signature(added, [])

    def test_export_flag_change_alters_signature(self):
        pub = [_Node(id="a::f", is_export=True)]
        priv = [_Node(id="a::f", is_export=False)]
        assert compute_signature(pub, []) != compute_signature(priv, [])

    # run_36266b66 integrity class: no mutation of inputs (frozenset is immutable,
    # but prove the function does not alter the caller's lists).
    def test_does_not_mutate_inputs(self):
        nodes = [_Node(id="a::f")]
        edges = [_Edge("a::f", "b::g")]
        nodes_copy = list(nodes)
        edges_copy = list(edges)
        compute_signature(nodes, edges)
        assert nodes == nodes_copy and edges == edges_copy

    # run_36266b66 integrity class: idempotency on re-feed.
    def test_idempotent_on_refeed(self):
        nodes = [_Node(id="a::f"), _Node(id="a::g")]
        assert compute_signature(nodes, []) == compute_signature(nodes, [])

    def test_empty_inputs(self):
        assert compute_signature([], []) == (frozenset(), frozenset())


# ── has_id_collision ────────────────────────────────────────────────────────


class TestIdCollision:
    def test_no_collision(self):
        assert not has_id_collision([_Node(id="a::f"), _Node(id="a::g")])

    def test_collision_detected(self):
        # Two overloaded defs → same _qualify(name,path) id.
        assert has_id_collision([_Node(id="a::f"), _Node(id="a::f")])

    def test_empty(self):
        assert not has_id_collision([])


# ── file_grade ──────────────────────────────────────────────────────────────


class TestFileGrade:
    def _sig(self, nodes, edges=()):
        return compute_signature(nodes, list(edges))

    def test_none_when_bytes_unchanged(self):
        """No byte change → NONE regardless of everything else."""
        assert file_grade(None, _ParseResult(), byte_changed=False, is_supported=True) == NONE

    def test_cosmetic_comment_edit(self):
        """Bytes changed, signature identical (comment/whitespace) → COSMETIC."""
        old = self._sig([_Node(id="a::f", line_start=1)])
        new = _ParseResult(nodes=[_Node(id="a::f", line_start=50)])  # only line moved
        assert file_grade(old, new, byte_changed=True, is_supported=True) == COSMETIC

    def test_structural_signature_differs(self):
        """A real new function → signature differs → STRUCTURAL."""
        old = self._sig([_Node(id="a::f")])
        new = _ParseResult(nodes=[_Node(id="a::f"), _Node(id="a::g")])
        assert file_grade(old, new, byte_changed=True, is_supported=True) == STRUCTURAL

    def test_structural_when_unsupported_language(self):
        """Gate-1 #3: regex-degraded language never gets the COSMETIC skip, even
        if the (low-fidelity) signature looks identical."""
        old = self._sig([_Node(id="a::f", line_start=1)])
        new = _ParseResult(nodes=[_Node(id="a::f", line_start=50)])
        assert file_grade(old, new, byte_changed=True, is_supported=False) == STRUCTURAL

    def test_structural_when_no_old_signature(self):
        """New file / no baseline → cannot prove cosmetic → STRUCTURAL."""
        new = _ParseResult(nodes=[_Node(id="a::f")])
        assert file_grade(None, new, byte_changed=True, is_supported=True) == STRUCTURAL

    def test_structural_when_empty_parse(self):
        """A changed file that parsed to zero nodes → cannot prove cosmetic → fail UP."""
        old = self._sig([_Node(id="a::f")])
        assert file_grade(old, _ParseResult(nodes=[]), byte_changed=True, is_supported=True) == STRUCTURAL

    def test_structural_when_id_collision(self):
        """Gate-1 #1: fresh parse shows a colliding id → signature untrustworthy → STRUCTURAL,
        even if it happens to equal the (also-collapsed) old signature."""
        old = self._sig([_Node(id="a::f")])  # collapsed to one element
        new = _ParseResult(nodes=[_Node(id="a::f"), _Node(id="a::f")])  # collision
        assert file_grade(old, new, byte_changed=True, is_supported=True) == STRUCTURAL

    def test_edge_only_change_is_structural(self):
        """Same nodes but a new call edge → topology changed → STRUCTURAL."""
        old = self._sig([_Node(id="a::f"), _Node(id="a::g")], [])
        new = _ParseResult(
            nodes=[_Node(id="a::f"), _Node(id="a::g")],
            edges=[_Edge("a::f", "a::g", "calls")],
        )
        assert file_grade(old, new, byte_changed=True, is_supported=True) == STRUCTURAL


# ── classify_changeset ──────────────────────────────────────────────────────


class TestClassifyChangeset:
    def test_skip_all_none(self):
        assert classify_changeset([NONE, NONE], 0, 100) == SKIP

    def test_skip_all_cosmetic(self):
        assert classify_changeset([COSMETIC, NONE, COSMETIC], 0, 100) == SKIP

    def test_skip_empty(self):
        assert classify_changeset([], 0, 100) == SKIP

    def test_partial_one_structural(self):
        assert classify_changeset([STRUCTURAL, COSMETIC], 0, 100) == PARTIAL_UPDATE

    def test_partial_boundary_below_arch(self):
        """Exactly ARCH_STRUCTURAL_THRESHOLD structural (10) is still PARTIAL (> is the trigger)."""
        grades = [STRUCTURAL] * grading.ARCH_STRUCTURAL_THRESHOLD
        assert classify_changeset(grades, 0, 1000) == PARTIAL_UPDATE

    def test_architecture_new_topdir(self):
        assert classify_changeset([STRUCTURAL], new_or_deleted_topdirs=1, total_graph_files=1000) == ARCHITECTURE_UPDATE

    def test_architecture_structural_over_threshold(self):
        grades = [STRUCTURAL] * (grading.ARCH_STRUCTURAL_THRESHOLD + 1)  # 11
        assert classify_changeset(grades, 0, 1000) == ARCHITECTURE_UPDATE

    def test_full_over_file_threshold(self):
        grades = [STRUCTURAL] * (grading.FULL_FILE_THRESHOLD + 1)  # 101
        assert classify_changeset(grades, 0, 100000) == FULL_UPDATE

    def test_full_over_pct_share(self):
        """21 structural of 40 graph files = 52.5% > FULL_PCT → FULL (even though
        21 < FULL_FILE_THRESHOLD)."""
        grades = [STRUCTURAL] * 21
        assert classify_changeset(grades, 0, total_graph_files=40) == FULL_UPDATE

    def test_pct_disabled_when_zero_graph_files(self):
        """total_graph_files=0 must not div-by-zero; falls through to count thresholds."""
        assert classify_changeset([STRUCTURAL], 0, 0) == PARTIAL_UPDATE

    def test_full_beats_architecture_precedence(self):
        """>FULL_FILE_THRESHOLD structural AND a new topdir → FULL wins (checked first)."""
        grades = [STRUCTURAL] * (grading.FULL_FILE_THRESHOLD + 1)
        assert classify_changeset(grades, new_or_deleted_topdirs=1, total_graph_files=100000) == FULL_UPDATE


# ── is_skippable ────────────────────────────────────────────────────────────


class TestIsSkippable:
    @pytest.mark.parametrize("grade,expected", [
        (NONE, True), (COSMETIC, True), (STRUCTURAL, False),
    ])
    def test_skippable(self, grade, expected):
        assert is_skippable(grade) is expected
