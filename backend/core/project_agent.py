"""project_agent — the Project Agent definition of a DDD (Brain), section ⑤ Delivery.

Design: docs/design/2026-10-28-project-agent-flow.md §4.

The Project Agent tells a Hive run how to do one Brain's work (design
docs/design/2026-11-01-project-agent-simple.md). A Brain has ONE definition; the kinds
of work inside it are **modes** (when, repos, tools, setup, validations, skills). Shared:
``identity.summary``, ``watches``, ``gates.enforce`` (a mode only adds gates),
``intake.repos``, ``default_mode``.

It lives next to bindings.yaml as ``project-agent.yaml``. The standard one is written
when the Brain is created and drafted from its knowledge (core/project_agent_draft);
the agent keeps it current. Nothing is confirmed: a dispatch uses the latest, and the
agent fixes what admission finds wrong. The actions kept for the person are the
product's (``RESERVED_ACTIONS``, the same for every Brain); ``status``, ``placements``,
``delivery`` and ``reserved_for_human`` an older file carries are not read. A v1 file
(``validations`` at the top) reads as a v2 definition with one mode.

How a task runs (native, or a pipeline profile) is not part of the definition: each
dispatch picks it from the request (design 2026-10-30-run-spec §3). ``profile`` /
``profile_rules`` a file still carries (in a mode, or v1's ``default_profile``) are not
read; ``notes`` says they can be removed.

Key public symbols:
- ``PROJECT_AGENT_FILE``   the file name (project-root relative)
- ``RESERVED_ACTIONS``     what a Project Agent session always asks the person
- ``normalize(doc)``       any schema -> the v2 shape (shared part + modes)
- ``validate(doc, project_dir=None)`` problems ([] = valid); reference checks when a
                           project dir is given
- ``is_general_name(name)`` the General Brain's name, in any case
- ``revision(project_dir)`` sha256 of the definition + the skill dirs it uses + 3-gates/
- ``gate_files(project_dir)`` / ``gates_digest(project_dir)`` the gate scripts and the
                           digest a Hive checks before running them
- ``source_problems``      host only: each command's source must be in the bound repo
- ``load_project_agent``   parse + validate -> dict for the Brain Hub, or None if absent
- ``project_agent_status`` ready / invalid / None, for the Hub home
"""
from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml

logger = logging.getLogger(__name__)

PROJECT_AGENT_FILE = "project-agent.yaml"
#: the actions reserved for the person, the same for every Brain and mode: a dispatched
#: session refuses them without asking anyone — send_external_message by the external-tool
#: gate (core/security_hooks.create_external_approval_gate), the others by the
#: dangerous-command gate's dispatched-task branch, which reads the predicate
#: core/security_hooks._is_reserved_action
RESERVED_ACTIONS: frozenset[str] = frozenset(
    {"push", "open_pr", "publish_release", "send_external_message"})
#: the Brain of tasks that belong to no project: its knowledge is Knowledge/Library and
#: its Project Agent is the Runner Agent (design 2026-10-28-project-agent-flow §3, §5)
GENERAL_BRAIN = "General"


def is_general_name(name: object) -> bool:
    """True for the General Brain's name in any case: no ``Projects/`` Brain may carry it
    (dispatch, sync and lesson scope treat the name as the General Brain)."""
    return isinstance(name, str) and name.casefold() == GENERAL_BRAIN.casefold()

_MAX_BYTES = 64 * 1024
_MODE_ID_RE = re.compile(r"[a-z][a-z0-9-]{0,39}")
#: a mode's ``tools`` row: ``<tool>@<version>`` as mise takes it (python@3.11, node@22,
#: rust@stable). The Hive's hive/bin/task-tools checks the same pattern before it runs.
TOOL_RE = re.compile(r"[a-z][a-z0-9-]{0,30}@[A-Za-z0-9][A-Za-z0-9._+-]{0,40}")
#: at most this many tools per mode
MAX_TOOLS = 20


