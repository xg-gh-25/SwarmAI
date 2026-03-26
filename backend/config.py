"""Application configuration settings."""
import platform
import secrets
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache

# Calculate project root directory (backend's parent directory)
_BACKEND_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _BACKEND_DIR.parent


def get_app_data_dir() -> Path:
    """Get the application data directory.

    Returns:
        All platforms: ~/.swarm-ai/
        
    Uses a consistent hidden directory in the user's home folder across
    all platforms for simplicity and easy access.
    """
    return Path.home() / ".swarm-ai"

# Default model ID mapping: Anthropic API model ID -> AWS Bedrock cross-region inference profile
# Used when CLAUDE_CODE_USE_BEDROCK=true and no override exists in config.json
# Format: us.anthropic.<model>-v1 (cross-region inference profile)
# See: https://docs.anthropic.com/en/docs/claude-code/model-config
ANTHROPIC_TO_BEDROCK_MODEL_MAP: dict[str, str] = {
    "claude-opus-4-6": "us.anthropic.claude-opus-4-6-v1",
    "claude-sonnet-4-6": "us.anthropic.claude-sonnet-4-6",
}


def get_bedrock_model_id(anthropic_model_id: str, config_map: dict[str, str] | None = None) -> str:
    """Convert Anthropic model ID to AWS Bedrock model ID.

    Checks ``config_map`` (from config.json ``bedrock_model_map``) first,
    then falls back to the hardcoded ``ANTHROPIC_TO_BEDROCK_MODEL_MAP``.
    Unknown model IDs pass through unchanged (allows custom ARNs).

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
    app_version: str = "1.0.0"
    debug: bool = False

    # Server
    host: str = "127.0.0.1"
    port: int = 8000

    # CORS - include Tauri origins for desktop app
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000", "http://localhost:1420", "tauri://localhost", "https://tauri.localhost", "http://tauri.localhost"]

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
