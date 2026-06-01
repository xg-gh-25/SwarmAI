#!/usr/bin/env python3
"""md_freeze.py — Freeze-Translate-Stitch for safe Markdown document translation.

Problem this solves: translating a document by "read whole doc → rewrite whole doc"
forces the model to re-emit verbatim content (code blocks, JSON, file trees) it must
not change — wasting output tokens and risking silent drift. This tool freezes every
fenced code block behind a sentinel placeholder so the LLM only translates prose, then
stitches the byte-identical originals back in. `verify` proves structural equivalence.

Pipeline:
    freeze <in.md>  → <stem>.skeleton.md + <stem>.blocks.json   (LLM translates skeleton)
    stitch <skeleton.md> <blocks.json>  → reassembled output
    verify <source.md> <output.md>      → structural equivalence report (exit 0/1)

Design (stdlib only, deterministic):
- A line-based state machine toggles `in_block` on any line matching ^(```|~~~).
  Fenced blocks (including the delimiter lines) are captured verbatim; in the skeleton
  each block is replaced by a single line `⟦FROZEN_N⟧`. This handles ```lang tags and
  ```markdown templates whose inner content (headings, tables, HTML comments) must be
  frozen, not treated as document structure.
- Pure functions over input lines → identical input yields identical output (call-twice
  safe). No global state, no OS mechanisms.

Public functions: split_blocks, freeze_text, stitch_text, verify_texts.
CLI entry: main().
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SENTINEL_PREFIX = "⟦FROZEN_"
SENTINEL_SUFFIX = "⟧"
_SENTINEL_RE = re.compile(r"^⟦FROZEN_(\d+)⟧$")
# A fence delimiter: 3+ backticks or 3+ tildes at start of line (optional indent),
# optionally followed by an info string. CommonMark allows up to 3 spaces of indent.
_FENCE_RE = re.compile(r"^[ ]{0,3}(`{3,}|~{3,})")
_ATX_HEADING_RE = re.compile(r"^[ ]{0,3}#{1,6}[ \t]")
_TABLE_ROW_RE = re.compile(r"^[ ]{0,3}\|")


class SplitResult:
    """Outcome of separating prose from fenced blocks."""

    def __init__(self, skeleton: str, blocks: list, unclosed: bool):
        self.skeleton = skeleton    # prose with each fenced block replaced by a sentinel line
        self.blocks = blocks        # the verbatim fenced blocks, in order (index == sentinel N)
        self.unclosed = unclosed    # True if a fence was left open at EOF (closed defensively)


def _split_lines_keepends(text: str) -> list:
    """Split into lines on Markdown line breaks ONLY (\\n, \\r\\n, lone \\r), keeping terminators.

    Deliberately NOT ``str.splitlines()``: the stdlib splits on eight extra
    Unicode/control characters (VT \\x0b, FF \\x0c, FS \\x1c, GS \\x1d, RS \\x1e,
    NEL \\x85, LS U+2028, PS U+2029) that CommonMark does NOT treat as line
    breaks. If a fenced code block contained one of those followed by a fence
    marker, ``splitlines`` would break there and a spurious ``` could close the
    block early — leaking real code into the translatable skeleton. This splitter
    is the single source of line truth for freeze, stitch, and structure counting.
    """
    lines: list = []
    n = len(text)
    i = start = 0
    while i < n:
        c = text[i]
        if c == "\n":
            lines.append(text[start:i + 1])
            i += 1
            start = i
        elif c == "\r":
            # \r\n is one break; a lone \r is also a break.
            if i + 1 < n and text[i + 1] == "\n":
                lines.append(text[start:i + 2])
                i += 2
            else:
                lines.append(text[start:i + 1])
                i += 1
            start = i
        else:
            i += 1
    if start < n:
        lines.append(text[start:])  # final line without a trailing break
    return lines


def _fence_marker(line: str) -> str | None:
    """Return the fence run ('```' or '~~~...') if `line` opens/closes a fence, else None."""
    m = _FENCE_RE.match(line)
    return m.group(1) if m else None