def mode_tools(m: dict[str, Any]) -> list[str]:
    """The ``<tool>@<version>`` strings of a mode's ``tools`` rows that are well formed,
    in order (``validate`` names the others)."""
    return [r["tool"] for r in _list_of_dicts(m.get("tools"))
            if isinstance(r.get("tool"), str) and TOOL_RE.fullmatch(r["tool"])][:MAX_TOOLS]
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
    one mode, ``default``, carrying its validations; its top-level
    ``summary`` / ``review_focus`` become ``identity.summary`` / ``watches``."""
    if doc.get("schema") == 2:
        return doc
    mode = {"id": "default", "when": "any change", "validations": doc.get("validations") or []}
    return {
        "schema": 2,
        "identity": {"summary": doc.get("summary") or ""},
        "intake": {},
        "watches": doc.get("review_focus") or [],
        "gates": {"enforce": []},
        "modes": [mode],
        "default_mode": "default",
    }


def _modes(doc: dict[str, Any]) -> list[dict[str, Any]]:
    return _list_of_dicts(doc.get("modes"))


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


def skill_network(skill_md: Path) -> str:
    """A skill's ``network:`` frontmatter (``private``: it needs a private network a
    Hive cannot reach, design 2026-10-30-dispatch-center §2.2); ``any`` without one."""
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError:
        return "any"
    fm = re.match(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", text, re.DOTALL)     # the frontmatter only
    m = re.search(r"^network:\s*([A-Za-z]+)\s*$", fm.group(1), re.MULTILINE) if fm else None
    return m.group(1).lower() if m else "any"


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
    from core.brain_sync import allowlisted
    root = _gates_dir(project_dir)
    if not root.is_dir():
        return {}
    out: dict[str, Path] = {}
    for p in sorted(root.iterdir()):
        if (p.is_file() and not p.is_symlink() and p.suffix == ".py" and not p.name.startswith("test_")
                and allowlisted(f"3-gates/{p.name}", _is_general_dir(project_dir))):
            out[p.stem] = p
    return out


def _is_general_dir(project_dir: Path) -> bool:
    """True for the General Brain's directory (``Knowledge/Library``), which travels
    under the General allowlist (its Markdown only)."""
    p = Path(project_dir)
    return p.name == "Library" and p.parent.name == "Knowledge"


def _hive_ignore(project_dir: Path) -> Optional[list[str]]:
    """The Brain's ``.hive-ignore`` globs ([] without one), or None when the list
    cannot be read (the package would stop too)."""
    from core.brain_sync import SyncError, hive_ignore_patterns
    try:
        return hive_ignore_patterns(Path(project_dir))
    except SyncError:
        return None


def _tree_files(root: Path, prefix: str, general: bool = False,
                ignored: Iterable[str] = ()) -> list[Path]:
    """The files under *root* that travel to a Hive (``core.brain_sync.allowlisted`` of
    ``<prefix>/<rel>``, less those the Brain's ``.hive-ignore`` keeps home): the revision
    and the gates digest cover exactly what a Hive receives, so a Hive recomputes the
    same values from its copy."""
    from core.brain_sync import allowlisted, is_ignored
    ignored = list(ignored)
    if not root.is_dir():
        return []
    files = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if any(part in _GATE_SKIP_PARTS for part in rel.parts) or p.suffix == ".pyc":
            continue
        travel = f"{prefix}/{rel.as_posix()}"
        if (p.is_file() and not p.is_symlink() and allowlisted(travel, general)
                and not is_ignored(travel, ignored)):
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
    for f in _tree_files(root, "3-gates", _is_general_dir(project_dir), _hive_ignore(project_dir) or []):
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

def _check_names(where: str, values: Any, allowed: Iterable[str], label: str, out: list[str]) -> None:
    allowed = set(allowed)
    bad = [v for v in _str_list(values) if v not in allowed]
    if bad:
        out.append(f"{where} names {label} that do not exist: {bad}")


def validate(doc: Any, project_dir: Optional[Path] = None) -> list[str]:
    """Problems in a parsed definition (any schema). Unknown keys, and the keys an
    older file carries that are no longer read, are ignored. With ``project_dir``, also
    check every name the definition uses against the Brain on disk (skills, gates,
    repos)."""
    if not isinstance(doc, dict):
        return ["not a mapping"]
    d = normalize(doc)
    out: list[str] = []
    shared_gates = set(_str_list((d.get("gates") or {}).get("enforce")))
    modes = _modes(d)
    if not modes:
        out.append("modes is empty")
    ids: set[str] = set()
    intake_repos = _str_list((d.get("intake") or {}).get("repos"))
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
        if isinstance(m.get("gates"), dict) and "enforce" in m["gates"]:
            mine_g = set(_str_list(m["gates"].get("enforce")))
            if shared_gates - mine_g:
                out.append(f"{where}.gates.enforce drops {sorted(shared_gates - mine_g)} (a mode only adds)")
        out.extend(_tool_problems(where, m.get("tools")))
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


def _tool_problems(where: str, tools: Any) -> list[str]:
    """A mode's ``tools``: a list of ``{tool, source}`` rows, ``tool`` matching TOOL_RE,
    one row per tool name."""
    if tools is None:
        return []
    if not isinstance(tools, list):
        return [f"{where}.tools is not a list"]
    out: list[str] = []
    names: set[str] = set()
    for i, r in enumerate(tools):
        t = r.get("tool") if isinstance(r, dict) else None
        if not isinstance(t, str) or not TOOL_RE.fullmatch(t):
            out.append(f"{where}.tools[{i}] is not a <tool>@<version> row (python@3.11)")
            continue
        name = t.split("@", 1)[0]
        if name in names:
            out.append(f"{where}.tools[{i}]: {name} is listed twice")
        names.add(name)
    if len(tools) > MAX_TOOLS:
        out.append(f"{where}.tools has more than {MAX_TOOLS} rows")
    return out


def _in_v1_terms(problems: list[str]) -> list[str]:
    """A v1 file's problems named by its own keys (its one mode is internal)."""
    seen: list[str] = []
    for msg in problems:
        msg = msg.replace("modes[default].", "")
        if msg not in seen:
            seen.append(msg)
    return seen


def _unsynced_gate_files(project_dir: Path) -> list[str]:
    """A ``.py`` under 3-gates/ whose name the sync allowlist keeps home (a secret or
    archive name rule): it is neither a gate nor importable by one, here or on a Hive."""
    from core.brain_sync import allowlisted
    root = _gates_dir(project_dir)
    if not root.is_dir():
        return []
    out = []
    for p in sorted(root.rglob("*.py")):
        rel = p.relative_to(root).as_posix()
        if "__pycache__" in rel or p.is_symlink() or rel.split("/")[-1].startswith("."):
            continue
        if not allowlisted(f"3-gates/{rel}", False):
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
    return out


def revision(project_dir: Path) -> Optional[str]:
    """sha256 of the definition file, every skill directory its modes use and the
    3-gates/ tree (the files of each that travel to a Hive); None when the Brain has no
    definition or its ``.hive-ignore`` cannot be read. Any change to one of them
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
    ignored = _hive_ignore(project_dir)
    if ignored is None:      # an unreadable .hive-ignore: no revision, so no dispatch
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
                _hash_files(h, caps, _tree_files(d, f"4-capabilities/{s}", _is_general_dir(project_dir),
                                                 ignored))
    h.update(b"gates:" + gates_digest(project_dir).encode("ascii"))
    return h.hexdigest()


# ── source check (host only, before a dispatch) ──────────────────────────────

def source_problems(project_dir: Path, mode: Optional[str] = None) -> list[str]:
    """The ``tools`` / ``setup`` / ``validations`` rows whose ``source`` is in neither the mode's
    bound repo nor the Brain; with *mode*, only that mode's rows (a dispatch runs one
    mode, so another mode's stale row does not refuse it). The sources live in the
    repositories, so this runs on the host before a dispatch (core/task_spec.admit); a
    Hive never checks it."""
    project_dir = Path(project_dir)
    raw = (project_dir / PROJECT_AGENT_FILE).read_text(encoding="utf-8")
    d = normalize(yaml.safe_load(raw) or {})
    checkouts = repo_roots(project_dir)
    missing: list[str] = []
    for m in _modes(d):
        if mode is not None and m.get("id") != mode:
            continue
        roots = [checkouts[r] for r in _str_list(m.get("repos")) if r in checkouts]
        for key in ("tools", "setup", "validations"):
            for row in _list_of_dicts(m.get(key)):
                src = str(row.get("source") or "")
                if not src:
                    missing.append(f"modes[{m.get('id')}].{key}: a row has no source")
                elif not any((root / src).is_file() for root in roots) and not (project_dir / src).is_file():
                    missing.append(f"modes[{m.get('id')}].{key}: source {src} not found in the bound repo")
    return missing


# ── load for the Brain Hub ───────────────────────────────────────────────────

def _mode_view(m: dict[str, Any], d: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(m.get("id") or ""),
        "when": str(m.get("when") or ""),
        "repos": _str_list(m.get("repos")),
        "tools": mode_tools(m),
        "setup": _rows(m.get("setup"), ("command", "source", "repo", "cwd")),
        "validations": _rows(m.get("validations"), ("id", "command", "source", "repo", "cwd")),
        "skills": _str_list(m.get("skills")),
        "gates": _str_list(m["gates"].get("enforce")) if isinstance(m.get("gates"), dict)
        and "enforce" in m["gates"] else _str_list((d.get("gates") or {}).get("enforce")),
    }


def load_project_agent(project_dir: Path) -> dict[str, Any] | None:
    """The definition as the Brain Hub shows it, or None when the Brain has none.

    Never raises: an unreadable or malformed file comes back with ``problems`` so the
    UI can say so instead of hiding it. The v1 keys the Hub already reads
    (``reviewFocus``, ``validations``, ``summary``) are filled from the default mode for
    either schema.
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
        "summary": str(identity.get("summary") or ""),
        "reviewFocus": _rows(d.get("watches"), ("area", "why", "source")),
        "rules": _rule_view(project_dir),
        "reservedForHuman": sorted(RESERVED_ACTIONS),
        "gates": _str_list((d.get("gates") or {}).get("enforce")),
        "repos": _str_list(intake.get("repos")),
        "modes": modes,
        "defaultMode": d.get("default_mode"),
        "validations": default["validations"] if default else [],
        "revision": revision(project_dir),
        "problems": validate(doc, project_dir),
        # the typical requests routing compares a task with (core/task_spec.route)
        "handles": [h for h in _str_list(intake.get("handles"))][:8],
    }


