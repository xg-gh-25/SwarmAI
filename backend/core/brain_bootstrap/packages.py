"""Package boundaries of a (mono)repo, read from workspace manifests.

npm / pnpm / lerna workspaces, a Cargo workspace, nested ``go.mod`` and nested Python
packages (``pyproject.toml`` / ``setup.py`` below the root). Pure manifest reads: no
code runs, nothing is fetched; a malformed manifest just contributes nothing.

The product's copy (the survey, and the code-intel analysis's ``packages`` field).
``s_repo-to-ddd``'s helper still carries the older ``detect_package_roots`` until that
skill reads survey.json instead (design 2026-10-27 step 4). This copy never touches the
filesystem for a manifest's globs (they are matched against the repo's tracked paths)
and never starts git from a directory a manifest names, so a crafted manifest can
neither walk the disk nor make git run anything.
"""
from __future__ import annotations

import fnmatch
import json
from collections import Counter
from pathlib import Path

from .repo_read import is_skipped_dir, list_files, load_yaml, safe_read_text

LANG_EXTENSIONS = {
    ".py": "python", ".js": "javascript", ".ts": "typescript", ".tsx": "typescript",
    ".jsx": "javascript", ".rs": "rust", ".go": "go", ".java": "java", ".rb": "ruby",
    ".c": "c", ".cpp": "cpp", ".h": "c", ".cs": "csharp", ".kt": "kotlin",
    ".swift": "swift", ".php": "php", ".sh": "shell", ".bash": "shell",
}


def language_mix(files: list[str], prefix: str = ".") -> dict[str, int]:
    """``{language: file count}`` over *files* below *prefix* ("." = all)."""
    pre = "" if prefix in ("", ".") else prefix.rstrip("/") + "/"
    counter: Counter = Counter()
    for f in files:
        if pre and not f.startswith(pre):
            continue
        lang = LANG_EXTENSIONS.get(Path(f).suffix.lower())
        if lang:
            counter[lang] += 1
    return dict(sorted(counter.items()))


_MAX_PATTERNS = 200   # workspace globs read from one manifest


def _seg_match(pat: list[str], parts: list[str]) -> bool:
    """Glob segments against path segments; ``**`` spans zero or more segments.

    Memoized on (pattern index, path index) and with runs of ``**`` merged, so the
    cost is at most len(pat) x len(parts)^2 whatever the pattern — a manifest is
    untrusted, and plain backtracking is exponential in the number of ``**``.
    """
    pat = [p for i, p in enumerate(pat) if not (p == "**" and i and pat[i - 1] == "**")]
    memo: dict[tuple[int, int], bool] = {}

    def m(i: int, j: int) -> bool:
        key = (i, j)
        if key not in memo:
            if i == len(pat):
                memo[key] = j == len(parts)
            elif pat[i] == "**":
                memo[key] = any(m(i + 1, k) for k in range(j, len(parts) + 1))
            else:
                memo[key] = (j < len(parts) and fnmatch.fnmatchcase(parts[j], pat[i])
                             and m(i + 1, j + 1))
        return memo[key]

    return m(0, 0)


def _expand(root: Path, patterns, files: list[str]) -> list[Path]:
    """Workspace globs → directories of the repo's own file list.

    Matching runs on the tracked paths, never on the filesystem: a manifest is
    untrusted input, so its globs neither walk the disk (no cost on a huge or
    symlinked tree) nor reach outside the repo (a ``..`` or absolute pattern matches
    nothing).
    """
    dirs: set[str] = set()
    for f in files:
        parts = f.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            dirs.add("/".join(parts[:i]))
    out: list[Path] = []
    for pat in (patterns or [])[:_MAX_PATTERNS]:
        if not isinstance(pat, str):
            continue
        pat = pat.strip().rstrip("/")
        while pat.startswith("./"):
            pat = pat[2:]
        if not pat or pat.startswith("!"):   # a negation excludes; it adds nothing
            continue
        segs = pat.split("/")
        out += [root / d for d in sorted(dirs) if _seg_match(segs, d.split("/"))]
    return out


