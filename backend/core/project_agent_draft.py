"""Draft a Brain's Project Agent definition (design 2026-10-28-project-agent-flow §5).

The model judges, a deterministic tool writes — the same split as
``brain_bootstrap/apply``:

- ``survey(project_dir)`` lists the facts a definition is built from: the bound repos,
  the Brain's skills (with their platform) and gates, the dev commands its repos
  declare (``validations.json`` entries and the parsed CI / Makefile / package.json /
  tox / nox commands, each with an id), the Brain's documents, and the format of the
  proposal the model writes.
- ``apply(project_dir, proposal)`` builds a ``schema: 2`` definition. Code decides the
  deterministic fields (``intake.repos``, the standard modes of the Brain's shape from
  core/project_agent_standard — research, plus code-change with a bound repo or write
  without one — skills, gates) and writes the keys in the standard order. The model's
  proposal adds the judged fields (identity summary, handles, watches, extra modes,
  each mode's ``when``); every judged row names a source, and a row whose source does
  not exist is not written. Every ``setup`` / ``validations`` command must be one the
  cited file declares, copied verbatim (a CI ``run:`` block may be cited line by line).
  The result is checked with ``project_agent.validate`` before it is written; nothing
  is confirmed (design 2026-11-01-project-agent-simple), and a later run merges with
  what is there.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shlex
import time
from pathlib import Path
from typing import Any, Optional

import yaml

from core.brain_bootstrap import repo_read as rr
from core.brain_bootstrap.commands import parse_commands
from core import project_agent_standard as std
from core.project_agent import (PROJECT_AGENT_FILE, _MODE_ID_RE,
                                _bindings, _skills, gate_files, repo_roots, validate)

logger = logging.getLogger(__name__)

#: an install / environment step (``setup``), recognised in a declared command
_SETUP_RE = re.compile(
    r"\b(pip3? install|uv (sync|venv|pip install)|npm (ci|install)|yarn install|"
    r"pnpm install|bun install|poetry install|bundle install|go mod download|cargo fetch)\b")
#: an install of a tool for the whole machine, not of the repository's dependencies
_GLOBAL_INSTALL_RE = re.compile(r"\s(-g|--global)(\s|$)|\byarn global\b")
#: a check (``validations``), recognised in a declared command or its name
_CHECK_RE = re.compile(
    r"\b(pytest|tox|nox|vitest|jest|mocha|go (test|vet)|cargo (test|check|clippy)|ruff|"
    r"flake8|mypy|pyright|eslint|tsc|lint|test|typecheck)\b")
_COMMAND_KINDS = ("ci_run", "make", "just", "npm_script", "tox_env", "nox_session")
_TEXT_MAX = 400
_DRAFT_DIR = ".artifacts/project-agent"

#: the proposal format the drafting agent reads; its keys come from the standard
#: (core/project_agent_standard.PROPOSAL_KEYS)
PROPOSAL_CONTRACT: dict[str, Any] = {
    "format": "one JSON object; every key is optional; every judged row names a source",
    "source": ("a Brain document (PRODUCT.md, TECH.md, IMPROVEMENT.md, PROJECT.md, AGENTS.md, "
               "bindings.yaml or any file under the Brain), or '<repo>:<path>' for a file "
               "in a bound repo; a row whose source does not exist is not written"),
    "keys": {
        **std.PROPOSAL_KEYS,
        "modes": [{
            "id": ("research | code-change (with a bound repo) | write (without one) | "
                   "a new id ([a-z][a-z0-9-]*)"),
            "source": "str",
            **std.MODE_PROPOSAL_KEYS,
        }],
    },
    "defaults": ("the standard modes (templates/project-agents/STANDARD.md): research always; "
                 "code-change when the Brain has a bound repo, its setup / validations default "
                 "to the install steps / the validations.json entries (else the check commands) "
                 "the repos declare; write when it has none"),
}


# ── facts ────────────────────────────────────────────────────────────────────

def _command_id(n: int) -> str:
    return f"c{n}"


def _validations_json(repo: str, root: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(rr.safe_read_text(root, "validations.json") or "null")
    except ValueError:
        return []
    out = []
    for e in (data.get("entries") if isinstance(data, dict) else None) or []:
        argv = e.get("argv") if isinstance(e, dict) else None
        if isinstance(argv, list) and argv and all(isinstance(a, str) for a in argv):
            item = {"repo": repo, "source": "validations.json", "kind": "validations_json",
                    "name": str(e.get("id") or "")[:80], "command": shlex.join(argv), "role": "validation"}
            if _cwd(e.get("cwd")):
                item["cwd"] = _cwd(e.get("cwd"))
            out.append(item)
    return out


def _cwd(v: Any) -> Optional[str]:
    """A working directory as one spelling: ``./desktop/`` and ``desktop`` are the same;
    the repo root is None."""
    if not isinstance(v, str):
        return None
    v = v.strip()
    while v.startswith("./"):
        v = v[2:]
    v = v.rstrip("/")
    return v if v not in ("", ".") else None


def _role(c: dict[str, Any]) -> str:
    text = f"{c.get('command', '')} {c.get('name', '')} {c.get('runs', '')}"
    if "${{" in c.get("command", "") or "${{" in (c.get("cwd") or ""):
        return "other"          # a GitHub expression: only runnable inside Actions
    if c["kind"] == "ci_run" and _SETUP_RE.search(c.get("command", "")) \
            and not _GLOBAL_INSTALL_RE.search(c.get("command", "")):
        return "setup"
    return "validation" if _CHECK_RE.search(text) else "other"


def _repo_commands(repo: str, root: Path) -> list[dict[str, Any]]:
    out = _validations_json(repo, root)
    for c in parse_commands(root, rr.list_files(root)):
        if c.get("kind") in _COMMAND_KINDS:
            item = {"repo": repo, **{k: c[k] for k in ("source", "kind", "name", "command") if k in c}}
            if _cwd(c.get("cwd")):
                item["cwd"] = _cwd(c.get("cwd"))
            item["role"] = _role(c)
            out.append(item)
    return out


_UNREADABLE: dict[str, Any] = {"unreadable": True}


def _existing(project_dir: Path) -> Optional[dict[str, Any]]:
    """The current definition, None when there is none, or ``_UNREADABLE`` when a file
    is there but cannot be read as a mapping (a hand edit with a typo: it is still the
    Brain's definition, so it must never be treated as absent)."""
    p = project_dir / PROJECT_AGENT_FILE
    if not p.exists() and not p.is_symlink():
        return None
    try:
        doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError):
        return _UNREADABLE
    return doc if isinstance(doc, dict) else _UNREADABLE


