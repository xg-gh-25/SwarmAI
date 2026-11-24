"""GET /api/library/recent: what changed in Knowledge/ lately, and who wrote it.

core.library_recent walks Knowledge/ for this window and the one before, leaving out
what is not content (lock sidecars, jsonl twins, sqlite files, the mounts' own index
dir), and attributes `source` from CDF's write journal (ingestion.db
ingestion_writes, read-only): a path CDF wrote in the window is `cdf` with its unit
count, JobResults / DailyActivity / Archives are `job`, the rest `chat`. A missing
journal means no cdf attribution, never an error. The counts come before the cap.
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core import library_recent
from core.library_recent import ITEM_CAP, cdf_writes_since, is_recent_noise, scan_recent

DAY = 24 * 3600


def _write(p: Path, age_days: float = 0.1, content: str = "x") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    t = time.time() - age_days * DAY
    os.utime(p, (t, t))
    return p


@pytest.fixture
def kdir(tmp_path: Path) -> Path:
    k = tmp_path / "Knowledge"
    k.mkdir()
    return k


@pytest.fixture
def journal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A real ingestion.db (the CDF schema) in a tmp data dir; returns add(path, age_days,
    step, op_kind, done_age_days): `age_days` is when the write was planned
    (created_at), `done_age_days` when it finished (updated_at, default the same)."""
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("SWARM_DATA_DIR", str(data))
    from core.ingestion.api import SCHEMA_DDL, ingestion_db_path
    db = ingestion_db_path()
    assert db.parent == data
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA_DDL)
    n = [0]

    def add(path: str, age_days: float = 0.1, step: str = "done", op_kind: str = "ingestion_unit",
            done_age_days: float | None = None):
        n[0] += 1
        now = datetime.now(timezone.utc)
        at = (now - timedelta(days=age_days)).isoformat()
        done = (now - timedelta(days=age_days if done_age_days is None else done_age_days)).isoformat()
        conn.execute(
            "INSERT INTO ingestion_writes (op_id, op_kind, unit_id, target_path, step, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (f"op{n[0]}", op_kind, f"u{n[0]}" if op_kind == "ingestion_unit" else None, path, step, at, done),
        )
        conn.commit()

    yield add
    conn.close()


def _paths(feed: dict) -> list[str]:
    return [it["path"] for it in feed["items"]]


@pytest.mark.parametrize("rel, noise", [
    ("Notes/a.md", False),
    ("Notes/a.md.lock", True),
    ("JobResults/run.jsonl", True),
    ("Library/code_intel.db", True),
    ("Library/x.sqlite3-wal", True),
    ("Library/code_intel.db-shm", True),
    ("Library/mounts/m1/notes.md", True),
    ("Library/mounts-guide.md", False),
    (".library-health.json", True),
    ("Notes/.hidden/a.md", True),
    ("Designs/db-design.md", False),
])
def test_noise(rel: str, noise: bool) -> None:
    assert is_recent_noise(tuple(rel.split("/"))) is noise


def test_feed_leaves_out_infrastructure(kdir: Path, journal) -> None:
    _write(kdir / "Notes" / "a.md")
    _write(kdir / "Notes" / "a.md.lock")
    _write(kdir / "DailyActivity" / "2026-11-24.jsonl")
    _write(kdir / "Library" / "mounts" / "m1" / "code_intel.db-shm")
    _write(kdir / "Library" / "mounts" / "m1" / "README.md")
    _write(kdir / "Library" / "graph.sqlite")
    feed = scan_recent(kdir)
    assert _paths(feed) == ["Knowledge/Notes/a.md"]
    assert feed["this_week"] == feed["count"] == 1


def test_source_comes_from_the_cdf_journal(kdir: Path, journal) -> None:
    _write(kdir / "Library" / "uv.md", age_days=0.2)
    _write(kdir / "Notes" / "by-chat.md", age_days=0.3)
    _write(kdir / "JobResults" / "run.md", age_days=0.4)
    _write(kdir / "Archives" / "digest.md", age_days=0.5)
    _write(kdir / "DailyActivity" / "2026-11-24.md", age_days=0.6)
    _write(kdir / "Mockups" / "x.html", age_days=0.7)
    journal("Knowledge/Library/uv.md")
    journal("Knowledge/Library/uv.md")
    journal("Knowledge/Library/uv.md", age_days=9)                   # before the window
    journal("Knowledge/Library/uv.md", step="planned")               # not done
    journal("Knowledge/Notes/by-chat.md", op_kind="user_edit")       # a person's edit, not CDF
    feed = scan_recent(kdir)
    by_path = {it["path"]: it for it in feed["items"]}
    assert by_path["Knowledge/Library/uv.md"]["source"] == "cdf"
    assert by_path["Knowledge/Library/uv.md"]["cdf_units"] == 2
    assert by_path["Knowledge/Notes/by-chat.md"]["source"] == "chat"
    assert by_path["Knowledge/JobResults/run.md"]["source"] == "job"
    assert by_path["Knowledge/Archives/digest.md"]["source"] == "job"
    assert by_path["Knowledge/DailyActivity/2026-11-24.md"]["source"] == "job"
    assert by_path["Knowledge/Mockups/x.html"]["source"] == "chat"
    assert all(it["cdf_units"] == 0 for it in feed["items"] if it["source"] != "cdf")
    assert feed["by_source"] == {"cdf": 1, "chat": 2, "job": 3}
    assert _paths(feed)[0] == "Knowledge/Library/uv.md"             # newest first


