"""project_agent — the Project Agent definition of a DDD (Brain), section ⑤ Delivery.

The Project Agent is the delivery agent that works with a person on one Brain. Its
definition is GENERATED at Brain creation (s_project-manager P3) from the Brain's
own knowledge (PRODUCT / TECH / IMPROVEMENT / PROJECT, bindings.yaml, the bound
repo's validations.json or CI) using only the profiles the pipeline supports. The
person reviews and revises it; they never fill it in from scratch.

It lives next to bindings.yaml as ``project-agent.yaml`` (⑤ is root-level files).
This module only READS and VALIDATES it; writing is the agent's job (a skill step).

Key public symbols:
- ``PROJECT_AGENT_FILE``   the file name (project-root relative)
- ``PROFILES``             pipeline profiles a definition may name (from the run protocol)
- ``PLACEMENTS``           where a Run may execute
- ``load_project_agent``   parse + validate -> dict for the Brain Hub, or None if absent
- ``project_agent_status`` draft / reviewed / invalid / None, for the Hub home
- ``validate``             the list of problems in a parsed definition ([] = valid)
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from core.run_protocol.lifecycle import PROFILE_STAGES

logger = logging.getLogger(__name__)

PROJECT_AGENT_FILE = "project-agent.yaml"
PROFILES: frozenset[str] = frozenset(PROFILE_STAGES)
PLACEMENTS: frozenset[str] = frozenset({"chat-tab", "local-worker", "hive"})
STATUSES: frozenset[str] = frozenset({"draft", "reviewed"})
_MAX_BYTES = 64 * 1024


def _list_of_dicts(v: Any) -> list[dict[str, Any]]:
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


def validate(doc: Any) -> list[str]:
    """Problems in a parsed definition. Unknown keys are ignored (forward-compatible)."""
    if not isinstance(doc, dict):
        return ["not a mapping"]
    out: list[str] = []
    if doc.get("default_profile") not in PROFILES:
        out.append(f"default_profile {doc.get('default_profile')!r} is not a pipeline profile")
    for i, rule in enumerate(_list_of_dicts(doc.get("profile_rules"))):
        if rule.get("profile") not in PROFILES:
            out.append(f"profile_rules[{i}].profile {rule.get('profile')!r} is not a pipeline profile")
        if not str(rule.get("when") or "").strip():
            out.append(f"profile_rules[{i}].when is empty")
    places = doc.get("placements") or []
    if not isinstance(places, list) or not places:
        out.append("placements is empty")
    else:
        bad = [p for p in places if p not in PLACEMENTS]
        if bad:
            out.append(f"placements has unknown values {bad}")
    if doc.get("status", "draft") not in STATUSES:
        out.append(f"status {doc.get('status')!r} is not draft or reviewed")
    return out


def load_project_agent(project_dir: Path) -> dict[str, Any] | None:
    """The definition as the Brain Hub shows it, or None when the Brain has none.

    Never raises: an unreadable or malformed file comes back with ``problems`` so the
    UI can say so instead of hiding it.
    """
    p = project_dir / PROJECT_AGENT_FILE
    if not p.is_file() or p.is_symlink():
        return None
    try:
        if p.stat().st_size > _MAX_BYTES:
            return {"path": PROJECT_AGENT_FILE, "problems": ["file is larger than 64 KB"]}
        doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        logger.debug("project-agent.yaml unreadable in %s: %s", project_dir, exc)
        return {"path": PROJECT_AGENT_FILE, "problems": ["file could not be parsed"]}
    if not isinstance(doc, dict):
        return {"path": PROJECT_AGENT_FILE, "problems": ["not a mapping"]}
    str_list = lambda v: [str(x) for x in v] if isinstance(v, list) else []  # noqa: E731
    return {
        "path": PROJECT_AGENT_FILE,
        "status": doc.get("status", "draft"),
        "reviewedAt": doc.get("reviewed_at"),
        "summary": str(doc.get("summary") or ""),
        "defaultProfile": doc.get("default_profile"),
        "profileRules": [{"when": str(r.get("when") or ""), "profile": r.get("profile")}
                         for r in _list_of_dicts(doc.get("profile_rules"))],
        "reviewFocus": [{"area": str(r.get("area") or ""), "why": str(r.get("why") or ""),
                         "source": str(r.get("source") or "")}
                        for r in _list_of_dicts(doc.get("review_focus"))],
        "validations": [{"id": str(r.get("id") or ""), "command": str(r.get("command") or ""),
                         "source": str(r.get("source") or "")}
                        for r in _list_of_dicts(doc.get("validations"))],
        "placements": str_list(doc.get("placements")),
        "landing": {str(k): str(v) for k, v in (doc.get("landing") or {}).items()}
        if isinstance(doc.get("landing"), dict) else {},
        "sources": str_list(doc.get("sources")),
        "problems": validate(doc),
    }


def project_agent_status(project_dir: Path) -> str | None:
    """The one word the Brain Hub home needs: ``draft`` (waits for the person to
    confirm), ``reviewed``, ``invalid`` (the file has problems), or None (no file)."""
    pa = load_project_agent(project_dir)
    if pa is None:
        return None
    if pa.get("problems"):
        return "invalid"
    return "reviewed" if pa.get("status") == "reviewed" else "draft"
