"""Generate Host Execution Release attestations from an actual staged host build.

Key public symbols:
- ``build_attestation`` — pure deterministic attestation construction.
- ``verify_attestation`` — strict installed-backend and record-consistency validation.
- ``main`` — writes ``app-execution-releases.json`` beside a staged installer.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path, PurePosixPath
from typing import Any



ADAPTER_VERSION = "swarmai-host-adapter/1"


def _canonical_json(value: Any) -> str:
    """Canonical subset for this ASCII-keyed, integer-free attestation schema."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _file_digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            value.update(block)
    return value.hexdigest()


def _host_build_input(install: dict, frontend_files: list[dict]) -> dict:
    files = sorted(
        ({"path": row["path"], "sha256": row["sha256"]} for row in install["files"]),
        key=lambda row: row["path"],
    )
    frontend = sorted(frontend_files, key=lambda row: row["path"])
    return {
        "version": install["version"],
        "target": install["target"],
        "executable": install["executable"],
        "backend_and_resources": files,
        "compiled_frontend": frontend,
    }


def _frontend_files(root: Path) -> list[dict]:
    rows: list[dict] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"frontend build contains symlink: {path.relative_to(root)}")
        if path.is_file():
            rows.append({"path": path.relative_to(root).as_posix(), "sha256": _file_digest(path)})
    if not rows:
        raise ValueError("compiled frontend contains no files")
    return rows


