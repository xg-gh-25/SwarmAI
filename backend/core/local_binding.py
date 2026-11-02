"""A Brain's own local checkout as a binding.

``artifact_cli bind --local <path>`` reads the user's checkout in place
(``core.code_intel.local_roots``). The rest of a Brain finds its repositories in
``bindings.yaml``: the Project Agent's facts and checks (``core.project_agent.repo_roots``),
the Hive task's repositories (``core.task_spec``) and P6's asset list. So the checkout is
also recorded there, as a binding whose ``worktree`` is the checkout and whose ``clone``
is its ``origin`` (the path itself when it has none: a Hive cannot clone it then, and
admission says so). ``core.ddd_bindings.bind_project`` treats such a binding as bound in
place and never clones over it.

Public: ``record(project_dir, root) -> repo name``, ``set_worktree(project_dir, repo, root)``.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

import yaml

BINDINGS = "bindings.yaml"
_GIT_TIMEOUT_S = 10


def _git(root: Path, *args: str) -> Optional[str]:
    try:
        r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                           timeout=_GIT_TIMEOUT_S, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = r.stdout.strip()
    return out if r.returncode == 0 and out else None


def binding_for(root: Path, repo: str) -> dict[str, Any]:
    """The binding entry for the checkout *root* named *repo*."""
    origin = _git(root, "remote", "get-url", "origin")
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD") or "main"
    clone = origin or str(root)
    return {"repo": repo, "kind": "external", "clone": clone, "worktree": str(root),
            "delivery_contract": {
                "remote_kind": "github-pr" if "github.com" in clone else "self-hosted-main",
                "branch": branch, "review_path": "local", "auto_send": "manual-push"}}


def record(project_dir: Path, root: Path) -> str:
    """Record the checkout *root* in *project_dir*'s bindings.yaml (once: a binding
    whose worktree is *root* already is left as it is) and return its repo name. The
    name is the folder's, suffixed when another binding has it. The file is backed up
    before it is rewritten."""
    project_dir, root = Path(project_dir), Path(root).resolve()
    p = project_dir / BINDINGS
    doc: dict[str, Any] = {}
    if p.is_file():
        loaded = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"{BINDINGS} is not a mapping")
        doc = loaded
    rows = doc.get("bindings") if isinstance(doc.get("bindings"), list) else []
    for b in rows:
        if isinstance(b, dict) and b.get("worktree") \
                and Path(str(b["worktree"])).expanduser().resolve() == root:
            return str(b.get("repo"))
    names = {str(b.get("repo")) for b in rows if isinstance(b, dict)}
    name = root.name
    while name in names:
        name += "-local"
    doc["bindings"] = [*rows, binding_for(root, name)]
    if p.is_file():
        shutil.copy2(p, p.with_name(BINDINGS + ".bak"))
    _replace_checked(p, doc)
    return name


def _replace_checked(p: Path, doc: dict[str, Any]) -> None:
    """Write *doc* to *p* only if it loads as bindings (temp file, checked, renamed); a
    temp file that does not load is removed, and *p* is left as it was."""
    from core.ddd_bindings import load_bindings
    tmp = p.with_name(BINDINGS + ".tmp")
    tmp.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    try:
        load_bindings(tmp)                   # what is written must load (raises ValueError)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(p)


def set_worktree(project_dir: Path, repo: str, root: Path) -> None:
    """Point the existing binding *repo* at the checkout *root* (``bind --relocate``):
    the rest of the binding is kept. Same write as ``record``: backed up, checked by
    ``load_bindings`` before it replaces the file."""
    project_dir, root = Path(project_dir), Path(root).resolve()
    p = project_dir / BINDINGS
    doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    rows = doc.get("bindings") if isinstance(doc, dict) else None
    hit = [b for b in rows or [] if isinstance(b, dict) and b.get("repo") == repo]
    if len(hit) != 1:
        raise ValueError(f"{BINDINGS} has no single binding named {repo!r}")
    hit[0]["worktree"] = str(root)
    shutil.copy2(p, p.with_name(BINDINGS + ".bak"))
    _replace_checked(p, doc)
