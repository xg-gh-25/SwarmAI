"""Tests for Host Execution Release attestation generation.

The attestation binds canonical App identity to exact packaged host bytes, generated
bindings, profile capabilities, contract versions, and dependency locks.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from scripts.generate_app_execution_releases import build_attestation, verify_attestation


def _index() -> dict:
    return {
        "schema_version": 1,
        "host_contract_version": "1",
        "binding_map_digest": "a" * 64,
        "apps": [{
            "app_id": "pollinate",
            "package_release_digest": "b" * 64,
            "binding_map_digest": "a" * 64,
            "profile_accounting": [{
                "profile": "desktop-daemon",
                "contract_version": "1",
                "capability_digest": "c" * 64,
                "supported": True,
                "blocked_resources": [],
                "resources": [],
            }],
        }],
    }


def _install() -> dict:
    return {
        "schema": 1,
        "version": "2.3.0",
        "target": "aarch64-apple-darwin",
        "executable": "python-backend/python-backend",
        "files": [{"path": "python-backend/python-backend", "sha256": "d" * 64}],
    }


def _frontend() -> list[dict]:
    return [{"path": "index.html", "sha256": "f" * 64}]


def test_attestation_is_deterministic_and_binds_all_inputs(tmp_path: Path) -> None:
    lock = tmp_path / "uv.lock"
    lock.write_text("locked\n", encoding="utf-8")
    first = build_attestation(_index(), _install(), "desktop-daemon", [lock], _frontend())
    second = build_attestation(_index(), _install(), "desktop-daemon", [lock], _frontend())
    assert first == second
    release = first["execution_releases"][0]
    assert release["app_id"] == "pollinate"
    assert release["profile"] == "desktop-daemon"
    assert len(release["execution_release_digest"]) == 64
    assert len(first["host_build_digest"]) == 64
    assert len(first["resolved_dependency_lock_digest"]) == 64


def test_host_byte_or_dependency_change_changes_execution_release(tmp_path: Path) -> None:
    lock = tmp_path / "uv.lock"
    lock.write_text("one", encoding="utf-8")
    base = build_attestation(_index(), _install(), "desktop-daemon", [lock], _frontend())
    changed_install = deepcopy(_install())
    changed_install["files"][0]["sha256"] = "e" * 64
    host_changed = build_attestation(_index(), changed_install, "desktop-daemon", [lock], _frontend())
    lock.write_text("two", encoding="utf-8")
    lock_changed = build_attestation(_index(), _install(), "desktop-daemon", [lock], _frontend())
    digests = {
        base["execution_releases"][0]["execution_release_digest"],
        host_changed["execution_releases"][0]["execution_release_digest"],
        lock_changed["execution_releases"][0]["execution_release_digest"],
    }
    assert len(digests) == 3


def test_unsupported_profile_has_no_execution_release(tmp_path: Path) -> None:
    lock = tmp_path / "lock"
    lock.write_text("x", encoding="utf-8")
    result = build_attestation(_index(), _install(), "hive", [lock], _frontend())
    assert result["execution_releases"] == []


def test_desktop_build_wires_and_bundles_execution_attestation() -> None:
    root = Path(__file__).resolve().parents[2]
    build = (root / "desktop/scripts/build-backend.sh").read_text(encoding="utf-8")
    prebuild = (root / "desktop/scripts/pre-build.sh").read_text(encoding="utf-8")
    bundle = (root / "desktop/src-tauri/tauri.bundle.conf.json").read_text(encoding="utf-8")
    verifier = (root / "scripts/verify_installer.sh").read_text(encoding="utf-8")
    assert "generate_app_registry.py --check" in build
    assert "npm run build" in prebuild
    assert prebuild.index("npm run build") < prebuild.index("generate_app_execution_releases.py")
    assert '"bundle-backend/app-execution-releases.json"' in bundle
    assert "C15" in verifier and "app-attestation-ok" in verifier


def test_frontend_change_changes_execution_release(tmp_path: Path) -> None:
    lock = tmp_path / "lock"
    lock.write_text("x", encoding="utf-8")
    first = build_attestation(_index(), _install(), "desktop-daemon", [lock], _frontend())
    changed = [{"path": "index.html", "sha256": "1" * 64}]
    second = build_attestation(_index(), _install(), "desktop-daemon", [lock], changed)
    assert first["host_build_digest"] != second["host_build_digest"]
    assert first["execution_releases"][0]["execution_release_digest"] != \
        second["execution_releases"][0]["execution_release_digest"]


def test_attestation_verifier_fails_closed_on_tampering(tmp_path: Path) -> None:
    lock = tmp_path / "lock"
    lock.write_text("x", encoding="utf-8")
    result = build_attestation(_index(), _install(), "desktop-daemon", [lock], _frontend())
    verify_attestation(_install(), result)

    changed_identity = deepcopy(result)
    changed_identity["execution_releases"][0]["identity"]["profile"] = "hive"
    with pytest.raises(ValueError, match="profile mismatch"):
        verify_attestation(_install(), changed_identity)

    unsafe_frontend = deepcopy(result)
    unsafe_frontend["frontend_files"][0]["path"] = "../index.html"
    with pytest.raises(ValueError, match="frontend path"):
        verify_attestation(_install(), unsafe_frontend)


def test_attestation_verifier_normalizes_malformed_install_errors(tmp_path: Path) -> None:
    lock = tmp_path / "lock"
    lock.write_text("x", encoding="utf-8")
    result = build_attestation(_index(), _install(), "desktop-daemon", [lock], _frontend())
    malformed = deepcopy(_install())
    malformed.pop("files")
    with pytest.raises(ValueError, match="file evidence"):
        verify_attestation(malformed, result)
