"""Design-lint PreToolUse guard — advisory CSS anti-pattern nudge for frontend writes.

WHAT: a sibling of ``security_hooks.inclusive_term_guard`` for a DIFFERENT domain.
When the agent is about to Write/Edit a FRONTEND STYLE file (``.tsx/.jsx/.css/.scss``
under ``desktop/src`` or ``hive``), this hook scans the written text for a small set
of HIGH-CONFIDENCE CSS design anti-patterns drawn from the design-judgment canon
(``backend/skills/s_frontend-design/data/design-judgment.md``) and, on a hit, appends
an ``additionalContext`` nudge naming the rule + its canonical check + the fix.

WHY: SELF.md records UI "data-dump / dated-anti-pattern" as the agent's #1 known
judgment blind spot — a prose checklist (AGENT R15) did not hold (SOUL P7: when prose
fails, build a gate). This is that gate, deliberately scoped to what a deterministic
regex can judge with ~zero false positives, because an advisory hook with low precision
is pure attention-fatigue noise (the inverted-SNR failure that killed a prior
comment-drift detector — Gate-1 flagged the same risk here, so the noisy rules
`font-overused` and the `#fff`-background half of `pure-black` were DROPPED).

DESIGN CONTRACT (mirrors inclusive_term_guard exactly):
- ALWAYS returns ``{"decision": "approve"}`` — never deny/block. Style is not security
  (STEERING #2); a design nudge must never truncate real work (O030).
- FAIL-OPEN by construction: a self-contained try/except guarantees any scan error
  (or non-target tool / oversized / malformed input) returns a bare approve — a scan
  bug can never crash the write path (this hook runs solo, outside _build_chain's
  try/except, same as inclusive_term_guard).
- SCOPE-GATED FIRST: only frontend style files under desktop/src|hive are scanned;
  every other write returns a bare approve at zero cost (no false-positive surface on
  backend .py / markdown / config).

SCOPE (v1, A-class only): CSS/style-level deterministic regex rules. Layout/structure
heuristics (equal-weight tile grids, count-header-over-list) are DEFERRED — they need
JSX/AST analysis and carry real false-positive risk. There is NO block tier.

KEY SYMBOLS:
- ``design_lint_guard`` — the async PreToolUse hook (registered in hook_builder.py).
- ``_scan_design_rules`` — pure scanner, returns the list of matched rule ids.
- ``_in_scope`` — the file-path gate (suffix + path-marker).
- ``_DESIGN_RULES`` — the rule table (id, pattern, canonical check ref, fix hint).
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

# Reuse the single source of truth for "what text is a Write/Edit/MultiEdit adding".
# security_hooks does NOT import design_hooks → no import cycle.
from core.security_hooks import _extract_written_text

logger = logging.getLogger(__name__)

# Above this char count, skip the scan (fail-safe — same rationale as the inclusive guard).
_DESIGN_SCAN_MAX_CHARS = 1_000_000

# Scope gate: only FRONTEND STYLE files, and only under a frontend source tree.
_TARGET_SUFFIXES = (".tsx", ".jsx", ".css", ".scss")
# realpath must contain one of these path segments (normalized to forward slash).
_TARGET_PATH_MARKERS = ("/desktop/src/", "/hive/")


# ── Rule table ────────────────────────────────────────────────────────────
# Each rule: (id, compiled regex, canonical design-judgment reference, fix hint).
# Deliberately HIGH-CONFIDENCE only — measured ~0 false positives on the live
# desktop/src tree (Gate-1 inverted-SNR guard). Patterns are intentionally narrow;
# a miss (false negative) is acceptable, a false positive is not (advisory noise
# erodes trust in the nudge — the whole mechanism dies if it cries wolf).

# pure-black TEXT only: `color: #000 / #000000 / black`. Does NOT touch backgrounds
# or #fff (both commonly legitimate → measured 12 #fff hits on live tree, all valid).
# The negative-lookbehind `(?<![-\w])` anchors the property name to `color`, so the
# `color:` TAIL of `background-color` / `border-color` / `outline-color` / `caret-color`
# (and their JSX camelCase siblings `backgroundColor` / `borderColor` / …) does NOT
# false-positive (Gate-2 correctness finding: an unanchored `color:` matched every
# `*-color: #000` property, violating the "does not touch backgrounds" contract).
# The optional quote-char `['"\x60]?` directly after the `\s*:\s*` makes the rule match
# BOTH CSS declaration syntax (`color: #000`) AND JSX inline-object syntax where the value
# is a quoted string (`style={{color: '#000'}}` / `"#000"` / `'black'`) — the .tsx inline-
# style form that the CSS-only pattern silently missed. The quote char has NO `\s*` after
# it (a value never has whitespace between its opening quote and the literal) — this is
# deliberate: an adjacent `\s*['"]?\s*` would create a catastrophic-backtracking surface
# (measured 395ms on a 5000-space line), the Gate-2 ReDoS class the easing rule caps with
# {1,12}. Only ONE optional quote char is allowed and the LITERAL stays #000/#000000/black,
# so a quoted NON-black color (`color: '#333'`) still does NOT fire (inverted-SNR guard).
_RE_PURE_BLACK_TEXT = re.compile(
    r"(?<![-\w])color\s*:\s*['\"\x60]?(?:#000(?:000)?\b|black\b)", re.IGNORECASE
)
# bounce / overshoot easing: explicit keywords, OR a cubic-bezier whose Y control
# point overshoots [0,1] (true spring/elastic). A plain cubic-bezier in-range is NOT
# matched (that is normal easing, not the dated-bounce anti-pattern).
# Digit runs are capped at {1,12} (not unbounded `+`) so a crafted degenerate
# cubic-bezier input cannot superlinear-backtrack (Gate-2 operational finding: the
# only unbounded-work path; capped since a real CSS number is never 12+ chars).
_RE_BOUNCE_EASING = re.compile(
    r"ease-elastic\b|\bbounce\b"
    r"|cubic-bezier\(\s*[\d.]{1,12}\s*,\s*-[\d.]{1,12}\s*,\s*[\d.]{1,12}\s*,\s*[\d.]{1,12}\s*\)"  # 2nd ctrl y<0
    r"|cubic-bezier\(\s*[\d.]{1,12}\s*,\s*[\d.]{1,12}\s*,\s*[\d.]{1,12}\s*,\s*(?:1\.\d{0,10}[1-9]|[2-9]\d{0,10}(?:\.\d{1,10})?)\s*\)",  # 4th ctrl y>1
    re.IGNORECASE,
)
# gray text on a colored background: a tailwind `text-gray-{400,500} ... bg-{color}-{n}`
# adjacency on one element. High-confidence form only (same-element class string); the
# `bg-` is negative-lookahead-exempted for neutral/white/black/transparent surfaces so a
# gray-on-neutral (legitimate) never fires. Measured 0 FP on the live desktop/src tree.
_RE_GRAY_ON_COLOR = re.compile(
    r"text-(?:gray|slate|zinc|neutral)-(?:400|500)\b[^\"'>]{0,80}?\bbg-(?!(?:gray|slate|zinc|neutral|white|black|transparent))\w+-\d{2,3}\b",
    re.IGNORECASE,
)
# NOTE: card-in-card and border-wall were CONSIDERED and DROPPED for v1. A flat regex
# cannot reliably judge DOM NESTING (card-in-card needs JSX/AST), and a border-count
# threshold false-positives on legitimately border-dense files (measured). Both are
# B-class structural checks — deferred with the rest of the layout heuristics rather
# than shipped as a low-precision regex that would re-create the inverted-SNR noise this
# hook exists to avoid. v1 ships ONLY the three measured-0-FP CSS rules below.

# term/id → (regex, canonical check ref in design-judgment.md, fix hint)
_DESIGN_RULES: list[tuple[str, re.Pattern[str], str, str]] = [
    (
        "pure-black-text",
        _RE_PURE_BLACK_TEXT,
        "Part 5 (soften contrast; headings #333, body #555 — color AS hierarchy)",
        "tint it: use a near-black with a hue (e.g. #1a1a2e / text-slate-800), never pure #000/black",
    ),
    (
        "bounce-easing",
        _RE_BOUNCE_EASING,
        "Cross-surface Motion (durations 120–250ms, motion = causality not flourish)",
        "use ease-out / a monotonic cubic-bezier in [0,1]; bounce/elastic easing reads dated",
    ),
    (
        "gray-on-color",
        _RE_GRAY_ON_COLOR,
        "Part 2 #2 + Part 3 (contrast carries hierarchy; low-contrast gray-on-color fails legibility)",
        "put gray text on a neutral surface, or raise the text weight/contrast on the colored one",
    ),
]


def _scan_design_rules(text: str) -> list[tuple[str, str, str]]:
    """Return ``[(rule_id, canonical_ref, fix)]`` for every rule matching ``text``.

    Pure; never raises on str input. Order follows ``_DESIGN_RULES``.
    """
    hits: list[tuple[str, str, str]] = []
    for rule_id, pattern, canon_ref, fix in _DESIGN_RULES:
        if pattern.search(text):
            hits.append((rule_id, canon_ref, fix))
    return hits


def _in_scope(file_path: str) -> bool:
    """True iff ``file_path`` is a frontend STYLE file under a frontend source tree.

    Suffix must be one of _TARGET_SUFFIXES AND the realpath must contain a frontend
    path marker. Never raises — a bad path returns False (fail-safe: out of scope).
    """
    try:
        if not file_path:
            return False
        if not file_path.lower().endswith(_TARGET_SUFFIXES):
            return False
        # realpath normalizes symlinks/.. ; forward-slash for cross-platform marker match.
        norm = os.path.realpath(file_path).replace(os.sep, "/")
        # Guard against a path whose realpath lost the marker (e.g. relative) by also
        # checking the raw path.
        raw = file_path.replace(os.sep, "/")
        return any(m in norm or m in raw for m in _TARGET_PATH_MARKERS)
    except Exception:  # noqa: BLE001 — scope check must never raise
        return False


async def design_lint_guard(
    input_data: dict[str, Any],
    tool_use_id: str | None,
    context: Any,
) -> dict[str, Any]:
    """PreToolUse (Write|Edit|MultiEdit): WARN on CSS design anti-patterns.

    ALWAYS approves — advisory nudge, never a block (STEERING #2: style is not
    security; O030: never truncate real work). When a frontend style write contains
    a high-confidence CSS anti-pattern, an ``additionalContext`` note lists the rule +
    its canonical design-judgment check + the fix, so the agent can self-correct
    BEFORE the pattern lands.

    Fail-safe by construction: a non-target tool, an out-of-scope path, empty/oversized
    content, or a malformed ``tool_input`` returns a bare ``{"decision": "approve"}`` —
    a self-contained try/except guarantees a scan error can never crash the write path
    (this hook runs solo, outside _build_chain's try/except, exactly like
    inclusive_term_guard).
    """
    try:
        tool_name = input_data.get("tool_name", "")
        if tool_name not in ("Write", "Edit", "MultiEdit"):
            return {"decision": "approve"}
        tool_input = input_data.get("tool_input") or {}
        if not isinstance(tool_input, dict):
            return {"decision": "approve"}

        # Scope gate FIRST — out-of-scope files cost nothing and get zero false positives.
        file_path = str(tool_input.get("file_path") or "")
        if not _in_scope(file_path):
            return {"decision": "approve"}

        text = _extract_written_text(tool_name, tool_input)
        if not text or len(text) > _DESIGN_SCAN_MAX_CHARS:
            return {"decision": "approve"}

        hits = _scan_design_rules(text)
        if not hits:
            return {"decision": "approve"}

        lines = "\n".join(
            f"  • {rule_id} — {canon_ref}\n    fix: {fix}"
            for rule_id, canon_ref, fix in hits
        )
        reminder = (
            "🎨 DESIGN-LINT NUDGE (advisory — not a block): this frontend write "
            "contains a CSS pattern that the design-judgment canon "
            "(s_frontend-design/data/design-judgment.md) flags as a dated anti-pattern:\n"
            f"{lines}\n"
            "Consider the fix before shipping. If the pattern is deliberate / load-bearing "
            "(a brand token, a quoted third-party style), leave it — this is a nudge, not a rule."
        )
        return {"decision": "approve", "additionalContext": reminder}
    except Exception:  # noqa: BLE001 — a WARN nudge must NEVER crash the write path
        logger.exception("design_lint_guard raised — failing open (approve)")
        return {"decision": "approve"}
