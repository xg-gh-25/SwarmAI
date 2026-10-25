"""Canonical App manifest and immutable package-release construction.

Key public symbols:
- ``AppManifest`` — strict schema for a canonical ``app.json``.
- ``PackageRelease`` — normalized file closure plus its SHA-256 identity.
- ``canonical_json`` — RFC 8785-compatible serializer for schema values.
- ``build_package_release`` — validate paths and hash one App source closure.
"""
from __future__ import annotations

import hashlib
import json
import os
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AppManifestError(ValueError):
    """The manifest or package closure is unsafe or non-canonical."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ProfileBinding(_StrictModel):
    """One resource's compatibility declaration for a SwarmAI profile."""

    supported: bool
    requires: list[str] = Field(default_factory=list)

    @field_validator("requires")
    @classmethod
    def unique_requirements(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("profile requirements must be unique")
        return value


class TargetBinding(_StrictModel):
    """One host-target rendering decision for a resource."""

    binding: str | None = None
    mode: Literal["unsupported"] | None = None
    required: bool | None = None
    omission: str | None = None
    profiles: dict[str, ProfileBinding] = Field(default_factory=dict)

    @model_validator(mode="after")
    def exactly_one_mode(self) -> "TargetBinding":
        if bool(self.binding) == bool(self.mode):
            raise ValueError("target must declare exactly one of binding or mode")
        if self.required is False and not self.omission:
            raise ValueError("target-optional resources require an omission reason")
        return self


def _visible_relative_path(value: str, field_name: str) -> str:
    normalized = unicodedata.normalize("NFC", value.replace("\\", "/"))
    path = PurePosixPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or ".." in path.parts
        or "." in path.parts
        or any(part.startswith(".") for part in path.parts)
    ):
        raise ValueError(f"{field_name} must be a visible normalized relative path")
    return normalized

_RESOURCE_TYPES = Literal[
    "skill", "agent", "sop", "knowledge", "command", "mcp", "job",
    "service", "surface", "backend", "host-binding", "project-template",
    "fixture",
]


class AppResource(_StrictModel):
    """One versioned contribution in the canonical App resource graph."""

    id: str = Field(pattern=r"^[a-z][a-z0-9-]*:[A-Za-z0-9_.:-]+$")
    type: _RESOURCE_TYPES
    source: str
    required: bool = True
    permissions: list[str] = Field(default_factory=list)
    targets: dict[str, TargetBinding]
    fixtures: list[str] = Field(default_factory=list)

    @field_validator("source")
    @classmethod
    def safe_source(cls, value: str) -> str:
        return _visible_relative_path(value, "resource source")


class AppDependency(_StrictModel):
    """Reference to another owner's immutable capability definition."""

    resource: str
    version: str
    required: bool = True


class FilePermissions(_StrictModel):
    read: list[str] = Field(default_factory=list)
    write: list[str] = Field(default_factory=list)

    @field_validator("read", "write")
    @classmethod
    def safe_file_scopes(cls, value: list[str]) -> list[str]:
        return [_visible_relative_path(item, "file permission scope") for item in value]


class PermissionTemplate(_StrictModel):
    files: FilePermissions = Field(default_factory=FilePermissions)
    network: list[str] = Field(default_factory=list)
    secrets: list[str] = Field(default_factory=list)
    external_actions: list[str] = Field(default_factory=list, alias="externalActions")


class Distribution(_StrictModel):
    targets: list[str] = Field(default_factory=list)
    visibility: Literal["internal", "private", "public"] = "internal"