def survey(project_dir: Path) -> dict[str, Any]:
    """The facts a definition is built from, plus the proposal format."""
    project_dir = Path(project_dir)
    bindings = _bindings(project_dir)
    bindings_error = None
    if not bindings and (project_dir / "bindings.yaml").is_file():
        # an invalid file reads as "no repo"; say so, or the draft silently loses
        # its code-change mode
        try:
            from core.ddd_bindings import load_bindings
            load_bindings(project_dir / "bindings.yaml")
        except Exception as exc:  # noqa: BLE001 — reported, never raised
            bindings_error = f"bindings.yaml cannot be read: {type(exc).__name__}"
    roots = repo_roots(project_dir)
    skills, skills_error = _skills(project_dir)
    commands: list[dict[str, Any]] = []
    for name in sorted(roots):
        commands += _repo_commands(name, roots[name])
    # the model reads these, so repo text is scanned like the brain survey's: secrets
    # redacted, text that reads like an injection dropped (such a command is then not
    # declared by its file and never reaches a draft)
    from core.brain_bootstrap.survey import _guard_parts
    _guard_parts({"commands": commands})
    for n, c in enumerate(commands, 1):
        c["id"] = _command_id(n)
    from core.project_registry import DDD_CANONICAL_DOCS
    docs = [d for d in (*DDD_CANONICAL_DOCS, "AGENTS.md", "bindings.yaml") if _brain_file(project_dir, d)]
    existing = _existing(project_dir)
    return {
        "brain": project_dir.name,
        "existing": None if existing is None else {"unreadable": True} if existing is _UNREADABLE
        else {"schema": existing.get("schema", 1)},
        "repos": [{"name": n, "kind": b.kind,
                   "remote_kind": getattr(b.delivery_contract, "remote_kind", None),
                   "checkout": n in roots} for n, b in bindings.items()],
        "skills": [{"name": k, "platform": v} for k, v in sorted((skills or {}).items())],
        "bindings_error": bindings_error,
        "skills_error": skills_error,
        "gates": sorted(gate_files(project_dir)),
        "commands": commands,
        "docs": docs,
        "contract": PROPOSAL_CONTRACT,
    }


