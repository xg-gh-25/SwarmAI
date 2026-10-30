"""Tests for skill health status folding + the fail-safe /api/skills/health endpoint.

Covers run_a85e6641 (scannable at-a-glance health signal on the Capabilities panel):
- fold_status(SkillStats|None) -> qualitative enum {healthy, low_success, never_used, stale}
  across ALL branches incl. None, empty last_used, and the invocation_count>=5 floor.
- build_health_map marks list-skills ABSENT from metrics as never_used.
- GET /api/skills/health is run-mode-keyed (folds ONLY over the _visible_to_caller list —
  Gate-1 BLOCK-3: internal skill names must NOT leak to a non-owner via the map keys) and
  FAIL-SAFE (metrics store raising -> 200 with empty map, never 500 — Gate-1 WARN + AC7).

Success comes from the after-session review's corrections that name the skill, over the
last WINDOW_DAYS: success = 1 - corrections / invocations in the window (design
2026-10-30-cdf-agent-decides D12 — no keyword match on the user's next message). A floor of
MIN_INVOCATIONS_FOR_LOW_SUCCESS invocations in the window prevents low_success on no real
signal.
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

    def test_a_skill_never_corrected_is_healthy(self):
        assert fold_status(_stats(), recent_invocations=10, corrections=0) == "healthy"

    def test_corrections_over_the_floor_are_low_success(self):
        # >= floor invocations in the window AND 1 - 5/10 = 0.5 < 0.7 -> low_success.
        assert fold_status(_stats(), recent_invocations=10, corrections=5) == "low_success"

    def test_the_stores_lifetime_success_rate_is_not_read(self):
        # the old keyword-derived success_rate in the store no longer decides anything
        assert fold_status(_stats(success_rate=0.1), recent_invocations=10, corrections=0) == "healthy"

    def test_low_success_below_invocation_floor_is_not_low_success(self):
        # a rarely-run skill (below the floor) must NOT show low_success on no real signal
        assert fold_status(_stats(), recent_invocations=2, corrections=2) == "healthy"

    def test_more_corrections_than_runs_floor_at_zero(self):
        from core.skill_health import success_in_window
        assert success_in_window(5, 9) == 0.0
        assert success_in_window(0, 3) is None

    def test_old_last_used_is_stale(self):
        old = (date.today() - timedelta(days=STALENESS_DAYS + 5)).isoformat()
        assert fold_status(_stats(last_used=old)) == "stale"

    def test_low_success_takes_precedence_over_stale(self):
        # Ordering: never_used>low_success>stale>healthy.
        old = (date.today() - timedelta(days=STALENESS_DAYS + 5)).isoformat()
        assert fold_status(_stats(last_used=old), recent_invocations=10, corrections=6) == "low_success"

    def test_empty_last_used_does_not_crash(self):
        # MAX(invocation_date) can be "" (SkillStats last_used `or ""`): guarded per row.
        assert fold_status(_stats(last_used="")) == "healthy"

    def test_malformed_last_used_does_not_crash(self):
        assert fold_status(_stats(last_used="not-a-date")) == "healthy"

    def test_boundary_success_exactly_070_is_healthy(self):
        # <0.7 is exclusive — exactly 0.7 is NOT low_success.
        assert fold_status(_stats(), recent_invocations=10, corrections=3) == "healthy"

    def test_all_statuses_are_in_the_enum(self):
        from core.skill_health import SKILL_HEALTH_STATUSES
        for st, runs, fixes in ((None, 0, 0), (_stats(), 9, 0), (_stats(), 9, 9)):
            assert fold_status(st, recent_invocations=runs, corrections=fixes) in SKILL_HEALTH_STATUSES


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
        stats = [_stats(name="s_used", last_used="2026-08-01")]
        m = build_health_map(stats, ["s_used"], recent_invocations={"s_used": 10},
                             corrections={"s_used": 1})
        assert m["s_used"]["success_rate"] == pytest.approx(0.9)
        assert m["s_used"]["last_used"] == "2026-08-01"

    def test_not_invoked_in_the_window_has_no_success_rate(self):
        m = build_health_map([_stats(name="s_used")], ["s_used"])
        assert m["s_used"]["success_rate"] is None and m["s_used"]["status"] == "healthy"

    def test_never_used_detail_is_null(self):
        m = build_health_map([], ["s_never"])
        assert m["s_never"]["success_rate"] is None
        assert m["s_never"]["last_used"] is None

    def test_invocation_count_present_for_frequency_sort(self):
        # run_ff4adc88: the frontend Most-Used strip + within-group frequency sort need the
        # RAW invocation_count (drives ORDER + strip membership, NOT a visible count — R30#4).
        # It exists in the merged SkillStats but was omitted from the returned map.
        stats = [_stats(name="s_used", invocation_count=42, success_rate=0.9, last_used="2026-08-01")]
        m = build_health_map(stats, ["s_used"])
        assert m["s_used"]["invocation_count"] == 42

    def test_never_used_invocation_count_is_null(self):
        # A skill absent from metrics has no frequency → null (sorts to the bottom, not 0-vs-0 tie).
        m = build_health_map([], ["s_never"])
        assert m["s_never"]["invocation_count"] is None

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

    def test_window_counts_and_corrections_join_on_the_canonical_name(self):
        # the store records "pdf" and "s_pdf"; the review may name either: all are one skill
        recent = date.today().isoformat()
        stats = [_stats(name="pdf", last_used=recent), _stats(name="s_pdf", last_used=recent)]
        m = build_health_map(stats, ["s_pdf"], recent_invocations={"pdf": 6, "s_pdf": 4},
                             corrections={"s_pdf": 2, "pdf": 2})
        # 1 - 4/10 = 0.6 < 0.7 AND 10 >= 5 → low_success
        assert m["s_pdf"]["status"] == "low_success"
        assert m["s_pdf"]["success_rate"] == pytest.approx(0.6)


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
        from middleware.hive_auth import HiveAuthMiddleware
        monkeypatch.setattr(HiveAuthMiddleware, "_is_authorized", lambda self, scope: True)
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

    def test_invocation_count_field_name_binds_backend_to_frontend(self):
        # Layer-4 (run_ff4adc88): the Most-Used strip + within-group sort read the RAW
        # frequency by field name. The backend build_health_map EMITS the key
        # `invocation_count`; the frontend helper `freqOf` must READ that exact key. A
        # rename on EITHER side (backend emits `invocations`, frontend reads `.count`)
        # silently breaks sort/strip and no single-side unit test catches it — this binds
        # them. Mutation-verified: change the key on either side → this goes RED.
        import re
        from pathlib import Path

        # Backend: the exact key build_health_map writes into the returned dict.
        health_py = (
            Path(__file__).resolve().parents[1] / "core/skill_health.py"
        ).read_text(encoding="utf-8")
        assert '"invocation_count":' in health_py, (
            "build_health_map no longer emits the 'invocation_count' key — the frontend "
            "freqOf() reads it; a rename breaks the Most-Used strip + frequency sort."
        )

        # Frontend: freqOf must read that SAME key off the health entry.
        tsx = (
            Path(__file__).resolve().parents[2]
            / "desktop/src/components/layout/CapabilitiesOverlay.tsx"
        ).read_text(encoding="utf-8")
        assert re.search(r"health\[[^\]]+\]\?\.invocation_count", tsx), (
            "Frontend freqOf() no longer reads `.invocation_count` off the health map — "
            "the field name DIVERGED from the backend build_health_map emit (contract break)."
        )
