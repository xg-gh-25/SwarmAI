"""scripts.expert_verdicts: a native review's checked verdicts are kept with their provenance.

Each test answers one question about the record a later replay of the experts reads:
does it carry the change and the prompt version, is a malformed or evidence-less verdict
kept out, and does ``stats`` count what was checked.
"""
from __future__ import annotations

import hashlib
import io
import json
import subprocess
from pathlib import Path

import pytest

from scripts import expert_verdicts as ev


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "t")
    (r / "a.py").write_text("x = 1\n")
    _git(r, "add", "a.py")
    _git(r, "commit", "-q", "-m", "init")
    return r


@pytest.fixture()
def prompt(tmp_path: Path) -> Path:
    p = tmp_path / "correctness.md"
    p.write_text("You are the correctness expert.\n")
    return p


def _doc(repo: Path, prompt: Path, findings: list[dict]) -> dict:
    return {"repo": str(repo), "goal": "g", "model": "m",
            "experts": [{"expert": "correctness", "prompt_file": str(prompt), "findings": findings}]}


REAL = {"severity": "HIGH", "file": "a.py", "line": 1, "claim": "breaks", "verdict": "real",
        "evidence": "pytest tests/test_a.py"}


def test_record_keeps_base_prompt_version_and_the_whole_change(repo, prompt):
    (repo / "a.py").write_text("x = 2\n")
    (repo / "new.py").write_text("y = 3\n")  # an uncommitted new file is part of the change
    rec = ev.record(_doc(repo, prompt, [REAL]))

    stored = ev.read_reviews()
    assert [r["review_id"] for r in stored] == [rec["review_id"]]
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    assert stored[0]["base"] == head
    assert stored[0]["experts"][0]["prompt_sha"] == hashlib.sha256(prompt.read_bytes()).hexdigest()
    patch = (ev.store_dir() / "patches" / f"{rec['patch_sha']}.patch").read_text()
    assert "+x = 2" in patch and "new.py" in patch and "+y = 3" in patch


def test_an_invalid_verdict_writes_nothing(repo, prompt):
    with pytest.raises(ev.ReviewInvalid, match="verdict"):
        ev.record(_doc(repo, prompt, [{**REAL, "verdict": "maybe"}]))
    assert ev.read_reviews() == []


def test_a_checked_verdict_needs_evidence_an_unverified_one_does_not(repo, prompt):
    with pytest.raises(ev.ReviewInvalid, match="evidence"):
        ev.record(_doc(repo, prompt, [{**REAL, "verdict": "false_positive", "evidence": " "}]))
    ev.record(_doc(repo, prompt, [{**REAL, "verdict": "unverified", "evidence": ""}]))
    assert ev.read_reviews()[0]["experts"][0]["findings"][0]["verdict"] == "unverified"


def test_stats_counts_checked_findings_and_an_expert_that_found_nothing(repo, prompt):
    ev.record(_doc(repo, prompt, [REAL, {**REAL, "verdict": "false_positive", "evidence": "a.py:1"},
                                  {**REAL, "verdict": "unverified", "evidence": ""}]))
    ev.record(_doc(repo, prompt, []))
    [row] = ev.stats(ev.read_reviews())
    assert (row["reviews"], row["findings"], row["real"], row["false_positive"],
            row["unverified"]) == (2, 3, 1, 1, 1)
    assert row["real_share"] == 0.5


def test_a_directory_that_is_not_a_git_checkout_is_still_recorded(tmp_path, prompt):
    plain = tmp_path / "plain"
    plain.mkdir()
    rec = ev.record(_doc(plain, prompt, [REAL]))
    assert rec["base"] is None and rec["patch_sha"] is None


def test_cli_record_from_stdin_and_refusal_exit_code(repo, prompt, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_doc(repo, prompt, [REAL]))))
    assert ev.main(["record", "--file", "-"]) == 0
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"repo": str(repo), "experts": []})))
    assert ev.main(["record", "--file", "-"]) == 2
    assert "not recorded" in capsys.readouterr().err
    assert len(ev.read_reviews()) == 1


