"""Shared definitions for the bounded-spawn contract.

This module is the ONE place the "does this prompt state a scope cap?" question is
answered. ``test_deliver_template_drift.py`` imports ``CAPS_RE`` from here rather
than keeping its own copy: two regexes for one contract drift apart silently, and
the divergence is invisible until an emitter refuses a prompt the test blesses (or
the reverse).

WHY A NUMERIC CAP AND NOT A WALL CLOCK: an unbounded review prompt has no
termination condition, so the sub-agent searches until it gives up. The fix is to
bound the SEARCH SPACE (files, tool calls, answer length), never the clock — a
timeout would truncate a review that is still finding real defects, converting a
slow success into a silent partial failure.

WHY THE PATTERN MATCHES INTENT AND NOT ONE PHRASING: real prompts in the corpus
use many spellings of the same cap ("at most 4 files", "At most 14 tool calls",
"read at most 4 files", "≤1 file"). A literal check on one of them passes by luck
and goes quietly vacuous the moment an author rewords, so the regex accepts any
"<limit-word> <number> <files|tool>" form.
"""
import re
from pathlib import Path

_SKILL_ROOT = Path(__file__).resolve().parents[1]
GATE_PROMPT_DIR = _SKILL_ROOT / "data" / "gate-prompts"

#: The literal every gate prompt must carry so a human can find its budget.
BUDGET_MARKER = "SCOPE BUDGET"

#: Slot a gate prompt carries when its sub-agent must receive the project's recalled
#: DDD knowledge. A prompt containing it can only be emitted with ``--project``
#: (spawn_prompt.py exit 3 otherwise): the knowledge used to be a hand-paste step,
#: and a hand-paste step is exactly what was skipped. Data-driven on purpose — a
#: gate opts in by carrying the slot, so there is no second list of gate ids to
#: keep in sync with the templates.
KNOWLEDGE_SLOT = "<PROJECT_KNOWLEDGE>"


def requires_knowledge(text: str) -> bool:
    """True if the prompt carries the project-knowledge slot."""
    return KNOWLEDGE_SLOT in (text or "")

#: A numeric ceiling on files read or tool calls made. Guards the INTENT (some
#: number bounds some countable resource), so rewording a cap does not silently
#: disable the check. Kept in sync with nothing — this IS the definition.
#:
#: The count is ``[1-9]\d*`` rather than ``\d+``: "at most 0 files" is
#: arithmetically a cap but instructs the reviewer to read nothing, so accepting
#: it would bless a prompt that cannot be carried out.
#:
#: ``(?:\w+\s+){0,2}`` before the noun admits the wordings authors actually write
#: ("at most 4 source files", "at most 6 more tool calls"). A cap this pattern
#: wrongly REJECTS blocks a human at exit 2, and a tool that blocks legitimate
#: work is a tool that gets deleted — which protects nothing.
CAPS_RE = re.compile(
    r"(?:at\s+most|no\s+more\s+than|up\s+to|max(?:imum)?(?:\s+of)?|limit(?:ed)?\s+to|"
    r"≤|<=)\s*([1-9]\d*)\s*(?:\w+\s+){0,2}(?:files?|tools?)\b",
    re.I | re.S,
)

#: How far past ``BUDGET_MARKER`` a cap may sit and still count as part of the
#: budget statement. Wide enough for a wrapped multi-line budget paragraph, tight
#: enough that prose elsewhere in the prompt cannot satisfy the check.
_BUDGET_WINDOW_CHARS = 800

#: Minimum instruction text (outside the budget statement) for a file to count as
#: a prompt. The smallest real gate prompt is an order of magnitude above this, so
#: the floor only ever rejects a file that is a budget and nothing else.
_MIN_TASK_CHARS = 80

#: gate id -> prompt file. Adding a gate means adding a file and an entry here;
#: both are required, and a missing file raises rather than emitting nothing.
GATES = {
    "gate0-skeptic": GATE_PROMPT_DIR / "gate0-skeptic.md",
    "gate1-skeptic": GATE_PROMPT_DIR / "gate1-skeptic.md",
    "gate2-adversarial": GATE_PROMPT_DIR / "gate2-adversarial.md",
}


class GatePromptError(RuntimeError):
    """A gate prompt could not be produced. Raised, never swallowed.

    Silence is the dangerous outcome here: an empty prompt spawned with exit 0
    would look like success while the sub-agent received no instructions at all.
    """


def has_numeric_cap(text: str) -> bool:
    """True if ``text`` states a numeric cap INSIDE its budget statement.

    Both halves are required: the marker proves a human declared a budget, and a
    number inside that statement proves the budget actually bounds something. A
    prompt carrying the marker with its numbers stripped is NOT bounded, and
    neither is one where the only number lives in unrelated prose.

    WHY THE SEARCH IS WINDOWED and not whole-file: a cap phrase appearing in an
    example, a quotation, or a negation ("do not limit yourself to at most 4
    files") satisfies a whole-file scan, so the check would pass for a prompt
    with no budget at all. The sibling drift test learned this the hard way —
    scoping its search to the budget paragraph was what made it non-vacuous — and
    applying one definition at two different SCOPES is the same divergence this
    module exists to prevent.
    """
    body = text or ""
    idx = body.find(BUDGET_MARKER)
    if idx == -1:
        return False
    return bool(CAPS_RE.search(body[idx: idx + _BUDGET_WINDOW_CHARS]))


def load_gate(gate_id: str) -> str:
    """Return the prompt body for ``gate_id``.

    Raises ``GatePromptError`` for an unknown id or an unreadable file. The whole
    file IS the prompt — there is no fence to locate, which is what keeps a
    nested code block inside a prompt from truncating it.
    """
    try:
        path = GATES[gate_id]
    except KeyError:
        known = ", ".join(sorted(GATES))
        raise GatePromptError(
            f"unknown gate id {gate_id!r}; known gates: {known}"
        ) from None
    return load_gate_file(path)


def load_gate_file(path) -> str:
    """Return the prompt body at ``path``, or raise ``GatePromptError``.

    Split out from :func:`load_gate` so a caller can validate an arbitrary
    candidate file (a test fixture, a prompt under review) through the same
    contract the registered gates use.
    """
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise GatePromptError(f"cannot read gate prompt {p}: {exc}") from exc
    if not text.strip():
        raise GatePromptError(f"gate prompt {p} is empty")
    # A prompt that is ONLY a budget line passes the cap check but gives the
    # sub-agent no task, so it must not be emittable either. The floor measures the
    # text OUTSIDE the budget statement: a prompt is a task plus its bounds, and
    # what makes it a prompt is the task. Checked after the empty guard so an empty
    # file reports "empty" rather than the more specific no-task message.
    task_text = _strip_budget_statement(text)
    if len(task_text) < _MIN_TASK_CHARS:
        raise GatePromptError(
            f"gate prompt {p} states a budget but no task ({len(task_text)} chars "
            f"outside the budget statement, need >= {_MIN_TASK_CHARS}) — a bounded "
            "prompt with nothing to do is not a prompt"
        )
    return text


def _strip_budget_statement(text: str) -> str:
    """Return ``text`` with its budget statement removed, for measuring the task.

    Drops the marker line and the window after it that :func:`has_numeric_cap`
    treats as the budget, so what remains is the instructions a sub-agent would
    actually act on.
    """
    idx = text.find(BUDGET_MARKER)
    if idx == -1:
        return text.strip()
    return (text[:idx] + text[idx + _BUDGET_WINDOW_CHARS:]).strip()
