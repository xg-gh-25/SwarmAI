"""Asset Survey — a deterministic inventory of the repos a brain owns.

Design docs/design/2026-10-27-new-brain-initial-ddd.md §5.2. No LLM, no network, and
nothing in a repo runs (``repo_read``). Output: ``Projects/<p>/.artifacts/bootstrap/
survey.json`` (under ``.artifacts/``: a derived file, not a DDD member, never read by
CDF as knowledge).

Readers, and what each needs:
- the agent writing a new brain's first entries (s_project-manager P2): tier-1 text
  inlined, everything else listed with a reason, commands, code shape, history;
- Brain Hub's Specs panel: ``repos[].docs`` (path, tier, signals) and ``api_contracts``;
- CDF's repo extractor later (S7): the same, keyed by ``identity.head``.

Contract (``SCHEMA`` / ``VERSION``; adding a field keeps the version, renaming or
removing one bumps it)::

    {schema, version, generated_at, project,
     repos: [{repo, root, identity: {remote, head, default_branch, license},
              packages: [{name, root, language_mix, detected_by}],
              docs: [{path, ref, kind, tier, signals, bytes, last_commit, included,
                      reason, text?, flags?}],
              api_contracts: [{path, ref, format, items}],
              commands: [{source, kind, name, command, runs?, flags?}], commands_note,
              code_shape: {structure, graph_clusters} | {unavailable},
              history: {hotspots: [{file, kind, count, commits}],
                        changelog: {path, sections: [{heading, text}]} | null},
              conventions: {configs: [{path, tool, options}], guides: [path]},
              errors?: {part: message}}]}

``root`` is a local path (this machine); every other path is repo-relative.
Hot spots come from fix history only, on code (documents edited in a fix are not hot
spots; design G5); symbols with many callers are ``code_shape.structure.central_symbols``,
never called hot spots. ``doc_index.sync_project`` is the entry point that writes this
file and indexes the repo documents together.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import commands as _commands
from . import docs as _docs
from . import packages as _packages
from . import repo_read as rr

logger = logging.getLogger(__name__)

SCHEMA = "swarmai.brain-survey"
VERSION = 1
SURVEY_REL = Path(".artifacts") / "bootstrap" / "survey.json"


def survey_path(project_dir: str | Path) -> Path:
    return Path(project_dir) / SURVEY_REL


# ── which repos ──────────────────────────────────────────────────────────────

def _meta_repo_root(db_path: Path) -> str | None:
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
        try:
            row = conn.execute("SELECT value FROM graph_meta WHERE key='repo_root'").fetchone()
        finally:
            conn.close()
        return row[0] if row else None
    except sqlite3.Error:
        return None


def owned_repos(project_dir: str | Path) -> list[dict]:
    """``[{name, root, db_path}]`` — every local repo the project owns; names unique.

    Ownership is ``code_intel.owned_repo_roots`` (the one decision of what may be read
    into this brain); this only names each root (its binding's ``repo``, else the
    directory name) and finds its code graph, if any.
    """
    from core.code_intel import code_intel_dbs, owned_repo_roots
    project_dir = Path(project_dir)
    roots = owned_repo_roots(project_dir)
    names: dict[str, str] = {}
    dbs: dict[str, Path] = {}
    for ref in code_intel_dbs(project_dir):
        root = str(ref.repo_root.resolve()) if ref.repo_root else _meta_repo_root(ref.db_path)
        if root:
            try:
                root = str(Path(root).resolve())
            except OSError:
                continue
            dbs.setdefault(root, ref.db_path)
            if ref.repo:
                names[root] = ref.repo
    bindings_file = project_dir / "bindings.yaml"
    if bindings_file.exists():
        try:
            from core.ddd_bindings import binding_worktree, load_bindings
            for b in load_bindings(bindings_file).bindings:
                try:
                    names.setdefault(str(binding_worktree(b).resolve()), b.repo)
                except ValueError:
                    continue
        except Exception as exc:  # noqa: BLE001 — names only; ownership already decided
            logger.debug("survey: bindings unreadable for %s: %s", project_dir.name, exc)
    out: list[dict] = []
    used: set[str] = set()
    for r in roots:
        # A name keys this repo's documents in the index (bind:<project>/<name>/), so
        # two owned repos must never share one: a later duplicate gets "-2", "-3", …
        # in ownership order, which is stable for the same declarations.
        base = (names.get(r) or Path(r).name).replace("/", "_") or "repo"
        name, n = base, 2
        while name in used:
            name, n = f"{base}-{n}", n + 1
        used.add(name)
        out.append({"name": name, "root": r, "db_path": dbs.get(r)})
    return out


# ── parts ────────────────────────────────────────────────────────────────────

def _code_shape(db_path: Path | None) -> dict:
    if db_path is None:
        return {"unavailable": "no code graph for this repo yet (bind or reindex builds it)"}
    from core.code_intel.analysis import analyze_graph_store
    from core.code_intel.graph_store import GraphStore
    graph = GraphStore(db_path)
    try:
        result = analyze_graph_store(graph, ("structure", "graph_clusters"))
    finally:
        graph.close()
    clusters = result.get("graph_clusters") or {}
    for c in clusters.get("clusters", []):
        c.pop("member_ids", None)   # graph ids: a reader cannot cite them (design §5.4)
    out = {"structure": result.get("structure"), "graph_clusters": clusters,
           "indexed_commit": result.get("indexed_commit")}
    if result.get("errors"):
        out["errors"] = result["errors"]
    return out


_PYPROJECT_TOOLS = ("ruff", "black", "isort", "mypy", "pyright", "pytest", "flake8",
                    "pylint", "coverage")
_CONFIG_FILES = {  # file name → tool; parsed when the format allows, else listed
    "ruff.toml": "ruff", ".ruff.toml": "ruff", "mypy.ini": "mypy", ".flake8": "flake8",
    "pytest.ini": "pytest", ".editorconfig": "editorconfig", "tsconfig.json": "typescript",
    ".eslintrc.json": "eslint", ".eslintrc": "eslint", ".eslintrc.js": "eslint",
    ".eslintrc.cjs": "eslint", ".eslintrc.yml": "eslint", ".eslintrc.yaml": "eslint",
    "eslint.config.js": "eslint", "eslint.config.mjs": "eslint", "eslint.config.cjs": "eslint",
    ".prettierrc": "prettier", ".prettierrc.json": "prettier", ".prettierrc.yml": "prettier",
    "prettier.config.js": "prettier", "biome.json": "biome", ".golangci.yml": "golangci-lint",
    ".golangci.yaml": "golangci-lint", "rustfmt.toml": "rustfmt", ".rustfmt.toml": "rustfmt",
    "clippy.toml": "clippy", ".pre-commit-config.yaml": "pre-commit",
    ".stylelintrc.json": "stylelint", "setup.cfg": "setup.cfg",
}


def _scalars(table, depth: int = 0) -> dict:
    """The scalar (and list-of-scalar) options of a config table, nested one level."""
    out: dict = {}
    if not isinstance(table, dict):
        return out
    for k, v in table.items():
        if isinstance(v, (str, int, float, bool)):
            out[k] = v
        elif isinstance(v, list) and all(isinstance(x, (str, int, float, bool)) for x in v):
            out[k] = v
        elif isinstance(v, dict) and depth == 0:
            sub = _scalars(v, 1)
            if sub:
                out[k] = sub
    return out


def _conventions(root: Path, files: list[str], docs: list[dict],
                 packages: list[dict] | None = None) -> dict:
    """Lint / format / type / test configs at the repo root and at each package root
    (a monorepo keeps them per package), with their scalar options."""
    package_roots = {p["root"] for p in packages or []
                     if isinstance(p, dict) and p.get("root") not in (None, "", ".")}
    configs: list[dict] = []
    for rel in sorted(f for f in files if f.rpartition("/")[2] == "pyproject.toml"
                      and (f == "pyproject.toml" or f.rpartition("/")[0] in package_roots)):
        try:
            import tomllib
            data = tomllib.loads(rr.safe_read_text(root, rel) or "")
        except Exception:  # noqa: BLE001
            data = {}
        tool = data.get("tool") if isinstance(data, dict) else None
        for name in _PYPROJECT_TOOLS:
            if isinstance(tool, dict) and name in tool:
                configs.append({"path": rel, "tool": name, "options": _scalars(tool[name])})
    for rel in files:
        base_dir, _, name = rel.rpartition("/")
        if name not in _CONFIG_FILES or (base_dir and base_dir not in package_roots):
            continue
        tool = _CONFIG_FILES[name]
        text = rr.safe_read_text(root, rel) or ""
        options = None
        try:
            if name.endswith(".json") or name in (".eslintrc", ".prettierrc"):
                options = _scalars(json.loads(text))
            elif name.endswith(".toml"):
                import tomllib
                options = _scalars(tomllib.loads(text))
            elif name.endswith((".yml", ".yaml")):
                data = rr.load_yaml(text)
                if tool == "pre-commit" and isinstance(data, dict):
                    options = {"hooks": sorted({h.get("id") for r in data.get("repos") or []
                                                if isinstance(r, dict)
                                                for h in r.get("hooks") or []
                                                if isinstance(h, dict) and h.get("id")})}
                else:
                    options = _scalars(data)
            elif name in ("mypy.ini", ".flake8", "pytest.ini", "setup.cfg", ".editorconfig"):
                import configparser
                parser = configparser.ConfigParser(interpolation=None, strict=False)
                parser.read_string(text if name != ".editorconfig" else "[_root]\n" + text)
                sections = {s: dict(parser.items(s)) for s in parser.sections()}
                if name == "setup.cfg":
                    sections = {s: v for s, v in sections.items()
                                if s.split(":")[-1] in _PYPROJECT_TOOLS or s in ("flake8", "isort")}
                    if not sections:
                        continue
                options = sections
        except Exception:  # noqa: BLE001 — unparsable config: listed, options unknown
            options = None
        configs.append({"path": rel, "tool": tool, "options": options})
    guides = [d["path"] for d in docs if "contributing" in d["signals"]]
    return {"configs": configs, "guides": guides}


def _changelog(root: Path, docs: list[dict]) -> dict | None:
    logs = sorted((d for d in docs if "changelog" in d["signals"]),
                  key=lambda d: (d["path"].count("/"), d["path"]))
    if not logs:
        return None
    text = rr.safe_read_text(root, logs[0]["path"])
    if text is None:
        return {"path": logs[0]["path"], "sections": []}
    return {"path": logs[0]["path"], "sections": _docs.changelog_sections(text)}


COMMANDS_NOTE = ("Declared by the repo (CI steps, Makefile, package.json, tox, nox); "
                 "not verified. Read a command before running it.")


class _Guard:
    """MemoryGuard over each piece of repo text that goes into survey.json besides
    the tier-1 documents (``docs._inline_tier1`` scans those): secrets redacted, text
    that reads like a prompt injection dropped and flagged. The agent writing a new
    brain reads this file, so nothing in it may pass unscanned."""

    def __init__(self) -> None:
        from core.memory_guard import MemoryGuard
        self._g = MemoryGuard()

    def text(self, value):
        """``(clean text or None, flags)``."""
        if not isinstance(value, str) or not value:
            return value, []
        scan = self._g.scan(value)
        flags = sorted({f"{f.category}:{f.pattern_name}" for f in scan.findings
                        if f.category != "invisible_chars"})
        return (None if scan.rejected else scan.sanitized_content), flags


def _guard_parts(out: dict) -> None:
    """Apply ``_Guard`` to the commands, hot-spot commit subjects, CHANGELOG
    sections and API contract summaries of one repo's survey, in place."""
    g = _Guard()
    for c in out.get("commands") or []:
        flags: list[str] = []
        for key in ("name", "command", "runs", "cwd"):
            if key in c:
                c[key], f = g.text(c[key])
                flags += f
        if flags:
            c["flags"] = sorted(set(flags))
    for h in (out.get("history") or {}).get("hotspots") or []:
        for c in h.get("commits") or []:
            clean, f = g.text(c.get("subject"))
            c["subject"] = clean if clean is not None else "(subject not shown: flagged)"
            if f:
                c["flags"] = f
    changelog = (out.get("history") or {}).get("changelog") or {}
    for sec in changelog.get("sections") or []:
        sec["text"], f = g.text(sec.get("text"))
        if f:
            sec["flags"] = f
    for a in out.get("api_contracts") or []:
        clean, f = g.text("\n".join(a.get("items") or []))
        if f:
            a["flags"] = f
        a["items"] = clean.split("\n") if clean else []


