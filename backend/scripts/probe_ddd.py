#!/usr/bin/env python3
"""probe_ddd — product eval probes for project brains (DDDs).

Contract: scripts/_probe_kit.py. Subcommands:
  skeleton   a new project gets the full six-section structure (identity files,
             the four knowledge docs, the section folders, a filled-in AGENTS.md)
             and the three default skills; creating the same name again is refused
  bootstrap  a new brain's first entries (the fixture repo repos/tokensign, surveyed,
             then a fixed entries file) are written typed, tainted and in their
             sections, the P6 knowledge check passes, an entry without its required
             field, with a source that is not in the repo, or a gap outside What to
             Watch For is refused, and a second run writes nothing
"""
from __future__ import annotations

import contextlib
import json
from unittest import mock

from scripts._probe_kit import arun, run, tempdir, verdict  # first: it isolates the data dir
from core.ddd_paths import ddd_write_path


# ── skeleton ──────────────────────────────────────────────────────────────────

def _skeleton_holds(scaffold) -> tuple[bool, str]:
    from core.swarm_workspace_manager import (DDD_NATIVE_SKILLS, SECTION_DIRS, SECTION_SCAFFOLD,
                                              SwarmWorkspaceManager)

    with tempdir() as d:
        ws = d / "ws"
        (ws / "Projects").mkdir(parents=True)
        with scaffold:
            arun(SwarmWorkspaceManager().create_project(project_name="ProbeProj",
                                                         workspace_path=str(ws)))
        p = ws / "Projects" / "ProbeProj"
        missing = [r for r in SECTION_SCAFFOLD if not (p / r).is_file() or not (p / r).read_text().strip()]
        missing += [r for r in SECTION_DIRS if not (p / r).is_dir()]
        if missing:
            return False, f"the new project is missing {missing}"
        agents = (p / "AGENTS.md").read_text(encoding="utf-8")
        if "{project_name}" in agents or "ProbeProj" not in agents:
            return False, "AGENTS.md was not filled in for the project"
        json.loads((p / "aim.json").read_text(encoding="utf-8"))
        skills = [s for s in DDD_NATIVE_SKILLS if not list(p.rglob(f"{s}/SKILL.md"))]
        if skills:
            return False, f"the new project is missing its default skills {skills}"
        try:
            arun(SwarmWorkspaceManager().create_project(project_name="ProbeProj",
                                                         workspace_path=str(ws)))
            return False, "a second project with the same name was created"
        except ValueError:
            pass
    return True, ""


def skeleton(negative: bool) -> int:
    from core import swarm_workspace_manager as swm

    broken = mock.patch.object(swm, "SECTION_SCAFFOLD", type(swm.SECTION_SCAFFOLD)())  # scaffolds nothing
    return verdict("DDD_SKELETON", negative,
                   lambda: _skeleton_holds(contextlib.nullcontext()),
                   lambda: _skeleton_holds_broken(broken))


def _skeleton_holds_broken(patch) -> tuple[bool, str]:
    # The check reads SECTION_SCAFFOLD itself, so compare against the real list
    # while create_project runs with an empty one.
    from core.swarm_workspace_manager import SECTION_SCAFFOLD, SwarmWorkspaceManager

    real = dict(SECTION_SCAFFOLD) if isinstance(SECTION_SCAFFOLD, dict) else list(SECTION_SCAFFOLD)
    with tempdir() as d:
        ws = d / "ws"
        (ws / "Projects").mkdir(parents=True)
        with patch:
            arun(SwarmWorkspaceManager().create_project(project_name="ProbeProj",
                                                         workspace_path=str(ws)))
        p = ws / "Projects" / "ProbeProj"
        missing = [r for r in real if not (p / r).is_file()]
    return (not missing), f"missing {missing}"


# ── bootstrap ─────────────────────────────────────────────────────────────────

_GOOD = [
    {"kind": "cognition", "type": "principle", "section": "Vision",
     "title": "A signed value can be trusted after a round trip through an untrusted channel",
     "what": "loads() refuses a value anyone changed.", "sources": ["README.md#tokensign"]},
    {"kind": "cognition", "type": "pitfall", "section": "Non-Goals",
     "title": "Signing is not encryption", "what": "A signed value is readable by anyone.",
     "applicability": "putting a secret in a token", "sources": ["README.md"]},
    {"kind": "cognition", "type": "pitfall", "section": "Runtime Traps",
     "title": "Changing the digest invalidates every token signed before",
     "what": "Old tokens fail verification unless the old digest is in fallback_digests.",
     "applicability": "configuring or upgrading the digest", "sources": ["docs/design.md#Digest"]},
    {"kind": "cognition", "type": "process", "section": "Conventions",
     "title": "Run the tests with make test", "what": "make test runs pytest.",
     "how": "make test", "sources": ["Makefile", "CONTRIBUTING.md"]},
    {"kind": "cognition", "type": "model", "section": "Key Subsystems",
     "title": "Signer and TimedSigner", "what": "HMAC signing; TimedSigner adds max_age.",
     "sources": ["src/tokensign/signer.py", "src/tokensign/timed.py:1"]},
] + [{"kind": "gap", "section": "What to Watch For", "title": f"{s}: no evidence yet",
      "what": "The repo says nothing about it."}
     # P2d: every PRODUCT and TECH section left without an entry gets its gap
     # (core.ddd_skeleton.GAP_DOCS); Architecture's only entry is a refused one below
     for s in ("What Makes It Different", "Strategic Priorities", "Success Criteria", "Architecture")]
