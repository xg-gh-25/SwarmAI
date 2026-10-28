"""Bedrock usage the CDF spends, per UTC day and source (the one record of it).

- ``attribute(source)`` names the CDF source the Bedrock calls inside it are spent for.
  The worker sets it per candidate, the source extractor per extraction.
- ``record(usage)`` adds one call's ``input_tokens`` / ``output_tokens`` to
  ``ingestion_llm_usage`` under the current source ("-" when none is set). Fail-open:
  a failed write is logged and never fails the judge or extractor.
- ``totals()`` sums every recorded call (the rehearsal replay's budget reads it).

Calls run outside any worker transaction (judging and extraction never hold the
ingestion write lock), so the short write here does not wait on the caller.
"""
from __future__ import annotations

import contextlib
import contextvars
import logging
from datetime import datetime, timezone
from typing import Any, Iterator

logger = logging.getLogger(__name__)

_SOURCE: contextvars.ContextVar[str] = contextvars.ContextVar("cdf_llm_source", default="-")


@contextlib.contextmanager
def attribute(source: str | None) -> Iterator[None]:
    """Attribute the Bedrock calls made inside this block to ``source``."""
    token = _SOURCE.set(str(source or "-"))
    try:
        yield
    finally:
        _SOURCE.reset(token)


def current_source() -> str:
    return _SOURCE.get()


def record(usage: dict[str, Any]) -> None:
    """Add one Bedrock call to today's row for the current source."""
    day = datetime.now(timezone.utc).date().isoformat()
    tokens_in = int(usage.get("input_tokens") or 0)
    tokens_out = int(usage.get("output_tokens") or 0)
    try:
        from .api import connect_rw
        conn = connect_rw(busy_timeout_ms=2000)
        try:
            conn.execute(
                """INSERT INTO ingestion_llm_usage(utc_day,source,calls,input_tokens,output_tokens)
                   VALUES (?,?,1,?,?)
                   ON CONFLICT(utc_day,source) DO UPDATE SET calls=calls+1,
                       input_tokens=input_tokens+excluded.input_tokens,
                       output_tokens=output_tokens+excluded.output_tokens""",
                (day, current_source(), tokens_in, tokens_out),
            )
        finally:
            conn.close()
    except Exception:
        logger.warning("CDF LLM usage not recorded (source=%s)", current_source(), exc_info=True)


def totals() -> dict[str, int]:
    """Sum of every recorded call: ``calls``, ``input_tokens``, ``output_tokens``."""
    from .api import connect_rw
    conn = connect_rw()
    try:
        row = conn.execute(
            """SELECT COALESCE(SUM(calls),0), COALESCE(SUM(input_tokens),0),
                      COALESCE(SUM(output_tokens),0) FROM ingestion_llm_usage"""
        ).fetchone()
    finally:
        conn.close()
    return {"calls": int(row[0]), "input_tokens": int(row[1]), "output_tokens": int(row[2])}