# ── sources ──────────────────────────────────────────────────────────────────

def _brain_file(project_dir: Path, src: str) -> bool:
    from core.ddd_paths import ddd_path
    from core.project_registry import DDD_CANONICAL_DOCS
    if src in DDD_CANONICAL_DOCS:
        return ddd_path(project_dir, src).is_file()
    p = rr.confine(project_dir, src)
    return p is not None and p.is_file()


def _source_ok(project_dir: Path, roots: dict[str, Path], src: Any) -> bool:
    if not isinstance(src, str) or not src.strip() or len(src) > 300:
        return False
    repo, sep, rel = src.partition(":")
    if sep and repo in roots:
        p = rr.confine(roots[repo], rel)
        return p is not None and p.is_file()
    return _brain_file(project_dir, src)


_OPEN_RE = re.compile(r"(?:^|[;&|(]\s*|\b(?:then|do|else)\s+)(if|case|for|while|until)\b")
_CLOSE_RE = re.compile(r"(?:^|[;&|]\s*)(fi|esac|done)\b")


def _complete(lines: list[str]) -> bool:
    """True when *lines* is a whole piece of shell: no line continues onto a line left
    out, and every if / case / loop it opens is closed."""
    if lines and lines[-1].endswith("\\"):
        return False
    opened = sum(len(_OPEN_RE.findall(ln)) for ln in lines)
    closed = sum(len(_CLOSE_RE.findall(ln)) for ln in lines)
    return opened == closed


def _normal_source(root: Path, source: str) -> Optional[str]:
    """*source* as the repo-relative path it resolves to (``a/../b`` → ``b``), or None
    when it is outside the repo, secret or missing."""
    p = rr.confine(root, source)
    if p is None or not p.is_file():
        return None
    return p.relative_to(Path(root).resolve()).as_posix()


def _declared(root: Path, source: str, command: str, cwd: Optional[str]) -> bool:
    """True when *source* (repo-relative, normalised) in *root* declares *command*
    (with *cwd*) verbatim: a ``validations.json`` entry, a parsed command of that file,
    or the first lines of a CI ``run:`` block — a prefix, so no line it depends on
    (a ``cd``, an ``export``, an activated venv) is left out, and a complete piece of
    shell."""
    if source == "validations.json":
        return any(c["command"] == command and c.get("cwd") == cwd
                   for c in _validations_json("", root))
    lines = [ln.strip() for ln in command.splitlines() if ln.strip()]
    for c in parse_commands(root, [source]):
        if _cwd(c.get("cwd")) != cwd or "${{" in (c.get("cwd") or ""):
            continue
        if c["command"] == command:
            return True
        if c["kind"] == "ci_run" and lines:
            block = [ln.strip() for ln in c["command"].splitlines() if ln.strip()]
            if block[:len(lines)] == lines and _complete(lines):
                return True
    return False


# ── apply ────────────────────────────────────────────────────────────────────

def _text(v: Any) -> Optional[str]:
    return v.strip()[:_TEXT_MAX] if isinstance(v, str) and v.strip() else None


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:40] or "check"


