#!/usr/bin/env python3
"""SwarmAI repo's own wording and frontend-style lint (moved out of the product, P7).

Until 2026-10-24 these two checks were product PreToolUse hooks (``inclusive_term_guard``
in security_hooks.py, ``design_lint_guard`` in core/design_hooks.py) that nudged EVERY
user's agent about Amazon's InclusiveTechScanner terms and SwarmAI's own CSS taste rules.
Neither is a product rule (design docs/design/2026-10-24-swarmai-usable-by-anyone.md P7:
the SwarmAI repo's discipline lives in its own scripts, pre-commit and CI), so they live here.

Scans only ADDED lines (a diff), so legacy text never fails a change:
    python scripts/lint_terms.py --staged          # pre-commit: what you are about to commit
    python scripts/lint_terms.py --diff <base>     # CI: <base>..HEAD
    python scripts/lint_terms.py FILE...           # whole files
Exit 1 when anything is found (``--warn`` prints and exits 0). The rules and their
false-positive exemptions are unchanged from the product hooks.

- ``scan_inclusive_terms(text)`` -> list of term ids
- ``scan_design_rules(path, text)`` -> list of rule ids (frontend style files only)
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys

# ── inclusive terms (InclusiveTechScanner's set: whitelist, blacklist, master/slave, ...) ──
# whitelist/blacklist: no left-\b (catch _WHITELIST, getWhitelist); \w* stem (whitelisted);
# the lookahead exempts the hypothesis API params whitelist_categories / whitelist_characters.
_RE_WHITELIST = re.compile(r"whitelist(?!_(?:categories|characters))\w*", re.IGNORECASE)
_RE_BLACKLIST = re.compile(r"blacklist\w*", re.IGNORECASE)
# master only adjacent to slave (git master / master copy are legitimate); standalone slave
# with a left word boundary (enslave is not flagged).
_RE_MASTER_SLAVE = re.compile(r"master[\s/_-]{0,3}slave|slave[\s/_-]{0,3}master", re.IGNORECASE)
_RE_SLAVE = re.compile(r"\bslave\w*", re.IGNORECASE)
_RE_WHITEDAY = re.compile(r"white[\s_-]?day\w*", re.IGNORECASE)
_RE_BLACKDAY = re.compile(r"black[\s_-]?day\w*", re.IGNORECASE)

INCLUSIVE_ALTERNATIVES = {
    "whitelist": "allowlist / allowed list",
    "blacklist": "denylist / blocked list",
    "master/slave": "primary/replica, leader/follower",
    "slave": "replica / follower / worker",
    "whiteday": "an inclusive term",
    "blackday": "an inclusive term",
}


def scan_inclusive_terms(text: str) -> list[str]:
    """Term ids found in ``text``. A text naming BOTH whitelist and blacklist is describing
    the contrast (a design decision), so neither is flagged."""
    found: list[str] = []
    has_white = bool(_RE_WHITELIST.search(text))
    has_black = bool(_RE_BLACKLIST.search(text))
    if has_white and not has_black:
        found.append("whitelist")
    if has_black and not has_white:
        found.append("blacklist")
    if _RE_MASTER_SLAVE.search(text):
        found.append("master/slave")
    elif _RE_SLAVE.search(text):
        found.append("slave")
    if _RE_WHITEDAY.search(text):
        found.append("whiteday")
    if _RE_BLACKDAY.search(text):
        found.append("blackday")
    return found


# ── frontend style rules (design-judgment canon; measured ~0 false positives) ──
_STYLE_SUFFIXES = (".tsx", ".jsx", ".css", ".scss")
_STYLE_DIRS = ("desktop/src/", "hive/")
# pure-black TEXT only (`color: #000|#000000|black`, CSS or a quoted JSX value). The
# lookbehind keeps background-color / border-color / backgroundColor out; one optional
# quote char and no \s after it (an extra \s* there backtracked catastrophically).
_RE_PURE_BLACK_TEXT = re.compile(
    r"(?<![-\w])color\s*:\s*['\"\x60]?(?:#000(?:000)?\b|black\b)", re.IGNORECASE)
# bounce / overshoot easing; digit runs capped at {1,12} so a crafted input cannot backtrack.
_RE_BOUNCE_EASING = re.compile(
    r"ease-elastic\b|\bbounce\b"
    r"|cubic-bezier\(\s*[\d.]{1,12}\s*,\s*-[\d.]{1,12}\s*,\s*[\d.]{1,12}\s*,\s*[\d.]{1,12}\s*\)"
    r"|cubic-bezier\(\s*[\d.]{1,12}\s*,\s*[\d.]{1,12}\s*,\s*[\d.]{1,12}\s*,\s*(?:1\.\d{0,10}[1-9]|[2-9]\d{0,10}(?:\.\d{1,10})?)\s*\)",
    re.IGNORECASE)
# tailwind gray text on a coloured (non-neutral) background on one element.
_RE_GRAY_ON_COLOR = re.compile(
    r"text-(?:gray|slate|zinc|neutral)-(?:400|500)\b[^\"'>]{0,80}?"
    r"\bbg-(?!(?:gray|slate|zinc|neutral|white|black|transparent))\w+-\d{2,3}\b",
    re.IGNORECASE)

DESIGN_RULES = {
    "pure-black-text": (_RE_PURE_BLACK_TEXT,
                        "tint it: a near-black with a hue (e.g. text-slate-800), never #000/black"),
    "bounce-easing": (_RE_BOUNCE_EASING,
                      "use ease-out / a monotonic cubic-bezier in [0,1]"),
    "gray-on-color": (_RE_GRAY_ON_COLOR,
                      "gray text on a neutral surface, or raise its contrast on the coloured one"),
}


def is_style_file(path: str) -> bool:
    p = path.replace("\\", "/")
    return p.lower().endswith(_STYLE_SUFFIXES) and any(
        p.startswith(d) or f"/{d}" in p for d in _STYLE_DIRS)


def scan_design_rules(path: str, text: str) -> list[str]:
    if not is_style_file(path):
        return []
    return [rid for rid, (rx, _fix) in DESIGN_RULES.items() if rx.search(text)]


# ── inputs ──
_SELF = "scripts/lint_terms.py"


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def _added_text(diff: str) -> dict[str, str]:
    """{path: added lines joined} from a unified diff."""
    out: dict[str, list[str]] = {}
    path = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            target = line[4:].strip()
            path = target[2:] if target.startswith("b/") else None
        elif path and line.startswith("+") and not line.startswith("+++"):
            out.setdefault(path, []).append(line[1:])
    return {p: "\n".join(lines) for p, lines in out.items()}


def collect(args: argparse.Namespace) -> dict[str, str]:
    if args.staged:
        return _added_text(_git("diff", "--cached", "-U0", "--no-color"))
    if args.diff:   # BASE vs HEAD; needs only those two commits (CI clones shallow)
        return _added_text(_git("diff", "-U0", "--no-color", args.diff, "HEAD"))
    texts = {}
    for f in args.files:
        with open(f, encoding="utf-8", errors="replace") as fh:
            texts[f] = fh.read()
    return texts


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--staged", action="store_true")
    src.add_argument("--diff", metavar="BASE")
    src.add_argument("files", nargs="*", default=[])
    ap.add_argument("--warn", action="store_true", help="report but exit 0")
    args = ap.parse_args(argv)
    problems = []
    for path, text in sorted(collect(args).items()):
        if path.replace("\\", "/").endswith(_SELF):
            continue   # this file has to spell the terms it looks for
        for term in scan_inclusive_terms(text):
            problems.append(f"{path}: '{term}' -> prefer {INCLUSIVE_ALTERNATIVES[term]}")
        for rid in scan_design_rules(path, text):
            problems.append(f"{path}: {rid} -> {DESIGN_RULES[rid][1]}")
    for p in problems:
        print(p)
    if problems and not args.warn:
        print(f"lint_terms: {len(problems)} finding(s). A load-bearing term (an external API "
              "name, a quoted spec) may stay: re-run with --warn.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