def _sha256_value(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _safe_attested_path(value: object) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and ".." not in path.parts and "." not in path.parts


def _validate_install_manifest(install: object) -> None:
    if not isinstance(install, dict) or install.get("schema") != 1:
        raise ValueError("unsupported or malformed install manifest")
    for field in ("version", "target", "executable"):
        if not isinstance(install.get(field), str) or not install[field]:
            raise ValueError(f"install manifest {field} is missing")
    files = install.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("install manifest file evidence is missing")
    seen_paths: set[str] = set()
    for row in files:
        if not isinstance(row, dict) or set(row) != {"path", "sha256"}:
            raise ValueError("invalid install manifest file row")
        if not _safe_attested_path(row["path"]) or not _sha256_value(row["sha256"]):
            raise ValueError("invalid install manifest path or digest")
        if row["path"] in seen_paths:
            raise ValueError("duplicate install manifest path")
        seen_paths.add(row["path"])
    if install["executable"] not in seen_paths:
        raise ValueError("install manifest executable is not listed")


def verify_attestation(install_manifest: dict, attestation: dict) -> None:
    """Validate installed backend binding and attestation self-consistency.

    Compiled frontend rows are measurements retained from the pre-bundle Vite output.
    Tauri embeds those bytes, so this function cannot independently re-enumerate them
    from an installed application executable.
    """

    _validate_install_manifest(install_manifest)
    if not isinstance(attestation, dict):
        raise ValueError("execution attestation must be an object")
    if attestation.get("schema_version") != 1:
        raise ValueError("unsupported execution attestation schema")
    if attestation.get("target") != install_manifest.get("target"):
        raise ValueError("attestation target does not match install manifest")
    if attestation.get("adapter_version") != ADAPTER_VERSION:
        raise ValueError("unexpected host adapter version")
    profile = attestation.get("profile")
    if profile not in {"desktop-daemon", "desktop-subprocess", "hive"}:
        raise ValueError("invalid attested runtime profile")

    frontend_files = attestation.get("frontend_files")
    if not isinstance(frontend_files, list) or not frontend_files:
        raise ValueError("compiled frontend evidence is missing")
    seen_paths: set[str] = set()
    for row in frontend_files:
        if not isinstance(row, dict) or set(row) != {"path", "sha256"}:
            raise ValueError("invalid compiled frontend evidence row")
        if not _safe_attested_path(row["path"]) or not _sha256_value(row["sha256"]):
            raise ValueError("invalid compiled frontend path or digest")
        if row["path"] in seen_paths:
            raise ValueError("duplicate compiled frontend path")
        seen_paths.add(row["path"])

    host_build_digest = _digest(_host_build_input(install_manifest, frontend_files))
    if attestation.get("host_build_digest") != host_build_digest:
        raise ValueError("installed backend/resource digest does not match attestation")
    dependency_digest = attestation.get("resolved_dependency_lock_digest")
    if not _sha256_value(dependency_digest):
        raise ValueError("invalid dependency-lock digest")

    releases = attestation.get("execution_releases")
    if not isinstance(releases, list) or not releases:
        raise ValueError("execution attestation contains no releases")
    seen_apps: set[str] = set()
    for release in releases:
        if not isinstance(release, dict):
            raise ValueError("invalid execution release")
        identity = release.get("identity")
        if not isinstance(identity, dict):
            raise ValueError("execution release identity is missing")
        app_id = release.get("app_id")
        if not isinstance(app_id, str) or not app_id or app_id in seen_apps:
            raise ValueError("invalid or duplicate execution release App id")
        seen_apps.add(app_id)
        if release.get("profile") != profile or identity.get("profile") != profile:
            raise ValueError("execution release profile mismatch")
        if identity.get("target") != attestation["target"]:
            raise ValueError("execution release target mismatch")
        if identity.get("adapter_version") != ADAPTER_VERSION:
            raise ValueError("execution release adapter mismatch")
        if identity.get("host_contract_version") != attestation.get("host_contract_version"):
            raise ValueError("execution release host contract mismatch")
        if identity.get("host_build_digest") != host_build_digest:
            raise ValueError("execution release host build mismatch")
        if identity.get("resolved_dependency_lock_digest") != dependency_digest:
            raise ValueError("execution release dependency-lock mismatch")
        if identity.get("package_release_digest") != release.get("package_release_digest"):
            raise ValueError("execution release package digest mismatch")
        if not _sha256_value(identity.get("target_output_or_embedded_binding_digest")):
            raise ValueError("invalid execution release binding digest")
        if not _sha256_value(identity.get("resolved_required_host_capability_digest")):
            raise ValueError("invalid execution release capability digest")
        if release.get("execution_release_digest") != _digest(identity):
            raise ValueError("execution release digest mismatch")


def build_attestation(
    index: dict,
    install_manifest: dict,
    profile: str,
    dependency_locks: list[Path],
    frontend_files: list[dict],
) -> dict:
    """Bind generated App declarations to one exact packaged host build."""

    host_build_digest = _digest(_host_build_input(install_manifest, frontend_files))
    lock_rows = [
        {"name": path.name, "sha256": _file_digest(path)}
        for path in sorted(dependency_locks, key=lambda item: item.name)
    ]
    dependency_digest = _digest(lock_rows)
    releases: list[dict] = []
    for app in sorted(index["apps"], key=lambda row: row["app_id"]):
        accounting = next(
            (row for row in app["profile_accounting"] if row["profile"] == profile),
            None,
        )
        if accounting is None or not accounting["supported"]:
            continue
        identity = {
            "package_release_digest": app["package_release_digest"],
            "target_output_or_embedded_binding_digest": app["binding_map_digest"],
            "adapter_version": ADAPTER_VERSION,
            "host_contract_version": index["host_contract_version"],
            "resolved_required_host_capability_digest": accounting["capability_digest"],
            "resolved_dependency_lock_digest": dependency_digest,
            "host_build_digest": host_build_digest,
            "profile": profile,
            "target": install_manifest["target"],
        }
        releases.append({
            "app_id": app["app_id"],
            "profile": profile,
            "package_release_digest": app["package_release_digest"],
            "execution_release_digest": _digest(identity),
            "identity": identity,
        })
    return {
        "schema_version": 1,
        "adapter_version": ADAPTER_VERSION,
        "host_contract_version": index["host_contract_version"],
        "target": install_manifest["target"],
        "profile": profile,
        "host_build_digest": host_build_digest,
        "resolved_dependency_lock_digest": dependency_digest,
        "frontend_files": frontend_files,
        "execution_releases": releases,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apps-index", type=Path, required=True)
    parser.add_argument("--install-manifest", type=Path, required=True)
    parser.add_argument("--profile", choices=["desktop-daemon", "desktop-subprocess", "hive"], required=True)
    parser.add_argument("--frontend-dir", type=Path, required=True)
    parser.add_argument("--dependency-lock", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        index = json.loads(args.apps_index.read_text(encoding="utf-8"))
        install = json.loads(args.install_manifest.read_text(encoding="utf-8"))
        if not args.dependency_lock or any(not path.is_file() for path in args.dependency_lock):
            raise ValueError("every declared dependency lock must exist")
        if not args.frontend_dir.is_dir():
            raise ValueError("compiled frontend directory must exist")
        frontend_files = _frontend_files(args.frontend_dir)
        result = build_attestation(
            index, install, args.profile, args.dependency_lock, frontend_files
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"execution-release attestation failed: {exc}", file=sys.stderr)
        return 1
    print(f"App execution releases: {len(result['execution_releases'])} -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
