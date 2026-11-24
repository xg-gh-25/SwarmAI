"""One "the model could not be reached" predicate for every CDF lane
(core/ingestion/unreachable.py).

What is tested:
- The month-summary lane and the session review read the same function
  (``summary_lane.model_unreachable is s2_jobs.model_unreachable``).
- The summary lane, writing through the Bedrock SDK, postpones (re-raises without counting
  a failure, no summary_failed) when Converse refuses the call for its region (a
  ValidationException whose message is the region block) and when the connection is
  refused, in botocore's form and as a raw ConnectionRefusedError.
- It still counts a real model error (another ValidationException, a throttle) and raises
  summary_failed after three.
- The predicate's exception form: the SDK shapes above and credentials count; a
  ClientError that only denies the model, and a non-SDK error whose text quotes the region
  block, do not; an exception carrying the CLI's own error result counts, as its text does.
"""
from __future__ import annotations

import errno
import json
import sqlite3
from datetime import datetime, timezone

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError, NoCredentialsError

from core.ingestion import s2_jobs, summary_lane
from core.ingestion.unreachable import model_unreachable

REGION_MESSAGE = ("Access to Anthropic models is not allowed from unsupported countries, "
                  "regions, or territories. Please refer to supported countries.")
CLI_REGION = ("review failed: success: API Error: 400 Access to Anthropic models is not "
              "allowed from unsupported countries, regions, or territories")
NOW = datetime(2026, 11, 18, tzinfo=timezone.utc)


def _client_error(code: str, message: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message},
                        "ResponseMetadata": {"HTTPStatusCode": 400}}, "Converse")


def _region_block() -> ClientError:
    return _client_error("ValidationException", REGION_MESSAGE)


def _refused_endpoint() -> EndpointConnectionError:
    try:
        try:
            raise ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused")
        except ConnectionRefusedError as inner:
            raise EndpointConnectionError(
                endpoint_url="https://bedrock-runtime.us-east-1.amazonaws.com/") from inner
    except EndpointConnectionError as exc:
        return exc


@pytest.fixture
def lane(tmp_path, monkeypatch):
    """A summary lane over one month with one event, whose model call raises
    ``state["error"]()`` from the Bedrock SDK (jobs.bedrock.structured)."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE ingestion_inbox(source_ref TEXT, payload TEXT, created_at TEXT)")
    conn.execute("CREATE TABLE ingestion_events(at TEXT, kind TEXT, detail TEXT)")
    monkeypatch.setattr(summary_lane, "_month_units", lambda *_: [
        (".context/TIMELINE.md#2026-10#cand:e1", "### 2026-10-03 Decided one writer")])
    state: dict = {"conn": conn, "lane": summary_lane.SummaryLane(tmp_path), "calls": 0}

    def structured(*_a, **_k):
        state["calls"] += 1
        raise state["error"]()

    monkeypatch.setattr("jobs.bedrock.structured", structured)
    yield state
    conn.close()


def _run(state, times: int) -> None:
    for _ in range(times):
        with pytest.raises(summary_lane.SummaryNotWritten):
            state["lane"].run_due(state["conn"], now=NOW)


def _events(conn) -> list[tuple[str, dict]]:
    return [(kind, json.loads(detail)) for kind, detail in
            conn.execute("SELECT kind, detail FROM ingestion_events")]


def test_both_lanes_read_one_predicate():
    assert summary_lane.model_unreachable is s2_jobs.model_unreachable is model_unreachable


@pytest.mark.parametrize("error", [
    _region_block,
    _refused_endpoint,
    lambda: ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused"),
], ids=["region-block-validation", "refused-endpoint", "connection-refused"])
def test_the_summary_lane_postpones_when_the_model_cannot_be_reached(lane, error):
    lane["error"] = error
    _run(lane, 4)                       # more than the three that raise summary_failed
    assert lane["calls"] == 4           # the model was really called each time
    assert lane["lane"].consecutive_failures == 0
    assert _events(lane["conn"]) == []


@pytest.mark.parametrize("error", [
    lambda: _client_error("ValidationException", "Input is too long for requested model."),
    lambda: _client_error("ThrottlingException", "Too many requests, please wait."),
    lambda: _client_error("AccessDeniedException",
                          "You don't have access to the model with the specified model ID."),
], ids=["validation", "throttle", "model-access"])
def test_the_summary_lane_still_counts_a_real_model_error(lane, error):
    lane["error"] = error
    _run(lane, 3)
    assert lane["lane"].consecutive_failures == 3
    (kind, detail), = _events(lane["conn"])
    assert kind == "summary_failed" and detail["failures"] == 3


def test_the_exception_form():
    assert model_unreachable(_region_block())
    assert model_unreachable(_client_error("AccessDeniedException", REGION_MESSAGE))
    assert model_unreachable(_refused_endpoint())
    assert model_unreachable(ConnectionRefusedError())
    assert model_unreachable(OSError(errno.ECONNREFUSED, "Connection refused"))
    assert model_unreachable(NoCredentialsError())
    try:                                # wrapped, as enqueue_month_summary wraps the writer
        try:
            raise _region_block()
        except ClientError as inner:
            raise summary_lane.SummaryNotWritten(f"writer failed: {inner}") from inner
    except summary_lane.SummaryNotWritten as outer:
        assert model_unreachable(outer)
    assert model_unreachable(RuntimeError(CLI_REGION))           # the CLI's error result
    for other in (RuntimeError("the summary itself was bad"),
                  _client_error("ValidationException", "Input is too long"),
                  _client_error("AccessDeniedException", "You don't have access to the model"),
                  OSError(5, "Input/output error"),
                  RuntimeError("reply missing: no structured output (" + REGION_MESSAGE + ")"),
                  RuntimeError("reply missing: no structured output (" + CLI_REGION + ")")):
        assert not model_unreachable(other), other


def test_the_text_form_is_only_the_clis_error_result():
    assert model_unreachable(CLI_REGION) and model_unreachable("RuntimeError: " + CLI_REGION)
    for other in (None, "", REGION_MESSAGE, "ClientError: " + REGION_MESSAGE,
                  "reply missing: no structured output (" + CLI_REGION + ")"):
        assert not model_unreachable(other), other
