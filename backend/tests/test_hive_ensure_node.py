"""hive/ensure-node.sh: the Hive's default Node is the newest installed one, and an
install fails when even that one is older than the frontend needs (design
docs/design/2026-10-31-swarm-out-single-loop-fixes.md §2.7).

Run against fake ``node`` binaries in a temporary directory (ENSURE_NODE_SEARCH /
ENSURE_NODE_BIN_DIR): no real Node and no root needed.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "hive" / "ensure-node.sh"


def _fake(directory: Path, name: str, version: str) -> None:
    p = directory / name
    p.write_text(f"#!/bin/sh\necho v{version}\n")
    p.chmod(0o755)


def _run(search: Path, links: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, ENSURE_NODE_SEARCH=str(search), ENSURE_NODE_BIN_DIR=str(links))
    return subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=30)


def test_the_newest_node_becomes_the_default_with_its_own_npm(tmp_path):
    search, links = tmp_path / "usr-bin", tmp_path / "local-bin"
    search.mkdir()
    _fake(search, "node", "18.20.0")              # the one the plain npm package brings
    _fake(search, "npm", "10.0.0")
    _fake(search, "node-20", "20.19.5")
    _fake(search, "npm-20", "10.8.2")
    _fake(search, "npx-20", "10.8.2")
    got = _run(search, links)
    assert got.returncode == 0, got.stderr
    assert os.readlink(links / "node") == str(search / "node-20")
    assert os.readlink(links / "npm") == str(search / "npm-20")
    assert os.readlink(links / "npx") == str(search / "npx-20")


def test_an_install_whose_newest_node_is_too_old_fails_and_says_why(tmp_path):
    search, links = tmp_path / "usr-bin", tmp_path / "local-bin"
    search.mkdir()
    _fake(search, "node", "18.20.0")
    _fake(search, "node-20", "20.18.1")
    got = _run(search, links)
    assert got.returncode == 1
    assert "20.18.1" in got.stderr and ">= 20.19.0" in got.stderr


def test_no_node_at_all_fails(tmp_path):
    search, links = tmp_path / "usr-bin", tmp_path / "local-bin"
    search.mkdir()
    assert _run(search, links).returncode == 1


@pytest.mark.parametrize("versions,want", [(("20.19.0", "22.1.0"), "node-22"), (("20.10.0", "20.9.9"), "node-20")])
def test_versions_compare_as_numbers(tmp_path, versions, want):
    search, links = tmp_path / "usr-bin", tmp_path / "local-bin"
    search.mkdir()
    _fake(search, "node-20", versions[0])
    _fake(search, "node-22" if want == "node-22" else "node-21", versions[1])
    got = _run(search, links, "1.0.0")
    assert got.returncode == 0, got.stderr
    assert os.readlink(links / "node") == str(search / want)


def test_the_minimum_is_the_frontends_declared_engine():
    engines = json.loads((ROOT / "desktop" / "package.json").read_text())["engines"]["node"]
    script_min = re.search(r'^MIN_NODE="([0-9.]+)"', SCRIPT.read_text(), re.M).group(1)
    assert engines == f">={script_min}"


def test_provision_and_update_run_it():
    from hive import user_data
    assert 'bash "$INSTALL_DIR/hive/ensure-node.sh"' in Path(user_data.__file__).read_text()
    provisioner = (ROOT / "backend" / "hive" / "provisioner.py").read_text()
    assert "bash /opt/swarmai/hive/ensure-node.sh ||" in provisioner      # the update warns, never rolls back
