"""project_agent — the Project Agent definition of a DDD (Brain), section ⑤ Delivery.

Design: docs/design/2026-10-28-project-agent-flow.md §4.

The Project Agent is the delivery face of one Brain: how its work is taken in, done
and delivered. A Brain has ONE definition; the kinds of work inside it are **modes**
(when, profile, placements, repos, setup, validations, skills, delivery). A mode can
only NARROW the shared part (placements a subset; reserved actions and gates only
added), so the safety baseline is reviewed once.

It lives next to bindings.yaml as ``project-agent.yaml``. A draft is generated at Brain
creation (core/project_agent_draft) from the Brain's own knowledge; the person reviews
it in Brain Hub. A v1 file (``default_profile`` / ``profile_rules`` / ``validations``
at the top) reads as a v2 definition with one mode.

Key public symbols:
- ``PROJECT_AGENT_FILE``   the file name (project-root relative)
- ``PROFILES`` / ``PLACEMENTS`` / ``DELIVERIES`` / ``RESERVED_ACTIONS``
- ``normalize(doc)``       any schema -> the v2 shape (shared part + modes)
- ``validate(doc, project_dir=None)`` problems ([] = valid); reference checks when a
                           project dir is given
- ``notes(doc, project_dir)`` advisory remarks that are not problems (a desktop-only
                           skill in a mode that may run on a Hive)
- ``revision(project_dir)`` sha256 of the definition + the skill dirs it uses + 3-gates/
- ``gate_files(project_dir)`` / ``gates_digest(project_dir)`` the gate scripts and the
                           digest a Hive checks before running them
- ``check_sources`` / ``source_check_current`` the host-only check that each command's
                           source file exists in the bound repo, stored per revision
- ``load_project_agent``   parse + validate -> dict for the Brain Hub, or None if absent
- ``project_agent_status`` draft / reviewed / invalid / None, for the Hub home
"""
from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml

from core.run_protocol.lifecycle import PROFILE_STAGES

logger = logging.getLogger(__name__)

PROJECT_AGENT_FILE = "project-agent.yaml"
PROFILES: frozenset[str] = frozenset(PROFILE_STAGES)
PLACEMENTS: frozenset[str] = frozenset({"chat-tab", "local-worker", "hive"})
STATUSES: frozenset[str] = frozenset({"draft", "reviewed"})
DELIVERIES: frozenset[str] = frozenset({"bundle", "branch-pr"})
PR_REMOTES: frozenset[str] = frozenset({"fork", "origin"})
#: the actions a definition may reserve for a person; each maps to a product gate
#: predicate (core/security_hooks._is_reserved_action)
RESERVED_ACTIONS: frozenset[str] = frozenset(
    {"push", "open_pr", "publish_release", "send_external_message"})
#: the Brain of tasks that belong to no project: its knowledge is Knowledge/Library and
#: its Project Agent is the Runner Agent (design 2026-10-28-project-agent-flow §3, §5)
GENERAL_BRAIN = "General"
_MAX_BYTES = 64 * 1024
_MODE_ID_RE = re.compile(r"[a-z][a-z0-9-]{0,39}")
#: files under 3-gates/ that are never part of a gate's content (byte-compiled caches)
_GATE_SKIP_PARTS = {"__pycache__"}


def _list_of_dicts(v: Any) -> list[dict[str, Any]]:
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


def _str_list(v: Any) -> list[str]:
    return [str(x) for x in v] if isinstance(v, list) else []


def _rows(v: Any, keys: tuple[str, ...]) -> list[dict[str, str]]:
    return [{k: str(r.get(k) or "") for k in keys} for r in _list_of_dicts(v)]


# ── normalize: any schema -> v2 ──────────────────────────────────────────────

