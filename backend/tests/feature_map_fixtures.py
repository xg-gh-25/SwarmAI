"""Shared fixtures for the Feature Map tests: an isolated app data folder, a real git
repo with a feature folder, and the pinned runtime (real Node, Playwright, Chromium).

The replay tests run the real runtime against a real page served by ``python3 -m
http.server`` in the sandbox: they prove the product's own behaviour (resolution of the
pinned Playwright, the browser's network block, viewports, flaky), so they cannot stub
the browser. They are not in the CI BVT allowlist (CI has no browser runtime); they run
locally and in the pipeline's TEST of a change to core/feature_map. The runtime is
installed once into the person's real app data folder (``runtime.ensure``) — a cache,
like the package caches — and pointed at with ``SWARM_FEATURE_MAP_RUNTIME``; everything
else the tests write goes to a temp app data folder.
"""
from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


@pytest.fixture
def app_data(tmp_path, monkeypatch):
    d = tmp_path / "appdata"
    d.mkdir()
    monkeypatch.setenv("SWARM_DATA_DIR", str(d))
    no_metadata_service(monkeypatch)
    return d


def no_metadata_service(monkeypatch) -> None:
    """These tests replay the product's own fixtures, as the person: the machine (a Hive, a
    cloud CI runner) is taken as one that does not need the replay user. The replay user
    has its own tests (test_feature_map_replay_user.py)."""
    from core.feature_map import replay_user
    monkeypatch.setattr(replay_user, "needs_replay_user", lambda: False)
    monkeypatch.setattr(replay_user, "_current", None)


@pytest.fixture(scope="session")
def pinned_runtime():
    """The real pinned runtime folder (installed on first use)."""
    from core.feature_map import runtime
    override = os.environ.get("SWARM_FEATURE_MAP_RUNTIME")
    if override:
        return Path(override)
    rt = runtime.ensure()          # the person's real app data runtime cache
    return rt.folder


@pytest.fixture
def runtime_env(pinned_runtime, monkeypatch):
    monkeypatch.setenv("SWARM_FEATURE_MAP_RUNTIME", str(pinned_runtime))
    return pinned_runtime


PAGE = """<!doctype html><html><body style="margin:0">
<button data-testid="open" onclick="document.getElementById('pop').style.display='block'">Open</button>
<div id="pop" data-testid="pop" role="dialog" style="display:none;position:absolute;left:1100px;top:100px;width:300px;height:100px">Popover</div>
<h1 data-testid="title">Hello</h1>
</body></html>
"""

TARGET = """id: web
kind: web
services:
  - {id: ui, argv: [python3, -m, http.server, "{port.ui}", --bind, 127.0.0.1], ready: "http://127.0.0.1:{port.ui}/"}
base_url: "http://127.0.0.1:{port.ui}/"
root: site/
start_states:
  marked: {setup: [[python3, -c, "open('marked.txt','w').write('1')"]]}
"""


def make_repo(root: Path, *, specs: dict[str, str], features: str, target: str = TARGET,
              page: str = PAGE, extra: dict[str, str] | None = None) -> Path:
    """A git repo with ``site/index.html`` and a feature folder; committed."""
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    (root / "site").mkdir(exist_ok=True)
    (root / "site" / "index.html").write_text(page, encoding="utf-8")
    folder = root / ".swarmai" / "features"
    (folder / "targets").mkdir(parents=True, exist_ok=True)
    (folder / "specs").mkdir(exist_ok=True)
    (folder / "targets" / "web.yaml").write_text(target, encoding="utf-8")
    (folder / "features.yaml").write_text(textwrap.dedent(features), encoding="utf-8")
    for name, body in specs.items():
        (folder / "specs" / name).write_text(textwrap.dedent(body), encoding="utf-8")
    for rel, body in (extra or {}).items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    return root
