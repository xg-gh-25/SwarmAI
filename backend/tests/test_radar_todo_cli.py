"""The radar skill's direct-SQLite CLI writes no ai_detected ToDo (design
docs/design/2026-10-31-continue-lookback-todo.md D2): a model-written ToDo goes
through the backend's create gate, which this CLI cannot apply."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "s_radar-todo" / "scripts" / "todo_db.py"


def test_add_refuses_ai_detected_and_writes_nothing(tmp_path):
    import sqlite3
    db = tmp_path / "data.db"
    sqlite3.connect(db).close()                       # the CLI refuses a missing store
    env = {"SWARM_DATA_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"}
    out = subprocess.run([sys.executable, str(SCRIPT), "add", "--title", "x",
                          "--source-type", "ai_detected"], env=env, capture_output=True,
                         text=True, timeout=60)
    assert out.returncode != 0 and "invalid choice" in out.stderr
    tables = sqlite3.connect(db).execute("SELECT name FROM sqlite_master").fetchall()
    assert tables == []                               # nothing was written
