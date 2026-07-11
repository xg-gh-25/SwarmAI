#!/usr/bin/env python3
"""
Tests for ddd_bindings.sync_back — the Run 5 reflow (reverse of bind_repo).

Focus: sync_back detects engineer edits in a bound repo's DDD docs and SURFACES a
reviewable delta WITHOUT mutating the live SwarmWS DDD (cultivation-safe), and defers
the git pull to HITL. Uses temp dirs to stand in for the worktree + SwarmWS roots
(the real GCRAIDLCPreset worktree's DDD docs are empty templates — full end-to-end
closure waits on real engineer-authored repo content; see Run 5 EVALUATE gate0_note).
"""
import hashlib
from pathlib import Path

import pytest

from core.ddd_bindings import (
    Binding,
    DeliveryContract,
    load_bindings,
    sync_back,
)

_DC = {
    "remote_kind": "code-amazon-cr",
    "build_system": "none",
    "branch": "mainline",
    "review_path": "s_internal-crux-review",
    "auto_send": "on-clean-review",
}


def _binding(sync_back_map=None):
    return Binding(
        repo="DemoRepo", kind="internal", clone="git://x",
        delivery_contract=DeliveryContract(**_DC),
        sync_back=sync_back_map,
    )


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ── AC1: model parses opt-in, absent = None ──────────────────────────────────

def test_binding_parses_with_sync_back(tmp_path):
    y = tmp_path / "bindings.yaml"
    y.write_text(
        "bindings:\n"
        "  - repo: DemoRepo\n    kind: internal\n    clone: 'git://x'\n"
        "    sync_back:\n      AGENTS.md: Projects/DemoRepo/AGENTS.md\n"
        "    delivery_contract:\n      remote_kind: code-amazon-cr\n"
        "      build_system: none\n      branch: mainline\n"
        "      review_path: s_internal-crux-review\n      auto_send: on-clean-review\n"
    )
    doc = load_bindings(y)
    assert doc.bindings[0].sync_back == {"AGENTS.md": "Projects/DemoRepo/AGENTS.md"}


def test_binding_absent_sync_back_is_none():
    assert _binding().sync_back is None


def test_sync_back_noop_when_unset(tmp_path):
    out = sync_back(_binding(None), tmp_path, tmp_path, now_iso="2026-07-12T00:00:00Z")
    assert out["deltas"] == [] and out["report_path"] is None
    assert out["pull_command"] == f"git -C {tmp_path.resolve()} pull"


# ── AC2: diff detects each status ────────────────────────────────────────────

def _setup(tmp_path, repo_text, ws_text):
    wt = tmp_path / "worktree"; ws = tmp_path / "ws"
    wt.mkdir(); (ws / "Projects" / "DemoRepo").mkdir(parents=True)
    if repo_text is not None:
        (wt / "AGENTS.md").write_text(repo_text)
    ws_target = ws / "Projects" / "DemoRepo" / "AGENTS.md"
    if ws_text is not None:
        ws_target.write_text(ws_text)
    b = _binding({"AGENTS.md": "Projects/DemoRepo/AGENTS.md"})
    return b, wt, ws, ws_target


def test_status_changed_with_diff(tmp_path):
    b, wt, ws, _ = _setup(tmp_path, "line A\nENGINEER EDIT\n", "line A\n")
    out = sync_back(b, wt, ws, now_iso="2026-07-12T00:00:00Z")
    d = out["deltas"][0]
    assert d["status"] == "changed"
    assert "ENGINEER EDIT" in d["diff"]


def test_status_unchanged(tmp_path):
    b, wt, ws, _ = _setup(tmp_path, "same\n", "same\n")
    out = sync_back(b, wt, ws, now_iso="2026-07-12T00:00:00Z")
    assert out["deltas"][0]["status"] == "unchanged"
    assert out["report_path"] is None  # nothing to surface


def test_status_new_in_repo(tmp_path):
    b, wt, ws, _ = _setup(tmp_path, "brand new\n", None)
    assert sync_back(b, wt, ws, now_iso="2026-07-12T00:00:00Z")["deltas"][0]["status"] == "new-in-repo"


def test_status_missing_in_repo(tmp_path):
    b, wt, ws, _ = _setup(tmp_path, None, "only in ws\n")
    assert sync_back(b, wt, ws, now_iso="2026-07-12T00:00:00Z")["deltas"][0]["status"] == "missing-in-repo"


