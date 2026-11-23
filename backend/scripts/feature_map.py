"""feature_map — write, check and replay a product's features from the user's side.

Design: docs/design/2026-11-18-feature-map.md (v3). Every result is recorded by this
program in the ledger (core/feature_map/ledger.py), never taken from an agent's word.

    "$SWARM_BACKEND_BIN" py -m scripts.feature_map where    [--keep-out-of-repo]
    "$SWARM_BACKEND_BIN" py -m scripts.feature_map verify   <feature> [<feature> ...]
    "$SWARM_BACKEND_BIN" py -m scripts.feature_map replay   [<feature> ...] [--affected] [--base REF] [--run R]
    "$SWARM_BACKEND_BIN" py -m scripts.feature_map affected [--base REF]
    "$SWARM_BACKEND_BIN" py -m scripts.feature_map status   [<feature> ...]

Common options: ``--repo-root`` (default: the git checkout of the current directory),
``--project`` / ``--repo`` (default: the Brain binding whose checkout this is), ``--keep``
(leave the sandbox for inspection). ``--run R`` (inside a pipeline run's worktree): the
code tree, base and changed paths come from the run's own engine, so the lines match what
the run's TEST check reads; a walk that fails is replayed once more at the run's base
(``trigger: base``) so a failure that was there before is told apart.

Output: one JSON object on stdout. Exit codes: 0 every replayed walk passed; 1 a walk
failed; 3 none failed, one was flaky; 2 nothing could be judged (a bad file, the runtime
unavailable, the target could not start, an error of this program). A quarantined walk is
replayed and recorded like any other (so it can earn its release) and never sets the
exit code. ``where`` prints the feature folder, choosing
and recording its place on the binding the first time (``repo`` for a working checkout,
``brain`` for a managed mirror or when asked).

The daemon never runs this; a replay is a child process (the Brain's Features actions,
when they exist, start this CLI detached and read the ledger).
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from core.feature_map import files, ledger, reach  # noqa: E402
from core.feature_map.replay import Selection, replay  # noqa: E402
from core.feature_map.replay_user import remove_tree  # noqa: E402


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                          check=True, timeout=120).stdout.strip()


def _toplevel(path: Path) -> Path:
    return Path(_git(path, "rev-parse", "--show-toplevel")).resolve()


def _main_checkout(root: Path) -> Path:
    """The main checkout of *root* (itself, or the one a linked worktree belongs to)."""
    common = Path(_git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    return common.parent.resolve() if common.name == ".git" else root


def _identity(root: Path, project: str | None, repo: str | None) -> tuple[str, str, Path | None]:
    """(brain, repo, project_dir) for the checkout at *root*."""
    from core.project_registry import get_swarmws
    projects = Path(get_swarmws()) / "Projects"
    if project and repo:
        return project, repo, projects / project
    try:
        from core.repo_changes import _bindings
        found = _bindings().get(str(_main_checkout(root)))
    except Exception:  # noqa: BLE001 — no workspace = an unbound checkout
        found = None
    if found:
        name, proj = found
        return project or proj, repo or name, projects / (project or proj)
    return ledger.brain_key(project), repo or root.name, (projects / project) if project else None


def _run_facts(run_id: str, root: Path) -> dict[str, Any]:
    """The run's own facts for this worktree: repo name, code tree, base, changed paths
    (the engine's ``_current_code``, the function its checks read)."""
    from core.pipeline_human import load_engine
    engine = load_engine(run_id)
    if engine is None:
        raise SystemExit(json.dumps({"ok": False, "error": f"no v1 run {run_id}"}))
    live = {name: Path(path).resolve() for name, path in engine.effective_repos().items()}
    name = next((n for n, p in live.items() if p == root.resolve()), None)
    if name is None:
        raise SystemExit(json.dumps({"ok": False, "error": f"{root} is not a worktree of {run_id}"}))
    _roots, trees, bases, _existing, changed = engine._current_code([name])
    if not trees.get(name) or not bases.get(name):
        raise SystemExit(json.dumps({"ok": False, "error": "the run's code tree or base is unknown"}))
    try:
        from datetime import datetime
        since = datetime.fromisoformat(str(engine.contract.get("created_at"))).timestamp()
    except (TypeError, ValueError):
        since = None
    project_dir = engine._project_dir()
    return {"repo": name, "project": str(engine.contract.get("project") or ""),
            "project_dir": str(project_dir) if project_dir else "",
            "code_tree": trees[name], "base": bases[name], "changed": changed.get(name) or [],
            "since": since}


def _load(folder: Path | None) -> tuple[files.FeatureMap | None, str]:
    if folder is None:
        return None, "no feature folder for this checkout (run `where` to create one)"
    try:
        return files.load_folder(folder), ""
    except files.FeatureMapError as exc:
        return None, str(exc)


def _select(fmap: files.FeatureMap, ids: list[str]) -> tuple[list[files.Feature], list[str]]:
    if not ids:
        return list(fmap.features), []
    chosen, unknown = [], []
    for fid in ids:
        f = fmap.feature(fid)
        (chosen.append(f) if f else unknown.append(fid))
    return chosen, unknown


def _quarantined_keys(fmap, rows) -> set[str]:
    """The walk keys quarantined before this replay (for each walk's current hash)."""
    out: set[str] = set()
    for f in fmap.features:
        h = files.content_hash(fmap, f)
        for key in {r.get("key") for r in rows if r.get("feature") == f.id and r.get("hash") == h}:
            if key and ledger.quarantined(rows, key, h):
                out.add(key)
    return out


def _commit_clean(root: Path) -> tuple[str, bool]:
    commit = _git(root, "rev-parse", "HEAD")
    clean = not _git(root, "status", "--porcelain")
    return commit, clean


def _record(brain: str, repo: str, fmap, results, *, root: Path, trigger: str, run: str | None,
            code_tree: str | None, commit: str, clean: bool, sandbox: Path | None,
            keep: bool) -> list[dict]:
    lines = []
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for i, r in enumerate(results):
        f = fmap.feature(r["feature"])
        line_id = f"{stamp}-{uuid.uuid4().hex[:6]}-{i}"
        copied = []
        # evidence is copied only from the replay's own sandbox folder
        sources = [Path(src).resolve() for src in r.get("evidence") or []]
        sources = [p for p in sources if sandbox and p.is_relative_to(Path(sandbox).resolve())]
        if sources:
            dest = ledger.evidence_dir(brain, repo, line_id)
            dest.mkdir(parents=True, exist_ok=True)
            for j, p in enumerate(sources):
                if p.is_file():
                    target = dest / f"{j}-{p.name}"
                    shutil.copy2(p, target)
                    copied.append(str(target))
        lines.append({
            "feature": r["feature"], "walk": r["walk"], "project": r["project"],
            "key": ledger.walk_key(r["feature"], r["walk"], r["project"]),
            "hash": files.content_hash(fmap, f) if f else "", "commit": commit, "clean": clean,
            "code_tree": code_tree, "checkout": str(root), "trigger": trigger, "run": run,
            "result": r["result"], "reason": r.get("reason") or "", "error": r.get("error") or "",
            "duration_s": r.get("duration_s") or 0.0,
            "evidence": str(ledger.evidence_dir(brain, repo, line_id)) if copied else "",
            "evidence_files": copied})
    written = ledger.append(brain, repo, lines) if lines else []
    if sandbox and not keep:
        remove_tree(sandbox)
    ledger.prune_evidence(brain, repo)
    return written


def _exit_code(results: list[dict], quarantined: set[str] = frozenset()) -> int:
    results = [r for r in results
               if ledger.walk_key(r["feature"], r["walk"], r["project"]) not in quarantined]
    judged = [r for r in results if r["result"] != "not_run"]
    if any(r["result"] == "fail" for r in results):
        return 1
    if not judged:
        return 2
    if any(r["result"] == "flaky" for r in results):
        return 3
    return 0


def _base_replay(root: Path, base: str, fmap_rel: str | None, folder: Path, failed: list[str],
                 brain: str, repo: str, run: str, keep: bool) -> list[dict]:
    """Replay the walks that failed at head once at the run's base, in a temporary
    worktree this command creates and removes (never the person's)."""
    tmp = Path(tempfile.mkdtemp(prefix="swarm-feature-map-base-"))
    wt = tmp / "base"
    try:
        _git(root, "worktree", "add", "--detach", str(wt), base)
    except subprocess.SubprocessError:
        shutil.rmtree(tmp, ignore_errors=True)
        return []
    try:
        base_folder = wt / fmap_rel if fmap_rel else folder
        bmap, _err = _load(base_folder)
        if bmap is None:
            return []
        feats = [f for f in (bmap.feature(fid) for fid in failed) if f]
        results, sbdir = replay(bmap, wt, Selection(feats), keep=keep)
        try:
            commit = _git(wt, "rev-parse", "HEAD")
            written = _record(brain, repo, bmap, results, root=wt, trigger="base", run=run,
                              code_tree=None, commit=commit, clean=True, sandbox=sbdir, keep=keep)
        except BaseException as exc:
            # remove it, or (keep) name it on the exception, as replay() does, so main()
            # can report where the kept base sandbox is
            if sbdir and keep:
                exc.sandbox_dir = str(sbdir)
            elif sbdir:
                remove_tree(sbdir)
            raise
        # base lines carry the base folder's walk keys; the run's facts pair them with the
        # head lines by key, so a walk the run renamed has no pair and counts as new
        return written
    finally:
        subprocess.run(["git", "-C", str(root), "worktree", "remove", "--force", str(wt)],
                       capture_output=True, timeout=120)
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    from core.feature_map.cli import parser
    args = parser(__doc__.split("\n\n")[0]).parse_args(argv)

    def _stop(signum, _frame):           # a capped caller's signal: clean up our children
        raise KeyboardInterrupt(f"signal {signum}")
    # SIGALRM too: a wall-clock cap (`perl -e 'alarm N; exec ...'`) ends us with it, and
    # its default action would kill this process with the sandbox's services still running
    for sig in (signal.SIGTERM, getattr(signal, "SIGHUP", None), getattr(signal, "SIGALRM", None),
                signal.SIGINT):
        if sig is not None:
            signal.signal(sig, _stop)

    out: dict[str, Any] = {"ok": True, "command": args.command}
    try:
        root = _toplevel(Path(args.repo_root))
    except subprocess.SubprocessError:
        print(json.dumps({"ok": False, "error": "not inside a git checkout"}))
        return 2
    run_facts = _run_facts(args.run, root) if args.run else None
    if run_facts:
        # exactly the engine's Brain, repo and project folder, so FM-1 reads these lines
        brain, repo = ledger.brain_key(run_facts["project"]), run_facts["repo"]
        project_dir = Path(run_facts["project_dir"]) if run_facts.get("project_dir") else None
    else:
        brain, repo, project_dir = _identity(root, args.project, args.repo)
    out.update(brain=brain, repo=repo)

    if args.command == "where":
        if project_dir is None:
            folder, mode = root / files.REPO_FOLDER, "repo"
        else:
            folder, mode = files.location(project_dir, repo, root)
            if folder is None:
                folder, mode = files.choose_location(project_dir, repo, root,
                                                     keep_out_of_repo=args.keep_out_of_repo)
        out.update(folder=str(folder), mode=mode)
        print(json.dumps(out, ensure_ascii=False))
        return 0

    folder, mode = (files.location(project_dir, repo, root) if project_dir
                    else ((root / files.REPO_FOLDER, "repo")
                          if (root / files.REPO_FOLDER / "features.yaml").is_file() else (None, "none")))
    fmap, error = _load(folder)
    out.update(folder=str(folder) if folder else None, mode=mode)
    if fmap is None:
        out.update(ok=False, error=error, results=[])
        print(json.dumps(out, ensure_ascii=False))
        return 2
    folder_rel = (folder.resolve().relative_to(root).as_posix() if mode == "repo" else None)
    rows = ledger.read(brain, repo)

    if args.command == "status":
        chosen, unknown = _select(fmap, args.features)
        walks = []
        for f in chosen:
            h = files.content_hash(fmap, f)
            keys = sorted({r.get("key") for r in rows if r.get("feature") == f.id and r.get("hash") == h})
            if not keys:
                walks.append({"feature": f.id, "title": f.title, "never_passed": True,
                              "last": None, "last_pass": None, "quarantined": False})
            for key in keys:
                st = ledger.status(rows, key, h)
                lp = st["last_pass"]
                if lp and lp.get("commit"):
                    try:
                        st["commits_since_pass"] = int(_git(root, "rev-list", "--count",
                                                            f"{lp['commit']}..HEAD"))
                    except subprocess.SubprocessError:
                        st["commits_since_pass"] = None
                walks.append({"feature": f.id, "title": f.title, **st})
        out.update(walks=walks, unknown=unknown)
        print(json.dumps(out, ensure_ascii=False))
        return 0

    base = args.base or (run_facts or {}).get("base")
    if args.command == "affected" or args.affected:
        folder_changes: list[str] = []
        if run_facts:
            changed = run_facts["changed"]
            if mode == "brain":
                from core.feature_map.pipeline import brain_folder_changes
                folder_changes = brain_folder_changes(folder, run_facts.get("since"))
        else:
            if not base:
                base = _git(root, "merge-base", "HEAD", "origin/HEAD") if _has_ref(root, "origin/HEAD") else "HEAD"
            changed = reach.changed_paths(root, base)
        aff = reach.affected(fmap, changed, root=root, folder_rel=folder_rel,
                             folder_changes=folder_changes)
        out.update(base=base, affected=aff)
        if args.command == "affected":
            print(json.dumps(out, ensure_ascii=False))
            return 0
        chosen = [fmap.feature(fid) for fid in aff["features"]]
        unknown: list[str] = []
    else:
        chosen, unknown = _select(fmap, args.features)
    if unknown:
        out.update(ok=False, error=f"unknown feature(s): {', '.join(unknown)}")
        print(json.dumps(out, ensure_ascii=False))
        return 2

    quarantined = _quarantined_keys(fmap, rows)
    commit, clean = _commit_clean(root)
    code_tree = (run_facts or {}).get("code_tree")
    trigger = "verify" if args.command == "verify" else "replay"
    sbdir = kept = None
    try:
        results, sbdir = replay(fmap, root, Selection(chosen), keep=args.keep)
        written = _record(brain, repo, fmap, results, root=root, trigger=trigger,
                          run=args.run, code_tree=code_tree, commit=commit, clean=clean,
                          sandbox=sbdir, keep=args.keep)
        kept, sbdir = sbdir, None          # _record removed it (or --keep left it)
        failed = sorted({r["feature"] for r in results if r["result"] == "fail"})
        base_lines = []
        if args.run and failed and base:
            base_lines = _base_replay(root, base, folder_rel, folder, failed, brain, repo,
                                      args.run, args.keep)
    except (KeyboardInterrupt, Exception) as exc:  # noqa: BLE001 — nothing judged, said as JSON
        if sbdir and not args.keep:
            remove_tree(sbdir)
        error = ("interrupted" if isinstance(exc, KeyboardInterrupt)
                 else f"{type(exc).__name__}: {exc}"[:600])
        # --keep: the head replay's sandbox (kept once recorded, or still in sbdir), and a
        # base replay's that raised (named on its exception)
        head = kept or sbdir
        failed_dir = getattr(exc, "sandbox_dir", None)
        print(json.dumps({**out, "ok": False, "error": error,
                          "sandbox": (str(head) if head else failed_dir) if args.keep else None,
                          "base_sandbox": failed_dir if args.keep and head else None},
                         ensure_ascii=False))
        return 2
    code = _exit_code(results, quarantined)
    out.update(results=[{k: v for k, v in w.items() if k not in ("checkout",)} for w in written],
               base_results=[{"feature": b["feature"], "walk": b["walk"], "project": b["project"],
                              "result": b["result"]} for b in base_lines],
               quarantined=sorted(quarantined),
               counts={x: sum(1 for r in results if r["result"] == x)
                       for x in ("pass", "fail", "flaky", "not_run")},
               exit=code, sandbox=str(kept) if args.keep and kept else None)
    print(json.dumps(out, ensure_ascii=False))
    return code


def _has_ref(root: Path, ref: str) -> bool:
    return subprocess.run(["git", "-C", str(root), "rev-parse", "--verify", "--quiet", ref],
                          capture_output=True, timeout=30).returncode == 0


if __name__ == "__main__":
    sys.exit(main())
