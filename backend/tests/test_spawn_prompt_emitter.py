"""Gate-prompt emitter contract — the spawn-time half of the bounded-spawn rule.

WHY THIS MODULE EXISTS (read before changing an assertion):

Sub-agent spawns whose prompt states no scope cap run far longer than bounded
ones — measured over 4242 real Agent spawns, an unbounded review-class spawn's
median wall-clock was ~5.7x a bounded one's. Two earlier fixes wrote a fenced
``SCOPE BUDGET`` into the stage docs and collapsed seven wordings into one
authority block, and ``test_deliver_template_drift.py`` guards that those
DOCUMENTS say the right thing. Both are green. Yet gate spawns kept shipping
unbounded, because the orchestrator HAND-COMPOSES each gate prompt and a
document it is supposed to copy from is not a mechanism.

So the prompt bodies now live in ``data/gate-prompts/<gate>.md`` — one whole file
per gate, with NO enclosing fence — and the orchestrator gets them by running
``scripts/spawn_prompt.py --gate <id>`` instead of retyping. Two properties are
what make this worth its code:

1. A cap-less prompt is UNEMITTABLE: the emitter exits 2 with EMPTY stdout, so a
   budget cannot be silently dropped by an author.
2. There is no fenced body left in the stage docs to copy, so the lazy path now
   goes THROUGH the emitter rather than around it.

WHY WHOLE-FILE AND NOT FENCE EXTRACTION (a defect this design avoids by
construction, found by adversarial review before it shipped): the Gate-1 body in
``stages/build.md`` itself contained a nested ``` block. Any find-the-next-fence
extractor closes on that nested fence and silently drops the tail — in the real
file that dropped the MANDATORY output-format spec, so ``GATE 1 VERDICT``
vanished from the emitted prompt while the numeric caps survived and the
fail-closed check still passed. A capped prompt with no verdict format is worse
than an unbounded one. A depth-counting parser does not save it either: per
CommonMark a ``` fence closes a ``` fence, so same-length nesting is genuinely
ambiguous. Whole-file prompts delete the problem instead of out-parsing it.

INTEGRITY OF THE MOVE is pinned by SHA-256 digests of the bodies as they existed
in their stage docs immediately BEFORE the move (``_PRE_MOVE_SHA256``). A digest
is used rather than ``git show HEAD:<stage doc>`` deliberately: after the move
commit lands, HEAD's stage doc no longer contains the body, so a HEAD-relative
comparison degrades to comparing nothing — a test that passes because it checks
nothing. The digest is immutable, so it keeps working forever.
"""
import hashlib
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1]
_SKILL_ROOT = _BACKEND / "skills" / "s_autonomous-pipeline"
_SCRIPTS = _SKILL_ROOT / "scripts"
_GATE_PROMPTS = _SKILL_ROOT / "data" / "gate-prompts"

# The three stage docs that USED to carry a pasteable prompt body. After the move
# they must carry none — that deletion is the point (AC8).
_STAGE_DOCS = (
    _SKILL_ROOT / "stages" / "evaluate.md",
    _SKILL_ROOT / "stages" / "build.md",
    _SKILL_ROOT / "stages" / "deliver.md",
)

_GATE_IDS = ("gate0-skeptic", "gate1-skeptic", "gate2-adversarial")

# SHA-256 of each prompt body EXACTLY as it stood between the fences in its stage
# doc before the move (body = the lines strictly between the open/close fence
# lines, trailing newline included). Measured pre-move; never recompute these
# from the post-move files — that would make the check circular.
_PRE_MOVE_SHA256 = {
    "gate0-skeptic": "75141a94d276a7cbc610f37af031fd0d0e1fb0cf69ff58da88711778cf6bbc67",  # pragma: allowlist secret
    # gate0/1/2 INTENTIONALLY edited for gate speed (pipeline-run-budget §11): caps cut to
    # 6 / 8 / 8 tool calls with the evidence handed in (gate1 inputs, gate2 diff file).
    # gate1 INTENTIONALLY edited: the re-check paragraph (a second round reviews the
    # fix only; a third needs a recorded reason — docs/design/pipeline-run-budget.md H2).
    "gate1-skeptic": "df5ddc77d651921b674b18e824ad7bd6a930669a7258cc9fda310a7794271190",  # pragma: allowlist secret
    # gate2 was INTENTIONALLY edited after the move: its hand-paste "Project-Specific
    # Traps" block (which named two TECH.md section titles and was filled for 1 of 13
    # real expert prompts) became the <PROJECT_KNOWLEDGE> slot that spawn_prompt.py
    # fills from DDD recall. Recomputed in the same change, as this test instructs.
    # ...and again for the same re-check paragraph (H2).
    "gate2-adversarial": "92fc9d37bd309a0a4e04369469910ae16ace7816bd5022cfb3c0a9de7e3d9ce2",  # pragma: allowlist secret
}