def survey_repo(name: str, root: str | Path, db_path: Path | None = None) -> dict:
    """The survey of one repo. Each part is computed on its own: a part that fails is
    reported under ``errors`` and the others are still returned."""
    root = Path(root).resolve()
    out: dict = {"repo": name, "root": str(root)}
    errors: dict[str, str] = {}

    def _part(key: str, fn, default):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — one part must not lose the others
            logger.warning("survey %s: %s failed: %s", name, key, exc)
            errors[key] = f"{type(exc).__name__}: {str(exc)[:200]}"
            return default

    files = _part("files", lambda: rr.list_files(root), [])
    ident = _part("identity", lambda: rr.identity(root, files),
                  {"remote": None, "head": None, "default_branch": None, "license": None})
    commits = _part("history", lambda: rr.log_commits(root), [])
    out["identity"] = ident
    out["packages"] = _part("packages", lambda: _packages.detect_packages(root, files), [])
    docs, apis = _part("docs", lambda: _docs.rank(
        root, files, head=ident.get("head"), packages=out["packages"],
        commit_dates=rr.last_commit_times(root, commits=commits)), ([], []))
    out["docs"] = docs
    out["api_contracts"] = apis
    out["commands"] = _part("commands", lambda: _commands.parse_commands(root, files), [])
    out["commands_note"] = COMMANDS_NOTE
    out["code_shape"] = _part("code_shape", lambda: _code_shape(db_path),
                              {"unavailable": "code shape could not be computed"})
    out["history"] = {
        # Code that keeps needing fixes; documents edited alongside a fix are not hot spots.
        "hotspots": _part("hotspots", lambda: [h for h in rr.git_gotchas(root, commits=commits)
                                               if not _docs.is_doc(h["file"])], []),
        "changelog": _part("changelog", lambda: _changelog(root, docs), None),
    }
    out["conventions"] = _part("conventions", lambda: _conventions(root, files, docs, out["packages"]),
                               {"configs": [], "guides": []})
    try:
        _guard_parts(out)
    except Exception as exc:  # noqa: BLE001 — fail closed: unscanned repo text is dropped
        logger.warning("survey %s: scanning failed, repo text dropped: %s", name, exc)
        errors["guard"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        out["commands"] = []
        out["api_contracts"] = [{**a, "items": []} for a in out.get("api_contracts") or []]
        out["history"] = {"hotspots": [{**h, "commits": [{"hash": c.get("hash"), "date": c.get("date")}
                                                        for c in h.get("commits") or []]}
                                       for h in out["history"].get("hotspots") or []],
                          "changelog": None}
    if errors:
        out["errors"] = errors
    return out


def survey_project(project_dir: str | Path, *, write: bool = True) -> dict:
    """Survey every repo *project_dir* owns; write ``survey.json`` when *write*.

    A project that owns no local repo gets ``repos: []`` (a pure-knowledge brain is
    complete without one).
    """
    project_dir = Path(project_dir)
    result = {
        "schema": SCHEMA, "version": VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "project": project_dir.name,
        "repos": [survey_repo(r["name"], r["root"], r["db_path"])
                  for r in owned_repos(project_dir)],
    }
    if write:
        write_survey(project_dir, result)
    return result


def write_survey(project_dir: str | Path, result: dict) -> Path:
    """Write *result* atomically (a reader never sees half a file)."""
    path = survey_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".survey.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


def read_survey(project_dir: str | Path) -> dict | None:
    """The project's survey.json, or None when absent, unreadable or another schema."""
    try:
        data = json.loads(survey_path(project_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return None
    return data


def refresh_repo_docs(project_dir: str | Path, repo: str, root: str | Path,
                      files: list[str] | None = None) -> bool:
    """Re-rank one repo's documents in an existing survey (after its HEAD moved).

    Only ``identity``, ``docs`` and ``api_contracts`` are recomputed — deterministic,
    no LLM. Returns False when there is no survey or it has no entry for *repo* (the
    next full ``survey`` creates one).
    """
    data = read_survey(project_dir)
    if data is None:
        return False
    entry = next((r for r in data.get("repos", []) if r.get("repo") == repo), None)
    if entry is None:
        return False
    root = Path(root).resolve()
    files = rr.list_files(root) if files is None else files
    ident = rr.identity(root, files)
    commits = rr.log_commits(root)
    docs, apis = _docs.rank(root, files, head=ident.get("head"),
                            packages=entry.get("packages") or [],
                            commit_dates=rr.last_commit_times(root, commits=commits))
    _guard_parts({"api_contracts": apis})
    entry.update({"identity": ident, "docs": docs, "api_contracts": apis})
    data["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    write_survey(project_dir, data)
    return True


# ── Brain Hub: the Specs panel ───────────────────────────────────────────────

def specs_view(project_dir: str | Path) -> dict:
    """What Brain Hub's Specs panel shows: each repo's documents by tier, without
    their text. ``{"available": False}`` when the project has no survey yet.

    Only documents of repos the project still owns are listed (a stale survey entry
    for a repo since unbound is dropped).
    """
    data = read_survey(project_dir)
    if data is None:
        return {"available": False, "repos": []}
    owned = {r["name"] for r in owned_repos(project_dir)}
    repos = []
    for r in data.get("repos") or []:
        if not isinstance(r, dict) or r.get("repo") not in owned:
            continue
        repos.append({
            "repo": r["repo"],
            "head": (r.get("identity") or {}).get("head"),
            "docs": [{k: d.get(k) for k in ("path", "tier", "signals", "flags")}
                     for d in r.get("docs") or [] if isinstance(d, dict) and d.get("path")],
            "api_contracts": [{"path": a.get("path"), "format": a.get("format"),
                               "items": len(a.get("items") or [])}
                              for a in r.get("api_contracts") or [] if isinstance(a, dict)],
        })
    return {"available": True, "generated_at": data.get("generated_at"), "repos": repos}


def read_spec(project_dir: str | Path, repo: str, path: str) -> dict | None:
    """One repo document's text for the Specs panel, or None.

    Nothing from survey.json is trusted here: the repo's root comes from the
    project's current ownership (``owned_repos``), *path* must be a document or API
    contract (``doc_index.indexable``), and the read stays inside the repo with secret
    files refused (``repo_read.safe_read_text``).
    """
    from .doc_index import indexable
    if not repo or not path or not indexable(path):
        return None
    match = next((r for r in owned_repos(project_dir) if r["name"] == repo), None)
    if match is None:
        return None
    text = rr.safe_read_text(Path(match["root"]), path)
    if text is None:
        return None
    return {"repo": repo, "path": path, "text": text}
