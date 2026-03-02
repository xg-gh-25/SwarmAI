"""Settings API endpoints backed by file-based AppConfigManager.

This module was refactored from a DB-backed settings store to use
``AppConfigManager`` (``~/.swarm-ai/config.json``) for all non-secret
application configuration.  Credential fields have been removed from
request/response models — AWS credentials are resolved via the standard
AWS credential chain.

Public symbols:

- ``router``                — FastAPI ``APIRouter`` mounted at ``/api/settings``.
- ``get_app_configuration`` — GET handler returning ``AppConfigResponse``.
- ``update_app_configuration`` — PUT handler accepting ``AppConfigRequest``.
- ``get_config_manager``    — Returns the module-level ``AppConfigManager`` instance.
- ``set_config_manager``    — Replaces the module-level instance (for testing / DI).
- ``_probe_aws_credentials`` — Check if AWS credentials are available via the credential chain.
- ``_probe_anthropic_api_key`` — Check if ``ANTHROPIC_API_KEY`` env var is set.
- ``get_open_tabs``         — GET handler returning open tab state from ``~/.swarm-ai/open_tabs.json``.
- ``save_open_tabs``        — PUT handler writing open tab state to ``~/.swarm-ai/open_tabs.json``.
"""

import json
import logging
import os
from fastapi import APIRouter, HTTPException

from config import get_app_data_dir
from schemas.settings import AppConfigRequest, AppConfigResponse
from core.app_config_manager import AppConfigManager, DEFAULT_CONFIG

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Module-level AppConfigManager instance (set at startup or via DI)
# ---------------------------------------------------------------------------

_config_manager: AppConfigManager | None = None


def get_config_manager() -> AppConfigManager:
    """Return the active ``AppConfigManager`` instance.

    If none has been set via ``set_config_manager()``, a default instance
    is created and loaded automatically.
    """
    global _config_manager
    if _config_manager is None:
        _config_manager = AppConfigManager()
        _config_manager.load()
    return _config_manager


def set_config_manager(manager: AppConfigManager) -> None:
    """Replace the module-level ``AppConfigManager`` (for startup wiring / tests)."""
    global _config_manager
    _config_manager = manager


# ---------------------------------------------------------------------------
# Credential probing helpers
# ---------------------------------------------------------------------------


def _probe_aws_credentials() -> bool:
    """Check if AWS credentials are available via the credential chain.

    Uses ``boto3.Session().get_credentials()`` to probe the standard AWS
    credential resolution order (env vars, ``~/.aws/credentials``,
    ``~/.ada/credentials``, config profiles, instance metadata) without
    exposing actual credential values.
    """
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
# Helpers
# ---------------------------------------------------------------------------


def _build_response(cfg: AppConfigManager) -> AppConfigResponse:
    """Build an ``AppConfigResponse`` from the current in-memory config cache.

    Credential status fields are computed at call time by probing the AWS
    credential chain and checking the ``ANTHROPIC_API_KEY`` env var.  No
    actual credential values are exposed in the response.
    """
    return AppConfigResponse(
        use_bedrock=cfg.get("use_bedrock", DEFAULT_CONFIG["use_bedrock"]),
        aws_region=cfg.get("aws_region", DEFAULT_CONFIG["aws_region"]),
        anthropic_base_url=cfg.get("anthropic_base_url"),
        available_models=cfg.get("available_models", DEFAULT_CONFIG["available_models"]),
        default_model=cfg.get("default_model", DEFAULT_CONFIG["default_model"]),
        claude_code_disable_experimental_betas=cfg.get(
            "claude_code_disable_experimental_betas",
            DEFAULT_CONFIG["claude_code_disable_experimental_betas"],
        ),
        aws_credentials_configured=_probe_aws_credentials(),
        anthropic_api_key_configured=_probe_anthropic_api_key(),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=AppConfigResponse)
