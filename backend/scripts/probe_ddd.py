#!/usr/bin/env python3
"""probe_ddd — product eval probes for project brains (DDDs).

Contract: scripts/_probe_kit.py. Subcommands:
  skeleton   a new project gets the full six-section structure (identity files,
             the four knowledge docs, the section folders, a filled-in AGENTS.md)
             and the three default skills; creating the same name again is refused
  noise      the noise check tells a healthy doc from one full of stale notes,
             and does not count dormant principles as noise
  cultivate  a session lesson written into a project lands only inside that project,
             is not written twice, and a path that escapes the project is refused
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

def _cultivate_holds(target_doc_escape: str) -> tuple[bool, str]:
    from core.ddd_cultivation import CultivationProposal, apply_to_ddd

    with tempdir() as d:
        project = d / "Projects" / "Demo"
        ddd_write_path(project, "IMPROVEMENT.md").write_text(
            "# Improvement\n\n## What Worked\n\n", encoding="utf-8")
        outside = d / "AGENT.md"
        outside.write_text("# outside the project\n", encoding="utf-8")
        lesson = "Write config files through an atomic rename so readers never see torn state."

        def prop(doc):
            return CultivationProposal(target_doc=doc, target_section="What Worked",
                                       content=lesson, source_run_id="probe", confidence=0.9)
        first = apply_to_ddd(prop("IMPROVEMENT.md"), project)
        second = apply_to_ddd(prop("IMPROVEMENT.md"), project)
        escape = apply_to_ddd(prop(target_doc_escape), project)
        if first not in ("applied", "created_section"):
            return False, f"a lesson was not written into the project ({first})"
        if second != "duplicate":
            return False, f"the same lesson was written twice ({second})"
        if escape in ("applied", "created_section") or lesson in outside.read_text(encoding="utf-8"):
            return False, f"a path outside the project was written ({escape})"
    return True, ""


def cultivate(negative: bool) -> int:
    from core import ddd_cultivation as dc

    import itertools
    counter = itertools.count()

    def broken():  # duplicate detection switched off: every signature is unique
        with mock.patch.object(dc, "content_signature", lambda line: f"sig{next(counter)}"):
            return _cultivate_holds("../../AGENT.md")
    return verdict("DDD_CULTIVATE", negative, lambda: _cultivate_holds("../../AGENT.md"), broken)


if __name__ == "__main__":
    raise SystemExit(run({"skeleton": skeleton, "noise": noise, "cultivate": cultivate}))