class _Builder:
    def __init__(self, project_dir: Path, facts: dict[str, Any]):
        self.dir = project_dir
        self.facts = facts
        self.roots = repo_roots(project_dir)
        self.commands = {c["id"]: c for c in facts["commands"]}
        self.dropped: list[str] = []
        self.sources: set[str] = set()

    def drop(self, where: str, why: str) -> None:
        self.dropped.append(f"{where}: {why}")

    def sourced(self, where: str, row: Any) -> bool:
        src = row.get("source") if isinstance(row, dict) else None
        if not _source_ok(self.dir, self.roots, src):
            self.drop(where, f"source {src!r} does not exist" if src else "no source")
            return False
        self.sources.add(src)
        return True

    def command_row(self, where: str, item: Any, with_id: bool,
                    repos: list[str]) -> Optional[dict[str, str]]:
        if isinstance(item, str):
            c = self.commands.get(item)
            if c is None:
                self.drop(where, f"command id {item!r} is not in facts.commands")
                return None
            item = {"id": c["name"], **{k: c[k] for k in ("command", "source", "repo", "cwd") if k in c}}
        if not isinstance(item, dict):
            self.drop(where, "not a command id or an object")
            return None
        command, source = _text(item.get("command")), _text(item.get("source"))
        repo = item.get("repo") or (repos[0] if len(repos) == 1 else None)
        cwd = _cwd(item.get("cwd"))
        if not command or not source:
            self.drop(where, "a command and its source are required")
            return None
        if not isinstance(repo, str) or repo not in repos:
            self.drop(where, f"repo {repo!r} is not a repo of this mode")
            return None
        if repo not in self.roots:
            self.drop(where, f"repo {repo!r} has no checkout on this machine")
            return None
        rel = _normal_source(self.roots[repo], source)
        if rel is None or not _declared(self.roots[repo], rel, item["command"].strip(), cwd):
            self.drop(where, f"{source} in {repo} does not declare {command!r}")
            return None
        source = rel
        row = {"command": item["command"].strip(), "source": source, "repo": repo}
        if cwd:
            row["cwd"] = cwd
        if with_id:
            row = {"id": _slug(str(item.get("id") or command)), **row}
        self.sources.add(f"{repo}:{source}")
        return row

    def command_rows(self, where: str, items: Any, with_id: bool,
                     repos: list[str]) -> list[dict[str, str]]:
        if not isinstance(items, list):
            self.drop(where, "not a list")
            return []
        rows: list[dict[str, str]] = []
        for i, item in enumerate(items):
            row = self.command_row(f"{where}[{i}]", item, with_id, repos)
            if row and row not in rows:
                rows.append(row)
        if with_id:
            seen: dict[str, int] = {}
            for row in rows:
                n = seen.get(row["id"], 0)
                seen[row["id"]] = n + 1
                if n:
                    row["id"] = f"{row['id']}-{n + 1}"
        return rows


def _default_commands(commands: list[dict[str, Any]], role: str) -> list[str]:
    """Ids of the commands a ``code-change`` mode gets when the proposal names none,
    decided per repo: for checks, the repo's ``validations.json`` entries when it has
    any, else its check commands; for setup, its CI install steps. Of a repo's CI
    steps only those of a workflow named ``ci.*`` count when it has one (the same steps
    are copied into build and release workflows). One per (command, cwd)."""
    out: list[str] = []
    for repo in dict.fromkeys(c["repo"] for c in commands):
        mine = [c for c in commands if c["repo"] == repo]
        if role == "setup":
            picked = [c for c in mine if c["role"] == "setup" and c["kind"] == "ci_run"]
        else:
            picked = [c for c in mine if c["kind"] == "validations_json"] or [
                c for c in mine if c["role"] == "validation"]
        if any(c["kind"] == "ci_run" and Path(c["source"]).stem.lower() == "ci" for c in picked):
            picked = [c for c in picked if c["kind"] != "ci_run" or Path(c["source"]).stem.lower() == "ci"]
        seen: set[tuple] = set()
        for c in picked:
            key = (c["command"], c.get("cwd"))
            if key not in seen:
                seen.add(key)
                out.append(c["id"])
    return out


