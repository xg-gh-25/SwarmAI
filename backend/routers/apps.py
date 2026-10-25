"""Read-only App Platform catalog API for Phase A.

Key public symbols:
- ``router`` — ``GET /api/apps`` and ``GET /api/apps/{app_id}`` only.
- ``create_router`` — dependency injection for isolated registry tests.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from core.app_profiles import ProfileName
from core.app_registry import AppCatalogEntry, AppRegistry, app_registry


class AppListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = 1
    host_contract_version: str
    current_profile: ProfileName | None
    apps: tuple[AppCatalogEntry, ...]


def create_router(registry: AppRegistry) -> APIRouter:
    """Build a read-only router around one immutable generated registry."""

    result = APIRouter()

    @result.get("", response_model=AppListResponse)
    async def list_apps() -> AppListResponse:
        return AppListResponse(
            host_contract_version=registry.host_contract_version,
            current_profile=registry.current_profile,
            apps=registry.list_apps(),
        )

    @result.get("/{app_id}", response_model=AppCatalogEntry)
    async def get_app(app_id: str) -> AppCatalogEntry:
        app = registry.get_app(app_id)
        if app is None:
            raise HTTPException(status_code=404, detail="App not found")
        return app

    return result


router = create_router(app_registry)
