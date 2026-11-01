"""The person's floor of a Project Agent: the authority only a person may widen.

Design: docs/design/2026-11-01-project-agent-owned-by-agent.md §1-§2.

A definition has two parts. Its **authority** — the actions it reserves for a person,
the gates it enforces, and how each mode delivers — decides what the agent may do
without asking; everything else decides how the work is done and is the agent's. The
agent confirms a definition on its own when its authority is no wider than the
**floor**: the authority of the person's last confirmation, or the product default
when no person has confirmed one (every reserved action, every mode delivering a
bundle, no gate). Only a person's confirmation moves the floor.

Everything here is a set comparison (SOUL P5): no model decides whether authority
widened.

The floor lives next to the definition in ``.project-agent-floor.json``, with the agent
confirmations since (who, when, why, what changed), so the Brain Hub can show them. It
is not part of the revision and does not travel to a Hive.

Public:
- ``authority(doc)``          the authority part of a definition (any schema)
- ``PRODUCT_FLOOR``           the floor of a Brain no person has confirmed
- ``widened(current, floor)`` what *current* widens against *floor* ([] = nothing)
- ``load(project_dir)``       the stored record, or the seeded one (see ``floor``)
- ``peek(project_dir)``       the same, never written (views)
- ``floor(project_dir)``      the floor in force
- ``set_by_person(project_dir, doc, who, revision)`` a person's confirmation moves it
- ``record_agent(project_dir, rec, entry)`` an agent's confirmation, kept with the floor
- ``changed_fields(before, after)`` what a confirmation changed against the last one
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

from core.project_agent import RESERVED_ACTIONS, _list_of_dicts, _str_list, normalize

logger = logging.getLogger(__name__)

FLOOR_FILE = ".project-agent-floor.json"
#: who a confirmation names when the agent made it (``reviewed_by``)
AGENT = "agent"
#: the shared part's key in ``reserved`` / ``gates``
SHARED = "*"
#: agent confirmations kept with the floor (the oldest drop first)
MAX_AGENT_ENTRIES = 50


def authority(doc: Any, project_dir: Optional[Path] = None) -> dict[str, Any]:
    """{reserved: {"*"|mode: [actions]}, gates: {"*"|mode: [gates]}, delivery: {mode:
    {delivery, pr_remote, repos}}} of a parsed definition; a mode without its own list
    uses the shared one (the runtime's rule, core/project_agent._mode_view). ``repos``
    only for a mode that pushes (``branch-pr``): where it pushes is authority too. With
    *project_dir*, also ``gates_digest``: what the gates check is the files under
    3-gates/, so a gate made to pass everything is as wide as a gate removed."""
    d = normalize(doc) if isinstance(doc, dict) else {}
    shared_r = sorted(set(_str_list(d.get("reserved_for_human"))))
    gates = d.get("gates") if isinstance(d.get("gates"), dict) else {}
    shared_g = sorted(set(_str_list(gates.get("enforce"))))
    out: dict[str, Any] = {"reserved": {SHARED: shared_r}, "gates": {SHARED: shared_g}, "delivery": {}}
    for m in _list_of_dicts(d.get("modes")):
        mid = str(m.get("id") or "")
        if not mid:
            continue
        out["reserved"][mid] = sorted(set(_str_list(m["reserved_for_human"]))) \
            if "reserved_for_human" in m else shared_r
        mg = m.get("gates")
        out["gates"][mid] = sorted(set(_str_list(mg.get("enforce")))) \
            if isinstance(mg, dict) and "enforce" in mg else shared_g
        delivery = str(m.get("delivery") or "bundle")
        pushes = delivery == "branch-pr"
        out["delivery"][mid] = {"delivery": delivery,
                                "pr_remote": str(m.get("pr_remote") or "fork") if pushes else None,
                                "repos": sorted(set(_str_list(m.get("repos")))) if pushes else None}
    if project_dir is not None:
        from core.project_agent import gates_digest
        out["gates_digest"] = gates_digest(Path(project_dir))
    return out


PRODUCT_FLOOR: dict[str, Any] = {
    "reserved": {SHARED: sorted(RESERVED_ACTIONS)},
    "gates": {SHARED: []},
    "delivery": {},
}


def _floor_of(floor: dict[str, Any], key: str, mid: str) -> list[str]:
    part = floor.get(key) if isinstance(floor.get(key), dict) else {}
    return _str_list(part.get(mid, part.get(SHARED)))


def widened(current: dict[str, Any], floor: dict[str, Any]) -> list[str]:
    """What *current* widens against *floor*, one plain line each ([] = nothing). A mode
    the floor does not know is held to the floor's shared part and to a bundle."""
    out: list[str] = []
    for key, what in (("reserved", "no longer keeps {x} for you"), ("gates", "no longer enforces gate {x}")):
        for mid, have in sorted((current.get(key) or {}).items()):
            gone = sorted(set(_floor_of(floor, key, mid)) - set(have))
            where = "the shared part" if mid == SHARED else f"mode {mid}"
            out.extend(f"{where} {what.format(x=x)}" for x in gone)
    floor_d = floor.get("delivery") if isinstance(floor.get("delivery"), dict) else {}
    for mid, now in sorted((current.get("delivery") or {}).items()):
        was = floor_d.get(mid) if isinstance(floor_d.get(mid), dict) else {"delivery": "bundle", "pr_remote": None}
        if now.get("delivery") == "branch-pr" and was.get("delivery") != "branch-pr":
            out.append(f"mode {mid} delivers a branch and a pull request (it pushes to GitHub)")
        elif now.get("delivery") == "branch-pr" and now.get("pr_remote") != was.get("pr_remote"):
            out.append(f"mode {mid} opens its pull request from {now.get('pr_remote')} "
                       f"instead of {was.get('pr_remote')}")
        elif now.get("delivery") == "branch-pr":
            new = sorted(set(_str_list(now.get("repos"))) - set(_str_list(was.get("repos"))))
            out.extend(f"mode {mid} pushes to {r} as well" for r in new)
    # a gate the person confirmed checks what its files say: compared when the floor
    # enforces any gate and knows the files it was confirmed with
    enforced = any(_str_list(v) for v in (floor.get("gates") or {}).values()) \
        if isinstance(floor.get("gates"), dict) else False
    if enforced and floor.get("gates_digest") and current.get("gates_digest") \
            and current["gates_digest"] != floor["gates_digest"]:
        out.append("a gate script under 3-gates/ changed since you confirmed it")
    return out


def _path(project_dir: Path) -> Path:
    return Path(project_dir) / FLOOR_FILE


def _read(project_dir: Path) -> Optional[dict[str, Any]]:
    try:
        rec = json.loads(_path(project_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) and isinstance(rec.get("floor"), dict) else None


def _save(project_dir: Path, rec: dict[str, Any]) -> None:
    target = _path(project_dir)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(rec, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _seed(project_dir: Path, doc: Optional[dict[str, Any]] = None,
          reviewed: Optional[bool] = None) -> dict[str, Any]:
    """The record of a Brain that has none yet. A definition a person confirmed before
    the floor existed (it is ``reviewed`` by someone other than the agent, with the
    source check of its current revision) gives its own authority; anything else gives
    the product floor. *doc* / *reviewed*: what the caller already read (a view)."""
    import yaml

    from core.project_agent import PROJECT_AGENT_FILE, confirmation, source_check_current
    project_dir = Path(project_dir)
    if doc is None:
        try:
            doc = yaml.safe_load((project_dir / PROJECT_AGENT_FILE).read_text(encoding="utf-8")) or {}
        except (OSError, UnicodeError, yaml.YAMLError):
            doc = {}
    if not (isinstance(doc, dict) and doc.get("reviewed_by") not in (None, "", AGENT)):
        return {"floor": PRODUCT_FLOOR, "set_by": None, "set_at": None, "seeded": True, "agent": []}
    if reviewed is None:
        reviewed = confirmation(doc.get("status"), source_check_current(project_dir)) == "reviewed"
    if reviewed:
        return {"floor": authority(doc, project_dir), "set_by": str(doc.get("reviewed_by")),
                "set_at": doc.get("reviewed_at"), "seeded": True, "agent": [], "doc": _plain(doc)}
    return {"floor": PRODUCT_FLOOR, "set_by": None, "set_at": None, "seeded": True, "agent": []}


def load(project_dir: Path) -> dict[str, Any]:
    """The stored record, or the seeded one when there is none. A seed from a person's
    confirmation is kept at once (best effort): once the definition changes, that
    confirmation can no longer be read back, and the floor would fall to the product
    default (stricter, so the person would be asked again for what they had allowed)."""
    rec = _read(project_dir)
    if rec is not None:
        return rec
    rec = _seed(project_dir)
    if rec.get("set_by"):
        try:
            _save(project_dir, {k: v for k, v in rec.items() if k != "seeded"})
        except OSError:
            logger.warning("project agent: the floor of %s was not kept", Path(project_dir).name)
    return rec


def peek(project_dir: Path, doc: Optional[dict[str, Any]] = None,
         reviewed: Optional[bool] = None) -> dict[str, Any]:
    """``load`` without keeping a seed (for views, which pass what they already read)."""
    return _read(project_dir) or _seed(project_dir, doc, reviewed)


def floor(project_dir: Path) -> dict[str, Any]:
    return load(project_dir)["floor"]


def set_by_person(project_dir: Path, doc: dict[str, Any], who: str, revision: Optional[str]) -> None:
    """A person confirmed *doc*: its authority is the new floor; the agent
    confirmations before it are history the person has now seen, so they are cleared."""
    _save(project_dir, {"floor": authority(doc, project_dir), "set_by": who, "set_at": _now(),
                        "revision": revision, "agent": [], "doc": _plain(doc)})


_NOT_COMPARED = frozenset({"status", "reviewed_at", "reviewed_by"})


def _plain(doc: dict[str, Any]) -> dict[str, Any]:
    """*doc* as JSON keeps it (what the next confirmation is compared with)."""
    return json.loads(json.dumps(normalize(doc), default=str))


def changed_fields(before: Any, after: dict[str, Any]) -> list[str]:
    """The fields of *after* that differ from *before*, by the form's paths
    (``modes[<id>].<key>``); a definition whose fields did not change changed in a skill
    or a gate it uses (the revision covers them)."""
    from core.project_agent_standard import RETIRED_KEYS
    if not isinstance(before, dict):
        return ["first confirmation"]
    a, b = _plain(after), before
    skip = _NOT_COMPARED | set(RETIRED_KEYS) | {"modes"}     # retired keys are dropped on write
    out = [k for k in sorted(set(a) | set(b)) if k not in skip and a.get(k) != b.get(k)]
    ma = {str(m.get("id")): m for m in _list_of_dicts(a.get("modes"))}
    mb = {str(m.get("id")): m for m in _list_of_dicts(b.get("modes"))}
    for mid in sorted(set(ma) | set(mb)):
        if mid not in mb:
            out.append(f"modes[{mid}] added")
        elif mid not in ma:
            out.append(f"modes[{mid}] removed")
        else:
            out.extend(f"modes[{mid}].{k}" for k in sorted(set(ma[mid]) | set(mb[mid]))
                       if ma[mid].get(k) != mb[mid].get(k))
    return out or ["a skill or a gate it uses"]


def record_agent(project_dir: Path, rec: dict[str, Any], entry: dict[str, Any]) -> None:
    """Keep an agent confirmation with the floor (the floor itself never changes).
    *rec* is the record ``load`` gave BEFORE the confirmation was written: once the
    definition names the agent as its confirmer, a seed would fall back to the product
    floor."""
    rec = dict(rec)
    rec.pop("seeded", None)
    if isinstance(entry.get("doc"), dict):
        rec["doc"] = _plain(entry.pop("doc"))
    rec["agent"] = ([e for e in rec.get("agent") or [] if isinstance(e, dict)] + [entry])[-MAX_AGENT_ENTRIES:]
    _save(project_dir, rec)
