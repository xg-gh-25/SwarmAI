"""Forced-execution tests for scripts/lint_experts.py — the D3 admission gate.

RP47 defense: every test DRIVES the real linter process end-to-end (subprocess),
never a mock. The fail-closed paths (bad expert → exit 1, enumeration failure →
exit 1) are mutation-proven: a weakened gate makes them GREEN-when-they-should-be-RED.

The linter enumerates via `git ls-files`, so a crafted-bad-expert test cannot use a
tmp dir (untracked → invisible to git). Instead the bad-expert tests invoke
`lint_expert()` DIRECTLY on a tmp file (unit level), and the whole-process tests run
the real binary against the real tracked tree (exit 0) and the forced-failure seam
(exit 1).
"""
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LINT = _REPO_ROOT / "scripts" / "lint_experts.py"

# Import lint_expert() directly for unit-level mutation proofs on crafted files.
sys.path.insert(0, str(_REPO_ROOT / "scripts"))
import lint_experts  # noqa: E402


def _write_expert(tmp_path: Path, name: str, fields: dict) -> Path:
    fm = "---\n" + "".join(f"{k}: {v}\n" for k, v in fields.items()) + "---\n\nbody\n"
    p = tmp_path / f"{name}.md"
    p.write_text(fm)
    return p


_GOOD = {
    "expert": "x", "display_name_zh": "x", "display_name_en": "X",
    "domain": "code", "scope": "s", "visibility": "public",
}


def test_good_expert_passes(tmp_path):
    """A complete expert file produces zero findings."""
    p = _write_expert(tmp_path, "good", _GOOD)
    assert lint_experts.lint_expert(p) == []


@pytest.mark.parametrize("missing", ["domain", "scope"])
def test_missing_required_field_is_rejected(tmp_path, missing):
    """MUTATION PROOF: dropping any D3 required field makes the linter flag it.

    Reverting the REQUIRED_FIELDS check in production would make these GREEN.
    """
    fields = {k: v for k, v in _GOOD.items() if k != missing}
    p = _write_expert(tmp_path, "bad", fields)
    findings = lint_experts.lint_expert(p)
    assert findings, f"linter MISSED a '{missing}'-less expert (fail-open!)"
    assert any(missing in f for f in findings)


@pytest.mark.parametrize("mem_key", ["memory", "memory_store", "persistent_state"])
def test_declared_memory_is_rejected(tmp_path, mem_key):
    """An expert declaring persistent memory is a crewmate → rejected (D3 crit 2)."""
    fields = dict(_GOOD)
    fields[mem_key] = "somevalue"
    p = _write_expert(tmp_path, "crewmate", fields)
    findings = lint_experts.lint_expert(p)
    assert findings, f"linter MISSED a '{mem_key}' declaration (fail-open!)"
    assert any(mem_key in f for f in findings)


def test_core_stance_domain_rule_relaxed_but_scope_still_required(tmp_path):
    """_core is exempt from the 'no universal expert' domain rule, but still needs
    a scope, and still must not declare memory."""
    # _core WITH scope → clean
    ok = _write_expert(tmp_path, "stance", {
        "expert": "_s", "display_name_zh": "x", "display_name_en": "X",
        "domain": "_core", "scope": "shared stance",
        "visibility": "internal",
    })
    assert lint_experts.lint_expert(ok) == []
    # _core MISSING scope → still rejected
    bad = _write_expert(tmp_path, "stance2", {
        "expert": "_s", "display_name_zh": "x", "display_name_en": "X",
        "domain": "_core", "visibility": "internal",
    })
    assert any("scope" in f for f in lint_experts.lint_expert(bad))


def test_no_frontmatter_is_rejected(tmp_path):
    """A plain .md with no frontmatter is not an admissible expert."""
    p = tmp_path / "plain.md"
    p.write_text("# just a title\n\nno frontmatter here\n")
    findings = lint_experts.lint_expert(p)
    assert findings and "frontmatter" in findings[0]


# ---- whole-process tests (the real binary, end-to-end) --------------------

def _run_lint(env_extra=None):
    import os
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(_LINT)],
        capture_output=True, text=True, cwd=str(_REPO_ROOT), env=env, timeout=90,
    )


def test_real_tree_exits_zero():
    """The real tracked backend/experts/ tree passes (exit 0)."""
    r = _run_lint()
    assert r.returncode == 0, (
        f"lint_experts.py failed on the real tree:\nstdout={r.stdout}\nstderr={r.stderr}"
    )


def test_enumeration_failure_fails_closed():
    """MUTATION PROOF of the fail-closed path: a git enumeration failure exits 1,
    it does NOT report a clean corpus it never read."""
    r = _run_lint({"SWARM_LINT_EXPERTS_FORCE_ENUM_FAILURE": "1"})
    assert r.returncode == 1, "enumeration failure did not fail closed"
    assert "could not run" in r.stdout.lower() or "could not run" in r.stderr.lower()
