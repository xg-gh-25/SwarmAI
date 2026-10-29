"""What a deleted Brain leaves outside its own folder, and taking it away with it.

Deleting a project moves ``Projects/<name>/`` to ``Projects/.trash/<name>-<stamp>/``
(``SwarmWorkspaceManager.delete_project``). A Brain also owns things outside that
folder, and they used to stay behind:

- the code-intel graphs of its repos: a bind-managed clone and its db under the
  bindings root, a local root's db under ``bindings/local``;
- its rows in the local-roots registry (confirmed and dismissed roots);
- the runnable bodies of its scheduled jobs in ``user-jobs.yaml`` (its ``kind: job``
  assets), and their scheduler state, which keep running the moved scripts;
- its name in ``config.yaml`` ``user_context.projects``;
- its domain skills' projected copies in ``.claude/skills/`` and its rows in the skill
  and job registries.

``collect`` reads what the Brain owns while its folder is still in place;
``take_away`` runs after the move: it moves the graph files and clones into
``<trash>/_outside/`` (preserve, never unlink: a clone may hold unpushed work) and
drops the registry, job, state and config rows (the removed job bodies are written
next to them). ``refresh_registries`` then rebuilds the skill and job registries and
re-projects ``.claude/skills/``; the projection drops a copy whose source is gone, so
this module never names a skill path itself.

What is moved has to be shown to be the deleted Brain's alone:

- a graph db or clone another Brain still owns stays (two Brains may share one
  checkout). When what another Brain owns cannot be read (its ``bindings.yaml`` does
  not parse, the local-roots registry is unreadable), nothing is moved: "cannot tell"
  is never read as "nobody owns it";
- a clone moves only when it is a real directory with a ``.git`` directly under the
  bindings root (``binding_worktree`` refuses every other path, and the names the
  root keeps beside the clones), and not while a bind of it is running;
- a user's own checkout (a local root) is never moved, only its derived graph;
- a job leaves ``user-jobs.yaml`` only when its body runs out of the Brain's folder
  (names ``Projects/<name>/``) and no other Brain declares its id (a system
  job has no body there: ``job_manager`` refuses a user job with a system id), and
  scheduler state goes only for the bodies that were removed. A
  scheduler run in progress may write that state back; the scheduler ignores state
  for a job it no longer has.

Each step is independent: one that fails or is skipped is listed in the report's
``failed`` and the others still run.
"""
from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_DB_SIDECARS = ("", "-shm", "-wal", ".rebuild-requested", ".rebuild-requested.lock",
                ".write.lock", ".lock")


class _Unreadable(Exception):
    """What some Brain owns cannot be read, so ownership cannot be decided."""


@dataclass
class Owned:
    """What one Brain owns outside its folder (read before the folder moves)."""

    name: str
    graph_dbs: list[Path] = field(default_factory=list)
    clones: list[Path] = field(default_factory=list)
    job_ids: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)


def _registry_projects() -> dict:
    """The local-roots registry's ``projects`` map ({} off the desktop). Raises
    ``_Unreadable`` when the registry exists but cannot be read."""
    from core.code_intel import local_roots as lr
    if not lr.local_mode():
        return {}
    try:
        return lr._read(lr.registry_path())["projects"]
    except lr.LocalRootError as exc:
        raise _Unreadable(str(exc)) from None


def _ownership(project_dir: Path, registry: dict) -> tuple[set[str], list[Path], list[str]]:
    """``(graph dbs, bind-managed clones, job ids)`` *project_dir* owns.

    Raises ``_Unreadable`` when its ``bindings.yaml`` exists and does not parse. A
    confirmed local root counts whether or not its disk is mounted now (its graph is
    keyed by the declared path)."""
    from core.code_intel import owned_graphs
    from core.code_intel.local_roots import CONFIRMED, db_path as local_db_path
    dbs = {str(r.db_path.resolve()) for r in owned_graphs(project_dir)}
    rows = registry.get(project_dir.name)
    for r in rows if isinstance(rows, list) else []:
        if isinstance(r, dict) and r.get("state") == CONFIRMED and isinstance(r.get("path"), str):
            dbs.add(str(local_db_path(r["path"]).resolve()))
    clones: list[Path] = []
    jobs: list[str] = []
    bindings = project_dir / "bindings.yaml"
    if bindings.is_file():
        from core.ddd_bindings import binding_db_path, binding_worktree, load_bindings
        try:
            doc = load_bindings(bindings)
            raw = yaml.safe_load(bindings.read_text(encoding="utf-8")) or {}
        except Exception as exc:  # noqa: BLE001 — any parse failure: ownership unknown
            raise _Unreadable(f"{bindings}: {exc}") from None
        for b in doc.bindings:
            if b.kind == "internal" or b.delivery_contract.build_system == "brazil":
                continue
            try:
                clones.append(binding_worktree(b))
                dbs.add(str(binding_db_path(b).resolve()))
            except ValueError:
                continue  # the user's own checkout, or a refused name: never ours to move
        assets = raw.get("governed_assets") if isinstance(raw, dict) else None
        jobs = [a["name"] for a in assets or []
                if isinstance(a, dict) and a.get("kind") == "job"
                and isinstance(a.get("name"), str) and a["name"]]
    return dbs, clones, jobs


