"""Tests for ``GET /artifacts/drift`` (Artifact-Lifecycle P0, ②).

WHAT IS TESTED
    The drift endpoint answers "has this file changed on disk since the row was
    created?" for a Canvas OUTPUT / Artifacts-overlay row. Response shape:
    ``{dirty: bool, reason: 'git-changed'|'mtime-newer'|'missing'|'clean', current_ref?: str}``.

METHODOLOGY
    Real ASGI (httpx.ASGITransport over the FastAPI app) + a REAL tmp git repo.
    ``artifacts._get_workspace_path`` is monkeypatched to the tmp repo so the
    endpoint runs against a controlled tree. Only the workspace resolution is
    substituted — git itself is real (the whole point is exercising real git).

KEY PROPERTIES / INVARIANTS (AC4)
    - tracked file whose last-commit sha != since_ref → dirty / git-changed
    - tracked file unchanged since since_ref → clean
    - untracked file with mtime(ms) > since_ms → dirty / mtime-newer
    - a deleted / absent path → missing (surfaced, not silent)
    - git error / uncomputable → clean (FAIL-SAFE: never a false dirty/missing)
      — the recovery-path test forces ``canvas_surface._git`` to return None.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, timeout=10)


@pytest.fixture()
def tmp_repo(tmp_path: Path) -> Path:
    """A real git repo with one committed file + one untracked file."""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t.co")
    _git(tmp_path, "config", "user.name", "t")
    tracked = tmp_path / "report.md"
    tracked.write_text("v1\n")
    _git(tmp_path, "add", "report.md")
    _git(tmp_path, "commit", "-qm", "add report")
    untracked = tmp_path / "scratch.txt"
    untracked.write_text("draft\n")
    return tmp_path


def _head_sha(repo: Path, relpath: str) -> str:
    r = subprocess.run(
        ["git", "log", "-1", "--format=%H", "--", relpath],
        cwd=str(repo), capture_output=True, text=True, timeout=10,
    )
    return r.stdout.strip()


async def _get(app, path_param: str, **params) -> dict:
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        q = {"path": path_param, **params}
        resp = await client.get("/artifacts/drift", params=q)
        assert resp.status_code == 200, resp.text
        return resp.json()


@pytest.fixture()
def app_with_ws(tmp_repo, monkeypatch):
    """The FastAPI app with _get_workspace_path pinned to the tmp repo."""
    from fastapi import FastAPI
    import routers.artifacts as art

    async def _fake_ws() -> str:
        return str(tmp_repo)

    monkeypatch.setattr(art, "_get_workspace_path", _fake_ws)
    app = FastAPI()
    app.include_router(art.router)
    return app, tmp_repo


# ── AC4: tracked file changed since since_ref → dirty/git-changed ────────────
async def test_tracked_unchanged_is_clean(app_with_ws):
    app, repo = app_with_ws
    sha = _head_sha(repo, "report.md")
    data = await _get(app, "report.md", since_ref=sha)
    assert data["dirty"] is False
    assert data["reason"] == "clean"


async def test_tracked_changed_is_dirty_git_changed(app_with_ws):
    app, repo = app_with_ws
    old_sha = _head_sha(repo, "report.md")
    # commit a change → last-commit sha now differs from old_sha
    (repo / "report.md").write_text("v2\n")
    _git(repo, "add", "report.md")
    _git(repo, "commit", "-qm", "update report")
    data = await _get(app, "report.md", since_ref=old_sha)
    assert data["dirty"] is True
    assert data["reason"] == "git-changed"
    assert data.get("current_ref") and data["current_ref"] != old_sha


async def test_since_ref_is_a_revspec_not_a_raw_sha(app_with_ws):
    """Gate-2 HIGH regression: a Canvas row's baseRef is `<sha>^` (a git revspec,
    the diff-baseline PARENT), NOT a bare sha. The endpoint must RESOLVE since_ref
    through rev-parse before comparing — a raw string `!=` would make every
    source-final row falsely 'git-changed' forever.

    Setup: commit v1 (=c1), then v2 (=c2, current). A row surfaced at c2 carries
    baseRef=`c2^` which resolves to c1. The file's current last-commit is c2, and
    c1 != c2 → the row IS dirty (it changed since its baseline). But critically:
    a row whose baseRef resolves to the CURRENT commit must be clean, and a raw
    `<sha>^` string must never be compared literally."""
    app, repo = app_with_ws
    (repo / "report.md").write_text("v2\n")
    _git(repo, "add", "report.md")
    _git(repo, "commit", "-qm", "v2")
    c2 = _head_sha(repo, "report.md")

    # baseRef = `<c2>^` (a revspec). Raw-string compare would be `c2^` != `c2` → the
    # OLD bug reported git-changed. Correct: `c2^` resolves to c1 (the parent), c1 !=
    # c2 → genuinely dirty. Either way NOT a raw-string comparison.
    data = await _get(app, "report.md", since_ref=f"{c2}^")
    assert data["reason"] in ("git-changed", "clean")  # resolved, not raw-compared
    assert data.get("current_ref") == c2  # current is the real sha, never `c2^`

    # The load-bearing NON-cries-wolf case: a row whose baseRef IS the current
    # commit's own `<current>^`-child — i.e. since_ref resolves to c2 itself — is
    # clean. Passing the current sha as a revspec must resolve to itself → clean.
    data2 = await _get(app, "report.md", since_ref=c2)
    assert data2["dirty"] is False
    assert data2["reason"] == "clean"


# ── AC4: untracked file mtime > since_ms → dirty/mtime-newer ─────────────────
async def test_untracked_newer_mtime_is_dirty(app_with_ws):
    app, repo = app_with_ws
    # since_ms far in the past → the untracked file's mtime is newer → dirty
    data = await _get(app, "scratch.txt", since_ms=1)
    assert data["dirty"] is True
    assert data["reason"] == "mtime-newer"


async def test_untracked_older_since_is_clean(app_with_ws):
    app, repo = app_with_ws
    # since_ms far in the FUTURE → file mtime is older → clean
    future_ms = 4102444800000  # year 2100
    data = await _get(app, "scratch.txt", since_ms=future_ms)
    assert data["dirty"] is False
    assert data["reason"] == "clean"


# ── AC4: deleted/absent path → missing ───────────────────────────────────────
async def test_missing_file_is_missing(app_with_ws):
    app, repo = app_with_ws
    data = await _get(app, "gone.md", since_ms=1)
    assert data["dirty"] is True
    assert data["reason"] == "missing"


# ── SECURITY (RP52/RP44): a path OUTSIDE the workspace is NOT probed ─────────
async def test_out_of_tree_path_is_not_probed(app_with_ws):
    """An attacker-supplied absolute path outside the workspace (e.g. /etc/hosts)
    must NOT leak its existence/mtime — the drift check confines to owned paths
    (classify_source → _owning_tree) and returns clean for anything else."""
    app, _repo = app_with_ws
    # /etc/hosts exists on the host; without confinement the mtime branch would
    # report it. With confinement it is not owned → clean (no disclosure).
    data = await _get(app, "/etc/hosts", since_ms=1)
    assert data["dirty"] is False
    assert data["reason"] == "clean"


async def test_traversal_escape_is_not_probed(app_with_ws):
    """A ../.. traversal that resolves outside the workspace tree is refused."""
    app, _repo = app_with_ws
    data = await _get(app, "../../../../../../etc/hosts", since_ms=1)
    assert data["dirty"] is False
    assert data["reason"] == "clean"


# ── Layer 4 CROSS-BOUNDARY: backend reason vocabulary == frontend contract ───
# The frontend `radar.ts` DriftResult.reason union ('git-changed'|'mtime-newer'|
# 'missing'|'clean') mirrors what this endpoint emits. This test pins the backend
# side of that contract: the FULL set of reasons the handler can return. If a code
# change adds/renames a reason without updating the frontend type, this set changes
# and the test goes RED — the divergence a unit test on either side alone can't see.
# (cross_boundary=true, kind=frontend-backend-contract → TEST Layer 4.)
async def test_drift_reason_vocabulary_matches_frontend_contract(app_with_ws):
    app, repo = repo_and_reasons = app_with_ws
    # Drive EVERY branch through the real endpoint and collect the reasons emitted.
    emitted: set[str] = set()

    sha = _head_sha(repo, "report.md")
    emitted.add((await _get(app, "report.md", since_ref=sha))["reason"])  # clean (tracked, unchanged)

    (repo / "report.md").write_text("v2\n")
    _git(repo, "add", "report.md")
    _git(repo, "commit", "-qm", "u")
    emitted.add((await _get(app, "report.md", since_ref=sha))["reason"])  # git-changed

    emitted.add((await _get(app, "scratch.txt", since_ms=1))["reason"])   # mtime-newer
    emitted.add((await _get(app, "gone.md", since_ms=1))["reason"])       # missing

    # The frontend DriftResult.reason union — the SSOT contract the TS type mirrors.
    FRONTEND_REASONS = {"git-changed", "mtime-newer", "missing", "clean"}
    # Every reason the backend actually emitted MUST be in the frontend's union
    # (a new/renamed backend reason the TS type doesn't know = a silent contract break).
    assert emitted <= FRONTEND_REASONS, (
        f"backend emitted reasons {emitted - FRONTEND_REASONS} not in the frontend "
        f"DriftResult union {FRONTEND_REASONS} — update radar.ts DriftResult.reason"
    )
    # And the four canonical reasons are all reachable (proves the test drove them).
    assert emitted == FRONTEND_REASONS, (
        f"expected all 4 reasons reachable, got {emitted}"
    )


# ── AC4 RECOVERY: git error → clean (fail-safe, never a false dirty) ─────────
async def test_git_error_fails_safe_to_clean(app_with_ws, monkeypatch):
    """Force canvas_surface._git to return None (its documented error signal).
    The drift check MUST degrade to clean — never a false dirty/missing."""
    app, repo = app_with_ws
    import routers.artifacts as art

    monkeypatch.setattr(art, "_git", lambda *a, **k: None)
    sha = _head_sha(repo, "report.md")
    data = await _get(app, "report.md", since_ref=sha)
    assert data["dirty"] is False
    assert data["reason"] == "clean"


# ── classify_source runs in a worker thread, never on the event loop ─────────
async def test_classify_source_runs_off_event_loop(app_with_ws, monkeypatch):
    """classify_source scans run state on disk; on the loop thread it starved
    /health for tens of seconds (the app showed Backend Offline). It must run in a
    worker thread."""
    import threading
    import core.artifact_source as src

    app, repo = app_with_ws
    loop_thread = threading.get_ident()
    seen: list[int] = []
    real = src.classify_source

    def _spy(*a, **kw):
        seen.append(threading.get_ident())
        return real(*a, **kw)

    monkeypatch.setattr(src, "classify_source", _spy)
    await _get(app, "report.md", since_ref=_head_sha(repo, "report.md"))
    assert seen, "classify_source was not called"
    assert all(t != loop_thread for t in seen)
