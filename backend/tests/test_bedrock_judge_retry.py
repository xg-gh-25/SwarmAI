"""Tests for OS-Eval judge credential resilience (run_72b01506).

What is tested: the bedrock judge path now self-heals on a transient stale-credential
error mid-eval, instead of zeroing out every LLM-judged golden case (90/147 errored
in the 2026-06-28 nightly, all identical "unable to assume credentials" failures).

Methodology / key invariants:
  AC1 — converse_with_retry evicts the cached client and retries EXACTLY ONCE on a
        credential/auth error on the first attempt; the 2nd (fresh) call succeeds.
  AC2 — a NON-auth error raises immediately: no evict, no retry (a real bug is not
        masked, the loop is not wasted).
  AC3 — the real eval_llm_judge path self-heals end-to-end (drives the REAL
        converse_with_retry, mocks only the get_client boundary — GUI32/PIT13).
  AC4 — happy path unchanged: success on first try => 1 converse call, 0 evict.

Mock boundary: jobs.bedrock.get_client + jobs.bedrock.evict_client only. The function
under change (converse_with_retry) runs for real — never mocked (GUI32).
"""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# backend/ on path (tests run from repo root or backend/)
_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def _valid_converse_response(text='{"verdict": "passed", "confidence": 0.9, "notes": "ok"}'):
    """Shape of a real bedrock converse() output the judge parses."""
    return {"output": {"message": {"content": [{"text": text}]}}}


def _verdict_tool_response(answer):
    """A converse() reply whose answer is a call to the judges' forced "verdict" tool."""
    return {"stopReason": "tool_use", "output": {"message": {"content": [
        {"toolUse": {"toolUseId": "t", "name": "verdict", "input": answer}}]}}}


def _auth_error():
    # Mirrors the real nightly failure string (account-assume failure).
    return Exception(
        "Error when retrieving credentials from custom-process: "
        "unable to assume credentials for account 000000000000"
    )


# ─── AC1: evict + retry-once on auth error ──────────────────────────────────
def test_converse_with_retry_evicts_and_retries_on_auth_error():
    from jobs import bedrock

    client = MagicMock()
    # 1st converse raises auth error, 2nd (after evict + fresh client) succeeds.
    client.converse.side_effect = [_auth_error(), _valid_converse_response()]

    with patch.object(bedrock, "get_client", return_value=client) as mock_get, \
         patch.object(bedrock, "evict_client") as mock_evict:
        resp = bedrock.converse_with_retry(
            messages=[{"role": "user", "content": [{"text": "hi"}]}],
            system=[{"text": "judge"}],
            inference_config={"maxTokens": 100, "temperature": 0.0},
            model_id="us.anthropic.claude-opus-4-6-v1",
        )

    assert client.converse.call_count == 2, "should retry exactly once after auth error"
    mock_evict.assert_called_once(), "must evict the stale client before retry"
    # 2nd attempt asks for a fresh client (force_new=True)
    assert mock_get.call_args_list[-1].kwargs.get("force_new") is True
    assert resp["output"]["message"]["content"][0]["text"].startswith('{"verdict"')


# ─── AC2: non-auth error raises immediately, no retry/evict ─────────────────
def test_converse_with_retry_does_not_retry_non_auth_error():
    from jobs import bedrock

    client = MagicMock()
    client.converse.side_effect = ValueError("ValidationException: model id is invalid")

    with patch.object(bedrock, "get_client", return_value=client), \
         patch.object(bedrock, "evict_client") as mock_evict:
        with pytest.raises(ValueError):
            bedrock.converse_with_retry(
                messages=[{"role": "user", "content": [{"text": "hi"}]}],
                system=[{"text": "judge"}],
                inference_config={"maxTokens": 100, "temperature": 0.0},
                model_id="bad-model",
            )

    assert client.converse.call_count == 1, "non-auth error must NOT trigger a retry"
    mock_evict.assert_not_called()


