"""Which DDD project a run belongs to (design docs/design/2026-10-26-run-knowledge.md
§3.2): decided by the repos' bindings, never by a name the agent types.

- ``resolve(project, repos)`` -> ``Binding(project, has_ddd, bound_repos, notes)``.
  A repo is bound when some ``Projects/<P>/bindings.yaml`` names its main checkout
  (``repo_changes.bound_projects``; a linked worktree counts as its main checkout).
  ``--project`` is first given the existing folder's spelling (case ignored,
  ``project_registry.canonical_project_name``), so a run never records another one.
  All bound repos must name one project and ``--project`` must be it (run_owner_gate
  requires an explicit ``--project`` on every mutating call, create included). With no
  bound repo, ``has_ddd`` is whether the named project has a DDD document. Raises ``ValueError`` with the fix in the message.
- ``has_ddd_docs(project)``: any canonical DDD doc exists for *project*.

Reads bindings and the workspace through ``config.get_swarmws`` (via repo_changes and
project_registry); runs git only to find a linked worktree's main checkout.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class Binding:
    project: str
    has_ddd: bool
    bound_repos: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _main_checkout(path: str) -> str:
    real = os.path.realpath(path)
    if not os.path.isfile(os.path.join(real, ".git")):
        return real                           # a main checkout (.git is a directory)
    from core.run_worktree import main_of
    main = main_of(real)
    return os.path.realpath(str(main)) if main is not None else real


def has_ddd_docs(project: str) -> bool:
    from core.ddd_paths import ddd_path
    from core.project_registry import DDD_CANONICAL_DOCS, get_projects_dir
    base = get_projects_dir() / project
    return base.is_dir() and any(ddd_path(base, doc).is_file() for doc in DDD_CANONICAL_DOCS)


def resolve(project: str, repos: Mapping[str, str]) -> Binding:
    from core.project_registry import canonical_project_name
    from core.repo_changes import bound_projects
    project = canonical_project_name(project)   # the folder's own spelling, on every FS
    bound = bound_projects()
    by_repo = {name: bound.get(_main_checkout(root)) for name, root in repos.items()}
    projects = sorted({p for p in by_repo.values() if p})
    unbound = sorted(name for name, p in by_repo.items() if not p)
    if len(projects) > 1:
        detail = ", ".join(f"{n} -> {p}" for n, p in sorted(by_repo.items()) if p)
        raise ValueError(f"these repos are bound to different projects ({detail}); a run "
                         "belongs to one project: create one run per project")
    if projects:
        owner = projects[0]
        if project != owner:
            raise ValueError(f"--project {project} does not match the repos' binding: they "
                             f"are bound to project {owner}. Use --project {owner}")
        notes = [f"repo {n} is not bound to project {owner}; this run still belongs to "
                 f"{owner}" for n in unbound]
        return Binding(owner, has_ddd_docs(owner),
                       sorted(n for n, p in by_repo.items() if p), notes)
    notes = [f"repo {n} is not bound to any project, so this run uses the project you "
             f"named ({project}); bind it in Projects/<P>/bindings.yaml to give its runs a "
             "DDD" for n in unbound]
    return Binding(project, has_ddd_docs(project), [], notes)
