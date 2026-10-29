"""CDF and chat share one process without slowing chat or being starved by it
(docs/design/2026-10-29-cdf-chat-coexistence.md).

- Byte offsets are computed in one pass: every unit's offsets slice exactly its text,
  CJK and emoji included, and a large file parses in linear time (it was quadratic:
  ~1 s for IMPROVEMENT.md, on the chat paths too).
- A parse is cached by the file's (mtime_ns, size); a changed file is parsed again.
- A streaming chat no longer holds the session lane; memory pressure still does.
- The session review forks a CLI only when the spawn budget allows; otherwise the
  job waits without spending an attempt and without running its user pass twice.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from core.ingestion import units as U


def _ddd(entries: int, *, cjk: bool = True) -> str:
    body = ["# TECH\n"]
    for section in range(max(1, entries // 50)):
        body.append(f"\n## Section {section} 第{section}节\n")
        for i in range(50):
            text = ("中文说明，包含 emoji 🐝 与「引号」。" if cjk else "plain text. ") * 6
            body.append(f"- [guideline] **Entry {section}.{i} 条目** {text}\n"
                        "  <!-- ref:0 | last:2026-10-20 | decay:active | source:manual -->\n")
    body.append("\n## Links\n- [link] **Ref 参考** <https://example.com>\n"
                "- [gap:follow_up] **Gap 缺口** something to do 🐝\n")
    return "".join(body)


def _assert_offsets_exact(path: Path, root: Path) -> list:
    raw = path.read_bytes()
    parsed = U.parse_units(path, root=root)
    assert parsed
    for unit in parsed:
        assert raw[unit.byte_start:unit.byte_end].decode("utf-8") == unit.text, unit.title
    return parsed


def test_offsets_slice_exactly_each_units_text(tmp_path):
    ddd = tmp_path / "TECH.md"
    ddd.write_text(_ddd(120), encoding="utf-8")
    _assert_offsets_exact(ddd, tmp_path)
    evo = tmp_path / "EVOLUTION.md"
    evo.write_text("# E 演化\n\n## Corrections Captured\n### C001 | 2026-10-01\n- **Correction**: 第一 🐝\n"
                   "### C002 | 2026-10-02\n- **Correction**: second\n\n## Competence Learned\n"
                   "### K001 | 2026-10-03\n- **Competence**: 第三\n", encoding="utf-8")
    assert [u.section for u in _assert_offsets_exact(evo, tmp_path)] == [
        "Corrections Captured", "Corrections Captured", "Competence Learned"]
    timeline = tmp_path / "TIMELINE.md"
    timeline.write_text("# Timeline\n\n## Not injected\n\n### 2026-10-26 事件一 🐝\n- what: 一\n\n"
                        "### 2026-10-27 Event two\n- what: two\n", encoding="utf-8")
    _assert_offsets_exact(timeline, tmp_path)
    doc = tmp_path / "note.md"
    doc.write_text("\n\n# 笔记 🐝\n\n正文。\n", encoding="utf-8")
    _assert_offsets_exact(doc, tmp_path)


def test_a_large_file_parses_in_linear_time(tmp_path):
    big = tmp_path / "IMPROVEMENT.md"
    big.write_text(_ddd(3000), encoding="utf-8")          # ~1.2 MB, 3000 entries
    assert big.stat().st_size > 1_000_000
    started = time.perf_counter()
    U.parse_units(big, root=tmp_path)
    assert time.perf_counter() - started < 1.0            # the quadratic offsets took several s


def test_a_parse_is_cached_until_the_file_changes(tmp_path, monkeypatch):
    path = tmp_path / "TECH.md"
    path.write_text(_ddd(50), encoding="utf-8")
    first = U.parse_units(path, root=tmp_path)
    reads = []
    real = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: reads.append(self) or real(self, *a, **k))
    again = U.parse_units(path, root=tmp_path)
    assert again == first and again is not first and reads == []
    again.append("caller's own list")                      # a caller cannot change the cache
    assert U.parse_units(path, root=tmp_path) == first
    path.write_text(path.read_text(encoding="utf-8") + "- [guideline] **New 新** added\n",
                    encoding="utf-8")
    assert any(u.title.startswith("New") for u in U.parse_units(path, root=tmp_path))


def test_a_streaming_chat_does_not_hold_the_session_lane(monkeypatch):
    from core.ingestion.worker import ingestion_admission
    from core.resource_monitor import resource_monitor
    memory = type("Memory", (), {"pressure_level": "ok"})()
    monkeypatch.setattr(resource_monitor, "system_memory", lambda: memory)
    monkeypatch.setattr("core.session_unit.is_any_chat_streaming", lambda: True)
    for lane in ("session", "scheduled", "summary", "run", "immediate"):
        assert ingestion_admission(lane).run, lane
    warning = type("Memory", (), {"pressure_level": "warning"})()
    monkeypatch.setattr(resource_monitor, "system_memory", lambda: warning)
    assert not ingestion_admission("session").run


def test_the_review_waits_for_the_spawn_budget(monkeypatch, tmp_path):
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))
    from core.ingestion import s2_jobs, worker
    from core.ingestion.store import connect_rw
    from core.ingestion.sources import s2_session
    from core.resource_monitor import resource_monitor
    budget = type("Budget", (), {"can_spawn": False, "reason": "memory 91%"})()
    monkeypatch.setattr(resource_monitor, "spawn_budget", lambda alive_count=0: budget)
    passes = []
    monkeypatch.setattr(s2_session, "extract_session_sync", lambda sid, passes=(): passes.append(passes))
    s2_jobs.request("s-1", "session_end", sdk_session_id="sdk-1")
    conn = connect_rw()
    executor = worker.S2JobExecutor()
    monkeypatch.setattr(worker, "ingestion_admission", lambda lane: worker.Admission(True, True))
    assert executor.drain(conn) == 0
    row = conn.execute("SELECT state,attempts,next_attempt_at,error FROM ingestion_s2_jobs").fetchone()
    conn.close()
    assert (row[0], row[1], row[3]) == ("queued", 0, None) and row[2]
    assert passes == []                                    # the user pass did not run either


def test_a_rewrite_that_keeps_size_and_mtime_is_not_served_from_the_cache(tmp_path):
    import os
    path = tmp_path / "TECH.md"
    path.write_text("# T\n\n## S\n- [guideline] **Alpha** one\n", encoding="utf-8")
    st = os.stat(path)
    assert U.parse_units(path, root=tmp_path)[0].title == "Alpha"
    path.write_text("# T\n\n## S\n- [guideline] **Omega** one\n", encoding="utf-8")   # same size
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))                              # same mtime
    assert U.parse_units(path, root=tmp_path)[0].title == "Omega"


def test_a_postponed_job_folds_in_a_request_made_while_it_was_claimed(monkeypatch, tmp_path):
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))
    from datetime import timedelta
    from core.ingestion import s2_jobs
    from core.ingestion.store import connect_rw
    s2_jobs.request("s-2", "session_end", sdk_session_id="sdk")
    conn = connect_rw()
    conn.execute("UPDATE ingestion_s2_jobs SET next_attempt_at=NULL")
    assert s2_jobs.claim(conn)["session_id"] == "s-2"
    s2_jobs.request("s-2", "session_end", delay=timedelta(minutes=30))     # arrives while claimed
    s2_jobs.postpone(conn, "s-2", timedelta(minutes=5))
    row = conn.execute("SELECT state,again,attempts,next_attempt_at FROM ingestion_s2_jobs").fetchone()
    conn.close()
    assert (row[0], row[1], row[2]) == ("queued", 0, 0)
    from datetime import datetime, timezone
    assert datetime.fromisoformat(row[3]) > datetime.now(timezone.utc) + timedelta(minutes=20)