# ─── AC1b: second attempt ALSO fails → raises, no third attempt ─────────────
def test_converse_with_retry_raises_when_retry_also_auth_fails():
    """Bound proof: a persistent auth failure retries ONCE then raises (no loop)."""
    from jobs import bedrock

    client = MagicMock()
    client.converse.side_effect = [_auth_error(), _auth_error()]  # both attempts fail

    with patch.object(bedrock, "get_client", return_value=client), \
         patch.object(bedrock, "evict_client") as mock_evict:
        with pytest.raises(Exception):
            bedrock.converse_with_retry(
                messages=[{"role": "user", "content": [{"text": "hi"}]}],
                system=[{"text": "judge"}],
                inference_config={"maxTokens": 100, "temperature": 0.0},
                model_id="m",
            )

    assert client.converse.call_count == 2, "retries exactly once then raises — never a 3rd attempt"
    mock_evict.assert_called_once(), "evicts once (on the first failure) before the single retry"


# ─── AC4: happy path unchanged ──────────────────────────────────────────────
def test_converse_with_retry_happy_path_single_call():
    from jobs import bedrock

    client = MagicMock()
    client.converse.return_value = _valid_converse_response()

    with patch.object(bedrock, "get_client", return_value=client), \
         patch.object(bedrock, "evict_client") as mock_evict:
        resp = bedrock.converse_with_retry(
            messages=[{"role": "user", "content": [{"text": "hi"}]}],
            system=[{"text": "judge"}],
            inference_config={"maxTokens": 100, "temperature": 0.0},
            model_id="us.anthropic.claude-opus-4-6-v1",
        )

    assert client.converse.call_count == 1, "happy path makes exactly one converse call"
    mock_evict.assert_not_called()
    assert resp == _valid_converse_response()


def test_converse_with_retry_region_is_passed_through():
    """Gate-1 mitigation: judge keeps its env-first region (no silent AppConfigManager switch)."""
    from jobs import bedrock

    client = MagicMock()
    client.converse.return_value = _valid_converse_response()

    with patch.object(bedrock, "get_client", return_value=client) as mock_get, \
         patch.object(bedrock, "evict_client"):
        bedrock.converse_with_retry(
            messages=[{"role": "user", "content": [{"text": "hi"}]}],
            system=[{"text": "judge"}],
            inference_config={"maxTokens": 100, "temperature": 0.0},
            model_id="m",
            region="ap-southeast-1",
        )

    # region must reach get_client so the judge's resolved region is honored.
    assert mock_get.call_args_list[0].kwargs.get("region") == "ap-southeast-1"


# ─── AC3: real eval_llm_judge self-heals end-to-end ─────────────────────────
def test_eval_llm_judge_self_heals_on_transient_auth_error():
    """Drive the REAL eval_llm_judge; only the get_client boundary is mocked (GUI32)."""
    import importlib.util

    runner_path = _BACKEND / "scripts" / "eval_runner.py"
    spec = importlib.util.spec_from_file_location("eval_runner_under_test", runner_path)
    eval_runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(eval_runner)

    from jobs import bedrock

    client = MagicMock()
    # auth error on first converse, valid judge verdict on retry
    client.converse.side_effect = [
        _auth_error(),
        _verdict_tool_response({"verdict": "passed", "assertion_results": [], "confidence": 0.95, "notes": "self-healed"}),
    ]

    case = {
        "id": "GS_TEST_SELFHEAL",
        "assertions": ["the agent does the right thing"],
        "scenario": {"turns": [{"input": "do the thing"}]},
    }

    # eval_llm_judge now runs in FAIL-FAST mode (read_timeout=30) → the boundary
    # is build_timeout_client (throwaway), which still self-heals on auth error.
    with patch.object(bedrock, "build_timeout_client", return_value=client), \
         patch.object(bedrock, "get_client", return_value=client), \
         patch.object(bedrock, "evict_client"), \
         patch.object(eval_runner, "_get_judge_model", return_value="us.anthropic.claude-opus-4-6-v1"):
        result = eval_runner.eval_llm_judge(case, "goal_success")

    assert result["status"] != "error", f"judge should self-heal, got: {result}"
    assert result["status"] == "passed"
    assert client.converse.call_count == 2


# ─── FAIL-FAST (run_9fdb8ad5): throwaway tight-timeout judge client ─────────
# A hung judge on the shared 120s client blows the serial eval wall. When
# read_timeout is set, converse_with_retry must use a THROWAWAY client
# (build_timeout_client) and NOT the shared cached client / evict path.