def _load_spawn_budget():
    """Import ``scripts/spawn_budget.py`` by path.

    A dotted import is impossible: the package directory is
    ``s_autonomous-pipeline``, and a hyphen is not a legal identifier.
    """
    path = _SCRIPTS / "spawn_budget.py"
    spec = importlib.util.spec_from_file_location("spawn_budget", path)
    assert spec and spec.loader, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_emitter(*args):
    """Run the emitter as a real subprocess and return (rc, stdout, stderr).

    Driven as a subprocess on purpose: the exit code IS half the contract
    (exit 2 + empty stdout), and only a real process exercises it.
    """
    proc = subprocess.run(
        [sys.executable, str(_SCRIPTS / "spawn_prompt.py"), *args],
        capture_output=True, text=True, timeout=60,
    )
    return proc.returncode, proc.stdout, proc.stderr


# ---------------------------------------------------------------------------
# AC3 — the move was lossless
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("gate_id", _GATE_IDS)
def test_gate_prompt_matches_pre_move_digest(gate_id):
    """Each gate file reproduces its pre-move body byte-for-byte.

    Guards the migration itself: transcribing a 143-line prompt by hand can drop
    content, which is the same silent-truncation class that killed the
    fence-extraction design. A digest catches a single changed byte.
    """
    body = (_GATE_PROMPTS / f"{gate_id}.md").read_bytes()
    actual = hashlib.sha256(body).hexdigest()
    assert actual == _PRE_MOVE_SHA256[gate_id], (
        f"{gate_id}.md no longer matches the prompt body that was moved out of the "
        f"stage doc (expected {_PRE_MOVE_SHA256[gate_id][:12]}…, got {actual[:12]}…). "
        "If you INTENTIONALLY edited this prompt, recompute the digest in the same "
        "commit and say why in the message — do not silently relax this test."
    )


def test_gate2_emit_changes_only_the_filled_placeholders():
    """With --project, gate2 is the gate file with exactly its placeholders filled.

    Expected text is assembled from the file on disk, not from the emitter, so the
    emitter cannot pass by agreeing with itself: everything before ``<PROJECT>``
    and everything after the knowledge slot must come through unchanged.
    """
    raw = (_GATE_PROMPTS / "gate2-adversarial.md").read_text()
    rc, out, err = _run_emitter("--gate", "gate2-adversarial", "--project", "NoSuchProject")
    assert rc == 0, err
    head = raw[: raw.index("<PROJECT>")]
    tail = raw[raw.index("<PROJECT_KNOWLEDGE>") + len("<PROJECT_KNOWLEDGE>"):]
    assert out.startswith(head), "text before the project placeholder was altered"
    assert out.endswith(tail), "text after the knowledge slot was altered"


def test_gate1_prompt_retains_the_output_format_block():
    """The Gate-1 prompt must still carry its verdict format.

    This is the specific content a fence-extracting emitter silently dropped: the
    numeric caps sat before the nested fence and survived, so every cap-based
    check still passed while the verdict spec was gone. Asserting the caps alone
    would not have caught it — this asserts the part that actually disappeared.
    """
    text = (_GATE_PROMPTS / "gate1-skeptic.md").read_text()
    assert "GATE 1 VERDICT" in text, (
        "gate1-skeptic.md lost its 'GATE 1 VERDICT' output-format block. A prompt "
        "with caps but no verdict format returns an unparseable answer — the "
        "failure mode this file's structure exists to prevent."
    )
    assert "API Existence" in text, "gate1-skeptic.md lost the Check 5 section"


# ---------------------------------------------------------------------------
# AC1/AC3 — the emitter emits the real prompt, caps included
# ---------------------------------------------------------------------------

#: Gates whose prompt carries no project-knowledge slot — emitted byte-for-byte.
_VERBATIM_GATE_IDS = ("gate0-skeptic", "gate1-skeptic")


@pytest.mark.parametrize("gate_id", _VERBATIM_GATE_IDS)
def test_emitter_prints_the_gate_file_verbatim(gate_id):
    """stdout is the gate file, unmodified, under one prepended attribution line.

    The ``EXPERT-ID: <gate>`` first line is metadata, not prompt prose: it is how a
    completed sub-agent is attributed, so stage-complete can count review rounds per
    gate. Everything after it must be the file byte-for-byte.

    The expected value is read from disk independently rather than re-derived
    from the emitter, so the assertion cannot be satisfied by the emitter simply
    agreeing with itself.
    """
    expected = f"EXPERT-ID: {gate_id}\n\n" + (_GATE_PROMPTS / f"{gate_id}.md").read_text()
    rc, out, err = _run_emitter("--gate", gate_id)
    assert rc == 0, f"emitter failed for {gate_id}: rc={rc} stderr={err!r}"
    assert out == expected, (
        f"emitter output for {gate_id} differs from the gate file — it must print "
        "the file verbatim and never re-author prompt prose."
    )


