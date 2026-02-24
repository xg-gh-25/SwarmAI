"""Settings API endpoints."""
import logging
from datetime import datetime
from fastapi import APIRouter, HTTPException

from schemas.settings import APIConfigurationRequest, APIConfigurationResponse
from config import settings as app_config, ANTHROPIC_TO_BEDROCK_MODEL_MAP
from database import db

logger = logging.getLogger(__name__)

router = APIRouter()

DEFAULT_SETTINGS_ID = "default"


@router.get("", response_model=APIConfigurationResponse)
async def get_api_configuration():
    """Get current API configuration.

    Returns configuration with masked sensitive values.
    """
    settings = await db.app_settings.get(DEFAULT_SETTINGS_ID)

    # Default models from config
    default_models = list(ANTHROPIC_TO_BEDROCK_MODEL_MAP.keys())

    if not settings:
        # Return default settings if none exist
        return APIConfigurationResponse(
            anthropic_api_key_set=False,
            anthropic_base_url=None,
            use_bedrock=False,
            bedrock_auth_type="credentials",
            aws_access_key_id_set=False,
            aws_bearer_token_set=False,
            aws_region="us-east-1",
            available_models=default_models,
            default_model=app_config.default_model,
        )

    # Get available_models from database, or use config defaults if empty
    available_models = settings.get("available_models")
    if not available_models:
        available_models = default_models

    return APIConfigurationResponse(
        anthropic_api_key_set=bool(settings.get("anthropic_api_key")),
        anthropic_base_url=settings.get("anthropic_base_url"),
        use_bedrock=bool(settings.get("use_bedrock")),
        bedrock_auth_type=settings.get("bedrock_auth_type", "credentials"),
        aws_access_key_id_set=bool(settings.get("aws_access_key_id")),
        aws_bearer_token_set=bool(settings.get("aws_bearer_token")),
        aws_region=settings.get("aws_region", "us-east-1"),
        available_models=available_models,
        default_model=settings.get("default_model", app_config.default_model),
    )


@router.put("", response_model=APIConfigurationResponse)
async def update_api_configuration(request: APIConfigurationRequest):
    """Update API configuration.

    Only updates fields that are provided (not None).
    """
    # Get existing settings or create new
    settings = await db.app_settings.get(DEFAULT_SETTINGS_ID)
    now = datetime.now().isoformat()

    # Default models from config
    default_models = list(ANTHROPIC_TO_BEDROCK_MODEL_MAP.keys())

    if not settings:
        settings = {
            "id": DEFAULT_SETTINGS_ID,
            "anthropic_api_key": "",
            "anthropic_base_url": None,
            "use_bedrock": False,
            "bedrock_auth_type": "credentials",
            "aws_access_key_id": "",
            "aws_secret_access_key": "",
            "aws_session_token": None,
            "aws_bearer_token": "",
            "aws_region": "us-east-1",
            "available_models": [],
            "default_model": app_config.default_model,
            "created_at": now,
            "updated_at": now,
        }

    # Update only provided fields
    if request.anthropic_api_key is not None:
        settings["anthropic_api_key"] = request.anthropic_api_key

    if request.anthropic_base_url is not None:
        # Allow empty string to clear the value
        settings["anthropic_base_url"] = request.anthropic_base_url if request.anthropic_base_url else None

    if request.use_bedrock is not None:
        settings["use_bedrock"] = request.use_bedrock

    if request.bedrock_auth_type is not None:
        settings["bedrock_auth_type"] = request.bedrock_auth_type

    if request.aws_access_key_id is not None:
        settings["aws_access_key_id"] = request.aws_access_key_id

    if request.aws_secret_access_key is not None:
        settings["aws_secret_access_key"] = request.aws_secret_access_key

    if request.aws_session_token is not None:
        # Allow empty string to clear the value
        settings["aws_session_token"] = request.aws_session_token if request.aws_session_token else None

    if request.aws_bearer_token is not None:
        settings["aws_bearer_token"] = request.aws_bearer_token

    if request.aws_region is not None:
        settings["aws_region"] = request.aws_region

    # Handle available_models update
    if request.available_models is not None:
        settings["available_models"] = request.available_models

    # Handle default_model update with validation
    if request.default_model is not None:
        available = settings.get("available_models", [])
        if available and request.default_model not in available:
            raise HTTPException(
                status_code=400,
                detail="default_model must be in available_models"
            )
        settings["default_model"] = request.default_model

    settings["updated_at"] = now

    # Save settings
    await db.app_settings.put(settings)

    logger.info(f"API configuration updated: use_bedrock={settings.get('use_bedrock')}, auth_type={settings.get('bedrock_auth_type')}")

    # Get available_models for response, or use config defaults if empty
    available_models = settings.get("available_models")
    if not available_models:
        available_models = default_models

    return APIConfigurationResponse(
        anthropic_api_key_set=bool(settings.get("anthropic_api_key")),
        anthropic_base_url=settings.get("anthropic_base_url"),
        use_bedrock=bool(settings.get("use_bedrock")),
        bedrock_auth_type=settings.get("bedrock_auth_type", "credentials"),
        aws_access_key_id_set=bool(settings.get("aws_access_key_id")),
        aws_bearer_token_set=bool(settings.get("aws_bearer_token")),
        aws_region=settings.get("aws_region", "us-east-1"),
        available_models=available_models,
        default_model=settings.get("default_model", app_config.default_model),
    )


async def get_api_settings() -> dict:
    """Get raw API settings for internal use.

    This returns the full settings including secrets for use by agent_manager.
    """
    settings = await db.app_settings.get(DEFAULT_SETTINGS_ID)

    if not settings:
        return {
            "anthropic_api_key": "",
            "anthropic_base_url": None,
            "use_bedrock": False,
            "bedrock_auth_type": "credentials",
            "aws_access_key_id": "",
            "aws_secret_access_key": "",
            "aws_session_token": None,
            "aws_bearer_token": "",
            "aws_region": "us-east-1",
        }

    return settings