def test_failfast_uses_throwaway_client_not_shared_cache():
    """read_timeout set → build_timeout_client used; get_client + evict untouched."""
    from jobs import bedrock

    client = MagicMock()
    client.converse.return_value = _valid_converse_response()

    with patch.object(bedrock, "build_timeout_client", return_value=client) as mock_build, \
         patch.object(bedrock, "get_client") as mock_get, \
         patch.object(bedrock, "evict_client") as mock_evict:
        resp = bedrock.converse_with_retry(
            messages=[{"role": "user", "content": [{"text": "hi"}]}],
            system=[{"text": "judge"}],
            inference_config={"maxTokens": 400, "temperature": 0.0},
            model_id="m",
            region="us-east-1",
            read_timeout=30,
            max_attempts=1,
        )

    # fail-fast path: throwaway client with the requested timeouts + region
    mock_build.assert_called_once()
    assert mock_build.call_args.kwargs.get("read_timeout") == 30
    assert mock_build.call_args.kwargs.get("max_attempts") == 1
    assert mock_build.call_args.kwargs.get("region") == "us-east-1"
    # shared cache is NEVER touched in fail-fast mode
    mock_get.assert_not_called()
    mock_evict.assert_not_called()
    assert client.converse.call_count == 1
    assert resp == _valid_converse_response()


def test_failfast_retries_once_on_auth_with_fresh_throwaway_client():
    """Fail-fast KEEPS the one auth-evict-retry (2026-06-28 self-heal), but via a
    FRESH throwaway client each attempt — never the shared cache."""
    from jobs import bedrock

    client = MagicMock()
    client.converse.side_effect = [_auth_error(), _valid_converse_response()]

    with patch.object(bedrock, "build_timeout_client", return_value=client) as mock_build, \
         patch.object(bedrock, "get_client") as mock_get, \
         patch.object(bedrock, "evict_client") as mock_evict:
        resp = bedrock.converse_with_retry(
            messages=[{"role": "user", "content": [{"text": "hi"}]}],
            system=[{"text": "judge"}],
            inference_config={"maxTokens": 400, "temperature": 0.0},
            model_id="m",
            read_timeout=30,
        )

    assert client.converse.call_count == 2, "auth error → one retry"
    assert mock_build.call_count == 2, "each attempt builds a FRESH throwaway client"
    mock_get.assert_not_called(), "shared cache never used in fail-fast"
    mock_evict.assert_not_called(), "shared cache never evicted in fail-fast"
    assert resp == _valid_converse_response()


def test_failfast_non_auth_error_raises_immediately():
    """Fail-fast: a NON-auth error raises on the first attempt (no wasted retry)."""
    from jobs import bedrock

    client = MagicMock()
    client.converse.side_effect = ValueError("ValidationException: bad model")

    with patch.object(bedrock, "build_timeout_client", return_value=client) as mock_build, \
         patch.object(bedrock, "get_client") as mock_get:
        with pytest.raises(ValueError):
            bedrock.converse_with_retry(
                messages=[{"role": "user", "content": [{"text": "hi"}]}],
                system=[{"text": "judge"}],
                inference_config={"maxTokens": 400, "temperature": 0.0},
                model_id="m",
                read_timeout=30,
            )

    assert client.converse.call_count == 1, "non-auth error must NOT retry"
    assert mock_build.call_count == 1
    mock_get.assert_not_called()


def test_default_path_unchanged_uses_shared_client():
    """No read_timeout → the shared cached get_client path (byte-identical to before)."""
    from jobs import bedrock

    client = MagicMock()
    client.converse.return_value = _valid_converse_response()

    with patch.object(bedrock, "build_timeout_client") as mock_build, \
         patch.object(bedrock, "get_client", return_value=client) as mock_get, \
         patch.object(bedrock, "evict_client"):
        bedrock.converse_with_retry(
            messages=[{"role": "user", "content": [{"text": "hi"}]}],
            system=[{"text": "judge"}],
            inference_config={"maxTokens": 100, "temperature": 0.0},
            model_id="m",
        )

    # default path: shared cached client, throwaway NEVER built
    mock_get.assert_called()
    mock_build.assert_not_called()


