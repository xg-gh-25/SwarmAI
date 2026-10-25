"""Generate the immutable App index, provenance schemas, and typed binding maps.

Key public symbols:
- ``render_outputs`` — pure deterministic generation used by tests and builds.
- ``main`` — write outputs or fail when committed generated files are stale.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from core.app_manifest import AppManifest, build_package_release, canonical_json
from core.app_profiles import account_profiles
from core.app_provenance import (
    AppProvenance,
    AppProvenanceError,
    load_verified_provenance,
)


OUTPUT_INDEX = Path("backend/generated/apps_index.json")
OUTPUT_SCHEMA = Path("backend/generated/app_manifest.schema.json")
OUTPUT_PROVENANCE_SCHEMA = Path("backend/generated/app_provenance.schema.json")
OUTPUT_PY = Path("backend/generated/app_bindings.py")
OUTPUT_TS = Path("desktop/src/generated/appBindings.ts")


def _json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _load_manifests(root: Path) -> list[tuple[Path, AppManifest]]:
    manifests: list[tuple[Path, AppManifest]] = []
    for path in sorted((root / "backend/apps").glob("*/app.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        manifests.append((path, AppManifest.model_validate(raw)))
    if not manifests:
        raise RuntimeError("no built-in App manifests found")
    ids = [manifest.id for _, manifest in manifests]
    if len(ids) != len(set(ids)):
        raise RuntimeError("duplicate built-in App id")
    return manifests


def _load_provenance(app_dir: Path) -> AppProvenance | None:
    """Load the App's single verified provenance sidecar when present."""

    expected = app_dir / "package" / "PROVENANCE.json"
    matches = sorted(app_dir.rglob("PROVENANCE.json"))
    if not matches:
        return None
    if matches != [expected]:
        raise AppProvenanceError(
            "external App provenance must exist only at package/PROVENANCE.json"
        )
    return load_verified_provenance(expected.parent)


def _provenance_summary(provenance: AppProvenance) -> dict | None:
    """Build a compact upstream source summary from verified provenance."""
    if not provenance.sources:
        return None
    sources = []
    for src in provenance.sources:
        sources.append({
            "id": src.id,
            "repository": src.repository,
            "commit": src.commit,
            "license": src.license,
        })
    return {"sources": sources}


def _derive_source_tier(provenance: AppProvenance | None) -> str:
    """Derive source tier from verified host provenance, never app.json."""

    return "external" if provenance is not None else "built-in"


def _binding_rows(
    manifests: list[tuple[Path, AppManifest]],
    provenance_map: dict[str, AppProvenance | None],
) -> list[dict]:
    rows: list[dict] = []
    seen: dict[str, str] = {}
    for _, manifest in manifests:
        provenance = provenance_map.get(manifest.id)
        for resource in manifest.resources:
            target = resource.targets.get("swarmai")
            if not target or not target.binding:
                continue
            owner = seen.get(target.binding)
            if owner is not None:
                raise RuntimeError(f"duplicate binding {target.binding}: {owner}, {manifest.id}")
            seen[target.binding] = manifest.id
            rows.append({
                "app_id": manifest.id,
                "resource_id": resource.id,
                "resource_type": resource.type,
                "binding": target.binding,
                "source": resource.source,
                "has_provenance": provenance is not None,
            })
    return sorted(rows, key=lambda row: (row["app_id"], row["resource_id"]))


_BUILTIN_BINDING_PREFIXES = {
    "skill": "builtin:skill:",
    "backend": "builtin:router:",
    "surface": "builtin:surface:",
    "host-binding": "builtin:client:",
}

_EXTERNAL_BINDING_PREFIXES = {
    "skill": "external:skill:",
}


def _python_defines_name(path: Path, name: str) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id == name for target in targets):
                return True
    return False


