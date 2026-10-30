"""project_agent_form — the Project Agent as the Brain Hub shows it: one form, eight
sections (design docs/design/2026-10-29-project-agent-standard-template.md §3, §5, §8).

``build_form(doc, project_dir)`` walks ``project_agent_standard.TEMPLATE`` and fills
every field: a stored field from the (normalized) definition, a "from the Brain" field
from the Brain's own files, a fixed field from the product. The front end renders what
this returns and holds no field list of its own.

Each field carries what a person needs to read it: its label and purpose, who may
change it (``kind``), whether a finished draft must have it, an example for an empty
one, and the ``path`` an edit sends back (``project_agent_review.edit``). A mode's own
fields come once per mode (``values`` / ``paths`` keyed by mode id).

What the form says is what the system does: the definition's guardrails run on a Hive
(``project_agent_session.make_pin``); a chat tab runs SwarmAI's own guards. A skill or a
tool "reaches a Hive" by the same rules the sync and the dispatch use
(``brain_sync.whitelisted``, the desktop/macos skill filter of ``task_spec``).

Public: ``build_form``, ``PRODUCT_GUARDS``, ``PROFILE_FOR``.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit

import yaml

from core import project_agent_standard as std
from core.project_agent import (RESERVED_ACTIONS, _bindings, _list_of_dicts, _mode_value,
                                _modes, _rows, _skills, _str_list, gate_files, is_general_name,
                                repo_roots)

logger = logging.getLogger(__name__)

#: SwarmAI's own guards every session runs (core/hook_builder registers each)
PRODUCT_GUARDS: tuple[tuple[str, str], ...] = (
    ("Dangerous commands", "rm -rf, force-push, changing a repo's visibility: it asks you first"),
    ("Actions outside this machine", "sending, publishing, pushing: it asks you first"),
    ("Background commands", "only long-lived services may run in the background"),
    ("Broken shell syntax", "an unfinished command is refused instead of hanging"),
    ("Linking into a linked folder", "an ln that would write inside a shared folder is refused"),
)
#: what each pipeline profile is for (its stages come from run_protocol)
PROFILE_FOR: dict[str, str] = {
    "full": "a bounded feature",
    "bugfix": "a defect with a reproduction",
    "trivial": "a known pattern; still reviewed and tested",
    "direct": "your words are the spec; no requirement or design documents",
    "research": "a question, no code",
}
ACTION_LABEL: dict[str, str] = {
    "push": "Push to a remote", "open_pr": "Open a pull request",
    "publish_release": "Publish a release", "send_external_message": "Send a message outside",
}
PLACE_LABEL: dict[str, tuple[str, str, str]] = {
    "chat-tab": ("Chat tab", "local", "On this machine, in a chat tab you can watch and stop."),
    "hive": ("Hive", "cloud", "Unattended, on your own SwarmAI in the cloud. Results and lessons come "
                              "back for you to accept."),
    "slack": ("Slack", "cloud", "A request posted in this Brain's Slack channel, run on your Hive."),
}
_DESKTOP_ONLY = ("desktop", "macos")
_DESC_MAX = 160


def _home(p: Path) -> str:
    s, home = str(p), os.path.expanduser("~")
    return "~" + s[len(home):] if s == home or s.startswith(home + os.sep) else s


def _public_url(url: Optional[str]) -> Optional[str]:
    """A clone URL without any user or password in it (a token never reaches the page)."""
    if not url:
        return None
    try:
        parts = urlsplit(url)
        port = parts.port               # raises on a port that is not a number
    except ValueError:
        return None
    if parts.scheme in ("http", "https", "ssh") and parts.hostname:
        netloc = parts.hostname + (f":{port}" if port else "")
        return urlunsplit((parts.scheme, netloc, parts.path, "", ""))
    return url if "@" not in url and "://" not in url else None


def _skill_description(skill_md: Path) -> str:
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError:
        return ""
    m = re.match(r"\A---\n(.*?)\n---", text, re.S)
    if not m:
        return ""
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return ""
    desc = " ".join(str(meta.get("description") or "").split()) if isinstance(meta, dict) else ""
    first = re.split(r"(?<=[.!?])\s|\s(?:TRIGGER|NOT FOR|DO NOT USE):", desc)[0].strip()
    return first if len(first) <= _DESC_MAX else first[:_DESC_MAX].rsplit(" ", 1)[0] + " …"


def _aim(project_dir: Path) -> dict[str, Any]:
    try:
        data = json.loads((project_dir / "aim.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    plugins = data.get("plugins") if isinstance(data, dict) else None
    return plugins if isinstance(plugins, dict) else {}


def _repos(project_dir: Path) -> list[dict[str, Any]]:
    roots = repo_roots(project_dir)
    return [{"name": name, "local": _home(roots[name]) if name in roots else None,
             "remote": _public_url(getattr(b, "clone", None))}
            for name, b in _bindings(project_dir).items()]


def _skill_rows(project_dir: Path, d: dict[str, Any]) -> list[dict[str, Any]]:
    from core.ddd_paths import ddd_path
    skills, _err = _skills(project_dir)
    used: dict[str, list[str]] = {}
    for m in _modes(d):
        for s in _str_list(m.get("skills")):
            used.setdefault(s, []).append(str(m.get("id")))
    root = ddd_path(project_dir, "capabilities")
    rows = []
    for name, platform in sorted((skills or {}).items()):
        rows.append({"name": name, "description": _skill_description(root / name / "SKILL.md"),
                     "modes": used.get(name, []), "builtin": False,
                     "hive": "no: needs this machine" if platform in _DESKTOP_ONLY else "yes"})
    for name in _str_list(_aim(project_dir).get("native_skills")):
        if name not in (skills or {}):
            rows.append({"name": name, "description": _skill_description(root / name / "SKILL.md"),
                         "modes": [], "builtin": True, "hive": "the Hive's own copy"})
    return rows


def _tool_rows(project_dir: Path, general: bool) -> list[dict[str, Any]]:
    from core.brain_sync import whitelisted
    plugins = _aim(project_dir)
    rows = []
    for path in _str_list(plugins.get("domain_tools")):
        rows.append({"name": path, "kind": "script",
                     "hive": "yes" if whitelisted(path, general) else "no: not synced to a Hive"})
    for m in _list_of_dicts(plugins.get("mcp")):
        rows.append({"name": str(m.get("name") or ""), "kind": "MCP server",
                     "hive": "no: set up on each machine"})
    return rows


def _job_rows(project_dir: Path) -> list[dict[str, Any]]:
    out = [{"name": str(j.get("name") or j.get("id") or "")} for j in _list_of_dicts(_aim(project_dir).get("jobs"))]
    try:
        from core.ddd_job_registry import _read_job_assets
        out += [{"name": str(j.get("name") or j.get("id") or "")} for j in _read_job_assets(project_dir / "bindings.yaml")]
    except Exception as exc:  # noqa: BLE001 — a jobs list the form cannot read shows as none
        logger.debug("project_agent_form: jobs of %s unreadable: %s", project_dir, exc)
    return [j for j in out if j["name"]]


def _get(d: dict[str, Any], store: str) -> Any:
    cur: Any = d
    for part in store.split("."):
        cur = cur.get(part) if isinstance(cur, dict) else None
    return cur


def _value(f: std.Field, v: Any) -> Any:
    if f.shape == "text":
        return v if isinstance(v, str) else ""
    if f.shape in ("list", "names"):
        return _str_list(v)
    if f.shape == "rows":
        return _rows(v, f.columns + (("repo",) if f.key in ("setup", "validations") else ())
                     + (("source",) if f.key == "aliases" and "source" not in f.columns else ()))
    return v


def _choices(f: std.Field, d: dict[str, Any], project_dir: Path, mode: Optional[dict[str, Any]]) -> Optional[list]:
    if f.key == "delivery":
        return ["bundle", "branch-pr"]
    if f.key == "default_mode":
        return [str(m.get("id")) for m in _modes(d)]
    if f.key == "reserved_for_human":
        return [{"value": a, "label": ACTION_LABEL.get(a, a)} for a in sorted(RESERVED_ACTIONS)]
    if f.key == "gates":
        return sorted(gate_files(project_dir))
    if f.key == "mode_skills":
        skills, _err = _skills(project_dir)
        return sorted(skills or {})
    if f.key == "placements":
        return [{"value": p, "label": PLACE_LABEL[p][0], "where": PLACE_LABEL[p][1],
                 "help": PLACE_LABEL[p][2], "available": p != "slack", "fixed": p == "chat-tab"}
                for p in ("chat-tab", "hive", "slack")]
    return None


def _view(f: std.Field, d: dict[str, Any], project_dir: Path, name: str, general: bool) -> Any:
    if f.key == "name":
        return f"{name} Project Agent" if not general else "Runner Agent"
    if f.key == "brain":
        return _home(project_dir)
    if f.key == "repos":
        return _repos(project_dir)
    if f.key == "skills":
        return _skill_rows(project_dir, d)
    if f.key == "tools":
        return _tool_rows(project_dir, general)
    if f.key == "jobs":
        return _job_rows(project_dir)
    if f.key == "product_guards":
        return [{"name": a, "what": b} for a, b in PRODUCT_GUARDS]
    if f.key == "landing":
        pr = any(m.get("delivery") == "branch-pr" for m in _modes(d))
        return ("You approve every landing: a Hive's commits come back as a bundle you review"
                + (", or as a branch you open a pull request from." if pr else "."))
    if f.key == "profiles":
        from core.run_protocol.lifecycle import PROFILE_STAGES
        return [{"name": p, "for": PROFILE_FOR.get(p, ""), "stages": list(PROFILE_STAGES[p])}
                for p in PROFILE_STAGES]
    return None


def _empty(v: Any) -> bool:
    return v in (None, "", [], {})


def _hive_row(d: dict[str, Any], project_dir: Path) -> dict[str, Any]:
    """Which modes stay off a Hive even when it is allowed, and why."""
    skills, _err = _skills(project_dir)
    local: list[dict[str, str]] = []
    for m in _modes(d):
        mid = str(m.get("id"))
        if "placements" in m and "hive" not in _str_list(m.get("placements")):
            local.append({"mode": mid, "why": "this mode runs only in a chat tab"})
            continue
        desk = [s for s in _str_list(m.get("skills")) if (skills or {}).get(s) in _DESKTOP_ONLY]
        if desk:
            local.append({"mode": mid, "why": f"on a Hive it runs without {', '.join(desk)}"})
    return {"local_modes": local}


def missing_required(doc: dict[str, Any]) -> list[str]:
    """The required fields still empty in a (normalized) definition: shared ones by
    label, a mode's own ones as ``label (mode)``. A finished draft has none."""
    out: list[str] = []
    for f in std.TEMPLATE:
        if not (f.required and f.store):
            continue
        if f.store.startswith("modes[]."):
            key = f.store[len("modes[]."):]
            out += [f"{f.label} ({m.get('id')})" for m in _modes(doc) if _empty(_value(f, m.get(key)))]
        elif _empty(_value(f, _get(doc, f.store))):
            out.append(f.label)
    return out


