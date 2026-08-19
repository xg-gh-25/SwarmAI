"""Tests for create_channel dedup-by-channel_type (upsert).

Covers the fix for the "duplicate channel" defect: create_channel used to
INSERT a fresh UUID row on every call, so repeatedly saving a Slack channel
(e.g. after a failed first attempt) produced multiple rows of the same
channel_type that then fought over the single Socket Mode connection.

The fix: create_channel now upserts by channel_type — if a channel of the
same type already exists, it UPDATEs that row (reusing its id, preserving
created_at, resetting status/error_message) instead of inserting a new one.

Methodology: in-memory SQLite (real DB, no mock of our own code), only
build_agent_config is stubbed (validates the agent exists — a boundary we
don't exercise here). Each test drives the real create_channel handler.
"""
import tempfile
from pathlib import Path

import pytest

from database.sqlite import SQLiteDatabase
import routers.channels as channels_router
from schemas.channel import ChannelCreateRequest


@pytest.fixture
async def db_and_router(monkeypatch, tmp_path):
    """Wire a real (file-based) SQLite DB into the `db` reference the router
    uses, and stub build_agent_config so the agent-existence check passes.

    A file DB (not ``:memory:``) is required: each SQLiteTable opens its own
    connection, and ``:memory:`` databases are per-connection — the schema
    created by initialize() would be invisible to db.channels' connection.
    """
    db_path = tmp_path / "test_channels.db"
    test_db = SQLiteDatabase(str(db_path))
    await test_db.initialize()

    # The router imported `db` by name (from database import db) — patch the
    # attribute the router module actually references.
    monkeypatch.setattr(channels_router, "db", test_db)

    async def _fake_agent(agent_id):
        return {"id": agent_id, "name": f"Agent-{agent_id}"}

    monkeypatch.setattr(channels_router, "build_agent_config", _fake_agent)

    yield test_db


def _req(config, name="Slack", agent_id="default"):
    # ChannelCreateRequest.channel_type is Literal["slack"] — slack is the only
    # type creatable via the API today. The dedup logic still groups BY
    # channel_type (correct when more types are added); the "must not collapse
    # different types" invariant is verified in
    # test_upsert_only_matches_same_type by pre-seeding a non-slack row directly.
    return ChannelCreateRequest(
        name=name,
        channel_type="slack",
        agent_id=agent_id,
        config=config,
    )


@pytest.mark.asyncio
async def test_duplicate_same_type_upserts_single_row(db_and_router):
    """AC1/AC5: two creates of the same channel_type → exactly ONE row,
    holding the SECOND create's config. (mutation-proven: removing the
    dedup branch makes this RED — len becomes 2.)"""
    db = db_and_router

    await channels_router.create_channel(_req({"bot_token": "xoxb-first"}))
    await channels_router.create_channel(_req({"bot_token": "xoxb-second"}))

    rows = await db.channels.list()
    assert len(rows) == 1, f"expected 1 slack channel, got {len(rows)}"

    cfg = rows[0]["config"]
    import json as _json
    if isinstance(cfg, str):
        cfg = _json.loads(cfg)
    assert cfg["bot_token"] == "xoxb-second", "upsert must overwrite config with the latest create"


@pytest.mark.asyncio
async def test_upsert_preserves_created_at(db_and_router):
    """AC4 (Gate-1 must-fix): the upserted row keeps the ORIGINAL created_at,
    not the second create's timestamp. Guards against put() stamping NOW."""
    db = db_and_router

    first = await channels_router.create_channel(_req({"bot_token": "xoxb-first"}))
    original_created_at = first.created_at
    original_id = first.id

    second = await channels_router.create_channel(_req({"bot_token": "xoxb-second"}))

    assert second.id == original_id, "upsert must reuse the existing row id"
    assert second.created_at == original_created_at, (
        "upsert must preserve the original created_at, not overwrite with NOW"
    )


@pytest.mark.asyncio
async def test_upsert_resets_status_and_error(db_and_router):
    """AC3: upserting over a row that was left in status=error resets it to
    inactive + clears error_message, so the gateway can re-start it."""
    db = db_and_router

    first = await channels_router.create_channel(_req({"bot_token": "xoxb-first"}))
    # Simulate a prior failed start leaving the row in error state.
    await db.channels.put({
        "id": first.id,
        "name": "Slack",
        "channel_type": "slack",
        "agent_id": "default",
        "config": {"bot_token": "xoxb-first"},
        "status": "error",
        "error_message": "No adapter registered for channel type 'slack'",
        "created_at": first.created_at,
    })

    second = await channels_router.create_channel(_req({"bot_token": "xoxb-second"}))

    assert second.status == "inactive", "upsert must reset status to inactive"
    assert second.error_message is None, "upsert must clear the stale error_message"


@pytest.mark.asyncio
async def test_upsert_only_matches_same_type(db_and_router):
    """AC2: dedup is per-type — creating a slack channel must NOT touch a row
    of a different channel_type. (channel_type is Literal['slack'] at the API,
    so the other-type row is seeded directly to prove the per-type predicate.)"""
    db = db_and_router

    # Seed a pre-existing non-slack channel directly (bypasses the API Literal).
    await db.channels.put({
        "id": "preexisting-discord",
        "name": "Discord",
        "channel_type": "discord",
        "agent_id": "default",
        "config": {"token": "d-a"},
        "status": "inactive",
    })

    await channels_router.create_channel(_req({"bot_token": "xoxb-a"}))

    rows = await db.channels.list()
    types = sorted(r["channel_type"] for r in rows)
    assert types == ["discord", "slack"], (
        f"slack create must add a new row, not touch the discord row, got {types}"
    )
    # The discord row is untouched.
    discord = next(r for r in rows if r["channel_type"] == "discord")
    assert discord["id"] == "preexisting-discord"
