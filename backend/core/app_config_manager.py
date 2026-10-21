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
- **Preserve, never destroy**: A missing config file is created from
  ``DEFAULT_CONFIG``. A present-but-unreadable one (empty, invalid JSON, not a
  dict) — and likewise an unparseable ``secrets.json`` — is renamed to
  ``<name>.corrupt-<ts>`` and surfaced via the recovery marker BEFORE anything
  replaces it; if it cannot be preserved it is left untouched (STEERING #20).
- **Atomic writes**: config.json and secrets.json are written via a temp file
  + ``os.replace`` so a crash can never leave a truncated file.
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
    # Route desktop Bedrock sessions through the loopback thinking-display shim
    # (core/bedrock_thinking_gateway.py). Turn off once the CLI sends `display`.
    "bedrock_thinking_gateway": True,
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
        # Confidence thresholds are NOT config: they live only in
        # evolution_optimizer.HIGH_CONFIDENCE / MED_CONFIDENCE (data-calibrated).
    },
    # Pipeline-run worktrees (docs/design/pipeline-run-worktree.md §9, §16.9): "on"
    # (the default) gives each new pipeline run its own git worktree per repo it writes;
    # only an explicit "off" turns it off. Read straight from config.json by
    # core.run_worktree.mode_enabled (no restart).
    "pipeline": {"worktree_mode": "on"},
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
        # Where a corruption-recovery marker goes. The default config lives in the
        # app data dir, which attention_authority scans; a custom path (tests,
        # tooling) keeps its marker next to itself so it never writes into the
        # live app dir.
        self._marker_dir: Path = (
            get_app_data_dir() if config_path is None else self._config_path.parent
        )
        self._cache: dict[str, Any] | None = None
        # True while config.json is unreadable AND could not be isolated: every
        # write must re-attempt preservation first, or the first update() after
        # a failed isolation would overwrite the very file load() refused to touch.
        self._unpreserved_corrupt: bool = False
        self._loaded_mtime: "float | None" = None

    # -- public API --------------------------------------------------------

    def load(self) -> dict[str, Any]:
        """Load config from file into the in-memory cache.

        Called once at startup.  Migrates from the legacy location
        (``~/.swarm-ai/config.json``) if the new path doesn't exist yet.
        If the file is missing the cache is populated with ``DEFAULT_CONFIG``
        and written to disk. If it is present but unreadable as a JSON object
        (empty, invalid JSON, not a dict) it is first ISOLATED — renamed to
        ``config.json.corrupt-<ts>`` and surfaced via the recovery marker — and
        only then replaced with defaults; if it cannot be isolated it is left
        untouched and the process runs on in-memory defaults (STEERING #20:
        never overwrite a user store you could not preserve).

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

        self._unpreserved_corrupt = False
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
            self._cache = dict(DEFAULT_CONFIG)
            if self._isolate_corrupt(self._config_path, exc):
                logger.error(
                    "Invalid config at %s (%s) — original isolated, using defaults",
                    self._config_path, _describe_parse_error(exc),
                )
                self._write_to_disk()
            else:
                self._unpreserved_corrupt = True
                logger.error(
                    "Invalid config at %s (%s) could NOT be isolated — left untouched; "
                    "running on in-memory defaults (nothing written)",
                    self._config_path, _describe_parse_error(exc),
                )
        self._loaded_mtime = self._file_mtime()  # incl. a defaults file written above
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

        Writes to secrets.json (so it survives a daemon restart) and then to the
        cache (so the current process sees it immediately, e.g.
        _configure_claude_environment at the next spawn). NEVER touches config.json.

        Raises ValueError if *key* is not a recognized secret key — this is the
        single sanctioned write path for secrets; arbitrary keys are rejected.
        Raises RuntimeError if secrets.json exists but cannot be read, or cannot
        be parsed AND cannot be preserved — writing would destroy the other keys.
        """
        if key not in SECRET_KEYS:
            raise ValueError(f"{key!r} is not a secret key; use update() for non-secrets")
        if self._cache is None:
            self.load()
        assert self._cache is not None
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
            # A missing store starts empty. A store that exists but does not
            # parse as a JSON object still holds the user's OTHER keys, so it is
            # isolated (preserved) before being replaced — never silently reset
            # to {}. A store that cannot be READ at all (permissions, I/O) is not
            # evidence of corruption: refuse, and write nothing.
            existing: dict[str, Any] = {}
            try:
                raw: str | None = self._secret_path.read_text(encoding="utf-8").strip()
            except FileNotFoundError:
                raw = ""
            except UnicodeDecodeError:
                raw = None  # undecodable bytes = unparseable content, not an I/O failure
            except OSError as e:
                raise RuntimeError(
                    f"Cannot read secret store {self._secret_path} ({type(e).__name__}) "
                    f"— refusing to overwrite it."
                ) from e
            if raw is None or raw:
                try:
                    if raw is None:
                        raise ValueError("not valid UTF-8")
                    loaded = json.loads(raw)
                    if not isinstance(loaded, dict):
                        raise ValueError("root is not a JSON object")
                    existing = loaded
                except ValueError as parse_exc:  # JSONDecodeError is a ValueError
                    if not self._isolate_corrupt(self._secret_path, parse_exc):
                        raise RuntimeError(
                            f"Secret store {self._secret_path} is unreadable and could "
                            f"not be preserved — refusing to overwrite it."
                        ) from parse_exc
            existing[key] = value
            # Atomic 0o600 write — a crash can never leave a truncated secrets.json.
            _atomic_write_text(self._secret_path, json.dumps(existing, indent=2) + "\n")
        finally:
            try:
                flock_unlock(lock_fd)
            except OSError:
                pass
            lock_fd.close()
        # Only after the durable write succeeded — a refused write must not leave
        # the process using a secret that would vanish on restart.
        self._cache[key] = value
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
        the dict is written to the config file. The cache only changes once
        the write has succeeded, so a refused write never leaves the process
        running on settings that are not on disk.

        Raises:
            RuntimeError: config.json was found unreadable at load(), could not
                be preserved then, and still cannot be — writing would destroy
                it. (Re-checked here first: a file fixed by hand since load(),
                or one that can now be isolated, unblocks the write.)
        """
        if self._cache is None or self._unpreserved_corrupt:
            self.load()  # re-reads a hand-fixed file / retries isolation
        elif self._file_mtime() != self._loaded_mtime:
            # config.json changed since it was read (a hand edit while the daemon runs):
            # merge onto what is on disk, never write the stale cache over it. A file that
            # no longer parses is refused (retryable): never isolate-and-reset here.
            try:
                disk = json.loads(self._config_path.read_text(encoding="utf-8"))
                if not isinstance(disk, dict):
                    raise ValueError("root is not a JSON object")
            except (OSError, ValueError) as exc:
                raise RuntimeError(f"{self._config_path} was edited and no longer parses "
                                   f"({exc}); fix it, then retry") from exc
            secrets = {k: v for k, v in self._cache.items() if k in SECRET_KEYS}
            self._cache = {**DEFAULT_CONFIG, **disk, **secrets}
        if self._unpreserved_corrupt:
            raise RuntimeError(
                f"{self._config_path} is unreadable and could not be preserved — "
                f"refusing to overwrite it. Fix or move the file, then retry."
            )
        assert self._cache is not None
        candidate = {**self._cache, **updates}
        self._write_to_disk(candidate)
        self._cache = candidate
        self._loaded_mtime = self._file_mtime()
        logger.info("Config updated: %s", list(updates.keys()))

    def reload(self) -> None:
        """Force re-read from the config file (e.g. after manual edits)."""
        self._cache = None
        self.load()

    # -- private helpers ----------------------------------------------------

    def _file_mtime(self) -> "float | None":
        try:
            return self._config_path.stat().st_mtime_ns / 1e9
        except OSError:
            return None

    def _write_to_disk(self, data: dict[str, Any] | None = None) -> None:
        """Persist *data* (default: the current cache) to disk, stripping secret keys."""
        data = self._cache if data is None else data
        if data is None:
            return
        if self._unpreserved_corrupt:
            # Defence in depth: update() already refuses in this state; no write
            # path may replace a file that load() could not preserve.
            raise RuntimeError(
                f"{self._config_path} is unreadable and could not be preserved — "
                f"refusing to overwrite it."
            )

        clean = {k: v for k, v in data.items() if k not in SECRET_KEYS}

        self._config_path.parent.mkdir(parents=True, exist_ok=True)

        # Atomic (temp + fsync + os.replace): a crash mid-write must never leave
        # a truncated config.json — that empty/partial file is exactly what
        # load() would then have to treat as corrupt.
        _atomic_write_text(
            self._config_path, json.dumps(clean, indent=2, default=str) + "\n"
        )

    def _isolate_corrupt(self, path: Path, exc: Exception) -> bool:
        """PRESERVE an unreadable store before anything is written in its place.

        Renames ``path`` to ``<name>.corrupt-<ts>`` (``data_safety.isolate_store``,
        never a delete) and records a recovery marker so the recover-vs-discard
        decision is surfaced to the user (``attention_authority``). Returns False
        if the store could NOT be preserved — the caller must then leave it alone.
        The marker reason carries only the error type/position, never file
        content (it is shown in chat; this may be the secret store).
        """
        from core.data_safety import IsolationError, isolate_store, write_recovery_marker

        try:
            isolated = isolate_store(path)
        except IsolationError as iso_exc:
            logger.error("Could not isolate unreadable %s: %s", path, iso_exc)
            # Still tell the user: this is the case they most need to know about
            # (writes to the file are refused until it is fixed or moved). The
            # marker points at the ORIGINAL path, which is still in place. One entry
            # per file: every retry (each update()/set_secret/boot) fails the same
            # way, and the marker only prunes entries whose path disappeared.
            try:
                from core.data_safety import read_recovery_marker

                pending = (read_recovery_marker(self._marker_dir) or {}).get("pending") or []
                if any(Path(e.get("isolated_path", "")) == path for e in pending):
                    return False
                write_recovery_marker(
                    self._marker_dir,
                    isolated_path=path,
                    reason=(
                        f"{path.name} could not be parsed ({_describe_parse_error(exc)}) "
                        f"and could NOT be moved aside ({type(iso_exc).__name__}). It has "
                        f"been left untouched; changes to it are refused until it is "
                        f"fixed or moved."
                    ),
                )
            except Exception as mk_exc:  # noqa: BLE001 — surfacing is best-effort
                logger.error("Failed to write recovery marker for %s: %s", path, mk_exc)
            return False
        try:
            write_recovery_marker(
                self._marker_dir,
                isolated_path=isolated,
                reason=(
                    f"{path.name} could not be parsed ({_describe_parse_error(exc)}) and "
                    f"was isolated instead of overwritten. A fresh {path.name} is now in "
                    f"use; your previous file is preserved."
                ),
            )
        except Exception as mk_exc:  # noqa: BLE001 — surfacing is best-effort; data already preserved
            logger.error("Failed to write recovery marker for %s: %s", isolated, mk_exc)
        return True


def _describe_parse_error(exc: Exception) -> str:
    """Error type + position only — never the parsed document's content."""
    if isinstance(exc, json.JSONDecodeError):
        return f"JSONDecodeError: {exc.msg} at line {exc.lineno} column {exc.colno}"
    return f"{type(exc).__name__}: {exc}" if isinstance(exc, ValueError) else type(exc).__name__


def _atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically with 0o600 permissions.

    A uniquely-named temp file in the same directory (``mkstemp`` creates it
    0o600) is written, fsync'd and ``os.replace``d onto ``path``. A unique name —
    not a fixed ``<file>.tmp`` — because several AppConfigManager instances can
    write in one process without a shared lock, and a fixed name would let one
    writer's cleanup delete another's temp file mid-write.
    """
    import tempfile

    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # re-assert after replace
    except OSError as exc:
        logger.debug("Could not set file permissions: %s", exc)
