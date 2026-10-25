"""Tests for the Phase A generated App index and read-only API.

The source manifests are build inputs; runtime reads one immutable generated index and
never scans manifests. Pollinate remains catalog-only and exposes no lifecycle action.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.app_registry import AppRegistry, UpstreamSource, UpstreamSourceEntry
from routers.apps import create_router
from scripts.generate_app_registry import _validate_binding_sources, render_outputs


ROOT = Path(__file__).resolve().parents[2]


def test_generated_app_outputs_are_fresh() -> None:
    expected = render_outputs(ROOT)
    for relative, content in expected.items():
        assert (ROOT / relative).read_text(encoding="utf-8") == content, relative


def test_generated_index_has_pollinate_and_no_lifecycle_controls() -> None:
    payload = json.loads((ROOT / "backend/generated/apps_index.json").read_text())
    assert payload["schema_version"] == 1
    apps = {app["app_id"]: app for app in payload["apps"]}
    assert apps["pollinate"]["evidence_state"] == "catalog-only"
    assert apps["pollinate"]["lifecycle_controls"] == []
    assert len(apps["pollinate"]["package_release_digest"]) == 64


def test_apps_api_is_read_only_and_reports_all_profiles(tmp_path: Path) -> None:
    source = ROOT / "backend/generated/apps_index.json"
    index = tmp_path / "apps_index.json"
    index.write_bytes(source.read_bytes())
    registry = AppRegistry(index)
    app = FastAPI()
    app.include_router(create_router(registry), prefix="/api/apps")
    client = TestClient(app)

    response = client.get("/api/apps")
    assert response.status_code == 200
    body = response.json()
    catalog = {item["app_id"]: item for item in body["apps"]}
    assert "pollinate" in catalog
    assert {p["profile"] for p in catalog["pollinate"]["profile_accounting"]} == {
        "desktop-daemon", "desktop-subprocess", "hive"
    }
    detail = client.get("/api/apps/pollinate")
    assert detail.status_code == 200
    assert detail.json()["lifecycle_controls"] == []
    assert client.post("/api/apps/pollinate/enable").status_code == 404


def test_registry_loads_index_once_without_manifest_scan(tmp_path: Path) -> None:
    index = tmp_path / "apps_index.json"
    index.write_text(json.dumps({
        "schema_version": 1,
        "host_contract_version": "1",
        "binding_map_digest": "0" * 64,
        "apps": [],
    }), encoding="utf-8")
    registry = AppRegistry(index)
    assert registry.list_apps() == ()
    index.write_text("not-json", encoding="utf-8")
    assert registry.list_apps() == ()


def test_main_application_mounts_read_only_apps_api(client) -> None:
    response = client.get("/api/apps")
    assert response.status_code == 200
    apps = {app["app_id"]: app for app in response.json()["apps"]}
    assert apps["pollinate"]["evidence_state"] == "catalog-only"


def test_generated_bindings_reject_unresolved_backend_router(tmp_path: Path) -> None:
    source = tmp_path / "backend/routers/missing.py"
    source.parent.mkdir(parents=True)
    source.write_text("value = 1\n", encoding="utf-8")
    rows = [{
        "app_id": "sample",
        "resource_id": "backend:api",
        "resource_type": "backend",
        "binding": "builtin:router:missing",
        "source": "backend/routers/missing.py",
    }]
    with pytest.raises(RuntimeError, match="unresolved backend router"):
        _validate_binding_sources(tmp_path, rows)


def test_generated_bindings_reject_wrong_implementation_root(tmp_path: Path) -> None:
    source = tmp_path / "backend/FakeSurface.tsx"
    source.parent.mkdir(parents=True)
    source.write_text("export const Fake = 1;\n", encoding="utf-8")
    rows = [{
        "app_id": "sample",
        "resource_id": "surface:fake",
        "resource_type": "surface",
        "binding": "builtin:surface:fake",
        "source": "backend/FakeSurface.tsx",
    }]
    with pytest.raises(RuntimeError, match="outside desktop/src"):
        _validate_binding_sources(tmp_path, rows)


# ===========================================================================
# Invalid provenance rejection tests
# ===========================================================================


def test_invalid_provenance_rejection(tmp_path: Path) -> None:
    """Apps with invalid PROVENANCE.json should be rejected at generation time."""
    from scripts.generate_app_registry import _load_provenance
    from core.app_provenance import AppProvenanceError

    # Create an app directory with invalid PROVENANCE.json
    app_dir = tmp_path / "backend/apps/bad-app"
    package_dir = app_dir / "package"
    package_dir.mkdir(parents=True)

    # Invalid JSON
    (package_dir / "PROVENANCE.json").write_text("{not valid json}", encoding="utf-8")

    with pytest.raises(AppProvenanceError, match="invalid App provenance"):
        _load_provenance(app_dir)


def test_provenance_file_digest_mismatch_rejected(tmp_path: Path) -> None:
    """Provenance with wrong file digest should be rejected."""
    from scripts.generate_app_registry import _load_provenance
    from core.app_provenance import AppProvenanceError
    import json

    # Create an app directory with PROVENANCE.json that has wrong digest
    app_dir = tmp_path / "backend/apps/bad-digest-app"
    package_dir = app_dir / "package"
    (package_dir / "upstream").mkdir(parents=True)

    # Create a file with known content
    (package_dir / "upstream/file.txt").write_text("actual content", encoding="utf-8")

    # Create PROVENANCE.json with wrong SHA-256
    provenance = {
        "schemaVersion": 1,
        "sources": [{
            "id": "main",
            "repository": "https://github.com/example/repo",
            "commit": "a" * 40,
            "license": "MIT",
        }],
        "files": [{
            "source": "main",
            "upstreamPath": "file.txt",
            "localPath": "upstream/file.txt",
            "sha256": "0" * 64,  # Wrong digest
        }],
    }
    (package_dir / "PROVENANCE.json").write_text(json.dumps(provenance), encoding="utf-8")

    with pytest.raises(AppProvenanceError, match="digest mismatch"):
        _load_provenance(app_dir)


# ===========================================================================
# Source tier derivation tests - ensures source_tier derived correctly
# ===========================================================================


def test_provenance_outside_canonical_package_path_is_rejected(tmp_path: Path) -> None:
    from core.app_provenance import AppProvenanceError
    from scripts.generate_app_registry import _load_provenance

    app_dir = tmp_path / "backend/apps/misplaced"
    wrong = app_dir / "other/PROVENANCE.json"
    wrong.parent.mkdir(parents=True)
    wrong.write_text("{}", encoding="utf-8")
    with pytest.raises(AppProvenanceError, match="package/PROVENANCE.json"):
        _load_provenance(app_dir)


def test_source_tier_derived_for_all_four_apps() -> None:
    """Verify source tiers for Pollinate and the four external canaries."""
    payload = json.loads((ROOT / "backend/generated/apps_index.json").read_text())
    apps_by_id = {app["app_id"]: app for app in payload["apps"]}

    # Pollinate: no provenance, source_tier=built-in
    assert apps_by_id["pollinate"]["source_tier"] == "built-in"
    assert "upstream_source" not in apps_by_id["pollinate"] or apps_by_id["pollinate"]["upstream_source"] is None

    # Domain-modeling: has provenance, source_tier=external
    assert apps_by_id["domain-modeling"]["source_tier"] == "external"
    assert "upstream_source" in apps_by_id["domain-modeling"]
    assert apps_by_id["domain-modeling"]["upstream_source"]["sources"][0]["license"]

    # Research: has provenance, source_tier=external
    assert apps_by_id["research"]["source_tier"] == "external"
    assert "upstream_source" in apps_by_id["research"]

    # Frontend-design: has provenance, source_tier=external
    assert apps_by_id["frontend-design"]["source_tier"] == "external"
    assert "upstream_source" in apps_by_id["frontend-design"]

    # PPTX-maker: has provenance, source_tier=external
    assert apps_by_id["pptx-maker"]["source_tier"] == "external"
    assert "upstream_source" in apps_by_id["pptx-maker"]


# ===========================================================================
# Upstream source API mapping tests
# ===========================================================================


def test_upstream_source_included_in_api_response(tmp_path: Path) -> None:
    """Verify upstream_source is serialized in the API response for external apps."""
    source = ROOT / "backend/generated/apps_index.json"
    index = tmp_path / "apps_index.json"
    index.write_bytes(source.read_bytes())
    registry = AppRegistry(index)
    app = FastAPI()
    app.include_router(create_router(registry), prefix="/api/apps")
    client = TestClient(app)

    response = client.get("/api/apps")
    assert response.status_code == 200
    body = response.json()

    # Find an external app (domain-modeling)
    external_apps = [a for a in body["apps"] if a["source_tier"] == "external"]
    assert len(external_apps) >= 1, "Expected at least one external app"

    # Check upstream_source structure for external apps
    for ext_app in external_apps:
        if ext_app.get("upstream_source"):
            sources = ext_app["upstream_source"]["sources"]
            assert len(sources) >= 1
            src = sources[0]
            assert "id" in src
            assert "repository" in src
            assert "commit" in src
            assert len(src["commit"]) == 40  # Full Git commit id
            assert "license" in src


def test_upstream_source_types_model_validation() -> None:
    """Verify UpstreamSource and UpstreamSourceEntry types validate correctly."""
    entry = UpstreamSourceEntry(
        id="main",
        repository="https://github.com/example/repo",
        commit="a" * 40,
        license="MIT",
    )
    assert entry.id == "main"
    assert entry.repository == "https://github.com/example/repo"
    assert entry.commit == "a" * 40
    assert entry.license == "MIT"

    upstream = UpstreamSource(sources=(entry,))
    assert len(upstream.sources) == 1
    assert upstream.sources[0].id == "main"


# ===========================================================================
# PPTX all-profile blocking tests
# ===========================================================================


def test_pptx_maker_all_profiles_blocked() -> None:
    """PPTX-maker has all resources unsupported, so all profiles are blocked."""
    payload = json.loads((ROOT / "backend/generated/apps_index.json").read_text())
    apps_by_id = {app["app_id"]: app for app in payload["apps"]}

    pptx = apps_by_id["pptx-maker"]
    # All profiles should be blocked (supported=False)
    for profile_report in pptx["profile_accounting"]:
        assert profile_report["supported"] is False, (
            f"Expected profile {profile_report['profile']} to be blocked for pptx-maker"
        )

    # Proof blockers should include profile-blocked for each profile
    expected_blockers = {
        "profile-blocked:desktop-daemon",
        "profile-blocked:desktop-subprocess",
        "profile-blocked:hive",
    }
    actual_blockers = set(pptx["proof_blockers"])
    assert expected_blockers.issubset(actual_blockers), (
        f"Expected profile blockers {expected_blockers}, got {actual_blockers}"
    )


# ===========================================================================
# Binding prefix and root enforcement tests
# ===========================================================================


def test_external_skill_binding_rejects_source_outside_app_dir(tmp_path: Path) -> None:
    """External skill binding prefix is invalid when source is NOT under backend/apps/<app_id>/."""
    # Create a skill source outside the app directory (in backend/skills/)
    skill_dir = tmp_path / "backend/skills/s_external"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# External Skill\n", encoding="utf-8")
    (skill_dir / "INSTRUCTIONS.md").write_text("Do things\n", encoding="utf-8")

    # Trying to use external:skill: prefix with source in backend/skills/ should fail
    rows = [{
        "app_id": "my-external-app",
        "resource_id": "skill:s_external",
        "resource_type": "skill",
        "binding": "external:skill:s_external",  # Wrong prefix - source is not under backend/apps/
        "source": "backend/skills/s_external",
        "has_provenance": True,  # Has provenance but source is in builtin area
    }]

    with pytest.raises(RuntimeError, match="unsupported or mismatched binding"):
        _validate_binding_sources(tmp_path, rows)


def test_external_skill_binding_accepts_source_inside_app_dir(tmp_path: Path) -> None:
    """External skill binding is valid when source is under backend/apps/<app_id>/."""
    # Create a skill source inside the app directory
    skill_dir = tmp_path / "backend/apps/my-app/package"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# My Skill\n", encoding="utf-8")
    (skill_dir / "INSTRUCTIONS.md").write_text("Do things\n", encoding="utf-8")

    rows = [{
        "app_id": "my-app",
        "resource_id": "skill:s_my-skill",
        "resource_type": "skill",
        "binding": "external:skill:s_my-skill",
        "source": "backend/apps/my-app/package",
        "has_provenance": True,
    }]

    # Should not raise
    _validate_binding_sources(tmp_path, rows)


def test_builtin_skill_binding_rejects_external_prefix(tmp_path: Path) -> None:
    """Built-in skill (no provenance) cannot use external:skill: prefix."""
    skill_dir = tmp_path / "backend/skills/s_builtin"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Builtin Skill\n", encoding="utf-8")
    (skill_dir / "INSTRUCTIONS.md").write_text("Do things\n", encoding="utf-8")

    rows = [{
        "app_id": "builtin-app",
        "resource_id": "skill:s_builtin",
        "resource_type": "skill",
        "binding": "external:skill:s_builtin",  # Wrong prefix for non-provenance
        "source": "backend/skills/s_builtin",
        "has_provenance": False,  # No provenance, so can't use external prefix
    }]

    with pytest.raises(RuntimeError, match="unsupported or mismatched binding"):
        _validate_binding_sources(tmp_path, rows)


def test_external_app_with_builtin_skill_source_uses_builtin_prefix(tmp_path: Path) -> None:
    """External app (has provenance) using a skill in backend/skills/ uses builtin:skill: prefix."""
    # The frontend-design app is an example of this: has provenance but skill is in backend/skills/
    skill_dir = tmp_path / "backend/skills/s_frontend-design"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Frontend Design\n", encoding="utf-8")
    (skill_dir / "INSTRUCTIONS.md").write_text("Build UIs\n", encoding="utf-8")

    rows = [{
        "app_id": "frontend-design",
        "resource_id": "skill:s_frontend-design",
        "resource_type": "skill",
        "binding": "builtin:skill:s_frontend-design",  # Uses builtin prefix even though has provenance
        "source": "backend/skills/s_frontend-design",  # Source is in builtin skills area
        "has_provenance": True,
    }]

    # Should not raise - this is a valid configuration
    _validate_binding_sources(tmp_path, rows)


def test_external_app_cannot_bind_privileged_backend(tmp_path: Path) -> None:
    source = tmp_path / "backend/apps/external/backend.py"
    source.parent.mkdir(parents=True)
    source.write_text("router = object()\n", encoding="utf-8")
    rows = [{
        "app_id": "external",
        "resource_id": "backend:api",
        "resource_type": "backend",
        "binding": "builtin:router:external",
        "source": "backend/apps/external/backend.py",
        "has_provenance": True,
    }]
    with pytest.raises(RuntimeError, match="instruction-only Skill"):
        _validate_binding_sources(tmp_path, rows)
