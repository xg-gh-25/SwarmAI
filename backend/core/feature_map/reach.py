"""Which features a change can touch: by target, not by file-level coverage (design §5).

A target is **reached** when a changed path is under its ``root`` or one of its
``paths``, or it ``needs`` a reached target; every feature of a reached target is
selected. A changed path under no target is **unplaced**: it reaches every target
rooted at the repo root (``root`` empty), and the answer lists it, never "none". A
change to the feature folder itself reaches the features whose files changed (a change
to ``features.yaml`` or a target file reaches all features of that target).

Exports: changed_paths, affected.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Iterable

from core.feature_map.files import FeatureMap


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), "-c", "core.quotepath=off", *args],
                          capture_output=True, text=True, check=True, timeout=120).stdout


def changed_paths(root: Path, base: str) -> list[str]:
    """Repo-relative paths changed since *base*: committed, staged, unstaged and
    untracked (not ignored)."""
    out = {p for p in _git(root, "diff", "--name-only", "--no-renames", base).splitlines() if p}
    out |= {p for p in _git(root, "ls-files", "--others", "--exclude-standard").splitlines() if p}
    return sorted(out)


def _under(path: str, prefix: str) -> bool:
    prefix = prefix.rstrip("/")
    return not prefix or path == prefix or path.startswith(prefix + "/")


def affected(fmap: FeatureMap, changed: Iterable[str], *,
             folder_rel: str | None = None,
             folder_changes: Iterable[str] = ()) -> dict[str, Any]:
    """``{"targets": [...], "features": [...], "unplaced": [...], "why": {id: reason}}``.
    *folder_rel* is the feature folder's repo-relative path when it lives in the repo;
    *folder_changes* are folder-relative paths changed outside the repo's diff (a
    Brain-home folder's files modified during a run)."""
    changed = sorted(set(changed))
    reached: dict[str, str] = {}
    features: dict[str, str] = {}
    unplaced: list[str] = []
    folder = (folder_rel or "").rstrip("/")
    by_spec = {f.spec: f.id for f in fmap.features if f.spec}

    def folder_file(rel: str) -> None:
        if rel in by_spec:
            features.setdefault(by_spec[rel], f"its spec {rel} changed")
        elif rel == "features.yaml":
            for f in fmap.features:
                features.setdefault(f.id, "features.yaml changed")
        elif rel.startswith("targets/") and rel.endswith(".yaml"):
            tid = rel[len("targets/"):-len(".yaml")]
            if tid in fmap.targets:
                reached.setdefault(tid, f"its target file {rel} changed")
        else:
            for f in fmap.features:
                features.setdefault(f.id, f"{rel} in the feature folder changed")

    for rel in sorted(set(folder_changes)):
        folder_file(rel)
    for path in changed:
        if folder and _under(path, folder):
            folder_file(path[len(folder) + 1:])
            continue
        hit = False
        for t in fmap.targets.values():
            if t.root and _under(path, t.root) or any(_under(path, p) for p in t.paths):
                reached.setdefault(t.id, f"{path} is under {t.root or t.paths[0]}")
                hit = True
        if not hit:
            unplaced.append(path)
    if unplaced:
        for t in fmap.targets.values():
            if not t.root:
                reached.setdefault(t.id, f"{unplaced[0]} is under no target, and "
                                   f"{t.id} is rooted at the repo root")
    # a target that needs a reached target is reached too
    grew = True
    while grew:
        grew = False
        for t in fmap.targets.values():
            if t.id not in reached and any(n in reached for n in t.needs):
                reached[t.id] = f"it needs {next(n for n in t.needs if n in reached)}"
                grew = True
    for f in fmap.features:
        if f.target in reached:
            features.setdefault(f.id, f"its target {f.target} is reached: {reached[f.target]}")
    return {"targets": sorted(reached), "features": [f.id for f in fmap.features
                                                       if f.id in features],
            "unplaced": unplaced, "why": {**{f"target:{k}": v for k, v in reached.items()},
                                          **features}}