_BAD = [  # (entry, the reason bootstrap-apply must give)
    ({"kind": "cognition", "type": "decision", "section": "Architecture",
      "title": "HMAC is the signing primitive", "what": "Signatures are HMAC.",
      "why": "symmetric keys", "sources": ["docs/design.md"]}, "rejected_alternatives"),
    ({"kind": "cognition", "type": "model", "section": "Key Subsystems",
      "title": "The serializer", "what": "Wraps json.", "sources": ["src/tokensign/serializer.py"]},
     "not a tracked file"),
    ({"kind": "gap", "section": "Vision", "title": "Vision: unclear", "what": "w"}, "a gap goes to"),
]


def _bootstrap_holds() -> tuple[bool, str]:
    import shutil
    import sys
    from core.brain_bootstrap import apply as ap
    from core.brain_bootstrap import survey
    from core.ddd_entry_lifecycle import parse_entries
    from core.ddd_paths import ddd_path
    from core.ddd_setup_state import write_setup_state
    from core.swarm_workspace_manager import SwarmWorkspaceManager
    from utils.backend_bin import bundled_path

    entries = _GOOD + [e for e, _ in _BAD]
    with tempdir() as d:
        repo = d / "tokensign"
        shutil.copytree(bundled_path("templates", "eval", "fixtures", "workspace", "repos", "tokensign"), repo)
        (d / "ws" / "Projects" / "Tokensign").mkdir(parents=True)
        arun(SwarmWorkspaceManager().provision_project_ddd("Tokensign", workspace_path=str(d / "ws")))
        proj = d / "ws" / "Projects" / "Tokensign"
        write_setup_state(proj, "setting-up")
        owned = [{"name": "tokensign", "root": str(repo.resolve()), "db_path": None}]
        with mock.patch.object(survey, "owned_repos", lambda _p: owned):
            if not survey.survey_project(proj)["repos"]:
                return False, "the survey found no repo"
            (proj / ap.ENTRIES_FILE).write_text(json.dumps(entries), encoding="utf-8")
            report = ap.apply_entries(proj)
            again = ap.apply_entries(proj)
        got = {r["index"]: r["reason"] for r in report.get("rejected", [])}
        want = {len(_GOOD) + i: why for i, (_, why) in enumerate(_BAD)}
        if set(got) != set(want) or any(want[i] not in got[i] for i in want):
            return False, f"refused {got}, expected the {len(_BAD)} bad entries ({list(want.values())})"
        if report.get("refused_docs"):
            return False, f"a newborn brain's docs were refused: {report['refused_docs']}"
        want_written = {"PRODUCT.md": 2, "TECH.md": 3, "IMPROVEMENT.md": 4}
        if report.get("written") != want_written:
            return False, f"written {report.get('written')}, expected {want_written}"
        placed = {(e.section, e.entry_type, e.taint)
                  for doc in want_written
                  for e in parse_entries(ddd_path(proj, doc).read_text(encoding="utf-8"))}
        want_placed = {("Vision", "principle", "external"), ("Non-Goals", "pitfall", "external"),
                       ("Runtime Traps", "pitfall", "external"), ("Conventions", "process", "external"),
                       ("Key Subsystems", "model", "external"), ("What to Watch For", "guideline", "")}
        if placed != want_placed:
            return False, f"entries read back as {sorted(placed)}, expected {sorted(want_placed)}"
        if again.get("written") or again.get("already_present") != len(_GOOD):
            return False, f"a second run wrote again: {again.get('written')}"
        sys.path.insert(0, str(bundled_path("skills", "s_project-manager", "scripts")))
        import verify_ddd_complete as p6
        checks = {c[0]: (c[1], c[2]) for c in p6._check_knowledge(proj)}
        for key in ("② Knowledge/structure", "② Knowledge/content", "② Knowledge/format"):
            if checks.get(key, ("missing",))[0] != "PASS":
                return False, f"P6 {key}: {checks.get(key)}"
    return True, ""


def bootstrap(negative: bool) -> int:
    from core.brain_bootstrap import apply as ap

    def broken():  # the rules switched off: no required fields, any source accepted
        with mock.patch.object(ap, "_REQUIRED", {}), \
                mock.patch.object(ap.Evidence, "check", lambda self, s: True):
            return _bootstrap_holds()
    return verdict("DDD_BOOTSTRAP", negative, _bootstrap_holds, broken)


if __name__ == "__main__":
    raise SystemExit(run({"skeleton": skeleton, "bootstrap": bootstrap}))
