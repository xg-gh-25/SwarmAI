"""Application configuration settings."""
from pathlib import Path
from pydantic_settings import BaseSettings
from functools import lru_cache

# Calculate project root directory (backend's parent directory)
_BACKEND_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _BACKEND_DIR.parent

# Model ID mapping: Anthropic API model ID -> AWS Bedrock model ID
# Used when CLAUDE_CODE_USE_BEDROCK=true
ANTHROPIC_TO_BEDROCK_MODEL_MAP: dict[str, str] = {
    # Claude 4.5 models
    "claude-haiku-4-5-20251001": "global.anthropic.claude-haiku-4-5-20251001-v1:0",
    "claude-sonnet-4-5-20250929": "global.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "claude-opus-4-5-20251101": "global.anthropic.claude-opus-4-5-20251101-v1:0",
}


def get_bedrock_model_id(anthropic_model_id: str) -> str:
    """Convert Anthropic model ID to AWS Bedrock model ID.

    Args:
        anthropic_model_id: The Anthropic API model identifier

    Returns:
        The corresponding AWS Bedrock model identifier, or the original ID if no mapping exists
    """
    return ANTHROPIC_TO_BEDROCK_MODEL_MAP.get(anthropic_model_id, anthropic_model_id)


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Application
    app_name: str = "Agent Platform API"
    app_version: str = "4.0.0"
    debug: bool = False

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    # CORS - include Tauri origins for desktop app
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000", "http://localhost:1420", "tauri://localhost", "https://tauri.localhost", "http://tauri.localhost"]

    # Database
    database_type: str = "sqlite"

    # SQLite configuration
    sqlite_db_path: str | None = None  # If None, uses default user data directory

    # AWS (for Bedrock model access)
    aws_region: str = "us-west-2"
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""

    # JWT Authentication
    jwt_secret_key: str = "your-secret-key-change-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7

    # Rate Limiting
    rate_limit_per_minute: int = 100

    # Claude Agent SDK / Anthropic API Configuration
    anthropic_api_key: str = ""
    anthropic_base_url: str | None = None  # Custom API endpoint (optional)
    default_model: str = "claude-sonnet-4-5-20250929"

    # Claude Code Configuration
    claude_code_use_bedrock: bool = True  # Use AWS Bedrock instead of Anthropic API
    claude_code_disable_experimental_betas: bool = True  # Disable experimental features

    # Agent workspace directory (default: ./workspace relative to project root)
    # This is where main skills are stored in .claude/skills/
    agent_workspace_dir: str = str(_PROJECT_ROOT / "workspace")

    # Isolated per-agent workspaces directory (OUTSIDE project tree for skill isolation)
    # Each agent gets its own workspace with absolute symlinks to allowed skills
    # This prevents agents from discovering skills in parent directories
    # Default: /tmp/agent-platform-workspaces (can be changed to persistent location)
    agent_workspaces_dir: str = "/tmp/agent-platform-workspaces"

    # Built-in Sandbox Configuration (Claude Agent SDK native bash sandboxing)
    sandbox_enabled_default: bool = True  # Default sandbox state for new agents (enabled for security)
    sandbox_auto_allow_bash: bool = True  # Auto-approve bash when sandboxed
    sandbox_excluded_commands: str = "docker"  # Comma-separated commands to bypass sandbox (e.g., "git,docker")
    sandbox_allow_unsandboxed: bool = False  # Allow model to bypass sandbox

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


settings = get_settings()
