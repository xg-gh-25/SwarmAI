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
    # code facts the rubrics list, so a true entry taken from the code is not "unsupported"
    ("repos/tokensign/src/tokensign/signer.py", "hmac.compare_digest"),
    ("repos/tokensign/src/tokensign/timed.py", "age < 0"),
    ("repos/ledgerd/internal/store/locks.go", "ids[i] < ids[j]"),
    ("repos/ledgerd/internal/api/transfers.go", "StatusBadRequest"),
    ("repos/ledgerd/cmd/ledgerd/main.go", ":9180"),
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


# ── the survey each offline case starts from ─────────────────────────────────

def _offline_cases() -> dict:
    import yaml
    cases = yaml.safe_load((BACKEND / "templates/eval/golden_set.yaml").read_text())["cases"]
    return {c["id"]: c for c in cases if c["id"].startswith("OFF_BRAIN_FIRST_ENTRIES")}


def test_the_shipped_surveys_are_what_the_survey_code_writes_now() -> None:
    # Fails after a change to a fixture repo or to the survey code until
    # `py -m scripts.eval_fixture_surveys --write` regenerates them.
    from scripts import eval_fixture_surveys as efs
    assert efs.drift() == []


def test_a_fixture_edit_puts_the_survey_out_of_date(tmp_path) -> None:
    import shutil
    from scripts import eval_fixture_surveys as efs
    ws = tmp_path / "ws"
    shutil.copytree(FIXTURE, ws)
    assert efs.drift(ws) == []
    readme = ws / "repos/tokensign/README.md"
    readme.write_text(readme.read_text() + "\nOne more line.\n")
    assert efs.drift(ws) == ["Projects/Tokensign/.artifacts/bootstrap/survey.json"]


def test_each_offline_case_starts_from_its_own_survey() -> None:
    from core.brain_bootstrap import survey
    for c in _offline_cases().values():
        repo = c["checks"][0].split(":", 1)[1]
        prompt = c["scenario"]["prompt"]
        (brain,) = [b for b in ("Tokensign", "Ledgerd") if f"brain named {b} " in prompt]
        rel = f"Projects/{brain}/{survey.SURVEY_REL.as_posix()}"
        assert f"`{rel}`" in prompt and "Read survey.json" in c["expected_trajectory"]
        data = json.loads((FIXTURE / rel).read_text())
        assert data["schema"] == survey.SCHEMA and data["project"] == brain
        (r,) = data["repos"]
        assert r["root"] == repo and "errors" not in r
        assert any(d["tier"] == 1 and d.get("text") for d in r["docs"])


def test_no_fixture_repo_names_a_port_another_case_asks_about() -> None:
    # OFF_WRONG_PREMISE / OFF_RIGHT_PREMISE ask for the Demo port (7421) against a
    # wrong 8080; a fixture repo holding either number is a false lead for a Grep.
    for f in (FIXTURE / "repos").rglob("*"):
        if f.is_file():
            text = f.read_text(encoding="utf-8")
            assert "8080" not in text and "7421" not in text, f


def test_a_full_twelve_entry_answer_fits_the_judge_window() -> None:
    # The prompt allows 12 entries; with ordinary one-sentence fields that is past 4000
    # characters, the old window, and the whole list must still reach the judge.
    sentence = "One ordinary sentence of about a hundred characters that says what the entry is about here. "
    entries = [dict(e, title=f"{e['title']} ({i})", what=sentence * 3)
               for i, e in enumerate((_GOOD * 2)[:12])]
    answer = _answer(entries)
    assert 4000 < len(answer) < er._JUDGE_TEXT_MAX
    assert _fail(entries) == []
