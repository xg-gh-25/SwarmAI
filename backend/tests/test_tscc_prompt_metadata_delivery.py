"""Regression tests for TSCC system-prompt metadata publication.

The TSCC Prompt tab must show the system prompt the model ACTUALLY received.
Publishing at build time broke that: ``send()`` rebuilds ``options`` on every
turn, but a warm subprocess reuse discards them, so from turn 2 onward the
registry held a prompt that was never sent (review run_abab234c, HIGH #1).

Publication now happens at DELIVERY — ``SessionUnit._spawn()``, the only
consumer of ``options.system_prompt``. These tests enter that path rather than
asserting on the pre-publication dict, which is how the previous version of
this feature stayed green while being inert.

Covers:
- ``_spawn`` publishes the delivered prompt, including a recall block appended
  after ``build_options``
- a spawn with nothing pending never clobbers an earlier turn's entry
- the pending stash is one-shot, so a respawn cannot re-publish stale metadata
- the router seeds write-if-absent (reintroduction guard for the overwrite)
- ``degraded`` survives the response model (review HIGH #2)

Testing methodology: real ``SessionUnit`` instances with the SDK client wrapper
and the spawn memory gate patched out, plus an endpoint-level check through
``TestClient`` so the serialization boundary is actually exercised.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from core.session_unit import SessionUnit


# ─────────────────────────────────────────────────────────────────────────
# Harness
# ─────────────────────────────────────────────────────────────────────────

def _opts(system_prompt: str):
    """A real ClaudeAgentOptions — _spawn reads several attrs off it."""
    from claude_agent_sdk import ClaudeAgentOptions
    return ClaudeAgentOptions(system_prompt=system_prompt)


class _FakeClient:
    """Stand-in for ClaudeSDKClient — _spawn only stores it."""


class _FakeWrapper:
    """Replaces _ClaudeClientWrapper so no subprocess is created."""

    pid = 4242

    def __init__(self, options=None):
        self.options = options

    async def __aenter__(self):
        return _FakeClient()


@pytest.fixture
def spawnable(monkeypatch):
    """Make ``_spawn`` runnable in-process: no subprocess, no memory gate.

    The gate is patched so the test cannot flake on the host's free RAM — this
    suite is about metadata publication, not admission control.
    """
    from core import claude_environment
    from core.resource_monitor import resource_monitor

    monkeypatch.setattr(claude_environment, "_ClaudeClientWrapper", _FakeWrapper)
    monkeypatch.setattr(
        resource_monitor,
        "spawn_budget",
        lambda *a, **k: SimpleNamespace(
            can_spawn=True, reason="", available_mb=8192.0,
            estimated_cost_mb=300.0, headroom_mb=4096.0,
        ),
    )
    yield


@pytest.fixture
def registry_clean():
    """Drop this session's registry entry before and after each test."""
    from core import session_registry
    sid = "tscc-delivery-test"
    session_registry.system_prompt_metadata.pop(sid, None)
    yield sid
    session_registry.system_prompt_metadata.pop(sid, None)


# ─────────────────────────────────────────────────────────────────────────
# Delivery-time publication
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_spawn_publishes_the_delivered_prompt(spawnable, registry_clean):
    """full_text comes off the options handed to the CLI — so the recall block
    the router appends AFTER build_options is included, which is the whole
    point of publishing at delivery instead of at build."""
    from core import session_registry

    sid = registry_clean
    unit = SessionUnit(session_id=sid, agent_id="default")

    built = "# Core prompt\nIDENTITY"
    unit._pending_prompt_metadata = {
        "files": [{"filename": "SWARMAI.md", "tokens": 10, "truncated": False}],
        "total_tokens": 10,
        "full_text": built,  # pre-recall value captured at build time
    }
    # _maybe_inject_recall mutates options.system_prompt after build_options.
    delivered = built + "\n\n## Recalled Knowledge\n[RECALLED] MEMORY.md"

    await unit._spawn(_opts(delivered), None)

    published = session_registry.system_prompt_metadata.get(sid)
    assert published is not None, "spawn must publish the metadata it was handed"
    assert published["full_text"] == delivered, (
        "full_text must be the prompt actually delivered to the CLI, not the "
        "pre-recall text captured during build_options"
    )
    assert "## Recalled Knowledge" in published["full_text"]
    assert published["total_tokens"] == 10, "other metadata fields pass through"