def build(project_dir: Path, proposal: Any, now: Optional[str] = None) -> tuple[dict[str, Any], list[str]]:
    """(draft definition, dropped rows with reasons) — nothing is written."""
    project_dir = Path(project_dir)
    facts = survey(project_dir)
    b = _Builder(project_dir, facts)
    p = proposal if isinstance(proposal, dict) else {}
    if not isinstance(proposal, dict):
        b.drop("proposal", "not a JSON object; only the deterministic fields are written")
    for key in sorted(set(p) - set(PROPOSAL_CONTRACT["keys"])):
        b.drop(key, "unknown key")
    repo_names = [r["name"] for r in facts["repos"]]
    skills = [s["name"] for s in facts["skills"]]

    doc: dict[str, Any] = {"schema": 2}

    ident = p.get("identity")
    if ident is not None:
        if not isinstance(ident, dict) or not _text(ident.get("summary")):
            b.drop("identity", "a summary is required")
        elif b.sourced("identity", ident):
            doc["identity"] = {"summary": _text(ident["summary"]), "source": ident["source"]}

    intake: dict[str, Any] = {}
    handles = []
    for i, h in enumerate(p.get("handles") or [] if isinstance(p.get("handles"), list) else []):
        if b.sourced(f"handles[{i}]", h) and _text(h.get("text")):
            handles.append(_text(h["text"]))
    if handles:
        intake["handles"] = handles
    if repo_names:
        intake["repos"] = repo_names
    if intake:
        doc["intake"] = intake

    watches = []
    for i, w in enumerate(p.get("watches") or [] if isinstance(p.get("watches"), list) else []):
        if b.sourced(f"watches[{i}]", w) and _text(w.get("area")) and _text(w.get("why")):
            watches.append({"area": _text(w["area"]), "why": _text(w["why"]), "source": w["source"]})
    if watches:
        doc["watches"] = watches

    gates = facts["gates"]
    if "gates" in p:
        chosen = [g for g in p["gates"] if isinstance(g, str) and g in gates] if isinstance(p["gates"], list) else []
        if not isinstance(p["gates"], list):
            b.drop("gates", "not a list; no gate is enforced")
        for g in (p["gates"] if isinstance(p["gates"], list) else []):
            if not isinstance(g, str) or g not in gates:
                b.drop(f"gates[{g!r}]", "not a gate of this Brain")
        gates = chosen
    doc["gates"] = {"enforce": gates}

    # modes: the standard ones for this shape of Brain (core/project_agent_standard), then
    # the proposed ones
    modes: dict[str, dict[str, Any]] = {m["id"]: m for m in std.standard_modes(bool(repo_names))}
    if repo_names:
        code = modes["code-change"]
        code["repos"] = list(repo_names)
        cmds = facts["commands"]
        code["setup"] = b.command_rows("code-change.setup", _default_commands(cmds, "setup"),
                                       False, repo_names)
        code["validations"] = b.command_rows("code-change.validations",
                                             _default_commands(cmds, "validation"), True, repo_names)
    assigned: dict[str, list[str]] = {}
    proposed_modes = p.get("modes") if isinstance(p.get("modes"), list) else []
    for i, pm in enumerate(proposed_modes):
        where = f"modes[{i}]"
        if not isinstance(pm, dict) or not isinstance(pm.get("id"), str):
            b.drop(where, "a mode needs an id")
            continue
        mid = pm["id"]
        where = f"modes[{mid}]"
        if mid in std.MODES and mid not in modes:
            # a standard mode of the other shape of Brain: its `when` would overlap this
            # shape's modes (core/project_agent_standard.MODES)
            b.drop(where, "this Brain has no bound repo" if mid == "code-change"
                   else "a Brain with a bound repo has no write mode (a design is research)")
            continue
        if mid not in modes:
            if not _MODE_ID_RE.fullmatch(mid):
                b.drop(where, "the id must match [a-z][a-z0-9-]*")
                continue
            if not _text(pm.get("when")) or not b.sourced(where, pm):
                if not _text(pm.get("when")):
                    b.drop(where, "a new mode needs a when")
                continue
            modes[mid] = {"id": mid, "when": _text(pm["when"])}
            repos = [r for r in pm["repos"] if isinstance(r, str) and r in repo_names] if isinstance(pm.get("repos"), list) else []
            if repos:
                modes[mid]["repos"] = repos
        else:
            m = modes[mid]
            if "when" in pm and _text(pm.get("when")) != m["when"]:
                if _text(pm.get("when")) and b.sourced(f"{where}.when", pm):
                    m["when"] = _text(pm["when"])
                elif not _text(pm.get("when")):
                    b.drop(f"{where}.when", "empty")
            for k in ("profile", "profile_rules"):
                if k in pm:
                    # how a task runs is picked per dispatch (design 2026-10-30-run-spec §3)
                    b.drop(f"{where}.{k}", "a Project Agent no longer names a pipeline profile")
        m = modes[mid]
        if isinstance(pm.get("skills"), list):
            for s in pm["skills"]:
                names = assigned.setdefault(mid, [])
                if not isinstance(s, str) or s not in skills:
                    b.drop(f"{where}.skills[{s!r}]", "not a skill of this Brain")
                elif s not in names:
                    names.append(s)
        for key, with_id in (("setup", False), ("validations", True)):
            if key in pm:
                if mid == "research":
                    b.drop(f"{where}.{key}", "the research mode changes no code")
                elif not m.get("repos"):
                    b.drop(f"{where}.{key}", "the mode has no repo")
                else:
                    m[key] = b.command_rows(f"{where}.{key}", pm[key], with_id, m["repos"])

    default = p.get("default_mode") if isinstance(p.get("default_mode"), str) and p["default_mode"] in modes else None
    if p.get("default_mode") is not None and default is None:
        b.drop("default_mode", f"{p.get('default_mode')!r} is not a mode")
    default = default or std.default_mode(bool(repo_names))
    placed = {s for v in assigned.values() for s in v}
    for s in skills:
        if s not in placed:
            assigned.setdefault(default, []).append(s)
    for mid, names in assigned.items():
        modes[mid]["skills"] = names
    for m in modes.values():
        for key in ("setup", "validations"):
            if key in m and not m[key]:
                del m[key]
    doc["modes"] = list(modes.values())
    doc["default_mode"] = default
    return std.ordered(doc), b.dropped


