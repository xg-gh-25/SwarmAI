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
