"""Tests for scripts/lint_problem_values.py — a (value, problem) loader's problem is never dropped.

``pipeline_validator._load_evidence_json`` returns ``(data, problem)``: ``(None, None)``
for an absent file and ``(None, reason)`` for one that exists but cannot be used.
``_load_evidence_json(...)[0] or {}`` shipped in two separate changes, the second despite
the rule being in the loader's docstring: the default turns an unusable file into an
absent one. The lint makes that shape a CI failure.

Tested: the two banned shapes per registered loader, the reads that stay legal (testing
the value for None / isinstance, keeping the problem), the scope declaration (the trees
it reads), and the current tree being clean.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LINT = _REPO_ROOT / "scripts" / "lint_problem_values.py"
sys.path.insert(0, str(_REPO_ROOT / "scripts"))
import lint_problem_values as lpv  # noqa: E402


def _findings(src: str) -> list[str]:
    return lpv.lint_source(src, "x.py")


def test_defaulting_the_value_is_refused():
    assert _findings("d = pv._load_evidence_json(p)[0] or {}\n")
    assert _findings("d = _load_evidence_json(p)[0] or []\n")


def test_discarding_the_problem_is_refused():
    assert _findings("d, _ = pv._load_evidence_json(p)\n")
    assert _findings("ok, _ = evidence_seal.check(p)\n")
    assert _findings("ok = evidence_seal.check(p)[0]\n")


def test_reads_that_keep_or_test_the_value_are_legal():
    for src in ("d, why = pv._load_evidence_json(p)\n",
                "d = pv._load_evidence_json(p)[0]\nif d is None:\n    pass\n",
                "ok = isinstance(pv._load_evidence_json(p)[0], dict)\n",
                "n = sum(1 for f in fs if pv._load_evidence_json(f)[0] is not None)\n",
                "ok, why = evidence_seal.check(p)\n",
                "x = other.check(p)[0] or {}\n",          # not a registered loader
                "x = cache.get(k)[0] or {}\n"):
        assert _findings(src) == [], src


def test_finding_names_file_and_line():
    out = lpv.lint_source("a = 1\nd = pv._load_evidence_json(p)[0] or {}\n", "backend/s.py")
    assert out and out[0].startswith("backend/s.py:2:")


def test_scope_is_declared_and_pinned():
    """A gate is only as wide as the trees it reads: pin the list itself."""
    assert lpv.SCOPE == ("backend", "scripts")


def test_unparsable_file_is_reported_not_skipped():
    out = lpv.lint_source("def x(:\n", "bad.py")
    assert out and "does not parse" in out[0]


def test_current_tree_is_clean():
    p = subprocess.run([sys.executable, str(_LINT)], cwd=_REPO_ROOT, capture_output=True,
                       text=True, timeout=60)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "file(s)" in p.stdout
