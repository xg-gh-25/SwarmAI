"""A new worker Hive moves its inherited private cognition aside, once, never deleting
it (core/worker_reset; design 2026-10-29-hive-run-record-and-worker-role §3.4)."""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def ws(tmp_path):
    w = tmp_path / "ws"
    ctx = w / ".context"
    ctx.mkdir(parents=True)
    for name in ("MEMORY.md", "EVOLUTION.md", "USER.md", "STEERING.md", "TOOLS.md", "TIMELINE.md",
                 "MEMORY-archive-2026-10.md", "SOUL.md", "AGENT.md", "KNOWLEDGE.md"):
        (ctx / name).write_text(f"hive's own {name}")
    da = w / "Knowledge" / "DailyActivity"
    da.mkdir(parents=True)
    (da / "2026-10-28.md").write_text("an earlier dispatch")
    (w / "Knowledge" / "Library").mkdir()
    (w / "Knowledge" / "Library" / "x.md").write_text("library")
    return w


def _role(monkeypatch, mode, role=None):
    monkeypatch.setenv("SWARMAI_MODE", mode) if mode else monkeypatch.delenv("SWARMAI_MODE", raising=False)
    monkeypatch.setenv("SWARMAI_ROLE", role) if role else monkeypatch.delenv("SWARMAI_ROLE", raising=False)


def test_a_new_worker_moves_its_private_cognition_aside_once(ws, monkeypatch):
    from core import worker_reset
    _role(monkeypatch, "hive")
    rec = worker_reset.set_aside(ws)
    moved = set(rec["moved"])
    assert moved == {".context/MEMORY.md", ".context/EVOLUTION.md", ".context/USER.md",
                     ".context/STEERING.md", ".context/TOOLS.md", ".context/TIMELINE.md",
                     ".context/MEMORY-archive-2026-10.md", "Knowledge/DailyActivity"}
    dest = ws / rec["moved_to"]
    assert (dest / ".context" / "MEMORY.md").read_text() == "hive's own MEMORY.md"     # kept, not lost
    assert (dest / "Knowledge" / "DailyActivity" / "2026-10-28.md").read_text() == "an earlier dispatch"
    for kept in ("SOUL.md", "AGENT.md", "KNOWLEDGE.md"):                               # public / derived
        assert (ws / ".context" / kept).exists()
    assert (ws / "Knowledge" / "Library" / "x.md").exists()
    assert not (ws / ".context" / "MEMORY.md").exists()
    assert json.loads((ws / worker_reset.MARKER).read_text())["moved"] == rec["moved"]
    (ws / ".context" / "MEMORY.md").write_text("the template, recreated")
    assert worker_reset.set_aside(ws) is None                                          # once
    assert (ws / ".context" / "MEMORY.md").read_text() == "the template, recreated"


@pytest.mark.parametrize("mode,role", [(None, None), ("daemon", None), ("hive", "reference")])
def test_a_person_s_instance_and_a_reference_hive_are_never_touched(ws, monkeypatch, mode, role):
    from core import worker_reset
    _role(monkeypatch, mode, role)
    assert worker_reset.set_aside(ws) is None
    assert (ws / ".context" / "MEMORY.md").read_text() == "hive's own MEMORY.md"
    assert not (ws / worker_reset.MARKER).exists()


def test_a_symlinked_file_is_left_alone(ws, monkeypatch, tmp_path):
    from core import worker_reset
    _role(monkeypatch, "hive")
    outside = tmp_path / "outside.md"
    outside.write_text("not the hive's")
    (ws / ".context" / "USER.md").unlink()
    (ws / ".context" / "USER.md").symlink_to(outside)
    rec = worker_reset.set_aside(ws)
    assert ".context/USER.md" not in rec["moved"] and outside.read_text() == "not the hive's"


def test_nothing_is_moved_through_a_linked_context_or_knowledge(ws, monkeypatch, tmp_path):
    import shutil
    from core import worker_reset
    _role(monkeypatch, "hive")
    outside = tmp_path / "outside-ctx"
    shutil.move(str(ws / ".context"), str(outside))
    (ws / ".context").symlink_to(outside)
    out_k = tmp_path / "outside-k"
    shutil.move(str(ws / "Knowledge"), str(out_k))
    (ws / "Knowledge").symlink_to(out_k)
    rec = worker_reset.set_aside(ws)
    assert rec["moved"] == []
    assert (outside / "MEMORY.md").exists() and (out_k / "DailyActivity" / "2026-10-28.md").exists()
