"""The Project Agent standard: what every Brain's definition holds, and how it is written
(design docs/design/2026-11-01-project-agent-simple.md).

A definition holds only what a Hive run reads: ``identity.summary``, ``intake`` (the
repos, and the typical requests routing compares a task with), the modes (``when``,
``repos``, ``setup``, ``validations``, ``skills``, ``gates``), ``gates.enforce``,
``watches`` and ``default_mode``. Nothing in it is confirmed.

- ``MODES``: the standard modes, for which shape of Brain and their ``when``. A Brain
  "has a repo" when its ``bindings.yaml`` binds one (``project_agent._bindings``).
- ``standard_modes(has_repo)`` / ``default_mode(has_repo)``: a draft's mode skeleton.
- ``PROPOSAL_KEYS`` / ``MODE_PROPOSAL_KEYS``: what the drafting agent may propose
  (``project_agent_draft.PROPOSAL_CONTRACT``).
- ``ordered(doc)``: the key order a written definition uses; keys no longer read are
  dropped (``RETIRED_KEYS``), so an older file loses them on its next write.
- ``HEADER``: the comment every definition file starts with.
- ``is_skeleton(doc)``: a definition code wrote that no one has drafted yet.
"""
from __future__ import annotations

import copy
from typing import Any

#: the standard modes. ``for``: every Brain, a Brain with a repo, or one without. The
#: mode inside a Brain is chosen by matching these ``when`` texts (core/task_spec), so the
#: modes of one shape never overlap: without a repo, research answers in the conversation
#: and write delivers a document; with a repo there is no write and a design is research.
MODES: dict[str, dict[str, Any]] = {
    "code-change": {"for": "repo", "when": "a change to the code of a bound repo"},
    "research": {"for": "every",
                 "when": {"repo": "a question, an investigation or a design, with no change to code",
                          "no-repo": "a question or an investigation, answered in the conversation"}},
    "write": {"for": "no-repo", "when": "a document or a report as the deliverable"},
}

#: the top-level keys the drafting agent may propose, in the order they are written
PROPOSAL_KEYS: dict[str, Any] = {
    "identity": {"summary": "one sentence: what the work in this Brain usually is", "source": "str"},
    "handles": [{"text": "a typical request, one sentence (routing compares a task with these)",
                 "source": "str"}],
    "watches": [{"area": "str", "why": "what every review of work here checks", "source": "str"}],
    "gates": ["names from facts.gates to enforce; omitted = all of them"],
    "default_mode": "a mode id",
}
#: the keys of one proposed mode
MODE_PROPOSAL_KEYS: dict[str, Any] = {
    "when": "the kind of intent this mode handles (a new `when` needs a source)",
    "repos": ["names from facts.repos (new modes only; code-change has them all)"],
    "tools": ["{tool: '<tool>@<version>' (python@3.11, node@22, bun@1.4.4, rust@stable), source}: "
              "a tool the mode's setup and checks need that a CI setup action or a version file "
              "of the repo names (setup-python, setup-node, .nvmrc, rust-toolchain.toml, ...)"],
    "setup": ["a facts.commands id, or {command, source, repo, cwd}"],
    "validations": ["a facts.commands id, or {id, command, source, repo, cwd}"],
    "skills": ["names from facts.skills; a skill no mode lists goes to the default mode"],
}

SHARED_ORDER: tuple[str, ...] = ("schema", "identity", "intake", "modes", "default_mode", "gates", "watches")
IDENTITY_ORDER: tuple[str, ...] = ("summary", "source")
INTAKE_ORDER: tuple[str, ...] = ("handles", "repos")
MODE_ORDER: tuple[str, ...] = ("id", "when", "repos", "tools", "setup", "validations", "skills", "gates", "source")

#: keys a definition no longer has (design 2026-11-01-project-agent-simple §2): nothing
#: reads them, and every write drops them
RETIRED_KEYS: tuple[str, ...] = (
    "landing", "drafted_at", "status", "reviewed_at", "reviewed_by", "reserved_for_human",
    "placements", "confidence", "sources")
_RETIRED_IDENTITY = ("in_scope", "out_of_scope")
_RETIRED_INTAKE = ("proposed_aliases",)
RETIRED_MODE_KEYS: tuple[str, ...] = ("placements", "delivery", "pr_remote", "reserved_for_human",
                                     "profile", "profile_rules")

HEADER = ("# Project Agent definition: how a Hive run does this Brain's work.\n"
          "# The agent keeps it current; a dispatch uses the latest. Nothing in it is confirmed.\n"
          "# Fields: templates/project-agents/STANDARD.md.\n")


def _applies(spec: dict[str, Any], has_repo: bool) -> bool:
    return spec["for"] == "every" or (spec["for"] == "repo") == has_repo


def standard_when(mode_id: str, has_repo: bool) -> str:
    when = MODES[mode_id]["when"]
    return when if isinstance(when, str) else when["repo" if has_repo else "no-repo"]


def standard_modes(has_repo: bool) -> list[dict[str, Any]]:
    """The standard modes of a Brain of this shape, in order (code-change first when
    there is a repo: it is the default mode then)."""
    return [{"id": mid, "when": standard_when(mid, has_repo)}
            for mid, spec in MODES.items() if _applies(spec, has_repo)]


def default_mode(has_repo: bool) -> str:
    return "code-change" if has_repo else "research"


def _order(d: dict[str, Any], keys: tuple[str, ...], drop: tuple[str, ...] = ()) -> dict[str, Any]:
    out = {k: d[k] for k in keys if k in d}
    out.update({k: v for k, v in d.items() if k not in out and k not in drop})
    return out


def ordered(doc: dict[str, Any]) -> dict[str, Any]:
    """A copy of *doc* with its keys (and those of identity, intake and each mode) in the
    standard order and retired keys dropped; keys the standard does not name keep their
    order, after the rest."""
    d = _order(copy.deepcopy(doc), SHARED_ORDER, RETIRED_KEYS)
    if isinstance(d.get("identity"), dict):
        d["identity"] = _order(d["identity"], IDENTITY_ORDER, _RETIRED_IDENTITY)
    if isinstance(d.get("intake"), dict):
        d["intake"] = _order(d["intake"], INTAKE_ORDER, _RETIRED_INTAKE)
    if isinstance(d.get("modes"), list):
        d["modes"] = [_order(m, MODE_ORDER, RETIRED_MODE_KEYS) if isinstance(m, dict) else m for m in d["modes"]]
    return d


def is_skeleton(doc: dict[str, Any]) -> bool:
    """A definition not drafted yet: it lacks a summary or typical requests, so a Hive
    run gets no project line and routing has nothing to compare a task with.
    ``core.project_agent.dispatch_problems`` reports it (P6 and admission alike)."""
    ident = doc.get("identity") if isinstance(doc.get("identity"), dict) else {}
    intake = doc.get("intake") if isinstance(doc.get("intake"), dict) else {}
    has_summary = isinstance(ident.get("summary"), str) and bool(ident["summary"].strip())
    handles = intake.get("handles")
    has_handles = isinstance(handles, list) and any(isinstance(h, str) and h.strip() for h in handles)
    return not has_summary or not has_handles
