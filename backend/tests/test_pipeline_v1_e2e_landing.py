"""Pipeline v1 real end-to-end tests: a person's run-land: one-use, tree-bound approvals; crash recovery.

Split from test_pipeline_v1_real_e2e.py (design 2026-10-27-pipeline-hygiene §2);
shared fixture and helpers: tests/pipeline_v1_e2e_common.py. Invariant checked
after every case: the run's event log folds (``status`` succeeds).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.pipeline_v1_rig import Rig
from tests.pipeline_v1_e2e_common import (  # noqa: F401  (rig is a fixture)
    CODE_PROFILES, _decision, _landed_clean, _main, _not_landed_tests_red, _prepare,
    _reason, _request, rig)

pytestmark = pytest.mark.timeout(300)


# ── a person's run-land ─────────────────────────────────────────────────────

def test_expired_approval_is_refused_and_a_fresh_one_lands(rig, monkeypatch):
    from core.run_protocol import landing
    r = rig("trivial")
    _not_landed_tests_red(r)
    base = _main(r)
    prepared = _prepare(r)
    # the hook records the person's approval in this process: make it already expired
    monkeypatch.setattr(landing, "AUTH_TTL_SECONDS", -1.0)
    refused = _request(r, prepared)
    assert refused.get("ok") is not True and _main(r) == base, refused
    monkeypatch.setattr(landing, "AUTH_TTL_SECONDS", 30 * 60)
    assert r.v1("status").get("ok") is True, "the refused attempt must be closed"
    landed = _request(r, _prepare(r))
    assert landed.get("ok") is True and _main(r) != base, landed


def test_code_changed_after_prepare_cannot_land_even_with_edited_json(rig):
    import json
    from tests.pipeline_v1_rig import _git
    r = rig("trivial")
    _not_landed_tests_red(r)
    base = _main(r)
    prepared = _prepare(r)
    r.edit({"backend/core/mod.py": "def add(a, b):\n    return 42\n"})   # untested tree
    path = Path(prepared["path"])
    value = json.loads(path.read_text())
    from core.run_protocol import git_tree, tree
    repos = {"repo": str(r.worktree)}
    report_sha = value["report_sha"]
    report = (r.data / "state" / "pipeline_agent_audit" / "runs" / r.run_id / "blobs"
              / report_sha).read_bytes()
    code_trees = tree.build_code_trees(repos, r.run_id, git_tree.write_tree,
                                       git_tree.list_reserved)
    forged = tree.build_delivery_trees(repos, r.run_id, code_trees, report, report_sha,
                                       git_tree.write_tree, git_tree.list_reserved)
    value["bindings"] = {name: {key: row[key] for key in ("code_tree", "delivery_tree",
                                                         "report_sha")}
                         for name, row in forged.items()}       # the JSON now "matches"
    path.write_text(json.dumps(value))
    out = _request(r, prepared)
    assert out.get("ok") is not True and _main(r) == base, out


# ── C1 / C6: execution reads the signed land_request event, once ────────────

def test_landing_reads_its_request_from_the_event_not_the_json(rig):
    r = rig("trivial")
    _not_landed_tests_red(r)
    base = _main(r)
    prepared = _prepare(r)
    Path(prepared["path"]).unlink()                 # the JSON is a display copy only
    landed = _request(r, prepared)
    assert landed.get("ok") is True and _main(r) != base, landed


def test_the_approval_card_shows_what_lands(rig):
    from core import security_hooks
    r = rig("trivial")
    _not_landed_tests_red(r)
    prepared = _prepare(r)
    request = prepared["prepared"]["request"]
    command = f"\"$SWARM_BACKEND_BIN\" pipeline-v1 run-land --project P --run-id {r.run_id} " \
              f"--request {request['request_id']} --message m"
    card = security_hooks.v1_land_card(command)
    row = request["bindings"]["repos"]["repo"]
    assert r.run_id in card and "skip tests: yes" in card, card
    assert row["code_tree"][:12] in card and row["delivery_tree"][:12] in card, card
    assert "accepted by XG" in card, card


def test_an_approval_is_consumed_once_and_a_second_try_is_integrity(rig):
    from core.run_protocol import landing
    r = rig("trivial")
    _not_landed_tests_red(r)
    prepared = _prepare(r)
    assert _request(r, prepared).get("ok") is True
    engine = r.engine()
    request_id = prepared["prepared"]["request"]["request_id"]
    with pytest.raises(landing.LandingError, match="already consumed"):
        landing.consume_authorization(
            engine.store, request_id, session_id=engine.owner_session(),
            bindings=prepared["prepared"]["request"]["bindings"]["repos"],
            at="2026-10-23T00:00:00Z", now=__import__("time").time())
    assert any(e["kind"] == "integrity" and e["payload"]["conflict_type"] == "auth_consumed_twice"
               for e in r.events())


# ── C5: manual landing without a final, and its errors are JSON ─────────────

def test_skip_tests_lands_a_run_that_never_ran_tests(rig):
    r = rig("direct")
    r.create()
    r.edit(Rig.CHANGE)
    assert r.v1("abandon", "--reason", "XG takes it from here").get("ok") is True
    base = _main(r)
    landed = _request(r, _prepare(r))
    assert landed.get("ok") is True and _main(r) != base, landed


def test_a_guard_failure_on_prepare_is_json_not_a_traceback(rig):
    from tests.pipeline_v1_rig import _git
    r = rig("direct")
    r.create()
    r.edit(Rig.CHANGE)
    assert r.v1("abandon", "--reason", "stop").get("ok") is True
    (r.repo / "other.txt").write_text("moved\n")
    _git(r.repo, "add", "other.txt"); _git(r.repo, "commit", "-qm", "main moves")
    out = r.v1("run-land", "--prepare", "--skip-tests", "--note", "n")
    assert out.get("ok") is False and "LG-4" in out.get("error", ""), out



def test_a_tested_prepare_on_an_untested_tree_asks_for_a_test_report(rig):
    r = rig("direct")
    r.create()
    r.edit(Rig.CHANGE)
    assert r.v1("abandon", "--reason", "stop").get("ok") is True
    out = r.v1("run-land", "--prepare", "--note", "n")
    assert out.get("ok") is False and "report-tests" in out.get("error", ""), out


def test_a_manual_landing_resumes_after_a_crash_past_auth_consumed(rig):
    """Re-audit ca91762f HIGH: auth_consumed recorded, refs moved, then the process
    died before the land event. The retry of the same request must finish the
    landing (append the missing land event), not stop at "already consumed"."""
    r = rig("trivial")
    _not_landed_tests_red(r)
    prepared = _prepare(r)
    assert _request(r, prepared).get("ok") is True
    landed_main = _main(r)
    log = r.data / "state" / "pipeline_agent_audit" / "runs" / r.run_id / "events.jsonl"
    lines = log.read_text().splitlines(keepends=True)
    kinds = [json.loads(x)["kind"] for x in lines]
    cut = max(i for i, k in enumerate(kinds) if k == "land")
    assert "auth_consumed" in kinds[:cut]
    log.write_text("".join(lines[:cut]))           # the crash: no land event, refs moved
    again = _request(r, prepared)
    assert again.get("ok") is True, again
    assert _main(r) == landed_main                  # nothing landed twice
    assert [e["kind"] for e in r.events()].count("land") == 1