@pytest.mark.parametrize("gate_id", _GATE_IDS)
def test_emitted_prompt_states_a_numeric_cap(gate_id):
    """Every emitted prompt carries a SCOPE BUDGET with real numbers.

    Asserted per gate, not in aggregate: an aggregate check passes while one
    gate silently loses its cap.
    """
    sb = _load_spawn_budget()
    extra = ("--project", "NoSuchProject") if gate_id == "gate2-adversarial" else ()
    rc, out, _ = _run_emitter("--gate", gate_id, *extra)
    assert rc == 0
    assert sb.BUDGET_MARKER in out, f"{gate_id} prompt lost its {sb.BUDGET_MARKER} line"
    assert sb.has_numeric_cap(out), (
        f"{gate_id} prompt states {sb.BUDGET_MARKER} but no numeric file/tool-call "
        "cap — the sub-agent gets no termination condition."
    )


# ---------------------------------------------------------------------------
# AC2 — fail CLOSED: a cap-less prompt is unemittable
# ---------------------------------------------------------------------------

#: A prompt body long enough to clear the no-task floor, so these fixtures
#: exercise the CAP check instead of being rejected for having no instructions.
_REAL_TASK = (
    "You are a skeptic reviewing a plan. Answer the four questions below and "
    "nothing else. Confirm or refute each of the three claims with file:line "
    "evidence you actually read.\n\n"
)


def test_capless_gate_file_exits_2_with_empty_stdout(tmp_path):
    """A prompt whose numbers were stripped must not be emittable.

    Empty stdout is load-bearing, not cosmetic: a caller that pipes stdout
    without checking the exit code must get nothing rather than a prompt whose
    cap silently vanished.
    """
    capless = tmp_path / "capless.md"
    capless.write_text(_REAL_TASK + "SCOPE BUDGET: be brief and do not over-search.\n")
    rc, out, err = _run_emitter("--gate-file", str(capless))
    assert rc == 2, f"expected exit 2 for a cap-less prompt, got {rc}"
    assert out == "", f"stdout must be EMPTY on the fail-closed path, got {out!r}"
    assert "cap" in err.lower(), f"stderr must explain the refusal, got {err!r}"


def test_capped_gate_file_is_emitted(tmp_path):
    """The negative test above must not pass merely because the path is broken.

    Same harness, same flag, only the cap differs — so a wholesale failure of
    --gate-file cannot masquerade as fail-closed behaviour.
    """
    capped = tmp_path / "capped.md"
    body = _REAL_TASK + "SCOPE BUDGET: read at most 4 files, at most 8 tool calls.\n"
    capped.write_text(body)
    rc, out, err = _run_emitter("--gate-file", str(capped))
    assert rc == 0, f"a capped prompt must emit, got rc={rc} stderr={err!r}"
    assert out == body


def test_cap_outside_the_budget_statement_does_not_count(tmp_path):
    """A cap in unrelated prose must not satisfy the check.

    A whole-file scan passes when the number sits in an example, a quotation or a
    stray sentence, so the prompt ships unbounded while the check reads green. The
    sibling drift test hit exactly this and scoped its search to the budget
    paragraph; the emitter has to apply the same scope, or the shared definition
    has diverged in the place that matters.
    """
    stray = tmp_path / "stray.md"
    stray.write_text(_REAL_TASK + "Somewhere in prose: at most 4 files were touched.\n")
    rc, out, _ = _run_emitter("--gate-file", str(stray))
    assert rc != 0, "a cap outside the budget statement must not license an emit"
    assert out == ""


def test_negated_cap_does_not_count(tmp_path):
    """A prohibition is not a cap.

    The pattern sees a number, not the polarity of the sentence around it, so
    "do not limit yourself to at most 4 files" reads as a permission unless the
    budget statement is what gets scanned.
    """
    negated = tmp_path / "negated.md"
    negated.write_text(
        _REAL_TASK + "Note: do not limit yourself to at most 4 files.\n"
    )
    rc, out, _ = _run_emitter("--gate-file", str(negated))
    assert rc != 0, "a negated cap must not license an emit"
    assert out == ""


def test_zero_cap_is_rejected(tmp_path):
    """"at most 0 files" bounds nothing a reviewer can act on."""
    zero = tmp_path / "zero.md"
    zero.write_text(_REAL_TASK + "SCOPE BUDGET: read at most 0 files.\n")
    rc, out, _ = _run_emitter("--gate-file", str(zero))
    assert rc != 0, "a zero cap instructs the reviewer to read nothing — reject it"
    assert out == ""


