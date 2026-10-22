#!/usr/bin/env python3
"""Emit a pipeline gate's sub-agent prompt, refusing to emit an unbounded one.

USAGE
    python backend/skills/s_autonomous-pipeline/scripts/spawn_prompt.py \
        --gate gate0-skeptic

    # Only the recalled-knowledge block, to put into a prompt this tool does not
    # own (the REVIEW-stage specialist prompts):
    python backend/skills/s_autonomous-pipeline/scripts/spawn_prompt.py \
        --knowledge-only --project <PROJECT> --files <changed files...>

    # The REVIEW_PATTERNS.md rows that apply to a change, one compact line each
    # (id | pattern | trigger) instead of the whole checklist. Paths as args, or
    # one per line on stdin:
    python backend/skills/s_autonomous-pipeline/scripts/spawn_prompt.py \
        --patterns-for <changed files...> [--diff-file <unified diff>]
    git diff --name-only | python .../spawn_prompt.py --patterns-for-stdin

    # The FULL rows (what to verify, example bug) of patterns whose trigger is
    # present in the change:
    python backend/skills/s_autonomous-pipeline/scripts/spawn_prompt.py \
        --patterns-rows RP7 RP13

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
       or a usage error (including an unknown flag). For --patterns-for*: no
       paths given, or REVIEW_PATTERNS.md unreadable / unparseable.
    3  the prompt needs project knowledge and ``--project`` was not given (also:
       ``--knowledge-only`` without ``--project``). stdout is EMPTY, stderr says why.
       Refused rather than emitted with the slot unfilled, because an unfilled slot
       is the hand-paste step that used to be skipped.

stdout is empty on every non-zero exit ON PURPOSE. A caller that pipes stdout
without checking the status then spawns with NO prompt, which is loud and
immediately wrong, rather than with a prompt whose cap silently vanished.
"""
import argparse
import fnmatch
import re
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


# ── REVIEW_PATTERNS on-demand selection ──────────────────────────────────────
REVIEW_PATTERNS_PATH = Path(__file__).resolve().parent.parent / "REVIEW_PATTERNS.md"
_MATCH_TOKEN_RE = re.compile(r"`match: ([^`]+)`")
_ALWAYS = "*"


class PatternsError(ValueError):
    """REVIEW_PATTERNS.md could not be parsed into selectable rows."""


def parse_match_spec(spec: str) -> tuple[bool, list[str], list[str]]:
    """``(always, globs, keywords)`` for one ``match:`` spec.

    Raises PatternsError on an unknown term, so a typo cannot silently turn a
    row into one that never applies.
    """
    terms = spec.split()
    if terms == [_ALWAYS]:
        return True, [], []
    globs, kws = [], []
    for term in terms:
        kind, sep, value = term.partition(":")
        if not sep or not value or kind not in ("glob", "kw"):
            raise PatternsError(f"bad match term {term!r} in `match: {spec}`")
        (globs if kind == "glob" else kws).append(value.lower())
    if not terms:
        raise PatternsError("empty `match:` spec")
    return False, globs, kws


def parse_review_patterns(text: str) -> tuple[list[str], list[dict]]:
    """``(header_lines, rows)`` from the REVIEW_PATTERNS.md text.

    A row is every line starting ``| RP`` (the same definition the lint test
    counts). Each row dict carries ``id``, ``line`` and the parsed spec. A row
    with NO match token is treated as always-applies — selection fails toward
    showing a row, never toward hiding one — and flagged ``missing``. More than
    one token in a row is ambiguous and raises.
    """
    lines = text.splitlines()
    header: list[str] = []
    rows: list[dict] = []
    for i, line in enumerate(lines):
        if not header and line.startswith("| # |") and i + 1 < len(lines):
            header = [line, lines[i + 1]]
        if not line.startswith("| RP"):
            continue
        rid = line.split("|", 2)[1].strip()
        specs = _MATCH_TOKEN_RE.findall(line)
        if len(specs) > 1:
            raise PatternsError(f"{rid}: {len(specs)} `match:` tokens, expected one")
        if specs:
            always, globs, kws = parse_match_spec(specs[0].strip())
        else:
            always, globs, kws = True, [], []
        rows.append({"id": rid, "line": line, "always": always, "globs": globs,
                     "keywords": kws, "missing": not specs})
    if not rows:
        raise PatternsError("no `| RP` rows found")
    return header, rows


def row_applies(row: dict, paths: list[str], diff_text: str = "") -> bool:
    """True if the row is always-on, a glob hits a path (or its basename), or a
    keyword occurs in a path or the diff. Case-insensitive throughout."""
    if row["always"]:
        return True
    norm = [p.replace("\\", "/").lower() for p in paths]
    for p in norm:
        base = p.rsplit("/", 1)[-1]
        if any(fnmatch.fnmatchcase(p, g) or fnmatch.fnmatchcase(base, g)
               for g in row["globs"]):
            return True
    hay = "\n".join(norm) + "\n" + diff_text.lower()
    return any(k in hay for k in row["keywords"])


def _select(text: str, paths: list[str], diff_text: str):
    """(header, rows, hits) — the one selection both renderers use."""
    header, rows = parse_review_patterns(text)
    return header, rows, [r for r in rows if row_applies(r, paths, diff_text)]


def _summary(rows: list[dict], hits: list[dict], verb: str) -> list[str]:
    always = sum(1 for r in hits if r["always"])
    out = ["", f"{len(hits)} of {len(rows)} patterns apply ({always} always-on, "
               f"{len(hits) - always} matched by path/diff) — {verb}"]
    missing = [r["id"] for r in rows if r["missing"]]
    if missing:
        out.append(f"WARNING: rows with no `match:` token, shown as always-on: "
                   f"{', '.join(missing)}")
    return out


