"""The Bedrock model id's profile geography follows the region the call is made from.

Design docs/design/2026-11-01-bedrock-id-follows-region.md. A ``us.`` inference profile
can be called only from a US region, so with the main region eu-west-1 every model
call must send ``eu.anthropic.*``. Tested:

- the rule itself (``model_registry.geo_for_region`` / ``regional_id`` /
  ``is_full_bedrock_id``), table-driven, including what must NOT change;
- the two exits: ``config.get_bedrock_model_id`` (chat, job CLIs, verify) and
  ``jobs.bedrock.converse_with_retry`` (every direct model call: its ``modelId``);
- the CLI's ``AWS_REGION`` comes from the same reader as the id's geography;
- the checks that used to accept only ``us.`` / ``global.`` ids accept any geography;
- the fallback price and model discovery are geography-aware.
"""
from __future__ import annotations

import os

import pytest

import model_registry as reg


@pytest.mark.parametrize("region,geo", [
    ("us-east-1", "us"), ("us-west-2", "us"), ("us-gov-west-1", "us-gov"),
    ("eu-west-1", "eu"), ("eu-central-1", "eu"),
    ("ap-northeast-1", "apac"), ("ap-southeast-1", "apac"),
    ("ca-central-1", "global"), ("sa-east-1", "global"), ("me-central-1", "global"),
    ("", None), (None, None), ("not a region", None),
])
def test_geo_for_region(region, geo):
    assert reg.geo_for_region(region) == geo


@pytest.mark.parametrize("model_id,region,want", [
    ("us.anthropic.claude-opus-5-5", "eu-west-1", "eu.anthropic.claude-opus-5-5"),
    ("us.anthropic.claude-opus-4-6-v1", "ap-northeast-1", "apac.anthropic.claude-opus-4-6-v1"),
    ("us.anthropic.claude-opus-5-5[1m]", "eu-central-1", "eu.anthropic.claude-opus-5-5[1m]"),
    ("eu.anthropic.claude-opus-5-5", "us-east-1", "us.anthropic.claude-opus-5-5"),
    ("us.anthropic.claude-opus-5-5", "ca-central-1", "global.anthropic.claude-opus-5-5"),
    ("us.anthropic.claude-opus-5-5", "us-gov-west-1", "us-gov.anthropic.claude-opus-5-5"),
    ("us.anthropic.claude-opus-5-5", "us-west-2", "us.anthropic.claude-opus-5-5"),
    # unchanged: global, foundation id, ARN, short name, no/bad region
    ("global.anthropic.claude-opus-5-5", "eu-west-1", "global.anthropic.claude-opus-5-5"),
    ("anthropic.claude-opus-5-5", "eu-west-1", "anthropic.claude-opus-5-5"),
    ("arn:aws:bedrock:eu-west-1:1:inference-profile/x", "eu-west-1",
     "arn:aws:bedrock:eu-west-1:1:inference-profile/x"),
    ("claude-opus-5-5", "eu-west-1", "claude-opus-5-5"),
    ("us.anthropic.claude-opus-5-5", None, "us.anthropic.claude-opus-5-5"),
    ("us.anthropic.claude-opus-5-5", "bogus", "us.anthropic.claude-opus-5-5"),
])
def test_regional_id(model_id, region, want):
    assert reg.regional_id(model_id, region) == want


def test_is_full_bedrock_id():
    for full in ("us.anthropic.x", "eu.anthropic.x", "apac.anthropic.x", "global.anthropic.x",
                 "us-gov.anthropic.x", "anthropic.x", "arn:aws:bedrock:x"):
        assert reg.is_full_bedrock_id(full), full
    for short in ("claude-opus-5-5", "sonnet", "", None):
        assert not reg.is_full_bedrock_id(short), short
    assert reg.normalize_short_name("us-gov.anthropic.claude-opus-5") == "claude-opus-5"


# ── exit 1: config.get_bedrock_model_id ────────────────────────────────────


def test_get_bedrock_model_id_follows_region():
    from config import get_bedrock_model_id
    assert get_bedrock_model_id("claude-opus-5-5", region="eu-west-1") == "eu.anthropic.claude-opus-5-5"
    assert get_bedrock_model_id("claude-opus-5-5", region="ap-southeast-1") == "apac.anthropic.claude-opus-5-5"
    assert get_bedrock_model_id("claude-opus-5-5", region="us-east-1") == "us.anthropic.claude-opus-5-5"
    # a stored bedrock_model_map value (written with us.) follows too
    stored = {"claude-opus-5-5": "us.anthropic.claude-opus-5-5"}
    assert get_bedrock_model_id("claude-opus-5-5", stored, region="eu-west-1") == "eu.anthropic.claude-opus-5-5"


def test_get_bedrock_model_id_reads_main_region(monkeypatch):
    import config
    monkeypatch.setattr(config, "_main_region", lambda: "eu-central-1")
    assert config.get_bedrock_model_id("claude-sonnet-4-6") == "eu.anthropic.claude-sonnet-4-6"


