"""Immutable runtime App Registry backed by one build-generated index.

Key public symbols:
- ``AppRegistry`` — load-once read API with no manifest discovery or filesystem walk.
- ``UpstreamSource`` — verified immutable source summary exposed by the catalog.
- ``app_registry`` — process-wide registry used by the read-only Apps router.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.app_profiles import ProfileAccounting, ProfileName, current_profile
from utils.backend_bin import bundled_path


EvidenceState = Literal["catalog-only", "development", "product-proven"]
SourceTier = Literal["built-in", "external"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RegistryResource(_StrictModel):
    resource_id: str
    resource_type: str
    required: bool


class RegistryDependency(_StrictModel):
    resource: str
    version: str
    required: bool


class UpstreamSourceEntry(_StrictModel):
    """One upstream source authority from verified provenance."""

    id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,62}$")
    repository: str
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    license: str = Field(pattern=r"^[A-Za-z0-9-.+]+$")

    @field_validator("repository")
    @classmethod
    def safe_repository(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ValueError("upstream repository must be HTTPS without credentials")
        return value


class UpstreamSource(_StrictModel):
    """Compact upstream source summary for external Apps."""

    sources: tuple[UpstreamSourceEntry, ...] = Field(min_length=1)


class AppCatalogEntry(_StrictModel):
    """One non-operational Phase A catalog projection."""

    app_id: str
    display_name: str
    description: str
    version: str
    instance_mode: str
    source_tier: SourceTier
    evidence_state: EvidenceState
    lifecycle_controls: tuple[str, ...]
    package_release_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_release_digest: str | None
    binding_map_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    proof_blockers: tuple[str, ...]
    profile_accounting: tuple[ProfileAccounting, ...]
    resources: tuple[RegistryResource, ...]
    dependencies: tuple[RegistryDependency, ...]
    upstream_source: UpstreamSource | None = None


class AppIndex(_StrictModel):
    schema_version: Literal[1]
    host_contract_version: str
    binding_map_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    apps: tuple[AppCatalogEntry, ...]


class AppRegistry:
    """Read one generated index once; no invalidation or rescan exists in Phase A."""

    def __init__(self, index_path: Path | None = None) -> None:
        self._index_path = index_path or bundled_path("generated", "apps_index.json")
        self._lock = threading.Lock()
        self._index: AppIndex | None = None
        self._by_id: dict[str, AppCatalogEntry] | None = None

    def _load(self) -> None:
        if self._index is not None:
            return
        with self._lock:
            if self._index is not None:
                return
            raw = json.loads(self._index_path.read_text(encoding="utf-8"))
            index = AppIndex.model_validate(raw)
            by_id = {app.app_id: app for app in index.apps}
            if len(by_id) != len(index.apps):
                raise RuntimeError("generated App index contains duplicate app ids")
            if any(app.binding_map_digest != index.binding_map_digest for app in index.apps):
                raise RuntimeError("generated App index binding digest mismatch")
            self._index = index
            self._by_id = by_id

    def list_apps(self) -> tuple[AppCatalogEntry, ...]:
        self._load()
        assert self._index is not None
        return self._index.apps

    def get_app(self, app_id: str) -> AppCatalogEntry | None:
        self._load()
        assert self._by_id is not None
        return self._by_id.get(app_id)

    @property
    def host_contract_version(self) -> str:
        self._load()
        assert self._index is not None
        return self._index.host_contract_version

    @property
    def current_profile(self) -> ProfileName | None:
        return current_profile()


app_registry = AppRegistry()