def normalize(doc: dict[str, Any]) -> dict[str, Any]:
    """The definition in the v2 shape. A v1 document (``schema`` absent or 1) becomes
    one mode, ``default``, carrying its profile, rules and validations; its top-level
    ``summary`` / ``review_focus`` become ``identity.summary`` / ``watches``."""
    if doc.get("schema") == 2:
        return doc
    mode = {
        "id": "default",
        "when": "any change",
        "profile": doc.get("default_profile"),
        "profile_rules": doc.get("profile_rules") or [],
        "placements": doc.get("placements") or [],
        "validations": doc.get("validations") or [],
        "delivery": "bundle",
    }
    return {
        "schema": 2,
        "status": doc.get("status", "draft"),
        "reviewed_at": doc.get("reviewed_at"),
        "reviewed_by": doc.get("reviewed_by"),
        "identity": {"summary": doc.get("summary") or ""},
        "intake": {},
        "watches": doc.get("review_focus") or [],
        "gates": {"enforce": []},
        "reserved_for_human": sorted(RESERVED_ACTIONS),
        "placements": doc.get("placements") or [],
        "landing": doc.get("landing") or {},
        "modes": [mode],
        "default_mode": "default",
        "sources": doc.get("sources") or [],
    }


def _modes(doc: dict[str, Any]) -> list[dict[str, Any]]:
    return _list_of_dicts(doc.get("modes"))


def _mode_value(mode: dict[str, Any], shared: dict[str, Any], key: str) -> Any:
    return mode[key] if key in mode else shared.get(key)


# ── reference sources on disk ────────────────────────────────────────────────

def _bindings(project_dir: Path) -> dict[str, Any]:
    """{repo name: Binding} from the Brain's bindings.yaml ({} when absent/invalid)."""
    p = project_dir / "bindings.yaml"
    if not p.is_file():
        return {}
    try:
        from core.ddd_bindings import load_bindings
        return {b.repo: b for b in load_bindings(p).bindings}
    except (ValueError, OSError, yaml.YAMLError) as exc:  # a broken file reads as no repo
        logger.debug("project_agent: bindings.yaml of %s unreadable: %s", project_dir, exc)
        return {}


def general_brain_dir(workspace: Path) -> Path:
    """Where the General Brain's definition lives: ``<workspace>/Knowledge/Library``."""
    return Path(workspace) / "Knowledge" / "Library"


def repo_roots(project_dir: Path) -> dict[str, Path]:
    """{repo name: local checkout} for each binding whose checkout exists on this
    machine: its declared ``worktree``, else the bind-managed clone directory."""
    out: dict[str, Path] = {}
    for name, b in _bindings(Path(project_dir)).items():
        try:
            if b.worktree:
                root = Path(b.worktree).expanduser().resolve()
            else:
                from core.ddd_bindings import binding_worktree
                root = binding_worktree(b)
        except (ValueError, OSError):
            continue
        if root.is_dir():
            out[name] = root
    return out


def _skills(project_dir: Path) -> tuple[Optional[dict[str, str]], Optional[str]]:
    """({domain skill name: platform}, error) for the Brain's 4-capabilities/."""
    try:
        from core.ddd_skill_registry import scan_domain_skill_dirs
        dirs = scan_domain_skill_dirs(project_dir)
    except OSError as exc:
        return None, f"capabilities could not be read ({type(exc).__name__})"
    out: dict[str, str] = {}
    for d in dirs:
        out[d.name] = _skill_platform(d / "SKILL.md")
    return out, None


def _skill_platform(skill_md: Path) -> str:
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError:
        return "all"
    m = re.search(r"^platform:\s*([A-Za-z]+)\s*$", text, re.MULTILINE)
    return m.group(1).lower() if m else "all"


def _gates_dir(project_dir: Path) -> Path:
    from core.ddd_paths import ddd_path
    return ddd_path(project_dir, "gates")


