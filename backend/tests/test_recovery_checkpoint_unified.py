"""Unified involuntary-respawn recovery checkpoint tests.

WHAT IS TESTED
--------------
``SessionUnit._arm_recovery_checkpoint(trigger, *, allow_wrapup=False)`` — the shared
helper that arms the SAME rich recovery checkpoint the voluntary gated self-heal path
builds, so that INVOLUNTARY ``kill -> COLD (keep --resume)`` paths (RSS proactive
restart, streaming RSS kill, stuck-STREAMING / stuck-WAITING_INPUT recovery, watchdog
death) hand the next ``send()`` structured "where I left off" context too. The helper
adds NO new kill — it only enriches recovery on kills that already happen.

METHODOLOGY
-----------
Bare ``SessionUnit.__new__(SessionUnit)`` instances with attribute stubs (mirrors
tests/test_self_heal_checkpoint_enrichment.py) — no DB, no subprocess, no full
construction. ``_derive_heal_enrichment`` is stubbed per test; the git floor inside
``build_rich_checkpoint`` is neutralized by patching
``core.session_healing._run_git_command_async`` so no real ``git`` subprocess runs and
results are deterministic.

KEY PROPERTIES / INVARIANTS
---------------------------
- Builds + sets ``_heal_checkpoint`` from available context (enrichment + attrs).
- Idempotent: never clobbers an already-armed (richer voluntary) checkpoint.
- Total: never raises into the kill path — enrichment raising / returning {} / a
  missing/None ``_app_session_id`` all degrade to a no-op or empty-enrichment build.
- Ordering: for wired paths the checkpoint is armed BEFORE the COLD transition and
  the kill still happens (timing/mechanism unchanged).
"""

import asyncio

import pytest

from core.session_healing import TaskCheckpoint
from core.session_unit import SessionState, SessionUnit


@pytest.fixture
def no_git(monkeypatch):
    """Neutralize the git floor so build_rich_checkpoint runs no real subprocess.

    build_rich_checkpoint() calls module-global _run_git_command_async when a
    working_dir is provided (the helper always passes the SwarmWS path). Patching it
    to an async no-op keeps the floor empty + deterministic and avoids spawning git.
    """
    import core.session_healing as sh

    async def _empty(_cmd, _working_dir, timeout=3.0):
        return ""

    monkeypatch.setattr(sh, "_run_git_command_async", _empty)
    return monkeypatch


def _make_unit(state: SessionState = SessionState.STREAMING) -> SessionUnit:
    """Build a bare SessionUnit with the minimal attrs the helper + wired paths read.

    Leaves _files_touched / _pipeline_* / _health_sensor / _last_user_query UNSET so
    the helper exercises its getattr() defaults (mirrors the voluntary block).
    """
    unit = SessionUnit.__new__(SessionUnit)
    unit.session_id = "sess-recovery-test"
    unit.state = state
    unit._heal_checkpoint = None
    unit._wrapup_conclusion = ""
    unit._wrapper = None  # pid property -> None
    unit._last_event_time = None
    unit._streaming_start_time = None
    return unit


def _async_return(value):
    """Return an async function (bind as an instance attr) yielding *value*."""

    async def _fn(*_args, **_kwargs):
        return value

    return _fn


def _async_raise(exc: Exception):
    """Return an async function (bind as an instance attr) that raises *exc*."""

    async def _fn(*_args, **_kwargs):
        raise exc

    return _fn


