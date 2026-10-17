"""Artifact source classification — link vs copy (Artifact-Lifecycle P0, ①).

WHAT THIS OWNS
    ``classify_source(abs_path, swarmws_root=None) -> (Literal['link','copy'], reason)``:
    a PURE decision about how a registered artifact relates to its backing file.

      - ``link`` — the file lives inside a tree we own (SwarmWS or a bound-repo
        worktree). The artifact is a LIVE POINTER: content is read from disk on
        open and diffed via git (``baseRef``). The file IS the artifact.
      - ``copy`` — the file is disposable / external / not-a-file. If a version
        store ever captures it (P1), it captures the BYTES; the on-disk original
        is not authoritative.

WHY IT DELEGATES (the load-bearing design decision — Gate-0 SUPPORTED)
    Tree ownership already has ONE canonical resolver: ``needs_human_review._owning_tree``.
    ``canvas_surface.is_canvas_surfaceable`` already reuses it "so the boundary can
    never drift (P8)". This module does the SAME — it is a THIN WRAPPER, never a
    second ``.git`` walk-up. A parallel classifier would drift from ``_owning_tree``
    (run_4de279ca: "a second denylist drifts"). So:

        owned by a known tree  → link   (delegated to _owning_tree)
        not owned + noise/tmp  → copy   (delegated to canvas_noise.is_noise_path)
        not owned + otherwise  → copy

    Disposable-root precedence ("a tmp clone is still disposable even though it has
    its own .git") is satisfied FOR FREE: ``_owning_tree`` only knows SwarmWS + bound
    worktrees, so an external ``/tmp/x/.git`` is never owned → None → the fallback's
    ``is_noise_path`` catches the tmp root. No explicit ordering code is needed.

SECURITY
    A ``link`` verdict's tree root authorizes later reads, so ``/`` and ``$HOME`` are
    never link roots. ``_owning_tree`` structurally cannot return them (SwarmWS is a
    specific subdir, worktrees are specific dirs), and we additionally hard-refuse
    them defensively. Non-absolute / empty / NUL-bearing input → ``copy`` (declined,
    never raised) — mirrors ``canvas_surface``'s "not ours to classify → decline".
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Optional

SourceKind = Literal["link", "copy"]

# Roots too broad to ever authorize as a link tree. A link root gates later reads,
# so these must be refused even in the impossible case _owning_tree returned one.
_FORBIDDEN_LINK_ROOTS: frozenset[str] = frozenset(
    {os.path.abspath(os.sep), os.path.abspath(os.path.expanduser("~"))}
)


def classify_source(
    abs_path: str, swarmws_root: Optional[str] = None
) -> tuple[SourceKind, str]:
    """Classify a path as ``link`` (owned live pointer) or ``copy`` (snapshot candidate).

    Args:
        abs_path: An ABSOLUTE filesystem path. Non-absolute / empty / NUL-bearing
            input is declined → ``("copy", "<reason>")`` (never raises).
        swarmws_root: Optional SwarmWS root override (absolute). Defaults to the
            live ``project_registry.get_swarmws()``. Present for test isolation.

    Returns:
        ``(kind, reason)`` where ``kind`` is ``'link'`` or ``'copy'`` and ``reason``
        is a short machine-stable tag for logging/telemetry.
    """
    # Decline unclassifiable input — same discipline as canvas_surface.
    if not abs_path or "\x00" in abs_path:
        return "copy", "unusable-input"

    p = Path(os.path.expanduser(abs_path))
    if not p.is_absolute():
        return "copy", "not-absolute"
    try:
        p = p.resolve()
    except (OSError, RuntimeError):
        return "copy", "unresolvable"

    # Delegate tree ownership to the SINGLE canonical resolver. Owned → link.
    try:
        from core.needs_human_review import _owning_tree
        from core.project_registry import get_swarmws

        ws_root = (
            Path(swarmws_root).resolve()
            if swarmws_root is not None
            else Path(get_swarmws()).resolve()
        )
        owning = _owning_tree(p, ws_root)
    except Exception:  # noqa: BLE001 — fail-safe: never let classification raise
        import logging as _logging
        _logging.getLogger(__name__).debug("classify_source: suppressed exception on a fail-safe path", exc_info=True)
        owning = None

    if owning is not None:
        tree_root = owning[0]
        # Defensive: a link root authorizes reads — never honor an over-broad root.
        if str(tree_root) in _FORBIDDEN_LINK_ROOTS:
            return "copy", "forbidden-link-root"
        return "link", str(tree_root)

    # Not owned by any known tree. Disposable / noise → copy (this is where a tmp
    # clone lands: _owning_tree returned None, is_noise_path catches the tmp root).
    try:
        from core.canvas_noise import is_noise_path

        if is_noise_path(p):
            return "copy", "disposable-or-noise"
    except Exception:  # noqa: BLE001 — fail-safe
        import logging as _logging
        _logging.getLogger(__name__).debug("classify_source: suppressed exception on a fail-safe path", exc_info=True)
        pass

    # External, non-noise path — still a copy (we don't own it; not a live pointer).
    return "copy", "external"
