"""Layer-4 cross-boundary E2E for the stage-advance continuation contract.

WHAT IS TESTED
--------------
The seam this change crosses is a CONTRACT boundary with two sides that must stay
in lockstep:

  producer:  `artifact_cli.py advance` — emits `next_stage_doc` + `next_action` on stdout
  consumers: 11 markdown docs (7 pipeline stage docs + INSTRUCTIONS.md §3f + 3 sibling
             skills) that tell the agent those fields exist and what they mean

Layers 1-3 are unit-shaped: they import the module in-process and assert on the
returned tuple. None of them drive the SEAM — a real process emitting real stdout
that a real doc's instruction describes. That gap is exactly where a
"every unit passes, the contract is severed" failure hides.

METHODOLOGY
-----------
- The producer side runs the CLI as a REAL SUBPROCESS (`python artifact_cli.py
  advance ...`) against a real run.json in a tmp workspace, and the assertions run
  against its actual stdout bytes. Nothing about the module under change is mocked
  or imported — a wiring break (bad import, arg not registered, exception at
  startup, non-JSON output) surfaces here and cannot surface in Layers 1-3.
- The consumer side reads the shipped docs from disk and requires that the phrase
  the docs promise is the phrase the subprocess actually printed — binding the two
  sides to one observed value rather than to two independently-written literals.
- The emitted path is then really opened, because the whole point of the field is
  that the agent can Read it.

KEY INVARIANT
-------------
A doc that says "advance prints `next_stage_doc` — read it" must not be able to
drift from a CLI that no longer prints it (or prints something unreadable). This
test fails if either side moves alone.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_THIS = Path(__file__).resolve()
BACKEND = _THIS.parent.parent
CLI = BACKEND / "scripts" / "artifact_cli.py"
SKILL = BACKEND / "skills" / "s_autonomous-pipeline"

# Every doc that PROMISES the fields the CLI must emit.
CONSUMER_DOCS = [
    *(SKILL / "stages" / f"{s}.md" for s in
      ["evaluate", "think", "plan", "build", "review", "test", "deliver"]),
    SKILL / "INSTRUCTIONS.md",
    BACKEND / "skills" / "s_evaluate" / "SKILL.md",
    BACKEND / "skills" / "s_qa" / "INSTRUCTIONS.md",
    BACKEND / "skills" / "s_deliver" / "SKILL.md",
]


@pytest.fixture
def live_run(tmp_path):
    """A real run.json in a real workspace the real CLI will read.

    The run is deliberately kept at its FIRST stage and the E2E cases advance into
    `evaluate`. Reason, learned by running this file rather than by reasoning about
    it: the real subprocess executes `_auto_validate_before_advance`, which is
    fail-closed — it BLOCKS unless every completed stage's `artifact_id` resolves to
    a real artifact file on disk (and BUILD's must additionally carry a loadable
    `ac_coverage`). The in-process tests stub that validator out, so none of this is
    visible to them; the first two runs of this file failed on it. Rather than
    fabricate a whole artifact history (which would test my fixture, not the seam),
    the E2E drives the transition that has no upstream artifact requirement — the
    real process, the real validator, the real stdout, minimal scaffolding.
    """
    run_id = "run_e2e01"
    run_dir = tmp_path / "Projects" / "E2E" / ".artifacts" / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(
        json.dumps({
            "run_id": run_id, "project": "E2E", "profile": "full",
            "status": "running", "stages": [],
        }),
        encoding="utf-8",
    )
    return tmp_path, run_id


def _run_advance(workspace: Path, run_id: str, state: str):
    """Invoke the CLI as a real subprocess; return (returncode, stdout, stderr)."""
    env = {
        **os.environ,
        "SWARM_WORKSPACE": str(workspace),
        "PYTHONPATH": str(BACKEND),
    }
    proc = subprocess.run(
        [sys.executable, str(CLI), "advance",
         "--project", "E2E", "--state", state, "--run-id", run_id],
        capture_output=True, text=True, env=env, cwd=str(BACKEND), timeout=120,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _last_json(stdout: str) -> dict:
    for line in reversed([l for l in stdout.strip().splitlines() if l.strip()]):
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    raise AssertionError(f"no JSON object on stdout: {stdout!r}")


class TestProducerConsumerSeam:
    """The real CLI process must emit what the shipped docs promise."""

    def test_real_subprocess_emits_a_readable_next_stage_doc(self, live_run):
        """Drives the real command; then really opens the path it printed."""
        workspace, run_id = live_run
        rc, out, err = _run_advance(workspace, run_id, "evaluate")
        assert rc == 0, f"advance failed (rc={rc}): {err[-600:]}"

        payload = _last_json(out)
        assert payload["pipeline_state"] == "evaluate"

        doc = payload["next_stage_doc"]
        assert doc, f"the real process printed no next_stage_doc: {payload}"

        # The field's entire purpose is that the agent can Read it — so read it.
        body = Path(doc).read_text(encoding="utf-8")
        assert body.strip(), f"emitted doc is empty: {doc}"
        assert Path(doc).name == "evaluate.md", (
            f"advancing to evaluate must hand back evaluate.md, got {Path(doc).name}"
        )

    def test_docs_promise_exactly_what_the_process_prints(self, live_run):
        """Bind both sides to ONE OBSERVED value, not two written literals.

        The phrase is taken from the subprocess's real stdout and then required to
        appear in every consumer doc. If the CLI's wording changes without the docs
        following (or vice versa), this fails — which is the drift the shared
        constant exists to prevent, verified across the process boundary.
        """
        workspace, run_id = live_run
        rc, out, _ = _run_advance(workspace, run_id, "evaluate")
        assert rc == 0

        action = _last_json(out)["next_action"]
        assert action, "no next_action on the wire"

        # The load-bearing clause the docs also carry.
        phrase = "advance is not the end of your turn"
        assert phrase in action, (
            f"the live CLI no longer emits the contract phrase: {action!r}"
        )

        missing = [d.name for d in CONSUMER_DOCS
                   if phrase not in d.read_text(encoding="utf-8")]
        assert not missing, (
            f"these docs promise the contract but no longer state it: {missing}"
        )

    def test_field_names_the_docs_reference_are_really_on_the_wire(self, live_run):
        """The docs name `next_stage_doc` explicitly — it must exist as a key."""
        workspace, run_id = live_run
        rc, out, _ = _run_advance(workspace, run_id, "evaluate")
        assert rc == 0

        payload = _last_json(out)
        for key in ("next_stage_doc", "next_action"):
            assert key in payload, f"docs reference {key} but the wire lacks it"

        referencing = [d for d in CONSUMER_DOCS
                       if "next_stage_doc" in d.read_text(encoding="utf-8")]
        assert referencing, "no doc references next_stage_doc — the promise vanished"

    def test_failed_advance_emits_no_continuation_promise(self, live_run):
        """A rejected advance must not hand out an instruction to continue."""
        workspace, run_id = live_run
        rc, out, _ = _run_advance(workspace, run_id, "not_a_real_stage")

        assert rc != 0, "an invalid state must not be accepted"
        assert "advance is not the end of your turn" not in out, (
            "a failed advance must not urge continuation"
        )