class TestArmRecoveryCheckpointBuilds:
    """The helper builds a TaskCheckpoint and sets _heal_checkpoint."""

    @pytest.mark.asyncio
    async def test_builds_and_sets_heal_checkpoint(self, no_git):
        """History available (mocked enrichment) -> checkpoint armed with context."""
        unit = _make_unit()
        unit._derive_heal_enrichment = _async_return(
            {
                "completed_steps": ["Wrote parser.py", "Wired router.py"],
                "pending_steps": ["Add error-path tests"],
                "key_findings": "parser complete; tests pending",
            }
        )

        assert unit._heal_checkpoint is None
        await unit._arm_recovery_checkpoint("rss_proactive")

        cp = unit._heal_checkpoint
        assert isinstance(cp, TaskCheckpoint)
        assert cp.trigger == "rss_proactive"
        assert cp.completed_steps == ["Wrote parser.py", "Wired router.py"]
        assert cp.pending_steps == ["Add error-path tests"]
        prompt = cp.to_continuation_prompt()
        assert "**Completed:**" in prompt
        assert "**Next:**" in prompt
        assert "parser complete; tests pending" in prompt

    @pytest.mark.asyncio
    async def test_threads_enrichment_attrs_and_trigger_to_builder(
        self, monkeypatch, no_git
    ):
        """Helper threads trigger + file tracker + pipeline attrs into the builder."""
        import core.session_unit as su

        captured = {}

        async def _spy_build(**kwargs):
            captured.update(kwargs)
            return TaskCheckpoint(original_request=kwargs.get("original_request", ""))

        monkeypatch.setattr(su, "build_rich_checkpoint", _spy_build)

        unit = _make_unit()
        unit._files_touched = ["a.py", "b.py"]
        unit._pipeline_run_id = "run-9"
        unit._pipeline_stage = "BUILD"
        unit._derive_heal_enrichment = _async_return({})

        await unit._arm_recovery_checkpoint("watchdog")

        assert captured["trigger"] == "watchdog"
        assert captured["file_tracker_paths"] == ["a.py", "b.py"]
        assert captured["pipeline_run_id"] == "run-9"
        assert captured["pipeline_stage"] == "BUILD"
        # No stored last-user-query attr -> empty original_request (else "").
        assert captured["original_request"] == ""
        # Involuntary callers never fold in the wrap-up conclusion.
        assert captured["agent_conclusion"] == ""

    @pytest.mark.asyncio
    async def test_allow_wrapup_gates_conclusion(self, monkeypatch, no_git):
        """allow_wrapup=True folds the wrap-up conclusion in; default False omits it."""
        import core.session_unit as su

        seen = []

        async def _spy_build(**kwargs):
            seen.append(kwargs.get("agent_conclusion"))
            return TaskCheckpoint(original_request="")

        monkeypatch.setattr(su, "build_rich_checkpoint", _spy_build)

        unit = _make_unit()
        unit._wrapup_conclusion = "Parser done; tests remain"
        unit._derive_heal_enrichment = _async_return({})

        await unit._arm_recovery_checkpoint("rss_proactive")  # default allow_wrapup
        unit._heal_checkpoint = None  # reset so the second arm proceeds
        await unit._arm_recovery_checkpoint("rss_proactive", allow_wrapup=True)

        assert seen == ["", "Parser done; tests remain"]


class TestArmRecoveryCheckpointIdempotent:
    """The helper never clobbers an already-armed (richer voluntary) checkpoint."""

    @pytest.mark.asyncio
    async def test_does_not_clobber_existing_checkpoint(self, monkeypatch):
        """A pre-set _heal_checkpoint is preserved; builder + enrichment not invoked."""
        import core.session_unit as su

        sentinel = TaskCheckpoint(original_request="VOLUNTARY", trigger="turn_approaching")
        unit = _make_unit()
        unit._heal_checkpoint = sentinel

        build_calls = []
        derive_calls = []

        async def _spy_build(**kwargs):
            build_calls.append(kwargs)
            return TaskCheckpoint(original_request="SHOULD_NOT_HAPPEN")

        async def _spy_derive(*_a, **_k):
            derive_calls.append(True)
            return {}

        monkeypatch.setattr(su, "build_rich_checkpoint", _spy_build)
        unit._derive_heal_enrichment = _spy_derive

        await unit._arm_recovery_checkpoint("rss_streaming")

        # Untouched — the richer voluntary checkpoint wins.
        assert unit._heal_checkpoint is sentinel
        assert build_calls == []
        assert derive_calls == []


class TestArmRecoveryCheckpointNeverRaises:
    """The helper never raises into the kill path (2.6 / total robustness)."""

    @pytest.mark.asyncio
    async def test_no_raise_when_enrichment_raises(self, no_git):
        """_derive_heal_enrichment raising -> no-op, no exception, no checkpoint."""
        unit = _make_unit()
        unit._derive_heal_enrichment = _async_raise(RuntimeError("boom"))

        await unit._arm_recovery_checkpoint("stuck_streaming")  # must not raise

        assert unit._heal_checkpoint is None

    @pytest.mark.asyncio
    async def test_no_raise_when_enrichment_empty_still_builds(self, no_git):
        """Empty enrichment {} still produces a (thin) checkpoint, never raises."""
        unit = _make_unit()
        unit._derive_heal_enrichment = _async_return({})

        await unit._arm_recovery_checkpoint("stuck_waiting_input")

        cp = unit._heal_checkpoint
        assert isinstance(cp, TaskCheckpoint)
        assert cp.trigger == "stuck_waiting_input"
        assert cp.completed_steps == []
        assert cp.pending_steps == []

    @pytest.mark.asyncio
    async def test_no_raise_with_real_derive_and_none_app_session_id(self, no_git):
        """Real _derive_heal_enrichment + _app_session_id=None -> {} -> builds, no raise."""
        unit = _make_unit()
        unit._app_session_id = None  # real derive short-circuits to {} (no DB import)

        await unit._arm_recovery_checkpoint("watchdog")  # real derive path

        assert isinstance(unit._heal_checkpoint, TaskCheckpoint)
        assert unit._heal_checkpoint.trigger == "watchdog"

    @pytest.mark.asyncio
    async def test_no_raise_with_real_derive_and_missing_app_session_id(self, no_git):
        """Real derive with the attr entirely absent -> {} -> builds, no raise."""
        unit = _make_unit()  # _app_session_id never set

        await unit._arm_recovery_checkpoint("rss_proactive")  # real derive path

        assert isinstance(unit._heal_checkpoint, TaskCheckpoint)


