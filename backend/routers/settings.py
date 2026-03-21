"""Settings API — generic dict pass-through backed by AppConfigManager.

GET /api/settings returns the full config dict (minus secrets) with
credential status fields injected.  PUT /api/settings accepts any subset
of known config keys and merges them into the config.  No per-field
Pydantic models — DEFAULT_CONFIG is the single source of truth.

Public symbols:

- ``router``                — FastAPI ``APIRouter`` mounted at ``/api/settings``.
- ``get_config_manager``    — Returns the module-level ``AppConfigManager`` instance.
- ``set_config_manager``    — Replaces the module-level instance (for testing / DI).
"""

import json
import logging
import os
from fastapi import APIRouter, HTTPException, Request

from config import get_app_data_dir
from core.app_config_manager import AppConfigManager, DEFAULT_CONFIG, SECRET_KEYS

logger = logging.getLogger(__name__)

router = APIRouter()

# Keys accepted from PUT requests — only DEFAULT_CONFIG keys minus secrets.
WRITABLE_KEYS: frozenset[str] = frozenset(DEFAULT_CONFIG.keys()) - SECRET_KEYS

# Expected types for known config keys — derived from DEFAULT_CONFIG values.
# Used for lightweight type validation on PUT. Keys with None defaults accept any type.
_EXPECTED_TYPES: dict[str, type | None] = {
    k: type(v) if v is not None else None
    for k, v in DEFAULT_CONFIG.items()
    if k not in SECRET_KEYS
}


# ---------------------------------------------------------------------------
# Module-level AppConfigManager instance (set at startup or via DI)
# ---------------------------------------------------------------------------

_config_manager: AppConfigManager | None = None


def get_config_manager() -> AppConfigManager:
    """Return the active ``AppConfigManager`` instance."""
    global _config_manager
    if _config_manager is None:
        _config_manager = AppConfigManager.instance()
    return _config_manager


def set_config_manager(manager: AppConfigManager) -> None:
    """Replace the module-level ``AppConfigManager`` (for startup wiring / tests)."""
    global _config_manager
    _config_manager = manager


# ---------------------------------------------------------------------------
# Credential probing helpers
# ---------------------------------------------------------------------------


def _probe_aws_credentials() -> bool:
    """Check if AWS credentials are available via the credential chain."""
    try:
        import boto3
        session = boto3.Session()
        creds = session.get_credentials()
        return creds is not None
    except Exception:
        return False


def _probe_anthropic_api_key() -> bool:
    """Check if ``ANTHROPIC_API_KEY`` env var is set and non-empty."""
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


# ---------------------------------------------------------------------------
# Generic response builder
# ---------------------------------------------------------------------------


def _build_config_response(cfg: AppConfigManager) -> dict:
    """Build a plain dict response from the config cache.

    Iterates DEFAULT_CONFIG keys (public API only, no _cache access),
    filters SECRET_KEYS, and injects credential status fields.
    """
    clean = {
        k: cfg.get(k, v)
        for k, v in DEFAULT_CONFIG.items()
        if k not in SECRET_KEYS
    }
    clean["aws_credentials_configured"] = _probe_aws_credentials()
    clean["anthropic_api_key_configured"] = _probe_anthropic_api_key()
    return clean


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("")
async def get_app_configuration():
    """Get current application configuration as a plain dict."""
    cfg = get_config_manager()
    return _build_config_response(cfg)


@router.put("")
async def update_app_configuration(request: Request):
    """Update application configuration (partial update, generic dict).

    Accepts any JSON object. Only keys present in DEFAULT_CONFIG (minus
    secrets) are accepted — unknown keys are silently discarded.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=422, detail="Request body must be valid JSON")
    cfg = get_config_manager()

    # Whitelist to known config keys only
    updates = {k: v for k, v in body.items() if k in WRITABLE_KEYS}

    # Lightweight type validation — reject values that don't match DEFAULT_CONFIG types
    for k, v in list(updates.items()):
        expected = _EXPECTED_TYPES.get(k)
        if expected is not None and v is not None and not isinstance(v, expected):
            raise HTTPException(
                status_code=422,
                detail=f"Invalid type for '{k}': expected {expected.__name__}, got {type(v).__name__}",
            )

    # anthropic_base_url empty-string → None
    if updates.get("anthropic_base_url") == "":
        updates["anthropic_base_url"] = None

    # Compute effective state BEFORE persisting
    effective_available = updates.get(
        "available_models",
        cfg.get("available_models", DEFAULT_CONFIG["available_models"]),
    )

    # Validation: default_model must be in available_models
    if "default_model" in updates and effective_available:
        if updates["default_model"] not in effective_available:
            raise HTTPException(
                status_code=400,
                detail="default_model must be in available_models",
            )

    # Auto-reset default_model when available_models changed
    if "available_models" in updates and "default_model" not in updates:
        current_default = cfg.get("default_model", DEFAULT_CONFIG["default_model"])
        new_models = updates["available_models"]
        if new_models and current_default not in new_models:
            updates["default_model"] = new_models[0]

    # Single atomic update — validated state only
    if updates:
        cfg.update(updates)

    return _build_config_response(cfg)


# ---------------------------------------------------------------------------
# Open Tabs persistence (filesystem-first: ~/.swarm-ai/open_tabs.json)
# ---------------------------------------------------------------------------

_OPEN_TABS_FILE = "open_tabs.json"


def _get_open_tabs_path():
    """Return the path to ``~/.swarm-ai/open_tabs.json``."""
    return get_app_data_dir() / _OPEN_TABS_FILE


@router.get("/open-tabs")
async def get_open_tabs():
    """Read persisted open-tab state from the filesystem."""
    path = _get_open_tabs_path()
    try:
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data
    except Exception as exc:
        logger.warning("Failed to read open_tabs.json: %s", exc)
        return None


@router.put("/open-tabs")
async def save_open_tabs(request: dict):
    """Write open-tab state to the filesystem."""
    if "tabs" not in request or not isinstance(request.get("tabs"), list):
        raise HTTPException(status_code=422, detail="'tabs' array is required")

    path = _get_open_tabs_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(request, indent=2), encoding="utf-8")
        return {"status": "ok"}
    except Exception as exc:
        logger.error("Failed to write open_tabs.json: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to persist open tabs: {exc}")
