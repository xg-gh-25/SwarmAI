"""run_fe26ed6c: _signal_stale_spec_details creates a Radar todo (real consumer
surface, not a sink-less event — GUI04) when a domain genuinely drifts, with dedup."""
import json
import sqlite3
from pathlib import Path



def _seed_todos_schema(db_path: Path):
    conn = sqlite3.connect(str(db_path))
    conn.execute("""CREATE TABLE todos (id TEXT PRIMARY KEY, workspace_id TEXT,
        title TEXT, description TEXT, source TEXT, source_type TEXT, status TEXT,
        priority TEXT, due_date TEXT, linked_context TEXT, task_id TEXT,
        created_at TEXT, updated_at TEXT)""")
    conn.commit(); conn.close()


def _stale_project(tmp_path) -> Path:
    """A project whose one spec.md marker != its domain spec_hash → stale."""
    proj = tmp_path / "Projects" / "P"
    (proj / "spec-details").mkdir(parents=True)
    doc = {"domains": [{"id": "domain:orders", "name": "orders", "spec_hash": "a" * 64}]}
    (proj / "code-intel.json").write_text(json.dumps(doc), encoding="utf-8")
    (proj / "spec-details" / "orders.spec.md").write_text(
        "# 规格:orders\n<!-- spec-hash: " + ("b" * 64) + " -->\nbody\n", encoding="utf-8")
    return proj.parent  # the Projects/ dir


def test_stale_spec_creates_radar_todo(tmp_path, monkeypatch):
    db = tmp_path / "data.db"; _seed_todos_schema(db)
    import core.escalation as esc_mod
    monkeypatch.setattr(esc_mod, "_get_db_path", lambda: db)
    from hooks.context_health_hook import ContextHealthHook
    hook = ContextHealthHook()
    projects_dir = _stale_project(tmp_path)
    hook._signal_stale_spec_details(projects_dir)

    conn = sqlite3.connect(str(db))
    rows = conn.execute("SELECT title, source FROM todos WHERE status='pending'").fetchall()
    conn.close()
    assert len(rows) == 1, f"expected 1 todo, got {rows}"
    assert "spec_details_stale:P" in rows[0][1]  # deterministic source key
    assert "orders.spec.md" in rows[0][0] or "P" in rows[0][0]


def test_dedup_no_duplicate_on_second_call(tmp_path, monkeypatch):
    db = tmp_path / "data.db"; _seed_todos_schema(db)
    import core.escalation as esc_mod
    monkeypatch.setattr(esc_mod, "_get_db_path", lambda: db)
    from hooks.context_health_hook import ContextHealthHook
    hook = ContextHealthHook()
    projects_dir = _stale_project(tmp_path)
    hook._signal_stale_spec_details(projects_dir)
    hook._signal_stale_spec_details(projects_dir)  # second call — must NOT dup

    conn = sqlite3.connect(str(db))
    n = conn.execute("SELECT count(*) FROM todos WHERE status='pending'").fetchone()[0]
    conn.close()
    assert n == 1, f"dedup failed: {n} todos"


def test_fresh_project_creates_no_todo(tmp_path, monkeypatch):
    db = tmp_path / "data.db"; _seed_todos_schema(db)
    import core.escalation as esc_mod
    monkeypatch.setattr(esc_mod, "_get_db_path", lambda: db)
    from hooks.context_health_hook import ContextHealthHook
    hook = ContextHealthHook()
    # fresh: marker == spec_hash
    proj = tmp_path / "Projects" / "P"
    (proj / "spec-details").mkdir(parents=True)
    h = "c" * 64
    (proj / "code-intel.json").write_text(
        json.dumps({"domains": [{"id": "domain:orders", "spec_hash": h}]}), encoding="utf-8")
    (proj / "spec-details" / "orders.spec.md").write_text(
        f"# x\n<!-- spec-hash: {h} -->\n", encoding="utf-8")
    hook._signal_stale_spec_details(proj.parent)
    conn = sqlite3.connect(str(db))
    n = conn.execute("SELECT count(*) FROM todos").fetchone()[0]
    conn.close()
    assert n == 0