def _rule_view(project_dir: Path) -> list[dict[str, Any]]:
    """The Brain's rules as the Hub lists them: the person's words, the day, and whether
    the words were verified when added (core.project_agent_rules); file order."""
    from core.project_agent_rules import rule_rows
    return rule_rows(project_dir)


def one_line(value: Any) -> str:
    """Definition or rule text as one line, whole: a newline in it cannot start a section
    of the prompt it is put into."""
    return " ".join(str(value or "").split())


def render_contract(project_dir: Path, brain: str) -> str:
    """The Project Agent block a session working for *brain* reads (design
    docs/design/2026-11-09-project-agent-contract.md §3.1): the summary, each mode's
    when, checks (to pick by what the change touches) and skills, the watches, the
    person's verified rules word for word, the gate names and how to add a rule. No
    setup: those commands build a fresh machine. Empty when the Brain has no readable
    definition. Every item whole and on one line (one_line)."""
    view = load_project_agent(Path(project_dir))
    if not view or "modes" not in view:
        return ""
    from core.project_agent_rules import verified_rules
    rules, _unverified = verified_rules(Path(project_dir))
    lines = [f"## Project Agent — {brain}",
             f"You are working for the {brain} Brain. Below is its Project Agent contract: "
             "the Brain's definition and the person's rules for it; the product's rules (SOUL, "
             "AGENT, the gates) rank above it."]
    if view.get("summary"):
        lines.append(one_line(view["summary"]))
    modes = view.get("modes") or []
    if modes:
        lines.append("Kinds of work (pick the one whose 'when' fits and say which):")
        for m in modes:
            line = f"- {one_line(m['id'])}: {one_line(m['when'])}"
            if m.get("validations"):
                line += (". The Brain's checks; run the ones this change touches: "
                         + "; ".join(f"{one_line(v['id'])}: `{one_line(v['command'])}`"
                                     for v in m["validations"]))
            if m.get("skills"):
                line += f". Skills: {', '.join(one_line(s) for s in m['skills'])}"
            lines.append(line)
    if view.get("reviewFocus"):
        lines.append("What every review of work here looks at:")
        lines += [f"- {one_line(w['area'])}: {one_line(w['why'])}" for w in view["reviewFocus"]]
    if rules:
        lines.append("The person's rules for this Brain, in their words:")
        lines += [f"- 「{one_line(r['said'])}」" for r in rules]
    if view.get("gates"):
        lines.append(f"Brain gates (run in dispatched sessions): {', '.join(one_line(g) for g in view['gates'])}.")
    lines.append("When the person states a standing requirement for this Brain, record it with "
                 f"`\"$SWARM_BACKEND_BIN\" py -m scripts.project_agent_rule add --brain {brain} --said "
                 "\"<their whole sentences, copied exactly>\"` and tell them.")
    return "\n".join(lines)