def test_budget_without_a_task_is_rejected(tmp_path):
    """A file that is only a budget line is not a prompt.

    It passes the cap check and would spawn a sub-agent with bounds but no
    instructions — bounded, and useless.
    """
    only_budget = tmp_path / "only_budget.md"
    only_budget.write_text("SCOPE BUDGET: read at most 4 files, at most 8 tool calls.\n")
    rc, out, err = _run_emitter("--gate-file", str(only_budget))
    assert rc != 0, "a budget with no task must not be emittable"
    assert out == ""
    assert "task" in err.lower(), f"stderr should name the missing task, got {err!r}"


# ---------------------------------------------------------------------------
# AC10 — a missing gate file fails LOUDLY, never empty-success
# ---------------------------------------------------------------------------

def test_missing_gate_file_fails_loudly(tmp_path):
    """A vanished prompt must be a named error, not a silent empty emit.

    The dangerous shape is exit 0 with empty stdout: the orchestrator would
    spawn with no prompt at all and nothing would say why.
    """
    rc, out, err = _run_emitter("--gate-file", str(tmp_path / "nope.md"))
    assert rc != 0, "a missing gate file must not exit 0"
    assert out == ""
    assert "nope.md" in err, f"stderr must name the missing file, got {err!r}"


def test_unknown_gate_id_exits_1_not_2():
    """A mistyped gate id is a LOAD failure (1), never a cap refusal (2).

    Exit 2 tells the reader to go add a cap to a prompt. If a typo also produced
    2, they would edit a prompt that was never the problem — so the two causes
    must not share a code. argparse's default usage-error code IS 2, which is the
    collision this asserts against; `rc != 0` was too loose to see it.
    """
    rc, out, _ = _run_emitter("--gate", "gate99-nonexistent")
    assert rc == 1, f"expected exit 1 (load failure) for an unknown gate, got {rc}"
    assert out == ""


# ---------------------------------------------------------------------------
# AC8 — the retype surface is GONE from the stage docs
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("doc", _STAGE_DOCS, ids=lambda p: p.name)
def test_stage_doc_has_no_pasteable_prompt_body(doc):
    """A stage doc must not carry its own copy of a gate prompt.

    This is what distinguishes the fix from the two that preceded it. While an
    inline body remains, retyping it stays the default-reachable path and a
    pointer line merely competes with it. The body has to be absent, not
    deprecated.
    """
    sb = _load_spawn_budget()
    text = doc.read_text()
    assert sb.BUDGET_MARKER not in text, (
        f"{doc.name} still contains a {sb.BUDGET_MARKER} line — a pasteable prompt "
        "body is back in the stage doc, which restores the hand-compose path this "
        f"change removed. The body belongs in data/gate-prompts/."
    )


@pytest.mark.parametrize("doc", _STAGE_DOCS, ids=lambda p: p.name)
def test_stage_doc_points_at_the_emitter(doc):
    """Each spawn point tells the orchestrator how to obtain its prompt."""
    text = doc.read_text()
    assert "spawn_prompt.py" in text, (
        f"{doc.name} no longer tells the orchestrator to run the emitter, so the "
        "prompt has no discoverable source."
    )


@pytest.mark.parametrize("doc", _STAGE_DOCS, ids=lambda p: p.name)
def test_stage_doc_states_the_exit_status_contract(doc):
    """The pointer must say the output is only usable on exit 0.

    Without it, a reader can paste empty stdout from the fail-closed path and
    spawn with no prompt — turning a refusal into a worse failure.
    """
    text = doc.read_text()
    assert "exit 0" in text, (
        f"{doc.name} does not state the exit-0 condition for using the emitter's "
        "output; empty stdout from the fail-closed path could be pasted blindly."
    )


@pytest.mark.parametrize("gate_id", ("gate0-skeptic", "gate1-skeptic", "meta-review"))
def test_gate_prompt_is_attributed_by_expert_id(gate_id):
    """Every emitted gate prompt starts with the EXPERT-ID anchor the SubagentStop hook
    reads, so a completed gate is counted per id (pipeline-run-budget H1)."""
    from core.runtime_hooks import _extract_expert_id
    args = ("--gate", gate_id) + (("--project", "NoSuchProject", "--files", "backend/core/a.py")
                                  if gate_id == "meta-review" else ())
    rc, out, err = _run_emitter(*args)
    assert rc == 0, err
    assert out.splitlines()[0] == f"EXPERT-ID: {gate_id}"
    assert _extract_expert_id(out[:400]) == gate_id  # what the SubagentStop hook will record