def gate_files(project_dir: Path) -> dict[str, Path]:
    """{gate name: script} — the top-level ``*.py`` scripts of 3-gates/ that travel to a
    Hive, tests excluded."""
    from core.brain_sync import whitelisted
    root = _gates_dir(project_dir)
    if not root.is_dir():
        return {}
    out: dict[str, Path] = {}
    for p in sorted(root.iterdir()):
        if (p.is_file() and not p.is_symlink() and p.suffix == ".py" and not p.name.startswith("test_")
                and whitelisted(f"3-gates/{p.name}", False)):
            out[p.stem] = p
    return out


def _tree_files(root: Path, prefix: str) -> list[Path]:
    """The files under *root* that travel to a Hive (``core.brain_sync.whitelisted`` of
    ``<prefix>/<rel>``): the revision and the gates digest cover exactly what a Hive
    receives, so a Hive recomputes the same values from its copy."""
    from core.brain_sync import whitelisted
    if not root.is_dir():
        return []
    files = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if any(part in _GATE_SKIP_PARTS for part in rel.parts) or p.suffix == ".pyc":
            continue
        if p.is_file() and not p.is_symlink() and whitelisted(f"{prefix}/{rel.as_posix()}", False):
            files.append(p)
    return files


def _hash_files(h: "hashlib._Hash", base: Path, files: Iterable[Path]) -> None:
    for f in files:
        _hash_one(h, str(f.relative_to(base)), f.read_bytes())


def _hash_one(h: "hashlib._Hash", rel: str, data: bytes) -> None:
    h.update(rel.encode("utf-8") + b"\0")
    h.update(data)
    h.update(b"\0")


def gates_snapshot(project_dir: Path) -> tuple[str, dict[str, bytes]]:
    """(digest, {path relative to 3-gates/: bytes}) of every file under 3-gates/ that
    travels to a Hive (``_tree_files``), each read once: a gate that runs is run from these bytes, so it
    is the content the digest covers."""
    root = _gates_dir(project_dir)
    h = hashlib.sha256()
    files: dict[str, bytes] = {}
    for f in _tree_files(root, "3-gates"):
        rel = str(f.relative_to(root))
        data = f.read_bytes()
        _hash_one(h, rel, data)
        files[rel] = data
    return h.hexdigest(), files


def gates_digest(project_dir: Path) -> str:
    """sha256 of the files under 3-gates/ that travel to a Hive: the value a session
    pins and compares before it runs any of this Brain's gates."""
    return gates_snapshot(project_dir)[0]


# ── validate ─────────────────────────────────────────────────────────────────

def _check_profile(where: str, mode: dict[str, Any], out: list[str]) -> None:
    if mode.get("profile") not in PROFILES:
        out.append(f"{where}.profile {mode.get('profile')!r} is not a pipeline profile")
    for i, rule in enumerate(_list_of_dicts(mode.get("profile_rules"))):
        if rule.get("profile") not in PROFILES:
            out.append(f"{where}.profile_rules[{i}].profile {rule.get('profile')!r} is not a pipeline profile")
        if not str(rule.get("when") or "").strip():
            out.append(f"{where}.profile_rules[{i}].when is empty")


def _check_names(where: str, values: Any, allowed: Iterable[str], label: str, out: list[str]) -> None:
    allowed = set(allowed)
    bad = [v for v in _str_list(values) if v not in allowed]
    if bad:
        out.append(f"{where} names {label} that do not exist: {bad}")