def test_the_window_is_when_cdf_finished_the_write_not_when_it_planned_it(kdir: Path, journal) -> None:
    # planned 8 days ago, written 6 days ago: the file changed inside the window, by CDF
    _write(kdir / "Library" / "slow.md", age_days=6)
    journal("Knowledge/Library/slow.md", age_days=8, done_age_days=6)
    # a write finished before the window does not count
    _write(kdir / "Library" / "old.md", age_days=0.5)
    journal("Knowledge/Library/old.md", age_days=9, done_age_days=8)
    by_path = {it["path"]: it for it in scan_recent(kdir)["items"]}
    assert by_path["Knowledge/Library/slow.md"]["source"] == "cdf"
    assert by_path["Knowledge/Library/slow.md"]["cdf_units"] == 1
    assert by_path["Knowledge/Library/old.md"]["source"] == "chat"


def test_a_library_file_cdf_did_not_write_is_chat(kdir: Path, journal) -> None:
    _write(kdir / "Library" / "mine.md")
    assert scan_recent(kdir)["items"][0]["source"] == "chat"


def test_missing_journal_means_no_cdf_never_an_error(kdir: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path / "empty"))
    (tmp_path / "empty").mkdir()
    _write(kdir / "Library" / "uv.md")
    assert cdf_writes_since(time.time() - 7 * DAY) == {}
    feed = scan_recent(kdir)
    assert feed["items"][0]["source"] == "chat"
    assert not (tmp_path / "empty" / "ingestion.db").exists()      # read-only: never created


def test_unreadable_journal_means_no_cdf(kdir: Path, tmp_path: Path, monkeypatch) -> None:
    data = tmp_path / "bad"
    data.mkdir()
    (data / "ingestion.db").write_bytes(b"not a database at all, just bytes" * 10)
    monkeypatch.setenv("SWARM_DATA_DIR", str(data))
    assert cdf_writes_since(0) == {}


def test_last_week_and_counts_before_the_cap(kdir: Path, journal, monkeypatch) -> None:
    monkeypatch.setattr(library_recent, "ITEM_CAP", 3)
    for i in range(5):
        _write(kdir / "Notes" / f"new-{i}.md", age_days=1 + i * 0.1)
    for i in range(4):
        _write(kdir / "Notes" / f"prev-{i}.md", age_days=8 + i)
    _write(kdir / "Notes" / "ancient.md", age_days=20)
    _write(kdir / "Notes" / "prev.md.lock", age_days=9)               # noise: not counted
    feed = scan_recent(kdir)
    assert len(feed["items"]) == 3
    assert feed["this_week"] == 5 and feed["count"] == 5
    assert feed["last_week"] == 4
    assert feed["by_source"] == {"cdf": 0, "chat": 5, "job": 0}


def test_default_cap_is_200() -> None:
    assert ITEM_CAP == 200


def test_window_follows_days(kdir: Path, journal) -> None:
    _write(kdir / "Notes" / "a.md", age_days=2)
    _write(kdir / "Notes" / "b.md", age_days=4)
    feed = scan_recent(kdir, days=3)
    assert feed["window_days"] == 3
    assert feed["this_week"] == 1 and feed["last_week"] == 1


def test_endpoint_runs_the_scan_off_the_loop(kdir: Path, journal, monkeypatch) -> None:
    from routers import library_api
    calls = []

    async def fake_run_nav(fn, *args):
        calls.append(fn)
        return fn(*args)

    monkeypatch.setattr(library_api, "_knowledge_dir", lambda: kdir)
    monkeypatch.setattr(library_api.executors, "run_nav", fake_run_nav)
    _write(kdir / "Notes" / "a.md")
    out = asyncio.run(library_api.recent_feed(days=7))
    assert calls == [scan_recent]
    assert out["this_week"] == 1 and out["items"][0]["source"] == "chat"
