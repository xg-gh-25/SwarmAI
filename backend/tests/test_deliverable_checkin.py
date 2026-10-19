"""§4.9 Phase 3 块A — the FOUR boundary cases of review-deliverable check-in (AC3).

Each boundary is a design §4.9 边界 that must behave correctly, and R28 requires
each recovery/boundary path to have a FORCED execution test (not just "compiles"):

  边界① gitignore conflict → WARN + skip B, NEVER edit the user's .gitignore (P4)
  边界② non-git / workspace-only → degrade to ledger A, no B (covered in
          test_artifact_cli_run_commit.py::TestReviewDeliverableCheckin too; the
          generator-level non-git case is here)
  边界③ sealed-only → B carries ONLY the sealed definition (REPORT.md +
          requirements.json + run-ref.txt), NOT every per-stage snapshot
  边界④ multi-git-root → B lands in the MOST-MODIFIED root only, never duplicated

These drive `_generate_review_deliverable` directly (unit) where the boundary is a
property of B generation, and through `cmd_run_commit` (integration) where it is a
property of root selection.

Design canon: Knowledge/Designs/2026-10-12-pipeline-requirement-spec-and-scope-depth-design.md §4.9
"""
import json
import subprocess
from argparse import Namespace
from pathlib import Path

import pytest


def _git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a],
                          capture_output=True, text=True, timeout=30)


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "Projects" / "TestProject" / ".artifacts" / "runs").mkdir(parents=True)
    return tmp_path


def _write_run_with_report(workspace, run_id, files_touched,
                           *, report=("# Report\n## Intent → Requirement 溯源\nFR-1\n"
                                      "## 9. Lessons\n- [note] a real projected report always carries §9\n"),
                           eval_artifact=None, requirements="default"):
    # Default report carries a SUBSTANTIVE §9 (a '- ' lesson bullet) because a real
    # cmd_run_report projection ALWAYS renders §9 — the §9-degenerate B-copy gate
    # (run_7c30c517) would otherwise correctly skip B for a stub report missing §9,
    # which is not what tests of OTHER contracts (sealed-only, content-safety) mean to
    # exercise. Degeneracy is tested explicitly in TestSection9DegenerateGate.
    """Write a run with a terminal REPORT.md on disk (ledger A). Since B's content
    契约 is now the EVALUATE requirements[] (NOT the REPORT.md), B generation needs a
    RESOLVABLE evaluate artifact — so by default this also lands a real evaluate
    artifact file + manifest entry (so cmd_run_commit's _load_artifact_for_metrics
    resolves it). Pass requirements=None to omit it (the 'no requirements → no B' case)."""
    run_dir = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    reqs = ([{"id": "FR-1", "statement": "ship a thing", "work_class": "task",
              "source": {"kind": "user-stated", "ref": "test"}, "status": "confirmed",
              "acceptance": [{"given": "g", "when": "w", "then": "t"}]}]
            if requirements == "default" else requirements)
    stages = [{"stage": "deliver", "status": "completed", "push_ready": True}]
    if reqs or eval_artifact is not None:
        stages.insert(0, {"stage": "evaluate", "status": "completed", "artifact_id": "art_eval1"})
        # Land a REAL evaluate artifact file + manifest entry so the cmd_run_commit
        # path's _load_artifact_for_metrics(project, "art_eval1") resolves requirements.
        art_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        art_file = art_dir / f"evaluation-20261012-art_eval1.json"
        art_file.write_text(json.dumps({"schema_version": 1, "requirements": reqs or [],
                                         "clarifications": []}), encoding="utf-8")
        manifest_file = art_dir / "manifest.json"
        manifest = {"artifacts": []}
        if manifest_file.exists():
            try:
                manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                manifest = {"artifacts": []}
        manifest.setdefault("artifacts", []).append(
            {"id": "art_eval1", "file": art_file.name, "type": "evaluation"})
        manifest_file.write_text(json.dumps(manifest), encoding="utf-8")
    data = {
        "id": run_id, "project": "TestProject", "requirement": "Phase3 thing",
        "profile": "goal", "status": "running", "stages": stages,
        "created_at": "2026-10-12T00:00:00+00:00", "updated_at": "2026-10-12T00:00:00+00:00",
        "files_touched": files_touched,
    }
    (run_dir / "run.json").write_text(json.dumps(data), encoding="utf-8")
    (run_dir / "REPORT.md").write_text(report)
    return run_dir


