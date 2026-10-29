"""Safe primitives for reading a repo the user bound — untrusted input.

Three rules hold for every caller (design 2026-10-27-new-brain-initial-ddd.md §5.2, R32):

1. **Reads stay inside the repo.** ``safe_read_text`` resolves symlinks and refuses a
   path that lands outside the repo root or inside ``.git/``, a non-file, a file with
   more than one hard link, an over-size file and a file that is not UTF-8 text.
2. **Secret files are never opened.** ``is_secret_path`` names them (``.env*``, keys,
   ``.npmrc`` / ``.netrc`` / ``.pypirc``, ``*credentials*``, ``.aws/``,
   ``.docker/config.json``); ``safe_read_text`` refuses them before touching the disk —
   checked on the name asked for AND on the file a symlink resolves to, so
   ``docs/notes.md -> ../.env`` is refused like ``.env``.
3. **git never runs repo-controlled code.** ``git()`` passes ``core.hooksPath=/dev/null``,
   an empty ``core.fsmonitor`` and ``safe.bareRepository=explicit`` on the command line
   (command-line config outranks the repo's own ``.git/config``, and the last setting is
   honoured only there), drops every inherited ``GIT_*`` variable, and never prompts.
   A committed directory that looks like a bare repository therefore cannot be picked
   up as a git dir whose config runs a command.

Untrusted YAML goes through ``load_yaml``, which refuses aliases: ``yaml.safe_load``
keeps an alias as a shared reference, and turning a nested alias tree into text
(``str()``, f-strings) expands it exponentially.

Nothing here writes to the repo or reaches the network.
"""
from __future__ import annotations

import fnmatch
import logging
import os
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_DOC_BYTES = 2 * 1024 * 1024   # a single text file larger than this is not read
_WALK_CAP = 20000                 # files listed by the non-git fallback walk

# Directories never descended into: vendored code, build output, tool caches.
SKIP_DIRS = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next",
    ".nuxt", "target", ".tox", ".nox", ".eggs", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".cache", ".gradle", "vendor", "third_party", "site-packages",
})
_SKIP_DIR_PATTERNS = ("*.egg-info",)

# Files never opened: they hold credentials, not knowledge.
_SECRET_NAME_PATTERNS = (
    ".env", ".env.*", "*.env", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_dsa*",
    "id_ecdsa*", "id_ed25519*", ".npmrc", ".netrc", ".pypirc", "*credentials*",
    ".htpasswd",
)
_SECRET_DIRS = frozenset({".aws", ".ssh", ".gnupg"})

GIT_HARDENING: tuple[str, ...] = (
    "-c", "core.hooksPath=/dev/null",
    "-c", "core.fsmonitor=",
    "-c", "safe.bareRepository=explicit",
    "-c", "log.showSignature=false",
)
# A diff, show or log never runs a configured external diff or textconv program
# (an empty ``-c diff.external=`` made git try to run "" and fail any patch).
_NO_DIFF_PROGRAMS = ("--no-ext-diff", "--no-textconv")
_GIT_TIMEOUT_S = 60


def is_skipped_dir(part: str) -> bool:
    """A path component that is vendored, generated or a tool cache."""
    return part in SKIP_DIRS or any(fnmatch.fnmatch(part, p) for p in _SKIP_DIR_PATTERNS)


def is_secret_path(rel: str) -> bool:
    """True when *rel* (repo-relative, POSIX) names a file that may hold credentials."""
    parts = [p for p in rel.replace("\\", "/").split("/") if p]
    if not parts:
        return False
    if any(p.lower() in _SECRET_DIRS for p in parts[:-1]):
        return True
    name = parts[-1].lower()
    if len(parts) >= 2 and parts[-2].lower() == ".docker" and name == "config.json":
        return True
    return any(fnmatch.fnmatch(name, pat) for pat in _SECRET_NAME_PATTERNS)


def confine(root: Path, rel: str) -> Path | None:
    """``root / rel`` resolved, or None when it is a secret path, escapes *root* (a
    ``..`` segment, an absolute path, or a symlink pointing outside), or resolves to
    a secret file or into ``.git/`` (a symlink inside the repo)."""
    if not rel or is_secret_path(rel):
        return None
    try:
        base = Path(root).resolve()
        target = (base / rel).resolve()
        resolved_rel = target.relative_to(base).as_posix()
    except (OSError, ValueError, RuntimeError):
        return None
    if is_secret_path(resolved_rel) or ".git" in resolved_rel.split("/"):
        return None
    return target