class TestWiredPathsArmBeforeColdTransition:
    """Wired involuntary paths arm the checkpoint BEFORE the COLD transition.

    The real ``_arm_recovery_checkpoint`` runs (enrichment stubbed, git neutralized);
    ``_crash_to_cold_async`` is replaced with a probe that records whether the
    checkpoint was already armed at the instant the COLD transition would fire. This
    proves ordering (arm-first) AND that the kill still happens with the unchanged
    ``clear_identity=False`` keep-resume contract.
    """

    @pytest.mark.asyncio
    async def test_force_unstick_streaming_arms_before_cold_and_kills(self, no_git):
        """stuck-STREAMING recovery: arm 'stuck_streaming' before COLD; kill still runs."""
        unit = _make_unit(state=SessionState.STREAMING)
        unit._derive_heal_enrichment = _async_return(
            {"completed_steps": ["did X"], "pending_steps": ["do Y"]}
        )

        probe = {}

        async def _probe_crash(*, clear_identity=False):
            probe["called"] = True
            probe["armed_at_crash"] = unit._heal_checkpoint is not None
            probe["clear_identity"] = clear_identity

        unit._crash_to_cold_async = _probe_crash

        await unit.force_unstick_streaming()

        assert probe.get("called") is True  # kill still happens
        assert probe.get("armed_at_crash") is True  # armed BEFORE the COLD transition
        assert probe.get("clear_identity") is False  # keep --resume, unchanged
        assert unit._heal_checkpoint is not None
        assert unit._heal_checkpoint.trigger == "stuck_streaming"

    @pytest.mark.asyncio
    async def test_force_unstick_waiting_input_arms_before_cold_and_kills(self, no_git):
        """stuck-WAITING_INPUT recovery: arm 'stuck_waiting_input' before COLD; kill runs."""
        unit = _make_unit(state=SessionState.WAITING_INPUT)
        unit._derive_heal_enrichment = _async_return({})

        probe = {}

        async def _probe_crash(*, clear_identity=False):
            probe["called"] = True
            probe["armed_at_crash"] = unit._heal_checkpoint is not None
            probe["clear_identity"] = clear_identity

        unit._crash_to_cold_async = _probe_crash

        await unit.force_unstick_waiting_input()

        assert probe.get("called") is True
        assert probe.get("armed_at_crash") is True
        assert probe.get("clear_identity") is False
        assert unit._heal_checkpoint is not None
        assert unit._heal_checkpoint.trigger == "stuck_waiting_input"

    @pytest.mark.asyncio
    async def test_force_unstick_streaming_noop_when_not_streaming(self, no_git):
        """Guard preserved: not-STREAMING -> early return, nothing armed, no kill."""
        unit = _make_unit(state=SessionState.IDLE)

        probe = {}

        async def _probe_crash(*, clear_identity=False):
            probe["called"] = True

        unit._crash_to_cold_async = _probe_crash

        await unit.force_unstick_streaming()

        assert probe.get("called") is None  # early-returned before kill
        assert unit._heal_checkpoint is None


class TestStaleCheckpointClearedOnSend:
    """send() Layer 0 clears stale _heal_checkpoint from prior aborted kills.

    Scenario: RSS spike → _arm_recovery_checkpoint armed → spike subsides → kill
    never fires → checkpoint lingers.  Next normal send() must clear it so a later
    unrelated restart doesn't inject stale context.
    """

    @pytest.mark.asyncio
    async def test_send_layer0_clears_stale_heal_checkpoint(self):
        """Production send() Layer 0 clears _heal_checkpoint in its synchronous preamble.

        Layer 0 is the section between 'Layer 0: Advance generation' comment and the
        first real ``await`` statement.  The clear must live there alongside
        _send_generation/_stop_event/_interrupted so no interleaving can occur.
        """
        import inspect

        source = inspect.getsource(SessionUnit.send)
        lines = source.split("\n")

        # Find Layer 0 marker and first real await (not in comments)
        layer0_start = None
        first_await = None
        for i, line in enumerate(lines):
            if "Layer 0:" in line and layer0_start is None:
                layer0_start = i
            stripped = line.lstrip()
            if (
                stripped.startswith("await ")
                and first_await is None
                and not stripped.startswith("#")
            ):
                first_await = i

        assert layer0_start is not None, "Layer 0 comment not found in send()"
        assert first_await is not None, "No real await found in send()"

        # The _heal_checkpoint clear must be between Layer 0 marker and first await
        layer0_section = "\n".join(lines[layer0_start:first_await])
        assert "_heal_checkpoint = None" in layer0_section, (
            "send() Layer 0 (synchronous preamble) must contain "
            "'self._heal_checkpoint = None' to clear stale checkpoints "
            "from prior aborted kills. Found Layer 0 at line "
            f"{layer0_start}, first await at line {first_await}, "
            f"but no _heal_checkpoint clear in between."
        )
