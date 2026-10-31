"""AttentionAuthority ingestion-source tests.

Each supported unresolved anomaly is normalized once with the specified tier.
A missing ingestion.db returns no items and never creates a file.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3

import pytest

from core import attention_authority as authority
from core.ingestion import api
from core.ingestion.api import SCHEMA_DDL


def _old() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat().replace("+00:00", "Z")


def test_collect_ingestion_covers_each_kind_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "ingestion.db"
    conn = sqlite3.connect(path); conn.executescript(SCHEMA_DDL)
    kinds = ["id_collision", "writer_unknown", "user_race", "poison", "restored_orphan", "missing_section"]
    # kinds of removed mechanisms (the source breaker, the judge, a backlog event no code
    # writes) are no longer surfaced, even when an old unresolved row is left
    gone = ["breaker", "backlog", "judge_down"]
    expected_prompt = "在 Projects/Widget/TECH.md 加 `## Runtime Traps`"
    for kind in kinds + gone:
        detail = {"summary": kind}
        if kind == "missing_section": detail["prompt"] = expected_prompt
        conn.execute("INSERT INTO ingestion_events(at,kind,detail,resolved) VALUES(?,?,?,0)", (_old(), kind, json.dumps(detail)))
    conn.execute("INSERT INTO ingestion_events(at,kind,detail,resolved) VALUES(?,?,?,0)", (_old(), "poison", "{}"))
    # a live owner, so the computed "stalled" item stays out of these event-kind tests
    conn.execute("INSERT INTO ingestion_owner(id,epoch,pid,started_at) VALUES(1,1,?,?)", (os.getpid(), _old()))
    conn.commit(); conn.close()

    def ro() -> sqlite3.Connection:
        value = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        value.execute("PRAGMA query_only=ON")
        return value

    monkeypatch.setattr(api, "connect_ro", ro)
    items = authority._collect_ingestion()
    assert {item.dispatch["context"]["kind"] for item in items} == set(kinds)
    assert len(items) == len(kinds)
    assert {item.dispatch["context"]["kind"] for item in items if item.tier == "blocking"} == {"user_race", "writer_unknown"}
    assert all(item.source == "ingestion" for item in items)
    missing = next(item for item in items if item.dispatch["context"]["kind"] == "missing_section")
    assert missing.dispatch["message"].startswith(expected_prompt)


def test_collect_ingestion_ignores_young_threshold_events(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "ingestion.db"
    conn = sqlite3.connect(path); conn.executescript(SCHEMA_DDL)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("INSERT INTO ingestion_events(at,kind,detail,resolved) VALUES(?,?,?,0)", (now, "backlog", json.dumps({"oldest_wait_seconds": 60})))
    conn.execute("INSERT INTO ingestion_events(at,kind,detail,resolved) VALUES(?,?,?,0)", (now, "judge_down", json.dumps({"since": now})))
    # a live owner, so the computed "stalled" item stays out of these event-kind tests
    conn.execute("INSERT INTO ingestion_owner(id,epoch,pid,started_at) VALUES(1,1,?,?)", (os.getpid(), _old()))
    conn.commit(); conn.close()
    monkeypatch.setattr(api, "connect_ro", lambda: sqlite3.connect(f"file:{path}?mode=ro", uri=True))
    assert authority._collect_ingestion() == []


def test_missing_db_returns_nothing_without_creation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "ingestion.db"

    def missing() -> sqlite3.Connection:
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True)

    monkeypatch.setattr(api, "connect_ro", missing)

    def creating(*, busy_timeout_ms: int = 5000) -> sqlite3.Connection:
        value = sqlite3.connect(path)
        value.executescript(SCHEMA_DDL)
        return value

    monkeypatch.setattr(api, "connect_rw", creating)
    assert authority._collect_ingestion() == []
    assert not path.exists()
