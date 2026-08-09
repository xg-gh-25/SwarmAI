"""Tests for the startup recall pre-warm (_prewarm_recall_body in main.py).

Root cause (run_16113a9b): recall_leg latency variance is dominated by the
first-in-process cold cost of the session + library legs (measured: session
1665ms→~300ms warm, library 819ms→~350ms warm). recall runs ONCE per session,
so the FIRST session eats the cold cost.

What a startup warmup actually persists (Gate-1 correction, verified against
vec_db.py:155 + session_recall.py:227): connections are opened+CLOSED per call,
so NOT a warm connection — what survives is (a) the OS page cache of the DB
files and (b) the function-local deferred imports (sqlite_vec native load,
RecallEngine/KnowledgeStore/SessionRecall) populating sys.modules process-wide.
Both persist across the fresh per-call connections, which is enough to remove
the cold penalty from the first real recall.

These tests pin the two behaviors the body MUST have: it warms exactly the two
cold legs (session + library, NOT ddd/context_files/codeintel), and it can
NEVER raise (a startup warmup failure must be non-fatal — mirrors _prewarm_boto3).
"""
import sys
from unittest.mock import patch

import main


def test_prewarm_recall_body_warms_only_the_two_cold_legs():
    """The warmup calls recall_all with domains == (session, library) — the two
    legs with a measured cold-start penalty. It must NOT warm ddd (a per-project,
    ~800ms CPU-BM25 leg that isn't even on the recall_leg path and has no active
    project at startup) nor context_files/codeintel (no cold-start penalty)."""
    with patch("core.recall_multi.recall_all") as mock_recall_all:
        main._prewarm_recall_body()
        assert mock_recall_all.called, "warmup must invoke recall_all"
        _, kwargs = mock_recall_all.call_args
        assert set(kwargs.get("domains", ())) == {"session", "library"}, (
            f"must warm exactly session+library, got {kwargs.get('domains')}"
        )
        # embed-free warmup — never a Bedrock call at startup
        assert kwargs.get("allow_embed") is False


def test_prewarm_recall_body_swallows_exceptions():
    """A startup warmup failure MUST be non-fatal — it can never propagate and
    abort lifespan startup (mirrors _prewarm_boto3's try/except non-critical)."""
    with patch("core.recall_multi.recall_all", side_effect=RuntimeError("boom")):
        # Must not raise — the body swallows and logs.
        main._prewarm_recall_body()
