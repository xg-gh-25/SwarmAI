"""``diff_assertion`` judges the file, not the line the expert reported (design 2026-10-26-
pipeline-review-closure-and-visibility §2.3 point 4, step 5).

Before: a docs / design / security_policy finding closed only when a hunk of base → now
touched the exact line the R1 expert reported; a fix a few lines away (or a line number
the expert got wrong) could never close, and the repo was guessed from which checkout
holds the path today (a deleted file resolved to none).

What is tested, through real runs (``tests/pipeline_v1_rig.py``):
- a fix away from the reported line closes once R2 judged it RESOLVED;
- the file changed after that R2 → refused, with the reason, nothing written;
- the file never changed since R1 → refused;
- a deleted file still resolves (the repo comes from the R1 handout's trees);
- the R2 input carries the file's diff from the R1 tree to now;
and, as a pure function, a path present in two repos resolves by the R1 handout.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.pipeline_v1_rig import Rig

pytestmark = pytest.mark.timeout(300)

FILLER = "".join(f"\n\ndef f{i}():\n    return {i}\n" for i in range(15))
MOD = "# the old note\ndef add(a, b):\n    return a + b\n" + FILLER
DOC = "# Guide\n\nStep one.\n"


@pytest.fixture
def rig(tmp_path, monkeypatch):
    r = Rig(tmp_path / "case", monkeypatch, profile="direct")
    yield r
    if r.run_id:
        assert r.v1("status").get("ok") is True, "the event log no longer folds"
    r.close()


def _finding(kind: str, file: str, line: int) -> str:
    return json.dumps({"severity": "LOW", "kind": kind, "file": file, "line": line,
                       "claim": "stale note", "fix": "rewrite it"})


def _at_r2(r: Rig, kind: str, file: str, line: int, fix: dict[str, str | None]) -> tuple[str, dict]:
    """R1 reports one finding; the fix is applied; review-address sends it to R2 and the
    expert answers RESOLVED. Returns the finding id and the R2 handout."""
    r.CHANGE = {"backend/core/mod.py": MOD, "docs/guide.md": DOC}
    r.create()
    r.drive(until="review")
    started = r.v1("review-start")
    for i, handout in enumerate(started["handouts"]):
        r.spawn_reviewer(handout["prompt"], _finding(kind, file, line) if i == 0
                         else "NO FINDINGS", f"r1-{i}")
    from core.run_protocol import review
    engine = r.engine()
    (row,) = review.ReviewOrchestrator(engine.store, engine.run_dir).findings()
    finding_id = row["canonical_id"]
    for path, text in fix.items():
        if text is None:
            (r.worktree / path).unlink()
        else:
            r.edit({path: text})
    out = r.v1("review-address", "--artifact", r.artifact("address", {"pass": 1, "items": [
        {"finding_id": finding_id, "hunk": "@@ the note"}]}))
    assert out.get("ok") is True, out
    (handout,) = out["handouts"]
    r.spawn_reviewer(handout["prompt"], json.dumps(
        {"finding_id": finding_id, "status": "RESOLVED", "note": "n"}) + "\nNO FINDINGS", "r2-0")
    return finding_id, handout


def _prove(r: Rig, finding_id: str) -> dict:
    return r.v1("review-proof", "--artifact", r.artifact("proof", {"pass": 1, "proofs": [
        {"finding_id": finding_id, "evidence_type": "diff_assertion"}]}))


def _status(r: Rig, finding_id: str) -> str:
    from core.run_protocol import review
    engine = r.engine()
    rows = review.ReviewOrchestrator(engine.store, engine.run_dir).findings()
    return next(row["status"] for row in rows if row["canonical_id"] == finding_id)


def test_a_fix_away_from_the_reported_line_closes(rig):
    # the expert said line 30; the stale note is line 1
    finding_id, _ = _at_r2(rig, "docs", "backend/core/mod.py", 30,
                           {"backend/core/mod.py": MOD.replace("the old note", "the new note")})
    out = _prove(rig, finding_id)
    assert out.get("ok") is True, out
    assert _status(rig, finding_id) == "closed_asserted"


def test_a_change_after_r2_is_refused_and_nothing_is_written(rig):
    finding_id, _ = _at_r2(rig, "docs", "backend/core/mod.py", 1,
                           {"backend/core/mod.py": MOD.replace("the old note", "the new note")})
    rig.edit({"backend/core/mod.py": MOD.replace("the old note", "a third note")})
    out = _prove(rig, finding_id)
    assert out.get("ok") is False, out
    assert "after the R2" in out.get("error", ""), out
    assert not [e for e in rig.events() if e["kind"] == "proof"]


def test_a_file_never_changed_since_r1_is_refused(rig):
    finding_id, _ = _at_r2(rig, "docs", "docs/guide.md", 3,
                           {"backend/core/mod.py": MOD.replace("the old note", "the new note")})
    out = _prove(rig, finding_id)
    assert out.get("ok") is False, out
    assert "not changed since R1" in out.get("error", ""), out
    assert _status(rig, finding_id) == "open"


def test_a_deleted_file_resolves_through_the_r1_trees(rig):
    finding_id, _ = _at_r2(rig, "security_policy", "docs/guide.md", 3,
                           {"docs/guide.md": None})
    out = _prove(rig, finding_id)
    assert out.get("ok") is True, out
    assert _status(rig, finding_id) == "closed_unproven"


def test_the_r2_input_carries_the_file_diff_from_r1(rig):
    _finding_id, handout = _at_r2(rig, "docs", "backend/core/mod.py", 1,
                                  {"backend/core/mod.py": MOD.replace("the old note",
                                                                      "the new note")})
    full = Path(handout["path"]).read_text(encoding="utf-8")
    assert "-# the old note" in full and "+# the new note" in full


def test_a_path_in_two_repos_resolves_by_the_r1_handout():
    from core.run_protocol.engine import EngineError, _repo_of_finding
    present = {("a", "t1"), ("b", "t2")}           # (repo, tree) holding the file

    def has(repo, tree, _file):
        return (repo, tree) in present

    # R1 saw only repo b's tree hold the file: b, though a holds the path now too
    assert _repo_of_finding("x.md", {"a": "t0", "b": "t2"}, {"a": "t1", "b": "t2"}, has) == "b"
    with pytest.raises(EngineError):
        _repo_of_finding("x.md", {"a": "t1", "b": "t2"}, {"a": "t1", "b": "t2"}, has)


def test_a_docs_fix_in_a_file_that_also_changed_code_closes_as_reviewed_only(rig):
    finding_id, _ = _at_r2(rig, "docs", "backend/core/mod.py", 1, {
        "backend/core/mod.py": MOD.replace("the old note", "the new note").replace(
            "a + b", "b + a")})
    out = _prove(rig, finding_id)
    assert out.get("ok") is True, out
    assert _status(rig, finding_id) == "closed_unproven"     # LOW: counts as closed
    from core.run_protocol import review
    engine = rig.engine()
    row = next(r for r in review.ReviewOrchestrator(engine.store, engine.run_dir).findings()
               if r["canonical_id"] == finding_id)
    assert row["evidence_event_id"], row                       # it points at its proof
    assert finding_id not in engine.check_context().run["stale_closures"]