class AppManifest(_StrictModel):
    """Strict canonical source manifest. Package-authored trust is impossible."""

    schema_version: Literal[1] = Field(alias="schemaVersion")
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,62}$")
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?$")
    display_name: str = Field(alias="displayName", min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    instance_mode: Literal["singleton", "per-project"] = Field(alias="instanceMode")
    brain_interface: str | None = Field(default=None, alias="brainInterface")
    resources: list[AppResource]
    dependencies: list[AppDependency] = Field(default_factory=list)
    permission_templates: dict[str, PermissionTemplate] = Field(
        default_factory=dict, alias="permissionTemplates"
    )
    distribution: Distribution

    @model_validator(mode="after")
    def unique_graph_ids(self) -> "AppManifest":
        ids = [resource.id for resource in self.resources]
        if len(ids) != len(set(ids)):
            raise ValueError("resource ids must be unique")
        return self


def _utf16_key(value: str) -> bytes:
    return value.encode("utf-16-be", errors="strict")


def _canonical(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        # The V1 schema needs no floats. Rejecting them avoids silently claiming
        # RFC 8785 number formatting via Python's different float serializer.
        raise AppManifestError("floating-point values are outside the V1 canonical schema")
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, list):
        return "[" + ",".join(_canonical(item) for item in value) + "]"
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise AppManifestError("JSON object keys must be strings")
        pieces = []
        for key in sorted(value, key=_utf16_key):
            pieces.append(f"{_canonical(key)}:{_canonical(value[key])}")
        return "{" + ",".join(pieces) + "}"
    raise AppManifestError(f"unsupported canonical JSON value: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Return deterministic RFC 8785 JSON for the V1 no-float schema."""

    return _canonical(value)


class PackageFile(_StrictModel):
    """One immutable file in a package closure."""

    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class PackageRelease(_StrictModel):
    """Canonical package closure and content identity."""

    schema_version: Literal[1]
    app_id: str
    app_version: str
    package_release_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest: dict[str, Any]
    files: tuple[PackageFile, ...]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _declared_files(root: Path, source: str) -> list[Path]:
    candidate = root.joinpath(*PurePosixPath(source).parts)
    if not candidate.exists():
        raise AppManifestError(f"declared source does not exist: {source}")
    if candidate.is_symlink():
        raise AppManifestError(f"declared source is a symlink: {source}")
    if candidate.is_file():
        return [candidate]

    files: list[Path] = []
    for directory, dirs, names in os.walk(candidate, followlinks=False):
        base = Path(directory)
        for name in [*dirs, *names]:
            if name.startswith("."):
                rel = (base / name).relative_to(root).as_posix()
                raise AppManifestError(f"package closure contains hidden path: {rel}")
            if (base / name).is_symlink():
                rel = (base / name).relative_to(root).as_posix()
                raise AppManifestError(f"package closure contains symlink: {rel}")
        dirs[:] = sorted(d for d in dirs if d not in {"__pycache__", "node_modules"})
        for name in sorted(names):
            if name.endswith(".pyc") or name == ".DS_Store":
                continue
            files.append(base / name)
    return files


def build_package_release(manifest: AppManifest, source_root: Path) -> PackageRelease:
    """Build the immutable closure for one built-in App source manifest."""

    root = source_root.resolve()
    paths: dict[str, Path] = {}
    path_owners: dict[str, str] = {}
    collisions: dict[str, tuple[str, Path]] = {}
    for resource in manifest.resources:
        for path in _declared_files(root, resource.source):
            resolved = path.resolve()
            try:
                raw_relative = path.relative_to(root).as_posix()
                resolved.relative_to(root)
            except ValueError as exc:
                raise AppManifestError(
                    f"resource escapes package root: {resource.source}"
                ) from exc
            relative = unicodedata.normalize("NFC", raw_relative)
            folded = relative.casefold()
            previous = collisions.get(folded)
            if previous is not None and previous[1] != resolved:
                raise AppManifestError(
                    f"cross-platform path collision: {previous[0]} / {raw_relative}"
                )
            collisions[folded] = (raw_relative, resolved)
            previous_owner = path_owners.get(relative)
            if previous_owner is not None and previous_owner != resource.id:
                raise AppManifestError(
                    f"path declared by multiple resources: {relative} "
                    f"({previous_owner}, {resource.id})"
                )
            path_owners[relative] = resource.id
            paths[relative] = resolved

    files = tuple(PackageFile(path=path, sha256=_sha256(paths[path])) for path in sorted(paths))
    normalized_manifest = _normalized_manifest(manifest)
    envelope = {
        "manifest": normalized_manifest,
        "files": [item.model_dump(mode="json") for item in files],
    }
    release_digest = hashlib.sha256(canonical_json(envelope).encode("utf-8")).hexdigest()
    return PackageRelease(
        schema_version=1,
        app_id=manifest.id,
        app_version=manifest.version,
        package_release_digest=release_digest,
        manifest=normalized_manifest,
        files=files,
    )


def _normalized_manifest(manifest: AppManifest) -> dict[str, Any]:
    value = manifest.model_dump(mode="json", by_alias=True, exclude_none=False)
    for resource in value["resources"]:
        resource["permissions"] = sorted(set(resource["permissions"]))
        resource["fixtures"] = sorted(set(resource["fixtures"]))
        for target in resource["targets"].values():
            for profile in target["profiles"].values():
                profile["requires"] = sorted(set(profile["requires"]))
    value["resources"].sort(key=lambda item: item["id"])
    value["dependencies"].sort(
        key=lambda item: (item["resource"], item["version"], item["required"])
    )
    for template in value["permissionTemplates"].values():
        template["files"]["read"] = sorted(set(template["files"]["read"]))
        template["files"]["write"] = sorted(set(template["files"]["write"]))
        template["network"] = sorted(set(template["network"]))
        template["secrets"] = sorted(set(template["secrets"]))
        template["externalActions"] = sorted(set(template["externalActions"]))
    value["distribution"]["targets"] = sorted(set(value["distribution"]["targets"]))
    return value