async def get_app_configuration():
    """Get current application configuration.

    Returns all non-secret config fields from the in-memory cache.
    Credential status fields are computed at call time by probing the
    AWS credential chain and the ``ANTHROPIC_API_KEY`` env var.
    """
    cfg = get_config_manager()
    return _build_response(cfg)


@router.put("", response_model=AppConfigResponse)
async def update_app_configuration(request: AppConfigRequest):
    """Update application configuration (partial update semantics).

    Only fields that are provided (not ``None``) are merged into the
    existing configuration.  Validation rules:

    - ``default_model`` must be in ``available_models`` when both are
      provided in the same request.
    - When ``available_models`` is updated and the current
      ``default_model`` is not in the new list, ``default_model`` is
      auto-reset to the first model in the new list.
    - Empty string for ``anthropic_base_url`` clears the value (→ None).
    """
    cfg = get_config_manager()
    updates: dict = {}

    # --- Collect provided fields into updates dict ---

    if request.use_bedrock is not None:
        updates["use_bedrock"] = request.use_bedrock

    if request.aws_region is not None:
        updates["aws_region"] = request.aws_region

    if request.anthropic_base_url is not None:
        # Empty string clears the value
        updates["anthropic_base_url"] = (
            request.anthropic_base_url if request.anthropic_base_url else None
        )

    if request.claude_code_disable_experimental_betas is not None:
        updates["claude_code_disable_experimental_betas"] = (
            request.claude_code_disable_experimental_betas
        )

    if request.available_models is not None:
        updates["available_models"] = request.available_models

    if request.default_model is not None:
        updates["default_model"] = request.default_model

    # --- Validation: default_model must be in available_models ---

    # Determine the effective available_models after this update
    effective_available = updates.get(
        "available_models",
        cfg.get("available_models", DEFAULT_CONFIG["available_models"]),
    )

    if "default_model" in updates and effective_available:
        if updates["default_model"] not in effective_available:
            raise HTTPException(
                status_code=400,
                detail="default_model must be in available_models",
            )

    # --- Apply updates ---
    if updates:
        cfg.update(updates)

    # --- Auto-reset default_model when available_models changed ---

    if "available_models" in updates:
        current_default = cfg.get("default_model", DEFAULT_CONFIG["default_model"])
        new_models = updates["available_models"]
        if new_models and current_default not in new_models:
            cfg.update({"default_model": new_models[0]})
            logger.info("Auto-reset default_model to %s", new_models[0])

    logger.info(
        "App configuration updated: use_bedrock=%s, region=%s",
        cfg.get("use_bedrock"),
        cfg.get("aws_region"),
    )

    return _build_response(cfg)


# ---------------------------------------------------------------------------
# Open Tabs persistence (filesystem-first: ~/.swarm-ai/open_tabs.json)
# ---------------------------------------------------------------------------

_OPEN_TABS_FILE = "open_tabs.json"


def _get_open_tabs_path():
    """Return the path to ``~/.swarm-ai/open_tabs.json``."""
    return get_app_data_dir() / _OPEN_TABS_FILE


@router.get("/open-tabs")
async def get_open_tabs():
    """Read persisted open-tab state from the filesystem.

    Returns the contents of ``~/.swarm-ai/open_tabs.json`` as-is.
    If the file does not exist or is unreadable, returns ``null``
    so the frontend can fall back to a fresh default tab.
    """
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
    """Write open-tab state to the filesystem.

    Accepts a JSON object with ``tabs`` (array of serializable tab
    objects) and ``activeTabId`` (string or null).  Writes to
    ``~/.swarm-ai/open_tabs.json``.
    """
    # Basic shape validation
    if "tabs" not in request or not isinstance(request.get("tabs"), list):
        raise HTTPException(status_code=422, detail="'tabs' array is required")

    path = _get_open_tabs_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(request, indent=2),
            encoding="utf-8",
        )
        return {"status": "ok"}
    except Exception as exc:
        logger.error("Failed to write open_tabs.json: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to persist open tabs: {exc}",
        )
