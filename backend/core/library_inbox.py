"""Library Inbox adapter for single external file drops.

The endpoint validates a caller-selected file and submits its complete content to S6.
Directories remain invalid. No file is copied into governed Knowledge storage.
"""

from __future__ import annotations

from pathlib import Path

# Protected system roots — a caller-supplied source under any of these is refused
# (exfiltration guard). Covers unix + macOS sensitive trees. NOT an exhaustive
# security boundary (that's the OS's job) — it blocks the obvious pull-a-system-
# file-into-the-tracked-workspace vector.
_SYSTEM_ROOTS = (
    "/etc", "/private/etc", "/var", "/private/var", "/usr", "/bin", "/sbin",
    "/System", "/Library", "/root", "/proc", "/sys", "/dev",
)

# Secret dirs UNDER the user's home — refused too. A home-dir carve-out (legit
# files live under ~) must NOT expose credential/key stores. Matched as a home-
# relative first segment (~/.ssh, ~/.aws, ...), so ~/Desktop/.ssh-notes is fine.
_HOME_SECRET_DIRS = (
    ".ssh", ".aws", ".gnupg", ".gpg", ".config/gcloud", ".kube", ".docker",
    ".netrc", ".password-store", ".swarm-ai",  # .swarm-ai = our own creds/DB
)


def _is_system_path(resolved: Path) -> bool:
    """True if `resolved` (already .resolve()'d) is at/under a protected root OR a
    home-secret dir (~/.ssh, ~/.aws, ...).

    Carve-out: the OS temp dir is a LEGITIMATE staging location (exports, browser
    downloads-then-move, and — on macOS — it resolves under /private/var/folders/,
    which the /var root would otherwise catch). A file the user chose to drop from
    temp is fine; the guard targets /etc, /usr, /System, /var/log, ~/.ssh, etc."""
    import tempfile
    from core.true_path import true_case
    resolved = true_case(resolved)      # ~/.SSH is ~/.ssh on a case-insensitive disk
    s = str(resolved)
    tmp_root = str(true_case(Path(tempfile.gettempdir()).resolve()))
    if s == tmp_root or s.startswith(tmp_root + "/"):
        return False
    for root in _SYSTEM_ROOTS:
        if s == root or s.startswith(root + "/"):
            return True
    # Home-secret dirs (credential/key stores under ~).
    try:
        home = true_case(Path.home().resolve())
        if resolved == home or home in resolved.parents:
            rel = resolved.relative_to(home).as_posix()
            for secret in _HOME_SECRET_DIRS:
                if rel == secret or rel.startswith(secret + "/"):
                    return True
    except (OSError, ValueError):
        pass
    return False


def copy_to_inbox(knowledge_dir: Path, source: Path) -> list:
    """Validate one external file and submit its complete content through S6.

    The historical function name remains the endpoint entry point; no copy occurs.
    """
    _ = knowledge_dir
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
    from core.ingestion import api_v2
    from core.ingestion.sources.s6_external import extract_external
    content = resolved.read_text(encoding="utf-8")
    root_id = api_v2.register_root(
        "file_revision", f"{resolved}#file", content, "external")
    return extract_external(
        f"library-inbox:{resolved}", content,
        evidence_anchor=f"doc:{root_id}", project_hint=None,
        root_ids=(root_id,))