def collect(project_dir: str | Path) -> Owned:
    """Read what the Brain at *project_dir* owns outside its folder. Never raises;
    what cannot be read is listed in ``unreadable`` and left alone."""
    project_dir = Path(project_dir)
    owned = Owned(name=project_dir.name)
    try:
        registry = _registry_projects()
    except _Unreadable as exc:
        owned.unreadable.append(str(exc))
        registry = {}
    try:
        dbs, owned.clones, owned.job_ids = _ownership(project_dir, registry)
        owned.graph_dbs = [Path(d) for d in sorted(dbs)]
    except _Unreadable as exc:
        owned.unreadable.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        owned.unreadable.append(f"{owned.name}: {exc}")
    return owned


def _others(projects_dir: Path, name: str) -> tuple[set[str], set[str], set[str]]:
    """``(graph dbs, clones, job ids)`` the OTHER Brains own (real paths). Raises
    ``_Unreadable`` when any of it cannot be read."""
    registry = _registry_projects()
    try:
        others = [p for p in projects_dir.iterdir()
                  if p.is_dir() and not p.name.startswith(".") and p.name != name]
    except OSError as exc:
        raise _Unreadable(f"{projects_dir}: {exc}") from None
    dbs: set[str] = set()
    clones: set[str] = set()
    jobs: set[str] = set()
    for p in others:
        try:
            d, c, j = _ownership(p, registry)
        except _Unreadable:
            raise
        except Exception as exc:  # noqa: BLE001 — any failure: ownership unknown
            raise _Unreadable(f"{p.name}: {exc}") from None
        dbs |= d
        clones |= {str(x.resolve()) for x in c}
        jobs |= set(j)
    return dbs, clones, jobs


def _free_name(dest_dir: Path, name: str, suffixes: tuple[str, ...]) -> str:
    """*name*, or ``name.N``, such that no ``<chosen><suffix>`` exists in *dest_dir*:
    a db and its sidecars get the same new name, so they still pair."""
    chosen, n = name, 1
    while any((dest_dir / f"{chosen}{s}").exists() for s in suffixes):
        chosen, n = f"{name}.{n}", n + 1
    return chosen


def _evict_graphs(dbs: list[Path]) -> None:
    """Close the cached graph stores of *dbs*, so nothing writes into the trash."""
    from core import code_intel as ci
    keys = {str(d) for d in dbs} | {str(d.resolve()) for d in dbs}
    with ci._cache_lock:
        entries = [e for e in (ci._db_graph_cache.pop(k, None) for k in keys) if e]
    for graph, _ident in entries:
        try:
            graph.close()
        except Exception as exc:  # noqa: BLE001 — evicted anyway
            logger.debug("brain teardown: closing %s failed: %s", graph._db_path, exc)


def _step(report: dict, key: str, fn) -> None:
    try:
        report[key] = fn()
    except Exception as exc:  # noqa: BLE001 — one step failing must not stop the others
        logger.error("brain teardown: %s failed: %s", key, exc)
        report.setdefault("failed", []).append(f"{key}: {exc}")


