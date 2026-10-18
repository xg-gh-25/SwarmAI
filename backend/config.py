"""Application configuration: data/log paths, version, Bedrock model map, settings.

Key symbols: ``get_app_data_dir``, ``get_log_file_path``, ``get_bedrock_model_id``,
``ANTHROPIC_TO_BEDROCK_MODEL_MAP``, and — loaded lazily from ``config_settings`` on
first access — ``Settings``, ``get_settings``, ``settings``.
"""
import os
from pathlib import Path
from typing import TYPE_CHECKING

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


# ``Settings`` / ``get_settings`` / ``settings`` live in ``config_settings`` and load on
# first access (PEP 562): the path helpers above are imported by every artifact_cli call
# and must not pull in pydantic_settings (~100 ms per process). ``from config import
# settings`` and ``config.settings`` behave exactly as before.
_LAZY_SETTINGS = ("Settings", "get_settings", "settings")

if TYPE_CHECKING:  # static names for type checkers and PyInstaller's import scan
    from config_settings import Settings, get_settings, settings


def __getattr__(name: str):
    if name not in _LAZY_SETTINGS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import config_settings
    for n in _LAZY_SETTINGS:
        globals()[n] = getattr(config_settings, n)
    return globals()[name]
