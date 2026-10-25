"""S6-4b (design 2026-10-24-swarmai-usable-by-anyone.md §3.5.2): review guidance and
security dispatch come from the repository, not from SwarmAI or Python assumptions.

What is tested, through the real CLI rig (``tests/pipeline_v1_rig.py``):
- the product has no built-in path or API heuristic: changing a file named
  ``security_hooks.py`` in a repo with no ``.swarmai/review.md`` and no declaration
  dispatches no security review;
- security is dispatched by any one of: the repo's review.md ``security:`` line
  matching, the BUILD report declaring ``"risk": ["security"]``; the reason names it;
- review.md is read at the run's base commit: a run that edits its own review.md
  in its worktree is still reviewed by the base version;
- a pattern table in a non-Python repo's review.md is selected for the change and
  shown to correctness;
Static:
- no expert prompt, SECURITY-BASELINE.md or product skill cites RP numbers or
  REVIEW_PATTERNS.md; the pipeline skill ships no REVIEW_PATTERNS.md;
- SwarmAI's own ``.swarmai/review.md`` dispatches security for
  ``backend/core/security_hooks.py`` as the old built-in rule did.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.pipeline_v1_rig import Rig

pytestmark = pytest.mark.timeout(300)

BACKEND = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND.parent
BIG = "".join(f"def f{i}(x):\n    return x + {i}\n\n" for i in range(20))   # 60 lines

GUIDE = """# Review notes for this repo

Handlers must check the session token.

security: `match: glob:handlers/* kw:os/exec`

| # | Pattern | Trigger | What to verify | Example |
|---|---|---|---|---|
| GO1 | **unchecked error** | a call returning err `match: glob:*.go kw:err` | err is checked | ignored write error |
| GO2 | **context leak** | context.WithCancel `match: kw:withcancel` | cancel is deferred | goroutine leak |
"""


@pytest.fixture
def rig(tmp_path, monkeypatch):
    made: list[Rig] = []

    def make() -> Rig:
        r = Rig(tmp_path / f"case{len(made)}", monkeypatch, profile="direct")
        made.append(r)
        return r
    yield make
    for r in made:
        r.close()


def _review(r: Rig, files: dict[str, str], *, guide: str | None = None,
            worktree_guide: str | None = None, risk: list[str] | None = None) -> dict:
    if guide is not None:
        r.commit_on_main({".swarmai/review.md": guide}, "review guidance")
    r.create()
    r.drive(until="build")
    r.edit(files)
    if worktree_guide is not None:
        r.edit({".swarmai/review.md": worktree_guide})
    report = r.report()
    if risk is not None:
        report["reports"][0]["risk"] = risk
    out = r.v1("stage-complete", "--artifact", r.artifact("report", report))
    assert out.get("ok") is True, out
    started = r.v1("review-start")
    assert started.get("ok") is True, started
    started["prompts"] = {h["event"]["payload"]["expert_id"]: h["prompt"]
                          for h in started["handouts"]}
    started["ids"] = [row["id"] for row in started["reviewers"]]
    return started


def test_no_builtin_heuristic_a_security_named_file_alone_is_not_security(rig):
    started = _review(rig(), {"backend/core/security_hooks.py":
                              "import subprocess\n" + BIG})
    assert started["ids"] == ["correctness"], started["reviewers"]


def test_review_md_security_line_dispatches_security(rig):
    started = _review(rig(), {"handlers/login.go": "package h\n" + BIG}, guide=GUIDE)
    reasons = {row["id"]: row["reason"] for row in started["reviewers"]}
    assert "security" in reasons and ".swarmai/review.md" in reasons["security"], reasons


def test_a_declared_risk_in_the_build_report_dispatches_security(rig):
    started = _review(rig(), {"backend/core/mod.py": BIG}, risk=["security"])
    reasons = {row["id"]: row["reason"] for row in started["reviewers"]}
    assert "security" in reasons and "report" in reasons["security"], reasons


def test_an_unknown_declared_risk_is_refused(rig):
    r = rig()
    r.create()
    r.drive(until="build")
    r.edit({"backend/core/mod.py": BIG})
    report = r.report()
    report["reports"][0]["risk"] = ["performance"]
    out = r.v1("stage-complete", "--artifact", r.artifact("report", report))
    assert out.get("ok") is False and "risk" in str(out), out


def test_review_md_is_read_at_the_base_not_the_worktree(rig):
    started = _review(rig(), {"handlers/login.go": "package h\n" + BIG}, guide=GUIDE,
                      worktree_guide="# nothing to see\n")
    assert "security" in started["ids"], started["reviewers"]


def test_a_non_python_pattern_table_reaches_correctness(rig):
    started = _review(rig(), {"svc/run.go": "package svc\n" + "x, err := f()\n" + BIG},
                      guide=GUIDE)
    prompt = started["prompts"]["correctness"]
    assert "| GO1 |" in prompt and "| GO2 |" not in prompt
    assert ".swarmai/review.md" in prompt


_RP = re.compile(r"\bRP\d+\b|REVIEW_PATTERNS")


def test_no_rp_numbers_in_expert_prompts_or_the_security_baseline():
    files = sorted((BACKEND / "experts").rglob("*.md")) + [
        BACKEND / "context" / "SECURITY-BASELINE.md"]
    hits = [f"{p.relative_to(BACKEND)}:{n}" for p in files
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
            if _RP.search(line)]
    assert not hits, hits
    assert not (BACKEND / "skills" / "s_autonomous-pipeline" / "REVIEW_PATTERNS.md").exists()


def test_swarmai_review_md_still_dispatches_security_for_its_gates():
    from core.run_protocol import review_md
    guide = review_md.parse((REPO_ROOT / ".swarmai" / "review.md").read_text(encoding="utf-8"))
    assert guide.security is not None and guide.rows
    assert review_md.applies(guide.security, ["backend/core/security_hooks.py"], "")
    assert not review_md.applies(guide.security, ["backend/core/turn_view.py"], "")
