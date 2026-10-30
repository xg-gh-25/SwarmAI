"""The session record the review reads (core/ingestion/sources/s2_session.py).

S2's own extraction passes are gone (design 2026-10-30-cdf-agent-decides D4): the
session's agent reviews it (s7_review). Tested here: the read set fails closed, and the
extraction passes no longer exist.
"""
from __future__ import annotations

from core.ingestion.sources import s2_session


def test_the_extraction_passes_are_gone():
    for name in ("extract_session", "extract_session_sync", "_trusted_pass", "_main_pass"):
        assert not hasattr(s2_session, name), name


def test_read_roots_fail_closed_when_an_event_has_no_bound_root(tmp_path, monkeypatch):
    """§9 anti selective citation: an unbound event, a missing data.db or a read error
    keeps the raw id in the read set (provenance then marks the unit tainted); the read
    set never shrinks silently."""
    import sqlite3
    from core import session_inputs

    missing = tmp_path / "absent.db"
    monkeypatch.setattr(session_inputs, "_db_path", lambda: missing)
    assert s2_session._root_ids(("e1", "e2")) == ("e1", "e2")

    db = tmp_path / "data.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE session_inputs(event_id TEXT, root_id TEXT)")
        conn.execute("INSERT INTO session_inputs VALUES('e1','root-1'),('e2',NULL)")
    monkeypatch.setattr(session_inputs, "_db_path", lambda: db)
    assert s2_session._root_ids(("e1", "e2", "e3")) == ("root-1", "e2", "e3")
