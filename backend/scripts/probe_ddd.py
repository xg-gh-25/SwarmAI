#!/usr/bin/env python3
"""probe_ddd — product eval probes for project brains (DDDs).

Contract: scripts/_probe_kit.py. Subcommands:
  skeleton   a new project gets the full six-section structure (identity files,
             the four knowledge docs, the section folders, a filled-in AGENTS.md)
             and the three default skills; creating the same name again is refused
  noise      the noise check tells a healthy doc from one full of stale notes,
             and does not count dormant principles as noise
  cultivate  a session lesson is handed to CDF with a stable slot and project
             snapshot, and no governed project file is written directly
  bootstrap  a new brain's first entries (the fixture repo repos/tokensign, surveyed,
             then a fixed entries file) are written typed, tainted and in their
             sections, the P6 knowledge check passes, an entry without its required
             field, with a source that is not in the repo, or a gap outside What to
             Watch For is refused, and a second run writes nothing
"""
from __future__ import annotations

import contextlib
import io
import json
from argparse import Namespace
from unittest import mock

from core.ddd_paths import ddd_write_path
from scripts._probe_kit import arun, run, tempdir, verdict


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


# ── noise ─────────────────────────────────────────────────────────────────────

def _noise_verdict(text: str) -> str:
    from scripts.artifact_cli import cmd_ddd_noise

    with tempdir() as d:
        doc = ddd_write_path(d, "IMPROVEMENT.md")
        doc.write_text(text, encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cmd_ddd_noise(Namespace(doc=str(doc), project=None), None)
    return out.getvalue()


def _entries(kind: str, state: str, n: int, ref: int, date: str) -> str:
    return "".join(f"- [{kind}] **{state} {kind} {i}** body text. ({date})\n"
                   f"  <!-- ref:{ref} | last:none | decay:{state} | source:manual -->\n"
                   for i in range(n))


HEALTHY = "## Lessons\n\n" + _entries("guideline", "active", 4, 3, "2026-10-20")
NOISY = "## Lessons\n\n" + _entries("guideline", "dormant", 4, 0, "2020-01-01")
DORMANT_PRINCIPLES = "## Principles\n\n" + _entries("principle", "dormant", 4, 0, "2020-01-01")


def _noise_holds(healthy: str, noisy: str) -> tuple[bool, str]:
    for label, text, want in (("healthy doc", healthy, "PASS"),
                              ("dormant principles", DORMANT_PRINCIPLES, "PASS"),
                              ("stale doc", noisy, "FAIL")):
        got = _noise_verdict(text)
        if f'"verdict": "{want}"' not in got:
            return False, f"{label}: expected {want}, got {got[:120]!r}"
    return True, ""


def noise(negative: bool) -> int:
    return verdict("DDD_NOISE", negative, lambda: _noise_holds(HEALTHY, NOISY),
                   lambda: _noise_holds(HEALTHY, HEALTHY))  # a "stale" doc that is not


# ── cultivate ─────────────────────────────────────────────────────────────────

def _cultivate_holds(accept: bool) -> tuple[bool, str]:
    from core.ddd_cultivation import CultivationProposal, apply_to_ddd
    from core.ingestion.api import EnqueueResult

    with tempdir() as d:
        project = d / "Projects" / "Demo"
        doc = ddd_write_path(project, "IMPROVEMENT.md")
        doc.write_text("# Improvement\n\n## What Worked\n\n", encoding="utf-8")
        outside = d / "AGENT.md"
        outside.write_text("# outside the project\n", encoding="utf-8")
        before_doc, before_outside = doc.read_bytes(), outside.read_bytes()
        calls = []

        def enqueue(*args, **kwargs):
            calls.append((args, kwargs))
            return EnqueueResult(status="queued" if accept else "rejected",
                                 candidate_id="cand" if accept else None)

        lesson = "Write config files through an atomic rename so readers never see torn state."
        proposal = CultivationProposal(
            target_doc="IMPROVEMENT.md", target_section="What Worked",
            content=lesson, source_run_id="probe", confidence=0.9)
        with mock.patch("core.ingestion.api.enqueue", side_effect=enqueue):
            first = apply_to_ddd(proposal, project)
            second = apply_to_ddd(proposal, project)

        if first != "queued" or second != "queued" or len(calls) != 2:
            return False, "the compatibility entry point did not enqueue both attempts"
        if calls[0][0][2:] != calls[1][0][2:]:
            return False, "a replay changed its semantic slot or payload"
        if calls[0][1]["project_snapshot"].get("project") != "Demo":
            return False, "the candidate lost its project snapshot"
        if doc.read_bytes() != before_doc or outside.read_bytes() != before_outside:
            return False, "apply_to_ddd mutated a governed file directly"
    return True, ""


def cultivate(negative: bool) -> int:
    return verdict("DDD_CULTIVATE", negative,
                   lambda: _cultivate_holds(True),
                   lambda: _cultivate_holds(False))


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
     for s in ("What Makes It Different", "Strategic Priorities", "Success Criteria")]
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
        want_written = {"PRODUCT.md": 2, "TECH.md": 3, "IMPROVEMENT.md": 3}
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
        for key in ("② Knowledge/structure", "② Knowledge/content"):
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
    raise SystemExit(run({"skeleton": skeleton, "noise": noise, "cultivate": cultivate,
                          "bootstrap": bootstrap}))
