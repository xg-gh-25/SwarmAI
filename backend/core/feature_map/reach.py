"""Which features a change can touch (design 2026-11-18 §5, refined by
2026-11-23-feature-map-replay-conditions §3).

A target is **reached** when a changed path is under its ``root`` or one of its
``paths`` and is not matched by its ``ignore``, or when it ``needs`` a reached target. A
changed path under no target is **unplaced**: it reaches every target rooted at the repo
root that does not ignore it, and the answer lists it, never "none". A path every target
that would reach it ignores is listed as **ignored**.

Within a target reached by changed paths, each such path selects the features that
``owns`` it; a path no feature owns selects every feature of the target, and a feature
that owns nothing is always selected. A target reached only through ``needs`` selects all
its features. A declared ``owns`` prefix missing from the checkout is listed as
**stale** and treated as not declared, so a moved directory replays more, never less. A
change to the feature folder itself reaches the features whose files changed (a change
to ``features.yaml`` or a target file reaches all features of that target). With no
``ignore`` and no ``owns`` the answer is the target-level one.

Exports: changed_paths, affected, glob_match.
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


def glob_match(pattern: str, path: str) -> bool:
    """Whether repo-relative *path* matches *pattern*: ``**`` is any number of path
    segments (at least one when it ends the pattern, so ``a/**`` is what is under
    ``a``, not ``a`` itself), ``*`` and ``?`` stay inside one segment; a pattern with neither matches the
    path itself or anything under it. Matched segment by segment (time linear in the
    pattern and path lengths, no backtracking regex), so a planted pattern cannot stall
    the callers' pools."""
    if not any(c in pattern for c in "*?"):
        return _under(path, pattern)
    pat = [p for i, p in enumerate(pattern.strip("/").split("/"))
           if not (p == "**" and i > 0 and pattern.strip("/").split("/")[i - 1] == "**")]
    segs = path.split("/")
    # ok[j]: pattern[:i] matches segs[:j]; one row per pattern segment
    ok = [True] + [False] * len(segs)
    for n, part in enumerate(pat):
        if part == "**" and n == len(pat) - 1:          # trailing: one segment or more
            row, seen = [False], False
            for j in range(1, len(segs) + 1):
                seen = seen or ok[j - 1]
                row.append(seen)
        elif part == "**":
            row, seen = [], False
            for j in range(len(segs) + 1):
                seen = seen or ok[j]
                row.append(seen)
        else:
            row = [False] + [ok[j] and _segment(part, segs[j]) for j in range(len(segs))]
        ok = row
    return ok[len(segs)]


def _segment(part: str, seg: str) -> bool:
    """One segment against ``*`` / ``?`` wildcards, by dynamic programming."""
    cur = [True] + [False] * len(seg)
    for c in part:
        if c == "*":
            nxt, seen = [], False
            for j in range(len(seg) + 1):
                seen = seen or cur[j]
                nxt.append(seen)
        else:
            nxt = [False] + [cur[j] and (c == "?" or c == seg[j]) for j in range(len(seg))]
        cur = nxt
    return cur[len(seg)]


def affected(fmap: FeatureMap, changed: Iterable[str], *,
             root: Path | str | None = None,
             folder_rel: str | None = None,
             folder_changes: Iterable[str] = ()) -> dict[str, Any]:
    """``{"targets", "features", "unplaced", "ignored", "stale", "why"}``. *root* is the
    checkout the paths belong to; a declared ``owns`` prefix missing from it is stale
    (without a root every prefix counts as present). *folder_rel* is the feature folder's
    repo-relative path when it lives in the repo; *folder_changes* are folder-relative
    paths changed outside the repo's diff (a Brain-home folder's files modified during a
    run)."""
    changed = sorted(set(changed))
    reached: dict[str, str] = {}
    by_path: dict[str, list[str]] = {}          # target id -> changed paths that reached it
    features: dict[str, str] = {}
    unplaced: list[str] = []
    ignored: list[str] = []
    folder = (folder_rel or "").rstrip("/")
    by_spec = {f.spec: f.id for f in fmap.features if f.spec}
    base = Path(root) if root is not None else None

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

    def reach_from(path: str, targets: list, unplaced_path: bool) -> bool:
        """Reach each of *targets* *path* is not ignored by; False when all ignore it."""
        hit = False
        for t in targets:
            if any(glob_match(g, path) for g in t.ignore):
                continue
            reached.setdefault(t.id, f"{path} is under no target, and {t.id} is rooted at "
                                     "the repo root" if unplaced_path
                               else f"{path} is under {t.root or t.paths[0]}")
            by_path.setdefault(t.id, []).append(path)
            hit = True
        return hit

    for rel in sorted(set(folder_changes)):
        folder_file(rel)
    roots = [t for t in fmap.targets.values() if not t.root]
    for path in changed:
        if folder and _under(path, folder):
            folder_file(path[len(folder) + 1:])
            continue
        under = [t for t in fmap.targets.values()
                 if t.root and _under(path, t.root) or any(_under(path, p) for p in t.paths)]
        if under:
            if not reach_from(path, under, False):
                ignored.append(path)
        elif roots and all(any(glob_match(g, path) for g in t.ignore) for t in roots):
            ignored.append(path)
        else:
            unplaced.append(path)
            reach_from(path, roots, True)
    # a target that needs a reached target is reached too
    grew = True
    while grew:
        grew = False
        for t in fmap.targets.values():
            if t.id not in reached and any(n in reached for n in t.needs):
                reached[t.id] = f"it needs {next(n for n in t.needs if n in reached)}"
                grew = True
    via_needs = {t.id for t in fmap.targets.values() if any(n in reached for n in t.needs)}

    stale: list[dict[str, str]] = []
    live: dict[str, tuple[str, ...]] = {}
    for f in fmap.features:
        keep = []
        for prefix in f.owns:
            if base is None or (base / prefix).exists():
                keep.append(prefix)
            else:
                stale.append({"feature": f.id, "path": prefix})
        live[f.id] = tuple(keep)
    unowned_by_target = {
        tid: [p for p in paths if not any(_under(p, o) for g in fmap.features
                                          if g.target == tid for o in live[g.id])]
        for tid, paths in by_path.items()}
    for f in fmap.features:
        if f.target not in reached or f.id in features:
            continue
        paths = by_path.get(f.target)
        if not paths or f.target in via_needs:        # through needs, or its target file
            features[f.id] = f"its target {f.target} is reached: {reached[f.target]}"
        elif not live[f.id]:
            features[f.id] = f"its target {f.target} is reached ({paths[0]}) and it owns nothing"
        else:
            mine = next((p for p in paths if any(_under(p, o) for o in live[f.id])), None)
            if mine is not None:
                features[f.id] = f"it owns {mine}"
                continue
            unowned = unowned_by_target.get(f.target) or []
            if unowned:
                features[f.id] = f"{unowned[0]} is owned by no feature of {f.target}"
    return {"targets": sorted(reached), "features": [f.id for f in fmap.features
                                                       if f.id in features],
            "unplaced": unplaced, "ignored": ignored, "stale": stale,
            "why": {**{f"target:{k}": v for k, v in reached.items()}, **features}}
