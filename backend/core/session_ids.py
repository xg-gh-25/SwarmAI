"""SDK session ids: the one pattern and the run.json transcript cap.

Kept free of imports so the pipeline CLI (each ``pipeline_v1.py`` call) can validate
an id without loading
core.transcript_usage -> leak_transcript -> session_utils -> config_settings ->
pydantic_settings (0.3 s per CLI call, measured 2026-10-20).

@exports SID_RE, MAX_TRANSCRIPT_IDS, valid_session_id
"""
import re

SID_RE = re.compile(r"^[0-9a-fA-F-]{8,64}$")
# A run touched by more sessions than this is not a real run (an edited run.json);
# the cap bounds the globs a stage command can be made to do.
MAX_TRANSCRIPT_IDS = 32


def valid_session_id(sid) -> bool:
    """True for an SDK session id: the pattern matched over the WHOLE string
    (``re.match`` with ``$`` would also take a trailing newline)."""
    return isinstance(sid, str) and bool(SID_RE.fullmatch(sid))
