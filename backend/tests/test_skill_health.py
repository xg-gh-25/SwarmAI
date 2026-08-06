"""Tests for skill health status folding + the fail-safe /api/skills/health endpoint.

Covers run_a85e6641 (scannable at-a-glance health signal on the Capabilities panel):
- fold_status(SkillStats|None) -> qualitative enum {healthy, low_success, never_used, stale}
  across ALL branches incl. None, empty last_used, and the invocation_count>=5 floor.
- build_health_map marks list-skills ABSENT from metrics as never_used.
- GET /api/skills/health is run-mode-keyed (folds ONLY over the _visible_to_caller list —
  Gate-1 BLOCK-3: internal skill names must NOT leak to a non-owner via the map keys) and
  FAIL-SAFE (metrics store raising -> 200 with empty map, never 500 — Gate-1 WARN + AC7).

Naming honesty (Gate-1 WARN-1): the metric is LIFETIME success_rate with no recency, so the
status is `low_success`, NOT `recently_failed` (R30#4 — a decision-inert/misleading label is
banned). A floor of invocation_count>=5 (mirrors get_evolution_candidates, skill_metrics.py:173)
prevents a 1-of-2-failed skill from showing low_success forever on no real signal.
"""
from datetime import date, timedelta

import pytest

from core.skill_health import (
    STALENESS_DAYS,
    MIN_INVOCATIONS_FOR_LOW_SUCCESS,
    fold_status,
    build_health_map,
)
from core.skill_metrics import SkillStats


def _stats(
    name="s_x",
    invocation_count=10,
    success_rate=1.0,
    last_used=None,
) -> SkillStats:
    if last_used is None:
        last_used = date.today().isoformat()
    return SkillStats(
        skill_name=name,
        invocation_count=invocation_count,
        success_rate=success_rate,
        avg_duration=0.0,
        correction_rate=0.0,
        last_used=last_used,
    )


class TestFoldStatus:
    def test_none_stats_is_never_used(self):
        # A skill with zero metric rows (absent from get_all_stats) -> never_used.
        assert fold_status(None) == "never_used"

    def test_recent_high_success_is_healthy(self):
        assert fold_status(_stats(success_rate=0.95, last_used=date.today().isoformat())) == "healthy"

    def test_low_lifetime_success_over_floor_is_low_success(self):
        # >= floor invocations AND success_rate < 0.7 -> low_success.
        s = _stats(invocation_count=10, success_rate=0.5, last_used=date.today().isoformat())
        assert fold_status(s) == "low_success"

    def test_low_success_below_invocation_floor_is_not_low_success(self):
        # Gate-1 WARN-1: a 1-of-2-failed skill (below the floor) must NOT show low_success
        # on no real signal — it is recent + present, so healthy.
        s = _stats(invocation_count=2, success_rate=0.5, last_used=date.today().isoformat())
        assert fold_status(s) == "healthy"

    def test_old_last_used_is_stale(self):
        old = (date.today() - timedelta(days=STALENESS_DAYS + 5)).isoformat()
        assert fold_status(_stats(success_rate=1.0, last_used=old)) == "stale"

    def test_low_success_takes_precedence_over_stale(self):
        # A skill both stale AND low-success: low_success wins (a broken skill is more
        # important to surface than an idle one). Ordering: never_used>low_success>stale>healthy.
        old = (date.today() - timedelta(days=STALENESS_DAYS + 5)).isoformat()
        s = _stats(invocation_count=10, success_rate=0.4, last_used=old)
        assert fold_status(s) == "low_success"

    def test_empty_last_used_does_not_crash(self):
        # Gate-1 WARN-1: MAX(invocation_date) can be "" (SkillStats last_used `or ""`).
        # date.fromisoformat("") would raise -> the fold MUST guard per-row, not rely on the
        # router try/except (which would blank ALL dots, not just this one).
        s = _stats(invocation_count=10, success_rate=1.0, last_used="")
        # No crash; a present-but-dateless row is treated as healthy (has invocations, not stale-provable).
        assert fold_status(s) == "healthy"

    def test_malformed_last_used_does_not_crash(self):
        s = _stats(invocation_count=10, success_rate=1.0, last_used="not-a-date")
        assert fold_status(s) == "healthy"

    def test_boundary_success_rate_exactly_070_is_healthy(self):
        # <0.7 is exclusive — exactly 0.7 is NOT low_success.
        s = _stats(invocation_count=10, success_rate=0.70, last_used=date.today().isoformat())
        assert fold_status(s) == "healthy"

    def test_all_statuses_are_in_the_enum(self):
        from core.skill_health import SKILL_HEALTH_STATUSES
        for st in (None, _stats(), _stats(success_rate=0.1, invocation_count=9)):
            assert fold_status(st) in SKILL_HEALTH_STATUSES


