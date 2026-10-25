"""External App source provenance and local-byte verification.

Key public symbols:
- ``AppProvenance`` — strict host-owned immutable source declaration.
- ``load_verified_provenance`` — validate one package sidecar and every pinned byte.
"""
from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AppProvenanceError(ValueError):
    """An external package's source declaration or local bytes are invalid."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)


def _relative_path(value: str, field_name: str) -> str:
    if not value or "\\" in value:
        raise ValueError(f"{field_name} must use a non-empty POSIX relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ValueError(f"{field_name} must not escape its package")
    return value


class ProvenanceSource(_StrictModel):
    """One immutable upstream repository authority."""

    id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,62}$")
    repository: str
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    license: str = Field(pattern=r"^[A-Za-z0-9-.+]+$")
    archive_sha256: str | None = Field(default=None, alias="archiveSha256",
                                       pattern=r"^[0-9a-f]{64}$")

    @field_validator("repository")
    @classmethod
    def safe_repository(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("repository must be an HTTPS URL without credentials")
        return value.rstrip("/")


class ProvenanceFile(_StrictModel):
    """One verbatim upstream file retained in the tracked package."""

    source: str = Field(pattern=r"^[a-z][a-z0-9-]{1,62}$")
    upstream_path: str = Field(alias="upstreamPath")
    local_path: str = Field(alias="localPath")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("upstream_path")
    @classmethod
    def safe_upstream_path(cls, value: str) -> str:
        return _relative_path(value, "upstreamPath")

    @field_validator("local_path")
    @classmethod
    def safe_local_path(cls, value: str) -> str:
        return _relative_path(value, "localPath")


class ProvenanceModification(_StrictModel):
    """A local adapter file derived from or interpreting upstream material."""

    local_path: str = Field(alias="localPath")
    description: str = Field(min_length=1, max_length=500)

    @field_validator("local_path")
    @classmethod
    def safe_local_path(cls, value: str) -> str:
        return _relative_path(value, "modification localPath")


class AppProvenance(_StrictModel):
    """Deterministic provenance sidecar with no trust or lifecycle authority."""

    schema_version: Literal[1] = Field(alias="schemaVersion")
    sources: tuple[ProvenanceSource, ...]
    files: tuple[ProvenanceFile, ...]
    modifications: tuple[ProvenanceModification, ...] = ()

    @model_validator(mode="after")
    def unique_and_connected(self) -> "AppProvenance":
        source_ids = [source.id for source in self.sources]
        if not source_ids or len(source_ids) != len(set(source_ids)):
            raise ValueError("provenance source ids must be non-empty and unique")
        local_paths = [item.local_path for item in self.files]
        if not local_paths or len(local_paths) != len(set(local_paths)):
            raise ValueError("provenance local paths must be non-empty and unique")
        upstream_keys = [(item.source, item.upstream_path) for item in self.files]
        if len(upstream_keys) != len(set(upstream_keys)):
            raise ValueError("provenance upstream source paths must be unique")
        referenced_sources = {item.source for item in self.files}
        if any(item.source not in source_ids for item in self.files):
            raise ValueError("every provenance file must name a declared source")
        if referenced_sources != set(source_ids):
            raise ValueError("every provenance source must retain file evidence")
        modification_paths = [item.local_path for item in self.modifications]
        if len(modification_paths) != len(set(modification_paths)):
            raise ValueError("modification paths must be unique")
        return self


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_verified_provenance(package_root: Path) -> AppProvenance:
    """Load ``PROVENANCE.json`` and verify each referenced local package file."""

    import json

    root = package_root.resolve()
    sidecar = package_root / "PROVENANCE.json"
    if not sidecar.is_file() or sidecar.is_symlink():
        raise AppProvenanceError("external App package requires a regular PROVENANCE.json")
    try:
        provenance = AppProvenance.model_validate(
            json.loads(sidecar.read_text(encoding="utf-8"))
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise AppProvenanceError(f"invalid App provenance: {exc}") from exc

    for item in provenance.files:
        path = package_root.joinpath(*PurePosixPath(item.local_path).parts)
        if not path.is_file() or path.is_symlink():
            raise AppProvenanceError(f"provenance file is missing or unsafe: {item.local_path}")
        try:
            path.resolve().relative_to(root)
        except (OSError, ValueError) as exc:
            raise AppProvenanceError(
                f"provenance file escapes package: {item.local_path}"
            ) from exc
        if _file_sha256(path) != item.sha256:
            raise AppProvenanceError(f"provenance digest mismatch: {item.local_path}")

    for item in provenance.modifications:
        path = package_root.joinpath(*PurePosixPath(item.local_path).parts)
        if not path.is_file() or path.is_symlink():
            raise AppProvenanceError(
                f"declared modification is missing or unsafe: {item.local_path}"
            )
        try:
            path.resolve().relative_to(root)
        except (OSError, ValueError) as exc:
            raise AppProvenanceError(
                f"declared modification escapes package: {item.local_path}"
            ) from exc

    declared_paths = {item.local_path for item in provenance.files}
    declared_paths.update(item.local_path for item in provenance.modifications)
    actual_paths: set[str] = set()
    for path in package_root.rglob("*"):
        relative = path.relative_to(package_root)
        if any(part.startswith(".") for part in relative.parts):
            raise AppProvenanceError(f"provenance package contains hidden path: {relative}")
        if path.is_symlink():
            raise AppProvenanceError(f"provenance package contains symlink: {relative}")
        if path.is_file() and relative.as_posix() != "PROVENANCE.json":
            actual_paths.add(relative.as_posix())
    if actual_paths != declared_paths:
        missing = sorted(declared_paths - actual_paths)
        undeclared = sorted(actual_paths - declared_paths)
        raise AppProvenanceError(
            f"provenance package closure mismatch; missing={missing}, "
            f"undeclared={undeclared}"
        )
    return provenance