def _merge(doc: dict[str, Any], old: dict[str, Any], proposal: dict[str, Any],
           project_dir: Path) -> None:
    """Keep in the new draft *doc* what the earlier draft *old* holds and the proposal
    does not decide (design 2026-10-29-project-agent-standard-template §6.3): the judged
    fields and every mode the proposal does not name. A standard mode keeps every key the
    agent set on it and the rows it left; a repo bound since joins its repos and brings
    its checks. Gates are not
    kept (every gate is enforced again), and a skill no mode lists goes to the default
    mode."""
    from core.project_agent import normalize
    old = normalize(old)
    if "identity" not in proposal and isinstance(old.get("identity"), dict) and old["identity"]:
        doc["identity"] = old["identity"]
    oi = old.get("intake") if isinstance(old.get("intake"), dict) else {}
    intake = doc.setdefault("intake", {})
    if "handles" not in proposal and oi.get("handles"):
        intake["handles"] = oi["handles"]
    if not intake:
        doc.pop("intake")
    if "watches" not in proposal and old.get("watches"):
        doc["watches"] = old["watches"]
    # gates are not kept: a redraft enforces every gate of the Brain again (the safe
    # direction; a gate added since the earlier draft is never left out), unless the
    # proposal narrows them
    named = {pm.get("id") for pm in proposal.get("modes") or [] if isinstance(pm, dict)} \
        if isinstance(proposal.get("modes"), list) else set()
    new_modes = {m["id"]: m for m in doc["modes"]}
    for om in old.get("modes") or []:
        if not isinstance(om, dict) or om.get("id") in named:
            continue
        nm = new_modes.get(om.get("id"))
        if nm is None:
            if om.get("id") not in std.MODES:      # a standard mode of the Brain's old shape goes
                doc["modes"].append(om)
            continue
        standard_whens = {std.standard_when(nm["id"], r) for r in (True, False)} if nm["id"] in std.MODES else set()
        # every key the earlier definition set on the mode is carried (the agent's own
        # edits: a when, repos, skills, gates, a key the standard does not name), except
        # its id, a standard `when` (rewritten to the Brain's shape) and retired keys
        for k, v in om.items():
            if k in ("id", "setup", "validations") or k in std.RETIRED_MODE_KEYS:
                continue
            if k == "when" and v in standard_whens:
                continue
            if k == "repos" and isinstance(v, list):
                # the repos the agent chose stay chosen; a repo bound since is added
                known = set(_str_list_of(oi.get("repos")))
                v = list(v) + [r for r in nm.get("repos") or [] if r not in v and r not in known]
            nm[k] = v
        for k in ("setup", "validations"):
            if isinstance(om.get(k), list):
                # the repos the earlier draft already decided for (a row a person dropped
                # stays dropped); only a repo bound since brings its rows in
                covered = {r.get("repo") for r in om[k] if isinstance(r, dict)} | set(om.get("repos") or [])
                nm[k] = list(om[k]) + [r for r in nm.get(k) or [] if r.get("repo") not in covered]
    ids = {m["id"] for m in doc["modes"]}
    # a skill no mode lists goes to the default mode (the proposal contract), so a skill
    # added to the Brain since the earlier draft is not left out
    placed = {s for m in doc["modes"] for s in m.get("skills") or []}
    loose = [s["name"] for s in survey_skills(project_dir) if s["name"] not in placed]
    if "default_mode" not in proposal and old.get("default_mode") in ids \
            and not (old["default_mode"] == "research" and "code-change" in ids and "code-change" not in
                     {m.get("id") for m in old.get("modes") or [] if isinstance(m, dict)}):
        doc["default_mode"] = old["default_mode"]       # but a newly bound repo makes code-change the default
    if loose:
        target = next(m for m in doc["modes"] if m["id"] == doc["default_mode"])
        target["skills"] = list(target.get("skills") or []) + loose