def contract_for_run(project_dir: Path, rules: Optional[list[str]] = None) -> Optional[dict[str, Any]]:
    """What a pipeline run records for its reviewers: ``{"rules": [...], "watches": [{area,
    why}]}``, or None when there is neither. *rules* None = the Brain's verified rules on
    this machine (a Hive passes the ones its dispatch carried). Never raises."""
    try:
        if rules is None:
            from core.project_agent_rules import verified_rules
            rules = [r["said"] for r in verified_rules(Path(project_dir))[0]]
        view = load_project_agent(Path(project_dir)) or {}
        watches = [{"area": w["area"], "why": w["why"]} for w in view.get("reviewFocus") or []
                   if w.get("area") and w.get("why")]
    except Exception:  # noqa: BLE001 — a run is created without it rather than not at all
        logger.debug("contract_for_run failed for %s", project_dir, exc_info=True)
        return None
    rules = [r for r in rules or [] if isinstance(r, str) and r.strip()]
    return {"rules": rules, "watches": watches} if rules or watches else None


#: the standard mode that changes a bound repo's code: it needs a check to run
CODE_CHANGE_MODE = "code-change"


def dispatch_problems(project_dir: Path, mode: Optional[str] = None) -> list[str]:
    """Why this Brain's definition cannot run a Hive task (empty: it can): its problems
    (``validate``), schema 1, a definition not drafted yet (no summary or no typical
    requests), a ``setup`` / ``validations`` command whose source is not there, a
    code-change mode with no check command. With *mode*, the source and check rules
    look at that mode only (a dispatch runs one mode). The one rule the P6 gate and
    admission (core/task_spec.admit) both use, so a Brain P6 calls ready is admitted
    and the reverse; whoever finds a problem is the agent, which fixes the file."""
    from core.project_agent_standard import is_skeleton
    project_dir = Path(project_dir)
    p = project_dir / PROJECT_AGENT_FILE
    if not p.is_file() or p.is_symlink():
        return [f"no {PROJECT_AGENT_FILE}"]
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeError, yaml.YAMLError):
        return [f"{PROJECT_AGENT_FILE} could not be parsed"]
    if not isinstance(raw, dict):
        return [f"{PROJECT_AGENT_FILE} is not a mapping"]
    out = list(validate(raw, project_dir))
    if raw.get("schema", 1) != 2:
        out.append("the definition is schema 1: rewrite it with modes (schema 2)")
        return out
    if is_skeleton(raw):
        out.append("not drafted yet: it needs a summary and typical requests "
                   "(artifact_cli project-agent-draft)")
    out.extend(source_problems(project_dir, mode))
    d = normalize(raw)
    for m in _modes(d):
        if mode is not None and m.get("id") != mode:
            continue
        if m.get("id") == CODE_CHANGE_MODE and not _list_of_dicts(m.get("validations")):
            out.append(f"modes[{CODE_CHANGE_MODE}] changes code and has no check command "
                       "(validations)")
    return out


def project_agent_status(project_dir: Path) -> str | None:
    """The one word the Brain Hub home needs: ``ready`` (a Hive task can run on it),
    ``invalid`` (``dispatch_problems`` found something; the agent fixes it), or None
    (no file)."""
    if load_project_agent(project_dir) is None:
        return None
    return "invalid" if dispatch_problems(project_dir) else "ready"
