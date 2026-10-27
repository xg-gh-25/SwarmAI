"""Pipeline v1 real end-to-end tests: every code profile lands; research; BUILD exhausted; the direct profile.

Split from test_pipeline_v1_real_e2e.py (design 2026-10-27-pipeline-hygiene §2);
shared fixture and helpers: tests/pipeline_v1_e2e_common.py. Invariant checked
after every case: the run's event log folds (``status`` succeeds).
"""
from __future__ import annotations

import pytest

from tests.pipeline_v1_rig import Rig, documents
from tests.pipeline_v1_e2e_common import (  # noqa: F401  (rig is a fixture)
    CODE_PROFILES, _decision, _landed_clean, _main, _not_landed_tests_red, _prepare,
    _reason, _request, rig)

pytestmark = pytest.mark.timeout(300)


# ── happy paths ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("profile", [
    pytest.param(p) for p in CODE_PROFILES])
def test_every_code_profile_lands(rig, profile):
    r = rig(profile)
    r.create()
    base = _main(r)
    state = r.drive()
    _landed_clean(state)
    assert _main(r) != base
    assert (r.repo / "backend/core/mod.py").read_text().endswith("return b + a\n")
    assert (r.repo / f".aidlc/deliverables/{r.run_id}/REPORT.md").is_file()


def test_research_completes_without_code(rig):
    r = rig("research", with_repo=False)
    r.create()
    state = r.drive()
    assert state["status"] == "completed" and state["landing"] == "no_code", state


# ── BUILD fails three times: the not-landed path, nothing reaches main ──────


def test_checks_exhausted_ends_not_landed_and_never_commits(rig):
    r = rig("direct")
    r.create()
    base = _main(r)
    r.drive(until="build")
    r.edit(Rig.CHANGE)
    for _ in range(3):
        r.v1("stage-complete")          # no report: BX-3 fails each attempt
    state = r.drive()
    assert state["status"] == "completed", state
    assert state["landing"] == "not_landed" and _reason(state) == "checks_exhausted", state
    assert _main(r) == base, "an exhausted run must not land"
    assert "render" in r.kinds(), "the not-landed REPORT is rendered by the engine"


# ── N1: direct has no PLAN, no documents, no exit criteria ──────────────────

def test_direct_starts_at_build_and_needs_the_users_words(rig):
    r = rig("direct")
    refused = r.v1("create", "--project", "P", "--profile", "direct",
                   "--repo", f"repo={r.repo}")
    assert refused.get("ok") is False and "--requirement" in refused.get("error", ""), refused
    out = r.create()
    assert "plan" not in out["state"]["stages"] and out["state"]["stage"] == "build"



def test_a_doc_only_direct_run_lands_without_any_document(rig):
    from tests.pipeline_v1_rig import _git
    r = rig("direct")
    r.create(requirement="Fix the README typo.")
    base = _main(r)
    r.edit({"README.md": "# readme\n\nfixed\n"})
    nothing = {"reports": [{"repo": "repo", "commands": [], "tests_changed": [],
                            "rationale": "a README typo: nothing to test"}]}
    assert r.v1("stage-complete", "--artifact", r.artifact("doc", nothing)).get("ok") is True
    _landed_clean(r.drive())
    assert _main(r) != base and "fixed" in (r.repo / "README.md").read_text()
    report = (r.repo / f".aidlc/deliverables/{r.run_id}/REPORT.md").read_text()
    assert "Fix the README typo." in report
    assert _git(r.repo, "log", "-1", "--format=%B").rstrip().endswith(
        "Co-Authored-By: Swarm <swarm@swarmai.dev>")


def test_a_direct_code_change_needs_a_build_report_on_the_current_tree(rig):
    r = rig("direct")
    r.create()
    r.edit(Rig.CHANGE)
    out = r.v1("stage-complete")
    assert out.get("ok") is False and "BX-3" in str(out.get("checks")), out   # no report
    assert r.report_and_complete().get("ok") is True and r.state()["stage"] == "review"


def test_a_direct_run_cannot_ask_and_its_log_still_folds(rig):
    r = rig("direct")
    r.create()
    before = len(r.events())
    out = r.v1("decision-request", "--artifact", _decision(r), "--document", "REQUIREMENTS")
    assert out.get("ok") is False and "trivial" in out.get("error", ""), out
    assert len(r.events()) == before and r.state()["stage"] == "build"