class TestBuildHealthMap:
    def test_absent_skill_is_never_used(self):
        # A skill in the list but with no metrics row -> never_used entry present.
        stats = [_stats(name="s_used", success_rate=1.0)]
        m = build_health_map(stats, ["s_used", "s_never_run"])
        assert m["s_never_run"]["status"] == "never_used"
        assert m["s_used"]["status"] == "healthy"

    def test_map_keys_are_subset_of_skill_names(self):
        # Gate-1 BLOCK-3: the map must NOT contain a metrics-only skill that is not in the
        # visible skill_names list (else an internal/filtered skill leaks via a map key).
        stats = [_stats(name="s_used"), _stats(name="s_cmhk_internal")]
        m = build_health_map(stats, ["s_used"])  # s_cmhk_internal NOT in visible list
        assert set(m.keys()) == {"s_used"}
        assert "s_cmhk_internal" not in m

    def test_detail_fields_present_for_drawer(self):
        stats = [_stats(name="s_used", success_rate=0.9, last_used="2026-08-01")]
        m = build_health_map(stats, ["s_used"])
        assert m["s_used"]["success_rate"] == 0.9
        assert m["s_used"]["last_used"] == "2026-08-01"

    def test_never_used_detail_is_null(self):
        m = build_health_map([], ["s_never"])
        assert m["s_never"]["success_rate"] is None
        assert m["s_never"]["last_used"] is None

    def test_bare_metrics_name_matches_prefixed_folder(self):
        # Gate-2 HIGH (run_a85e6641): the metrics store records skill_name in BOTH formats
        # — bare ("pdf", from the SDK tool_use input path) and s_-prefixed ("s_pdf", from the
        # summary-parse path). The visible skill list is always folder_name ("s_pdf"). Without
        # canonicalizing the join, a heavily-used skill recorded under the bare name would
        # falsely fold to never_used. Verified against prod DB: 9 bare + 53 prefixed names live.
        stats = [_stats(name="pdf", invocation_count=10, success_rate=1.0)]
        m = build_health_map(stats, ["s_pdf"])
        assert m["s_pdf"]["status"] == "healthy", "bare metrics name must match s_-prefixed folder"

    def test_prefixed_metrics_name_still_matches_prefixed_folder(self):
        # The other recording path (summary → "s_pdf") must ALSO match — canonicalization
        # must not break the already-matching case.
        stats = [_stats(name="s_autonomous-pipeline", invocation_count=10, success_rate=1.0)]
        m = build_health_map(stats, ["s_autonomous-pipeline"])
        assert m["s_autonomous-pipeline"]["status"] == "healthy"

    def test_colliding_rows_are_MERGED_not_replaced(self):
        # Meta-review cross-fix HIGH (run_a85e6641): a skill with BOTH a bare and an
        # s_-prefixed metrics row (6 live in prod, e.g. deep-research: bare 199 recent +
        # s_ 17 old) must MERGE, not last-write-win. Replace would drop the dominant recent
        # row → wrong/stale dot for a healthy skill. Verify the merge honors the UNION:
        # invocations summed, MAX(last_used) taken.
        recent = date.today().isoformat()
        old = (date.today() - timedelta(days=STALENESS_DAYS + 40)).isoformat()
        stats = [
            # dominant, recent bare row (like deep-research bare: 199 / recent)
            _stats(name="deep-research", invocation_count=199, success_rate=1.0, last_used=recent),
            # minority, OLD s_-prefixed row (like s_deep-research: 17 / old)
            _stats(name="s_deep-research", invocation_count=17, success_rate=1.0, last_used=old),
        ]
        m = build_health_map(stats, ["s_deep-research"])
        # MAX(last_used) = recent → NOT stale (the bug: replace picks the old row → stale)
        assert m["s_deep-research"]["status"] == "healthy", (
            "colliding rows must merge (MAX last_used = recent), not drop the dominant recent row"
        )
        assert m["s_deep-research"]["last_used"] == recent

    def test_merge_weights_success_rate_by_invocations(self):
        # Merged success_rate must be invocation-weighted, not a naive average, so a
        # low-success row is not masked/amplified by a small counterpart.
        recent = date.today().isoformat()
        stats = [
            _stats(name="x", invocation_count=90, success_rate=0.5, last_used=recent),   # 45 successes
            _stats(name="s_x", invocation_count=10, success_rate=1.0, last_used=recent),  # 10 successes
        ]
        m = build_health_map(stats, ["s_x"])
        # weighted = (45+10)/100 = 0.55 < 0.7 AND 100>=5 → low_success
        assert m["s_x"]["status"] == "low_success"
        assert abs(m["s_x"]["success_rate"] - 0.55) < 1e-9


