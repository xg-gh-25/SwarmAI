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


def test_a_larger_test_only_change_gets_a_reviewer():
    s = _scope(("backend/tests/test_x.py", 42, True))
    assert cli._small_change(s) is None
    assert _ids(s) == {"correctness"}


def test_a_few_lines_on_a_security_path_get_security():
    s = _scope(("backend/routers/auth.py", 30, False))
    assert cli._small_change(s) is None
    assert _ids(s) == {"security"}


def test_relaxed_profiles_and_docs_keep_their_rules():
    assert _ids(_scope(("backend/tests/test_x.py", 42, True)), profile="trivial") == set()
    assert _ids(_scope(("docs/readme.md", 80, False))) == set()
    # a small source edit is still waived
    assert _ids(_scope(("backend/core/x.py", 4, False))) == set()


def test_not_small_always_means_someone_reviews_on_a_strict_profile():
    """The invariant the validator relies on, over a spread of change shapes."""
    shapes = [_scope(("backend/tests/test_x.py", n, True)) for n in (0, 21, 300)]
    shapes += [_scope(("backend/core/x.py", n, False)) for n in (21, 49, 50, 300)]
    shapes += [_scope(("backend/routers/chat.py", n, False)) for n in (1, 30)]
    shapes += [_scope(("backend/core/new.py", 5, False, "A"))]
    for s in shapes:
        if cli._small_change(s) is None and not cli._is_docs_only(s):
            assert _ids(s), s


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
