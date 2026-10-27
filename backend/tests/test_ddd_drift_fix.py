"""DDD drift auto-fix (design docs/design/2026-10-27-ddd-drift-auto-fix.md r2): a fix is
applied only when its evidence settles the answer, the edit stays inside one plain line,
it is recorded and backed up before the doc changes, and one fix can be undone."""
import json
from pathlib import Path

import pytest

from core import ddd_drift_fix as df

PROJECT_MD = """# P — Context

## Current Focus

Next: add a behavioural benchmark once `s_review` exists, graded per dimension.

## Open Items

- [x] P5 (REVIEW) — the reviewer is ported into the `s_review` skill
- [ ] P6 VERIFY

## Lessons

- [pitfall] **Old lesson** — the gate was deferred at the time, tracked in PROJECT.
  <!-- ref:0 | last:2026-08-01 | decay:active -->

```
once `s_review` exists in a code block
```
"""


@pytest.fixture()
def ws(tmp_path):
    proj = tmp_path / "Projects" / "P"
    (proj / "2-understanding").mkdir(parents=True)
    (proj / "2-understanding" / "PROJECT.md").write_text(PROJECT_MD)
    (proj / "2-understanding" / "TECH.md").write_text("Design doc: `docs/old-design.md` for the engine.\n")
    (tmp_path / "Knowledge" / "Designs").mkdir(parents=True)
    (tmp_path / "Knowledge" / "Designs" / "old-design.md").write_text("> SUPERSEDED: see `docs/new-design.md`.\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "new-design.md").write_text("# new\n")
    return tmp_path, proj


def _fix(**over):
    fix = {"doc": "PROJECT.md",
           "old": "add a behavioural benchmark once `s_review` exists",
           "new": "add a behavioural benchmark now that `s_review` exists",
           "evidence": [{"kind": "done", "file": "PROJECT.md",
                         "quote": "- [x] P5 (REVIEW) — the reviewer is ported into the `s_review` skill"}]}
    fix.update(over)
    return fix


def _check(ws, fix, src=None):
    root, proj = ws
    return df.check_fix(fix, proj, root, src)


class TestCertain:
    def test_a_ticked_checklist_settles_it(self, ws):
        assert _check(ws, _fix())["ok"] is True

    def test_a_superseded_header_and_an_existing_path_settle_a_path_swap(self, ws):
        fix = {"doc": "TECH.md", "old": "Design doc: `docs/old-design.md`", "new": "Design doc: `docs/new-design.md`",
               "evidence": [{"kind": "superseded", "file": "Knowledge/Designs/old-design.md",
                             "quote": "> SUPERSEDED: see `docs/new-design.md`."},
                            {"kind": "exists", "file": "docs/new-design.md"}]}
        assert _check(ws, fix)["ok"] is True

    def test_code_evidence_counts_only_from_the_bound_repo_outside_tests(self, ws, tmp_path):
        repo = tmp_path / "repo"
        (repo / "src").mkdir(parents=True)
        (repo / "tests").mkdir()
        (repo / "src" / "a.py").write_text("ROUTES = 57\n")
        (repo / "tests" / "t.py").write_text("ROUTES = 57\n")
        root, proj = ws
        (proj / "2-understanding" / "TECH.md").write_text("The API has ROUTES = 42 handlers today.\n")
        fix = {"doc": "TECH.md", "old": "The API has ROUTES = 42 handlers", "new": "The API has ROUTES = 57 handlers",
               "evidence": [{"kind": "code", "file": "src/a.py", "quote": "ROUTES = 57"}]}
        assert df.check_fix(fix, proj, root, repo)["ok"] is True
        fix["evidence"][0]["file"] = "tests/t.py"
        assert df.check_fix(fix, proj, root, repo)["ok"] is False
        (repo / ".artifacts").mkdir()
        (repo / ".artifacts" / "run.py").write_text("ROUTES = 57\n")  # a run log, not the code
        fix["evidence"][0]["file"] = ".artifacts/run.py"
        assert df.check_fix(fix, proj, root, repo)["ok"] is False
        assert df.check_fix({**fix, "evidence": [{"kind": "code", "file": "src/a.py", "quote": "ROUTES = 57"}]},
                            proj, root, None)["ok"] is False


class TestNotCertain:
    @pytest.mark.parametrize("kind,why", [("doc", "another statement"), ("contradiction", "another statement")])
    def test_another_statement_is_not_evidence(self, ws, kind, why):
        fix = _fix(evidence=[{"kind": kind, "file": "PROJECT.md", "quote": "- [ ] P6 VERIFY"}])
        r = _check(ws, fix)
        assert r["ok"] is False and why in r["reason"]

    def test_done_evidence_must_be_a_ticked_line(self, ws):
        r = _check(ws, _fix(evidence=[{"kind": "done", "file": "PROJECT.md", "quote": "- [ ] P6 VERIFY"}]))
        assert r["ok"] is False and "ticked" in r["reason"]

    def test_superseded_evidence_must_say_so(self, ws):
        r = _check(ws, _fix(evidence=[{"kind": "superseded", "file": "PROJECT.md", "quote": "- [ ] P6 VERIFY"}]))
        assert r["ok"] is False and "superseded" in r["reason"]

    def test_the_quote_must_be_in_the_file(self, ws):
        r = _check(ws, _fix(evidence=[{"kind": "done", "file": "PROJECT.md", "quote": "- [x] something else entirely"}]))
        assert r["ok"] is False and "not in" in r["reason"]

    def test_an_added_number_must_come_from_the_evidence(self, ws):
        fix = _fix(new="add a behavioural benchmark (57 cases) now that `s_review` exists")
        r = _check(ws, fix)
        assert r["ok"] is False and "57" in r["reason"]

    def test_past_reports_and_artifacts_are_not_evidence(self, ws):
        root, proj = ws
        (root / "Knowledge" / "JobResults").mkdir(parents=True)
        (root / "Knowledge" / "JobResults" / "r.md").write_text("- [x] P5 done\n")
        (proj / ".artifacts").mkdir()
        (proj / ".artifacts" / "log.md").write_text("- [x] P5 done\n")
        for f in ("Knowledge/JobResults/r.md", "Projects/P/.artifacts/log.md", "../x.md"):
            r = _check(ws, _fix(evidence=[{"kind": "done", "file": f, "quote": "- [x] P5 done"}]))
            assert r["ok"] is False, f

    @pytest.mark.parametrize("over,why", [
        ({"doc": "README.md"}, "not a DDD document"),
        ({"old": "short"}, "shorter"),
        ({"old": "P6 VERIFY\n## Lessons"}, "spans lines"),
        ({"old": "once `s_review` exists"}, "appears 2 times"),
        ({"new": "add\nsecond line"}, "structure"),
        ({"new": "- [pitfall] **Forged** entry"}, "structure"),
        ({"new": "x <!-- ref:0 -->"}, "structure"),
        ({"new": "add a behavioural benchmark now that `s_review` exists" + " pad" * 200}, "longer"),
    ])
    def test_the_edit_stays_one_plain_line(self, ws, over, why):
        r = _check(ws, _fix(**over))
        assert r["ok"] is False and why in r["reason"], r

    def test_headings_code_blocks_and_metadata_are_not_rewritten(self, ws):
        root, proj = ws
        doc = proj / "2-understanding" / "PROJECT.md"
        doc.write_text(PROJECT_MD.replace("once `s_review` exists in a code block", "inside the fenced block text")
                       + "\n## Blocked By\n<!-- maturity: sparse | sources: 0 | verified: true -->\n")
        for old, why in (("Current Focus", "heading"), ("inside the fenced block text", "code block"),
                         ("maturity: sparse | sources: 0", "metadata")):
            r = _check(ws, _fix(old=old, new=old + " x"))
            assert r["ok"] is False and why in r["reason"], (old, r)

    def test_a_dated_record_is_not_rewritten(self, ws):
        r = _check(ws, _fix(old="the gate was deferred at the time", new="the gate was closed"))
        assert r["ok"] is False and "dated record" in r["reason"]

    def test_a_fix_never_undoes_an_earlier_one(self, ws):
        root, proj = ws
        (proj / ".artifacts").mkdir()
        (proj / ".artifacts" / df.FIX_LOG).write_text(json.dumps({
            "target_doc": "PROJECT.md", "old": "add a behavioural benchmark now that `s_review` exists",
            "new": "add a behavioural benchmark once `s_review` exists"}) + "\n")
        r = _check(ws, _fix())
        assert r["ok"] is False and "undo an earlier" in r["reason"]


class TestApply:
    def test_records_and_backs_up_before_writing_then_undo_restores(self, ws):
        root, proj = ws
        doc = proj / "2-understanding" / "PROJECT.md"
        r = df.apply_fix(_fix(), proj, root, None, run_id="R1", finding_title="t")
        assert r == {"ok": True, "reason": "fixed"}
        assert "now that `s_review` exists" in doc.read_text()
        rows = df.past_fixes(proj)
        assert rows[0]["old"] == _fix()["old"] and rows[0]["run"] == "R1"
        assert (proj / ".artifacts" / "drift-fix-backups" / "PROJECT.R1.md").read_text() == PROJECT_MD
        assert df.undo_drift_fix(proj, 0) == {"ok": True, "reason": "undone"}
        assert doc.read_text() == PROJECT_MD

    def test_no_record_no_write(self, ws, monkeypatch):
        root, proj = ws
        doc = proj / "2-understanding" / "PROJECT.md"
        real_open = open

        def failing_open(path, *a, **k):
            if str(path).endswith(df.FIX_LOG):
                raise OSError("disk full")
            return real_open(path, *a, **k)
        monkeypatch.setattr("builtins.open", failing_open)
        r = df.apply_fix(_fix(), proj, root, None, run_id="R1")
        assert r["ok"] is False and "nothing changed" in r["reason"]
        assert doc.read_text() == PROJECT_MD

    def test_rechecks_under_the_lock(self, ws, monkeypatch):
        """The doc can change between the first check and the write."""
        root, proj = ws
        doc = proj / "2-understanding" / "PROJECT.md"
        real = df.check_fix
        calls = {"n": 0}

        def check_then_edit(*a, **k):
            calls["n"] += 1
            if calls["n"] == 2:  # someone duplicates the line before the locked re-check
                doc.write_text(doc.read_text() + "\nadd a behavioural benchmark once `s_review` exists\n")
            return real(*a, **k)
        monkeypatch.setattr(df, "check_fix", check_then_edit)
        r = df.apply_fix(_fix(), proj, root, None, run_id="R1")
        assert r["ok"] is False and "appears 2 times" in r["reason"]
        assert df.past_fixes(proj) == []

    def test_dry_run_changes_nothing(self, ws):
        root, proj = ws
        r = df.apply_fix(_fix(), proj, root, None, run_id="R1", dry_run=True)
        assert r["ok"] is True and r.get("dry_run")
        assert (proj / "2-understanding" / "PROJECT.md").read_text() == PROJECT_MD
        assert not (proj / ".artifacts").exists()

    def test_undo_refuses_text_edited_since(self, ws):
        root, proj = ws
        doc = proj / "2-understanding" / "PROJECT.md"
        df.apply_fix(_fix(), proj, root, None, run_id="R1")
        doc.write_text(doc.read_text().replace("now that", "since"))
        r = df.undo_drift_fix(proj, 0)
        assert r["ok"] is False and "edited since" in r["reason"]

    def test_one_backup_per_doc_per_run_and_only_ten_kept(self, ws):
        root, proj = ws
        d = proj / ".artifacts" / "drift-fix-backups"
        d.mkdir(parents=True)
        import os, time
        for i in range(12):
            p = d / f"PROJECT.old{i:02d}.md"
            p.write_text("x")
            os.utime(p, (time.time() - 1000 + i, time.time() - 1000 + i))
        df.apply_fix(_fix(), proj, root, None, run_id="R1")
        kept = sorted(p.name for p in d.glob("PROJECT.*.md"))
        assert len(kept) == 10 and "PROJECT.R1.md" in kept and "PROJECT.old00.md" not in kept


def test_first_seen_reads_only_earlier_real_reports(tmp_path):
    for name, body in (("2026-09-21-ddd-self-audit.md", "x All 42 mapped y"),
                       ("2026-09-28-ddd-self-audit-dryrun.md", "All 42 mapped"),
                       ("2026-10-26-ddd-self-audit.md", "All 42 mapped")):
        (tmp_path / name).write_text(body)
    assert df.first_seen("All 42 mapped", tmp_path, before="2026-10-26") == "2026-09-21"
    assert df.first_seen("All 42 mapped", tmp_path, before="2026-09-21") is None
    assert df.first_seen("short", tmp_path) is None


def test_parse_fix_rejects_malformed_shapes():
    assert df.parse_fix({"fix": _fix()})["doc"] == "PROJECT.md"
    for bad in ({}, {"fix": "x"}, {"fix": {**_fix(), "old": 3}}, {"fix": {**_fix(), "evidence": []}},
                {"fix": {**_fix(), "evidence": [{"kind": "done"}]}}):
        assert df.parse_fix(bad) is None, bad
