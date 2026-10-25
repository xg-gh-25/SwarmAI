"""External App provenance tests.

Covers strict source pins, HTTPS authorities, file containment, symlink rejection,
digest verification, deterministic fields, and real canary package sidecars.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from core.app_provenance import AppProvenanceError, load_verified_provenance


ROOT = Path(__file__).resolve().parents[2]


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write_package(root: Path) -> Path:
    package = root / "package"
    package.mkdir(parents=True)
    source = b"pinned upstream\n"
    (package / "UPSTREAM.md").write_bytes(source)
    (package / "ADAPTATION.md").write_text("local adapter\n", encoding="utf-8")
    payload = {
        "schemaVersion": 1,
        "sources": [{
            "id": "example-source",
            "repository": "https://github.com/example/source",
            "commit": "a" * 40,
            "license": "MIT",
        }],
        "files": [{
            "source": "example-source",
            "upstreamPath": "SKILL.md",
            "localPath": "UPSTREAM.md",
            "sha256": _sha(source),
        }],
        "modifications": [{
            "localPath": "ADAPTATION.md",
            "description": "Host-specific wrapper.",
        }],
    }
    (package / "PROVENANCE.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    return package


def test_verified_provenance_accepts_pinned_local_bytes(tmp_path: Path) -> None:
    provenance = load_verified_provenance(_write_package(tmp_path))
    assert provenance.sources[0].commit == "a" * 40
    assert provenance.files[0].local_path == "UPSTREAM.md"


def test_provenance_rejects_digest_mismatch_and_mutable_ref(tmp_path: Path) -> None:
    package = _write_package(tmp_path)
    (package / "UPSTREAM.md").write_text("changed\n", encoding="utf-8")
    with pytest.raises(AppProvenanceError, match="digest mismatch"):
        load_verified_provenance(package)

    package = _write_package(tmp_path / "second")
    raw = json.loads((package / "PROVENANCE.json").read_text())
    raw["sources"][0]["commit"] = "main"
    (package / "PROVENANCE.json").write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(AppProvenanceError, match="invalid App provenance"):
        load_verified_provenance(package)


def test_provenance_rejects_credentials_escape_and_symlink(tmp_path: Path) -> None:
    package = _write_package(tmp_path)
    raw = json.loads((package / "PROVENANCE.json").read_text())
    raw["sources"][0]["repository"] = "https://token@example.com/source"
    (package / "PROVENANCE.json").write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(AppProvenanceError, match="without credentials"):
        load_verified_provenance(package)

    package = _write_package(tmp_path / "second")
    outside = tmp_path / "outside.md"
    outside.write_text("outside\n", encoding="utf-8")
    (package / "UPSTREAM.md").unlink()
    (package / "UPSTREAM.md").symlink_to(outside)
    with pytest.raises(AppProvenanceError, match="missing or unsafe"):
        load_verified_provenance(package)


def test_real_external_canary_provenance_is_valid() -> None:
    packages = [
        ROOT / "backend/apps/domain-modeling/package",
        ROOT / "backend/apps/research/package",
        ROOT / "backend/apps/frontend-design/package",
        ROOT / "backend/apps/pptx-maker/package",
    ]
    for package in packages:
        provenance = load_verified_provenance(package)
        assert provenance.files, package
        assert all(source.commit for source in provenance.sources)


def test_provenance_rejects_undeclared_package_file(tmp_path: Path) -> None:
    package = _write_package(tmp_path)
    (package / "untracked.py").write_text("print('unexpected')\n", encoding="utf-8")
    with pytest.raises(AppProvenanceError, match="closure mismatch"):
        load_verified_provenance(package)
