"""Change and confirm a Project Agent definition from the Brain Hub form (design
docs/design/2026-10-29-project-agent-standard-template.md §7).

``edit`` applies the form's changes to the revision the person saw: each change names
the ``path`` the form gave the field (``project_agent_form``) and either a new value or
a row to drop. Only fields the template lets the form change are accepted
(``project_agent_standard.editable``): Editable and Your-choice fields take a value; a
From-the-Brain field takes only drops (a check, a setup step, a gate). The result must
pass ``validate``; it is written as a draft, so a changed definition is confirmed again.

``confirm`` confirms the revision seen: ``status: reviewed``, ``reviewed_at`` and
``reviewed_by``; the proposed aliases still on the form move into ``aim.json`` (routing
reads them there); the host source check (``check_sources``) runs for the new revision.

Both refuse when the file (or a skill or gate it uses) changed since it was shown, so
nobody confirms or edits what they did not see.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

import yaml

from core import project_agent_standard as std
from core.project_agent import (PLACEMENTS, PROFILES, PROJECT_AGENT_FILE, RESERVED_ACTIONS,
                                _list_of_dicts, _skills, check_sources, gate_files, normalize,
                                revision, validate)

_ALIAS_MAX = 40


class ReviewError(Exception):
    """The confirm cannot be applied; ``status`` is the HTTP status to answer with."""

    def __init__(self, status: int, message: str, problems: Optional[list[str]] = None):
        super().__init__(message)
        self.status = status
        self.problems = problems or []


def _missing(path: str) -> ReviewError:
    return ReviewError(400, f"{path} is not in the definition")


_MODE_PATH_RE = re.compile(r"\Amodes\[(?P<mode>[a-z][a-z0-9-]{0,39})\]\.(?P<key>[a-z_]{1,40})\Z")
_TEXT_MAX = 400
_LIST_MAX = 20


def _field_for(path: Any) -> tuple[std.Field, Optional[str], str]:
    """(template field, mode id or None, key in its container) for an edit path; a path
    the form does not give out is refused."""
    if not isinstance(path, str):
        raise ReviewError(400, f"cannot change {path!r}")
    m = _MODE_PATH_RE.match(path)
    for f in std.TEMPLATE:
        if not f.store or not std.editable(f):
            continue
        if m and f.store == f"modes[].{m['key']}":
            return f, m["mode"], m["key"]
        if not m and f.store == path:
            return f, None, path
    raise ReviewError(400, f"cannot change {path!r}")


def _text_value(path: str, v: Any) -> str:
    if not isinstance(v, str) or len(v) > _TEXT_MAX or "\n" in v.strip():
        raise ReviewError(400, f"{path}: one line of at most {_TEXT_MAX} characters")
    return v.strip()


def _list_value(path: str, v: Any) -> list[str]:
    if not isinstance(v, list) or len(v) > _LIST_MAX:
        raise ReviewError(400, f"{path}: a list of at most {_LIST_MAX} lines")
    return [_text_value(path, x) for x in v if not (isinstance(x, str) and not x.strip())]


def _names_value(path: str, v: Any, allowed: set[str], current: list[str], only_drop: bool) -> list[str]:
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise ReviewError(400, f"{path}: a list of names")
    bad = [x for x in v if x not in (set(current) if only_drop else allowed)]
    if bad:
        raise ReviewError(400, f"{path}: {bad} {'can only be removed here' if only_drop else 'are not allowed'}")
    return list(dict.fromkeys(v))


def _rows_value(f: std.Field, path: str, v: Any) -> list[dict[str, str]]:
    if not isinstance(v, list) or len(v) > _LIST_MAX:
        raise ReviewError(400, f"{path}: a list of at most {_LIST_MAX} rows")
    out = []
    for r in v:
        if not isinstance(r, dict):
            raise ReviewError(400, f"{path}: each row is an object")
        row = {k: _text_value(path, r.get(k) or "") for k in f.columns if k != "source"}
        if any(not row[k] for k in row):
            raise ReviewError(400, f"{path}: every row needs {', '.join(row)}")
        if f.key == "profile_rules" and row["profile"] not in PROFILES:
            raise ReviewError(400, f"{path}: {row['profile']!r} is not a pipeline profile")
        if isinstance(r.get("source"), str) and r["source"].strip():
            row["source"] = _text_value(path, r["source"])
        out.append(row)
    return out


def _apply_change(doc: dict[str, Any], project_dir: Path, change: Any) -> None:
    if not isinstance(change, dict) or "path" not in change or ("value" in change) == ("remove" in change):
        raise ReviewError(400, "each change is {path, value} or {path, remove}")
    path = change["path"]
    f, mode_id, key = _field_for(path)
    if mode_id is not None:
        container = next((m for m in _list_of_dicts(doc.get("modes")) if m.get("id") == mode_id), None)
        if container is None:
            raise ReviewError(400, f"{path}: there is no mode {mode_id}")
    else:
        container = doc
        parts = key.split(".")
        for part in parts[:-1]:
            if not isinstance(container.get(part), dict):
                container[part] = {}
            container = container[part]
        key = parts[-1]
    current = container.get(key)
    if "remove" in change:
        i = change["remove"]
        if not f.removable or f.shape != "rows" or not isinstance(i, int) or isinstance(i, bool):
            raise ReviewError(400, f"{path}: rows cannot be removed here")
        rows = [x for x in current if isinstance(x, dict)] if isinstance(current, list) else []
        if not 0 <= i < len(rows):
            raise ReviewError(400, f"{path}[{i}] is not in the definition")
        current.remove(rows[i])
        return
    v = change["value"]
    if f.kind == "brain" and f.shape != "names":
        raise ReviewError(400, f"{path} comes from the Brain; change it there")
    if f.shape == "text":
        new: Any = _text_value(path, v)
    elif f.shape == "list":
        new = _list_value(path, v)
    elif f.shape == "rows":
        new = _rows_value(f, path, v)
    elif f.shape == "choice":
        allowed = {"profile": PROFILES, "delivery": {"bundle", "branch-pr"},
                   "default_mode": {str(m.get("id")) for m in _list_of_dicts(doc.get("modes"))}}[f.key]
        if v not in allowed:
            raise ReviewError(400, f"{path}: {v!r} is not one of {sorted(allowed)}")
        new = v
    else:   # names
        cur = [x for x in current if isinstance(x, str)] if isinstance(current, list) else []
        if f.key == "gates":
            new = _names_value(path, v, set(gate_files(project_dir)), cur, only_drop=True)
        elif f.key == "reserved_for_human":
            new = _names_value(path, v, set(RESERVED_ACTIONS), cur, only_drop=False)
        elif f.key == "mode_skills":
            skills, _err = _skills(project_dir)
            new = _names_value(path, v, set(skills or {}), cur, only_drop=False)
        elif f.key == "placements":
            new = _names_value(path, v, set(PLACEMENTS), cur, only_drop=False)
            if "chat-tab" not in new:
                raise ReviewError(400, f"{path}: a chat tab is always allowed")
            if "hive" not in new:   # a mode only narrows: nothing of it may run on a Hive either
                for mode in _list_of_dicts(doc.get("modes")):
                    if isinstance(mode.get("placements"), list):
                        mode["placements"] = [p for p in mode["placements"] if p != "hive"]
        else:
            new = _names_value(path, v, set(), cur, only_drop=True)
    if f.key == "gates":
        for mode in _list_of_dicts(doc.get("modes")):   # a mode only adds: a dropped gate goes there too
            g = mode.get("gates")
            if isinstance(g, dict) and isinstance(g.get("enforce"), list):
                g["enforce"] = [x for x in g["enforce"] if x in new or x not in cur]
    if f.key == "reserved_for_human":
        dropped = set(_str(current)) - set(new)
        for mode in _list_of_dicts(doc.get("modes")):
            if isinstance(mode.get("reserved_for_human"), list):
                mode["reserved_for_human"] = [x for x in mode["reserved_for_human"] if x not in dropped]
    if new in ("", []) and f.shape in ("text", "list") and key in container:
        container.pop(key)
    else:
        container[key] = new


def _str(v: Any) -> list[str]:
    return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []


def _load_seen(project_dir: Path, seen_revision: str) -> tuple[Path, dict[str, Any]]:
    """The definition, parsed from the bytes the revision the person saw covers."""
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
    return target, normalize(doc)


def _write(target: Path, doc: dict[str, Any], note: str) -> None:
    text = (std.HEADER + f"# {note}\n"
            + yaml.safe_dump(std.ordered(doc), sort_keys=False, allow_unicode=True, width=100))
    tmp = target.with_name(f".{target.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, target)


def edit(project_dir: Path, *, seen_revision: str, changes: list[Any]) -> dict[str, Any]:
    """Apply the form's *changes* to the revision seen and write the result as a draft.
    Raises ReviewError: 404 no definition, 409 changed since it was seen, 400 a change
    the form may not make, 422 the result does not validate (nothing is written)."""
    project_dir = Path(project_dir)
    target, doc = _load_seen(project_dir, seen_revision)
    if not isinstance(changes, list) or not changes:
        raise ReviewError(400, "nothing to change")
    for change in changes:
        _apply_change(doc, project_dir, change)
    doc["status"] = "draft"
    doc["reviewed_at"] = None
    doc["reviewed_by"] = None
    problems = validate(doc, project_dir)
    if problems:
        raise ReviewError(422, "the changed definition would not be valid", problems)
    _write(target, doc, "Changed on the Brain Hub form; confirm it there.")
    return {"revision": revision(project_dir), "status": "draft"}


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


def confirm(project_dir: Path, *, seen_revision: str, reviewer: str,
            now: Optional[str] = None) -> dict[str, Any]:
    """Confirm the revision seen. Raises ReviewError: 404 no definition, 409 changed
    since it was seen, 409 aim.json unreadable, 422 the result does not validate
    (nothing is written)."""
    project_dir = Path(project_dir)
    target, doc = _load_seen(project_dir, seen_revision)
    intake = doc.get("intake") if isinstance(doc.get("intake"), dict) else {}
    accepted: list[str] = []
    for a in _list_of_dicts(intake.get("proposed_aliases")):
        name = str(a.get("name") or "").strip()
        if name and len(name) <= _ALIAS_MAX and name not in accepted:
            accepted.append(name)
    intake.pop("proposed_aliases", None)       # the ones the person kept move to aim.json

    doc["status"] = "reviewed"
    doc["reviewed_at"] = now or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    doc["reviewed_by"] = reviewer
    problems = validate(doc, project_dir)
    if problems:
        raise ReviewError(422, "the confirmed definition would not be valid", problems)

    aim = _read_aim(project_dir) if accepted else None
    _write(target, doc, "Confirmed on the Brain Hub form.")
    # the aliases go in only once the definition they belong to is confirmed on disk
    added = _write_aliases(project_dir, aim, accepted)
    check = check_sources(project_dir)
    return {"revision": revision(project_dir), "reviewedAt": doc["reviewed_at"],
            "reviewedBy": reviewer, "aliasesAdded": added, "sourceCheck": check}
