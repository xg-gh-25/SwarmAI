"""CDF Bedrock usage record (core/ingestion/llm_usage.py, schema v6).

What is tested:
- A Bedrock call's input/output tokens land in ingestion_llm_usage under the source the
  caller attributed (worker per candidate, extractor per source, S1 entailment), and
  calls outside any attribution land under "-".
- Two calls the same day for one source add up in one row; ``totals`` sums all rows.
- Recording is fail-open: a broken store never fails the judge.
- A v5 database migrates to v6: the usage table appears and the daily-stats
  llm_calls/tokens columns (which never held usage) are gone.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("SWARM_DATA_DIR", str(data))
    return data


def _judge(monkeypatch: pytest.MonkeyPatch, tokens_in: int, tokens_out: int):
    """BedrockJudge whose jobs.bedrock.text() call is faked, filling usage like the real one."""
    from core.ingestion import llm

    def fake_text(prompt, *, usage, **_):
        usage.update(input_tokens=tokens_in, output_tokens=tokens_out)
        return "{}"

    monkeypatch.setattr(llm, "text", fake_text)
    return llm.BedrockJudge(model_id="test-model")


def _rows(data: Path) -> list[tuple]:
    conn = sqlite3.connect(data / "ingestion.db")
    try:
        return conn.execute(
            "SELECT source,calls,input_tokens,output_tokens FROM ingestion_llm_usage ORDER BY source"
        ).fetchall()
    finally:
        conn.close()


def test_calls_are_recorded_under_the_attributed_source(data_dir, monkeypatch) -> None:
    from core.ingestion import llm_usage
    judge = _judge(monkeypatch, 1000, 50)
    with llm_usage.attribute("S2"):
        judge._invoke("p", lambda raw: raw)
        judge._invoke("p", lambda raw: raw)
    with llm_usage.attribute("S4"):
        judge._invoke("p", lambda raw: raw)
    judge._invoke("p", lambda raw: raw)
    assert _rows(data_dir) == [("-", 1, 1000, 50), ("S2", 2, 2000, 100), ("S4", 1, 1000, 50)]
    assert llm_usage.totals() == {"calls": 4, "input_tokens": 4000, "output_tokens": 200}


def test_extractor_attributes_its_source(data_dir, monkeypatch) -> None:
    from core.ingestion import llm_usage
    from core.ingestion.sources import extract_llm
    seen: list[str] = []

    class Llm:
        def extract(self, prompt: str):
            seen.append(llm_usage.current_source())
            return type("R", (), {"value": [], "raw": "[]"})()

    extract_llm.extract_units("S5", "text", (), allowed_kinds=("cognition",), llm=Llm())
    assert seen == ["S5"]
    assert llm_usage.current_source() == "-"


def test_worker_attributes_each_candidate_to_its_source(monkeypatch) -> None:
    from core.ingestion import llm_usage, worker
    seen: list[str] = []
    monkeypatch.setattr(worker, "_process_candidate",
                        lambda *a, **k: seen.append(llm_usage.current_source()))
    worker.process_candidate(None, None, {"source": "remember", "source_v2": "S3"}, Path("."), None)
    worker.process_candidate(None, None, {"source": "remember", "source_v2": None}, Path("."), None)
    assert seen == ["S3", "remember"]


def test_truncated_reply_is_untyped_and_still_recorded(data_dir, monkeypatch) -> None:
    from core.ingestion import llm
    from core.ingestion.prompts import UntypedOutput
    from jobs.bedrock import StructuredOutputError

    def cut(prompt, *, usage, **_):
        usage.update(input_tokens=500, output_tokens=4096)
        raise StructuredOutputError("truncated", "half a reply", "reply reached max_tokens=4096")

    monkeypatch.setattr(llm, "text", cut)
    with pytest.raises(UntypedOutput) as caught:
        llm.BedrockJudge(model_id="m")._invoke("p", lambda raw: raw)
    assert "truncated" in str(caught.value) and caught.value.raw == "half a reply"
    assert _rows(data_dir) == [("-", 1, 500, 4096)]


def test_recording_fails_open(data_dir, monkeypatch) -> None:
    from core.ingestion import api
    judge = _judge(monkeypatch, 10, 1)

    def broken(**_):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(api, "connect_rw", broken)
    assert judge._invoke("p", lambda raw: raw).raw == "{}"


def test_v5_database_migrates_to_v6(data_dir) -> None:
    from core.ingestion.api import SCHEMA_DDL
    from core.ingestion.api_v2 import SCHEMA_V2_DDL
    from core.ingestion.store import _USAGE_SCHEMA_DDL, _V2_MIGRATION_DDL, connect_rw
    db = data_dir / "ingestion.db"
    conn = sqlite3.connect(db, isolation_level=None)
    conn.execute("PRAGMA auto_vacuum=2")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(f"BEGIN;{SCHEMA_DDL}{_V2_MIGRATION_DDL}{SCHEMA_V2_DDL}{_USAGE_SCHEMA_DDL}"
                       "PRAGMA user_version=5;COMMIT;")
    conn.close()
    migrated = connect_rw()
    try:
        assert migrated.execute("PRAGMA user_version").fetchone()[0] == 10
        columns = {row[1] for row in migrated.execute("PRAGMA table_info(ingestion_daily_stats)")}
        assert "llm_calls" not in columns and "tokens" not in columns
        assert migrated.execute("SELECT COUNT(*) FROM ingestion_llm_usage").fetchone()[0] == 0
    finally:
        migrated.close()