def split_blocks(text: str) -> SplitResult:
    """Separate `text` into a skeleton (prose + sentinels) and a list of fenced blocks.

    The line-based state machine: outside a block, a fence line opens a block; inside a
    block, a fence line whose marker char matches and is at least as long closes it
    (CommonMark closing rule). An unclosed fence at EOF is closed defensively and flagged.

    This is shared by freeze (writes skeleton+blocks) and verify (re-derives both sides
    the same way), guaranteeing the two never disagree on what counts as a block.
    """
    # Preserve exact line content including the trailing newline so reassembly is
    # byte-perfect. Use the Markdown-only splitter (NOT str.splitlines) so Unicode/
    # control separators inside code blocks are never mistaken for line breaks.
    lines = _split_lines_keepends(text)

    skeleton_parts: list[str] = []
    blocks: list[str] = []
    in_block = False
    open_marker = ""          # the marker char run that opened the current block
    current: list[str] = []
    unclosed = False

    for line in lines:
        # Strip only the line terminator for matching; keep original for storage.
        bare = line.rstrip("\r\n")
        marker = _fence_marker(bare)

        if not in_block:
            if marker is not None:
                # Open a new fenced block.
                in_block = True
                open_marker = marker
                current = [line]
            else:
                skeleton_parts.append(line)
        else:
            current.append(line)
            # A closing fence uses the same char, length >= opening, and no info string.
            if marker is not None and marker[0] == open_marker[0] and len(marker) >= len(open_marker):
                rest = bare.strip()[len(marker):].strip()
                if rest == "":
                    # Close the block.
                    idx = len(blocks)
                    blocks.append("".join(current))
                    sentinel = f"{SENTINEL_PREFIX}{idx}{SENTINEL_SUFFIX}"
                    # The sentinel inherits the opening line's terminator so the skeleton
                    # keeps the document's newline shape.
                    term = line[len(bare):] if line[len(bare):] else "\n"
                    skeleton_parts.append(sentinel + term)
                    in_block = False
                    open_marker = ""
                    current = []

    if in_block:
        # Unclosed fence: close defensively at EOF so we never hang or lose content.
        unclosed = True
        idx = len(blocks)
        blocks.append("".join(current))
        skeleton_parts.append(f"{SENTINEL_PREFIX}{idx}{SENTINEL_SUFFIX}\n")

    return SplitResult("".join(skeleton_parts), blocks, unclosed)


def _check_sentinel_collision(text: str) -> None:
    """Abort if the source already contains our sentinel marker (would corrupt stitch)."""
    if SENTINEL_PREFIX in text:
        raise ValueError(
            f"Source contains the reserved sentinel marker {SENTINEL_PREFIX!r}. "
            "Cannot freeze safely — remove or rename it first."
        )


def freeze_text(text: str) -> tuple[str, dict, bool]:
    """Return (skeleton, blocks_payload, unclosed). Raises on sentinel collision."""
    _check_sentinel_collision(text)
    sr = split_blocks(text)
    payload = {
        "version": 1,
        "sentinel_prefix": SENTINEL_PREFIX,
        "sentinel_suffix": SENTINEL_SUFFIX,
        "blocks": {str(i): b for i, b in enumerate(sr.blocks)},
    }
    return sr.skeleton, payload, sr.unclosed


def stitch_text(skeleton: str, payload: dict) -> str:
    """Reassemble by replacing each sentinel line with its byte-identical block.

    Fails loud (ValueError) if the sidecar is the wrong shape, a referenced block is
    missing, a block value is not a string, or a block is never used — a silent
    mismatch here would defeat the entire point of the tool (LL18).
    """
    if not isinstance(payload, dict):
        raise ValueError(f"blocks sidecar must be a JSON object, got {type(payload).__name__}.")
    blocks = payload.get("blocks", {})
    if not isinstance(blocks, dict):
        raise ValueError(f"blocks['blocks'] must be a JSON object, got {type(blocks).__name__}.")
    used: set[str] = set()
    out_parts: list[str] = []

    for line in _split_lines_keepends(skeleton):
        bare = line.rstrip("\r\n")
        m = _SENTINEL_RE.match(bare)
        if m:
            key = m.group(1)
            if key not in blocks:
                raise ValueError(f"Skeleton references {SENTINEL_PREFIX}{key}{SENTINEL_SUFFIX} but blocks.json has no block {key}.")
            block = blocks[key]
            if not isinstance(block, str):
                raise ValueError(f"Block {key} must be a string, got {type(block).__name__} — blocks.json is corrupt.")
            out_parts.append(block)
            used.add(key)
        else:
            out_parts.append(line)

    unused = set(blocks) - used
    if unused:
        raise ValueError(
            f"{len(unused)} frozen block(s) were never reinserted (missing sentinels): "
            f"{sorted(unused, key=int)}. The translated skeleton dropped a placeholder."
        )
    return "".join(out_parts)


def _count_structure(skeleton: str) -> tuple[int, int]:
    """Count ATX headings and table rows in skeleton prose (sentinels already excluded)."""
    headings = tables = 0
    for line in _split_lines_keepends(skeleton):
        if _SENTINEL_RE.match(line.rstrip("\r\n")):
            continue
        if _ATX_HEADING_RE.match(line):
            headings += 1
        elif _TABLE_ROW_RE.match(line):
            tables += 1
    return headings, tables


