"""Generate the Phase A immutable App index and typed built-in binding maps.

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


OUTPUT_INDEX = Path("backend/generated/apps_index.json")
OUTPUT_SCHEMA = Path("backend/generated/app_manifest.schema.json")
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


def _binding_rows(manifests: list[tuple[Path, AppManifest]]) -> list[dict]:
    rows: list[dict] = []
    seen: dict[str, str] = {}
    for _, manifest in manifests:
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
            })
    return sorted(rows, key=lambda row: (row["app_id"], row["resource_id"]))


_BINDING_PREFIXES = {
    "skill": "builtin:skill:",
    "backend": "builtin:router:",
    "surface": "builtin:surface:",
    "host-binding": "builtin:client:",
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
        expected_prefix = _BINDING_PREFIXES.get(resource_type)
        if expected_prefix is None or not row["binding"].startswith(expected_prefix):
            raise RuntimeError(
                f"unsupported or mismatched built-in binding: {row['binding']} "
                f"for {resource_type}"
            )
        source_text = row["source"]
        source = root / source_text
        if resource_type == "skill":
            if not source_text.startswith("backend/skills/"):
                raise RuntimeError(f"Skill binding is outside backend/skills: {source_text}")
            if not source.is_dir() or not (source / "SKILL.md").is_file() or not (
                source / "INSTRUCTIONS.md"
            ).is_file():
                raise RuntimeError(f"unresolved Skill binding source: {source_text}")
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
        '"""Generated built-in App bindings. DO NOT EDIT.\n\n'
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
        "/** Generated built-in App bindings. DO NOT EDIT.\n"
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


def _app_record(root: Path, manifest: AppManifest, binding_digest: str) -> dict:
    release = build_package_release(manifest, root)
    return {
        "app_id": manifest.id,
        "display_name": manifest.display_name,
        "description": manifest.description,
        "version": manifest.version,
        "instance_mode": manifest.instance_mode,
        "source_tier": "built-in",
        "evidence_state": "catalog-only",
        "lifecycle_controls": [],
        "package_release_digest": release.package_release_digest,
        "execution_release_digest": None,
        "binding_map_digest": binding_digest,
        "proof_blockers": ["host-build-attestation-missing", "proof-not-run"],
        "profile_accounting": [
            report.model_dump(mode="json") for report in account_profiles(manifest)
        ],
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


def render_outputs(root: Path) -> dict[Path, str]:
    """Return every generated file exactly as it must appear in the tree."""

    root = root.resolve()
    manifests = _load_manifests(root)
    rows = _binding_rows(manifests)
    _validate_binding_sources(root, rows)
    binding_digest = hashlib.sha256(canonical_json(rows).encode("utf-8")).hexdigest()
    index = {
        "schema_version": 1,
        "host_contract_version": "1",
        "binding_map_digest": binding_digest,
        "apps": [
            _app_record(root, manifest, binding_digest) for _, manifest in manifests
        ],
    }
    return {
        OUTPUT_INDEX: _json_text(index),
        OUTPUT_SCHEMA: _json_text(AppManifest.model_json_schema(by_alias=True)),
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