def render_patterns_for(text: str, paths: list[str], diff_text: str = "") -> str:
    """The FULL applying rows (table header, rows, count line). --patterns-for now
    prints render_patterns_compact; this stays the reference the compact form is
    measured against."""
    header, rows, hits = _select(text, paths, diff_text)
    out = list(header) + [r["line"] for r in hits] + _summary(
        rows, hits, "verify each listed row or mark it N/A; rows not listed do not "
                    "apply to these files.")
    return "\n".join(out) + "\n"


_COMPACT_HEADER = ["| # | Pattern | Trigger |", "|---|---------|---------|"]
_RP_ID_RE = re.compile(r"RP\d+")


def _compact_line(row: dict) -> str:
    """``| RPn | name | trigger |`` with the match token removed. Cells are split
    from the LEFT: later columns may hold a literal or escaped pipe, the first
    three never do."""
    cells = row["line"].split("|", 4)  # ['', id, pattern, trigger, rest]
    trigger = _MATCH_TOKEN_RE.sub("", cells[3]).strip() if len(cells) > 3 else ""
    return f"| {cells[1].strip()} | {cells[2].strip()} | {trigger} |"


def render_patterns_compact(text: str, paths: list[str], diff_text: str = "") -> str:
    """The rows render_patterns_for selects, one compact line each: id, pattern
    name and trigger, without the long verify/example columns (a REVIEW used to
    read every row in full and judge only a few). The
    reviewer fetches the full row of any row whose trigger is present with
    ``--patterns-rows``."""
    _, rows, hits = _select(text, paths, diff_text)
    out = list(_COMPACT_HEADER) + [_compact_line(r) for r in hits] + _summary(
        rows, hits,
        "give each listed row pass / N/A / fail; rows not listed do not apply to these "
        "files. Before judging a row whose trigger is present in the change, print its "
        "full row (what to verify + example bug): python "
        "backend/skills/s_autonomous-pipeline/scripts/spawn_prompt.py --patterns-rows "
        "RP<n> [RP<m> ...]")
    return "\n".join(out) + "\n"


def render_patterns_rows(text: str, ids: list[str]) -> str:
    """The full table header + the named rows, in the order given. Raises
    PatternsError naming the first id that is malformed or not in the file."""
    header, rows = parse_review_patterns(text)
    by_id = {r["id"].upper(): r for r in rows}
    out = list(header)
    for raw in ids:
        rid = raw.strip().upper()
        if not _RP_ID_RE.fullmatch(rid) or rid not in by_id:
            raise PatternsError(f"unknown pattern id {raw!r}")
        out.append(by_id[rid]["line"])
    return "\n".join(out) + "\n"


def _emit_rows(ids: list[str], source: str | None) -> int:
    src = Path(source) if source else REVIEW_PATTERNS_PATH
    try:
        rendered = render_patterns_rows(src.read_text(encoding="utf-8"), ids)
    except (OSError, PatternsError) as exc:
        sys.stderr.write(f"spawn_prompt: cannot print review pattern rows: {exc}\n")
        return 1
    sys.stdout.write(rendered)
    return 0


def _emit_patterns(paths: list[str], diff_file: str | None, source: str | None) -> int:
    paths = [p.strip() for p in paths if p.strip()]
    if not paths:
        sys.stderr.write("spawn_prompt: --patterns-for needs at least one path.\n")
        return 1
    src = Path(source) if source else REVIEW_PATTERNS_PATH
    try:
        text = src.read_text(encoding="utf-8")
        diff_text = (Path(diff_file).read_text(encoding="utf-8", errors="replace")
                     if diff_file else "")
        rendered = render_patterns_compact(text, paths, diff_text)
    except (OSError, PatternsError) as exc:
        sys.stderr.write(f"spawn_prompt: cannot select review patterns: {exc}\n")
        return 1
    sys.stdout.write(rendered)
    return 0


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
    source.add_argument(
        "--patterns-for", nargs="+", metavar="FILE",
        help="emit only the REVIEW_PATTERNS.md rows that apply to these paths",
    )
    source.add_argument(
        "--patterns-rows", nargs="+", metavar="RP",
        help="print the FULL REVIEW_PATTERNS.md rows for these ids (e.g. RP7 RP13)",
    )
    source.add_argument(
        "--patterns-for-stdin", action="store_true",
        help="like --patterns-for, reading the paths one per line from stdin",
    )
    parser.add_argument(
        "--diff-file",
        help="with --patterns-for*: diff text whose content also feeds kw: terms",
    )
    parser.add_argument(
        "--patterns-source",
        help="with --patterns-for* / --patterns-rows: alternate patterns file (default: the skill's)",
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

    selecting = bool(args.patterns_for) or args.patterns_for_stdin
    if args.patterns_rows:
        if args.diff_file:
            parser.error("--diff-file only applies to --patterns-for*; --patterns-source "
                         "to --patterns-for* / --patterns-rows")
        return _emit_rows(args.patterns_rows, args.patterns_source)
    if (args.diff_file or args.patterns_source) and not selecting:
        parser.error("--diff-file only applies to --patterns-for*; --patterns-source "
                     "to --patterns-for* / --patterns-rows")
    if selecting:
        paths = args.patterns_for or sys.stdin.read().splitlines()
        return _emit_patterns(paths, args.diff_file, args.patterns_source)

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