def _validate_binding_sources(root: Path, rows: list[dict]) -> None:
    for row in rows:
        resource_type = row["resource_type"]
        binding = row["binding"]
        if row.get("has_provenance", False) and resource_type != "skill":
            raise RuntimeError(
                "external Apps may bind instruction-only Skill resources"
            )
        app_id = row["app_id"]
        source_text = row["source"]
        source = root / source_text
        external_root = f"backend/apps/{app_id}/"
        external_skill = (
            resource_type == "skill"
            and row.get("has_provenance", False)
            and source_text.startswith(external_root)
        )
        if external_skill:
            expected_prefix = _EXTERNAL_BINDING_PREFIXES["skill"]
        else:
            expected_prefix = _BUILTIN_BINDING_PREFIXES.get(resource_type)
        if expected_prefix is None or not binding.startswith(expected_prefix):
            raise RuntimeError(
                f"unsupported or mismatched binding: {binding} for {resource_type}"
            )

        if resource_type == "skill":
            if not external_skill and not source_text.startswith("backend/skills/"):
                raise RuntimeError(f"Skill binding is outside backend/skills: {source_text}")
            if not source.is_dir() or not (source / "SKILL.md").is_file() or not (
                source / "INSTRUCTIONS.md"
            ).is_file():
                raise RuntimeError(
                    f"unresolved Skill binding; SKILL.md and INSTRUCTIONS.md are required: "
                    f"{source_text}"
                )
        elif resource_type == "backend":
            if not source_text.startswith("backend/"):
                raise RuntimeError(f"backend binding is outside backend: {source_text}")
            if source.suffix != ".py" or not source.is_file() or not _python_defines_name(
                source, "router"
            ):
                raise RuntimeError(f"unresolved backend router binding: {source_text}")
        elif resource_type in {"surface", "host-binding"}:
            if not source_text.startswith("desktop/src/"):
                raise RuntimeError(f"frontend binding is outside desktop/src: {source_text}")
            if not source.is_file() or source.suffix not in {".ts", ".tsx"}:
                raise RuntimeError(f"unresolved frontend binding source: {source_text}")


def _python_bindings(rows: list[dict]) -> str:
    backend_rows: dict[str, tuple[str, str]] = {}
    skill_rows: dict[str, str] = {}
    for row in rows:
        key = f"{row['app_id']}/{row['resource_id']}"
        source = row["source"]
        if row["resource_type"] == "backend":
            module = source.removeprefix("backend/").removesuffix(".py").replace("/", ".")
            backend_rows[key] = (module, "router")
        elif row["resource_type"] == "skill":
            skill_rows[key] = source
    return (
        '"""Generated App bindings. DO NOT EDIT.\n\n'
        "Regenerate with ``python backend/scripts/generate_app_registry.py``.\n"
        '"""\nfrom __future__ import annotations\n\n'
        "from typing import Final\n\n"
        f"APP_ROUTER_IMPORTS: Final = {backend_rows!r}\n"
        f"APP_SKILL_PATHS: Final = {skill_rows!r}\n"
    )


def _ts_import(source: str) -> str:
    relative = source.removeprefix("desktop/src/")
    no_ext = relative.rsplit(".", 1)[0]
    return "../" + no_ext


def _typescript_bindings(rows: list[dict], binding_digest: str) -> str:
    surfaces = []
    clients = []
    for row in rows:
        if row["resource_type"] not in {"surface", "host-binding"}:
            continue
        key = f"{row['app_id']}/{row['resource_id']}"
        rendered = f"  {json.dumps(key)}: () => import({json.dumps(_ts_import(row['source']))}),"
        if row["resource_type"] == "surface":
            surfaces.append(rendered)
        else:
            clients.append(rendered)
    return (
        "/** Generated App bindings. DO NOT EDIT.\n"
        " * Regenerate with `python backend/scripts/generate_app_registry.py`.\n"
        " */\n"
        f"export const BUILTIN_APP_BINDING_DIGEST = {json.dumps(binding_digest)} as const;\n\n"
        "export const BUILTIN_APP_SURFACE_IMPORTS = {\n"
        + "\n".join(surfaces)
        + "\n} as const;\n\n"
        "export const BUILTIN_APP_CLIENT_IMPORTS = {\n"
        + "\n".join(clients)
        + "\n} as const;\n\n"
        "export type BuiltinAppSurface = keyof typeof BUILTIN_APP_SURFACE_IMPORTS;\n"
        "export type BuiltinAppClient = keyof typeof BUILTIN_APP_CLIENT_IMPORTS;\n"
    )


