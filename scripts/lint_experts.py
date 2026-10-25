#!/usr/bin/env python3
"""Lint the Expert Library corpus (``backend/experts/``) — the D3 admission gate.

An **expert** is a cross-Super-IC shared adversarial reviewer (see
``core.expert_manager``). Adding one is a system-level capability extension, so it
must pass two admission criteria (design 2026-10-16 §D3) — fail CLOSED on any:

  1. **Domain declared** — declares a non-empty ``domain`` (no "universal expert").
  2. **No persistent identity / memory** — must NOT declare ``memory`` /
     ``memory_store`` / persistent state (that would be a crewmate, not an expert).

Also requires ``scope`` (the demo介绍词 + what the reviewer covers), which the
registry and the UI both read.

Mirrors ``scripts/lint_skills.py``: reuses ``core.skill_manager.parse_frontmatter``
(the same parser the runtime + registry use, so the linter can never disagree with
them), enumerates via git (not a filesystem walk, so a vendored/untracked file can
never redden the build), and distinguishes an ABSENT scope (legit no-op for a
consumer repo) from a PRESENT-but-empty one (broken enumeration → block).

Run from repo root (CI runs it after backend deps install):
    python scripts/lint_experts.py

Exit code 0 = clean, 1 = at least one finding (or the gate could not establish scope).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# Add backend/ to sys.path so we can import core.skill_manager (same shim as lint_skills).
_BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(_BACKEND_DIR))

from core.skill_manager import SkillParseError, parse_frontmatter  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent

# The experts tree, repo-relative. Listed explicitly (not discovered) for the same
# reason lint_skills names its SKILL_DIRS: the gate is only as wide as the trees it
# reads, and that width must be reviewable, not implicit.
EXPERTS_DIR = Path("backend/experts")

# The D3 admission fields that MUST be present + non-empty (``spawn_as`` was
# removed in S6-4, design usable-by-anyone §3.5.1: nothing read it any more). A missing one is a
# fail-closed REJECTION, never a warning — the whole point of the gate is that a
# malformed expert cannot land.
REQUIRED_FIELDS = ("domain", "scope")

# Keys whose mere PRESENCE means the file declared persistent memory/state → it is a
# crewmate, not an expert (D3 criterion 2). Fail closed on any.
FORBIDDEN_MEMORY_KEYS = ("memory", "memory_store", "persistent_state", "state_store")

# The shared adversarial-stance skeleton is scaffolding, not a dispatchable expert.
# It legitimately carries domain: _core and is NOT held to the per-expert field set
# the same way (it still must declare a scope, and must NOT declare
# memory — those still apply; only the "no universal expert" domain rule is relaxed
# because _core IS the shared layer).
_CORE_DOMAIN = "_core"


class CorpusEnumerationError(RuntimeError):
    """The gate could not determine its own scope, so it must not report a verdict.

    Raised rather than swallowed: a swallowed git failure yields an empty file list,
    and an empty list reads as a clean corpus — "could not run" collapsing into
    "found nothing", the exact fail-open this gate exists to prevent.
    """


def corpus_files() -> list[Path]:
    """Every tracked expert ``.md`` under EXPERTS_DIR, enumerated by git.

    Two zero-file outcomes are deliberately different: an ABSENT EXPERTS_DIR is a
    legitimate no-op (a consumer repo need not carry experts); a PRESENT dir that
    enumerates to nothing is a broken enumeration and blocks.
    """
    root = _REPO_ROOT / EXPERTS_DIR
    if not root.exists():
        return []  # nothing in scope — a real no-op, not a blind pass

    try:
        if os.environ.get("SWARM_LINT_EXPERTS_FORCE_ENUM_FAILURE"):
            # Test seam: the fail-closed path needs a real end-to-end execution test.
            raise subprocess.SubprocessError("forced enumeration failure")
        listed = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), "ls-files", "-z", "--",
             EXPERTS_DIR.as_posix()],
            capture_output=True, text=True, timeout=60, check=True,
        ).stdout
    except (subprocess.SubprocessError, OSError) as e:
        raise CorpusEnumerationError(
            f"cannot enumerate {EXPERTS_DIR.as_posix()} via git: {e}"
        ) from e

    out: list[Path] = []
    listed_count = 0
    for rel in listed.split("\0"):
        if not rel or not rel.endswith(".md"):
            continue
        listed_count += 1
        p = _REPO_ROOT / rel
        if not p.is_file():
            raise CorpusEnumerationError(
                f"{rel} is tracked but not a readable file — the working tree does "
                f"not match the index"
            )
        out.append(p)

    if listed_count == 0:
        raise CorpusEnumerationError(
            f"{EXPERTS_DIR.as_posix()} exists but git listed no .md files under it — "
            f"the scope is present and the enumeration is broken"
        )
    return out


def lint_expert(path: Path) -> list[str]:
    """Return a finding per admission violation in one expert file (empty = clean)."""
    try:
        meta, _body = parse_frontmatter(path)
    except SkillParseError as e:
        return [f"{path}: malformed frontmatter: {e}"]
    except Exception as e:  # noqa: BLE001 — any read failure is a hard finding
        return [f"{path}: cannot read: {e}"]

    if not meta:
        return [f"{path}: no YAML frontmatter — an expert must declare "
                f"{', '.join(REQUIRED_FIELDS)}"]

    findings: list[str] = []
    is_core = str(meta.get("domain", "") or "") == _CORE_DOMAIN

    for fld in REQUIRED_FIELDS:
        # The "no universal expert" domain rule (criterion 2) is relaxed only for the
        # _core skeleton — but scope still applies to it.
        if fld == "domain" and is_core:
            continue
        if not str(meta.get(fld, "") or "").strip():
            findings.append(
                f"{path}: missing required field '{fld}' (D3 admission: an expert "
                f"must declare {', '.join(REQUIRED_FIELDS)})"
            )

    for bad in FORBIDDEN_MEMORY_KEYS:
        if bad in meta:
            findings.append(
                f"{path}: declares '{bad}' — an expert must be stateless / zero-memory "
                f"(D3 criterion 2). A file that needs persistent memory is a crewmate, "
                f"not an expert."
            )

    return findings


def main() -> int:
    try:
        files = corpus_files()
    except CorpusEnumerationError as e:
        print(f"\nexpert lint could not run: {e}")
        print("  Blocking rather than reporting a clean corpus it never read.")
        return 1

    if not files:
        # EXPERTS_DIR absent entirely — a consumer repo without experts. Clean no-op.
        print("expert lint: no backend/experts/ tree in this checkout — nothing to lint ✓")
        return 0

    all_errors: list[str] = []
    for path in files:
        all_errors.extend(lint_expert(path))

    if all_errors:
        print(f"\n{len(all_errors)} expert lint error(s):")
        for err in all_errors:
            print(f"  ✗ {err}")
        return 1

    print(f"{len(files)} expert file(s) pass D3 admission checks ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
