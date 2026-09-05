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
from model_registry import (
    DEFAULT_JUDGE_MODEL,
    FLAGSHIP_MODEL,
    default_available_models,
    default_bedrock_model_map,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SECRET_KEYS: frozenset[str] = frozenset({
    "aws_access_key_id",
    "aws_secret_access_key",
    "aws_session_token",
    "aws_bearer_token",
    # The Bedrock bearer token (AWS_BEARER_TOKEN_BEDROCK). A DEDICATED key —
    # NOT the vague orphan `aws_bearer_token` above — so it maps 1:1 to the
    # botocore-derived env var name and can't be confused with any other
    # AWS bearer token. Persisted to the durable 0o600 secret store, injected
    # at spawn by _configure_claude_environment when auth_method=bedrock_api_key.
    "aws_bearer_token_bedrock",
    "anthropic_api_key",
})

DEFAULT_CONFIG: dict[str, Any] = {
    "use_bedrock": True,
    "aws_region": "us-east-1",
    # All three DERIVE from model_registry (the single authority) — never
    # re-add a literal model name here. These used to be hand-written copies
    # that drifted two generations behind the live flagship, so a missing or
    # corrupt config.json silently downgraded the model.
    #
    # available_models is flagship-FIRST: routers/settings.py auto-resets
    # default_model to available_models[0] when the list changes, so a
    # newest-last order would silently select the OLDEST model as default.
    "default_model": FLAGSHIP_MODEL,
    "available_models": default_available_models(),
    "bedrock_model_map": default_bedrock_model_map(),
    "thinking_mode": "adaptive",       # "adaptive" | "enabled" | "disabled"
    "thinking_effort": "high",         # "low" | "medium" | "high" | "xhigh" | "max"
    "anthropic_base_url": None,
    # Persisted, non-secret. The active auth method chosen at setup, so error
    # remediation (CredentialBanner + spawn pre-flight) can be method-aware:
    # use_bedrock alone can't distinguish "ada" from "sso" (both are Bedrock).
    # Values: "ada" | "sso" | "apikey" | "iam_role" | "bedrock_api_key".
    # Written on successful verify.
    "auth_method": None,
    # Persisted display context ("internal" | "external"), mirrors the wizard's
    # detected/toggled deployment context so Settings shows the same card set.
    "deployment_context": None,
    "sandbox_additional_write_paths": "~/.swarm-ai/",
    "sandbox_enabled_default": False,
    "sandbox_auto_allow_bash": True,
    "sandbox_excluded_commands": "docker,ps,pgrep,pkill,top,open,screencapture,osascript,launchctl",
    "sandbox_allow_unsandboxed": False,
    "sandbox_allowed_hosts": "*",
    # Pinned judge for self-eval — DERIVED like the three model fields above.
    # A literal here was a fourth independent "default judge" value (alongside
    # eval_runner's fallback and the Hive seed), and it named a model the seed
    # did not. Short name; resolved to a Bedrock ID via bedrock_model_map /
    # the registry, never passed raw to converse().
    "eval_judge_model": DEFAULT_JUDGE_MODEL,
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
        "optimizer": "auto",           # "auto" | "llm" | "heuristic"
        "high_confidence": 0.35,       # Deploy threshold
        "med_confidence": 0.15,        # Recommend threshold
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
        # Secrets live in a SEPARATE file next to config.json — NEVER in
        # config.json itself (which is git-tracked / backed up). This file is
        # 0o600 and gitignored. It is the durable store for SECRET_KEYS so an
        # API key survives a daemon restart (config.json strips secrets by
        # design — see _write_to_disk).
        self._secret_path: Path = self._config_path.parent / "secrets.json"
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
        # Hydrate secrets from the separate 0o600 store into the cache (never
        # from config.json). This makes SECRET_KEYS durable across restarts.
        self._load_secrets()
        return dict(self._cache)

    def _load_secrets(self) -> None:
        """Merge persisted secrets (secrets.json) into the in-memory cache.

        Secrets are stored separately from config.json so they never enter
        the git-tracked / backed-up config. Missing / invalid file → no-op.
        """
        if self._cache is None:
            return
        try:
            raw = self._secret_path.read_text(encoding="utf-8").strip()
            if not raw:
                return
            data = json.loads(raw)
            if isinstance(data, dict):
                for k, v in data.items():
                    if k in SECRET_KEYS and v:
                        self._cache[k] = v
        except FileNotFoundError:
            return
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            logger.warning("Could not read secrets store (%s): %s", self._secret_path, exc)

    def set_secret(self, key: str, value: str) -> None:
        """Persist a single SECRET_KEYS value to the durable 0o600 secret store.

        Writes to the cache (so the current process sees it immediately, e.g.
        _configure_claude_environment at the next spawn) AND to secrets.json
        (so it survives a daemon restart). NEVER touches config.json.

        Raises ValueError if *key* is not a recognized secret key — this is the
        single sanctioned write path for secrets; arbitrary keys are rejected.
        """
        if key not in SECRET_KEYS:
            raise ValueError(f"{key!r} is not a secret key; use update() for non-secrets")
        if self._cache is None:
            self.load()
        assert self._cache is not None
        self._cache[key] = value
        self._secret_path.parent.mkdir(parents=True, exist_ok=True)
        # Serialize the read-modify-write across processes. The lock is held on a
        # PERSISTENT sibling lockfile (secrets.json.lock) — NOT on secrets.json
        # itself, because the RMW ends in os.replace() which swaps the inode, so a
        # lock on secrets.json's fd would guard the wrong (old) inode. The lockfile
        # is never replaced, so its inode is stable. Cross-platform via
        # utils.file_lock (fcntl on POSIX, msvcrt on Windows) — imported here (not
        # module-top) so app_config_manager stays importable even where the lock
        # primitive is unavailable, and to avoid a hard dependency at boot. Without
        # this, two concurrent set_secret calls each read the old dict, add their
        # own key, and replace → last writer wins, first key lost (classic
        # lost-update on the shared secret store).
        from utils.file_lock import flock_exclusive, flock_unlock
        import os as _os
        lock_path = str(self._secret_path) + ".lock"
        try:
            lock_fd = open(lock_path, "w")
        except OSError as e:
            # A non-writable app-data dir (misprovisioned Hive box, wrong perms)
            # would otherwise surface as a bare 500 on the setup-wizard call.
            # Re-raise with an actionable message instead. set_secret is only
            # invoked from request handlers (never at boot), so this cannot crash
            # the daemon.
            raise RuntimeError(
                f"Cannot open secret lockfile {lock_path!r} — the app data "
                f"directory is not writable ({e}). Check permissions on "
                f"{self._secret_path.parent}."
            ) from e
        try:
            flock_exclusive(lock_fd)
            # Read-modify-write the secret store, preserving other secrets.
            existing: dict[str, Any] = {}
            try:
                raw = self._secret_path.read_text(encoding="utf-8").strip()
                if raw:
                    loaded = json.loads(raw)
                    if isinstance(loaded, dict):
                        existing = loaded
            except (FileNotFoundError, json.JSONDecodeError, ValueError, OSError):
                existing = {}
            existing[key] = value
            # Atomic 0o600 write: write to a temp file created 0o600, then os.replace
            # onto the target (rename is atomic on the same filesystem). A crash can
            # never leave a truncated secrets.json → the persisted key is durable.
            tmp_path = str(self._secret_path) + ".tmp"
            fd = _os.open(tmp_path, _os.O_WRONLY | _os.O_CREAT | _os.O_TRUNC, 0o600)
            try:
                with _os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(json.dumps(existing, indent=2) + "\n")
                    fh.flush()
                    _os.fsync(fh.fileno())
                _os.replace(tmp_path, str(self._secret_path))
                _os.chmod(str(self._secret_path), 0o600)  # re-assert after replace
            finally:
                if _os.path.exists(tmp_path):
                    try:
                        _os.unlink(tmp_path)
                    except OSError:
                        pass
        finally:
            try:
                flock_unlock(lock_fd)
            except OSError:
                pass
            lock_fd.close()
        logger.info("Secret persisted: %s (secrets.json, 0o600)", key)

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
