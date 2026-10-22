"""One rule for "does this change get a Gate-2 review?" (XG 2026-10-20).

``_small_change`` waives the review; ``_dispatch_experts`` picks the reviewers. They
disagreed on a test-only change: not small (it has no non-test source) yet nobody
was picked (0 source lines < 50), so the plan held only the meta-review and the
validator asked for a review no one had been sent. Every such direct run ended "not
ready to push".

What is tested:
- a test-only change is small under the same line rule (counted over its test lines)
  and so dispatches nobody (plan []: the existing review-presence waiver applies)
- a change that is NOT small always gets a reviewer on a strict profile: correctness,
  plus security on a sensitive path (the few-lines-on-an-auth-path case)
- relaxed profiles and documentation keep their rules
- the outcome shown to the user is in the requirement's language (a character count)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
for p in (BACKEND, BACKEND / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import artifact_cli as cli  # noqa: E402
import pipeline_validator as pv  # noqa: E402


def _scope(*files) -> dict:
    """files: (path, lines, is_test[, status])"""
    changed = [{"path": f[0], "status": f[3] if len(f) > 3 else "M", "added": f[1], "removed": 0,
                "is_test": f[2]} for f in files]
    src = sum(f[1] for f in files if not f[2])
    return {"changed_files": changed, "source_insertions": src, "source_deletions": 0,
            "diff_insertions": sum(f[1] for f in files), "diff_deletions": 0}


def _ids(scope, profile="direct"):
    return {e["id"] for e in cli._dispatch_experts(profile=profile, work_type=None, scope=scope, required=[])}


def test_a_small_test_only_change_is_waived_and_dispatches_nobody():
    s = _scope(("backend/tests/test_x.py", cli.SMALL_CHANGE_LINES, True))
    assert cli._small_change(s)
    assert _ids(s) == set()


def test_a_few_lines_on_a_security_path_get_security():
    s = _scope(("backend/routers/auth.py", 30, False))
    assert cli._small_change(s) is None
    assert _ids(s) == {"security"}


def test_relaxed_profiles_and_docs_keep_their_rules():
    assert _ids(_scope(("backend/tests/test_x.py", 42, True)), profile="trivial") == set()
    assert _ids(_scope(("docs/readme.md", 80, False))) == set()
    # a small source edit is still waived
    assert _ids(_scope(("backend/core/x.py", 4, False))) == set()


@pytest.mark.parametrize("added", [
    "+uvicorn.run(app, port=80)",
    "+uvicorn.run(app, host='::')",
    "+listen_port = 18321",
])
def test_port_or_listener_change_is_a_security_risk(monkeypatch, added):
    """E1 explicitly includes port/listener changes even on a generic file path."""
    monkeypatch.setattr(cli, "_git_lines", lambda *args, **kwargs: [
        "+++ b/backend/core/server.py", added,
    ])
    scope = _scope(("backend/core/server.py", 30, False))
    assert cli._security_risk(scope)
    assert _ids(scope) == {"security"}


# ── the outcome is shown to the user: their language ─────────────────────────

SKIPPED = {"title": "t", "quality": {"tests_pass": True},
           "adversarial_review": {"profile_tier": "skipped", "spawned": False}}


def test_a_chinese_requirement_needs_a_chinese_outcome():
    en = {"problem_before": "fixtures keyed by name", "result_now": "keyed by stage", "approach": []}
    zh = {"problem_before": "夹具用的是旧字段", "result_now": "现在用 stage", "approach": []}
    assert pv.outcome_language_errors(en, "修 main 上 3 个红测试")
    assert pv.outcome_language_errors(zh, "修 main 上 3 个红测试") == []
    assert pv.outcome_language_errors(en, "fix the 3 red tests") == []
    assert pv.outcome_language_errors(None, "修") == [] and pv.outcome_language_errors(en, None) == []


def test_stage_complete_refuses_an_outcome_in_the_wrong_language(monkeypatch):
    monkeypatch.setattr(cli, "_stage_artifact", lambda *a, **k: None)
    art = dict(SKIPPED, outcome={"problem_before": "it was red", "result_now": "it is green", "approach": []})
    errs, _ = cli._validate_stage_candidate(
        project="P", run_id="run_x", stage="deliver", profile="direct", artifact=art, record_in={},
        cand_rec={"status": "completed"},
        cand_run={"created_at": "2026-10-20T00:00:00+00:00", "stages": [], "requirement": "修红测试",
                  "dispatch_plan": {"deliver": []}})
    assert any("language of the requirement" in e for e in errs)