class TestHealthEndpointFailSafe:
    def test_health_endpoint_returns_map(self, client):
        resp = client.get("/api/skills/health")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)
        for entry in data.values():
            assert entry["status"] in {"healthy", "low_success", "never_used", "stale"}

    def test_health_endpoint_fail_safe_on_store_error(self, client, monkeypatch):
        # AC7 / Gate-1: if the metrics store raises, the endpoint returns 200 + {} (never 500).
        import routers.skills as skills_router

        def _boom(*a, **k):
            raise RuntimeError("db locked")

        monkeypatch.setattr(skills_router, "_load_skill_health_stats", _boom)
        resp = client.get("/api/skills/health")
        assert resp.status_code == 200
        assert resp.json() == {}

    def test_health_endpoint_empty_metrics_returns_empty_no_grey_wall(self, client, monkeypatch):
        # meta-review MED: an entirely-empty metrics table (fresh install / hive DB) must
        # return {} → NO dots, not a wall of grey never_used dots.
        import routers.skills as skills_router
        monkeypatch.setattr(skills_router, "_load_skill_health_stats", lambda: [])
        resp = client.get("/api/skills/health")
        assert resp.status_code == 200
        assert resp.json() == {}, "empty metrics must yield {} (no dots), not all-never_used"

    def test_health_endpoint_omits_internal_for_non_owner(self, client, monkeypatch):
        # Gate-1 BLOCK-3 (leak guard): a non-owner (hive) session must NOT receive an internal
        # skill name as a health-map key.
        monkeypatch.setenv("SWARMAI_MODE", "hive")
        resp = client.get("/api/skills/health")
        assert resp.status_code == 200
        for folder_name in resp.json():
            assert not folder_name.startswith(("s_cmhk-", "s_ivt-", "s_internal-")), (
                f"{folder_name} internal skill leaked to non-owner via health map"
            )


class TestCrossBoundaryContractBinding:
    """Layer-4 cross-boundary E2E (cross_boundary=true, frontend↔backend contract).

    The seam: the backend status enum (SKILL_HEALTH_STATUSES) and the frontend dot map
    (HEALTH_DOT in CapabilitiesOverlay.tsx) MUST stay in lockstep — a backend status with
    no frontend dot (or vice-versa) is a silent contract break no unit test on either side
    catches. This binds the frontend table to the backend SSOT so a divergence goes RED.

    Mutation-verified non-vacuous: add/remove a value on either side → this test fails.
    (This is the "frontend table DERIVED-FROM/BOUND-TO backend SSOT" Layer-4 pattern.)
    """

    def _frontend_health_dot_keys(self) -> set[str]:
        import re
        from pathlib import Path

        tsx = (
            Path(__file__).resolve().parents[2]
            / "desktop/src/components/layout/CapabilitiesOverlay.tsx"
        ).read_text(encoding="utf-8")
        # Extract the HEALTH_DOT object body and pull its top-level keys.
        m = re.search(r"const HEALTH_DOT[^{]*\{(.*?)\n\};", tsx, re.DOTALL)
        assert m, "HEALTH_DOT map not found in CapabilitiesOverlay.tsx (contract moved?)"
        body = m.group(1)
        # keys look like `  healthy: { ... }` — first identifier before ':' on each entry line.
        return set(re.findall(r"^\s*([a-z_]+):\s*\{", body, re.MULTILINE))

    def test_frontend_dot_map_matches_backend_status_enum(self):
        from core.skill_health import SKILL_HEALTH_STATUSES

        backend = set(SKILL_HEALTH_STATUSES)
        frontend = self._frontend_health_dot_keys()
        assert frontend == backend, (
            "Skill-health status contract DIVERGED between backend and frontend.\n"
            f"  backend SKILL_HEALTH_STATUSES: {sorted(backend)}\n"
            f"  frontend HEALTH_DOT keys:      {sorted(frontend)}\n"
            f"  backend-only (no frontend dot): {sorted(backend - frontend)}\n"
            f"  frontend-only (no backend status): {sorted(frontend - backend)}"
        )
