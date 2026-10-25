#!/usr/bin/env python3
"""Emit a pipeline gate's sub-agent prompt, refusing to emit an unbounded one.

USAGE
    python backend/skills/s_autonomous-pipeline/scripts/spawn_prompt.py \
        --gate gate0-skeptic

    # Only the recalled-knowledge block, to put into a prompt this tool does not
    # own (the REVIEW-stage specialist prompts):
    python backend/skills/s_autonomous-pipeline/scripts/spawn_prompt.py \
        --knowledge-only --project <PROJECT> --files <changed files...>

    Use the output ONLY on exit 0.

WHY THIS EXISTS: gate prompts are hand-composed at spawn time, and a prompt that
states no scope cap gives its sub-agent no termination condition — measured over
4242 real spawns, an unbounded review-class spawn ran several times longer than a
bounded one. Writing the cap into a document the orchestrator is supposed to copy
did not hold, twice. So the prompt now comes from code, and the one failure mode
that matters is made impossible: a prompt without a numeric cap CANNOT be emitted.

PROJECT KNOWLEDGE: a prompt carrying the ``<PROJECT_KNOWLEDGE>`` slot gets the
project's DDD entries recalled for the changed files (scripts/project_knowledge.py),
plus ``<PROJECT>`` and the changed-file list filled in. The block always says which
of three states it is in — recalled / none found / recall UNAVAILABLE — so an empty
or broken recall can never pass for "no knowledge exists".

EXIT CODES — the contract, not decoration:
    0  stdout is the prompt, ready to paste.
    2  the prompt states no numeric cap. stdout is EMPTY, stderr says why.
    1  the prompt could not be loaded at all (unknown gate, missing/empty file),
       or a usage error (including an unknown flag).
    3  the prompt needs project knowledge and ``--project`` was not given (also:
       ``--knowledge-only`` without ``--project``). stdout is EMPTY, stderr says why.
       Refused rather than emitted with the slot unfilled, because an unfilled slot
       is the hand-paste step that used to be skipped.

stdout is empty on every non-zero exit ON PURPOSE. A caller that pipes stdout
without checking the status then spawns with NO prompt, which is loud and
immediately wrong, rather than with a prompt whose cap silently vanished.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from spawn_budget import (  # noqa: E402  (path set above)
        BUDGET_MARKER,
        GATES,
        KNOWLEDGE_SLOT,
        GatePromptError,
        has_numeric_cap,
        load_gate,
        load_gate_file,
        requires_knowledge,
    )
    import project_knowledge  # noqa: E402  (path set above)
except ImportError as exc:  # pragma: no cover - defensive, must stay LOUD
    # Never degrade to exit 0 here. A silent success would emit nothing and read
    # as "this gate has no prompt", which is the failure this tool prevents.
    sys.stderr.write(
        f"spawn_prompt: cannot import spawn_budget ({exc}). The emitter and its "
        "definitions must sit in the same scripts/ directory.\n"
    )
    raise SystemExit(1)

class _Parser(argparse.ArgumentParser):
    """ArgumentParser that exits 1, not 2, on a usage error.

    Exit 2 is reserved for "the prompt states no numeric cap" — a signal that
    tells the reader to go add a cap to the prompt. argparse's default usage-error
    code is also 2, so a mistyped ``--gate`` id would arrive as a cap refusal and
    send the reader editing a prompt that is fine. Distinct causes need distinct
    codes; a usage error is a load failure, which is 1.
    """

    def error(self, message):
        self.exit(1, f"spawn_prompt: {message}\n")


def _fill_project(text: str, project: str, files: list[str]) -> str:
    """Fill the project placeholders, then the knowledge slot LAST.

    Order matters: recalled DDD text is arbitrary markdown, so it is inserted after
    every other substitution — a recalled entry that happens to contain
    ``<PROJECT>`` or the slot literal is never re-substituted.
    """
    listed = "\n".join(f"- {f}" for f in files) if files else "(none given)"
    text = text.replace("<PROJECT>", project)
    text = text.replace("<list of all changed files>", "\n" + listed)
    block, _state = project_knowledge.render_block(project, files)
    return text.replace(KNOWLEDGE_SLOT, block)


def main(argv=None) -> int:
    parser = _Parser(
        description="Emit a gate's sub-agent prompt (only on exit 0).",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--gate", choices=sorted(GATES),
        help="registered gate id whose prompt to emit",
    )
    source.add_argument(
        "--gate-file",
        help="path to a candidate prompt file, validated by the same contract",
    )
    source.add_argument(
        "--knowledge-only", action="store_true",
        help="emit only the recalled project-knowledge block (needs --project)",
    )
    parser.add_argument(
        "--project",
        help="project whose DDD is recalled into the prompt's knowledge slot",
    )
    parser.add_argument(
        "--files", nargs="*", default=[],
        help="the change's files; their names key the DDD recall",
    )
    args = parser.parse_args(argv)

    if args.knowledge_only:
        if not args.project:
            sys.stderr.write(
                "spawn_prompt: REFUSING --knowledge-only without --project — there is "
                "no DDD to recall from.\n"
            )
            return 3
        block, _state = project_knowledge.render_block(args.project, args.files)
        sys.stdout.write(block + "\n")
        return 0

    try:
        text = load_gate(args.gate) if args.gate else load_gate_file(args.gate_file)
    except GatePromptError as exc:
        sys.stderr.write(f"spawn_prompt: {exc}\n")
        return 1

    target = args.gate or args.gate_file
    if not has_numeric_cap(text):
        sys.stderr.write(
            f"spawn_prompt: REFUSING to emit {target} — it states no numeric cap on "
            f"files read or tool calls, so the sub-agent would get no termination "
            f"condition. Add a concrete cap to its '{BUDGET_MARKER}' line (e.g. "
            f"'read at most 4 files, at most 8 tool calls'). Bound the SEARCH, "
            f"never the clock.\n"
        )
        return 2

    if requires_knowledge(text):
        if not args.project:
            sys.stderr.write(
                f"spawn_prompt: REFUSING to emit {target} — its prompt carries the "
                f"project-knowledge slot {KNOWLEDGE_SLOT}, so it needs "
                f"--project <PROJECT> --files <changed files...>. Emitting it unfilled "
                f"would send the reviewer in without the project's recorded "
                f"knowledge.\n"
            )
            return 3
        text = _fill_project(text, args.project, args.files)

    # The EXPERT-ID anchor lets completion hooks attribute the answer to this gate.
    if args.gate and not text.lstrip().startswith("EXPERT-ID:"):
        text = f"EXPERT-ID: {args.gate}\n\n{text}"
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