def _json(root: Path, rel: str):
    try:
        return json.loads(safe_read_text(root, rel) or "null")
    except ValueError:
        return None


def _npm(root: Path, files: list[str]) -> list[Path]:
    data = _json(root, "package.json")
    ws = data.get("workspaces") if isinstance(data, dict) else None
    if isinstance(ws, dict):
        ws = ws.get("packages")
    return _expand(root, ws if isinstance(ws, list) else [], files)


def _pnpm(root: Path, files: list[str]) -> list[Path]:
    if "pnpm-workspace.yaml" not in files:
        return []
    try:
        data = load_yaml(safe_read_text(root, "pnpm-workspace.yaml") or "")
    except Exception:  # noqa: BLE001 — a malformed manifest contributes nothing
        return []
    pkgs = data.get("packages") if isinstance(data, dict) else None
    return _expand(root, pkgs if isinstance(pkgs, list) else [], files)


def _lerna(root: Path, files: list[str]) -> list[Path]:
    data = _json(root, "lerna.json")
    pkgs = data.get("packages") if isinstance(data, dict) else None
    return _expand(root, pkgs if isinstance(pkgs, list) else [], files)


def _cargo(root: Path, files: list[str]) -> list[Path]:
    if "Cargo.toml" not in files:
        return []
    try:
        import tomllib
        data = tomllib.loads(safe_read_text(root, "Cargo.toml") or "")
    except Exception:  # noqa: BLE001
        return []
    ws = data.get("workspace") if isinstance(data, dict) else None
    members = ws.get("members") if isinstance(ws, dict) else None
    return _expand(root, members if isinstance(members, list) else [], files)


def _nested(markers: tuple[str, ...]):
    def reader(root: Path, files: list[str]) -> list[Path]:
        out: list[Path] = []
        for f in files:
            parts = f.split("/")
            if len(parts) < 2 or parts[-1] not in markers:
                continue
            if any(is_skipped_dir(p) for p in parts[:-1]):
                continue
            out.append(root / "/".join(parts[:-1]))
        return out
    return reader


_READERS = {
    "npm": _npm, "pnpm": _pnpm, "lerna": _lerna, "cargo": _cargo,
    "go": _nested(("go.mod",)), "python": _nested(("pyproject.toml", "setup.py")),
}


def detect_packages(repo_root: str | Path, files: list[str] | None = None) -> list[dict]:
    """``[{name, root, language_mix, detected_by}]`` — at least one item.

    ``root`` is POSIX-relative ("." for the repo itself). A repo with no multi-package
    signal is one package rooted at ".". When the root holds more source files than
    all detected packages together, the root is listed too, so its own code is not
    dropped. Names that collide are qualified by their parent directory.
    """
    root = Path(repo_root).resolve()
    files = list_files(root) if files is None else files
    found: dict[Path, set[str]] = {}
    for label, reader in _READERS.items():
        try:
            dirs = reader(root, files)
        except Exception:  # noqa: BLE001 — one malformed manifest must not stop the rest
            dirs = []
        for d in dirs:
            if d != root:
                found.setdefault(d, set()).add(label)
    signals = sorted(n.split(".")[0] for n in ("nx.json", "turbo.json") if n in files)

    def _item(name: str, rel: str, by: list[str]) -> dict:
        return {"name": name, "root": rel, "language_mix": language_mix(files, rel),
                "detected_by": ",".join(sorted(set(by))) or "root"}

    if not found:
        return [_item(root.name, ".", signals)]
    items = [_item(d.name, d.relative_to(root).as_posix(), sorted(by) + signals)
             for d, by in sorted(found.items(), key=lambda kv: str(kv[0]))]
    if sum(language_mix(files).values()) > sum(sum(i["language_mix"].values()) for i in items):
        items.insert(0, _item(root.name, ".", signals))
    counts = Counter(i["name"] for i in items)
    for i in items:
        if counts[i["name"]] > 1:
            parent = Path(i["root"]).parent.name
            i["name"] = f"{parent}/{i['name']}" if parent and parent != "." else f"{i['name']}:{i['root']}"
    return items
