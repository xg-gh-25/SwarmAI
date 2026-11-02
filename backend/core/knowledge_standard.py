"""The knowledge standard: what is worth keeping, and how it is written, at every door
into the brain (SWARMAI.md, "What a DDD is for, and how it stays true": quality is made
when knowledge is extracted, checks stay light).

One copy, read by every extractor:

- CDF's session review puts it in its prompt (``core/ingestion/sources/s7_review``);
- a new Brain's first-entry contract carries it (``core/brain_bootstrap/apply.entry_contract``),
  which is what the agent reads while it writes the entries.

Nothing checks an entry against it: it is what the extractor reads, not a gate.

- ``KEEP_ONLY_IF``, ``STANDS_ALONE``, ``NOT_KNOWLEDGE`` — the sentences, plain text
- ``WRITE_WHEN`` — when each destination is written; when its condition does not hold,
  nothing is written there (the fields it needs are required by the unit contract,
  ``contracts_v2``: applicability, a gap's proposed_next)
- ``sentences()`` — all of them, in order
- ``item(n, text)`` — one numbered prompt item, wrapped the way S7 lays its list out
"""
from __future__ import annotations

import textwrap

KEEP_ONLY_IF = ("Keep a unit only if all three hold: it is useful in another task; a capable "
                "person who was not here would go wrong or waste time without it; you can point "
                "to its evidence.")

STANDS_ALONE = ('Write each unit as situation (applicability: a recognisable trigger, not "when '
                'developing"), judgment (what; how for a process), reason (why; for a decision '
                "also rejected_alternatives) and evidence. It must stand on its own for a reader "
                'who was not here: no "above", "this" or "plan A".')

NOT_KNOWLEDGE = (
    "The state of the moment (not pushed, needs a build, not verified yet, a count that will "
    "change) is not knowledge and is not written into any unit.",
    "What one look at the code or the data answers (a signature, a schema, a constant's value, "
    "a file's layout) is no lesson.",
    "A problem still open is a gap, never a lesson.",
    "Commit hashes, run ids, version numbers of a document and approval dates are evidence, "
    "not knowledge: they stay out of a unit's text.",
)

WRITE_WHEN = (
    "The general knowledge takes a unit only when its rule can be stated in one sentence that "
    "still holds with every project, document and incident taken out.",
    "A gap is exactly one problem, with a concrete next step; a note on the progress of the "
    "work is not a gap.",
    "An event says what was done and what came of it.",
    "A project's knowledge takes only what changes how a later judgment in that project is "
    "made (a decision, a model, a pitfall with the sign that it is coming); what a piece of "
    "work did is in git and the activity record, not in the project's knowledge.",
)

#: the width S7's numbered list is wrapped to
WIDTH = 89


def sentences() -> list[str]:
    """Every sentence of the standard, in order."""
    return [KEEP_ONLY_IF, STANDS_ALONE, *NOT_KNOWLEDGE, *WRITE_WHEN]


def item(n: int, text: str) -> str:
    """``text`` as item *n* of a numbered prompt list (a hanging indent of three)."""
    return textwrap.fill(" ".join(text.split()), width=WIDTH, initial_indent=f"{n}. ",
                         subsequent_indent="   ", break_on_hyphens=False, break_long_words=False)