@pytest.mark.asyncio
async def test_spawn_with_nothing_pending_preserves_earlier_turn(
    spawnable, registry_clean,
):
    """A warm reuse leaves nothing pending. If such a turn were to reach spawn,
    it must not replace the entry published by the turn that really was sent —
    the exact clobber that made the panel show a never-sent prompt."""
    from core import session_registry

    sid = registry_clean
    turn1 = {"files": [], "total_tokens": 7, "full_text": "TURN 1 REAL PROMPT"}
    session_registry.system_prompt_metadata[sid] = turn1

    unit = SessionUnit(session_id=sid, agent_id="default")
    assert unit._pending_prompt_metadata is None, "fresh unit has nothing pending"

    await unit._spawn(_opts("TURN 2 REBUILT PROMPT (never sent)"), None)

    assert session_registry.system_prompt_metadata[sid]["full_text"] == (
        "TURN 1 REAL PROMPT"
    ), "a spawn with no pending metadata must not overwrite a published prompt"


@pytest.mark.asyncio
async def test_pending_stash_is_one_shot(spawnable, registry_clean):
    """After publishing, the stash is cleared so a later respawn publishes ITS
    own turn's metadata instead of resurrecting this one."""
    from core import session_registry

    sid = registry_clean
    unit = SessionUnit(session_id=sid, agent_id="default")
    unit._pending_prompt_metadata = {
        "files": [], "total_tokens": 1, "full_text": "built",
    }

    await unit._spawn(_opts("delivered once"), None)
    assert unit._pending_prompt_metadata is None, "publish must consume the stash"

    # A respawn (self-heal / RSS / --resume) with no new stash must not
    # re-publish the consumed one over whatever is current.
    session_registry.system_prompt_metadata[sid] = {
        "files": [], "total_tokens": 2, "full_text": "current",
    }
    await unit._spawn(_opts("respawn prompt"), None)
    assert session_registry.system_prompt_metadata[sid]["full_text"] == "current"


@pytest.mark.asyncio
async def test_publish_failure_never_breaks_spawn(spawnable, registry_clean):
    """The publish is observability. A malformed stash must not stop a spawn —
    the session still comes up, just without panel metadata."""
    sid = registry_clean
    unit = SessionUnit(session_id=sid, agent_id="default")
    unit._pending_prompt_metadata = "not-a-dict"  # type: ignore[assignment]

    await unit._spawn(_opts("prompt"), None)  # must not raise

    assert unit._client is not None, "spawn completed despite the bad stash"


# ─────────────────────────────────────────────────────────────────────────
# Reintroduction guard for the build-time overwrite
# ─────────────────────────────────────────────────────────────────────────

def test_router_seeds_metadata_without_overwriting():
    """The router may SEED the registry (so a cold start's spawn window is not
    blank) but must never assign over an existing entry — an unconditional
    assignment here is what defeated the previous fix's gate."""
    from core import session_router

    src = inspect.getsource(session_router)
    assert "system_prompt_metadata.setdefault(session_id, _spm)" in src, (
        "build-time seed must be write-if-absent"
    )
    assert "system_prompt_metadata[session_id] = _spm" not in src, (
        "unconditional build-time assignment reintroduced — this overwrites the "
        "prompt that was actually sent with a rebuilt, discarded one"
    )
    assert "unit._pending_prompt_metadata = _spm" in src, (
        "router must hand the metadata to the unit for delivery-time publish"
    )


