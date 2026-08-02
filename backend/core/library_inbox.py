"""Library Inbox — a regularized landing zone for single-file drops.

Knowledge/Inbox/ is a staging area (design Cycle 6): a SINGLE external file MAY be
copied in (small, explicit, no-drift-risk — the ONE exception to the never-copy
rule). An external DIRECTORY is always MOUNTED, never copied. Re-filing out of the
Inbox is done later by the user or by chat.

This module owns only the mechanical copy + the directory guarantee; the API
router is a thin wrapper, and the overlay shows Inbox as its own native category.
"""

from __future__ import annotations

import shutil
from pathlib import Path

INBOX_NAME = "Inbox"

# Protected system roots — a caller-supplied source under any of these is refused
# (exfiltration guard). Covers unix + macOS sensitive trees. NOT an exhaustive
# security boundary (that's the OS's job) — it blocks the obvious pull-a-system-
# file-into-the-tracked-workspace vector.
_SYSTEM_ROOTS = (
    "/etc", "/private/etc", "/var", "/private/var", "/usr", "/bin", "/sbin",
    "/System", "/Library", "/root", "/proc", "/sys", "/dev",
)


def _is_system_path(resolved: Path) -> bool:
    """True if `resolved` (already .resolve()'d) is at or under a protected root.

    Carve-out: the OS temp dir is a LEGITIMATE staging location (exports, browser
    downloads-then-move, and — on macOS — it resolves under /private/var/folders/,
    which the /var root would otherwise catch). A file the user chose to drop from
    temp is fine; the guard targets /etc, /usr, /System, /var/log, etc."""
    import tempfile
    s = str(resolved)
    tmp_root = str(Path(tempfile.gettempdir()).resolve())
    if s == tmp_root or s.startswith(tmp_root + "/"):
        return False
    for root in _SYSTEM_ROOTS:
        if s == root or s.startswith(root + "/"):
            return True
    return False


def ensure_inbox(knowledge_dir: Path) -> Path:
    """Create Knowledge/Inbox/ if absent (idempotent). Returns its path."""
    inbox = Path(knowledge_dir) / INBOX_NAME
    inbox.mkdir(parents=True, exist_ok=True)
    return inbox


def copy_to_inbox(knowledge_dir: Path, source: Path) -> Path:
    """Copy a SINGLE file into Knowledge/Inbox/. Returns the landed path.

    Rejects a directory (dirs are MOUNTED, not copied — the core design decision)
    and a missing source. On a name collision, appends a non-clobbering numeric
    suffix so an existing Inbox file is never overwritten.
    """
    source = Path(source).expanduser()
    if not source.exists():
        raise FileNotFoundError(f"source does not exist: {source}")
    if source.is_dir():
        raise ValueError(
            f"{source} is a directory — directories are MOUNTED (index in place), "
            f"never copied into the Inbox (index-not-warehouse)."
        )
    # SAFETY (adversarial pass): source_path is caller-supplied, so a bare copy
    # would pull a sensitive HOST file (/etc/passwd, /etc/ssh/..., /var/...) into
    # the git-tracked workspace. Deny sensitive SYSTEM roots. A denylist (not a
    # home-only allowlist) is deliberate — a legit user drop can live outside ~
    # (e.g. an external volume /Volumes/... or a temp export), so an allowlist
    # would false-reject real files; the exfiltration risk is specifically the
    # system dirs below.
    resolved = source.resolve()
    if _is_system_path(resolved):
        raise ValueError(
            f"source {source} is under a protected system path — system files "
            f"may not be copied into the Inbox (exfiltration guard)."
        )
    inbox = ensure_inbox(knowledge_dir)
    dest = _unique_dest(inbox, source.name)
    shutil.copy2(source, dest)
    return dest


def _unique_dest(inbox: Path, name: str) -> Path:
    """A non-clobbering destination path in the inbox for `name`."""
    dest = inbox / name
    if not dest.exists():
        return dest
    stem, suffix = Path(name).stem, Path(name).suffix
    i = 1
    while True:
        candidate = inbox / f"{stem}-{i}{suffix}"
        if not candidate.exists():
            return candidate
        i += 1