def validate(doc: Any, project_dir: Optional[Path] = None) -> list[str]:
    """Problems in a parsed definition (any schema). Unknown keys are ignored. With
    ``project_dir``, also check every name the definition uses against the Brain on
    disk (skills, gates, repos)."""
    if not isinstance(doc, dict):
        return ["not a mapping"]
    d = normalize(doc)
    out: list[str] = []
    if d.get("status", "draft") not in STATUSES:
        out.append(f"status {d.get('status')!r} is not draft or reviewed")

    shared_places = d.get("placements") or []
    if not isinstance(shared_places, list) or not shared_places:
        out.append("placements is empty")
        shared_places = []
    elif [p for p in shared_places if p not in PLACEMENTS]:
        out.append(f"placements has unknown values {[p for p in shared_places if p not in PLACEMENTS]}")
    shared_reserved = set(_str_list(d.get("reserved_for_human")))
    unknown = sorted(shared_reserved - RESERVED_ACTIONS)
    if unknown:
        out.append(f"reserved_for_human has unknown actions {unknown}")
    shared_gates = set(_str_list((d.get("gates") or {}).get("enforce")))

    modes = _modes(d)
    if not modes:
        out.append("modes is empty")
    ids: set[str] = set()
    for i, m in enumerate(modes):
        mid = str(m.get("id") or "")
        where = f"modes[{mid or i}]"
        if not _MODE_ID_RE.fullmatch(mid):
            out.append(f"modes[{i}].id {mid!r} is not a short lower-case name")
        elif mid in ids:
            out.append(f"modes[{i}].id {mid!r} is used twice")
        ids.add(mid)
        if not str(m.get("when") or "").strip():
            out.append(f"{where}.when is empty")
        _check_profile(where, m, out)
        places = _mode_value(m, d, "placements") or []
        if not isinstance(places, list) or not places:
            out.append(f"{where}.placements is empty")
        else:
            wider = [p for p in places if p not in shared_places]
            if wider:
                out.append(f"{where}.placements {wider} are not in the shared placements (a mode only narrows)")
        if "reserved_for_human" in m:
            mine = set(_str_list(m.get("reserved_for_human")))
            if mine - RESERVED_ACTIONS:
                out.append(f"{where}.reserved_for_human has unknown actions {sorted(mine - RESERVED_ACTIONS)}")
            if shared_reserved - mine:
                out.append(f"{where}.reserved_for_human drops {sorted(shared_reserved - mine)} (a mode only adds)")
        if isinstance(m.get("gates"), dict) and "enforce" in m["gates"]:
            mine_g = set(_str_list(m["gates"].get("enforce")))
            if shared_gates - mine_g:
                out.append(f"{where}.gates.enforce drops {sorted(shared_gates - mine_g)} (a mode only adds)")
        delivery = m.get("delivery", "bundle")
        if delivery not in DELIVERIES:
            out.append(f"{where}.delivery {delivery!r} is not bundle or branch-pr")
        if delivery == "branch-pr" and m.get("pr_remote", "fork") not in PR_REMOTES:
            out.append(f"{where}.pr_remote {m.get('pr_remote')!r} is not fork or origin")
        intake_repos = _str_list((d.get("intake") or {}).get("repos"))
        if intake_repos:
            outside = [r for r in _str_list(m.get("repos")) if r not in intake_repos]
            if outside:
                out.append(f"{where}.repos {outside} are not in intake.repos")
    if d.get("default_mode") not in ids:
        out.append(f"default_mode {d.get('default_mode')!r} is not one of the modes")

    if project_dir is not None:
        out.extend(_reference_problems(d, Path(project_dir)))
    if doc.get("schema") != 2:
        out = _in_v1_terms(out)
    return out


def _in_v1_terms(problems: list[str]) -> list[str]:
    """A v1 file's problems named by its own keys (its one mode is internal)."""
    seen: list[str] = []
    for msg in problems:
        msg = (msg.replace("modes[default].profile_rules", "profile_rules")
               .replace("modes[default].profile", "default_profile")
               .replace("modes[default].placements", "placements")
               .replace("modes[default].", ""))
        if msg not in seen:
            seen.append(msg)
    return seen


