"""Tests for canvas_noise.is_noise_path — the shape denylist that keeps machine
scratch out of the Canvas OUTPUTS rail.

Background: is_canvas_surfaceable admits ANY file the session touched outside
SwarmWS (git → external-diff, plain FS → external-nodiff). That had no denylist, so
every temp/cache/state write the agent made became a persistent rail row that ALSO
auto-popped the Canvas — a flood of /private/tmp/*.diff, ~/.claude/**,
~/Library/Caches/**. This module is the SOLE copy of those shapes (the frontend
denylist was deleted in run_4de279ca for drifting; the Layer-2 watcher composes its
pre-filter from NOISE_SEGMENTS rather than re-listing them).

What is pinned here:
  - the noisy roots/segments/extensions DENY,
  - the deliberately-ambiguous names do NOT (a false deny silently loses a row the
    user wanted — worse than one extra row),
  - author-facing subtrees under a denied root are rescued,
  - purity + fail-open (never raises; a relative path is not judged).
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from core.canvas_noise import (
    NOISE_SEGMENTS,
    _SWARM_AUTHOR_FACING,
    _SWARM_STATE_ROOT,
    is_noise_path,
)

HOME = Path.home()
# Derived from the module constants rather than written as a literal, for two reasons.
# (1) SSOT: rename the state dir and these tests follow instead of silently passing on
# a path that no longer exists. (2) ci.yml builds its skip list by grepping test files
# for the app-state dir name and --ignore-ing every match (they are assumed to depend
# on the real home dir). is_noise_path is PURE — it never touches the filesystem, so
# that assumption is false here, and hardcoding the name would make this whole file
# CI-invisible. Keep the literal out of this file.
STATE_ROOT = HOME / _SWARM_STATE_ROOT


# ── OS temp: the reported flood ────────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "/private/tmp/p1.diff",
    "/private/tmp/ev2.json",
    "/private/tmp/dead_code_audit.py",
    "/private/tmp/mcs-telemetry-l1-1786706885.454894_8214.log",
    "/tmp/scratch.md",              # unresolved form (macOS symlinks /tmp → /private/tmp)
    "/var/tmp/x.json",
    "/private/var/tmp/x.json",
])
def test_os_temp_is_noise(path):
    assert is_noise_path(path) is True


def test_platform_tempdir_is_noise():
    """$TMPDIR, whatever it is on this platform — macOS gives a per-user
    /var/folders/**/T dir, which is a DIFFERENT path from /private/tmp and is where
    Python's tempfile (and pytest's tmp_path) actually writes."""
    assert is_noise_path(str(Path(tempfile.gettempdir()) / "out.json")) is True


# ── system / pseudo filesystems ────────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "/etc/passwd", "/private/etc/hosts", "/dev/null", "/proc/self/status",
    "/var/log/system.log", "/usr/local/bin/tool", "/opt/homebrew/bin/git",
    "/System/Library/x.plist", "/Library/Preferences/y.plist",
    "/Applications/Foo.app/Contents/Info.plist",
])
def test_system_roots_are_noise(path):
    assert is_noise_path(path) is True


# ── tool/app state + caches under $HOME ────────────────────────────────────────

@pytest.mark.parametrize("rel", [
    ".claude/shell-snapshots/snapshot-zsh-1.sh",   # the SDK's own churn
    ".claude/history.jsonl",
    ".cache/uv/wheels/x.whl",
    "Library/Caches/pip/http/abc",
    "Library/Logs/DiagnosticReports/crash.ips",
    "Library/Application Support/Code/User/state.json",
    ".npm/_cacache/index-v5/aa",
    ".cargo/registry/src/foo/lib.rs",
    ".Trash/deleted-notes.md",
])
def test_home_state_and_caches_are_noise(rel):
    assert is_noise_path(str(HOME / rel)) is True


@pytest.mark.parametrize("rel", [
    "logs/backend-daemon.log", "data.db", "open_tabs.json", "daemon/swarmai-backend",
])
def test_swarmai_own_state_is_noise(rel):
    assert is_noise_path(str(STATE_ROOT / rel)) is True


# ── the rescue: author-facing subtrees under a denied root ─────────────────────

@pytest.mark.parametrize("rel", _SWARM_AUTHOR_FACING)
def test_author_facing_subtrees_escape_the_swarm_state_root(rel):
    """The app state dir is denied as plumbing, but a skill source under it IS a
    deliverable a user reviews — the exception list must rescue it, or the denylist
    would silently swallow every skill edit. Parametrized over the real constant, so
    adding an exception without a test is impossible."""
    assert is_noise_path(str(HOME / rel / "sub" / "doc.md")) is False


def test_exception_does_not_rescue_noise_shapes_inside_it():
    """The exception rescues from the ROOT rule only. A compiled artifact inside a
    rescued subtree is still noise (segment/extension layers run first)."""
    assert is_noise_path(str(STATE_ROOT / "skills/s_x/__pycache__/m.pyc")) is True
    assert is_noise_path(str(STATE_ROOT / "skills/s_x/node_modules/pkg/index.js")) is True


# ── build/cache segments anywhere in the path ──────────────────────────────────

@pytest.mark.parametrize("path", [
    "/Users/x/Desktop/proj/node_modules/react/index.js",
    "/Users/x/Desktop/proj/__pycache__/mod.cpython-311.pyc",
    "/Users/x/Desktop/proj/.venv/lib/python3.11/site-packages/foo.py",
    "/Users/x/Desktop/proj/.git/index",
    "/Users/x/Desktop/proj/.pytest_cache/v/cache/lastfailed",
    "/Users/x/Desktop/proj/dist/bundle.js",
    "/Users/x/Desktop/proj/build/output.txt",
    "/Users/x/Desktop/proj/.next/server/page.js",
    "/Users/x/Desktop/proj/foo.egg-info/PKG-INFO",
])
def test_build_and_cache_segments_are_noise(path):
    assert is_noise_path(path) is True


# ── extensions + bookkeeping basenames ────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "/Users/x/Desktop/proj/mod.pyc", "/Users/x/Desktop/proj/lib.so",
    "/Users/x/Desktop/proj/lib.dylib", "/Users/x/Desktop/proj/App.class",
    "/Users/x/Desktop/proj/run.log", "/Users/x/Desktop/proj/RUN.LOG",  # case-insensitive
    "/Users/x/Desktop/proj/server.pid", "/Users/x/Desktop/proj/app.sock",
    "/Users/x/Desktop/proj/notes.md.swp", "/Users/x/Desktop/proj/x.tmp",
    "/Users/x/Desktop/proj/old.bak", "/Users/x/Desktop/proj/merge.orig",
    "/Users/x/Desktop/proj/.DS_Store", "/Users/x/Desktop/proj/Thumbs.db",
    "/Users/x/Desktop/proj/notes.md~",      # vim/emacs backup
    "/Users/x/Desktop/proj/.#notes.md",     # emacs lock
    "/Users/x/Desktop/proj/~$report.docx",  # Word lock
])
def test_machine_output_shapes_are_noise(path):
    assert is_noise_path(path) is True


# ── FALSE-DENY guards: the highest-value tests here ───────────────────────────
# A false deny silently loses a row the user WANTED, with no error and no log — far
# worse than one extra row. These pin the deliberate non-denials.

@pytest.mark.parametrize("path", [
    "/Users/x/Desktop/AI-Native/notes.txt",       # a plain-FS file → external-nodiff
    "/Users/x/Desktop/proj/README.md",
    "/Users/x/Desktop/proj/src/main.py",
    "/Users/x/Desktop/proj/Cargo.lock",           # committed lockfiles: NOT `.lock`-denied
    "/Users/x/Desktop/proj/uv.lock",
    "/Users/x/Desktop/proj/poetry.lock",
    "/Users/x/Desktop/proj/.vscode/settings.json",  # users review committed editor cfg
    "/Users/x/Desktop/proj/target/debug/notes.md",  # `target` too generic to deny
    "/Users/x/Desktop/proj/out/report.md",          # `out` too generic
    "/Users/x/Desktop/proj/coverage/index.html",    # `coverage` too generic
    "/Users/x/Desktop/proj/.github/workflows/ci.yml",  # dot-dir, but a real deliverable
    "/Volumes/External/work/report.md",             # external drives hold user content
])
def test_deliverables_and_ambiguous_names_are_NOT_noise(path):
    assert is_noise_path(path) is False


@pytest.mark.parametrize("rel", [
    "Library/CloudStorage/OneDrive-Personal/report.docx",
    "Library/Mobile Documents/com~apple~CloudDocs/notes.md",
    ".kiro/steering/my-rules.md",   # only ~/.kiro/logs is denied — steering is authored
    "Documents/plan.md",
    "Desktop/idea.md",
])
def test_user_document_locations_under_home_are_NOT_noise(rel):
    """Cloud-sync mounts under ~/Library hold REAL user documents, so ~/Library is
    never denied wholesale — only its app-internal subdirs are."""
    assert is_noise_path(str(HOME / rel)) is False


# ── contract: purity, fail-open, absolute-only ────────────────────────────────

@pytest.mark.parametrize("bad", [
    "", "relative/not/absolute.txt", "./x.md", "notes.txt",
])
def test_relative_and_empty_are_not_judged(bad):
    """Only ABSOLUTE paths are judged — resolve first, then ask. Fail direction is
    OPEN so an unjudgeable path keeps the pre-denylist behavior."""
    assert is_noise_path(bad) is False


def test_never_raises_on_garbage():
    for bad in ["\x00null", "/" * 4096, "~", None, 42, object()]:
        assert is_noise_path(bad) in (True, False)  # a bool, never an exception


def test_judges_nonexistent_paths_identically():
    """Purity check: the predicate is path-SHAPE only, so it needs no filesystem.
    Every path in this module is fabricated and still classifies."""
    assert is_noise_path("/definitely/not/here/__pycache__/x.pyc") is True
    assert is_noise_path("/definitely/not/here/README.md") is False


# ── the dedupe must not have narrowed the Layer-2 watcher ──────────────────────

def test_noise_segments_superset_of_the_watchers_former_hardcoded_list():
    """workspace_surface_watcher._SKIP_SEGMENTS now composes from NOISE_SEGMENTS
    instead of re-listing the generic dirs. Pin the generic half it used to hardcode
    so that refactor can never silently narrow the pre-filter."""
    former_generic = {
        ".git", "__pycache__", "node_modules", ".venv", "venv",
        "dist", "build", ".pytest_cache", ".mypy_cache",
    }
    assert former_generic <= set(NOISE_SEGMENTS)

    from core.workspace_surface_watcher import _SKIP_SEGMENTS
    former_swarmws = {"DailyActivity", "JobResults", "Signals", "EvalHistory",
                      ".artifacts", "Services"}
    assert (former_generic | former_swarmws) <= _SKIP_SEGMENTS
