"""Confirm a Project Agent definition from the Brain Hub review card (design
2026-10-28-project-agent-flow §5, step 3).

The card shows every row of the definition with its source. The person can delete
rows, allow the Hive, accept or reject the proposed aliases, and confirm. Wording
changes go through chat (the agent edits the file); this module never rewrites a
row's text.

``confirm`` applies those choices to the revision the person saw: if the file (or a
skill or gate it uses) changed since, it is refused, so nobody confirms what they did
not see. The result must pass ``validate``. Then the file is written with ``status:
reviewed``, ``reviewed_at`` and ``reviewed_by``, the accepted aliases move into
``aim.json`` (routing reads them there as a deterministic signal), and the host source
check (``check_sources``) runs for the new revision.

Removal paths, as the card sends them (indexes are those of the revision seen):
``identity`` · ``confidence`` · ``intake.handles[<i>]`` · ``watches[<i>]`` ·
``gates[<name>]`` · ``reserved_for_human[<name>]`` · ``modes[<id>]`` ·
``modes[<id>].setup[<i>]`` · ``modes[<id>].validations[<i>]`` ·
``modes[<id>].profile_rules[<i>]`` · ``modes[<id>].skills[<name>]`` ·
``modes[<id>].gates[<name>]`` · ``modes[<id>].reserved_for_human[<name>]`` (what a mode
adds on top of the shared lists). An index counts the rows the card shows: for a list of
mappings (watches, setup, validations, profile_rules) the i-th mapping, as
``load_project_agent`` lists them.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

import yaml

from core.project_agent import (PROJECT_AGENT_FILE, _list_of_dicts, check_sources, normalize,
                                revision, validate)

_PATH_RE = re.compile(
    r"\A(?:(?P<top>identity|confidence)"
    r"|(?P<list>intake\.handles|watches)\[(?P<li>\d{1,4})\]"
    r"|(?P<named>gates|reserved_for_human)\[(?P<name>[^\[\]\n]{1,80})\]"
    r"|modes\[(?P<mode>[a-z][a-z0-9-]{0,39})\]"
    r"(?:\.(?P<mkey>setup|validations|profile_rules)\[(?P<mi>\d{1,4})\]"
    r"|\.(?P<mnamed>skills|gates|reserved_for_human)\[(?P<mname>[^\[\]\n]{1,80})\])?)\Z")
_ALIAS_MAX = 40


class ReviewError(Exception):
    """The confirm cannot be applied; ``status`` is the HTTP status to answer with."""

    def __init__(self, status: int, message: str, problems: Optional[list[str]] = None):
        super().__init__(message)
        self.status = status
        self.problems = problems or []


def _missing(path: str) -> ReviewError:
    return ReviewError(400, f"{path} is not in the definition")


def _remove(doc: dict[str, Any], paths: list[Any]) -> None:
    """Apply the removals. An unknown path, or a row that is not there, is an error."""
    modes = {m.get("id"): m for m in _list_of_dicts(doc.get("modes"))}
    by_index: dict[int, tuple[list, set[int]]] = {}   # id(list) -> (list, indexes)
    drop_modes: set[str] = set()

    def mark(seq: Any, index: int, path: str, mappings: bool) -> None:
        """Mark the row the card numbered *index*: in a list of mappings, the
        index-th mapping (the card never shows a row that is not one)."""
        if not isinstance(seq, list):
            raise _missing(path)
        positions = [i for i, x in enumerate(seq) if isinstance(x, dict)] if mappings else list(range(len(seq)))
        if index >= len(positions):
            raise _missing(path)
        by_index.setdefault(id(seq), (seq, set()))[1].add(positions[index])

    def drop_name(seq: Any, name: str) -> bool:
        if isinstance(seq, list) and name in seq:
            seq.remove(name)
            return True
        return False

    for path in paths:
        m = _PATH_RE.match(path) if isinstance(path, str) else None
        if m is None:
            raise ReviewError(400, f"cannot remove {path!r}")
        if m["top"]:
            if m["top"] not in doc:
                raise _missing(path)
            doc.pop(m["top"])
        elif m["list"] == "intake.handles":
            intake = doc.get("intake")
            mark(intake.get("handles") if isinstance(intake, dict) else None, int(m["li"]), path, False)
        elif m["list"] == "watches":
            mark(doc.get("watches"), int(m["li"]), path, True)
        elif m["named"] == "gates":
            gates = doc.get("gates")
            if not drop_name(gates.get("enforce") if isinstance(gates, dict) else None, m["name"]):
                raise _missing(path)
            for mode in modes.values():      # a mode only adds to the shared list: drop it there too
                g = mode.get("gates")
                drop_name(g.get("enforce") if isinstance(g, dict) else None, m["name"])
        elif m["named"] == "reserved_for_human":
            if not drop_name(doc.get("reserved_for_human"), m["name"]):
                raise _missing(path)
            for mode in modes.values():
                drop_name(mode.get("reserved_for_human"), m["name"])
        else:
            mode = modes.get(m["mode"])
            if mode is None:
                raise _missing(path)
            if m["mkey"]:
                mark(mode.get(m["mkey"]), int(m["mi"]), path, True)
            elif m["mnamed"] == "skills":
                if not drop_name(mode.get("skills"), m["mname"]):
                    raise _missing(path)
            elif m["mnamed"] == "gates":
                # a shared name removed here leaves the mode narrower than the shared
                # part, which validate refuses: shared names go with gates[<name>]
                g = mode.get("gates")
                if not drop_name(g.get("enforce") if isinstance(g, dict) else None, m["mname"]):
                    raise _missing(path)
            elif m["mnamed"] == "reserved_for_human":
                if not drop_name(mode.get("reserved_for_human"), m["mname"]):
                    raise _missing(path)
            elif m["mode"] == doc.get("default_mode"):
                raise ReviewError(400, f"{path} is the default mode; choose another default in chat first")
            else:
                drop_modes.add(m["mode"])
    for seq, indexes in by_index.values():
        for i in sorted(indexes, reverse=True):
            del seq[i]
    if drop_modes:
        doc["modes"] = [x for x in _list_of_dicts(doc.get("modes")) if x.get("id") not in drop_modes]


def _set_hive(doc: dict[str, Any], allow: bool) -> None:
    """Add the Hive to the shared placements, or take it out everywhere."""
    shared = [p for p in doc.get("placements") or [] if isinstance(p, str)]
    if allow and "hive" not in shared:
        shared.append("hive")
    if not allow:
        shared = [p for p in shared if p != "hive"]
        for mode in _list_of_dicts(doc.get("modes")):
            if isinstance(mode.get("placements"), list):
                mode["placements"] = [p for p in mode["placements"] if p != "hive"]
    doc["placements"] = shared


def _read_aim(project_dir: Path) -> Optional[dict[str, Any]]:
    """The Brain's aim.json, None when it has none; refused when it cannot be read
    (checked before anything is written)."""
    aim = project_dir / "aim.json"
    if not aim.is_file():
        return None
    try:
        data = json.loads(aim.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ReviewError(409, "aim.json cannot be read; fix it, then confirm") from None
    if not isinstance(data, dict):
        raise ReviewError(409, "aim.json is not an object; fix it, then confirm")
    return data


def _write_aliases(project_dir: Path, data: Optional[dict[str, Any]], names: list[str]) -> list[str]:
    """Merge *names* into ``aim.json`` ``aliases``; returns the names added. A Brain
    without an aim.json (the General Brain) keeps none: routing has no use for them."""
    if not names or data is None:
        return []
    aim = project_dir / "aim.json"
    have = [a for a in data.get("aliases") or [] if isinstance(a, str)]
    added = [n for n in names if n not in have]
    if not added:
        return []
    data["aliases"] = have + added
    tmp = aim.with_name(f".{aim.name}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, aim)
    return added


def confirm(project_dir: Path, *, seen_revision: str, remove: list[Any] | None = None,
            allow_hive: Optional[bool] = None, aliases: list[Any] | None = None,
            reviewer: str, now: Optional[str] = None) -> dict[str, Any]:
    """Apply the card's choices to the revision seen and confirm. Raises ReviewError:
    404 no definition, 409 changed since it was seen, 400 a bad removal or alias,
    422 the result does not validate (nothing is written)."""
    project_dir = Path(project_dir)
    target = project_dir / PROJECT_AGENT_FILE
    if not target.is_file() or target.is_symlink():
        raise ReviewError(404, "this Brain has no Project Agent definition")
    try:
        raw = target.read_bytes()
        current = revision(project_dir)
        same = target.read_bytes() == raw          # the bytes parsed are the bytes the revision covers
    except OSError:
        raise ReviewError(422, f"{PROJECT_AGENT_FILE} cannot be read") from None
    if not seen_revision or current != seen_revision or not same:
        raise ReviewError(409, "the definition changed since it was shown; review it again")
    try:
        doc = yaml.safe_load(raw.decode("utf-8"))
    except (UnicodeError, yaml.YAMLError):
        raise ReviewError(422, f"{PROJECT_AGENT_FILE} cannot be read") from None
    if not isinstance(doc, dict):
        raise ReviewError(422, f"{PROJECT_AGENT_FILE} is not a mapping")
    doc = normalize(doc)
    _remove(doc, list(remove or []))
    if allow_hive is not None:
        _set_hive(doc, bool(allow_hive))

    intake = doc.get("intake") if isinstance(doc.get("intake"), dict) else {}
    proposed = [str(a.get("name")) for a in _list_of_dicts(intake.get("proposed_aliases"))]
    accepted: list[str] = []
    for a in aliases or []:
        if not isinstance(a, str) or a not in proposed:
            raise ReviewError(400, f"alias {a!r} was not proposed")
        if not a.strip() or len(a) > _ALIAS_MAX:
            raise ReviewError(400, f"alias {a!r} must be 1-{_ALIAS_MAX} characters")
        if a not in accepted:
            accepted.append(a)
    intake.pop("proposed_aliases", None)       # the person decided on every proposed alias

    doc["status"] = "reviewed"
    doc["reviewed_at"] = now or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    doc["reviewed_by"] = reviewer
    problems = validate(doc, project_dir)
    if problems:
        raise ReviewError(422, "the confirmed definition would not be valid", problems)

    aim = _read_aim(project_dir) if accepted else None
    text = ("# Project Agent definition — confirmed on the Brain Hub review card.\n"
            "# Design docs/design/2026-10-28-project-agent-flow.md §4.\n"
            + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100))
    tmp = target.with_name(f".{target.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, target)
    # the aliases go in only once the definition they belong to is confirmed on disk
    added = _write_aliases(project_dir, aim, accepted)
    check = check_sources(project_dir)
    return {"revision": revision(project_dir), "reviewedAt": doc["reviewed_at"],
            "reviewedBy": reviewer, "aliasesAdded": added, "sourceCheck": check}