def _str_list_of(v: Any) -> list[str]:
    return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []


def survey_skills(project_dir: Path) -> list[dict[str, str]]:
    """The Brain's own skills, as ``survey`` lists them."""
    skills, _err = _skills(Path(project_dir))
    return [{"name": k, "platform": v} for k, v in sorted((skills or {}).items())]


def ensure_skeleton(project_dir: Path) -> Optional[dict[str, Any]]:
    """Write the skeleton definition when the Brain has none (design §6.1): what code
    can decide from the Brain, no summary and no typical requests, so P6 reports it
    until the drafting step fills them in. Never raises; returns the apply report, or
    None when a definition is already there or it could not run."""
    project_dir = Path(project_dir)
    target = project_dir / PROJECT_AGENT_FILE
    if target.exists() or target.is_symlink():
        return None
    try:
        return apply(project_dir, {}, merge=False)
    except Exception as exc:  # noqa: BLE001 — creating a Brain never fails on its agent draft
        logger.warning("project_agent_draft: no skeleton for %s: %s", project_dir, exc)
        return None


def refresh_draft(project_dir: Path) -> Optional[dict[str, Any]]:
    """After a repo is bound: redraft a draft (or the missing definition) in merge mode,
    so a code-change mode and the new repo's checks appear and nothing judged is lost.
    Never raises."""
    project_dir = Path(project_dir)
    if _existing(project_dir) is _UNREADABLE:
        return None
    try:
        return apply(project_dir, {})
    except Exception as exc:  # noqa: BLE001 — a bind never fails on its agent draft
        logger.warning("project_agent_draft: draft of %s not refreshed: %s", project_dir, exc)
        return None


def apply(project_dir: Path, proposal: Any, *, dry_run: bool = False,
          now: Optional[str] = None, merge: bool = True) -> dict[str, Any]:
    """Build the definition and write it unless it does not validate. With *merge* (the
    default) what the definition there holds (its judged fields and other modes) is kept
    where the proposal does not decide it (``_merge``). Returns ``{written, path, dropped, problems, definition}``."""
    project_dir = Path(project_dir)
    target = project_dir / PROJECT_AGENT_FILE
    report: dict[str, Any] = {"written": False, "path": str(target), "dropped": [], "problems": []}
    existing = _existing(project_dir)
    if existing is _UNREADABLE:
        report["problems"].append(f"{PROJECT_AGENT_FILE} is there but cannot be read; fix or remove it first")
        return report
    facts = survey(project_dir)
    for err in (facts["bindings_error"], facts["skills_error"]):
        if err:
            report["problems"].append(f"{err}; fix it before drafting")
    if report["problems"]:
        return report
    doc, dropped = build(project_dir, proposal, now)
    if merge and existing and isinstance(proposal, dict):
        _merge(doc, existing, proposal, project_dir)
        doc = std.ordered(doc)
    report["dropped"] = dropped
    report["definition"] = doc
    report["problems"] = validate(doc, project_dir)
    if report["problems"] or dry_run:
        return report
    text = (std.HEADER + "# Written by `artifact_cli project-agent-draft` from this Brain's knowledge.\n"
            + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100))
    if target.is_file():
        # every replaced draft is kept, by time: one slot would lose a hand edit on the
        # second run
        stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
        keep = project_dir / _DRAFT_DIR / f"previous-{stamp}.yaml"
        n = 1
        while keep.exists():
            n += 1
            keep = keep.with_name(f"previous-{stamp}-{n}.yaml")
        keep.parent.mkdir(parents=True, exist_ok=True)
        keep.write_bytes(target.read_bytes())
        report["previous"] = str(keep)
    tmp = target.with_name(f".{target.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, target)
    report["written"] = True
    return report
