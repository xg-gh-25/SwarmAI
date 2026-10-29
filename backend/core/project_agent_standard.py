"""The Project Agent standard: how a definition is written by default (design
docs/design/2026-10-29-default-brains-and-project-agent-standard.md §4).

One source for what the draft generator (core/project_agent_draft), the two definitions
shipped with the product (templates/project-agents/) and the review card's remarks
(``project_agent.notes``) share. The field guide for people is
templates/project-agents/STANDARD.md.

What a standard is NOT: a validation rule. ``project_agent.validate`` decides what is
wrong; this says what a draft looks like, and ``deviations`` lists where a definition
leaves it as advisory remarks a person may keep on purpose.

- ``SHARED_ORDER`` / ``MODE_ORDER``: the order of a definition's keys and of a mode's.
- ``MODES``: the standard modes, for which shape of Brain, their ``when`` and profile.
  A Brain "has a repo" when its ``bindings.yaml`` binds one (``project_agent._bindings``,
  the same test the generator uses).
- ``standard_modes(has_repo)`` / ``default_mode(has_repo)``: a draft's mode skeleton.
- ``ordered(doc)``: the definition with its keys in the standard order.
- ``HEADER``: the comment every definition file starts with.
- ``deviations(doc, has_repo)``: where a definition leaves the standard: a person removed
  something, or the model proposed no summary or handles.
"""
from __future__ import annotations

import copy
from typing import Any

from core.project_agent import RESERVED_ACTIONS

SHARED_ORDER: tuple[str, ...] = (
    "schema", "status", "reviewed_at", "reviewed_by", "drafted_at",
    "identity", "intake", "confidence", "watches", "gates",
    "reserved_for_human", "placements", "landing",
    "modes", "default_mode", "sources")
IDENTITY_ORDER: tuple[str, ...] = ("summary", "in_scope", "out_of_scope", "source")
INTAKE_ORDER: tuple[str, ...] = ("handles", "repos", "proposed_aliases")
MODE_ORDER: tuple[str, ...] = (
    "id", "when", "profile", "profile_rules", "placements", "repos", "setup",
    "validations", "skills", "gates", "reserved_for_human", "delivery")

#: the actions a draft reserves for a person: all the product knows
DEFAULT_RESERVED: tuple[str, ...] = tuple(sorted(RESERVED_ACTIONS))

#: the standard modes. ``for``: every Brain, a Brain with a repo, or one without. The
#: mode inside a Brain is chosen by matching these ``when`` texts (core/task_spec), so the
#: modes of one shape never overlap: without a repo, research answers in the conversation
#: and write delivers a document; with a repo there is no write and a design is research.
MODES: dict[str, dict[str, Any]] = {
    "code-change": {"for": "repo", "profile": "bugfix", "delivery": "bundle",
                    "when": "a change to the code of a bound repo"},
    "research": {"for": "every", "profile": "research",
                 "when": {"repo": "a question, an investigation or a design, with no change to code",
                          "no-repo": "a question or an investigation, answered in the conversation"}},
    "write": {"for": "no-repo", "profile": "direct",
              "when": "a document or a report as the deliverable"},
}

HEADER = ("# Project Agent definition: how this Brain's work is taken in, done and delivered.\n"
          "# A draft until a person confirms it on the Brain Hub review card.\n"
          "# Fields and defaults: the Project Agent standard, templates/project-agents/STANDARD.md.\n")


def _applies(spec: dict[str, Any], has_repo: bool) -> bool:
    return spec["for"] == "every" or (spec["for"] == "repo") == has_repo


def standard_when(mode_id: str, has_repo: bool) -> str:
    when = MODES[mode_id]["when"]
    return when if isinstance(when, str) else when["repo" if has_repo else "no-repo"]


def standard_modes(has_repo: bool) -> list[dict[str, Any]]:
    """The standard modes of a Brain of this shape, in order (code-change first when
    there is a repo: it is the default mode then)."""
    out = []
    for mid, spec in MODES.items():
        if _applies(spec, has_repo):
            m = {"id": mid, "when": standard_when(mid, has_repo), "profile": spec["profile"]}
            if "delivery" in spec:
                m["delivery"] = spec["delivery"]
            out.append(m)
    return out


def default_mode(has_repo: bool) -> str:
    return "code-change" if has_repo else "research"


def _order(d: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    out = {k: d[k] for k in keys if k in d}
    out.update({k: v for k, v in d.items() if k not in out})
    return out


def ordered(doc: dict[str, Any]) -> dict[str, Any]:
    """A copy of *doc* with its keys (and those of identity, intake and each mode) in the
    standard order; keys the standard does not name keep their order, after the rest."""
    d = _order(copy.deepcopy(doc), SHARED_ORDER)
    if isinstance(d.get("identity"), dict):
        d["identity"] = _order(d["identity"], IDENTITY_ORDER)
    if isinstance(d.get("intake"), dict):
        d["intake"] = _order(d["intake"], INTAKE_ORDER)
    if isinstance(d.get("modes"), list):
        d["modes"] = [_order(m, MODE_ORDER) if isinstance(m, dict) else m for m in d["modes"]]
    return d


def deviations(doc: dict[str, Any], has_repo: bool) -> list[str]:
    """Where a (normalized, schema 2) definition leaves the standard: a missing summary or
    handles (the model proposed none, or a person removed them), fewer reserved actions
    than the default, a repo without a code-change mode. Not reported: a changed profile
    of a standard mode, which the generator itself allows from a sourced proposal.
    Advisory, never a problem."""
    out: list[str] = []
    ident = doc.get("identity") if isinstance(doc.get("identity"), dict) else {}
    if not (isinstance(ident.get("summary"), str) and ident["summary"].strip()):
        out.append("standard: identity.summary is missing (routing and the review card show it)")
    intake = doc.get("intake") if isinstance(doc.get("intake"), dict) else {}
    handles = intake.get("handles")
    if not (isinstance(handles, list) and any(isinstance(h, str) and h.strip() for h in handles)):
        out.append("standard: intake.handles is missing (routing compares a task with them)")
    reserved = doc.get("reserved_for_human")
    have = set(reserved) if isinstance(reserved, list) else set()
    gone = [a for a in DEFAULT_RESERVED if a not in have]
    if gone:
        out.append(f"standard: no longer reserved for a person: {', '.join(gone)}")
    modes = doc.get("modes") if isinstance(doc.get("modes"), list) else []
    if has_repo and not any(isinstance(m, dict) and m.get("id") == "code-change" for m in modes):
        out.append("standard: this Brain has a bound repo but no code-change mode")
    return out
