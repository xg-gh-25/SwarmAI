"""In-memory cached application configuration backed by ~/.swarm-ai/config.json.

Single source of truth for non-secret application settings (Bedrock toggle,
AWS region, model selection, available models, model mapping, base URL,
experimental betas flag).

Key design decisions:

- **Single source of truth**: ``config.json`` is the ONLY place config lives.
  No DB storage, no env var defaults, no migration from other sources.
- **Zero IO on reads**: The config file is loaded into an in-memory dict at
  startup.  All ``get()`` calls return from the cache.
- **Secret filtering on writes**: ``update()`` strips AWS credentials, API keys,
  and bearer tokens before persisting to disk.
- **Graceful fallback**: If the config file is missing, empty, or contains
  invalid JSON, the manager falls back to ``DEFAULT_CONFIG``.
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
        "claude-opus-4-5-20251101",
        "claude-sonnet-4-5-20250929",
    ],
    "bedrock_model_map": {
        "claude-opus-4-6": "us.anthropic.claude-opus-4-6-v1",
        "claude-sonnet-4-6": "us.anthropic.claude-sonnet-4-6",
        "claude-sonnet-4-5-20250929": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        "claude-opus-4-5-20251101": "us.anthropic.claude-opus-4-5-20251101-v1:0",
    },
    "anthropic_base_url": None,
    "claude_code_disable_experimental_betas": True,
}


# ---------------------------------------------------------------------------
# AppConfigManager
# ---------------------------------------------------------------------------


class AppConfigManager:
    """In-memory cached config backed by ``~/.swarm-ai/config.json``.

    **Single source of truth** — config.json is the ONLY place settings
    live.  No DB storage, no env var defaults, no migration from other
    sources.  If the file doesn't exist, it's created from
    ``DEFAULT_CONFIG``.

    Typical lifecycle::

        mgr = AppConfigManager()
        mgr.load()                       # once at startup
        region = mgr.get("aws_region")   # zero IO
        mgr.update({"aws_region": "eu-west-1"})  # write-through
    """

    def __init__(self, config_path: Path | None = None) -> None:
        self._config_path: Path = config_path or (get_app_data_dir() / "config.json")
        self._cache: dict[str, Any] | None = None

    # -- public API --------------------------------------------------------

    def load(self) -> dict[str, Any]:
        """Load config from file into the in-memory cache.

        Called once at startup.  If the file is missing, empty, or contains
        invalid JSON the cache is populated with ``DEFAULT_CONFIG`` and
        written to disk.

        Returns:
            The loaded (or default) configuration dict.
        """
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
