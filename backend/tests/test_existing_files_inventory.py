"""The file inventory the DESIGN_SPEC checks read (engine.check_context().existing_files)
is the run's computed code tree (review of 31e50759a): it includes a new file the agent
has not `git add`ed yet, and a non-ASCII or spaced file name exactly as written (git's
default quoting turned `测试_test.go` into an octal-escaped, quoted string).

Methodology: the real CLI rig (tests/pipeline_v1_rig.py), an isolated direct run in
BUILD, files written into its worktree untracked."""
from __future__ import annotations

import pytest

from tests.pipeline_v1_rig import Rig

pytestmark = pytest.mark.timeout(300)


def test_existing_files_are_the_code_tree_untracked_and_unicode_included(tmp_path, monkeypatch):
    rig = Rig(tmp_path / "case", monkeypatch, profile="direct")
    try:
        rig.create()
        rig.drive(until="build")
        rig.edit({"pkg/测试_test.go": "package pkg\n", "web/my tests/a.spec.ts": "x\n"})
        existing = rig.engine().check_context().existing_files
        assert "pkg/测试_test.go" in existing, sorted(existing)[:20]
        assert "web/my tests/a.spec.ts" in existing
    finally:
        rig.close()
