"""Background model calls outside CDF that fail for lack of credentials reach the person.

- Every structured()/text() call passes ``jobs.bedrock._call``; a call that fails because
  no credentials resolve or Bedrock rejects them is noted in ``core.background_credentials``
  (source = the call's usage feature), any other failure is not, and the caller still
  gets its error. Session summarization and the session scorer, which only log such a
  failure, therefore leave a record.
- ``core.attention_authority`` shows ONE credentials item whether CDF, another caller or
  both failed; it is gone once a later call gets a reply (or CDF extracts again).
- The item's words and Settings tab follow how the install signs in to Bedrock: the AWS
  sign-in (Swarm AWS Account) or a Bedrock API key (LLM Models).

Only the Converse call is faked (a fake client), so the real structured()/_call run.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest
from botocore.exceptions import ClientError, NoCredentialsError

from core import background_credentials as bc
from jobs import bedrock

SCHEMA = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}


def _client_error(code: str, message: str = "") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message}}, "Converse")


class _Client:
    def __init__(self, outcome):
        self.outcome = outcome

    def converse(self, **_kwargs):
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


_REPLY = {"stopReason": "tool_use", "usage": {"inputTokens": 1, "outputTokens": 1},
          "output": {"message": {"content": [
              {"toolUse": {"name": "answer", "input": {"x": "ok"}}}]}}}


def _fake_bedrock(monkeypatch, outcome):
    """Both client paths of converse_with_retry return a client whose converse() gives
    *outcome* (an exception to raise or a reply)."""
    monkeypatch.setattr(bedrock, "get_client", lambda **_k: _Client(outcome))
    monkeypatch.setattr(bedrock, "build_timeout_client", lambda **_k: _Client(outcome))
    monkeypatch.setattr(bedrock, "evict_client", lambda: None)


def _ask(feature="test.feature", **kw):
    return bedrock.structured("p", schema=SCHEMA, model_id="us.anthropic.claude-sonnet-4-6",
                              max_tokens=10, region="us-east-1", feature=feature, **kw)


def _sources():
    return [r["source"] for r in bc.failures()]


# ── the record, written by jobs.bedrock ─────────────────────────────────────

@pytest.mark.parametrize("exc", [
    NoCredentialsError(),
    _client_error("ExpiredTokenException", "The security token included in the request is expired"),
    _client_error("UnrecognizedClientException", "The security token included in the request is invalid."),
])
def test_structured_records_a_credentials_failure(monkeypatch, exc):
    _fake_bedrock(monkeypatch, exc)
    with pytest.raises(type(exc)) as caught:
        _ask("summarize.session")
    assert caught.value is exc                      # the caller gets its own error, unchanged
    rows = bc.failures()
    assert [r["source"] for r in rows] == ["summarize.session"]
    assert rows[0]["reason"].startswith("judge_unavailable:aws_credentials")
    # the reason names the error class/code, never the message
    assert "security token" not in rows[0]["reason"]


def test_text_records_too_and_the_usage_attribute_names_the_source(monkeypatch):
    from core import usage_accounting
    _fake_bedrock(monkeypatch, NoCredentialsError())
    with usage_accounting.attribute("job.memory_health"), pytest.raises(NoCredentialsError):
        bedrock.text("p", model_id="us.anthropic.claude-sonnet-4-6", max_tokens=10,
                     region="us-east-1")
    assert _sources() == ["job.memory_health"]


@pytest.mark.parametrize("exc", [
    _client_error("ThrottlingException", "Too many requests"),
    _client_error("AccessDeniedException", "You don't have access to the model with the specified model ID."),
    _client_error("ValidationException", "bad input"),
    TimeoutError("Bedrock call exceeded 30s"),
    ValueError("token budget"),
])
def test_other_failures_are_not_recorded(monkeypatch, exc):
    _fake_bedrock(monkeypatch, exc)
    with pytest.raises(type(exc)):
        _ask()
    assert bc.failures() == []
    assert not (bc._path()).exists()


def test_a_reply_clears_the_record(monkeypatch):
    _fake_bedrock(monkeypatch, NoCredentialsError())
    with pytest.raises(NoCredentialsError):
        _ask("summarize.session")
    with pytest.raises(NoCredentialsError):
        _ask("eval.session_score")
    assert set(_sources()) == {"summarize.session", "eval.session_score"}
    _fake_bedrock(monkeypatch, _REPLY)
    assert _ask("job.skill_proposer") == {"x": "ok"}
    assert bc.failures() == []


def test_a_reply_the_caller_rejects_still_clears(monkeypatch):
    """A reply cut at max_tokens is a StructuredOutputError, but Bedrock answered: the
    credentials work."""
    _fake_bedrock(monkeypatch, NoCredentialsError())
    with pytest.raises(NoCredentialsError):
        _ask()
    _fake_bedrock(monkeypatch, {**_REPLY, "stopReason": "max_tokens"})
    with pytest.raises(bedrock.StructuredOutputError):
        _ask()
    assert bc.failures() == []


# ── the callers that only log ───────────────────────────────────────────────

def test_summarization_records_and_swallows(monkeypatch):
    from core.summarization import SummarizationPipeline
    _fake_bedrock(monkeypatch, NoCredentialsError())
    assert SummarizationPipeline._call_enrichment_llm("prompt") == {}   # still swallowed
    assert _sources() == ["summarize.session"]


def test_summarization_other_error_not_recorded(monkeypatch):
    from core.summarization import SummarizationPipeline
    _fake_bedrock(monkeypatch, _client_error("ThrottlingException", "slow down"))
    assert SummarizationPipeline._call_enrichment_llm("prompt") == {}
    assert bc.failures() == []


def test_session_scorer_records_and_fails_closed(monkeypatch):
    from core.session_scorer import default_judge_fn, score_session
    _fake_bedrock(monkeypatch, NoCredentialsError())
    out = score_session(prompt="do x", response="done", tool_names=[], judge_fn=default_judge_fn)
    assert out["status"] == "error" and out["goal_score"] is None
    assert _sources() == ["eval.session_score"]


def test_session_scorer_other_error_not_recorded(monkeypatch):
    from core.session_scorer import default_judge_fn, score_session
    _fake_bedrock(monkeypatch, _client_error("ThrottlingException", "slow down"))
    out = score_session(prompt="do x", response="done", tool_names=[], judge_fn=default_judge_fn)
    assert out["status"] == "error"
    assert bc.failures() == []


# ── bounds and robustness ───────────────────────────────────────────────────

def test_record_is_bounded():
    for i in range(bc.MAX_SOURCES + 8):
        assert bc.note_failure(f"job.n{i}", NoCredentialsError())
    assert bc.note_failure("job.n0", _client_error("ExpiredToken" + "x" * 500))
    rows = bc.failures()
    assert len(rows) == bc.MAX_SOURCES
    assert len({r["source"] for r in rows}) == len(rows)          # one entry per source
    assert rows[0]["source"] == "job.n0"                          # the newest kept
    assert all(len(r["reason"]) <= bc.MAX_REASON_CHARS for r in rows)
    assert "job.n1" not in {r["source"] for r in rows}            # the oldest dropped


def test_corrupt_record_is_moved_aside():
    path = bc._path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert bc.failures() == []                    # the reader reads only
    assert path.read_text(encoding="utf-8") == "{not json"
    assert bc.note_failure("summarize.session", NoCredentialsError())
    assert [p.name for p in path.parent.iterdir() if p.name.startswith(path.name + ".corrupt-")]
    assert _sources() == ["summarize.session"]


def test_record_errors_never_break_the_call(monkeypatch):
    def broken(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(bc, "_write", broken)
    _fake_bedrock(monkeypatch, NoCredentialsError())
    with pytest.raises(NoCredentialsError):
        _ask()
    bc._path().parent.mkdir(parents=True, exist_ok=True)
    bc._path().write_text(json.dumps({"failures": {"a": {"at": "2026-01-01T00:00:00+00:00",
                                                          "reason": "r"}}}))
    _fake_bedrock(monkeypatch, _REPLY)
    assert _ask() == {"x": "ok"}


# ── the attention item ──────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path / "SwarmWS"))
    from core.ingestion.store import connect_rw
    conn = connect_rw()
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    conn.execute("INSERT OR REPLACE INTO ingestion_owner(id,epoch,pid,started_at) VALUES(1,1,?,?)",
                 (os.getpid(), old))
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def via(monkeypatch):
    from core import aws_account
    mode = {"value": "aws"}
    monkeypatch.setattr(aws_account, "get_chat_via", lambda *_a, **_k: mode["value"])
    return mode


def _credential_items():
    from core import attention_authority as authority
    return [i for i in authority._collect_ingestion() if i.id == "ingestion:needs_account"]


def _fail_cdf(monkeypatch):
    from core.ingestion import llm
    from core.ingestion.sources import common, extract_llm

    def no_credentials(*_a, **_k):
        raise NoCredentialsError()

    monkeypatch.setattr(extract_llm, "structured", no_credentials)
    with pytest.raises(llm.JudgeCredentialsError):
        common.extract_and_enqueue("S2", "session:s1", "text", (), evidence_range={"from": 0},
                                   session_id="s1", allowed_kinds=("cognition",))


def _extract_ok(session="s2"):
    from core.ingestion import llm
    from core.ingestion.sources import common

    class _Empty:
        def extract(self, prompt):
            return llm.JudgeResult({}, {}, "{}")

    common.extract_and_enqueue("S2", f"session:{session}", "text", (), evidence_range={"from": 1},
                               session_id=session, allowed_kinds=("cognition",), llm=_Empty())


def test_item_from_the_shared_record_alone(store, via):
    before = store.execute("SELECT COUNT(*) FROM extraction_attempts").fetchone()[0]
    bc.note_failure("summarize.session", NoCredentialsError())
    items = _credential_items()
    assert len(items) == 1
    assert "session summaries" in items[0].detail
    # nothing was written into CDF's own tables
    assert store.execute("SELECT COUNT(*) FROM extraction_attempts").fetchone()[0] == before


def test_item_without_the_cdf_store(tmp_path, monkeypatch, via):
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path / "empty"))
    bc.note_failure("eval.session_score", NoCredentialsError())
    assert len(_credential_items()) == 1


def test_one_item_when_cdf_and_others_fail(store, via, monkeypatch):
    _fail_cdf(monkeypatch)
    bc.note_failure("summarize.session", NoCredentialsError())
    bc.note_failure("eval.session_score", NoCredentialsError())
    items = _credential_items()
    assert len(items) == 1
    assert "Cognitive Distillation" in items[0].detail and "session scoring" in items[0].detail


def test_item_clears_after_a_reply(store, via, monkeypatch):
    _fake_bedrock(monkeypatch, NoCredentialsError())
    with pytest.raises(NoCredentialsError):
        _ask("summarize.session")
    assert len(_credential_items()) == 1
    _fake_bedrock(monkeypatch, _REPLY)
    _ask("eval.session_score")
    assert _credential_items() == []


def test_older_than_cdf_success_is_ignored(store, via, monkeypatch):
    """A CDF extraction that got a reply from Bedrock clears the record (it passes the
    same jobs.bedrock._call); a CDF attempt recorded without a model call (an empty S4
    batch, an S5 window: status no_unit) proves nothing and hides nothing."""
    from core.ingestion.sources import common
    bc.note_failure("job.skill_proposer", NoCredentialsError())
    _extract_ok()                 # recorded as finished, but no model was called
    assert len(_credential_items()) == 1
    from core.ingestion.sources.extract_llm import TOOL_NAME
    _fake_bedrock(monkeypatch, {**_REPLY, "output": {"message": {"content": [
        {"toolUse": {"name": TOOL_NAME, "input": {"cognition_units": []}}}]}}})
    common.extract_and_enqueue("S2", "session:s3", "text", (), evidence_range={"from": 2},
                               session_id="s3", allowed_kinds=("cognition",))
    assert _credential_items() == []
    bc.note_failure("job.skill_proposer", NoCredentialsError())   # a newer failure shows again
    assert len(_credential_items()) == 1


def test_wording_for_aws_sign_in(store, via):
    via["value"] = "aws"
    bc.note_failure("summarize.session", NoCredentialsError())
    item = _credential_items()[0]
    assert item.dispatch["context"] == {"kind": "aws_account", "settings_tab": "aws-account"}
    assert "Settings → Swarm AWS Account" in item.dispatch["message"]
    assert "profile or SSO" in item.detail
    assert "API key" not in item.detail + item.dispatch["message"]


def test_wording_for_bedrock_api_key(store, via):
    via["value"] = "bedrock_key"
    bc.note_failure("summarize.session", NoCredentialsError())
    item = _credential_items()[0]
    assert item.dispatch["context"] == {"kind": "aws_account", "settings_tab": "ai-models"}
    text = item.title + " " + item.detail + " " + item.dispatch["message"]
    assert "Settings → LLM Models" in item.dispatch["message"] and "Bedrock API key" in text
    assert "sign in" not in text.lower() and "AWS account" not in text
