"""In-memory cached application configuration backed by SwarmWS/config.json.

Single source of truth for non-secret application settings (Bedrock toggle,
AWS region, model selection, available models, model mapping, base URL,
sandbox settings).

Key design decisions:

- **Single source of truth**: ``SwarmWS/config.json`` is the ONLY place config
  lives.  No DB storage, no env var defaults, no migration from other sources.
- **Zero IO on reads**: The config file is loaded into an in-memory dict at
  startup.  All ``get()`` calls return from the cache.
- **Secret filtering on writes**: ``update()`` strips AWS credentials, API keys,
  and bearer tokens before persisting to disk.
- **Graceful fallback**: If the config file is missing, empty, or contains
  invalid JSON, the manager falls back to ``DEFAULT_CONFIG``.
- **Legacy migration**: On first load, if the new path doesn't exist but the
  legacy ``~/.swarm-ai/config.json`` does, the file is moved automatically.
- **File permissions**: The config file is created with ``0o600`` (owner
  read/write only) for privacy.

Public API:

- ``AppConfigManager``  — Main class with ``load()``, ``get()``, ``update()``,
  ``reload()`` methods.
- ``DEFAULT_CONFIG``     — Dict of default configuration values.
- ``SECRET_KEYS``        — Frozenset of keys that must never be persisted.
"""

import json
import logging
import os
import stat
from pathlib import Path
from typing import Any

from config import get_app_data_dir

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SECRET_KEYS: frozenset[str] = frozenset({
    "aws_access_key_id",
    "aws_secret_access_key",
    "aws_session_token",
    "aws_bearer_token",
    "anthropic_api_key",
})

DEFAULT_CONFIG: dict[str, Any] = {
    "use_bedrock": True,
    "aws_region": "us-east-1",
    "default_model": "claude-opus-4-6",
    "available_models": [
        "claude-opus-4-6",
        "claude-sonnet-4-6",
    ],
    "bedrock_model_map": {
        "claude-opus-4-6": "us.anthropic.claude-opus-4-6-v1",
        "claude-sonnet-4-6": "us.anthropic.claude-sonnet-4-6",
    },
    "anthropic_base_url": None,
    "sandbox_additional_write_paths": "",
    "sandbox_enabled_default": False,
    "sandbox_auto_allow_bash": True,
    "sandbox_excluded_commands": "docker",
    "sandbox_allow_unsandboxed": False,
    "sandbox_allowed_hosts": "*",
    "evolution": {
        "enabled": True,
        "max_retries": 3,
        "verification_timeout_seconds": 120,
        "auto_approve_skills": False,
        "auto_approve_scripts": False,
        "auto_approve_installs": False,
        "proactive_enabled": True,
        "stuck_detection_enabled": True,
        "max_triggers_per_session": 3,
        "same_type_cooldown_seconds": 60,
        "max_active_entries": 30,
        "deprecation_days": 30,
    },
}


# ---------------------------------------------------------------------------
# AppConfigManager
# ---------------------------------------------------------------------------


class AppConfigManager:
    """In-memory cached config backed by ``SwarmWS/config.json``.

    **Single source of truth** — config.json is the ONLY place settings
    live.  No DB storage, no env var defaults, no migration from other
    sources.  If the file doesn't exist, it's created from
    ``DEFAULT_CONFIG``.  Legacy ``~/.swarm-ai/config.json`` is migrated
    automatically on first load.

    Typical lifecycle::

        mgr = AppConfigManager()          # or AppConfigManager.instance()
        mgr.load()                        # once at startup
        region = mgr.get("aws_region")    # zero IO
        mgr.update({"aws_region": "eu-west-1"})  # write-through

    Use ``AppConfigManager.instance()`` to get the process-wide singleton
    (avoids creating new objects that re-read the config file).  The
    regular constructor is kept for tests and explicit path overrides.
    """

    _instance: "AppConfigManager | None" = None

    @classmethod
    def instance(cls) -> "AppConfigManager":
        """Return the process-wide singleton (lazy-created on first call).

        The singleton uses the default config path.  Call the constructor
        directly if you need a custom path (e.g. in tests).
        """
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def _reset_instance(cls) -> None:
        """Reset the singleton — for tests only."""
        cls._instance = None

    def __init__(self, config_path: Path | None = None) -> None:
        self._config_path: Path = config_path or (get_app_data_dir() / "SwarmWS" / "config.json")
        self._cache: dict[str, Any] | None = None

    # -- public API --------------------------------------------------------

    def load(self) -> dict[str, Any]:
        """Load config from file into the in-memory cache.

        Called once at startup.  Migrates from the legacy location
        (``~/.swarm-ai/config.json``) if the new path doesn't exist yet.
        If the file is missing, empty, or contains invalid JSON the cache
        is populated with ``DEFAULT_CONFIG`` and written to disk.

        Returns:
            The loaded (or default) configuration dict.
        """
        # One-time migration: move legacy ~/.swarm-ai/config.json → SwarmWS/config.json
        if not self._config_path.exists():
            legacy = get_app_data_dir() / "config.json"
            if legacy.is_file() and not legacy.is_symlink():
                try:
                    self._config_path.parent.mkdir(parents=True, exist_ok=True)
                    import shutil
                    shutil.move(str(legacy), str(self._config_path))
                    logger.info("Migrated config.json from %s → %s", legacy, self._config_path)
                except OSError as exc:
                    logger.warning("Failed to migrate legacy config.json: %s", exc)

        try:
            raw = self._config_path.read_text(encoding="utf-8").strip()
            if not raw:
                raise ValueError("empty file")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("root is not a JSON object")
            # Merge with defaults so new keys are always present
            merged = {**DEFAULT_CONFIG, **data}
            self._cache = merged
            logger.info("Loaded config from %s", self._config_path)
        except FileNotFoundError:
            logger.info(
                "Config file not found at %s — creating with defaults",
                self._config_path,
            )
            self._cache = dict(DEFAULT_CONFIG)
            self._write_to_disk()
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning(
                "Invalid config at %s (%s) — using defaults",
                self._config_path,
                exc,
            )
            self._cache = dict(DEFAULT_CONFIG)
            self._write_to_disk()
        return dict(self._cache)

    def get(self, key: str, default: Any = None) -> Any:
        """Read a value from the in-memory cache (zero IO).

        If ``load()`` has not been called yet, it is called automatically.
        """
        if self._cache is None:
            self.load()
        return self._cache.get(key, default)  # type: ignore[union-attr]

    def update(self, updates: dict[str, Any]) -> None:
        """Merge *updates* into the cache and persist to disk.

        Secret keys (see ``SECRET_KEYS``) are silently stripped before
        the dict is written to the config file.
        """
        if self._cache is None:
            self.load()
        assert self._cache is not None
        self._cache.update(updates)
        self._write_to_disk()
        logger.info("Config updated: %s", list(updates.keys()))

    def reload(self) -> None:
        """Force re-read from the config file (e.g. after manual edits)."""
        self._cache = None
        self.load()

    # -- private helpers ----------------------------------------------------

    def _write_to_disk(self) -> None:
        """Persist the current cache to disk, stripping secret keys."""
        if self._cache is None:
            return

        clean = {
            k: v for k, v in self._cache.items() if k not in SECRET_KEYS
        }

        self._config_path.parent.mkdir(parents=True, exist_ok=True)

        self._config_path.write_text(
            json.dumps(clean, indent=2, default=str) + "\n",
            encoding="utf-8",
        )

        try:
            os.chmod(self._config_path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError as exc:
            logger.debug("Could not set file permissions: %s", exc)
