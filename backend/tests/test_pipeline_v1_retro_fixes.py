"""Review findings whose proof could not be attached (design 2026-10-29-pipeline-retro-
fixes F3).

Questions, through real runs (``tests/pipeline_v1_rig.py``):
- F3a: an expert writes a finding's file without its leading directory
  (``core/mod.py`` for ``backend/core/mod.py``): is it recorded as the path from the
  repository root? A file present as given, or with no match, stays as given.
- F3b: a design finding whose fix landed in another file: does a diff_assertion close
  it when the address names that file in ``fix_files``, and does R2 see that file's
  diff? Without ``fix_files`` it is still refused; a ``fix_files`` path that is no
  file of the run's repo is refused by review-address with nothing written.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.pipeline_v1_rig import Rig
from tests.test_pipeline_v1_diff_assertion_by_file import DOC, MOD, _prove, _status

pytestmark = pytest.mark.timeout(300)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    r = Rig(tmp_path / "case", monkeypatch, profile="direct")
    yield r
    if r.run_id:
        assert r.v1("status").get("ok") is True, "the event log no longer folds"
    r.close()


def _row(kind: str, file: str, claim: str = "stale note", severity: str = "LOW") -> str:
    return "New finding: " + json.dumps({"severity": severity, "kind": kind, "file": file,
                                         "line": 1, "claim": claim, "fix": "rewrite it"})


def _r1(r: Rig, answer: str) -> list[dict]:
    r.CHANGE = {"backend/core/mod.py": MOD, "docs/guide.md": DOC}
    r.create()
    r.drive(until="review")
    started = r.v1("review-start")
    for i, handout in enumerate(started["handouts"]):
        r.spawn_reviewer(handout["prompt"], answer if i == 0 else "NO FINDINGS", f"r1-{i}")
    from core.run_protocol import review
    engine = r.engine()
    return review.ReviewOrchestrator(engine.store, engine.run_dir).findings()


def test_a_finding_path_without_its_leading_directory_is_recorded_from_the_root(rig):
    rows = _r1(rig, "\n".join([_row("docs", "core/mod.py", "short path"),
                               _row("docs", "backend/core/mod.py", "full path"),
                               _row("docs", "nothere.py", "no such file")]))
    occ = {e["payload"]["claim"]: e["payload"]["file"] for e in rig.events()
           if e["kind"] == "finding_occ"}
    assert occ == {"short path": "backend/core/mod.py", "full path": "backend/core/mod.py",
                   "no such file": "nothere.py"}, occ
    assert {row["file"] for row in rows} == {"backend/core/mod.py", "nothere.py"}


def _addressed(r: Rig, finding_id: str, extra: dict) -> dict:
    return r.v1("review-address", "--artifact", r.artifact("address", {"pass": 1, "items": [
        {"finding_id": finding_id, "hunk": "@@ the boundary", **extra}]}))


def test_a_fix_in_another_file_closes_with_fix_files(rig):
    (row,) = _r1(rig, _row("design", "backend/core/mod.py"))
    rig.edit({"docs/guide.md": DOC + "\nThe boundary is intended.\n"})
    out = _addressed(rig, row["canonical_id"], {"fix_files": ["docs/guide.md"]})
    assert out.get("ok") is True, out
    (handout,) = out["handouts"]
    assert "+The boundary is intended." in Path(handout["path"]).read_text(encoding="utf-8")
    rig.spawn_reviewer(handout["prompt"], json.dumps(
        {"finding_id": row["canonical_id"], "status": "RESOLVED", "note": "n"})
        + "\nNO FINDINGS", "r2-0")
    out = _prove(rig, row["canonical_id"])
    assert out.get("ok") is True, out
    assert _status(rig, row["canonical_id"]) == "closed_unproven"


def test_a_fix_in_another_file_without_fix_files_is_still_refused(rig):
    (row,) = _r1(rig, _row("design", "backend/core/mod.py"))
    rig.edit({"docs/guide.md": DOC + "\nThe boundary is intended.\n"})
    out = _addressed(rig, row["canonical_id"], {})
    (handout,) = out["handouts"]
    rig.spawn_reviewer(handout["prompt"], json.dumps(
        {"finding_id": row["canonical_id"], "status": "RESOLVED", "note": "n"})
        + "\nNO FINDINGS", "r2-0")
    out = _prove(rig, row["canonical_id"])
    assert out.get("ok") is False and "not changed since R1" in out.get("error", ""), out


def test_a_fix_files_path_outside_the_repo_is_refused_with_nothing_written(rig):
    (row,) = _r1(rig, _row("design", "backend/core/mod.py"))
    before = len(rig.events())
    out = _addressed(rig, row["canonical_id"], {"fix_files": ["docs/missing.md"]})
    assert out.get("ok") is False and "fix_files" in out.get("error", ""), out
    # a path that leaves the repository, though it names a file that exists
    outside = f"../{rig.worktree.name}/backend/core/mod.py"
    assert (rig.worktree / outside).exists()
    out = _addressed(rig, row["canonical_id"], {"fix_files": [outside]})
    assert out.get("ok") is False and "fix_files" in out.get("error", ""), out
    assert len(rig.events()) == before


def _r2(r: Rig, handout: dict, finding_id: str, status: str) -> None:
    r.spawn_reviewer(handout["prompt"], json.dumps(
        {"finding_id": finding_id, "status": status, "note": "n"}) + "\nNO FINDINGS",
        f"r2-{status}-{len(r.events())}")


def test_a_re_address_without_fix_files_shows_r2_the_findings_own_file(rig):
    """Review fix: R2 and the proof judge the same files. Pass 1 named the guide; pass 2
    names nothing, so R2 sees the finding's own file, as the proof will judge it. The
    finding is a HIGH: after R2 only an open HIGH gets the visit's one more pass (design
    2026-10-31-pipeline-review-close-and-landing-route §3 change 1)."""
    (row,) = _r1(rig, _row("design", "backend/core/mod.py", severity="HIGH"))
    fid = row["canonical_id"]
    rig.edit({"docs/guide.md": DOC + "\nThe boundary is intended.\n"})
    out = _addressed(rig, fid, {"fix_files": ["docs/guide.md"]})
    _r2(rig, out["handouts"][0], fid, "OPEN")
    rig.edit({"backend/core/mod.py": MOD.replace("the old note", "the new note")})
    out = rig.v1("review-address", "--artifact", rig.artifact("address2", {"pass": 2, "items": [
        {"finding_id": fid, "hunk": "@@ the note"}]}))
    assert out.get("ok") is True, out
    text = Path(out["handouts"][0]["path"]).read_text(encoding="utf-8")
    assert "+# the new note" in text and "+The boundary is intended." not in text


def _closed_on_the_guide(r: Rig) -> str:
    (row,) = _r1(r, _row("design", "backend/core/mod.py"))
    fid = row["canonical_id"]
    r.edit({"docs/guide.md": DOC + "\nThe boundary is intended.\n"})
    out = _addressed(r, fid, {"fix_files": ["docs/guide.md"]})
    _r2(r, out["handouts"][0], fid, "RESOLVED")
    assert _prove(r, fid).get("ok") is True
    return fid


def test_a_closure_on_fix_files_goes_stale_when_those_files_change(rig):
    fid = _closed_on_the_guide(rig)
    assert fid not in rig.engine().check_context().run["stale_closures"]
    rig.edit({"docs/guide.md": DOC})                    # the fix is gone; mod.py untouched
    assert fid in rig.engine().check_context().run["stale_closures"]


def test_a_directory_is_not_a_fix_file(rig):
    (row,) = _r1(rig, _row("design", "backend/core/mod.py"))
    out = _addressed(rig, row["canonical_id"], {"fix_files": ["backend/core"]})
    assert out.get("ok") is False and "fix_files" in out.get("error", ""), out