def safe_read_text(root: Path, rel: str, max_bytes: int = MAX_DOC_BYTES) -> str | None:
    """The UTF-8 text of *rel* inside *root*, or None (secret, escaping, missing,
    not a regular file, larger than *max_bytes*, or not UTF-8 text). Never raises."""
    target = confine(root, rel)
    if target is None:
        if rel and not is_secret_path(rel):
            logger.warning("survey: refused a path outside the readable repo: %s", rel)
        return None
    try:
        if not target.is_file():
            return None
        st = target.stat()
        # A second hard link can give a secret file a document's name; a git
        # checkout never creates one.
        if st.st_size > max_bytes or st.st_nlink > 1:
            return None
        return target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def load_yaml(text: str):
    """Parse untrusted YAML: ``yaml.safe_load`` without aliases (see the module
    docstring). Raises ``yaml.YAMLError`` on an alias or malformed input."""
    import yaml

    class _NoAliasLoader(yaml.SafeLoader):
        def compose_node(self, parent, index):
            if self.check_event(yaml.events.AliasEvent):
                raise yaml.YAMLError("YAML aliases are not accepted in repo files")
            return super().compose_node(parent, index)

    return yaml.load(text, Loader=_NoAliasLoader)  # noqa: S506 — SafeLoader subclass


def _git_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0", "GIT_PAGER": "cat"})
    return env


def git(root: Path, *args: str, timeout: int = _GIT_TIMEOUT_S) -> str | None:
    """Run ``git <args>`` in *root* with the hardening above. stdout, or None when git
    is missing, fails, or times out. Never raises."""
    if args and args[0] in ("diff", "show", "log"):
        args = (args[0], *_NO_DIFF_PROGRAMS, *args[1:])
    cmd = ["git", *GIT_HARDENING, "-C", str(root), *args]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=timeout, env=_git_env(),
                             stdin=subprocess.DEVNULL, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("survey: git %s failed: %s", args[:1], exc)
        return None
    if out.returncode != 0:
        return None
    return out.stdout.decode("utf-8", errors="replace")


def is_git_repo(root: Path) -> bool:
    top = git(root, "rev-parse", "--show-toplevel")
    if not top:
        return False
    try:
        return Path(top.strip()).resolve() == Path(root).resolve()
    except OSError:
        return False


def list_files(root: Path) -> list[str]:
    """Repo-relative POSIX paths of the files to consider, sorted.

    A git repo lists its tracked files (so ``.gitignore`` is respected and untracked
    junk never counts); anything else is walked, bounded by ``_WALK_CAP``. Skipped
    directories (``SKIP_DIRS``) are excluded either way.
    """
    root = Path(root)
    files: list[str] = []
    listed = git(root, "ls-files", "-z") if is_git_repo(root) else None
    if listed is not None:
        files = [f for f in listed.split("\0") if f]
    else:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if not is_skipped_dir(d))
            for name in sorted(filenames):
                rel = Path(dirpath, name).relative_to(root).as_posix()
                files.append(rel)
                if len(files) >= _WALK_CAP:
                    break
            if len(files) >= _WALK_CAP:
                logger.warning("survey: %s has more than %d files; listing stopped",
                               root, _WALK_CAP)
                break
    return sorted(f for f in files
                  if not any(is_skipped_dir(p) for p in f.split("/")[:-1]))


# ── identity ─────────────────────────────────────────────────────────────────

_USERINFO_RE = re.compile(r"^(?P<scheme>[a-z][a-z0-9+.-]*://)[^/@]*@", re.IGNORECASE)


def redact_remote(url: str | None) -> str | None:
    """A remote URL without credentials or query string (``https://u:t@h/x?y`` →
    ``https://h/x``). scp-style ``git@host:path`` keeps its user, which is not a
    secret."""
    if not url:
        return None
    url = url.strip().split("?", 1)[0].split("#", 1)[0]
    return _USERINFO_RE.sub(lambda m: m.group("scheme"), url)


_LICENSES = (  # (name, a phrase only that license's text carries), first match wins
    ("Apache-2.0", "apache license"), ("MPL-2.0", "mozilla public license"),
    ("AGPL-3.0", "gnu affero general public license"),
    ("LGPL", "gnu lesser general public license"), ("GPL", "gnu general public license"),
    ("BSD-3-Clause", "neither the name"), ("BSD-2-Clause", "redistributions in binary form"),
    ("MIT", "permission is hereby granted, free of charge"),
    ("ISC", "permission to use, copy, modify, and/or distribute"),
    ("Unlicense", "this is free and unencumbered software"),
)


def license_name(root: Path, files: list[str]) -> str | None:
    """The license of a root ``LICENSE*`` / ``COPYING*`` file by its text, or
    "unrecognized" when one exists but matches no known license; None when absent."""
    for rel in files:
        if "/" in rel or not re.match(r"(?i)^(licen[cs]e|copying)(\.|$)", rel):
            continue
        text = (safe_read_text(root, rel) or "").lower()
        text = " ".join(text.split())
        for name, phrase in _LICENSES:
            if phrase in text:
                return name
        return "unrecognized"
    return None