def verify_texts(source: str, output: str) -> tuple[bool, list[str]]:
    """Compare structural equivalence of source vs translated output.

    Checks (all must hold): same number of fenced blocks; each block byte-identical;
    same ATX-heading count and table-row count in the prose skeleton. Returns
    (ok, report_lines). Counting headings/tables on the SKELETON (not the raw file) is
    essential — otherwise headings/tables INSIDE ```markdown templates would inflate
    the count and produce false failures.
    """
    src = split_blocks(source)
    out = split_blocks(output)
    report: list[str] = []
    ok = True

    if len(src.blocks) != len(out.blocks):
        ok = False
        report.append(f"FENCE COUNT mismatch: source has {len(src.blocks)} fenced block(s), output has {len(out.blocks)}.")
    else:
        report.append(f"fenced blocks: {len(src.blocks)} (match)")
        for i, (a, b) in enumerate(zip(src.blocks, out.blocks)):
            if a != b:
                ok = False
                report.append(f"BLOCK {i} altered: code fence content differs between source and output.")

    sh, st = _count_structure(src.skeleton)
    oh, ot = _count_structure(out.skeleton)
    if sh != oh:
        ok = False
        report.append(f"HEADING COUNT mismatch: source {sh}, output {oh}.")
    else:
        report.append(f"headings: {sh} (match)")
    if st != ot:
        ok = False
        report.append(f"TABLE ROW mismatch: source {st}, output {ot}.")
    else:
        report.append(f"table rows: {st} (match)")

    return ok, report


# ── CLI ─────────────────────────────────────────────────────────────────────

def _cmd_freeze(args: argparse.Namespace) -> int:
    src_path = Path(args.input)
    text = src_path.read_text(encoding="utf-8")
    try:
        skeleton, payload, unclosed = freeze_text(text)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    stem = src_path.with_suffix("")
    skeleton_path = Path(args.skeleton) if args.skeleton else Path(f"{stem}.skeleton.md")
    blocks_path = Path(args.blocks) if args.blocks else Path(f"{stem}.blocks.json")

    skeleton_path.write_text(skeleton, encoding="utf-8")
    blocks_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if unclosed:
        print("warning: source had an unclosed code fence — closed defensively at EOF.", file=sys.stderr)
    n = len(payload["blocks"])
    print(f"froze {n} block(s) → {skeleton_path} + {blocks_path}")
    print(f"Translate ONLY the prose in {skeleton_path}; leave {SENTINEL_PREFIX}N{SENTINEL_SUFFIX} lines untouched, then run stitch.")
    return 0


def _cmd_stitch(args: argparse.Namespace) -> int:
    skeleton = Path(args.skeleton).read_text(encoding="utf-8")
    try:
        payload = json.loads(Path(args.blocks).read_text(encoding="utf-8"))
        out = stitch_text(skeleton, payload)
    except json.JSONDecodeError as e:
        print(f"error: {args.blocks} is not valid JSON: {e}", file=sys.stderr)
        return 2
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if args.output:
        Path(args.output).write_text(out, encoding="utf-8")
        print(f"stitched → {args.output}")
    else:
        sys.stdout.write(out)
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    source = Path(args.source).read_text(encoding="utf-8")
    output = Path(args.output).read_text(encoding="utf-8")
    ok, report = verify_texts(source, output)
    for line in report:
        print(line)
    if ok:
        print("VERIFY: PASS — structurally equivalent.")
        return 0
    print("VERIFY: FAIL — see discrepancies above.", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="md_freeze.py",
        description="Freeze-Translate-Stitch for safe Markdown translation.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_freeze = sub.add_parser("freeze", help="Extract fenced blocks into a sidecar; emit a prose skeleton.")
    p_freeze.add_argument("input", help="Source markdown file.")
    p_freeze.add_argument("--skeleton", help="Skeleton output path (default <stem>.skeleton.md).")
    p_freeze.add_argument("--blocks", help="Blocks JSON output path (default <stem>.blocks.json).")
    p_freeze.set_defaults(func=_cmd_freeze)

    p_stitch = sub.add_parser("stitch", help="Reinsert frozen blocks into a translated skeleton.")
    p_stitch.add_argument("skeleton", help="Translated skeleton file.")
    p_stitch.add_argument("blocks", help="Blocks JSON sidecar from freeze.")
    p_stitch.add_argument("-o", "--output", help="Output path (default: stdout).")
    p_stitch.set_defaults(func=_cmd_stitch)

    p_verify = sub.add_parser("verify", help="Check structural equivalence of source vs output.")
    p_verify.add_argument("source", help="Original source markdown.")
    p_verify.add_argument("output", help="Translated/stitched output markdown.")
    p_verify.set_defaults(func=_cmd_verify)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