def test_delivery_publish_lives_in_spawn():
    """Publication must stay in _spawn, the single point where the prompt
    reaches the CLI. Moving it back out reopens the never-sent-prompt class.

    run_380413c5: full_text is now COMPOSED (base + this-turn delivered
    query-channel prefix) rather than raw system_prompt — but the invariant this
    test guards is unchanged: the publish still lives in _spawn and the BASE is
    still read off the delivered ``options.system_prompt`` (the query-channel
    prefix rides the query, captured separately). Assert the invariant, not the
    exact old literal."""
    src = inspect.getsource(SessionUnit._spawn)
    assert "_pending_prompt_metadata" in src
    # base_full_text is derived from the delivered options.system_prompt, and
    # full_text is composed from it — both still inside _spawn.
    assert "options.system_prompt" in src, (
        "full_text base must be read off the delivered options"
    )
    assert '_pending["full_text"]' in src and "_compose_full_text" in src, (
        "full_text is composed (base + delivered query-channel prefix) at the "
        "in-spawn publish point"
    )


# ─────────────────────────────────────────────────────────────────────────
# degraded must survive the response model (review HIGH #2)
# ─────────────────────────────────────────────────────────────────────────

class TestDegradedReachesConsumer:
    """prompt_builder mirrors the fail-loud degrade reason into the metadata
    dict. Without a schema field, Pydantic's extra='ignore' dropped it at the
    response boundary, so no consumer could ever see it."""

    def test_schema_keeps_degraded(self):
        from schemas.tscc import SystemPromptMetadata

        dumped = SystemPromptMetadata(
            files=[], total_tokens=5, full_text="x",
            degraded="missing_core_sections: SOUL",
        ).model_dump()
        assert dumped["degraded"] == "missing_core_sections: SOUL"

    def test_schema_accepts_metadata_dict_with_degraded(self):
        """The endpoint builds the model via ``SystemPromptMetadata(**metadata)``
        straight from the registry dict — the shape prompt_builder produces."""
        from schemas.tscc import SystemPromptMetadata

        metadata = {
            "files": [], "total_tokens": 3, "full_text": "y",
            "degraded": "core_context_failed: OSError",
        }
        assert SystemPromptMetadata(**metadata).degraded == (
            "core_context_failed: OSError"
        )

    def test_endpoint_returns_degraded(self):
        """End-to-end through the router + response model, which is where the
        signal was being stripped."""
        from fastapi.testclient import TestClient
        from core import session_registry
        from main import app

        sid = "tscc-degraded-endpoint"
        session_registry.system_prompt_metadata[sid] = {
            "files": [], "total_tokens": 11, "full_text": "z",
            "degraded": "missing_core_sections: SOUL,AGENT",
        }
        try:
            with TestClient(app) as client:
                resp = client.get(f"/api/chat/{sid}/system-prompt")
            assert resp.status_code == 200
            assert resp.json()["degraded"] == "missing_core_sections: SOUL,AGENT"
        finally:
            session_registry.system_prompt_metadata.pop(sid, None)

    def test_healthy_prompt_reports_empty_degraded(self):
        """A complete assembly omits the key; the endpoint must still answer
        with a defined field so the UI banner has an unambiguous "no" value."""
        from fastapi.testclient import TestClient
        from core import session_registry
        from main import app

        sid = "tscc-degraded-healthy"
        session_registry.system_prompt_metadata[sid] = {
            "files": [], "total_tokens": 4, "full_text": "ok",
        }
        try:
            with TestClient(app) as client:
                resp = client.get(f"/api/chat/{sid}/system-prompt")
            assert resp.status_code == 200
            assert resp.json()["degraded"] == ""
        finally:
            session_registry.system_prompt_metadata.pop(sid, None)


# ─────────────────────────────────────────────────────────────────────────
# effective_token_budget must survive the response model (review MED #5)
# ─────────────────────────────────────────────────────────────────────────

