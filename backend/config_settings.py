"""The pydantic ``Settings`` model behind ``config.settings`` (loaded on first use).

Split out of ``config.py`` so that importing ``config`` for its path helpers
(``get_app_data_dir``, ``get_log_file_path``) no longer imports pydantic_settings
(~100 ms). ``config`` re-exports every name here lazily — always import them from
``config``, never from this module, so there is exactly one ``settings`` instance.

Key symbols: ``Settings``, ``get_settings``, ``settings``.
"""
import secrets
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from config import _read_version


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Application
    app_name: str = "SwarmAI"
    app_version: str = _read_version()  # reads from VERSION file (single source of truth)
    debug: bool = False

    # Server
    host: str = "127.0.0.1"
    port: int = 8000

    # CORS - Tauri origins for the desktop app (production default).
    # Security (TT V2265734761 — Cataphract Critical, fix 4): dev-only browser
    # origins (Vite localhost:5173, CRA localhost:3000) are NOT in the production
    # default — a page served on those origins could otherwise issue cross-origin
    # PUT/POST to the daemon. They are re-added ONLY under settings.debug (see the
    # debug block in main.py). localhost:1420 is the Tauri devUrl and stays.
    cors_origins: list[str] = ["http://localhost:1420", "tauri://localhost", "https://tauri.localhost", "http://tauri.localhost"]

    # Database
    database_type: str = "sqlite"

    # SQLite configuration
    sqlite_db_path: str | None = None  # If None, uses default user data directory

    # AWS (credentials resolved via standard AWS credential chain, not stored here)
    # aws_region is in config.json via AppConfigManager

    # JWT Authentication
    jwt_secret_key: str = ""  # Set via JWT_SECRET_KEY env var; auto-generated if empty
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7

    # Hive app-layer auth (defense-in-depth INNER layer; Caddy basic_auth is the OUTER layer).
    # ONLY enforced when SWARMAI_MODE=hive (see middleware/hive_auth.py). Desktop/daemon/
    # subprocess trust the 127.0.0.1 bind and never enforce this. These MUST carry the SAME
    # credential Caddy validates (hive/Caddyfile @protected) — the provisioner exports
    # HIVE_USER / HIVE_PASS_HASH to both the Caddy .env and the backend service env.
    # hive_pass_hash is a bcrypt hash (produced by `caddy hash-password`), verified via
    # core.auth.verify_password. Empty hash in hive mode => middleware denies ALL (fail-closed).
    hive_user: str = ""  # Set via HIVE_USER env var
    hive_pass_hash: str = ""  # Set via HIVE_PASS_HASH env var (bcrypt hash)

    # Rate Limiting
    rate_limit_per_minute: int = 100

    # NOTE: The following settings have been moved to SwarmWS/config.json
    # (managed by AppConfigManager, single source of truth):
    #   - anthropic_api_key, anthropic_base_url, default_model
    #   - claude_code_use_bedrock
    #   - aws_region, available_models, bedrock_model_map
    #   - sandbox_enabled_default, sandbox_auto_allow_bash, sandbox_excluded_commands
    #   - sandbox_allow_unsandboxed, sandbox_additional_write_paths, sandbox_allowed_hosts

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    s = Settings()
    if not s.jwt_secret_key:
        s.jwt_secret_key = secrets.token_hex(32)
    return s


settings = get_settings()