def test_a_snapshot_keeps_the_code_the_experts_saw_not_the_fixed_code(repo, prompt):
    (repo / "a.py").write_text("x = 'reviewed'\n")
    snap = ev.snapshot(str(repo))
    (repo / "a.py").write_text("x = 'fixed'\n")  # the finding is fixed before it is recorded
    rec = ev.record({"snapshot": snap["snapshot"], "experts": [
        {"expert": "correctness", "prompt_file": str(prompt), "findings": [REAL]}]})
    assert rec["patch_at"] == "review" and rec["patch_sha"] == snap["patch_sha"]
    patch = (ev.store_dir() / "patches" / f"{rec['patch_sha']}.patch").read_text()
    assert "reviewed" in patch and "fixed" not in patch
    without = ev.record(_doc(repo, prompt, [REAL]))
    assert without["patch_at"] == "record"


def test_the_patch_is_plain_git_format_whatever_the_users_diff_settings(repo, prompt):
    _git(repo, "config", "color.ui", "always")
    _git(repo, "config", "diff.noprefix", "true")
    (repo / "a.py").write_text("x = 2\n")
    rec = ev.record(_doc(repo, prompt, [REAL]))
    patch = (ev.store_dir() / "patches" / f"{rec['patch_sha']}.patch").read_bytes()
    assert b"\x1b[" not in patch and b"diff --git a/a.py b/a.py" in patch


def test_a_subdirectory_is_recorded_from_the_checkout_top_level(repo, prompt):
    (repo / "sub").mkdir()
    (repo / "sub" / "n.py").write_text("n = 1\n")
    (repo / "a.py").write_text("x = 2\n")
    rec = ev.record(_doc(repo / "sub", prompt, [REAL]))
    assert rec["repo"] == str(repo.resolve())
    patch = (ev.store_dir() / "patches" / f"{rec['patch_sha']}.patch").read_text()
    assert "b/sub/n.py" in patch and "b/a.py" in patch


def test_a_checkout_with_no_commit_yet_is_diffed_against_the_empty_tree(tmp_path, prompt):
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    _git(fresh, "init", "-q")
    (fresh / "f.py").write_text("f = 1\n")
    rec = ev.record(_doc(fresh, prompt, [REAL]))
    assert rec["base"] is None
    assert "+f = 1" in (ev.store_dir() / "patches" / f"{rec['patch_sha']}.patch").read_text()


def test_a_base_that_is_not_a_commit_is_refused(repo, prompt):
    with pytest.raises(ev.ReviewInvalid, match="not a commit"):
        ev.record({**_doc(repo, prompt, [REAL]), "base": "no-such-ref"})


def test_git_missing_is_a_refusal_not_a_crash(repo, prompt, monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("git")
    monkeypatch.setattr(ev.subprocess, "run", boom)
    with pytest.raises(ev.ReviewInvalid, match="git"):
        ev.record(_doc(repo, prompt, [REAL]))


def test_untracked_files_past_the_cap_are_named_not_stored(repo, prompt, monkeypatch):
    monkeypatch.setattr(ev, "_MAX_UNTRACKED_FILES", 1)
    (repo / "b.py").write_text("b = 1\n")
    (repo / "c.py").write_text("c = 1\n")
    rec = ev.record(_doc(repo, prompt, [REAL]))
    assert rec["skipped"] == ["c.py"]


def test_med_is_read_as_medium(repo, prompt):
    rec = ev.record(_doc(repo, prompt, [{**REAL, "severity": "MED"}]))
    assert rec["experts"][0]["findings"][0]["severity"] == "MEDIUM"


def test_a_damaged_store_line_does_not_stop_the_reader(repo, prompt):
    ev.record(_doc(repo, prompt, [REAL]))
    path = ev.store_dir() / "verdicts.jsonl"
    with open(path, "ab") as fh:
        fh.write(b"\xff\xfe not json\n")
        fh.write(json.dumps({"experts": [{"expert": "x", "prompt_sha": "p", "findings": 5}]}).encode() + b"\n")
    rows = {r["expert"]: r for r in ev.stats(ev.read_reviews())}
    assert rows["correctness"]["real"] == 1 and rows["x"]["findings"] == 0


def test_a_damaged_snapshot_is_a_refusal_not_a_crash(repo, prompt):
    snap = ev.snapshot(str(repo))
    (ev.store_dir() / "snapshots" / f"{snap['snapshot']}.json").write_text("[]")
    with pytest.raises(ev.ReviewInvalid, match="damaged"):
        ev.record({"snapshot": snap["snapshot"], "experts": [
            {"expert": "correctness", "prompt_file": str(prompt), "findings": []}]})