def identity(root: Path, files: list[str]) -> dict:
    """``{remote, head, default_branch, license}`` — each None when unknown. The git
    fields come only from a repo whose top level is *root* (``is_git_repo``): a folder
    inside another repo would otherwise report that repo's remote and HEAD."""
    if not is_git_repo(root):
        return {"remote": None, "head": None, "default_branch": None,
                "license": license_name(root, files)}
    head = (git(root, "rev-parse", "HEAD") or "").strip() or None
    remote = redact_remote(git(root, "config", "--get", "remote.origin.url"))
    branch = (git(root, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
              or "").strip()
    if branch.startswith("origin/"):
        branch = branch[len("origin/"):]
    if not branch:
        branch = (git(root, "rev-parse", "--abbrev-ref", "HEAD") or "").strip()
    return {"remote": remote, "head": head,
            "default_branch": branch if branch and branch != "HEAD" else None,
            "license": license_name(root, files)}


# ── history ──────────────────────────────────────────────────────────────────

# A fix commit names itself anywhere in the subject: "fix: …", "fix(x): …",
# "session: fix the …", "Revert \"…\"", "bugfix …". Word boundaries keep "prefix" and
# "fixture" out.
_FIX_RE = re.compile(r"\b(fix(es|ed)?|hotfix|bugfix|revert(s|ed)?)\b", re.IGNORECASE)
_REVERT_RE = re.compile(r"\brevert(s|ed)?\b", re.IGNORECASE)
_SHA_RE = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")
_HISTORY_COMMITS = 500


def _log_commits(root: Path, n: int) -> list[dict]:
    """``[{hash, date, subject, files}]`` of the last *n* commits (newest first); none
    unless *root* is a repo's top level (``is_git_repo``), so a folder inside another
    repo never gets that repo's history — the same rule ``list_files`` follows."""
    if not is_git_repo(root):
        return []
    out = git(root, "log", "--no-ext-diff", "--no-color", f"-n{n}",
              "--pretty=format:%x1e%H%x1f%ad%x1f%s", "--date=short", "--name-only")
    commits: list[dict] = []
    for block in (out or "").split("\x1e"):
        lines = block.strip("\n").split("\n")
        head = lines[0].split("\x1f") if lines and lines[0] else []
        if len(head) != 3 or not _SHA_RE.match(head[0]):
            continue
        commits.append({"hash": head[0], "date": head[1], "subject": head[2].strip(),
                        "files": [f for f in lines[1:] if f.strip()]})
    return commits


def log_commits(root: Path, n: int = 2000) -> list[dict]:
    """The last *n* commits, newest first — one git call shared by the history readers."""
    return _log_commits(root, n)


def git_gotchas(root: Path, commits: list[dict] | None = None) -> list[dict]:
    """Files that keep needing fixes, with the commits as evidence.

    A file touched by two or more commits whose subject says fix / hotfix / bugfix /
    revert (anywhere, as a word) among the last
    ``_HISTORY_COMMITS``, and any reverted change, becomes ``{file, kind, commits:
    [{hash, date, subject}], count}``; ``kind`` is ``repeated_fix`` or ``revert``.
    Every item carries real commit hashes; nothing is inferred. Sorted by count, then
    file, so the same history gives the same list.
    """
    commits = _log_commits(root, _HISTORY_COMMITS) if commits is None else commits
    fixes = [c for c in commits[:_HISTORY_COMMITS] if _FIX_RE.search(c["subject"])]
    by_file: dict[str, list[dict]] = {}
    for c in fixes:
        for f in c["files"]:
            by_file.setdefault(f, []).append(c)
    items: dict[str, dict] = {}

    def _ev(cs: list[dict]) -> list[dict]:
        return [{"hash": c["hash"], "date": c["date"], "subject": c["subject"]} for c in cs]

    for f, cs in by_file.items():
        if len(cs) >= 2:
            items[f] = {"file": f, "kind": "repeated_fix", "count": len(cs),
                        "commits": _ev(cs[:5])}
    for c in fixes:
        if _REVERT_RE.search(c["subject"]):
            for f in c["files"][:1]:
                items.setdefault(f, {"file": f, "kind": "revert", "count": 1,
                                     "commits": _ev([c])})
    return sorted(items.values(), key=lambda i: (-i["count"], i["file"]))


def last_commit_times(root: Path, n: int = 2000,
                      commits: list[dict] | None = None) -> dict[str, str]:
    """``{path: YYYY-MM-DD}`` of the newest commit touching each path among the last
    *n* commits. Used to order the last tier deterministically (a fresh clone gives
    every file the same filesystem mtime)."""
    out: dict[str, str] = {}
    for c in (_log_commits(root, n) if commits is None else commits):
        for f in c["files"]:
            out.setdefault(f, c["date"])
    return out
