"""What a deleted Brain leaves outside its own folder, and taking it away with it.

Deleting a project moves ``Projects/<name>/`` to ``Projects/.trash/<name>-<stamp>/``
(``SwarmWorkspaceManager.delete_project``). A Brain also owns things outside that
folder, and they used to stay behind:

- the code-intel graphs of its repos (``code_intel.owned_graphs``): a bind-managed
  clone and its db under the bindings root, a local root's db under ``bindings/local``;
- its rows in the local-roots registry (confirmed and dismissed roots);
- the runnable bodies of its scheduled jobs in ``user-jobs.yaml`` (its ``kind: job``
  assets), and their scheduler state, which keep running the moved scripts;
- its name in ``config.yaml`` ``user_context.projects``;
- its domain skills' projected copies in ``.claude/skills/`` and its rows in the skill
  and job registries.

``collect`` reads what the Brain owns while its folder is still in place; ``take_away``
moves the files into ``<trash>/_outside/`` (preserve, never unlink: a clone may hold
unpushed work) and drops the registry, job, state and config rows (the removed job
bodies are written next to them). A graph or clone another Brain still owns is left
alone: two Brains may share one checkout. A user's own checkout (a local root) is
never touched, only its derived graph. Each step is independent: one that fails is
reported and the others still run.
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


@dataclass
class Owned:
    """What one Brain owns outside its folder (read before the folder moves)."""

    name: str
    graph_dbs: list[Path] = field(default_factory=list)
    clones: list[Path] = field(default_factory=list)
    job_ids: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)


def _bind_clones(project_dir: Path) -> list[Path]:
    """The bind-managed clones of *project_dir* (under the bindings root only)."""
    bindings = project_dir / "bindings.yaml"
    if not bindings.is_file():
        return []
    try:
        from core.ddd_bindings import binding_worktree, load_bindings
        doc = load_bindings(bindings)
    except Exception:  # noqa: BLE001 — a bad bindings.yaml owns no clone we can find
        return []
    out = []
    for b in doc.bindings:
        if b.kind == "internal" or b.delivery_contract.build_system == "brazil":
            continue
        try:
            out.append(binding_worktree(b))
        except ValueError:
            continue  # the user's own checkout: never ours to move
    return out


def collect(project_dir: str | Path, workspace_root: str | Path) -> Owned:
    """Read what the Brain at *project_dir* owns outside its folder. Never raises."""
    project_dir = Path(project_dir)
    ws = Path(workspace_root)
    owned = Owned(name=project_dir.name)
    try:
        from core.code_intel import owned_graphs
        owned.graph_dbs = [ref.db_path for ref in owned_graphs(project_dir)]
    except Exception as exc:  # noqa: BLE001
        logger.warning("brain teardown: graphs of %s unreadable: %s", owned.name, exc)
    owned.clones = _bind_clones(project_dir)
    try:
        from core.ddd_job_registry import _read_job_assets
        owned.job_ids = [a["name"] for a in _read_job_assets(project_dir / "bindings.yaml")
                         if isinstance(a.get("name"), str) and a["name"]]
    except Exception:  # noqa: BLE001 — no bindings.yaml / no job assets
        owned.job_ids = []
    try:
        from core import ddd_skill_registry
        owned.skills = [r["skill"] for r in ddd_skill_registry.read_manifest(ws)
                        if r.get("owner_ddd") == owned.name]
    except Exception as exc:  # noqa: BLE001
        logger.warning("brain teardown: skills of %s unreadable: %s", owned.name, exc)
    return owned


def _still_owned(projects_dir: Path, name: str) -> tuple[set[str], set[str]]:
    """The graph dbs and clones the OTHER Brains own (real paths)."""
    from core.code_intel import owned_graphs
    dbs: set[str] = set()
    clones: set[str] = set()
    try:
        others = [p for p in projects_dir.iterdir()
                  if p.is_dir() and not p.name.startswith(".") and p.name != name]
    except OSError:
        others = []
    for p in others:
        try:
            dbs |= {str(r.db_path.resolve()) for r in owned_graphs(p)}
        except Exception:  # noqa: BLE001
            pass
        clones |= {str(c.resolve()) for c in _bind_clones(p)}
    return dbs, clones


def _move(src: Path, dest_dir: Path) -> bool:
    if not src.exists() and not src.is_symlink():
        return False
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    n = 1
    while dest.exists():
        dest = dest_dir / f"{src.name}.{n}"
        n += 1
    shutil.move(str(src), str(dest))
    return True


def _step(report: dict, key: str, fn) -> None:
    try:
        report[key] = fn()
    except Exception as exc:  # noqa: BLE001 — one step failing must not stop the others
        logger.warning("brain teardown: %s failed: %s", key, exc)
        report.setdefault("failed", []).append(f"{key}: {exc}")


def take_away(owned: Owned, trash_dir: str | Path, workspace_root: str | Path) -> dict:
    """Move or drop what *owned* lists; return what was done. Never raises."""
    ws = Path(workspace_root)
    out_dir = Path(trash_dir) / "_outside"
    report: dict = {"brain": owned.name}

    def graphs():
        keep_dbs, keep_clones = _still_owned(ws / "Projects", owned.name)
        moved = []
        for db in owned.graph_dbs:
            if str(db.resolve()) in keep_dbs:
                continue
            for suffix in _DB_SIDECARS:
                if _move(Path(str(db) + suffix), out_dir / "code-intel"):
                    moved.append(db.name + suffix)
        for clone in owned.clones:
            if str(clone.resolve()) not in keep_clones and _move(clone, out_dir / "clones"):
                moved.append(clone.name + "/")
        return moved

    def local_roots():
        from core.code_intel import local_roots as lr
        if not lr.local_mode():
            return False
        return lr.forget_project(owned.name)

    def jobs():
        if not owned.job_ids:
            return []
        from jobs import job_manager
        ids = set(owned.job_ids)
        user_jobs = job_manager._get_user_jobs()
        removed = [j for j in user_jobs if j.get("id") in ids]
        if removed:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "user-jobs.yaml").write_text(
                yaml.safe_dump({"jobs": removed}, sort_keys=False, allow_unicode=True))
            job_manager._save_user_jobs([j for j in user_jobs if j.get("id") not in ids])
        from jobs import scheduler
        with scheduler._state_lock():
            state = scheduler.load_state()
            dropped = [i for i in ids if state.jobs.pop(i, None) is not None]
            if dropped:
                scheduler.save_state(state)
        return sorted(j["id"] for j in removed)

    def config():
        from jobs.config_io import mutate_config

        def _drop(cfg: dict) -> bool:
            names = (cfg.get("user_context") or {}).get("projects")
            if isinstance(names, list) and owned.name in names:
                names.remove(owned.name)
                return True
            return False
        return mutate_config(_drop, write_if=lambda _cfg, changed: changed)

    def skills():
        moved = [s for s in owned.skills
                 if _move(ws / ".claude" / "skills" / s, out_dir / "projected-skills")]
        from core import ddd_job_registry, ddd_skill_registry
        from core.skill_manager import skill_manager
        ddd_skill_registry.build_manifest(ws, skill_manager.builtin_path)
        ddd_job_registry.build_manifest_jobs(ws)
        skill_manager.invalidate_cache()
        return moved

    _step(report, "graphs", graphs)
    _step(report, "local_roots", local_roots)
    _step(report, "jobs", jobs)
    _step(report, "user_context", config)
    _step(report, "skills", skills)
    logger.info("brain teardown: %s", json.dumps(report, default=str))
    return report
