"""Tests for App Platform Phase A manifest and release invariants.

- Canonical JSON follows the RFC 8785 subset used by the schema.
- Package digests are independent of object and declaration order.
- Closure rejects traversal, missing sources, symlinks, and path collisions.
- Runtime-profile accounting is required-resource fail-closed.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.app_manifest import (
    AppManifest,
    AppManifestError,
    build_package_release,
    canonical_json,
)
from core.app_profiles import account_profiles, current_profile


def _manifest(resources: list[dict]) -> dict:
    return {
        "schemaVersion": 1,
        "id": "sample",
        "version": "1.0.0",
        "displayName": "Sample",
        "description": "A sample App",
        "instanceMode": "singleton",
        "brainInterface": None,
        "resources": resources,
        "dependencies": [],
        "permissionTemplates": {},
        "distribution": {"targets": [], "visibility": "internal"},
    }


def _resource(source: str = "payload.txt") -> dict:
    return {
        "id": "skill:sample",
        "type": "skill",
        "source": source,
        "required": True,
        "permissions": [],
        "targets": {
            "swarmai": {
                "binding": "builtin:skill:sample",
                "profiles": {
                    "desktop-daemon": {"supported": True, "requires": ["agent-skills"]},
                },
            }
        },
        "fixtures": [],
    }


def test_canonical_json_uses_utf16_key_order_and_rejects_floats() -> None:
    # RFC 8785 sorts object properties by UTF-16 code units. U+10000 sorts before
    # U+E000 in UTF-16 even though Python code-point order does the opposite.
    assert canonical_json({"\ue000": 1, "\U00010000": 2}) == '{"𐀀":2,"":1}'
    with pytest.raises(AppManifestError, match="floating-point"):
        canonical_json({"unsafe": 1.5})


def test_canonical_json_preserves_non_path_unicode_and_paths_use_nfc() -> None:
    decomposed = "Cafe\u0301"
    composed = "Café"
    assert canonical_json({"value": decomposed}) != canonical_json({"value": composed})

    raw = _manifest([_resource("dir/Cafe\u0301.txt")])
    raw["permissionTemplates"] = {
        "default": {"files": {"read": ["Knowledge/Cafe\u0301/**"], "write": []}}
    }
    manifest = AppManifest.model_validate(raw)
    assert manifest.resources[0].source == "dir/Café.txt"
    assert manifest.permission_templates["default"].files.read == ["Knowledge/Café/**"]


def test_package_digest_is_stable_across_manifest_key_order(tmp_path: Path) -> None:
    (tmp_path / "payload.txt").write_text("hello\n", encoding="utf-8")
    raw = _manifest([_resource()])
    reordered = {key: raw[key] for key in reversed(list(raw))}
    left = build_package_release(AppManifest.model_validate(raw), tmp_path)
    right = build_package_release(AppManifest.model_validate(reordered), tmp_path)
    assert left.package_release_digest == right.package_release_digest
    assert left.files[0].path == "payload.txt"
    assert len(left.package_release_digest) == 64


def test_closure_rejects_traversal_missing_and_symlink(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-app-proof.txt"
    outside.write_text("outside", encoding="utf-8")
    try:
        with pytest.raises(Exception):
            AppManifest.model_validate(_manifest([_resource("../outside-app-proof.txt")]))
        with pytest.raises(AppManifestError, match="does not exist"):
            build_package_release(
                AppManifest.model_validate(_manifest([_resource("missing.txt")])), tmp_path
            )
        (tmp_path / "link.txt").symlink_to(outside)
        with pytest.raises(AppManifestError, match="symlink"):
            build_package_release(
                AppManifest.model_validate(_manifest([_resource("link.txt")])), tmp_path
            )
    finally:
        outside.unlink(missing_ok=True)


def test_closure_rejects_cross_platform_path_collision(tmp_path: Path) -> None:
    (tmp_path / "A.txt").write_text("a", encoding="utf-8")
    (tmp_path / "a.txt").write_text("b", encoding="utf-8")
    raw = _manifest([_resource("A.txt"), {**_resource("a.txt"), "id": "skill:other"}])
    with pytest.raises(AppManifestError, match="collision"):
        build_package_release(AppManifest.model_validate(raw), tmp_path)


def test_unknown_launcher_mode_has_no_current_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARMAI_MODE", "invented")
    assert current_profile() is None


def test_profile_accounting_is_required_resource_fail_closed() -> None:
    manifest = AppManifest.model_validate(_manifest([_resource()]))
    reports = {r.profile: r for r in account_profiles(manifest)}
    assert reports["desktop-daemon"].supported is True
    assert reports["desktop-subprocess"].supported is False
    assert reports["hive"].supported is False
    assert reports["hive"].blocked_resources == ("skill:sample",)


def test_manifest_rejects_duplicate_resource_ids_and_unknown_fields() -> None:
    with pytest.raises(Exception):
        AppManifest.model_validate(_manifest([_resource(), _resource()]))
    raw = _manifest([_resource()])
    raw["invented"] = True
    with pytest.raises(Exception):
        AppManifest.model_validate(raw)


def test_closure_rejects_exact_duplicate_path_ownership(tmp_path: Path) -> None:
    (tmp_path / "payload.txt").write_text("x", encoding="utf-8")
    raw = _manifest([_resource(), {**_resource(), "id": "skill:other"}])
    with pytest.raises(AppManifestError, match="declared by multiple resources"):
        build_package_release(AppManifest.model_validate(raw), tmp_path)


def test_package_digest_ignores_set_like_declaration_order(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")
    first = _resource("a.txt")
    first["permissions"] = ["z", "a"]
    first["targets"]["swarmai"]["profiles"]["desktop-daemon"]["requires"] = [
        "agent-skills", "api-client"
    ]
    second = {**_resource("b.txt"), "id": "skill:other"}
    left = AppManifest.model_validate(_manifest([first, second]))
    right_raw = _manifest([second, first])
    right_raw["resources"][1]["permissions"] = ["a", "z"]
    right_raw["resources"][1]["targets"]["swarmai"]["profiles"]["desktop-daemon"]["requires"] = [
        "api-client", "agent-skills"
    ]
    right = AppManifest.model_validate(right_raw)
    assert build_package_release(left, tmp_path).package_release_digest == \
        build_package_release(right, tmp_path).package_release_digest


def test_manifest_rejects_hidden_source_paths() -> None:
    with pytest.raises(Exception):
        AppManifest.model_validate(_manifest([_resource(".git/config")]))


def test_closure_rejects_hidden_nested_local_state(tmp_path: Path) -> None:
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "ok.txt").write_text("ok", encoding="utf-8")
    (payload / ".env").write_text("SECRET=x", encoding="utf-8")
    with pytest.raises(AppManifestError, match="hidden path"):
        build_package_release(
            AppManifest.model_validate(_manifest([_resource("payload")])), tmp_path
        )
