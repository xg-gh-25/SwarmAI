"""SwarmAI Host Runtime Profiles and App resource accounting.

Key public symbols:
- ``HOST_PROFILES`` — immutable versioned capability declarations.
- ``account_profiles`` — exact required-resource support reports for one App.
- ``current_profile`` — maps the backend launcher mode to a profile identity.
"""
from __future__ import annotations

import hashlib
import os
from typing import Literal

from pydantic import BaseModel, ConfigDict

from core.app_manifest import AppManifest, canonical_json


ProfileName = Literal["desktop-daemon", "desktop-subprocess", "hive"]


class HostProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: ProfileName
    contract_version: str
    capabilities: frozenset[str]


_COMMON = {
    "agent-skills", "api-client", "canvas", "chat-launcher",
    "in-process-backend", "product-registry", "workspace-files",
    "subagents", "web-research",
}
HOST_PROFILES: tuple[HostProfile, ...] = (
    HostProfile(
        name="desktop-daemon", contract_version="1",
        capabilities=frozenset(_COMMON | {
            "tauri-ui", "local-filesystem", "daemon-scheduler", "channel-gateway",
        }),
    ),
    HostProfile(
        name="desktop-subprocess", contract_version="1",
        capabilities=frozenset(_COMMON | {"tauri-ui", "local-filesystem"}),
    ),
    HostProfile(
        name="hive", contract_version="1",
        capabilities=frozenset(_COMMON | {
            "web-ui", "same-origin-api", "remote-filesystem", "daemon-scheduler",
            "channel-gateway", "linux-arm64", "aws-instance-role",
        }),
    ),
)
_PROFILES = {profile.name: profile for profile in HOST_PROFILES}


class ResourceProfileResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    resource_id: str
    status: Literal["mapped", "omitted-optional", "blocked"]
    missing_capabilities: tuple[str, ...] = ()
    reason: str | None = None


class ProfileAccounting(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    profile: ProfileName
    contract_version: str
    capability_digest: str
    supported: bool
    blocked_resources: tuple[str, ...]
    resources: tuple[ResourceProfileResult, ...]


def current_profile() -> ProfileName | None:
    """Resolve registered launcher modes and fail closed for unknown values."""

    mode = os.environ.get("SWARMAI_MODE", "daemon")
    if mode == "hive":
        return "hive"
    if mode in {"subprocess", "dev"}:
        return "desktop-subprocess"
    if mode == "daemon":
        return "desktop-daemon"
    return None


def _capability_digest(profile: HostProfile, required: set[str]) -> str:
    resolved = sorted(profile.capabilities.intersection(required))
    return hashlib.sha256(canonical_json(resolved).encode("utf-8")).hexdigest()


def _is_required(resource_required: bool, target_required: bool | None) -> bool:
    return resource_required if target_required is None else target_required


def account_profiles(manifest: AppManifest) -> tuple[ProfileAccounting, ...]:
    """Account every resource against every SwarmAI profile, fail closed."""

    reports: list[ProfileAccounting] = []
    for profile in HOST_PROFILES:
        rows: list[ResourceProfileResult] = []
        required_caps: set[str] = set()
        for resource in manifest.resources:
            target = resource.targets.get("swarmai")
            profile_binding = target.profiles.get(profile.name) if target and target.binding else None
            required = _is_required(resource.required, target.required if target else None)
            if profile_binding and profile_binding.supported:
                required_caps.update(profile_binding.requires)
                missing = tuple(sorted(set(profile_binding.requires) - profile.capabilities))
                if not missing:
                    rows.append(ResourceProfileResult(resource_id=resource.id, status="mapped"))
                    continue
                reason = "missing capabilities: " + ", ".join(missing)
            else:
                missing = ()
                reason = "profile binding is absent or unsupported"

            if required:
                rows.append(ResourceProfileResult(
                    resource_id=resource.id,
                    status="blocked",
                    missing_capabilities=missing,
                    reason=reason,
                ))
            else:
                omission = target.omission if target else None
                rows.append(ResourceProfileResult(
                    resource_id=resource.id,
                    status="omitted-optional",
                    missing_capabilities=missing,
                    reason=omission or reason,
                ))

        blocked = tuple(row.resource_id for row in rows if row.status == "blocked")
        reports.append(ProfileAccounting(
            profile=profile.name,
            contract_version=profile.contract_version,
            capability_digest=_capability_digest(profile, required_caps),
            supported=not blocked,
            blocked_resources=blocked,
            resources=tuple(rows),
        ))
    return tuple(reports)


def profile_by_name(name: ProfileName) -> HostProfile:
    return _PROFILES[name]
