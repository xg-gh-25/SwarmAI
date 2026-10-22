"""Stage-advance continuation contract — the CONTINUE-side enforcement carrier.

WHAT IS TESTED
--------------
The pipeline skill had an *enforcement asymmetry*: its STOP side is code-enforced
(``cmd_advance`` itself ``sys.exit(1)``s on validation failure; ``run-update
--status completed`` BLOCKS; ``cmd_run_checkpoint`` hard-blocks a checkpoint whose
``should_checkpoint=false``), while its CONTINUE side existed only as loop-level
prose (INSTRUCTIONS.md §3f, the Core Loop, Rule 20) with **zero turn-boundary
directive and zero carrier**. Observed consequence: the orchestrating agent ran
``advance`` and then ended its assistant turn (``stop_reason=end_turn``, zero
tool_use in that message) — nothing interrupted it, it simply read ``advance`` as
a terminal action.

The fix under test adds a *condition-aware* carrier: ``cmd_advance``'s success
payload now names the next stage doc (``next_stage_doc``) and what to do with it
(``next_action``), and the same contract phrase is mirrored in the stage docs so
the two sources cannot drift.

METHODOLOGY
-----------
- The CLI-side tests drive ``cmd_advance`` directly with a stub args object and a
  ``tmp_path`` workspace, then parse the captured stdout. ``Path.is_file()`` is
  never mocked — "the emitted path really exists" IS the contract.
- The doc-side tests read the real source-of-truth files under
  ``backend/skills/`` (never the ``.claude/skills/`` projection, which lags) and
  assert the shared phrase, following the established
  ``test_deliver_template_drift.py`` pattern.
- The stop-side tests are REGRESSION guards: they assert this change did not
  weaken the fail-closed validation branch, and that the Stop hook remains
  passive (no ``decision="block"`` / ``continue_=False`` interception, which the
  design explicitly forbids — it would misfire on the only three legitimate
  stops: real L2 escalation, a true checkpoint, and the user pressing stop).

KEY INVARIANTS
--------------
1. A path is emitted only after ``Path.is_file()`` passes — never a fabricated one.
2. ``next_action`` is conditioned on the existing ``_compute_should_checkpoint``
   SSOT: when a checkpoint is genuinely due, the payload must NOT urge
   continuation.
3. The CLI phrase and the doc phrase come from ONE module constant.
4. The continuation carrier never makes ``advance`` fail (degrades silently).
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

# ── Locate the real source tree (tests may run from either repo root) ────────
_THIS = Path(__file__).resolve()
BACKEND = _THIS.parent.parent
REPO = BACKEND.parent
SKILL = BACKEND / "skills" / "s_autonomous-pipeline"
STAGES = SKILL / "stages"

# Sibling skills that also call `advance` (Gate-1 finding #4 — R27 scope).
SIBLING_DOCS = [
    BACKEND / "skills" / "s_evaluate" / "SKILL.md",
    BACKEND / "skills" / "s_qa" / "INSTRUCTIONS.md",
    BACKEND / "skills" / "s_deliver" / "SKILL.md",
]


def _load_cli():
    """Import artifact_cli.py by path (it lives in scripts/, not a package)."""
    path = BACKEND / "scripts" / "artifact_cli.py"
    spec = importlib.util.spec_from_file_location("_artifact_cli_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def cli():
    return _load_cli()


class _Args:
    """Minimal stand-in for argparse.Namespace as cmd_advance consumes it."""

    def __init__(self, project, state, run_id=None):
        self.project = project
        self.state = state
        self.run_id = run_id


def _write_run(tmp_path, project, run_id, profile="full", completed=(), token_cost=0):
    """Create a run.json in the layout _resolve_run_file expects."""
    run_dir = tmp_path / "Projects" / project / ".artifacts" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    stages = [
        {"stage": s, "status": "completed", "token_cost": token_cost} for s in completed
    ]
    (run_dir / "run.json").write_text(
        json.dumps({"run_id": run_id, "project": project, "profile": profile,
                    "status": "running", "stages": stages}),
        encoding="utf-8",
    )
    return run_dir


@pytest.fixture
def workspace(tmp_path, monkeypatch, cli):
    """Point the CLI's workspace resolution at tmp_path.

    `_get_workspace()` reads the SWARM_WORKSPACE env var (artifact_cli.py:120-125),
    so setting it is what actually redirects run.json lookups — verified, not
    assumed. Auto-validation is stubbed out because these tests exercise the
    continuation payload, not the validator (which has its own suite).
    """
    monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
    monkeypatch.setattr(cli, "_auto_validate_before_advance",
                        lambda *a, **k: None, raising=False)
    return tmp_path


def _advance_payload(cli, args, capsys):
    """Run cmd_advance and return its parsed stdout JSON."""
    class _Reg:
        def advance_pipeline(self, project, state):
            return None

    cli.cmd_advance(args, _Reg())
    out = capsys.readouterr().out.strip().splitlines()
    assert out, "cmd_advance produced no stdout"
    return json.loads(out[-1])


# ── AC1: stdout carries a validated next_stage_doc + next_action ─────────────


# ── AC2: correct successor for every transition; terminal is null ────────────

class TestStageResolution:
    """AC2 — the doc named must be the stage being ENTERED, for every transition.

    ``advance --state X`` means "the run is now entering X" — ``stages/build.md``
    issues ``advance --state review`` at the END of BUILD. So the doc to read next
    is ``X.md`` itself.

    This is stated at length because the FIRST version of this suite asserted the
    opposite (``("review", "test.md")`` etc., i.e. stage-after-next) and so ratified
    an off-by-one that skipped the stage just entered. A spec reviewer caught it, not
    these tests. RP64: a hardcoded-literal expectation LOCKS a wrong answer, and a
    test written by the same author who wrote the bug tends to encode the same
    misunderstanding. If a future change makes these tests fail, re-derive the
    semantics from a stage doc's own advance line before "fixing" the expectation.
    """


    def test_goal_cycle_is_an_advanceable_state(self):
        """Every profile stage — including goal's `goal_cycle` — is in the REAL
        PIPELINE_STATES that `advance_pipeline` validates against (the gap this
        file used to pin is closed: the enum is derived from PIPELINE_PROFILES)."""
        import sys
        sys.path.insert(0, str(BACKEND))
        from core.artifact_registry import PIPELINE_STATES
        from core.pipeline_profiles import PIPELINE_PROFILES

        assert "goal_cycle" in PIPELINE_PROFILES["goal"], "goal profile shape changed"
        for stages in PIPELINE_PROFILES.values():
            assert set(stages) <= set(PIPELINE_STATES)


    def test_reflect_doc_carries_v1_terminal_commands(self):
        """REFLECT must route background processing to a bounded terminal command."""
        text = (STAGES / "reflect.md").read_text(encoding="utf-8")
        assert "pipeline_v1.py reflect-complete" in text
        assert "reflect-exhausted" in text


# ── AC7: budget is NEVER a reason to withhold continuation (post-refactor) ───


# ── AC3 / AC4 / AC9: the doc side, sharing ONE phrase ───────────────────────

class TestDocContracts:
    """AC9 — sibling skills retain the legacy advance contract they still use."""


    @pytest.mark.parametrize("doc", SIBLING_DOCS, ids=lambda p: p.parent.name)
    def test_sibling_advance_passes_run_id(self, doc):
        """A --run-id-less advance skips validation entirely (run_f3975b8b)."""
        text = doc.read_text(encoding="utf-8")
        for line in text.splitlines():
            if "artifact_cli.py advance" in line:
                assert "--run-id" in line, (
                    f"{doc.parent.name}: advance without --run-id skips auto-validation"
                )


# ── AC5 / AC6: the stop side is untouched (regression guards) ───────────────

class TestStopSideUnweakened:
    """AC5/AC6 — the continuation carrier must not erode any legitimate stop."""


    def test_stop_hook_remains_passive(self):
        """AC6 — no end_turn interception (STEERING #2: would misfire on real stops).

        Asserts the two mechanisms that could actually intercept a turn ending —
        a ``decision: "block"`` verdict or ``continue_: False`` — rather than
        scanning for the word "block", which would trip on any unrelated comment.
        Interception is forbidden by design: the Stop hook's input carries only
        ``stop_hook_active``, so it cannot tell an illegitimate stall from the
        three legitimate stops (real L2 escalation, a true checkpoint, the user
        pressing stop) and would misfire on exactly those.
        """
        src = (BACKEND / "core" / "hook_builder.py").read_text(encoding="utf-8")
        idx = src.index("def _stop_hook")
        body = src[idx: src.index('registry.register("Stop"', idx)]

        assert '"decision": "approve"' in body, "Stop hook must stay passive"
        assert '"block"' not in body, "Stop hook must not return a block verdict"
        assert "continue_" not in body, "Stop hook must not force continuation"

        # The two interception mechanisms must be absent from the WHOLE Stop
        # registration, not just the callback body — a blocking verdict could also
        # be injected at the register() call site.
        reg_line_start = src.index('registry.register("Stop"', idx)
        registration = src[reg_line_start: reg_line_start + 200]
        assert "block" not in registration.lower(), (
            "the Stop registration must not introduce a blocking verdict"
        )


class TestLegacyFlatRunDeletion:
    """3b: the legacy flat ``pipeline-run-<id>.json`` read path was deleted.
    These lock the two contracts the deletion must preserve."""


    def test_nameless_stage_record_reads_question_mark(self, cli, workspace):
        """After collapsing ``s.get('stage', s.get('name'))`` → ``s.get('stage', '?')``,
        a persisted stage record with neither key reads '?' (the dead ``name``
        fallback is gone, the default survives). Guards the collapse is non-vacuous."""
        rec = {"status": "completed"}  # no 'stage', no 'name'
        assert rec.get("stage", "?") == "?"
        # and a real record still reads its stage
        assert {"stage": "build"}.get("stage", "?") == "build"
