"""DDD distribution policy — the fail-CLOSED reach gate for the packager.

This module answers ONE question: *given a DDD's ``aim.json``, where is this brain
permitted to be distributed?* It is deliberately a **separate module from
``verify_ddd_complete.py``** (the DDD *completeness* gate), because the two have
OPPOSITE failure philosophies and must never be conflated (Gate-1 H1):

- ``verify_ddd_complete`` is **fail-OPEN** — a completeness check must never itself
  become a source of failure; an unreadable doc degrades to a warning.
- This policy is **fail-CLOSED** — an absent / malformed / typo'd ``distribution``
  block resolves to "NOT distributable" (``targets=[]``, ``visibility=internal``).
  A missing declaration must never widen reach by inference (the C041 lesson:
  reach is declared, never guessed).

Vocabulary is pinned to the design SSOT
(``docs/2026-07-20-ddd-dual-target-distribution-design.md`` §0.2):
``targets`` ⊆ {``aim-capabilities``, ``open-plugin``}; ``visibility`` ∈
{``internal``, ``external``}. An *unrecognized-but-present* token is a **loud
warning**, never a silent drop — so a vocabulary mismatch (e.g. a caller writing
``aim`` instead of ``aim-capabilities``) surfaces instead of masquerading as a
legitimately-empty ``targets:[]`` (Gate-1 C1).

Pure functions, no I/O beyond an optional file read. Import-safe, no SwarmAI
backend dependency — travels inside a distributed package.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# --- Pinned vocabulary (design SSOT §0.2) --------------------------------------
TARGET_AIM = "aim-capabilities"
TARGET_OPEN_PLUGIN = "open-plugin"
KNOWN_TARGETS: frozenset[str] = frozenset({TARGET_AIM, TARGET_OPEN_PLUGIN})

VISIBILITY_INTERNAL = "internal"
VISIBILITY_EXTERNAL = "external"
KNOWN_VISIBILITY: frozenset[str] = frozenset({VISIBILITY_INTERNAL, VISIBILITY_EXTERNAL})

# Fail-closed defaults: nothing leaves the home system unless explicitly declared.
_DEFAULT_TARGETS: tuple[str, ...] = ()
_DEFAULT_VISIBILITY = VISIBILITY_INTERNAL


@dataclass(frozen=True)
class DistributionPolicy:
    """The resolved reach of a DDD. ``targets`` is the CEILING a caller may subset.

    ``warnings`` records anything that was rejected/ignored (unknown token, malformed
    block) — loud, never silent (Gate-1 C1). ``declared`` is False when the block was
    absent or unusable, i.e. the policy is the fail-closed default.
    """

    targets: tuple[str, ...] = _DEFAULT_TARGETS
    visibility: str = _DEFAULT_VISIBILITY
    declared: bool = False
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_distributable(self) -> bool:
        return bool(self.targets)

    def permits(self, target: str) -> bool:
        """Is *target* within this ceiling?"""
        return target in self.targets

    def permits_external_publish(self) -> bool:
        """Emit ≠ publish. A public publish is allowed ONLY when visibility is
        explicitly external (design §0.2 invariant 1). An open-plugin tree may be
        EMITTED under internal visibility for a private install, but never PUBLISHED
        publicly — that gate lives here."""
        return self.visibility == VISIBILITY_EXTERNAL


def validate_distribution(aim: Any) -> DistributionPolicy:
    """Resolve a DDD's distribution reach from its parsed ``aim.json`` dict.

    FAIL-CLOSED: any problem (not a dict, no ``distribution`` block, block not a
    dict, ``targets`` not a list, unknown visibility) → the default
    (``targets=()``, ``visibility=internal``, ``declared=False``). Unknown-but-present
    tokens are dropped WITH a warning (never silently), so a vocab mismatch is visible.
    """
    warnings: list[str] = []

    if not isinstance(aim, dict):
        return DistributionPolicy(warnings=("aim.json is not an object → not distributable",))

    block = aim.get("distribution")
    if block is None:
        # Absent block is the common, legitimate "not yet declared" case — no warning
        # (would be noise on every non-distributed DDD), just the fail-closed default.
        return DistributionPolicy(declared=False)

    if not isinstance(block, dict):
        return DistributionPolicy(
            declared=False,
            warnings=(f"distribution block is {type(block).__name__}, not an object → not distributable",),
        )

    # --- targets ---------------------------------------------------------------
    raw_targets = block.get("targets", [])
    valid_targets: list[str] = []
    if not isinstance(raw_targets, list):
        warnings.append(f"distribution.targets is {type(raw_targets).__name__}, not a list → treated as empty")
        raw_targets = []
    for t in raw_targets:
        if not isinstance(t, str):
            warnings.append(f"distribution.targets entry {t!r} is not a string → ignored")
            continue
        if t not in KNOWN_TARGETS:
            # Loud, NOT silent: this is the C1 vocab-drift guard.
            warnings.append(
                f"distribution.targets has unknown token {t!r} "
                f"(expected one of {sorted(KNOWN_TARGETS)}) → ignored"
            )
            continue
        if t not in valid_targets:
            valid_targets.append(t)

    # --- visibility ------------------------------------------------------------
    raw_vis = block.get("visibility", _DEFAULT_VISIBILITY)
    if not isinstance(raw_vis, str) or raw_vis not in KNOWN_VISIBILITY:
        warnings.append(
            f"distribution.visibility {raw_vis!r} invalid "
            f"(expected one of {sorted(KNOWN_VISIBILITY)}) → fail-closed to '{_DEFAULT_VISIBILITY}'"
        )
        raw_vis = _DEFAULT_VISIBILITY

    # Deterministic target order (sorted) so downstream emit is reproducible (H3).
    return DistributionPolicy(
        targets=tuple(sorted(valid_targets)),
        visibility=raw_vis,
        declared=True,
        warnings=tuple(warnings),
    )


def validate_distribution_file(aim_path: str | Path) -> DistributionPolicy:
    """Read + validate ``aim.json`` from disk. Unreadable/invalid JSON → fail-closed."""
    p = Path(aim_path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return DistributionPolicy(warnings=(f"aim.json unreadable/invalid ({p}): {type(exc).__name__}",))
    return validate_distribution(data)


def resolve_requested_targets(
    policy: DistributionPolicy, requested: list[str] | None
) -> tuple[list[str], list[str]]:
    """Apply the SUBSET-ONLY rule (design §0.2 invariant 5 / Gate-1 AC5).

    The declared ``policy.targets`` is the CEILING. A caller may request a SUBSET;
    it may NEVER add a target the DDD didn't declare. Returns
    ``(permitted, refused)`` where ``refused`` are requested targets outside the
    ceiling (the caller tried to widen — surfaced, never honored).

    ``requested=None`` → emit the full declared set (no narrowing).
    """
    if requested is None:
        return list(policy.targets), []
    permitted = [t for t in requested if t in policy.targets]
    refused = [t for t in requested if t not in policy.targets]
    return permitted, refused