def test_build_timeout_client_config_and_no_cache_mutation():
    """build_timeout_client sets the tight config and does NOT touch module cache."""
    from jobs import bedrock

    # snapshot module cache
    bedrock._client = "SENTINEL_SHARED_CLIENT"
    fake = MagicMock()
    captured = {}

    def _fake_boto_client(service, **kw):
        captured["service"] = service
        captured["config"] = kw.get("config")
        captured["region"] = kw.get("region_name")
        return fake

    class _Session:
        def client(self, service, **kw):
            return _fake_boto_client(service, **kw)

    with patch.object(bedrock, "_resolve_credentials", return_value={}), \
         patch("boto3.session.Session", lambda *a, **k: _Session()):
        out = bedrock.build_timeout_client(read_timeout=30, max_attempts=1, region="us-east-1")

    assert out is fake
    assert captured["service"] == "bedrock-runtime"
    assert captured["region"] == "us-east-1"
    assert captured["config"].read_timeout == 30
    assert captured["config"].retries["max_attempts"] == 1
    # the shared module cache is untouched by building a throwaway client
    assert bedrock._client == "SENTINEL_SHARED_CLIENT"
    bedrock._client = None  # cleanup


def test_eval_llm_judge_uses_failfast_timeout_and_keeps_throttle_retry():
    """The REAL judge passes read_timeout=60 (anti-hang) AND max_attempts=2
    (Gate-2: keep boto throttle-retry) to the throwaway client."""
    import importlib.util

    runner_path = _BACKEND / "scripts" / "eval_runner.py"
    spec = importlib.util.spec_from_file_location("eval_runner_failfast_cfg", runner_path)
    eval_runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(eval_runner)

    from jobs import bedrock

    captured = {}

    def _capturing_converse(**kwargs):
        captured.update(kwargs)
        return _verdict_tool_response({"verdict": "passed", "assertion_results": [], "confidence": 0.9, "notes": "ok"})

    case = {
        "id": "GS_TEST_FFCFG",
        "assertions": ["the agent does the right thing"],
        "scenario": {"turns": [{"input": "do the thing"}]},
    }

    with patch.object(bedrock, "converse_with_retry", side_effect=_capturing_converse), \
         patch.object(eval_runner, "_get_judge_model", return_value="us.anthropic.claude-opus-4-6-v1"):
        result = eval_runner.eval_llm_judge(case, "goal_success")

    assert result["status"] == "passed"
    assert captured.get("read_timeout") == 60, "judge must use the 60s fail-fast read timeout"
    assert captured.get("max_attempts") == 2, "judge must keep boto throttle-retry (Gate-2 MEDIUM)"


def test_failfast_call_ends_when_the_socket_read_never_returns(monkeypatch):
    """A CDF judge call sat in an SSL read for 90 minutes with read_timeout=25 set, and the
    worker thread with it: nothing in the session lane was judged (2026-10-29). The call
    is bounded by the timeouts it was configured with; past that it is abandoned and
    raises, so the caller defers the unit and moves on."""
    import threading
    import time
    from jobs import bedrock
    never = threading.Event()
    client = MagicMock()
    client.converse.side_effect = lambda **kw: never.wait(10)
    monkeypatch.setattr(bedrock, "_wall_bound", lambda read_timeout, attempts: 0.3)
    with patch.object(bedrock, "build_timeout_client", return_value=client):
        start = time.monotonic()
        with pytest.raises(TimeoutError):
            bedrock.converse_with_retry(messages=[], system=[], inference_config={},
                                        model_id="m", read_timeout=25, max_attempts=2)
    assert time.monotonic() - start < 5
    never.set()


def test_the_wall_bound_is_the_configured_timeouts():
    from jobs import bedrock
    assert bedrock._wall_bound(25, 2) == (bedrock.CONNECT_TIMEOUT + 25) * 2 + bedrock.WALL_GRACE


def test_build_timeout_client_uses_its_own_session():
    """boto3's default session is not thread-safe, and the CDF worker, the session-job
    thread and the run lane build these clients at the same time."""
    from jobs import bedrock
    sessions = []

    class FakeSession:
        def __init__(self, *a, **k):
            sessions.append(self)

        def client(self, service, **kw):
            return ("client", service, kw.get("config").read_timeout)

    with patch.object(bedrock, "_resolve_credentials", return_value={}), \
         patch("boto3.session.Session", FakeSession), \
         patch("boto3.client", side_effect=AssertionError("default session used")):
        out = bedrock.build_timeout_client(read_timeout=30, max_attempts=1, region="us-east-1")
    assert out == ("client", "bedrock-runtime", 30) and len(sessions) == 1