# ── exit 2: jobs.bedrock.converse_with_retry ───────────────────────────────


class _FakeClient:
    def __init__(self):
        self.calls = []

    def converse(self, **kw):
        self.calls.append(kw)
        return {"output": {}, "usage": {}}


def test_converse_sends_regional_model_id(monkeypatch):
    from jobs import bedrock
    fake = _FakeClient()
    monkeypatch.setattr(bedrock, "get_client", lambda force_new=False, region=None: fake)
    monkeypatch.setattr(bedrock, "_ledger", lambda resp, model_id, feature: resp)
    bedrock.converse_with_retry(messages=[], system=[], inference_config={},
                                model_id="us.anthropic.claude-sonnet-4-6", region="ap-northeast-1")
    assert fake.calls[0]["modelId"] == "apac.anthropic.claude-sonnet-4-6"


def test_converse_uses_configured_region_when_none_given(monkeypatch):
    from jobs import bedrock
    fake = _FakeClient()
    monkeypatch.setattr(bedrock, "get_client", lambda force_new=False, region=None: fake)
    monkeypatch.setattr(bedrock, "_ledger", lambda resp, model_id, feature: resp)
    monkeypatch.setattr(bedrock, "_load_config", lambda: ("eu-west-1", {}))
    bedrock.converse_with_retry(messages=[], system=[], inference_config={},
                                model_id="us.anthropic.claude-sonnet-4-6")
    assert fake.calls[0]["modelId"] == "eu.anthropic.claude-sonnet-4-6"


def test_get_model_id_never_synthesizes(monkeypatch):
    from jobs import bedrock
    monkeypatch.setattr(bedrock, "_load_config", lambda: ("us-east-1", {}))
    assert bedrock.get_model_id("claude-opus-4-6") == "us.anthropic.claude-opus-4-6-v1"
    assert bedrock.get_model_id("not-a-model") == "not-a-model"


# ── the CLI's region and the id's geography share one reader ───────────────


def test_cli_region_is_the_main_region(monkeypatch):
    from core import claude_environment
    from core.aws_account import RECORD_KEY
    for k in ("AWS_REGION", "AWS_DEFAULT_REGION"):
        monkeypatch.delenv(k, raising=False)
    # aws_region unset: the record's region is the main region (aws_account.region)
    claude_environment._configure_claude_environment({RECORD_KEY: {"region": "eu-central-1"}})
    assert os.environ["AWS_REGION"] == "eu-central-1"
    assert os.environ["AWS_DEFAULT_REGION"] == "eu-central-1"


# ── checks that accepted only us. / global. ────────────────────────────────


def test_job_model_check_accepts_any_geography():
    from jobs.model_resolve import _is_usable_model
    assert _is_usable_model("eu.anthropic.some-custom-name")
    assert _is_usable_model("apac.anthropic.some-custom-name")


def test_fallback_price_for_any_geography():
    from core import usage_prices
    row = {"input": 1_000_000}
    assert usage_prices.price({}, "eu.anthropic.claude-sonnet-4-6", row) == pytest.approx(3.0)
    assert usage_prices.price({}, "us.anthropic.claude-sonnet-4-6", row) == pytest.approx(3.0)
    assert usage_prices.price({}, "eu.anthropic.unknown-model", row) is None


def test_discovery_prefers_the_main_region_geography(monkeypatch):
    from routers import settings
    from core import aws_account

    class _Pages:
        def paginate(self):
            return [{"inferenceProfileSummaries": [
                {"inferenceProfileId": p, "type": "SYSTEM_DEFINED"}
                for p in ("us.anthropic.claude-opus-5-5", "global.anthropic.claude-opus-5-5",
                          "eu.anthropic.claude-opus-5-5", "global.anthropic.claude-sonnet-5")]}]

    class _Client:
        def get_paginator(self, name):
            return _Pages()

    monkeypatch.setattr(settings, "_bedrock_client", lambda region=None: _Client())
    monkeypatch.setattr(aws_account, "region", lambda config=None: "eu-west-1")
    got = {m["short_name"]: m["bedrock_id"] for m in settings._list_bedrock_inference_profiles([])}
    assert got["claude-opus-5-5"] == "eu.anthropic.claude-opus-5-5"
    assert got["claude-sonnet-5"] == "global.anthropic.claude-sonnet-5"


def test_chat_model_follows_the_session_config_region():
    """prompt_builder resolves the chat model with the region its config gives the CLI."""
    from core.prompt_builder import PromptBuilder
    pb = PromptBuilder.__new__(PromptBuilder)
    pb._config = {"default_model": "claude-opus-5-5", "aws_region": "eu-west-1"}
    assert pb.resolve_model({}) == "eu.anthropic.claude-opus-5-5[1m]"
    pb._config = {"default_model": "claude-opus-5-5", "aws_region": "us-west-2"}
    assert pb.resolve_model({}) == "us.anthropic.claude-opus-5-5[1m]"