def _unsynced_gate_files(project_dir: Path) -> list[str]:
    """A ``.py`` under 3-gates/ whose name the sync whitelist keeps home (a secret or
    archive name rule): it is neither a gate nor importable by one, here or on a Hive."""
    from core.brain_sync import whitelisted
    root = _gates_dir(project_dir)
    if not root.is_dir():
        return []
    out = []
    for p in sorted(root.rglob("*.py")):
        rel = p.relative_to(root).as_posix()
        if "__pycache__" in rel or p.is_symlink() or rel.split("/")[-1].startswith("."):
            continue
        if not whitelisted(f"3-gates/{rel}", False):
            out.append(f"3-gates/{rel} is not used: its name matches a secret or archive rule, so it "
                       "is not synced or hashed; rename it")
    return out


def _reference_problems(d: dict[str, Any], project_dir: Path) -> list[str]:
    out: list[str] = []
    skills, err = _skills(project_dir)
    if err:
        out.append(err)
    gates = gate_files(project_dir)
    out.extend(_unsynced_gate_files(project_dir))
    bindings = _bindings(project_dir)
    _check_names("gates.enforce", (d.get("gates") or {}).get("enforce"), gates, "gates", out)
    _check_names("intake.repos", (d.get("intake") or {}).get("repos"), bindings, "repos", out)
    for m in _modes(d):
        where = f"modes[{m.get('id')}]"
        if skills is not None:
            _check_names(f"{where}.skills", m.get("skills"), skills, "skills", out)
        if isinstance(m.get("gates"), dict):
            _check_names(f"{where}.gates.enforce", m["gates"].get("enforce"), gates, "gates", out)
        _check_names(f"{where}.repos", m.get("repos"), bindings, "repos", out)
        if m.get("delivery") == "branch-pr":
            for r in _str_list(m.get("repos")):
                b = bindings.get(r)
                if b is None:
                    continue
                if b.kind == "internal":
                    out.append(f"{where}: repo {r} is internal; only delivery bundle is allowed")
                elif "github.com" not in (b.clone or ""):
                    out.append(f"{where}: repo {r} is not on GitHub; branch-pr needs a GitHub remote")
    return out


def notes(doc: Any, project_dir: Path) -> list[str]:
    """Advisory remarks, not problems: a desktop/macos-only skill in a mode that may
    run on a Hive (the Hive does not project it)."""
    if not isinstance(doc, dict):
        return []
    d = normalize(doc)
    skills, _err = _skills(Path(project_dir))
    out: list[str] = []
    for m in _modes(d):
        places = _mode_value(m, d, "placements") or []
        if "hive" not in places or skills is None:
            continue
        for s in _str_list(m.get("skills")):
            if skills.get(s) in ("desktop", "macos"):
                out.append(f"modes[{m.get('id')}]: skill {s} is {skills[s]}-only and is not available on a Hive")
    return out


def revision(project_dir: Path) -> Optional[str]:
    """sha256 of the definition file, every skill directory its modes use and the
    3-gates/ tree (the files of each that travel to a Hive); None when the Brain has no definition. Any change to one of them
    gives a new revision, so a run pins exactly what it was dispatched with."""
    project_dir = Path(project_dir)
    p = project_dir / PROJECT_AGENT_FILE
    if not p.is_file() or p.is_symlink():
        return None
    try:
        raw = p.read_bytes()
        doc = yaml.safe_load(raw.decode("utf-8")) or {}
    except (OSError, UnicodeError, yaml.YAMLError):
        return None
    h = hashlib.sha256()
    h.update(raw)
    if isinstance(doc, dict):
        from core.ddd_paths import ddd_path
        caps = ddd_path(project_dir, "capabilities")
        used = sorted({s for m in _modes(normalize(doc)) for s in _str_list(m.get("skills"))})
        for s in used:
            d = caps / s
            if d.is_dir() and not d.is_symlink():
                h.update(b"skill:" + s.encode("utf-8") + b"\0")
                _hash_files(h, caps, _tree_files(d, f"4-capabilities/{s}"))
    h.update(b"gates:" + gates_digest(project_dir).encode("ascii"))
    return h.hexdigest()