def build_form(doc: dict[str, Any], project_dir: Path, name: Optional[str] = None) -> dict[str, Any]:
    """The form for a normalized (schema 2) definition *doc* of the Brain at
    *project_dir*: ``{sections, modes, missing, enforced}``."""
    project_dir = Path(project_dir)
    general = is_general_name(name) or (project_dir.name == "Library" and project_dir.parent.name == "Knowledge")
    name = name or project_dir.name
    modes = _modes(doc)
    std_ids = set(std.MODES)
    sections: list[dict[str, Any]] = []
    for sid, title, purpose in std.SECTIONS:
        fields = []
        for f in std.TEMPLATE:
            if f.section != sid:
                continue
            out: dict[str, Any] = {
                "key": f.key, "label": f.label, "help": f.help, "kind": f.kind,
                "kindLabel": std.KINDS[f.kind], "shape": f.shape, "required": f.required,
                "removable": f.removable, "editable": std.editable(f), "example": f.example,
                "columns": list(f.columns)}
            if f.store and f.store.startswith("modes[]."):
                key = f.store[len("modes[]."):]
                out["scope"] = "mode"
                out["values"] = {str(m.get("id")): _value(f, m.get(key)) for m in modes}
                out["paths"] = {str(m.get("id")): f"modes[{m.get('id')}].{key}" for m in modes}
                out["choices"] = _choices(f, doc, project_dir, None)
            elif f.store:
                out["scope"] = "shared"
                out["value"] = _value(f, _get(doc, f.store))
                out["path"] = f.store
                out["choices"] = _choices(f, doc, project_dir, None)
            else:
                out["scope"] = "shared"
                out["value"] = _view(f, doc, project_dir, name, general)
            if f.key == "placements":
                out["hive"] = _hive_row(doc, project_dir)
            fields.append(out)
        sections.append({"id": sid, "title": title, "purpose": purpose, "fields": fields})
    return {
        "sections": sections,
        "modes": [{"id": str(m.get("id")), "standard": m.get("id") in std_ids,
                   "isDefault": m.get("id") == doc.get("default_mode"),
                   "placements": _str_list(_mode_value(m, doc, "placements")),
                   "ownPlacements": "placements" in m}
                  for m in modes],
        "missing": missing_required(doc),
        "skeleton": std.is_skeleton(doc),
        # a Hive runs the definitions the host syncs to it: there it is read-only
        "readOnly": os.environ.get("SWARMAI_MODE") == "hive",
        # where the definition's own guardrails run (design §8)
        "enforced": "The guardrails of this definition are enforced when the agent runs on a Hive. "
                    "In a chat tab, SwarmAI's own guards apply.",
    }
