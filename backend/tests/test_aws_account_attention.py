"""Need-You for the SwarmAI account (core/attention_authority._collect_aws_account; design
2026-10-31-swarmai-aws-account §7.1).

Questions answered:
1. a sign-in that expired or reaches another account is one BLOCKING item that opens
   Settings → SwarmAI Account; a connected account adds nothing.
2. fewer Hives than asked for is one REVIEW item until the person says "later"
   (``dismissed``); ready adds nothing; a Hive (cloud instance) adds nothing.
3. the item comes from the last status the page or the reconcile loop computed (no
   database read in the attention thread), and that status names the price per type.
"""
from __future__ import annotations

import pytest

from core import attention_authority as aa_
from core import aws_account
from hive import accounts as ha
from hive import fleet


@pytest.fixture
def state(monkeypatch):
    rec = {"state": aws_account.CONNECTED, "account_id": "111111111111", "source": "ada"}
    monkeypatch.setattr(aws_account, "get_record", lambda: rec)
    monkeypatch.setattr(aws_account, "is_hive_mode", lambda: False)
    monkeypatch.setattr(fleet, "_last_status", None)
    return rec


def _items():
    return aa_._collect_aws_account()


@pytest.mark.parametrize("st", ["needs_login", "account_mismatch"])
def test_a_broken_sign_in_is_blocking_and_opens_the_page(state, st):
    state["state"] = {"needs_login": aws_account.NEEDS_LOGIN, "account_mismatch": aws_account.MISMATCH}[st]
    (item,) = _items()
    assert item.tier == aa_.TIER_BLOCKING and item.source == "aws_account"
    assert item.dispatch["context"] == {"kind": "aws_account", "settings_tab": "aws-account"}
    assert item.summary == "111111111111"


def test_connected_and_ready_add_nothing(state, monkeypatch):
    monkeypatch.setattr(fleet, "_last_status", {"state": "ready", "dismissed": False})
    assert _items() == []


def test_hives_short_is_one_review_item_until_later(state, monkeypatch):
    monkeypatch.setattr(fleet, "_last_status", {"state": "short", "short": 2, "dismissed": False})
    (item,) = _items()
    assert item.tier == aa_.TIER_REVIEW and "2 Hives short" in item.title
    monkeypatch.setattr(fleet, "_last_status", {"state": "short", "short": 2, "dismissed": True})
    assert _items() == []
    monkeypatch.setattr(fleet, "_last_status", {"state": "cannot_build", "short": 1, "dismissed": False,
                                                "capability": {"detail": "missing ec2:RunInstances"}})
    (item,) = _items()
    assert "ec2:RunInstances" in item.detail


def test_a_hive_adds_nothing(state, monkeypatch):
    state["state"] = aws_account.NEEDS_LOGIN
    monkeypatch.setattr(aws_account, "is_hive_mode", lambda: True)
    assert _items() == []


def test_it_is_in_the_channel(state, monkeypatch, tmp_path):
    state["state"] = aws_account.NEEDS_LOGIN
    ids = [i.id for i in aa_.collect(tmp_path).items]
    assert "aws_account:needs_login" in ids


@pytest.mark.asyncio
async def test_status_is_cached_and_names_prices(tmp_path, monkeypatch):
    from database import db
    monkeypatch.setattr(ha, "swarmai_account", lambda: {"account_id": "111111111111", "region": "us-east-1"})
    monkeypatch.setattr(fleet, "_last_status", None)
    fleet._save_config({})
    try:
        st = await fleet.status(db.db_path)
    finally:
        fleet._save_config({})
    assert fleet.last_status() == st
    assert st["prices"]["m7g.xlarge"] == fleet.MONTHLY_USD["m7g.xlarge"]