class TestBudgetReachesConsumer:
    """The budget is per-model — 100K only at a >=500K context window, 50K at
    >=200K, 30K at >=64K. The panel hardcoded the 100K tier because the schema
    dropped the real value, so a 45K prompt on a 200K model read "45% · in
    budget" while sitting at 90% of its actual ceiling."""

    def test_schema_keeps_budget(self):
        from schemas.tscc import SystemPromptMetadata

        dumped = SystemPromptMetadata(
            files=[], total_tokens=45_000, full_text="x",
            effective_token_budget=50_000,
        ).model_dump()
        assert dumped["effective_token_budget"] == 50_000

    def test_endpoint_returns_budget(self):
        from fastapi.testclient import TestClient
        from core import session_registry
        from main import app

        sid = "tscc-budget-endpoint"
        session_registry.system_prompt_metadata[sid] = {
            "files": [], "total_tokens": 45_000, "full_text": "z",
            "effective_token_budget": 50_000,
        }
        try:
            with TestClient(app) as client:
                resp = client.get(f"/api/chat/{sid}/system-prompt")
            assert resp.status_code == 200
            assert resp.json()["effective_token_budget"] == 50_000
        finally:
            session_registry.system_prompt_metadata.pop(sid, None)

    def test_unreported_budget_is_zero_not_a_guessed_tier(self):
        """A build that reported no budget must serialize 0. The UI reads 0 as
        "unknown" and omits the percentage — substituting a tier here would put
        the 2-3x misreport straight back."""
        from fastapi.testclient import TestClient
        from core import session_registry
        from main import app

        sid = "tscc-budget-missing"
        session_registry.system_prompt_metadata[sid] = {
            "files": [], "total_tokens": 45_000, "full_text": "z",
        }
        try:
            with TestClient(app) as client:
                resp = client.get(f"/api/chat/{sid}/system-prompt")
            assert resp.status_code == 200
            assert resp.json()["effective_token_budget"] == 0
        finally:
            session_registry.system_prompt_metadata.pop(sid, None)

    def test_prompt_builder_reports_the_computed_budget(self, tmp_path):
        """Guards the producer side: the metadata key the schema now exposes is
        the one compute_token_budget actually returns."""
        import inspect
        from core import prompt_builder
        from core.context_directory_loader import ContextDirectoryLoader

        src = inspect.getsource(prompt_builder)
        assert 'prompt_metadata["effective_token_budget"]' in src
        assert "compute_token_budget(model_context_window)" in src
        # And the tiers the panel now renders are the ones the loader computes —
        # the panel must never re-derive these, only display what it is told.
        loader = ContextDirectoryLoader(context_dir=tmp_path)
        assert loader.compute_token_budget(1_000_000) == 100_000
        assert loader.compute_token_budget(200_000) == 50_000
        assert loader.compute_token_budget(128_000) == 30_000


# ─────────────────────────────────────────────────────────────────────────
# A recall miss must read as "ran, matched nothing" (review MED #6/#7)
# ─────────────────────────────────────────────────────────────────────────

class TestRecallMissIsReported:
    def test_endpoint_reports_ran_with_zero_hits(self):
        """The panel needs to tell "the wording missed" apart from "recall never
        ran" — they call for opposite actions from the user."""
        from fastapi.testclient import TestClient
        from core import session_registry
        from main import app

        sid = "tscc-recall-miss"
        session_registry.recall_snapshot[sid] = {
            "ran": True, "hits": [], "body": "",
            "tokens": 0, "latency_ms": 41.5, "keywords": ["evolution", "pipeline"],
        }
        try:
            with TestClient(app) as client:
                resp = client.get(f"/api/chat/{sid}/recall")
            assert resp.status_code == 200
            body = resp.json()
            assert body["ran"] is True
            assert body["hits"] == []
            assert body["keywords"] == ["evolution", "pipeline"]
        finally:
            session_registry.recall_snapshot.pop(sid, None)

    def test_no_snapshot_still_reports_never_ran(self):
        """The ran=False default must stay meaningful — it is now the ONLY way to
        say "recall did not run", so it must not be reachable by a miss."""
        from fastapi.testclient import TestClient
        from main import app

        with TestClient(app) as client:
            resp = client.get("/api/chat/tscc-recall-absent/recall")
        assert resp.status_code == 200
        assert resp.json()["ran"] is False


