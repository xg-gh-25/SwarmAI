"""Shared MCP configuration validation utilities.

Extracted from ``backend/routers/mcp.py`` to make validation logic
importable by both the Validation_Service router and the
MCP_Config_Loader.  Contains:

- ``validate_env_no_system_db``  — Reject env vars pointing at SwarmAI's
  internal database (was ``_validate_env_no_system_db``).
- ``validate_config_entry``      — Schema-level checks for a single
  Config_Entry dict (required fields, connection-type constraints, env
  security).
"""

import logging
from pathlib import Path

from config import get_app_data_dir
from core.exceptions import ValidationException

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Protected-path guard — prevent MCP env vars from targeting system DB
# ---------------------------------------------------------------------------

def validate_env_no_system_db(env: dict[str, str]) -> None:
    """Reject env vars whose values resolve to SwarmAI's internal database.

    Checks every value that looks like a filesystem path (contains ``/``
    or ``\\``).  Resolves via ``Path.expanduser().resolve()`` and blocks:

    1. Exact match to ``~/.swarm-ai/data.db`` (or WAL/SHM companions).
    2. Any ``.db`` file anywhere inside ``~/.swarm-ai/``.

    This prevents users from accidentally pointing the SQLite MCP (or any
    future MCP) at the live system database, which would cause write-lock
    contention and allow unrestricted SQL (including DROP TABLE).

    Raises:
        ValidationException: When an env value resolves to a protected path.
    """
    if not env:
        return

    app_dir = get_app_data_dir().resolve()
    protected_files = {
        (app_dir / "data.db"),
        (app_dir / "data.db-wal"),
        (app_dir / "data.db-shm"),
    }

    for key, value in env.items():
        # Skip values that don't look like paths
        if "/" not in value and "\\" not in value:
            continue
        try:
            resolved = Path(value).expanduser().resolve()
        except (OSError, ValueError):
            continue

        # Block exact match to system DB files
        if resolved in protected_files:
            raise ValidationException(
                message="Protected path",
                detail=(
                    f"'{key}' points to SwarmAI's system database. "
                    f"Use a separate database file instead."
                ),
                fields=[{
                    "field": f"env.{key}",
                    "error": "Cannot target SwarmAI system database",
                }],
            )

        # Block any .db inside ~/.swarm-ai/
        if resolved.suffix == ".db":
            try:
                is_inside = resolved.is_relative_to(app_dir)
            except (ValueError, TypeError):
                is_inside = False
            if is_inside:
                raise ValidationException(
                    message="Protected path",
                    detail=(
                        f"'{key}' points to a database inside SwarmAI's "
                        f"data directory ({app_dir}). Use a path outside "
                        f"this directory."
                    ),
                    fields=[{
                        "field": f"env.{key}",
                        "error": "Cannot use databases inside SwarmAI data directory",
                    }],
                )


# ---------------------------------------------------------------------------
# Config entry schema validation
# ---------------------------------------------------------------------------

_REQUIRED_FIELDS = ("id", "name", "connection_type", "config")
_VALID_CONNECTION_TYPES = ("stdio", "sse", "http")


def validate_config_entry(entry: dict) -> list[str]:
    """Validate a Config_Entry dict and return a list of error messages.

    An empty list means the entry is valid.  Checks performed:

    1. Required top-level fields: ``id``, ``name``, ``connection_type``,
       ``config``.
    2. ``connection_type`` must be one of ``stdio``, ``sse``, ``http``.
    3. ``stdio`` entries must have a non-empty ``config.command``.
    4. ``sse`` / ``http`` entries must have a non-empty ``config.url``.
    5. If ``config.env`` is present, it is passed through
       :func:`validate_env_no_system_db` — a
       :class:`~core.exceptions.ValidationException` is raised (not
       returned as a string) for env-var violations, matching the
       original router behaviour.

    Returns:
        List of human-readable error strings (empty when valid).
    """
    errors: list[str] = []

    # 1. Required fields
    for field in _REQUIRED_FIELDS:
        if field not in entry or entry[field] is None:
            errors.append(f"Missing required field: '{field}'")

    # If we're missing critical fields, return early — further checks
    # would produce confusing cascading errors.
    if errors:
        return errors

    # Check that string fields are non-empty
    for field in ("id", "name"):
        if not isinstance(entry[field], str) or not entry[field].strip():
            errors.append(f"Field '{field}' must be a non-empty string")

    # 2. Connection type
    conn_type = entry["connection_type"]
    if conn_type not in _VALID_CONNECTION_TYPES:
        errors.append(
            f"Invalid connection_type '{conn_type}'; "
            f"must be one of {_VALID_CONNECTION_TYPES}"
        )
        return errors  # Can't validate config fields without valid type

    config = entry.get("config") or {}
    if not isinstance(config, dict):
        errors.append("Field 'config' must be a dict")
        return errors

    # 3. stdio → config.command required
    if conn_type == "stdio":
        cmd = config.get("command")
        if not cmd or (isinstance(cmd, str) and not cmd.strip()):
            errors.append(
                "stdio connection_type requires a non-empty "
                "'config.command'"
            )

    # 4. sse / http → config.url required
    if conn_type in ("sse", "http"):
        url = config.get("url")
        if not url or (isinstance(url, str) and not url.strip()):
            errors.append(
                f"{conn_type} connection_type requires a non-empty "
                f"'config.url'"
            )

    # 5. Env var security — raises ValidationException on violation
    env = config.get("env")
    if env and isinstance(env, dict):
        validate_env_no_system_db(env)

    return errors
