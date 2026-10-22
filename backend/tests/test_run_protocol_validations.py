"""Tests Pipeline v1 base-pinned validation manifest selection.

Methodology: injected git reader and checked-in manifest bytes; no subprocess or network.
"""
from __future__ import annotations

import json

import pytest

from core.run_protocol.validations import ValidationError, load_manifest, manifest_digest, required_for

BASE = "a" * 40


def _manifest():
    return {"schema": 1, "entries": [
        {"id": "backend", "paths": ["backend/**/*.py"], "cwd": ".",
         "argv": ["python", "smoke.py"], "call_type": "build"},
        {"id": "desktop", "paths": ["desktop/src/**"], "cwd": "desktop",
         "argv": ["npx", "tsc"], "call_type": "typecheck"},
    ]}


def test_load_reads_exact_base_not_worktree():
    seen = []
    value = load_manifest("/repo", BASE, read_git=lambda root, spec: (
        seen.append((root, spec)) or json.dumps(_manifest()).encode()))
    assert seen == [("/repo", BASE + ":validations.json")]
    assert value["entries"][0]["id"] == "backend"


def test_required_entries_match_zero_or_nested_directories():
    manifest = _manifest()
    assert [x["id"] for x in required_for(manifest, ["backend/main.py"])] == ["backend"]
    assert [x["id"] for x in required_for(manifest, ["backend/core/x.py", "desktop/src/a.ts"])] == ["backend", "desktop"]


def test_manifest_digest_is_order_stable_for_objects():
    a = _manifest()
    b = json.loads(json.dumps(a, sort_keys=True))
    assert manifest_digest(a) == manifest_digest(b)


@pytest.mark.parametrize("mutate", [
    lambda m: m.update(schema=2),
    lambda m: m["entries"].append(dict(m["entries"][0])),
    lambda m: m["entries"][0].update(paths=["../secret"]),
    lambda m: m["entries"][0].update(argv=[]),
    lambda m: m["entries"][0].update(call_type="test"),
])
def test_malformed_manifest_fails_closed(mutate):
    value = _manifest()
    mutate(value)
    with pytest.raises(ValidationError):
        required_for(value, ["backend/a.py"])


# ── B6 / G-2: a repo without validations.json, and an empty list ────────────

def test_a_base_without_validations_json_requires_nothing(tmp_path):
    import subprocess
    from core.run_protocol.validations import load_manifest, required_for
    repo = tmp_path / "r"; repo.mkdir()
    run = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True,
                                    capture_output=True, text=True).stdout.strip()
    run("init", "-q"); run("config", "user.email", "a@b.c"); run("config", "user.name", "T")
    (repo / "a.py").write_text("x = 1\n"); run("add", "."); run("commit", "-qm", "base")
    manifest = load_manifest(str(repo), run("rev-parse", "HEAD"))
    assert manifest == {"schema": 1, "entries": []}
    assert required_for(manifest, ["a.py"]) == []


def test_an_empty_entry_list_is_a_valid_manifest():
    from core.run_protocol.validations import validate_manifest
    assert validate_manifest({"schema": 1, "entries": []})["entries"] == []