def _compute_proof_blockers(
    profile_accounting: list[dict],
) -> list[str]:
    """Compute proof blockers including profile-blocked entries."""
    blockers: list[str] = ["host-build-attestation-missing", "proof-not-run"]

    # Add profile-blocked entries for any profile that is not supported
    for report in profile_accounting:
        if not report["supported"]:
            blocker = f"profile-blocked:{report['profile']}"
            if blocker not in blockers:
                blockers.append(blocker)

    return sorted(blockers)


def _app_record(
    root: Path,
    manifest: AppManifest,
    binding_digest: str,
    provenance: AppProvenance | None,
) -> dict:
    release = build_package_release(manifest, root)
    source_tier = _derive_source_tier(provenance)

    # Build profile accounting
    profile_reports = [
        report.model_dump(mode="json") for report in account_profiles(manifest)
    ]

    # Compute proof blockers from profile accounting
    proof_blockers = _compute_proof_blockers(profile_reports)

    # Build upstream source summary if provenance exists
    upstream_source = _provenance_summary(provenance) if provenance else None

    record = {
        "app_id": manifest.id,
        "display_name": manifest.display_name,
        "description": manifest.description,
        "version": manifest.version,
        "instance_mode": manifest.instance_mode,
        "source_tier": source_tier,
        "evidence_state": "catalog-only",
        "lifecycle_controls": [],
        "package_release_digest": release.package_release_digest,
        "execution_release_digest": None,
        "binding_map_digest": binding_digest,
        "proof_blockers": proof_blockers,
        "profile_accounting": profile_reports,
        "resources": [
            {
                "resource_id": resource.id,
                "resource_type": resource.type,
                "required": resource.required,
            }
            for resource in manifest.resources
        ],
        "dependencies": [dependency.model_dump(mode="json") for dependency in manifest.dependencies],
    }

    if upstream_source:
        record["upstream_source"] = upstream_source

    return record


def render_outputs(root: Path) -> dict[Path, str]:
    """Return every generated file exactly as it must appear in the tree."""

    root = root.resolve()
    manifests = _load_manifests(root)

    # Load and verify provenance for each app
    provenance_map: dict[str, AppProvenance | None] = {}
    for path, manifest in manifests:
        app_dir = path.parent
        try:
            provenance = _load_provenance(app_dir)
        except AppProvenanceError as exc:
            raise RuntimeError(
                f"invalid provenance for {manifest.id}: {exc}"
            ) from exc
        provenance_map[manifest.id] = provenance

    rows = _binding_rows(manifests, provenance_map)
    _validate_binding_sources(root, rows)
    binding_digest = hashlib.sha256(canonical_json(rows).encode("utf-8")).hexdigest()
    index = {
        "schema_version": 1,
        "host_contract_version": "1",
        "binding_map_digest": binding_digest,
        "apps": [
            _app_record(root, manifest, binding_digest, provenance_map[manifest.id])
            for _, manifest in manifests
        ],
    }
    return {
        OUTPUT_INDEX: _json_text(index),
        OUTPUT_SCHEMA: _json_text(AppManifest.model_json_schema(by_alias=True)),
        OUTPUT_PROVENANCE_SCHEMA: _json_text(AppProvenance.model_json_schema(by_alias=True)),
        OUTPUT_PY: _python_bindings(rows),
        OUTPUT_TS: _typescript_bindings(rows, binding_digest),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if generated files are stale")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args(argv)
    outputs = render_outputs(args.root)
    stale: list[str] = []
    for relative, content in outputs.items():
        path = args.root / relative
        if args.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != content:
                stale.append(relative.as_posix())
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"generated {relative}")
    if stale:
        print("stale generated App files: " + ", ".join(stale), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
