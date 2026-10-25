"""Channel recall: only the owner's private chat recalls (design
docs/design/2026-10-25-knowledge-delivery-gaps.md §3.3, part A / G4).

What is tested (§4 G4):
- an owner message in a private chat (chat_type "im") gets the first recall;
- an owner message in a group / channel / multi-person DM gets none, and the first
  recall stays open (each message is judged on its own);
- a TRUSTED / PUBLIC sender's private chat gets none;
- the follow-up (new topic) recall follows the same rule;
- a desktop tab (no channel_context) recalls as before;
- the gateway puts chat_type into channel_context (the only source of it).
Mutation: let every owner message recall -> the group test goes red.
"""
from unittest.mock import MagicMock, patch

import pytest

from core import session_router as sr


@pytest.fixture(autouse=True)
def _no_unified():
    with patch("core.session_router._unified_recall_body", return_value=(None, None)):
        yield


@pytest.fixture
def unit():
    u = MagicMock()
    u._recall_injected = False
    u.is_channel_session = False
    u._recall_keyword_misses = 0
    u._recall_followups = 0
    return u


@pytest.fixture
def options():
    o = MagicMock()
    o.system_prompt = "## Base"
    return o


def _ctx(chat_type, owner=True):
    return {"channel_type": "slack", "is_owner": owner, "chat_type": chat_type,
            "is_group": chat_type in ("group", "channel", "mpim")}


async def _recall(unit, options, ctx, msg="How does the evolution pipeline work?"):
    with patch("core.session_router._recall_for_query", return_value="REMEMBERED") as rq:
        await sr._maybe_inject_recall(user_message=msg, options=options, unit=unit,
                                      channel_context=ctx)
    return rq


@pytest.mark.asyncio
async def test_owner_private_chat_recalls(unit, options):
    await _recall(unit, options, _ctx("im"))
    assert "REMEMBERED" in options.system_prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("chat_type", ["mpim", "group", "channel", ""])
async def test_owner_in_a_shared_chat_does_not_recall(unit, options, chat_type):
    await _recall(unit, options, _ctx(chat_type))
    assert options.system_prompt == "## Base"
    assert unit._recall_injected is False


@pytest.mark.asyncio
async def test_someone_else_private_chat_does_not_recall(unit, options):
    await _recall(unit, options, _ctx("im", owner=False))
    assert options.system_prompt == "## Base"


@pytest.mark.asyncio
async def test_followup_recall_follows_the_same_rule(unit, options):
    unit._recall_injected = True
    unit._recall_terms = frozenset({"alpha", "beta"})
    with patch("core.session_router._maybe_followup_recall") as fu:
        await sr._maybe_inject_recall(user_message="totally new topic gamma delta",
                                      options=options, unit=unit,
                                      channel_context=_ctx("mpim"))
    fu.assert_not_called()


@pytest.mark.asyncio
async def test_desktop_tab_recalls_as_before(unit, options):
    await _recall(unit, options, None)
    assert "REMEMBERED" in options.system_prompt


def test_rule():
    assert sr._channel_recall_allowed(None) is True
    assert sr._channel_recall_allowed(_ctx("im")) is True
    assert sr._channel_recall_allowed(_ctx("mpim")) is False
    assert sr._channel_recall_allowed(_ctx("im", owner=False)) is False
    assert sr._channel_recall_allowed({"is_owner": True}) is False