# ── source check (host only, at review time) ─────────────────────────────────

SOURCE_CHECK_FILE = ".project-agent-check.json"


def check_sources(project_dir: Path) -> dict[str, Any]:
    """Check that every ``setup`` / ``validations`` row's ``source`` exists in the
    bound repo of its mode, and keep the result next to the definition. The sources
    live in the repository, not the Brain, so this runs on the host when the person
    reviews; a Hive reads the stored result and never re-checks. The result names the
    revision it was made for: a later edit makes it stale."""
    import json
    import time
    project_dir = Path(project_dir)
    raw = (project_dir / PROJECT_AGENT_FILE).read_text(encoding="utf-8")
    d = normalize(yaml.safe_load(raw) or {})
    checkouts = repo_roots(project_dir)
    missing: list[str] = []
    for m in _modes(d):
        roots = [checkouts[r] for r in _str_list(m.get("repos")) if r in checkouts]
        for key in ("setup", "validations"):
            for row in _list_of_dicts(m.get(key)):
                src = str(row.get("source") or "")
                if not src:
                    missing.append(f"modes[{m.get('id')}].{key}: a row has no source")
                elif not any((root / src).is_file() for root in roots) and not (project_dir / src).is_file():
                    missing.append(f"modes[{m.get('id')}].{key}: source {src} not found in the bound repo")
    result = {"revision": revision(project_dir), "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "ok": not missing, "missing": missing}
    (project_dir / SOURCE_CHECK_FILE).write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def source_check_current(project_dir: Path) -> Optional[dict[str, Any]]:
    """The stored source check when it was made for the current revision, else None."""
    import json
    p = Path(project_dir) / SOURCE_CHECK_FILE
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) and rec.get("revision") == revision(Path(project_dir)) else None


# ── load for the Brain Hub ───────────────────────────────────────────────────

def _mode_view(m: dict[str, Any], d: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(m.get("id") or ""),
        "when": str(m.get("when") or ""),
        "profile": m.get("profile"),
        "profileRules": [{"when": str(r.get("when") or ""), "profile": r.get("profile")}
                         for r in _list_of_dicts(m.get("profile_rules"))],
        "placements": _str_list(_mode_value(m, d, "placements")),
        "repos": _str_list(m.get("repos")),
        "setup": _rows(m.get("setup"), ("command", "source", "repo", "cwd")),
        "validations": _rows(m.get("validations"), ("id", "command", "source", "repo", "cwd")),
        "skills": _str_list(m.get("skills")),
        "delivery": str(m.get("delivery") or "bundle"),
        "prRemote": str(m.get("pr_remote") or "fork") if m.get("delivery") == "branch-pr" else None,
        "reservedForHuman": _str_list(m["reserved_for_human"]) if "reserved_for_human" in m
        else _str_list(d.get("reserved_for_human")),
        "gates": _str_list(m["gates"].get("enforce")) if isinstance(m.get("gates"), dict)
        and "enforce" in m["gates"] else _str_list((d.get("gates") or {}).get("enforce")),
    }


