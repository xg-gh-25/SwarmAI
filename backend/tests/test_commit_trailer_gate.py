"""Tests for the commit-trailer identity gate (scripts/check_commit_trailers.py).

WHY THIS FILE EXISTS
--------------------
The gate exists because ``.git/hooks/prepare-commit-msg`` — which rewrites an SDK
``Co-Authored-By: Claude`` trailer into the project's ``Swarm`` identity — NEVER RUNS:
``core.hooksPath`` points at the corporate git-defender hook set, and a hooksPath
override REPLACES ``.git/hooks`` rather than merging with it. So the trailer rule was
unenforced (22 of the last 80 commits carry none).

Enforcement moved to CI. Which means the gate itself now needs the treatment this repo
applies to every guard: a test that ACTUALLY ENTERS it (INV-5). Six guards in this
codebase shipped without executing (``_get_session_router`` NameError, ``self._pid``,
the inert reconciliation endpoint, the eslint rule that was in no CI step...), so a
gate is not trusted here until something proves it fires.

Covered:
  - classify() on each real shape (ok / missing / wrong-identity), including the
    Claude/Anthropic identity the project rule names explicitly;
  - the CI wiring — that ci.yml RUNS the script (a gate nobody invokes is prose), and
    that the job checks out full history (a depth-1 clone makes the script SKIP, which
    would look green while enforcing nothing — the subtlest way for this to go inert).
"""
from __future__ import annotations

import importlib.util
import pathlib
import re

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "scripts" / "check_commit_trailers.py"


def _load():
    spec = importlib.util.spec_from_file_location("cct", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def cct():
    assert _SCRIPT.is_file(), f"gate script missing: {_SCRIPT}"
    return _load()


def test_classify_accepts_the_project_identity(cct):
    body = "Some body text\n\nCo-Authored-By: Swarm <swarm@swarmai.dev>\n"
    assert cct.classify(body) == "ok"


def test_classify_flags_a_missing_trailer(cct):
    assert cct.classify("Just a body, no trailer at all\n") == "missing"
    assert cct.classify("") == "missing"


def test_classify_flags_the_forbidden_claude_identity(cct):
    """The project rule: 'Never use Claude/Anthropic identity in commit trailers'."""
    for line in (
        "Co-Authored-By: Claude <noreply@anthropic.com>",
        "Co-authored-by: Claude Code <noreply@anthropic.com>",
    ):
        assert cct.classify(f"body\n\n{line}\n") == "wrong-identity", line


def test_classify_flags_any_other_co_author_identity(cct):
    """A trailer that is present but is not the project identity is still a violation —
    otherwise 'add any co-author' would satisfy an identity rule."""
    body = "body\n\nCo-Authored-By: Someone Else <someone@example.com>\n"
    assert cct.classify(body) == "wrong-identity"


def test_required_trailer_matches_the_documented_rule(cct):
    """AGENTS.md pins the exact string; a typo here would silently accept nothing."""
    assert cct.REQUIRED_TRAILER == "Co-Authored-By: Swarm <swarm@swarmai.dev>"


def test_gate_is_wired_into_ci_and_actually_invoked():
    """A gate that no CI step runs is prose (the eslint-rule failure mode, 4 rounds)."""
    ci = (_REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "run: python3 scripts/check_commit_trailers.py" in ci, (
        "ci.yml no longer RUNS scripts/check_commit_trailers.py — the trailer gate is "
        "inert. Matching the `run:` line specifically (not just the filename) so a "
        "mere comment mentioning it cannot satisfy this assertion."
    )


def test_ci_job_fetches_full_history_or_the_gate_silently_skips():
    """The script SKIPs when its cutoff SHA is absent, which a depth-1 checkout
    guarantees. Green-but-enforcing-nothing is the worst outcome, so pin fetch-depth."""
    ci = (_REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    job = ci[ci.index("  version-check:"):]
    job = job[: job.index("\n  # ")] if "\n  # " in job else job
    # Match the real YAML KEY, not the bare token. Mutation-testing caught the naive
    # `"fetch-depth: 0" in job` version being BLIND: the step's own explanatory COMMENT
    # contains the literal string, so deleting the actual `with: fetch-depth: 0` left
    # the assertion green. Second occurrence of that exact class in one session (the
    # other was `ci.includes('lint:contract')` matching its own comment) — an assertion
    # must not be satisfiable by prose describing its subject.
    key = re.compile(r"^[ \t]*fetch-depth:[ \t]*0[ \t]*$", re.MULTILINE)
    assert key.search(job), (
        "the version-check job no longer checks out full history — "
        "check_commit_trailers.py will not find its cutoff SHA and will SKIP, "
        "reporting success while enforcing nothing"
    )
