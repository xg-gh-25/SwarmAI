"""Forward-compatible schema migrations for ``.project.json`` metadata.

This module keeps all migration logic isolated from the workspace manager
so that each version-step function can be independently tested.  Migrations
are registered via a decorator and chained automatically when the running
application encounters a ``.project.json`` whose ``schema_version`` is older
than ``CURRENT_SCHEMA_VERSION``.

Key public symbols:

- ``CURRENT_SCHEMA_VERSION``   — The version the running app expects
- ``MIGRATION_REGISTRY``       — OrderedDict of ``(from, to) → fn`` steps
- ``register_migration``       — Decorator to register a new step
- ``migrate_if_needed``        — Entry point: brings any older dict up to date
- ``get_migration_chain``      — Introspection helper for planned migrations
- ``compare_versions``         — Semver comparison returning -1, 0, or 1
"""

from __future__ import annotations

import copy
import logging
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Callable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema version the running application expects
# ---------------------------------------------------------------------------
CURRENT_SCHEMA_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Migration registry — maps (from_version, to_version) → migration function
# Each migration function is a pure function: dict → dict
# ---------------------------------------------------------------------------
MIGRATION_REGISTRY: OrderedDict[tuple[str, str], Callable[[dict], dict]] = OrderedDict()


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def compare_versions(a: str, b: str) -> int:
    """Compare two semver strings (MAJOR.MINOR.PATCH only).

    Returns -1 if *a* < *b*, 0 if equal, 1 if *a* > *b*.

    Uses tuple comparison on ``(int, int, int)`` parsed from each string.
    Raises ``ValueError`` for malformed version strings (non-numeric parts,
    missing components, pre-release suffixes).
    """
    def _parse(v: str) -> tuple[int, int, int]:
        parts = v.split(".")
        if len(parts) != 3:
            raise ValueError(
                f"Invalid semver string '{v}': expected MAJOR.MINOR.PATCH"
            )
        try:
            return (int(parts[0]), int(parts[1]), int(parts[2]))
        except ValueError:
            raise ValueError(
                f"Invalid semver string '{v}': all components must be integers"
            )

    ta, tb = _parse(a), _parse(b)
    if ta < tb:
        return -1
    if ta > tb:
        return 1
    return 0


def register_migration(from_ver: str, to_ver: str) -> Callable:
    """Decorator to register a schema migration step.

    The decorated function must accept a ``dict`` (the raw metadata) and
    return a new ``dict`` with the migration applied.  It should be a pure
    function — no side effects.

    Example::

        @register_migration("1.0.0", "1.1.0")
        def _migrate_1_0_0_to_1_1_0(data: dict) -> dict:
            data = {**data}
            data.setdefault("new_field", "default_value")
            data["schema_version"] = "1.1.0"
            return data
    """
    # Validate version ordering
    if compare_versions(from_ver, to_ver) >= 0:
        raise ValueError(
            f"Migration from_ver ({from_ver}) must be less than to_ver ({to_ver})"
        )

    def decorator(fn: Callable[[dict], dict]) -> Callable[[dict], dict]:
        MIGRATION_REGISTRY[(from_ver, to_ver)] = fn
        return fn

    return decorator


def get_migration_chain(
    from_version: str, to_version: str
) -> list[tuple[str, str, Callable[[dict], dict]]]:
    """Return the ordered list of migration steps from *from_version* to *to_version*.

    Each element is a ``(from_ver, to_ver, migration_fn)`` triple.

    Returns an empty list when no migrations are needed (versions equal)
    or when no path exists.

    Raises:
        ValueError: If *from_version* >= *to_version*.
    """
    if compare_versions(from_version, to_version) >= 0:
        return []

    chain: list[tuple[str, str, Callable[[dict], dict]]] = []
    current = from_version

    while compare_versions(current, to_version) < 0:
        found = False
        for (fv, tv), fn in MIGRATION_REGISTRY.items():
            if fv == current:
                chain.append((fv, tv, fn))
                current = tv
                found = True
                break
        if not found:
            # No registered migration from current version — gap in chain
            break

    return chain


def migrate_if_needed(metadata: dict) -> tuple[dict, bool]:
    """Migrate a ``.project.json`` dict to ``CURRENT_SCHEMA_VERSION``.

    Args:
        metadata: Raw parsed JSON dict from ``.project.json``.

    Returns:
        ``(migrated_data, was_migrated)`` — the updated dict and a boolean
        indicating whether any migration was applied.

        - If ``schema_version`` equals ``CURRENT_SCHEMA_VERSION``, returns
          the data unchanged with ``was_migrated=False``.
        - If ``schema_version`` is *newer* than ``CURRENT_SCHEMA_VERSION``,
          returns the data unchanged with ``was_migrated=False`` (forward
          compatibility — never downgrade).
        - If ``schema_version`` is *older*, applies the migration chain and
          appends ``schema_migrated`` history entries to ``update_history``.
    """
    data = copy.deepcopy(metadata)
    current_ver = data.get("schema_version", "1.0.0")

    # Forward compatibility: do not downgrade newer schemas
    if compare_versions(current_ver, CURRENT_SCHEMA_VERSION) >= 0:
        return data, False

    chain = get_migration_chain(current_ver, CURRENT_SCHEMA_VERSION)
    if not chain:
        return data, False

    history_entries: list[dict] = []

    for from_ver, to_ver, fn in chain:
        logger.info(
            "Migrating .project.json schema from %s to %s", from_ver, to_ver
        )
        data = fn(data)

        # Build a history entry for this migration step
        history_entries.append({
            "version": data.get("version", 1),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": "schema_migrated",
            "changes": {"schema_version": {"from": from_ver, "to": to_ver}},
            "source": "system",
        })

    # Append migration history entries to the data's update_history
    if "update_history" not in data:
        data["update_history"] = []
    data["update_history"].extend(history_entries)

    return data, True