def load_project_agent(project_dir: Path) -> dict[str, Any] | None:
    """The definition as the Brain Hub shows it, or None when the Brain has none.

    Never raises: an unreadable or malformed file comes back with ``problems`` so the
    UI can say so instead of hiding it. The v1 keys the Hub already reads
    (``defaultProfile``, ``profileRules``, ``reviewFocus``, ``validations``,
    ``summary``) are filled from the default mode for either schema.
    """
    project_dir = Path(project_dir)
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
    d = normalize(doc)
    modes = [_mode_view(m, d) for m in _modes(d)]
    default = next((m for m in modes if m["id"] == d.get("default_mode")), modes[0] if modes else None)
    identity = d.get("identity") if isinstance(d.get("identity"), dict) else {}
    intake = d.get("intake") if isinstance(d.get("intake"), dict) else {}
    return {
        "path": PROJECT_AGENT_FILE,
        "schema": doc.get("schema", 1),
        "status": d.get("status", "draft"),
        "reviewedAt": d.get("reviewed_at"),
        "reviewedBy": d.get("reviewed_by"),
        "summary": str(identity.get("summary") or ""),
        "identity": {"summary": str(identity.get("summary") or ""),
                     "inScope": _str_list(identity.get("in_scope")),
                     "outOfScope": _str_list(identity.get("out_of_scope")),
                     "source": str(identity.get("source") or "")},
        "intake": {"handles": _str_list(intake.get("handles")), "repos": _str_list(intake.get("repos")),
                   "proposedAliases": _rows(intake.get("proposed_aliases"), ("name", "source"))},
        "confidence": str(d.get("confidence") or ""),
        "reviewFocus": _rows(d.get("watches"), ("area", "why", "source")),
        "reservedForHuman": _str_list(d.get("reserved_for_human")),
        "gates": _str_list((d.get("gates") or {}).get("enforce")),
        "placements": _str_list(d.get("placements")),
        "landing": {str(k): str(v) for k, v in (d.get("landing") or {}).items()}
        if isinstance(d.get("landing"), dict) else {},
        "modes": modes,
        "defaultMode": d.get("default_mode"),
        "defaultProfile": default["profile"] if default else None,
        "profileRules": default["profileRules"] if default else [],
        "validations": default["validations"] if default else [],
        "sources": _str_list(d.get("sources")),
        "revision": revision(project_dir),
        "problems": validate(doc, project_dir),
        "notes": notes(doc, project_dir),
        "sourceCheck": source_check_current(project_dir),
        "sourcePaths": _source_paths(d, project_dir),
    }


def _source_paths(d: dict[str, Any], project_dir: Path) -> dict[str, str]:
    """{source as written: absolute path} for every source of the definition that
    resolves to a file — a Brain document, ``<repo>:<path>``, or (key
    ``<repo>:<path>``) a setup / validations row's file in its repo — so the review
    card can open it. Sources that do not resolve are left out."""
    from core.brain_bootstrap.repo_read import confine
    from core.ddd_paths import ddd_path
    from core.project_registry import DDD_CANONICAL_DOCS
    roots = repo_roots(project_dir)
    out: dict[str, str] = {}

    def add(src: Any, repo: Optional[str] = None) -> None:
        if not isinstance(src, str) or not src:
            return
        key = f"{repo}:{src}" if repo else src
        if key in out:
            return
        name, sep, rel = key.partition(":")
        if sep and name in roots:
            p = confine(roots[name], rel)
        elif src in DDD_CANONICAL_DOCS:
            p = ddd_path(project_dir, src)
        else:
            p = confine(project_dir, src)
        if p is not None and p.is_file():
            out[key] = str(p)

    ident = d.get("identity") if isinstance(d.get("identity"), dict) else {}
    add(ident.get("source"))
    for w in _list_of_dicts(d.get("watches")):
        add(w.get("source"))
    intake = d.get("intake") if isinstance(d.get("intake"), dict) else {}
    for a in _list_of_dicts(intake.get("proposed_aliases")):
        add(a.get("source"))
    for s in _str_list(d.get("sources")):
        add(s)
    for m in _modes(d):
        add(m.get("source"))
        for key in ("setup", "validations"):
            for row in _list_of_dicts(m.get(key)):
                add(row.get("source"), row.get("repo") if isinstance(row.get("repo"), str) else None)
    return out


def project_agent_status(project_dir: Path) -> str | None:
    """The one word the Brain Hub home needs: ``draft`` (waits for the person to
    confirm), ``reviewed``, ``invalid`` (the file has problems), or None (no file)."""
    pa = load_project_agent(project_dir)
    if pa is None:
        return None
    if pa.get("problems"):
        return "invalid"
    return "reviewed" if pa.get("status") == "reviewed" else "draft"
