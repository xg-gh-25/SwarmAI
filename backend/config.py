"""Application configuration settings."""
import os
import secrets
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache

from model_registry import MODEL_REGISTRY

# Calculate project root directory (backend's parent directory)
_BACKEND_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _BACKEND_DIR.parent


def _read_version() -> str:
    """Read version from VERSION file (single source of truth).

    Search order:
    1. Project root (dev: backend/../VERSION)
    2. PyInstaller bundle root (prod: sys._MEIPASS/VERSION)
    3. Fallback "0.0.0-dev" — clearly wrong so drift is loud, not silent
    """
    import sys

    # Dev: VERSION at project root (backend/../)
    version_file = _PROJECT_ROOT / "VERSION"
    if version_file.exists():
        v = version_file.read_text().strip()
        if v:
            return v

    # Prod (PyInstaller): VERSION bundled at _MEIPASS root
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bundled = Path(meipass) / "VERSION"
        if bundled.exists():
            v = bundled.read_text().strip()
            if v:
                return v

    return "0.0.0-dev"


def get_app_data_dir() -> Path:
    """Get the application data directory.

    Returns:
        SWARM_DATA_DIR (if set + non-empty) → Path(that value);
        otherwise all platforms: ~/.swarm-ai/

    The SWARM_DATA_DIR env var is an ESCAPE HATCH so a test/sandbox/CI process can
    point at a scratch dir instead of the live production store. Without it, ANY app
    code run outside the daemon defaulted to ~/.swarm-ai/data.db and created it on
    miss — the upstream mechanism class behind the 8-12 data.db loss (SWARM_DATA_DIR
    lets `conftest` pin every test off the production path; see STEERING #20 / #1).

    UNSET (or empty) is byte-identical to the pre-override behavior, so all existing
    callers see zero change. Uses a consistent hidden directory in the user's home
    folder across all platforms for simplicity and easy access.
    """
    override = os.environ.get("SWARM_DATA_DIR")
    if override:
        return Path(override)
    return Path.home() / ".swarm-ai"


def get_log_file_path() -> Path:
    """Get the daemon log file path — the single source of truth for the log
    filename across all consumers (main.py's RotatingFileHandler AND any job
    handler that needs to read the live log).

    Daemon writes to backend-daemon.log; dev.sh redirects to backend.log, so
    multiple processes never share a file.

    Lives in config.py (a dependency-free leaf module) on purpose: importing it
    from main.py would drag in the entire FastAPI app + a duplicate
    RotatingFileHandler on the live log file (import blast radius). Consumers
    import THIS, not main.
    """
    import os
    log_dir = get_app_data_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    mode = os.environ.get("SWARMAI_MODE", "daemon")
    if mode == "daemon":
        return log_dir / "backend-daemon.log"
    return log_dir / "backend.log"

# Default model ID mapping: Anthropic API model ID -> AWS Bedrock cross-region
# inference profile. Used when CLAUDE_CODE_USE_BEDROCK=true and no override
# exists in config.json.
#
# DERIVED from the single authority (``model_registry.MODEL_REGISTRY``) — do NOT
# re-add a literal table here. Five independent copies of this table drifted
# behind the live flagship model; the registry is now the one place a new model
# is added. The name is kept because it is a live contract: ``routers/agents.py``
# returns ``list(ANTHROPIC_TO_BEDROCK_MODEL_MAP.keys())`` from ``GET /models``,
# so this must stay a dict.
#
# A COPY, not an alias — this name is reachable from a router, and a caller
# mutating it would otherwise corrupt the authority process-wide. Every other
# derivation copies too (``default_bedrock_model_map``).
ANTHROPIC_TO_BEDROCK_MODEL_MAP: dict[str, str] = dict(MODEL_REGISTRY)


def get_bedrock_model_id(anthropic_model_id: str, config_map: dict[str, str] | None = None) -> str:
    """Convert Anthropic model ID to AWS Bedrock model ID.

    Checks ``config_map`` (from config.json ``bedrock_model_map``) first,
    then falls back to ``ANTHROPIC_TO_BEDROCK_MODEL_MAP`` (the registry).
    Unknown model IDs pass through unchanged (allows custom ARNs) — note this
    means an id absent from BOTH maps reaches Bedrock verbatim, which is only
    valid for a real ARN. Keeping the registry current is what prevents a
    legitimate short name from taking that path.

    Args:
        anthropic_model_id: The Anthropic API model identifier
        config_map: Optional override map from config.json (checked first)

    Returns:
        The corresponding AWS Bedrock model identifier, or the original ID if no mapping exists
    """
    if config_map and anthropic_model_id in config_map:
        return config_map[anthropic_model_id]
    return ANTHROPIC_TO_BEDROCK_MODEL_MAP.get(anthropic_model_id, anthropic_model_id)


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
