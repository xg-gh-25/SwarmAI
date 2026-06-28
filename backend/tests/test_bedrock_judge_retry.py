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


def _auth_error():
    # Mirrors the real nightly failure string (account-assume failure).
    return Exception(
        "Error when retrieving credentials from custom-process: "
        "unable to assume credentials for account 533267412361"
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
        _valid_converse_response('{"verdict": "passed", "assertion_results": [], "confidence": 0.95, "notes": "self-healed"}'),
    ]

    case = {
        "id": "GS_TEST_SELFHEAL",
        "assertions": ["the agent does the right thing"],
        "scenario": {"turns": [{"input": "do the thing"}]},
    }

    with patch.object(bedrock, "get_client", return_value=client), \
         patch.object(bedrock, "evict_client"), \
         patch.object(eval_runner, "_get_judge_model", return_value="us.anthropic.claude-opus-4-6-v1"):
        result = eval_runner.eval_llm_judge(case, "goal_success")

    assert result["status"] != "error", f"judge should self-heal, got: {result}"
    assert result["status"] == "passed"
    assert client.converse.call_count == 2
