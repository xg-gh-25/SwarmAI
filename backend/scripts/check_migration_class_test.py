"""Tests for check_migration_class — the goal-run class-completeness gate core.

Design: Projects/SwarmAI/2-understanding/knowledge/designs/2026-08-10-goal-run-class-completeness-gate-design.md
run_1d3df9e6. Covers AC3/AC4/AC5/AC7/AC8/AC10 (AC11 is evaluate.md-side; AC6 is validator-side).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from check_migration_class import (
    check_migration_class,
    validate_enumeration_cmd,
    CompletenessResult,
    _valid_locator,
    _locator_matches_line,
)


def _write_tree(tmp_path: Path) -> Path:
    """A fake source tree with 3 sink callers across 2 files (one caller the
    author's mental model might miss)."""
    (tmp_path / "hooks").mkdir()
    (tmp_path / "core").mkdir()
    (tmp_path / "hooks" / "distill.py").write_text(
        "def a():\n    _run_locked_write(p, 'Guidelines', x)  # lessons\n"
        "def b():\n    _run_locked_write(p, 'Decisions', y)  # decisions — the easy-to-miss sibling\n"
    )
    (tmp_path / "core" / "cult.py").write_text(
        "def c():\n    apply_to_ddd(prop, d)  # writeback\n"
    )
    return tmp_path


# ── AC10 / R-A: enumeration_cmd must be chokepoint-shaped ─────────────────────
class TestEnumerationValidation:
    def test_reject_echo_literal_list(self):
        ok, reason = validate_enumeration_cmd("echo 'a\\nb\\nc'")
        assert ok is False and "echo" in reason.lower()

    def test_reject_bare_member_list_no_grep(self):
        ok, reason = validate_enumeration_cmd("printf 'lessons\\ndecisions\\n'")
        assert ok is False

    def test_accept_sink_grep_across_tree(self):
        ok, _ = validate_enumeration_cmd(
            "grep -rn '_run_locked_write(\\|apply_to_ddd(' backend/ | grep -v 'def '")
        assert ok is True

    def test_reject_curated_single_file_subset_flagged(self):
        # a grep scoped to ONE named file is the Axis-1 blind spot — warn/reject
        ok, reason = validate_enumeration_cmd(
            "grep -n '_run_locked_write(' backend/hooks/distill.py")
        assert ok is False and ("subset" in reason.lower() or "tree" in reason.lower())

    # ── Gate-3 hardening: shell-abuse / curation vectors on the enumeration_cmd ──
    def test_reject_command_chaining(self):
        for c in ("grep -rn 's(' backend/ ; echo faked",
                  "grep -rn 's(' $(cat dirs)",
                  "grep -rn 's(' backend/ && echo x",
                  "grep -rn 's(' backend/ > out.txt"):
            ok, reason = validate_enumeration_cmd(c)
            assert ok is False, f"must reject chaining/redirection: {c}"

    def test_chain_char_inside_grep_pattern_is_allowed(self):
        # a `\|` BRE alternation inside the quoted pattern is NOT shell chaining
        ok, _ = validate_enumeration_cmd("grep -rn '_a(\\|_b(' backend/")
        assert ok is True

    def test_reject_include_exclude_scope_flag(self):
        ok, reason = validate_enumeration_cmd("grep -rn --include=*.py 's(' backend/")
        assert ok is False and ("include" in reason.lower() or "subset" in reason.lower())

    def test_reject_non_def_pipe_filter(self):
        # only `| grep -v 'def '` is sanctioned; any other pipe curates/transforms
        for c in ("grep -rn 's(' backend/ | sed 's/x/y/'",
                  "grep -rn 's(' backend/ | grep memory",
                  "grep -rn 's(' backend/ | grep -v somemember"):
            ok, reason = validate_enumeration_cmd(c)
            assert ok is False, f"must reject curating pipe: {c}"

    def test_accept_sanctioned_grep_v_def_pipe(self):
        ok, _ = validate_enumeration_cmd("grep -rn 's(' backend/ | grep -v 'def '")
        assert ok is True


# ── AC3/AC8: reconcile live members; an undeclared live member BLOCKS ─────────
class TestReconciliation:
    def _mc(self, tmp_path, members):
        return {
            "description": "every path that writes to a store",
            "enumeration_cmd": f"grep -rn '_run_locked_write(\\|apply_to_ddd(' {tmp_path} | grep -v 'def '",
            "members": members,
        }

    def test_all_declared_migrated_passes(self, tmp_path):
        _write_tree(tmp_path)
        mc = self._mc(tmp_path, [
            {"id": "lessons", "disposition": "migrated", "locator": "hooks/distill.py:2", "evidence": "_admit_memory_lesson"},
            {"id": "decisions", "disposition": "migrated", "locator": "hooks/distill.py:4", "evidence": "_admit_memory_lesson"},
            {"id": "writeback", "disposition": "migrated", "locator": "core/cult.py:2", "evidence": "admission_band"},
        ])
        res = check_migration_class(mc, cwd=tmp_path)
        assert res.passed is True
        assert res.blocked == []

    def test_undeclared_live_member_blocks(self, tmp_path):
        """AC8 — the run_0d60e04e catch: a live sink caller (decisions) with NO
        members[] row = MISSED → BLOCK."""
        _write_tree(tmp_path)
        mc = self._mc(tmp_path, [
            {"id": "lessons", "disposition": "migrated", "locator": "hooks/distill.py:2", "evidence": "_admit_memory_lesson"},
            {"id": "writeback", "disposition": "migrated", "locator": "core/cult.py:2", "evidence": "admission_band"},
            # 'decisions' sink caller (distill.py:4) deliberately NOT declared
        ])
        res = check_migration_class(mc, cwd=tmp_path)
        assert res.passed is False
        assert any(b["kind"] == "MISSED" for b in res.blocked)

    def test_carveout_with_reason_passes(self, tmp_path):
        _write_tree(tmp_path)
        mc = self._mc(tmp_path, [
            {"id": "lessons", "disposition": "migrated", "locator": "hooks/distill.py:2", "evidence": "_admit_memory_lesson"},
            {"id": "decisions", "disposition": "migrated", "locator": "hooks/distill.py:4", "evidence": "_admit_memory_lesson"},
            {"id": "writeback", "disposition": "carved-out", "locator": "core/cult.py:2", "evidence": "value-refresh not ingestion"},
        ])
        res = check_migration_class(mc, cwd=tmp_path)
        assert res.passed is True

    def test_carveout_without_reason_blocks(self, tmp_path):
        """AC5 — carve-out must carry a one-line reason, else UNJUSTIFIED → block."""
        _write_tree(tmp_path)
        mc = self._mc(tmp_path, [
            {"id": "lessons", "disposition": "migrated", "locator": "hooks/distill.py:2", "evidence": "_admit_memory_lesson"},
            {"id": "decisions", "disposition": "migrated", "locator": "hooks/distill.py:4", "evidence": "_admit_memory_lesson"},
            {"id": "writeback", "disposition": "carved-out", "locator": "core/cult.py:2", "evidence": ""},
        ])
        res = check_migration_class(mc, cwd=tmp_path)
        assert res.passed is False
        assert any(b["kind"] == "UNJUSTIFIED_CARVEOUT" for b in res.blocked)


# ── AC7: coverage table always emitted ────────────────────────────────────────
class TestCoverageTable:
    def test_table_emitted_on_pass_and_block(self, tmp_path):
        _write_tree(tmp_path)
        mc = {
            "description": "writes",
            "enumeration_cmd": f"grep -rn '_run_locked_write(\\|apply_to_ddd(' {tmp_path} | grep -v 'def '",
            "members": [{"id": "lessons", "disposition": "migrated", "locator": "hooks/distill.py:2", "evidence": "_admit_memory_lesson"}],
        }
        res = check_migration_class(mc, cwd=tmp_path)
        assert res.coverage_table  # non-empty string
        assert "CLASS:" in res.coverage_table


# ── absent migration_class → no-op PASS (AC2 core, keyword handling is evaluate.md) ──
class TestNoOp:
    def test_absent_is_noop_pass(self, tmp_path):
        res = check_migration_class(None, cwd=tmp_path)
        assert res.passed is True
        assert res.noop is True


# ── Gate-2 adversarial regressions (run_1d3df9e6 review) ──────────────────────
class TestGate2Regressions:
    def _mc(self, tmp_path, members):
        return {"description": "writes",
                "enumeration_cmd": f"grep -rn '_run_locked_write(\\|apply_to_ddd(' {tmp_path} | grep -v 'def '",
                "members": members}

    def test_1_full_path_no_basename_collision(self):
        # #1: distill.py:4 must NOT match a different-directory core/distill.py:4
        from check_migration_class import _locator_matches_line
        assert _locator_matches_line("hooks/distill.py:4", "x/core/distill.py:4:sink()") is False
        assert _locator_matches_line("hooks/distill.py:4", "x/hooks/distill.py:4:sink()") is True

    def test_2_bare_locator_rejected(self):
        # #2: a no-line / no-path locator is a wildcard → rejected at validation
        from check_migration_class import _valid_locator
        assert _valid_locator("distill.py") is False          # no line
        assert _valid_locator("distill.py:4") is False        # Gate-3 M2: bare basename (no dir) → rejected
        assert _valid_locator("hooks/distill.py:4") is True   # directory-qualified → valid
        assert _valid_locator("") is False

    def test_2_bad_locator_member_blocks(self, tmp_path):
        _write_tree(tmp_path)
        mc = self._mc(tmp_path, [{"id": "wildcard", "disposition": "migrated",
                                  "locator": "distill.py", "evidence": "s"}])  # no line
        res = check_migration_class(mc, cwd=tmp_path)
        assert res.passed is False
        assert any(b["kind"] == "BAD_LOCATOR" for b in res.blocked)

    def test_3_empty_enumeration_blocks_not_passes(self, tmp_path):
        # #3: a non-empty class whose grep matches nothing must BLOCK, not fail-open
        mc = {"description": "writes",
              "enumeration_cmd": f"grep -rn '_TYPO_NEVER_MATCHES(' {tmp_path}",
              "members": [{"id": "m", "disposition": "migrated", "locator": "hooks/f.py:1", "evidence": "s"}]}
        res = check_migration_class(mc, cwd=tmp_path)
        assert res.passed is False
        assert any(b["kind"] == "EMPTY_ENUMERATION" for b in res.blocked)

    def test_4_deep_subdir_grep_rejected(self):
        # #4: a recursive grep scoped to a DEEP subdir omits peer subtrees → reject
        ok, reason = validate_enumeration_cmd("grep -rn '_run_locked_write(' backend/hooks/")
        assert ok is False and "subdir" in reason.lower()
        # tree-root scope is fine
        assert validate_enumeration_cmd("grep -rn '_run_locked_write(' backend/")[0] is True

    def test_4_truncated_output_rejected(self):
        ok, reason = validate_enumeration_cmd("grep -rn 'sink(' backend/ | head -3")
        assert ok is False and ("head" in reason.lower() or "truncat" in reason.lower())


# ── Gate-3 adversarial regressions (E2E audit of run_1d3df9e6) ────────────────
class TestGate3Regressions:
    """Locks the CRITICAL/HIGH findings the E2E audit confirmed and fixed. Each test
    replays the exact attack vector; a future edit that reopens the hole fails HERE."""

    def _mc(self, tmp_path, members, cmd=None):
        return {"description": "writes",
                "enumeration_cmd": cmd or f"grep -rn '_run_locked_write(\\|apply_to_ddd(' {tmp_path} | grep -v 'def '",
                "members": members}

    def test_C1_empty_members_blocks_not_fail_open(self, tmp_path):
        """C1 (CRITICAL): a class that declares enumeration_cmd but members=[] previously
        PASSED silently (reconcile loop can't produce MISSED with zero declared rows) —
        the exact run_0d60e04e miss. Must now BLOCK with EMPTY_MEMBERS."""
        _write_tree(tmp_path)
        res = check_migration_class(self._mc(tmp_path, []), cwd=tmp_path)
        assert res.passed is False, "empty members[] must not fail-open to PASS"
        assert any(b["kind"] == "EMPTY_MEMBERS" for b in res.blocked)

    def test_H1_member_curating_pipe_rejected(self):
        """H1 (HIGH): `| grep -v <member-name>` filters the to-be-missed sibling out of
        the enumeration → never MISSED → PASS. Only `| grep -v 'def '` is sanctioned."""
        ok, _ = validate_enumeration_cmd("grep -rn '_run_locked_write(' backend/ | grep -v decisions")
        assert ok is False
        # the sanctioned structural filter still works:
        assert validate_enumeration_cmd("grep -rn '_run_locked_write(' backend/ | grep -v 'def '")[0] is True

    def test_H3_scope_flag_rejected(self):
        """H3 (HIGH): --include/--exclude scopes the grep to a file subset."""
        assert validate_enumeration_cmd("grep -rn --include='*_new.py' 's(' backend/")[0] is False

    def test_M3_sed_awk_truncation_rejected(self):
        """M3: sed/awk post-filters truncate the member set (like head/tail)."""
        assert validate_enumeration_cmd("grep -rn 's(' backend/ | sed -n '1,3p'")[0] is False
        assert validate_enumeration_cmd("grep -rn 's(' backend/ | awk 'NR<=2'")[0] is False

    def test_M4_command_chaining_rejected(self):
        """M4: chaining/substitution/redirection lets an author fake the enumeration
        output or run side effects under shell=True."""
        for c in ("grep -rn 's(' backend/; true",
                  "grep -rn 's(' backend/ && echo x",
                  "grep -rn 's(' $(cat dirs)",
                  "grep -rn 's(' backend/ > out"):
            assert validate_enumeration_cmd(c)[0] is False, f"must reject: {c}"

    def test_M4_bre_alternation_not_mistaken_for_chaining(self):
        """M4 guard: a grep-BRE `\\|` alternation inside the quoted pattern is NOT a
        shell pipe/chain — it must still pass (else the sanctioned sink-alternation breaks)."""
        assert validate_enumeration_cmd("grep -rn '_a(\\|_b(' backend/")[0] is True

    def test_M2_bare_basename_locator_no_cross_dir_match(self):
        """M2: `x.py:10` suffix-matched a different-dir `a/b/x.py:10`. Require ≥1 slash."""
        assert _valid_locator("x.py:10") is False
        assert _valid_locator("hooks/x.py:10") is True
        assert _locator_matches_line("hooks/distill.py:4", "z/core/distill.py:4:s()") is False
