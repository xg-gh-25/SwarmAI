"""Tests for completion_surface_verdict — the pipeline completion commit+surface
gate. Root regression (run_0851350b): a hand-committed run (commits empty) was
mis-classified as docs-only and skipped both enforcements. The gate must key off
files_touched (BUILD ground truth), not commits."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from completion_gate import completion_surface_verdict  # noqa: E402


def _commit(files):
    return [{"files": files}]


class TestCompletionGate:
    def test_source_work_but_no_commits_BLOCKS(self):
        # THE BUG: run wrote source, hand-committed (commits empty) → must BLOCK.
        v = completion_surface_verdict(
            files_touched=["backend/scripts/artifact_cli.py"],
            commits=[],
            deliver_surfaced=False,
        )
        assert v.ok is False
        assert v.block_reason == "uncommitted_source"

    def test_source_committed_but_not_surfaced_BLOCKS(self):
        v = completion_surface_verdict(
            files_touched=["backend/scripts/artifact_cli.py"],
            commits=_commit(["backend/scripts/artifact_cli.py"]),
            deliver_surfaced=False,
        )
        assert v.ok is False
        assert v.block_reason == "unsurfaced_source"

    def test_source_committed_and_surfaced_OK(self):
        v = completion_surface_verdict(
            files_touched=["backend/scripts/artifact_cli.py"],
            commits=_commit(["backend/scripts/artifact_cli.py"]),
            deliver_surfaced=True,
        )
        assert v.ok is True
        assert v.block_reason is None

    def test_docs_only_run_NOT_blocked(self):
        # files_touched explicitly empty = no source work = never gated.
        v = completion_surface_verdict(files_touched=[], commits=[], deliver_surfaced=False)
        assert v.ok is True

    def test_unknown_files_touched_WARNS_not_blocks(self):
        # Legacy / in-flight resume: field absent → never hard-block.
        v = completion_surface_verdict(files_touched=None, commits=[], deliver_surfaced=False)
        assert v.ok is True
        assert v.warnings  # nudged

    def test_sibling_session_only_commits_do_NOT_satisfy_our_source(self):
        # Commits exist but for a DIFFERENT file than this run's source →
        # this run's own source was never committed → BLOCK (run-scoping).
        v = completion_surface_verdict(
            files_touched=["backend/scripts/artifact_cli.py"],
            commits=_commit(["desktop/src/other/sibling.ts"]),
            deliver_surfaced=True,
        )
        assert v.ok is False
        assert v.block_reason == "uncommitted_source"

    def test_dir_anchored_match_not_bare_basename(self):
        # files_touched carries a repo-relative path; commit carries the abs path.
        # Must match by dir-anchored suffix, and NOT collide on bare basename.
        v = completion_surface_verdict(
            files_touched=["scripts/artifact_cli.py"],
            commits=_commit(["/abs/repo/scripts/artifact_cli.py"]),
            deliver_surfaced=True,
        )
        assert v.ok is True  # matched → committed+surfaced → OK

    def test_bare_basename_does_not_falsely_match_deep_path(self):
        # A bare basename in files_touched must NOT match a deep committed path.
        v = completion_surface_verdict(
            files_touched=["config.py"],
            commits=_commit(["backend/sub/config.py"]),
            deliver_surfaced=True,
        )
        assert v.ok is False  # no real match → our source uncommitted
        assert v.block_reason == "uncommitted_source"
