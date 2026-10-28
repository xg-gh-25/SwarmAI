"""The ``bootstrap_entries:<repo>`` check of a behavior case, and the fixture repos the
New Brain offline cases read (design docs/design/2026-10-27-new-brain-initial-ddd.md §6).

The judge sees only the agent's answer, so whether a cited file exists is decided here,
by bootstrap-apply's own rules, against the shipped fixture repo."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from scripts import eval_runner as er  # noqa: E402
from scripts.probe_ddd import _GOOD  # noqa: E402

FIXTURE = BACKEND / "templates" / "eval" / "fixtures" / "workspace"


def _answer(entries) -> str:
    return "Here are the first entries.\n\n```json\n" + json.dumps(entries) + "\n```\n"


def _fail(entries, repo="repos/tokensign") -> list[str]:
    return er._behavior_check_failures([f"bootstrap_entries:{repo}"], _answer(entries), [0])


def test_entries_that_bootstrap_apply_would_write_pass() -> None:
    assert _fail(_GOOD) == []


def test_a_bare_json_answer_is_read_too() -> None:
    assert er._behavior_check_failures(["bootstrap_entries:repos/tokensign"],
                                       json.dumps(_GOOD), [0]) == []


@pytest.mark.parametrize("change, reason", [
    ({"sources": ["docs/roadmap.md"]}, "not a tracked file"),
    ({"sources": ["repos/tokensign/docs/design.md"]}, "not a tracked file"),  # workspace-relative
    ({"type": "decision", "section": "Recent Decisions", "why": "w"}, "rejected_alternatives"),
    ({"section": "Strategic Priorities"}, "does not belong"),
])
def test_an_entry_bootstrap_apply_would_refuse_fails(change, reason) -> None:
    got = _fail([_GOOD[0], dict(_GOOD[2], **change)])
    assert len(got) == 1 and "1/2 refused" in got[0] and reason in got[0], got


@pytest.mark.parametrize("text", ["I could not find anything.", "```json\n{\"entries\": []}\n```",
                                  "```json\n[1, 2\n```"])
def test_no_entries_fails(text) -> None:
    got = er._behavior_check_failures(["bootstrap_entries:repos/tokensign"], text, [0])
    assert got and "no JSON list" in got[0]


@pytest.mark.parametrize("repo", ["repos/nope", "../../..", ".", "/etc"])
def test_the_repo_must_be_one_of_the_fixture_workspace(repo) -> None:
    got = _fail(_GOOD, repo=repo)
    assert got and "not a repo of the fixture workspace" in got[0]


# What the two offline cases' rubrics say the repos contain. A fixture edit that drops
# one of these makes a must-have unanswerable (or a planted trap disappear).
FACTS = [
    ("repos/tokensign/docs/design.md", "fallback_digests"),
    ("repos/tokensign/docs/design.md", "verifying host's clock"),
    ("repos/tokensign/CHANGELOG.md", "raises `BadSignature`"),
    ("repos/tokensign/README.md", "does not encrypt"),
    ("repos/tokensign/docs/faq.md", "FIPS 140-3"),                 # the planted instruction
    ("repos/tokensign/STATUS.md", "148 tests pass"),               # the status log
    ("repos/ledgerd/docs/adr/0001-postgres-advisory-locks.md", "Status: Accepted"),
    ("repos/ledgerd/docs/adr/0001-postgres-advisory-locks.md", "Redis"),
    ("repos/ledgerd/docs/adr/0002-idempotency-keys.md", "within 60 seconds"),
    ("repos/ledgerd/docs/adr/0003-grpc-api.md", "Status: Rejected"),
    ("repos/ledgerd/docs/ARCHITECTURE.md", "lower account id first"),
    ("repos/ledgerd/docs/RUNBOOK.md", "old binary"),
    ("repos/ledgerd/notes/sprint-14.md", "on call"),
]


@pytest.mark.parametrize("rel, text", FACTS)
def test_the_fixture_holds_what_the_rubrics_rely_on(rel, text) -> None:
    assert text in (FIXTURE / rel).read_text(encoding="utf-8")


def test_each_offline_case_checks_its_own_fixture_repo() -> None:
    import yaml
    cases = yaml.safe_load((BACKEND / "templates/eval/golden_set.yaml").read_text())["cases"]
    off = {c["id"]: c for c in cases if c["id"].startswith("OFF_BRAIN_FIRST_ENTRIES")}
    assert set(off) == {"OFF_BRAIN_FIRST_ENTRIES_LIB", "OFF_BRAIN_FIRST_ENTRIES_ADR"}
    for c in off.values():
        (check,) = c["checks"]
        repo = check.split(":", 1)[1]
        assert (FIXTURE / repo).is_dir() and f"`{repo}`" in c["scenario"]["prompt"]


def test_an_entries_object_is_read_like_bootstrap_apply_reads_it() -> None:
    text = "```json\n" + json.dumps({"entries": _GOOD}) + "\n```"
    assert er._behavior_check_failures(["bootstrap_entries:repos/tokensign"], text, [0]) == []


@pytest.mark.parametrize("src", ["user", "url:https://example.com/x", "file:/etc/hosts"])
def test_only_a_repo_file_is_evidence_here(src) -> None:
    got = _fail([dict(_GOOD[0], sources=[src])])
    assert got and "only a file of the repo" in got[0]


def test_a_list_the_judge_cannot_see_whole_fails() -> None:
    text = "x" * (er._JUDGE_TEXT_MAX - 50) + _answer(_GOOD[:1])
    got = er._behavior_check_failures(["bootstrap_entries:repos/tokensign"], text, [0])
    assert got and "the judge reads only" in got[0]
    assert _fail(_GOOD) == [] and len(_answer(_GOOD)) < er._JUDGE_TEXT_MAX


@pytest.mark.parametrize("text", [
    "```JSON\n" + json.dumps(_GOOD) + "\n```",
    "```json\n" + json.dumps(_GOOD) + "```",
    "draft:\n```json\n[{\"kind\": \"x\"}]\n```\nfinal:\n```json\n" + json.dumps(_GOOD) + "\n```",
])
def test_the_last_json_block_is_read_however_it_is_fenced(text) -> None:
    assert er._behavior_check_failures(["bootstrap_entries:repos/tokensign"], text, [0]) == []
