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

from core.app_registry import AppRegistry
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
    assert payload["apps"][0]["app_id"] == "pollinate"
    assert payload["apps"][0]["evidence_state"] == "catalog-only"
    assert payload["apps"][0]["lifecycle_controls"] == []
    assert len(payload["apps"][0]["package_release_digest"]) == 64


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
    assert body["apps"][0]["app_id"] == "pollinate"
    assert {p["profile"] for p in body["apps"][0]["profile_accounting"]} == {
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
    assert response.json()["apps"][0]["app_id"] == "pollinate"
    assert response.json()["apps"][0]["evidence_state"] == "catalog-only"


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