# ─────────────────────────────────────────────────────────────────────────
# Restart must not leave a stale recall snapshot behind (review MED #9)
# ─────────────────────────────────────────────────────────────────────────

class TestRestartClearsRecallSnapshot:
    """_cleanup_internal cleared the snapshot on the UNIT but left the registry
    copy — and the registry copy is the one the panel reads. So after a restart
    the panel paired a pre-restart recall snapshot with a post-restart prompt
    that carries no recall block."""

    def test_cleanup_clears_both_unit_and_registry(self):
        from core import session_registry

        sid = "tscc-restart-clear"
        unit = SessionUnit(session_id=sid, agent_id="default")
        unit._recall_snapshot = {"ran": True, "hits": [], "body": "",
                                 "tokens": 5, "latency_ms": 1.0, "keywords": ["x"]}
        session_registry.recall_snapshot[sid] = unit._recall_snapshot
        try:
            unit._cleanup_internal()

            assert unit._recall_snapshot is None
            assert sid not in session_registry.recall_snapshot, (
                "the registry copy is what the panel reads — clearing only the "
                "unit attribute leaves the stale snapshot visible"
            )
        finally:
            session_registry.recall_snapshot.pop(sid, None)

    @pytest.mark.asyncio
    async def test_same_send_recycle_restores_this_turn_snapshot(
        self, spawnable, registry_clean,
    ):
        """The one case a blunt pop would break: a recycle-before-reuse inside the
        SAME send tears the subprocess down after the router copied the snapshot,
        then respawns with a prompt that DOES carry the recall block. The armed
        pending value must restore it, or the panel would claim no recall ran on a
        turn where it did."""
        from core import session_registry

        sid = registry_clean
        unit = SessionUnit(session_id=sid, agent_id="default")
        snap = {"ran": True, "hits": [], "body": "", "tokens": 0,
                "latency_ms": 12.0, "keywords": ["evolution"]}
        # What the router does on a turn where recall ran.
        unit._recall_snapshot = snap
        session_registry.recall_snapshot[sid] = snap
        unit._pending_recall_snapshot = snap
        try:
            unit._cleanup_internal()          # the recycle
            assert sid not in session_registry.recall_snapshot

            await unit._spawn(_opts("prompt with ## Recalled Knowledge"), None)

            assert session_registry.recall_snapshot.get(sid) == snap, (
                "respawn must restore the snapshot matching the delivered prompt"
            )
            assert unit._pending_recall_snapshot is None, "one-shot"
        finally:
            session_registry.recall_snapshot.pop(sid, None)

    @pytest.mark.asyncio
    async def test_respawn_on_a_later_turn_does_not_resurrect(
        self, spawnable, registry_clean,
    ):
        """A restart on a turn where recall did NOT run leaves nothing armed, so the
        respawn must publish nothing — the delivered prompt has no recall block."""
        from core import session_registry

        sid = registry_clean
        unit = SessionUnit(session_id=sid, agent_id="default")
        unit._recall_snapshot = {"ran": True, "hits": [], "body": "",
                                 "tokens": 0, "latency_ms": 1.0, "keywords": ["old"]}
        session_registry.recall_snapshot[sid] = unit._recall_snapshot
        # Router arms nothing because _ttft_recall_ms was None this turn.
        unit._pending_recall_snapshot = None
        try:
            unit._cleanup_internal()
            await unit._spawn(_opts("rebuilt prompt, no recall block"), None)

            assert sid not in session_registry.recall_snapshot, (
                "a prompt with no recall block must not be paired with an old "
                "recall snapshot"
            )
        finally:
            session_registry.recall_snapshot.pop(sid, None)

    def test_router_arms_only_when_recall_ran(self):
        """Reintroduction guard: the arm must stay gated on _ttft_recall_ms, which
        is non-None exactly on the turn recall ran."""
        from core import session_router

        src = inspect.getsource(session_router)
        assert "_rsnap if _ttft_recall_ms is not None else None" in src, (
            "arming unconditionally would resurrect stale snapshots on respawn"
        )
