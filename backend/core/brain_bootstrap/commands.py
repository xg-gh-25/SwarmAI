"""Dev commands a repo declares, parsed — never guessed from a file's first lines.

Sources: CI workflows (GitHub Actions ``run:`` steps, GitLab CI ``script:`` lines),
Makefile and justfile targets, ``package.json`` scripts, tox environments and nox
sessions. Each item is ``{source, kind, name, command}`` where ``source`` is the file
that declares it (the evidence an entry cites). ``cwd`` is where the command runs when
that is not the repo root: a GitHub Actions step's (or its job's default)
``working-directory``, or the directory of a Makefile in a subdirectory. Nothing is
executed. A CI file's
``env:`` / ``secrets`` blocks are never read into the output: only the commands.
"""
from __future__ import annotations

import configparser
import json
import re
from pathlib import Path

import shlex

from .repo_read import load_yaml, safe_read_text

_MAKE_TARGET_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.\-/]*)\s*:(?![=:])", re.MULTILINE)
_JUST_RECIPE_RE = re.compile(r"^@?([A-Za-z0-9][A-Za-z0-9_\-]*)(?:\s+[^:=\n]*)?:(?!=)", re.MULTILINE)
_NOX_SESSION_RE = re.compile(
    r"@nox\.session(?:[ \t]*\(([^)]{0,500})\))?[ \t]*\n[ \t]*def[ \t]+(\w+)", re.MULTILINE)
_NOX_NAME_RE = re.compile(r"name\s*=\s*['\"]([^'\"]+)['\"]")


def _yaml(text: str):
    try:
        return load_yaml(text)
    except Exception:  # noqa: BLE001 — malformed CI or a YAML alias → no commands from it
        return None


def _label(value, fallback: str) -> str:
    """A step / job name as short text: only a string counts (anything else could be
    a huge structure from the repo)."""
    return value[:200] if isinstance(value, str) and value.strip() else fallback


def _github_actions(root: Path, rel: str) -> list[dict]:
    data = _yaml(safe_read_text(root, rel) or "")
    jobs = data.get("jobs") if isinstance(data, dict) else None
    out: list[dict] = []
    for job_id, job in (jobs or {}).items():
        steps = job.get("steps") if isinstance(job, dict) else None
        job_cwd = _working_directory(job.get("defaults") if isinstance(job, dict) else None)
        for i, step in enumerate(steps or []):
            run = step.get("run") if isinstance(step, dict) else None
            if isinstance(run, str) and run.strip():
                name = _label(step.get("name"), f"step {i + 1}")
                item = {"source": rel, "kind": "ci_run",
                        "name": f"{_label(job_id, 'job')}: {name}",
                        "command": run.strip()}
                wd = step.get("working-directory")
                cwd = wd if isinstance(wd, str) and wd.strip() else job_cwd
                if cwd:
                    item["cwd"] = cwd.strip()[:200]
                out.append(item)
    return out


def _working_directory(defaults) -> str | None:
    """A job's ``defaults.run.working-directory`` when it is a plain string."""
    run = defaults.get("run") if isinstance(defaults, dict) else None
    wd = run.get("working-directory") if isinstance(run, dict) else None
    return wd if isinstance(wd, str) and wd.strip() else None


def _gitlab_ci(root: Path, rel: str) -> list[dict]:
    data = _yaml(safe_read_text(root, rel) or "")
    out: list[dict] = []
    for job_id, job in (data or {}).items() if isinstance(data, dict) else []:
        if not isinstance(job, dict) or str(job_id).startswith("."):
            continue
        for key in ("before_script", "script"):
            lines = job.get(key)
            lines = [lines] if isinstance(lines, str) else lines
            for line in lines or []:
                if isinstance(line, str) and line.strip():
                    out.append({"source": rel, "kind": "ci_run",
                                "name": f"{_label(job_id, 'job')}: {key}",
                                "command": line.strip()})
    return out


def _makefile(root: Path, rel: str, tool: str) -> list[dict]:
    text = safe_read_text(root, rel) or ""
    regex = _MAKE_TARGET_RE if tool == "make" else _JUST_RECIPE_RE
    seen: list[str] = []
    for m in regex.finditer(text):
        name = m.group(1)
        if name.startswith(".") or name in seen or "%" in name:
            continue
        seen.append(name)
    where = rel.rpartition("/")[0]
    return [{"source": rel, "kind": tool, "name": n, "command": f"{tool} {n}",
             **({"cwd": where} if where else {})} for n in seen]


def _package_json(root: Path, rel: str) -> list[dict]:
    try:
        data = json.loads(safe_read_text(root, rel) or "null")
    except ValueError:
        return []
    scripts = data.get("scripts") if isinstance(data, dict) else None
    prefix = "" if rel == "package.json" else f"(cd {shlex.quote(rel.rsplit('/', 1)[0])} && "
    return [{"source": rel, "kind": "npm_script", "name": name,
             "command": f"{prefix}npm run {name}{')' if prefix else ''}",
             "runs": str(body)}
            for name, body in (scripts or {}).items() if isinstance(name, str)]


def _tox(root: Path, rel: str) -> list[dict]:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read_string(safe_read_text(root, rel) or "")
    except configparser.Error:
        return []
    envs: list[str] = []
    if parser.has_option("tox", "envlist"):
        envs += [e.strip() for e in re.split(r"[,\n]", parser.get("tox", "envlist")) if e.strip()]
    envs += [s.split(":", 1)[1] for s in parser.sections() if s.startswith("testenv:")]
    seen = list(dict.fromkeys(envs))
    return [{"source": rel, "kind": "tox_env", "name": e, "command": f"tox -e {e}"}
            for e in seen]


def _nox(root: Path, rel: str) -> list[dict]:
    text = safe_read_text(root, rel) or ""
    out: list[dict] = []
    for m in _NOX_SESSION_RE.finditer(text):
        explicit = _NOX_NAME_RE.search(m.group(1) or "")
        name = explicit.group(1) if explicit else m.group(2)
        out.append({"source": rel, "kind": "nox_session", "name": name,
                    "command": f"nox -s {name}"})
    return out


def parse_commands(root: Path, files: list[str]) -> list[dict]:
    """Every declared dev command, in a fixed order (by source path, then as declared)."""
    out: list[dict] = []
    for rel in sorted(files):
        low = rel.lower()
        name = low.rsplit("/", 1)[-1]
        if low.startswith(".github/workflows/") and name.endswith((".yml", ".yaml")):
            out += _github_actions(root, rel)
        elif low == ".gitlab-ci.yml":
            out += _gitlab_ci(root, rel)
        elif name in ("makefile", "gnumakefile") and rel.count("/") <= 1:
            out += _makefile(root, rel, "make")
        elif name in ("justfile", ".justfile") and "/" not in rel:
            out += _makefile(root, rel, "just")
        elif name == "package.json" and rel.count("/") <= 2 and "node_modules" not in low:
            out += _package_json(root, rel)
        elif low == "tox.ini":
            out += _tox(root, rel)
        elif low == "noxfile.py":
            out += _nox(root, rel)
    return out
