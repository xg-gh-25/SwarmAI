"""Tests for pipeline_pr default-branch resolution (run_66d5c01e de-SwarmAI-ize).

The PR creator must NOT assume the base branch is 'main' — a project on
master/develop would get a PR against the wrong base. These tests pin
`_get_default_branch` (symref → common-names fallback → None) so the fix
doesn't silently regress to hardcoded main.
"""
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pipeline_pr  # noqa: E402


def _cp(stdout="", rc=0):
    m = mock.Mock()
    m.stdout = stdout
    m.returncode = rc
    return m


class TestGetDefaultBranch:
    def test_symref_resolves_master(self):
        # origin/HEAD → master: must return 'master', NOT 'main'.
        with mock.patch.object(pipeline_pr.subprocess, "run",
                               return_value=_cp("origin/master\n", 0)):
            assert pipeline_pr._get_default_branch() == "master"

    def test_symref_resolves_main(self):
        with mock.patch.object(pipeline_pr.subprocess, "run",
                               return_value=_cp("origin/main\n", 0)):
            assert pipeline_pr._get_default_branch() == "main"

    def test_symref_bare_name_no_slash(self):
        # Defensive: a bare ref with no slash returns as-is.
        with mock.patch.object(pipeline_pr.subprocess, "run",
                               return_value=_cp("develop\n", 0)):
            assert pipeline_pr._get_default_branch() == "develop"

    def test_fallback_to_common_names_when_no_symref(self):
        # No symref (empty) → probe origin/main, origin/master, origin/develop.
        # Simulate: symref empty, then origin/main verify fails, origin/master OK.
        calls = [
            _cp("", 0),        # symbolic-ref → empty
            _cp("", 1),        # rev-parse origin/main → fail
            _cp("abc123", 0),  # rev-parse origin/master → ok
        ]
        with mock.patch.object(pipeline_pr.subprocess, "run", side_effect=calls):
            assert pipeline_pr._get_default_branch() == "master"

    def test_none_when_nothing_resolves(self):
        # symref empty + all common names fail → None (caller omits --base).
        calls = [_cp("", 0), _cp("", 1), _cp("", 1), _cp("", 1)]
        with mock.patch.object(pipeline_pr.subprocess, "run", side_effect=calls):
            assert pipeline_pr._get_default_branch() is None