def take_away(owned: Owned, trash_dir: str | Path, workspace_root: str | Path) -> dict:
    """Move or drop what *owned* lists, after its folder moved; return what was done.
    Never raises."""
    ws = Path(workspace_root)
    out_dir = Path(trash_dir) / "_outside"
    report: dict = {"brain": owned.name}
    for why in owned.unreadable:
        report.setdefault("failed", []).append(f"collect: {why} (left in place)")
    try:
        keep = _others(ws / "Projects", owned.name)
    except _Unreadable as exc:
        keep = None
        report.setdefault("failed", []).append(
            f"ownership: {exc} (graphs, clones and jobs left in place)")

    def graphs():
        keep_dbs, keep_clones, _ = keep
        moved = []
        dbs = [db for db in owned.graph_dbs if str(db.resolve()) not in keep_dbs]
        _evict_graphs(dbs)
        dest = out_dir / "code-intel"
        for db in dbs:
            present = [s for s in _DB_SIDECARS if Path(str(db) + s).exists()]
            if not present:
                continue
            dest.mkdir(parents=True, exist_ok=True)
            new = _free_name(dest, db.name, _DB_SIDECARS)
            for s in present:
                shutil.move(str(db) + s, str(dest / f"{new}{s}"))
                moved.append(db.name + s)
        from core.ddd_bindings import BindInProgress, _bind_lock, bind_lock_path
        for clone in owned.clones:
            if str(clone.resolve()) in keep_clones:
                continue
            if clone.is_symlink() or not clone.is_dir() or not (clone / ".git").exists():
                continue  # not a clone this Brain's bind made
            try:
                with _bind_lock(clone, clone.name):
                    (out_dir / "clones").mkdir(parents=True, exist_ok=True)
                    new = _free_name(out_dir / "clones", clone.name, ("",))
                    shutil.move(str(clone), str(out_dir / "clones" / new))
            except BindInProgress:
                report.setdefault("failed", []).append(
                    f"graphs: a bind of {clone.name} is running (left in place)")
                continue
            moved.append(clone.name + "/")
            lock = bind_lock_path(clone)
            if lock.is_file() and not lock.is_symlink():
                shutil.move(str(lock), str(out_dir / "clones" / f"{new}.bind.lock"))
        return moved

    def local_roots():
        from core.code_intel import local_roots as lr
        if not lr.local_mode():
            return False
        return lr.forget_project(owned.name)

    def jobs():
        _, _, keep_jobs = keep
        from jobs import job_manager
        ids = set(owned.job_ids) - keep_jobs
        if not ids:
            return []
        user_jobs = job_manager._get_user_jobs()
        # Declared by this Brain AND running out of its folder (every job asset's body
        # names its own scripts): a user's job that merely shares the id stays.
        home = f"Projects/{owned.name}/"
        removed = [j for j in user_jobs if j.get("id") in ids
                   and home in json.dumps(j, ensure_ascii=False, default=str)]
        if not removed:
            return []
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "user-jobs.yaml").write_text(
            yaml.safe_dump({"jobs": removed}, sort_keys=False, allow_unicode=True))
        job_manager._save_user_jobs([j for j in user_jobs if j.get("id") not in ids])
        gone = {j["id"] for j in removed}
        from jobs import scheduler
        with scheduler._state_lock():
            state = scheduler.load_state()
            if any(state.jobs.pop(i, None) is not None for i in sorted(gone)):
                scheduler.save_state(state)
        return sorted(gone)

    def config():
        from jobs.config_io import mutate_config

        def _drop(cfg: dict) -> bool:
            names = (cfg.get("user_context") or {}).get("projects")
            if isinstance(names, list) and owned.name in names:
                names.remove(owned.name)
                return True
            return False
        return mutate_config(_drop, write_if=lambda _cfg, changed: changed)

    if keep is not None:
        _step(report, "graphs", graphs)
    _step(report, "local_roots", local_roots)
    if keep is not None:
        _step(report, "jobs", jobs)
    _step(report, "user_context", config)
    log = logger.error if "failed" in report else logger.info
    log("brain teardown: %s", json.dumps(report, default=str))
    return report


async def refresh_registries(workspace_root: str | Path) -> None:
    """After a Brain moved out of ``Projects/``: rebuild the skill registry and
    re-project ``.claude/skills/`` (dropping the Brain's projected copies), then the
    job registry, which resolves skill names. Never raises."""
    import asyncio

    from core.projection_layer import refresh_workspace_skills
    ws = Path(workspace_root)
    await refresh_workspace_skills(ws, why="a Brain delete")
    try:
        from core import ddd_job_registry
        await asyncio.to_thread(ddd_job_registry.build_manifest_jobs, ws)
    except Exception as exc:  # noqa: BLE001 — rebuilt on the next refresh
        logger.error("brain teardown: job registry rebuild failed: %s", exc)