def _src_repo(tmp_path, name, gitignore=None):
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t.co")
    _git(repo, "config", "user.name", "t")
    if gitignore is not None:
        (repo / ".gitignore").write_text(gitignore)
    (repo / "f.py").write_text("v1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    (repo / "f.py").write_text("v2\n")
    return repo


def _write_run_with_requirements(workspace, run_id, files_touched, *, write_report=False,
                                 requirements=None, clarifications=None, reflect_lessons=None):
    """AC3 契约迁移 fixture: a run whose B-generation substance is the EVALUATE
    `requirements[]` (NOT a terminal REPORT.md). By default write_report=False —
    this reproduces the REAL DELIVER-time sequence where run-commit runs BEFORE
    REFLECT writes REPORT.md. B must still generate from requirements.

    reflect_lessons (default None): when a list is given, append a completed `reflect`
    stage carrying those lessons[] — so cmd_run_report projects a SUBSTANTIVE §9 (the
    seed-lessons→run-report ordering satisfied). Default None keeps the pre-REFLECT
    shape (no reflect stage → degenerate §9 → B correctly skipped)."""
    run_dir = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    stages = [
        {"stage": "evaluate", "status": "completed", "artifact_id": "art_eval1"},
        {"stage": "deliver", "status": "completed", "push_ready": True},
    ]
    if reflect_lessons is not None:
        stages.append({"stage": "reflect", "status": "completed", "lessons": reflect_lessons})
    data = {
        "id": run_id, "project": "TestProject",
        "requirement": "把 depth 轴加到 pipeline(需求溯源快照内容来源)",
        "profile": "full", "status": "running", "stages": stages,
        "created_at": "2026-10-12T00:00:00+00:00", "updated_at": "2026-10-12T00:00:00+00:00",
        "files_touched": files_touched,
    }
    (run_dir / "run.json").write_text(json.dumps(data), encoding="utf-8")
    if write_report:
        (run_dir / "REPORT.md").write_text("# Report\n## 9. Lessons\n- learned X\n")
    reqs = requirements if requirements is not None else [
        {"id": "FR-1", "statement": "加 depth 正交轴", "work_class": "task",
         "source": {"kind": "user-stated", "ref": "XG 要求"}, "status": "confirmed",
         "acceptance": [{"given": "a run", "when": "set depth", "then": "rigor adjusts"}]},
    ]
    eval_art = {
        "schema_version": 1, "requirements": reqs,
        "clarifications": clarifications if clarifications is not None else [],
    }
    return run_dir, eval_art


def _run_commit(workspace, run_id, force=False):
    from scripts.artifact_cli import cmd_run_commit
    from unittest.mock import patch
    args = Namespace(project="TestProject", run_id=run_id, force=force)
    captured = {}
    with patch("scripts.artifact_cli._get_workspace", return_value=workspace):
        try:
            cmd_run_commit(args, reg=None)
        except SystemExit as e:
            captured["exit"] = e.code
    return captured


class TestContentContractCopyReport:
    """AC1-AC4 回退(run_1d663ea0): B's audit content is a verbatim COPY of ledger A's
    terminal REPORT.md — which ALREADY contains the §4.6 '需求溯源审计' (Intent→Requirement)
    section (rendered into it by cmd_run_report via render_audit_trail_md). B carries a
    single REPORT.md (NOT a separate REQUIREMENTS.md). The generation threshold is
    'A has REPORT.md on disk' — which IS satisfied at run-commit time because deliver.md
    generates REPORT.md (Pipeline Report step) BEFORE run-commit. run_44a06138 is the
    known-good proof: its B is a verbatim copy of A's terminal REPORT.md incl. §9 Lessons."""

    def test_B_is_a_projection_of_terminal_report(self, workspace, tmp_path):
        """AC1+AC3 (tracer): A has a terminal REPORT.md with the §需求溯源审计 section →
        B is a WHITELIST PROJECTION of A as REPORT.md (NOT a separate REQUIREMENTS.md,
        NOT a verbatim copy). Whitelisted sections survive; A (ledger) is untouched."""
        from scripts.artifact_cli import _generate_review_deliverable
        from unittest.mock import patch
        repo = _src_repo(tmp_path, "reprepo")
        report_body = ("# Autonomous Pipeline Report: thing\n## TL;DR\ndone\n"
                       "## 需求溯源审计 (Requirement Provenance Audit)\n"
                       "### 原始意图 (Original Intent)\n> add depth axis\nFR-1\n"
                       "## 9. Lessons\n- learned X\n")
        _write_run_with_report(workspace, "run_rep", [str(repo / "f.py")], report=report_body)

        with patch("scripts.artifact_cli._get_workspace", return_value=workspace):
            created, warnings = _generate_review_deliverable("TestProject", "run_rep", str(repo))

        assert created, f"B must generate when A has REPORT.md; got {warnings}"
        names = {Path(c).name for c in created}
        assert "REPORT.md" in names, f"B must carry REPORT.md, got {names}"
        assert "REQUIREMENTS.md" not in names, f"B must NOT carry a separate REQUIREMENTS.md, got {names}"
        # CONTRACT CHANGE (run_d8321b43): B is a WHITELIST PROJECTION of A, not a verbatim
        # copy. A (ledger) stays the full durable audit; B (committed) carries only the
        # reviewer-facing whitelist sections. Prefix-match keeps the CJK provenance title
        # even though its rendered form has an English suffix.
        b_report = next(c for c in created if Path(c).name == "REPORT.md")
        a_report = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / "run_rep" / "REPORT.md"
        b_body = Path(b_report).read_text(encoding="utf-8")
        a_body = a_report.read_text(encoding="utf-8")
        assert "## TL;DR" in b_body
        assert "需求溯源审计" in b_body, "B must keep the provenance section (whitelist, prefix-match)"
        # §9 Lessons stays in A: REFLECT writes it after the landing, so B never waits on it.
        assert "## 9. Lessons" not in b_body and "learned X" not in b_body
        # A (ledger) is untouched — still carries its full self.
        assert "需求溯源审计" in a_body and "## 9. Lessons" in a_body

    def test_copied_report_carries_provenance_section(self, workspace, tmp_path):
        """AC2: the copied REPORT.md carries the §4.6 需求溯源审计 section (Intent→
        Requirement) — the whole point of copying the terminal report (it has the
        provenance section baked in by cmd_run_report)."""
        from scripts.artifact_cli import _generate_review_deliverable
        from unittest.mock import patch
        repo = _src_repo(tmp_path, "provrepo2")
        report_body = ("# Report\n## 需求溯源审计 (Requirement Provenance Audit)\n"
                       "### 原始意图\n> the original intent verbatim\n"
                       "### 需求推导\nFR-1: add depth\n## 9. Lessons\n- lesson\n")
        _write_run_with_report(workspace, "run_prov2", [str(repo / "f.py")], report=report_body)

        with patch("scripts.artifact_cli._get_workspace", return_value=workspace):
            created, _ = _generate_review_deliverable("TestProject", "run_prov2", str(repo))

        b_report = next((c for c in created if Path(c).name == "REPORT.md"), None)
        assert b_report, f"B REPORT.md must exist, got {created}"
        body = Path(b_report).read_text(encoding="utf-8")
        assert "需求溯源审计" in body, "copied REPORT.md must carry the §4.6 provenance section"
        assert "the original intent verbatim" in body, "provenance section must carry the original intent"
        assert "FR-1" in body, "provenance section must carry the requirement derivation"

    def test_missing_report_is_auto_generated_then_B_produced(self, workspace, tmp_path):
        """FR-1 (supersedes the old no-B-warns-loudly contract): when A has NO terminal
        REPORT.md on disk at run-commit time, _generate_review_deliverable must AUTO-GENERATE
        it (reusing cmd_run_report, the deterministic run.json→REPORT.md projection) and THEN
        produce B — not skip-with-warning. The old behavior silently lost the audit projection
        on every ordering-violated run (52% of historical runs); the guarantee now lives in
        code, not deliver.md prose. The run.json has enough stages for cmd_run_report to render
        a substantive report."""
        from scripts.artifact_cli import _generate_review_deliverable
        from unittest.mock import patch
        repo = _src_repo(tmp_path, "autogenrepo")
        # §9 already seeded (reflect lessons present) → auto-gen renders a SUBSTANTIVE
        # report → the unchanged §9-degenerate gate passes → B is produced. (The ordering
        # that was violated is "REPORT.md on disk"; lessons were already in the ledger.)
        run_dir, _ = _write_run_with_requirements(
            workspace, "run_autogen", [str(repo / "f.py")], write_report=False,
            reflect_lessons=["auto-gen fills the file-absence hole, §9 content stays gated"])
        assert not (run_dir / "REPORT.md").is_file(), "precondition: no REPORT.md on disk"
        with patch("scripts.artifact_cli._get_workspace", return_value=workspace):
            created, warnings = _generate_review_deliverable("TestProject", "run_autogen", str(repo))
        # REPORT.md was auto-generated into ledger A ...
        assert (run_dir / "REPORT.md").is_file(), \
            f"missing REPORT.md must be AUTO-GENERATED, not skipped; warnings={warnings}"
        # ... and B was produced from it.
        b_report = next((c for c in created if Path(c).name == "REPORT.md"), None)
        assert b_report, f"after auto-generate, B REPORT.md must be produced; got {created}, {warnings}"
        # OBSERVABILITY: a successful auto-gen must surface a (non-blocking) warning so an
        # operator SEES the DELIVER ordering was slipped-and-recovered — the whole point of
        # the feature is to make the violation visible, not silently paper it over.
        assert any("AUTO-GENERATED" in w for w in warnings), \
            f"auto-gen success must emit an ordering-slip warning; got {warnings}"

    def test_autogen_failure_fails_loud_not_silent(self, workspace, tmp_path):
        """FR-2: if auto-generation of REPORT.md itself fails (真异常 — e.g. cmd_run_report
        raises on an unreadable/corrupt ledger), B is NOT produced AND the warning carries the
        fail-loud sentinel so the caller BLOCKS the commit rather than silently committing
        without the audit projection. The INPUT-SHAPE guard: a generation failure is loud, never
        a silent no-op (IMPROVEMENT.md: fail-loud must guard the failure, not swallow it)."""
        from scripts.artifact_cli import _generate_review_deliverable, _FAILLOUD_SENTINEL
        from unittest.mock import patch
        repo = _src_repo(tmp_path, "autogenfailrepo")
        run_dir, _ = _write_run_with_requirements(
            workspace, "run_autogenfail", [str(repo / "f.py")], write_report=False)
        with patch("scripts.artifact_cli._get_workspace", return_value=workspace), \
             patch("scripts.artifact_cli.cmd_run_report", side_effect=RuntimeError("boom")):
            created, warnings = _generate_review_deliverable("TestProject", "run_autogenfail", str(repo))
        assert created == [], f"auto-gen failure → no B; got {created}"
        assert warnings and any(w.startswith(_FAILLOUD_SENTINEL) for w in warnings), \
            f"auto-gen failure must carry the fail-loud sentinel (caller blocks); got {warnings}"

    def test_autogen_without_lessons_still_produces_B(self, workspace, tmp_path):
        """REPORT.md absent AND no reflect lessons yet (the normal state at landing: REFLECT
        runs after DELIVER): auto-gen writes A's report and B is produced from it, without
        §9 and without any warning that would block or lower anything."""
        from scripts.artifact_cli import _generate_review_deliverable, _FAILLOUD_SENTINEL
        from unittest.mock import patch
        repo = _src_repo(tmp_path, "autogendegenrepo")
        # reflect_lessons=None → no reflect stage → auto-gen renders degenerate §9.
        run_dir, _ = _write_run_with_requirements(
            workspace, "run_autogendegen", [str(repo / "f.py")], write_report=False)
        with patch("scripts.artifact_cli._get_workspace", return_value=workspace):
            created, warnings = _generate_review_deliverable("TestProject", "run_autogendegen", str(repo))
        assert (run_dir / "REPORT.md").is_file(), "auto-gen should still create the file"
        b_report = next((c for c in created if Path(c).name == "REPORT.md"), None)
        assert b_report, f"no lessons yet → B is still produced; got {created}, {warnings}"
        assert "## 9. Lessons" not in Path(b_report).read_text(encoding="utf-8")
        assert not any(w.startswith(_FAILLOUD_SENTINEL) for w in warnings), warnings


class TestReportIsLedgerProjectionNotPlaceholder:
    """AC1+AC4 (anti-drift guard): the audit copy B a reviewer sees in git must carry the
    run's REAL §9 REFLECT lessons — never the `_(populated in REFLECT)_` placeholder that
    the deleted hand-written template produced. B is a verbatim copy of ledger A; A is the
    deterministic ledger projection (`cmd_run_report`). This test goes RED if anyone
    re-introduces a hand-written template whose §9 is a frozen placeholder, OR if B is
    frozen before the lessons are seeded into the reflect record. It is the executable
    form of NFR-1 (completeness-as-contract: §9 shows truth, never a lying placeholder)."""

    def test_B_report_carries_no_lessons_section_at_all(self, workspace, tmp_path):
        """§9 Lessons lives only in ledger A (REFLECT writes it after the landing): B carries
        neither the real lessons nor any placeholder, so it can never freeze a stale §9."""
        from scripts.artifact_cli import _generate_review_deliverable
        from unittest.mock import patch
        repo = _src_repo(tmp_path, "lessonsrepo")
        # A terminal report as cmd_run_report would project it with §9 FILLED (lessons
        # seeded into the reflect record at DELIVER-tail, before run-commit).
        report_body = (
            "# Report\n## 需求溯源审计\nFR-1\n"
            "## 9. Lessons\n"
            "- [pitfall] a one-shot event modeled as shared state is a race source\n"
            "- [decision] root-fix deletes the competing renderer, not patches it\n"
        )
        _write_run_with_report(workspace, "run_les", [str(repo / "f.py")], report=report_body)
        with patch("scripts.artifact_cli._get_workspace", return_value=workspace):
            created, _ = _generate_review_deliverable("TestProject", "run_les", str(repo))
        b_report = next((c for c in created if Path(c).name == "REPORT.md"), None)
        assert b_report, f"B REPORT.md must exist, got {created}"
        body = Path(b_report).read_text(encoding="utf-8")
        assert "## 9. Lessons" not in body and "a one-shot event modeled" not in body
        assert "_(populated in REFLECT)_" not in body
        assert "FR-1" in body   # the reviewer-facing sections are still there

    def test_gitignored_dest_skips_B_and_does_not_edit_gitignore(self, workspace, tmp_path, capsys):
        repo = _src_repo(tmp_path, "ignrepo", gitignore=".aidlc/\n")
        gitignore_before = (repo / ".gitignore").read_text()
        _write_run_with_report(workspace, "run_ign", [str(repo / "f.py")])

        _run_commit(workspace, "run_ign")
        capsys.readouterr()

        # B dir NOT created (would never be committed anyway).
        assert not (repo / ".aidlc" / "deliverables" / "run_ign").exists(), \
            "B must be skipped when its dest is gitignored"
        # The user's .gitignore is UNTOUCHED (P4 — never silently edit the user's file).
        assert (repo / ".gitignore").read_text() == gitignore_before, \
            "must NOT edit the user's .gitignore"
        # The code commit still happened (B skip never blocks the code).
        head = _git(repo, "show", "--name-only", "--format=", "HEAD").stdout
        assert "f.py" in head, "code commit must still proceed when B is skipped"


class TestBoundary2NonGit:
    """边界②: a non-git project (STEERING #5: only SwarmAI DDD is in git) → the run's
    files resolve to NO git repo → B is NOT generated (deliver via ledger A), and the
    run-commit does not crash. This is the REAL boundary behavior, exercised through
    cmd_run_commit (where the git-root gating lives), not just the generator."""

    def test_non_git_files_produce_no_B_and_no_crash(self, workspace, tmp_path, capsys):
        # A plain (non-git) dir holding the run's touched file — like an internal
        # project that is pure local disk, never a git repo.
        plain = tmp_path / "plaindir"
        plain.mkdir()
        (plain / "f.py").write_text("x=1\n")
        _write_run_with_report(workspace, "run_ng", [str(plain / "f.py")])

        cap = _run_commit(workspace, "run_ng")
        capsys.readouterr()

        # No B anywhere under the non-git dir (there is no repo to check it into).
        assert not (plain / ".aidlc").exists(), \
            "a non-git project must get NO B (deliver via ledger A, §4.9 边界②)"
        # run-commit refused cleanly (no git repo for the file → nothing committed),
        # NOT a crash/traceback. exit code is a controlled refusal (2) or clean return.
        assert cap.get("exit") in (None, 2), f"expected clean refusal, got {cap}"
        # Ledger A still intact (the deliverable lives there for audit).
        run_dir = workspace / "Projects/TestProject/.artifacts/runs/run_ng"
        assert (run_dir / "REPORT.md").is_file(), "ledger A REPORT.md must remain for audit"


class TestBoundary3SealedOnly:
    """边界③: B carries ONLY the sealed definition, never per-stage snapshots."""

    def test_B_contains_only_sealed_files(self, workspace, tmp_path, capsys):
        repo = _src_repo(tmp_path, "sealedrepo")
        run_dir = _write_run_with_report(workspace, "run_seal", [str(repo / "f.py")])
        # Litter ledger A with per-stage snapshot files that must NOT leak into B.
        (run_dir / "checkpoint-plan.json").write_text("{}")
        (run_dir / "evaluation-20261012-art_x.json").write_text("{}")
        (run_dir / "METRICS.json").write_text("{}")

        _run_commit(workspace, "run_seal")
        capsys.readouterr()

        bdir = repo / ".aidlc" / "deliverables" / "run_seal"
        got = sorted(p.name for p in bdir.iterdir()) if bdir.exists() else []
        # B carries EXACTLY ONE file: a verbatim copy of ledger A's terminal
        # REPORT.md. The §4.6 需求溯源审计 (Intent→Requirement) provenance + §9
        # Lessons live INSIDE that single report (rendered by cmd_run_report), so
        # the old orphan sidecars (requirements.json machine-SRS + run-ref.txt
        # back-pointer) are gone — a reviewer resolves everything from the ONE
        # human-readable REPORT the ledger projects. Never a per-stage snapshot.
        assert set(got) == {"REPORT.md"}, \
            f"B must contain ONLY the projected REPORT.md, got {got}"
        assert "requirements.json" not in got, \
            "orphan requirements.json removed — provenance lives inside REPORT.md §4.6"
        assert "run-ref.txt" not in got, \
            "orphan run-ref.txt removed — a cross-machine reader cannot resolve its ledger path"
        assert "checkpoint-plan.json" not in got
        assert "METRICS.json" not in got
        assert "evaluation-20261012-art_x.json" not in got


class TestBoundary4MultiRoot:
    """边界④: B lands in the MOST-MODIFIED root only, never duplicated across roots."""

    def test_B_lands_in_most_modified_root_only(self, workspace, tmp_path, capsys):
        repo_big = _src_repo(tmp_path, "bigrepo")
        repo_small = _src_repo(tmp_path, "smallrepo")
        # big has 2 touched files, small has 1 → big is most-modified → B there only.
        (repo_big / "g.py").write_text("v2\n")
        _write_run_with_report(
            workspace, "run_multi",
            [str(repo_big / "f.py"), str(repo_big / "g.py"), str(repo_small / "f.py")],
        )
        _run_commit(workspace, "run_multi")
        capsys.readouterr()

        assert (repo_big / ".aidlc" / "deliverables" / "run_multi").exists(), \
            "B must land in the most-modified root (bigrepo)"
        assert not (repo_small / ".aidlc" / "deliverables" / "run_multi").exists(), \
            "B must NOT be duplicated into the less-modified root (smallrepo)"


class TestContentSafetyScan:
    """§4.9 边界③ FR-4 (AC4): B carries verbatim intent/clarifications and lands in a
    possibly-public repo. A content-safety scan (reused from ddd_packager — the SAME
    fail-closed scan s_ddd-distribute runs) must flag sensitive content BEFORE check-in
    so the human reviews before pushing. Never auto-redact (don't mutate the audit
    record); never block the LOCAL commit (push is the separate human gate)."""

    def test_sensitive_report_triggers_content_safety_warning(self, workspace, tmp_path):
        from scripts.artifact_cli import _generate_review_deliverable
        from unittest.mock import patch
        repo = _src_repo(tmp_path, "sensrepo")
        # B is now a verbatim COPY of A's terminal REPORT.md — so a secret anywhere in
        # that report (its 需求溯源审计 section carries the verbatim original intent)
        # reaches B. Put the secret in the copied report body.
        _write_run_with_report(
            workspace, "run_sens", [str(repo / "f.py")],
            report=('# Report\n## 需求溯源审计\n### 原始意图\n'
                    '> add retry; user pasted token = "hunter2secret" by mistake\n'
                    '## 9. Lessons\n- [note] substantive §9 so the B-copy gate lets B through\n'),
        )
        with patch("scripts.artifact_cli._get_workspace", return_value=workspace):
            created, warnings = _generate_review_deliverable("TestProject", "run_sens", str(repo))

        # B was still generated (we never block the LOCAL commit)...
        assert any("REPORT.md" in c for c in created)
        # ...but a LOUD content-safety warning fired naming the sensitive finding.
        joined = " ".join(warnings)
        assert "CONTENT-SAFETY" in joined, f"must warn on sensitive content, got {warnings}"
        assert "sensitive finding" in joined or "finding" in joined

    def test_clean_report_no_content_safety_warning(self, workspace, tmp_path):
        from scripts.artifact_cli import _generate_review_deliverable
        from unittest.mock import patch
        repo = _src_repo(tmp_path, "cleanrepo")
        _write_run_with_report(
            workspace, "run_clean", [str(repo / "f.py")],
        )
        with patch("scripts.artifact_cli._get_workspace", return_value=workspace):
            created, warnings = _generate_review_deliverable("TestProject", "run_clean", str(repo))

        assert any("REPORT.md" in c for c in created)
        assert not any("CONTENT-SAFETY" in w for w in warnings), \
            f"clean content must not trigger a false content-safety warning, got {warnings}"


class TestReviewProjection:
    """run_d8321b43: B is a WHITELIST PROJECTION of ledger A, not a verbatim copy.
    Keeps reviewer-facing sections (TL;DR / §1 / 需求溯源审计 / §2 / §3 / §6 / §7 / §8 / §10);
    drops pure process (§4 Pipeline Execution / §5 TDD Results). §7 Quality Gates is
    KEPT but STRIPPED to its reviewer-facing sub-verdicts (§7.5 Adversarial Review +
    §7.6 Completion Audit) — the leading Gate stat table (REVIEW findings count / TEST
    pass count / Confidence N/10) is dropped as noise (Gate-2 Red-Team HIGH: the
    adversarial verdict + 'NOT RUN' quality-gap flag are the single most reviewer-
    critical facts and must survive; the stat rows are noise). §8 Files Changed is
    re-rendered from the live staging set. A (cmd_run_report output) is never touched."""

    _FULL_A = (
        "# Autonomous Pipeline Report: thing\n"
        "## TL;DR\ndone\n"
        "## 1. Requirement\nmake it true\n"
        "## Requirements\n| ID | status |\n| FR-1 | confirmed |\n### 澄清问答\nQ1: self-answered\n"
        "## 需求溯源审计 (Requirement Provenance Audit)\n### 原始意图\n> intent\nFR-1\n"
        "## 2. Evaluation\nGO scope complex\n"
        "## 3. Design & Approach\nwhitelist projection\n"
        "## 4. Pipeline Execution\n### 4.1 Per-Stage Elapsed\nstage timings\n"
        "## 5. TDD Results\n| Tests total | 7 |\n| Tests passed | 7 |\n"
        "## 6. Decision Log\nchose A because\n"
        "## 7. Quality Gates\n"
        "| Gate | Result |\n|------|--------|\n"
        "| REVIEW (code quality) | 2 findings (HIGH) |\n"
        "| TEST (suite) | 22/22 pass |\n"
        "| Confidence | 8/10 |\n"
        "\n### 7.5 Adversarial Review\n"
        "**⚠️ NOT RUN** — adversarial sub-agent was not spawned during this run.\n"
        "\n### 7.6 Completion Audit\n- [x] AC1 met\n**Met:** 1 | **Unmet:** 0\n"
        "## 8. Files Changed\n- `self_reported.py`\n\n**+10 / -2 lines** | 1 files\n"
        "## 9. Lessons\n- learned something transferable\n"
        "## 10. Known Gaps & Attention Flags\ngap: none\n"
    )

    def _project(self, source_files):
        from scripts.artifact_cli import _project_report_for_review
        return _project_report_for_review(self._FULL_A, source_files)

    def test_AC1_whitelist_sections_kept(self):
        out = self._project(["a.py"])
        for title in ("## TL;DR", "## 1. Requirement", "## Requirements", "需求溯源审计",
                      "## 2. Evaluation", "## 3. Design & Approach", "## 6. Decision Log",
                      "## 7. Quality Gates", "## 8. Files Changed",
                      "## 10. Known Gaps & Attention Flags"):
            assert title in out, f"whitelist section must survive: {title!r}\n{out}"
        assert "## 9. Lessons" not in out   # written at REFLECT, after the landing: A only
        # The intent→requirement table + clarifications (the North-Star-critical content).
        assert "FR-1 | confirmed" in out and "澄清问答" in out
        # Prefix-match must NOT conflate the two distinct requirement sections.
        assert "## 1. Requirement\nmake it true" in out  # §1 kept as itself
        assert "## Requirements\n" in out                # the table section kept as itself

    def test_AC2_process_stats_sections_dropped(self):
        out = self._project(["a.py"])
        for title in ("## 4. Pipeline Execution", "### 4.1", "## 5. TDD Results",
                      "Tests total"):
            assert title not in out, f"pure-process section must be dropped: {title!r}\n{out}"

    def test_AC7_quality_gates_kept_but_stat_table_stripped(self):
        # Gate-2 Red-Team HIGH: §7 is KEPT (adversarial verdict + completion audit are
        # the most reviewer-critical facts), but the leading Gate stat table is stripped.
        out = self._project(["a.py"])
        # KEPT — the reviewer-facing sub-verdicts:
        assert "### 7.5 Adversarial Review" in out, "§7.5 adversarial verdict must survive"
        assert "NOT RUN" in out, "§7.5 'adversarial NOT RUN' quality-gap flag must survive"
        assert "### 7.6 Completion Audit" in out, "§7.6 completion audit must survive"
        assert "AC1 met" in out
        # STRIPPED — the stat-table noise (Confidence / findings count / TEST pass count):
        assert "Confidence | 8/10" not in out, "Confidence stat row must be stripped"
        assert "REVIEW (code quality)" not in out, "REVIEW findings-count stat row must be stripped"
        assert "22/22 pass" not in out, "TEST pass-count stat row must be stripped"

    def test_AC2_provenance_prefix_match(self):
        # Gate-1 Q1: real title has an English suffix; exact-equal would drop it.
        out = self._project(["a.py"])
        assert "需求溯源审计 (Requirement Provenance Audit)" in out
        assert "> intent" in out and "FR-1" in out

    def test_AC4_section8_from_live_staging(self):
        # §8 re-rendered from the live staging set, NOT the agent self-reported file.
        out = self._project(["backend/real_a.py", "backend/real_b.py"])
        assert "## 8. Files Changed" in out
        assert "backend/real_a.py" in out and "backend/real_b.py" in out
        assert "self_reported.py" not in out, "§8 must use live staging, not A's self-report"
        # The self-reported lines-count noise is gone (we show real files, not stats).
        assert "+10 / -2 lines" not in out

    def test_AC6_empty_staging_is_honest_not_fabricated(self):
        out = self._project([])
        assert "## 8. Files Changed" in out
        # No fabricated file list; an honest "no committed files" marker instead.
        assert "self_reported.py" not in out
        assert ("无 committed" in out or "no committed" in out.lower()
                or "No committed" in out), f"empty staging must be honest, got §8:\n{out}"


class TestSection8Relativization:
    """§8 Files Changed must render REPO-RELATIVE paths, never machine-local absolute
    ones — the committed B-copy lands in a possibly-PUBLIC repo (R30#7). `files_touched`
    holds ABSOLUTE paths (build.md 'prefer ABSOLUTE' for run-commit's cross-repo git add),
    so the renderer MUST relativize against the repo root. Threaded via an optional
    repo_root param; repo_root=None preserves legacy pass-through behavior."""

    _A = (
        "# Report\n"
        "## 8. Files Changed\n- `PLACEHOLDER`\n\n**+0 / -0 lines** | 1 files\n"
        "## 9. Lessons\n- x\n"
    )

    def _render(self, source_files, repo_root=None):
        from scripts.artifact_cli import _render_review_files_section
        return _render_review_files_section(source_files, repo_root)

    def test_absolute_path_relativized(self):
        # AC1: an absolute path under repo_root → repo-relative, no absolute prefix.
        out = self._render(["/abs/repo/backend/core/x.py"], "/abs/repo")
        assert "- `backend/core/x.py`" in out
        assert "/abs/repo" not in out, f"absolute prefix must not leak:\n{out}"

    def test_symlinked_root_still_relativized(self, tmp_path):
        # Gate-1 REQUIRED change: git rev-parse --show-toplevel canonicalizes symlinks,
        # but files_touched may be the UNRESOLVED form. A naive relative_to raises
        # ValueError → fail-safe keeps ABSOLUTE → the leak survives in exactly the CI/temp
        # env where it matters. .resolve() both sides fixes it.
        #
        # Build the symlink mismatch EXPLICITLY (not via the platform's /tmp) so the test
        # is non-vacuous on EVERY platform — on Linux CI /tmp is a real dir, so relying on
        # it would make this a no-op there (REVIEW spec-compliance caveat). Here: a real
        # symlink `link` → real dir `canonical`; repo_root is the CANONICAL path (what
        # git rev-parse returns), the file is addressed via the SYMLINK path (unresolved).
        import os
        canonical = tmp_path / "canonical" / "myrepo"
        canonical.mkdir(parents=True)
        link = tmp_path / "link"
        os.symlink(tmp_path / "canonical", link)
        canonical_root = str(canonical)                       # git-toplevel (resolved) form
        unresolved_file = str(link / "myrepo" / "backend" / "core" / "y.py")  # via symlink
        out = self._render([unresolved_file], canonical_root)
        assert "- `backend/core/y.py`" in out, f"symlinked-root file must relativize:\n{out}"
        assert str(link) not in out and str(canonical) not in out, (
            f"absolute (symlink OR canonical) must not leak:\n{out}"
        )

    def test_outside_root_fail_safe_keeps_original(self):
        # AC2: a path NOT under repo_root cannot be relativized → keep original, never '../..'.
        out = self._render(["/other/place/z.py"], "/abs/repo")
        assert "- `/other/place/z.py`" in out
        assert ".." not in out, f"must not emit ../.. garbage:\n{out}"

    def test_already_relative_passes_through(self):
        # AC2: an already-relative path → keep as-is (resolve-against-cwd then relative_to
        # repo raises ValueError → fail-safe), never mangled to a cwd-relative ../.. chain.
        out = self._render(["backend/core/w.py"], "/abs/repo")
        assert "- `backend/core/w.py`" in out
        assert ".." not in out, f"already-relative must pass through clean:\n{out}"

    def test_repo_root_none_is_legacy_passthrough(self):
        # AC3: repo_root=None (default / legacy callers) → render verbatim, no relativization.
        out = self._render(["/abs/repo/backend/core/x.py"], None)
        assert "- `/abs/repo/backend/core/x.py`" in out