def test_binary_doc_classified_not_crashed(tmp_path):
    wt = tmp_path / "worktree"; ws = tmp_path / "ws"
    wt.mkdir(); (ws / "Projects" / "DemoRepo").mkdir(parents=True)
    (wt / "AGENTS.md").write_bytes(b"\xff\xfe\x00\x01binary")
    (ws / "Projects" / "DemoRepo" / "AGENTS.md").write_text("text\n")
    b = _binding({"AGENTS.md": "Projects/DemoRepo/AGENTS.md"})
    assert sync_back(b, wt, ws, now_iso="2026-07-12T00:00:00Z")["deltas"][0]["status"] == "binary"


# ── AC3: NON-DESTRUCTIVE — live ws DDD byte-unchanged ────────────────────────

def test_non_destructive_ws_target_unchanged(tmp_path):
    b, wt, ws, ws_target = _setup(tmp_path, "line A\nENGINEER EDIT\n", "line A\n")
    before = _sha(ws_target)
    out = sync_back(b, wt, ws, now_iso="2026-07-12T00:00:00Z")
    assert _sha(ws_target) == before, "sync_back must NOT mutate the live DDD doc"
    assert out["report_path"] is not None and Path(out["report_path"]).exists()
    assert "ENGINEER EDIT" in Path(out["report_path"]).read_text()


# ── AC4: git pull is HITL (command surfaced, never invoked) ──────────────────

def test_pull_is_hitl_no_git_call(tmp_path, monkeypatch):
    import subprocess as _sp
    def _boom(*a, **k):
        raise AssertionError("sync_back must NOT invoke git/subprocess (pull is HITL)")
    monkeypatch.setattr(_sp, "run", _boom)
    monkeypatch.setattr(_sp, "check_output", _boom)
    b, wt, ws, _ = _setup(tmp_path, "x\ny\n", "x\n")
    out = sync_back(b, wt, ws, now_iso="2026-07-12T00:00:00Z")
    assert out["pull_command"].startswith("git -C ") and out["pull_command"].endswith(" pull")


# ── path traversal guard (Gate-1 must-fix) ───────────────────────────────────

@pytest.mark.parametrize("bad", ["../../../etc/passwd", "/etc/passwd"])
def test_path_traversal_rejected_repo_key(tmp_path, bad):
    """repo-doc key (worktree side) must not escape the worktree root."""
    wt = tmp_path / "worktree"; ws = tmp_path / "ws"; wt.mkdir(); ws.mkdir()
    b = _binding({bad: "Projects/DemoRepo/AGENTS.md"})
    with pytest.raises(ValueError):
        sync_back(b, wt, ws, now_iso="2026-07-12T00:00:00Z")


@pytest.mark.parametrize("bad", ["../../../etc/passwd", "/etc/passwd"])
def test_path_traversal_rejected_ws_value(tmp_path, bad):
    """ws-target value (SwarmWS side) must ALSO not escape (Gate-2: asymmetric gap)."""
    wt = tmp_path / "worktree"; ws = tmp_path / "ws"; wt.mkdir(); ws.mkdir()
    (wt / "AGENTS.md").write_text("x\n")
    b = _binding({"AGENTS.md": bad})
    with pytest.raises(ValueError):
        sync_back(b, wt, ws, now_iso="2026-07-12T00:00:00Z")


def test_oversized_doc_classified_not_ooms(tmp_path):
    """A >1MB mapped doc must classify 'too-large', never allocate an unbounded diff (Gate-2 MED)."""
    b, wt, ws, _ = _setup(tmp_path, "x\n", "x\n")
    (wt / "AGENTS.md").write_text("A" * 1_100_000)  # >1MB
    out = sync_back(b, wt, ws, now_iso="2026-07-12T00:00:00Z")
    assert out["deltas"][0]["status"] == "too-large"
    assert out["deltas"][0]["diff"] == ""


# ── AC5: simulated engineer-edit end-to-end ──────────────────────────────────

def test_simulated_engineer_edit_e2e(tmp_path):
    """Simulated (not real): an engineer edits a DDD doc in the (temp) worktree ->
    sync_back detects + surfaces it + leaves the live doc untouched. Real closure
    waits on engineer-authored content in the actual bound repo (Run 3/4 HITL)."""
    b, wt, ws, ws_target = _setup(
        tmp_path,
        "# AGENTS\n\nRule 1\nRule 2 (added by engineer in the repo)\n",
        "# AGENTS\n\nRule 1\n",
    )
    before = _sha(ws_target)
    out = sync_back(b, wt, ws, now_iso="2026-07-12T00:00:00Z")
    # detected
    assert out["deltas"][0]["status"] == "changed"
    assert "Rule 2 (added by engineer in the repo)" in out["deltas"][0]["diff"]
    # surfaced
    assert Path(out["report_path"]).exists()
    # non-destructive
    assert _sha(ws_target) == before


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-x", "-q"]))
