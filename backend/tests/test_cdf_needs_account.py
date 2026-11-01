"""Background cognition that fails for lack of AWS credentials says so (R2.8).

- A Bedrock call of the CDF extractor that fails because no AWS credentials resolve, or
  because AWS rejects them, is recorded with the reason ``judge_unavailable:aws_credentials``
  (core/ingestion/llm.py); any other failure is recorded as before.
- The attention overlay then shows ONE item, "Background cognition needs the SwarmAI AWS
  account", that opens Settings -> SwarmAI AWS Account, instead of a generic failure.
- Once an extraction succeeds again the item is gone.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
from botocore.exceptions import ClientError, NoCredentialsError

from core.ingestion import llm


def _client_error(code: str, message: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message}}, "Converse")


@pytest.mark.parametrize("exc", [
    NoCredentialsError(),
    _client_error("ExpiredTokenException", "The security token included in the request is expired"),
    _client_error("UnrecognizedClientException", "The security token included in the request is invalid."),
    _client_error("InvalidSignatureException", "The request signature we calculated does not match"),
    _client_error("AccessDeniedException", "Authentication failed: invalid API key"),
])
def test_credentials_failures_are_recognised(exc):
    assert llm.failure_reason(exc) == llm.AWS_CREDENTIALS_REASON
    wrapped = RuntimeError("outer")
    wrapped.__cause__ = exc
    assert llm.failure_reason(wrapped) == llm.AWS_CREDENTIALS_REASON


@pytest.mark.parametrize("exc", [
    _client_error("AccessDeniedException", "You don't have access to the model with the specified model ID."),
    _client_error("ThrottlingException", "Too many requests"),
    ValueError("token budget"),
])
def test_other_failures_are_not(exc):
    assert llm.failure_reason(exc) is None


def test_extractor_records_the_credentials_reason(monkeypatch):
    from core.ingestion.sources import extract_llm

    def no_credentials(*_a, **_k):
        raise NoCredentialsError()

    monkeypatch.setattr(extract_llm, "structured", no_credentials)
    with pytest.raises(llm.JudgeCredentialsError) as caught:
        extract_llm.BedrockExtractor(("cognition",)).extract("p")
    assert str(caught.value).startswith(llm.AWS_CREDENTIALS_REASON)
    assert isinstance(caught.value.__cause__, NoCredentialsError)


def test_extractor_other_errors_unchanged(monkeypatch):
    from core.ingestion.sources import extract_llm

    def boom(*_a, **_k):
        raise _client_error("ThrottlingException", "slow down")

    monkeypatch.setattr(extract_llm, "structured", boom)
    with pytest.raises(ClientError):
        extract_llm.BedrockExtractor(("cognition",)).extract("p")


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


def _fail_one_extraction(monkeypatch):
    from core.ingestion.sources import common, extract_llm

    def no_credentials(*_a, **_k):
        raise NoCredentialsError()

    monkeypatch.setattr(extract_llm, "structured", no_credentials)
    with pytest.raises(llm.JudgeCredentialsError):
        common.extract_and_enqueue("S2", "session:s1", "text", (), evidence_range={"from": 0},
                                   session_id="s1", allowed_kinds=("cognition",))


def test_one_needs_account_item(store, monkeypatch):
    from core import attention_authority as authority
    _fail_one_extraction(monkeypatch)
    _fail_one_extraction(monkeypatch)
    # a session job that failed the same way, and its poison event
    now = datetime.now(timezone.utc).isoformat()
    store.execute("""INSERT INTO ingestion_s2_jobs(session_id,reason,state,attempts,requested_at,error)
                     VALUES('s1','session_end','failed',3,?,?)""",
                  (now, f"JudgeCredentialsError: {llm.AWS_CREDENTIALS_REASON}: NoCredentialsError"))
    store.execute("INSERT INTO ingestion_events(at,kind,detail,resolved) VALUES(?,?,?,0)",
                  (now, "poison", '{"reason": "%s"}' % llm.AWS_CREDENTIALS_REASON))
    store.commit()

    items = authority._collect_ingestion()
    account = [i for i in items if i.title == "Background cognition needs the SwarmAI AWS account"]
    assert len(account) == 1
    item = account[0]
    assert item.dispatch["context"] == {"kind": "aws_account", "settings_tab": "aws-account"}
    assert "Settings" in item.dispatch["message"] and "SwarmAI AWS Account" in item.dispatch["message"]
    # the generic "repeatedly failed" item is not shown for the same cause
    assert not [i for i in items if i.dispatch["context"].get("kind") == "poison"]


def test_item_clears_after_a_successful_extraction(store, monkeypatch):
    from core import attention_authority as authority
    from core.ingestion.sources import common
    _fail_one_extraction(monkeypatch)
    assert any(i.id == "ingestion:needs_account" for i in authority._collect_ingestion())

    class _Empty:
        def extract(self, prompt):
            return llm.JudgeResult({}, {}, "{}")

    common.extract_and_enqueue("S2", "session:s1", "text", (), evidence_range={"from": 1},
                               session_id="s1", allowed_kinds=("cognition",), llm=_Empty())
    assert not any(i.id == "ingestion:needs_account" for i in authority._collect_ingestion())


def test_a_stale_failed_job_does_not_keep_the_item_after_a_success(store, monkeypatch):
    """A session job that ran out of attempts while credentials were missing stays
    'failed' until that session is reopened; once a later extraction succeeds the
    credentials work again, so the item goes."""
    from core import attention_authority as authority
    from core.ingestion.sources import common
    now = datetime.now(timezone.utc).isoformat()
    store.execute("""INSERT INTO ingestion_s2_jobs(session_id,reason,state,attempts,requested_at,error)
                     VALUES('old','session_end','failed',3,?,?)""",
                  (now, f"JudgeCredentialsError: {llm.AWS_CREDENTIALS_REASON}: NoCredentialsError"))
    store.commit()
    assert any(i.id == "ingestion:needs_account" for i in authority._collect_ingestion())

    class _Empty:
        def extract(self, prompt):
            return llm.JudgeResult({}, {}, "{}")

    common.extract_and_enqueue("S2", "session:s2", "text", (), evidence_range={"from": 1},
                               session_id="s2", allowed_kinds=("cognition",), llm=_Empty())
    assert not any(i.id == "ingestion:needs_account" for i in authority._collect_ingestion())
