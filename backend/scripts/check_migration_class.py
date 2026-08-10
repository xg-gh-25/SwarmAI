"""Goal-run class-completeness gate — the deterministic core.

Design: Projects/SwarmAI/2-understanding/knowledge/designs/2026-08-10-goal-run-class-completeness-gate-design.md
run_1d3df9e6.

WHY: a goal run migrates a CLASS (all callers of a chokepoint / all siblings of a type)
across many cycles. Every diff-scoped adversarial only sees TOUCHED code, so a class
member no cycle touched is invisible — that is how the `decisions` write path shipped
ungated in run_0d60e04e. This gate enumerates the FULL class at goal completion and
blocks any member that is neither migrated nor explicitly carved-out.

ANTI-CIRCULARITY (Gate-2 CRITICAL, R-A): a self-authored member list inherits the
migration's blind spot. So enumeration MUST grep a PHYSICAL SINK (the last-mile write
call every member is forced through — e.g. `_run_locked_write` / `apply_to_ddd`) across
the tree; the sink's caller set is the class BY CONSTRUCTION, independent of the author's
mental model. `validate_enumeration_cmd` rejects a member-list / echo / curated-file
subset that would re-import the blind spot.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


# Heuristics for a chokepoint-shaped enumeration_cmd (R-A / AC10).
_ECHO_LIKE = re.compile(r"\b(echo|printf|cat\s+<<|yes)\b")
_GREP_LIKE = re.compile(r"\b(grep|rg|ag)\b")
_RECURSIVE = re.compile(r"\b(grep|rg|ag)\b[^|]*\s-[a-zA-Z]*r")
_TRUNCATED = re.compile(r"\|\s*(head|tail)\b")
# The grep's directory target. It MUST be a tree ROOT (`.`, `backend/`, or a top-level
# package dir), NOT a deep subdir (Gate-2 #4: `grep -r backend/hooks/` is recursive but
# omits siblings in backend/core/ → re-imports the blind spot). A "deep" target has ≥2
# path segments (`backend/hooks/`); a root target has ≤1 (`backend/`, `.`).
_GREP_DIR_TARGET = re.compile(r"\b(?:grep|rg|ag)\b[^|]*\s(?:-[a-zA-Z]+\s+)*([./A-Za-z0-9_\-]+/?)(?:\s|\||$)")


def validate_enumeration_cmd(cmd: str) -> "tuple[bool, str]":
    """R-A/AC10: accept only a chokepoint-shaped enumeration (a recursive sink grep across
    the tree ROOT). Reject (all re-import the author's blind spot):
      - echo/printf literal member lists,
      - non-grep commands,
      - non-recursive greps or single-file targets (a sibling file is invisible),
      - greps scoped to a DEEP subdir (Gate-2 #4: omits sibling subtrees),
      - truncated output (| head / | tail — silently drops members).
    Returns (ok, reason). ok=True → chokepoint-shaped.
    """
    c = (cmd or "").strip()
    if not c:
        return (False, "empty enumeration_cmd")
    if _ECHO_LIKE.search(c):
        return (False, "echo/printf-style literal member list — re-imports the author's "
                        "blind spot; grep a physical sink across the tree instead")
    if not _GREP_LIKE.search(c):
        return (False, "not a grep/rg/ag over source — must enumerate a physical sink")
    if _TRUNCATED.search(c):
        return (False, "| head/tail truncates the member set — silently drops class members")
    if not _RECURSIVE.search(c):
        return (False, "non-recursive grep — a sibling in another file is invisible; "
                        "use `grep -r` across the tree root")
    # scope check: a RELATIVE grep target must be a tree ROOT, not a deep subdir (Gate-2 #4:
    # `grep -r backend/hooks/` omits backend/core/). Only applies to relative targets — an
    # absolute path is scored by its trailing depth-from-a-recognizable-root, which we can't
    # know, so we only guard the real-pipeline shape (repo-relative `pkg/sub/`).
    m = _GREP_DIR_TARGET.search(c)
    if m:
        target = m.group(1).strip()
        if not target.startswith("/"):                 # relative only
            norm = target.strip("/")
            depth = 0 if norm in (".", "") else norm.count("/") + 1
            if depth >= 2:
                return (False, f"grep scoped to a deep subdir ('{m.group(1)}') — a sibling in a "
                                f"peer subtree is invisible; scope to the tree root (e.g. 'backend/')")
    return (True, "chokepoint-shaped")


@dataclass
class CompletenessResult:
    passed: bool
    blocked: list = field(default_factory=list)   # [{kind, member, detail}]
    coverage_table: str = ""
    noop: bool = False


_VALID_DISPOSITIONS = frozenset({"migrated", "carved-out"})


def _run_enumeration(cmd: str, cwd: Path) -> list[str]:
    """Run the enumeration_cmd; return the list of live member lines (file:line:match)."""
    proc = subprocess.run(
        cmd, shell=True, cwd=str(cwd), capture_output=True, text=True, timeout=60,
    )
    # grep exit 1 = no matches (not an error); >1 = real error.
    if proc.returncode not in (0, 1):
        raise RuntimeError(f"enumeration_cmd failed (exit {proc.returncode}): {proc.stderr[:200]}")
    return [ln for ln in proc.stdout.splitlines() if ln.strip()]


def _valid_locator(locator: str) -> bool:
    """A locator MUST be `<relative/path>:<line>` — a real path (≥1 slash OR a .py file)
    AND a line number. Gate-2 #2: a bare basename with no line is a wildcard that absorbs
    every sink call in the file; reject it at validation so one member can't hide N siblings."""
    loc = (locator or "").strip()
    if not loc:
        return False
    parts = loc.split(":")
    return len(parts) >= 2 and parts[1].strip().isdigit() and parts[0].strip().endswith(".py")


def _locator_matches_line(locator: str, live_line: str) -> bool:
    """A member's `locator` (`relative/path.py:line` of its SINK CALL) matches a live grep
    line. Distinct from `evidence` (the disposition proof). Gate-2 #1: match on the FULL
    relative path + EXACT line, NOT the basename — else `distill.py:4` false-matches a
    different-directory `core/distill.py:4` and an undeclared sibling hides under it.

    grep -rn output shape: `<path>:<line>:<text>`. We require the locator's full path to
    be a suffix of the live line's path field AND the line numbers to be equal."""
    if not _valid_locator(locator):
        return False
    loc_path, loc_line = locator.strip().split(":")[0], locator.strip().split(":")[1]
    # live grep line: split into at most 3 → path, line, rest
    lparts = live_line.split(":", 2)
    if len(lparts) < 2 or not lparts[1].strip().isdigit():
        return False
    live_path, live_line_no = lparts[0].strip(), lparts[1].strip()
    if live_line_no != loc_line:
        return False
    # full-path suffix match (locator is repo-relative; live_path may be cwd-relative)
    lp = live_path.replace("\\", "/")
    return lp == loc_path or lp.endswith("/" + loc_path)


def check_migration_class(migration_class: "dict | None", cwd: Path) -> CompletenessResult:
    """Enumerate the class from LIVE source and reconcile against declared members[].

    Blocks (any → passed=False) with kinds:
      MISSED               — a live sink caller with no declared member (the run_0d60e04e catch)
      BAD_ENUMERATION      — enumeration_cmd is not chokepoint-shaped (R-A/AC10)
      UNJUSTIFIED_CARVEOUT — a carved-out member with no reason string (AC5)
      BAD_DISPOSITION      — a member whose disposition is neither migrated nor carved-out
    Absent migration_class → no-op PASS (AC2 core; keyword-mandatory handling is evaluate.md).
    """
    if not migration_class:
        return CompletenessResult(passed=True, noop=True,
                                  coverage_table="(no migration_class declared — gate is a no-op)")

    cmd = migration_class.get("enumeration_cmd", "")
    members = migration_class.get("members", []) or []
    desc = migration_class.get("description", "(unnamed class)")
    blocked: list = []

    ok, reason = validate_enumeration_cmd(cmd)
    if not ok:
        blocked.append({"kind": "BAD_ENUMERATION", "member": "(enumeration_cmd)", "detail": reason})
        table = f"CLASS: {desc}\n  ❌ BAD_ENUMERATION — {reason}"
        return CompletenessResult(passed=False, blocked=blocked, coverage_table=table)

    # Every declared member MUST carry a valid `path.py:line` locator (Gate-2 #2): a
    # bare-basename / no-line locator is a wildcard that lets one member absorb siblings.
    for m in members:
        if not _valid_locator(m.get("locator", "")):
            blocked.append({"kind": "BAD_LOCATOR", "member": m.get("id", "?"),
                            "detail": f"locator '{m.get('locator','')}' is not a valid "
                                      f"'relative/path.py:line' — a wildcard locator hides siblings"})
    if blocked:
        table = f"CLASS: {desc}\n" + "\n".join(f"  ❌ {b['member']}  {b['kind']}" for b in blocked)
        return CompletenessResult(passed=False, blocked=blocked, coverage_table=table)

    live = _run_enumeration(cmd, cwd)

    # Gate-2 #3 (fail-open fix): a non-empty declared class whose enumeration returns ZERO
    # live members means the enumeration_cmd is broken (typo'd sink, wrong cwd) — the exact
    # blind spot the gate exists to catch. BLOCK, never silently PASS on an empty result.
    if members and not live:
        blocked.append({"kind": "EMPTY_ENUMERATION", "member": "(enumeration_cmd)",
                        "detail": "enumeration returned 0 live members but the class declares "
                                  f"{len(members)} — the grep found nothing (typo'd sink / wrong cwd). "
                                  "Cannot verify completeness against an empty enumeration."})
        table = f"CLASS: {desc}\n  ❌ EMPTY_ENUMERATION — grep matched 0 lines for a {len(members)}-member class"
        return CompletenessResult(passed=False, blocked=blocked, coverage_table=table)

    # Reconcile every LIVE member against a declared row (the completeness direction).
    rows: list[str] = []
    for live_line in live:
        matched = next((m for m in members if _locator_matches_line(m.get("locator", ""), live_line)), None)
        if matched is None:
            blocked.append({"kind": "MISSED", "member": live_line.strip(),
                            "detail": "live sink caller with no declared member (class sibling on the old path)"})
            rows.append(f"  ❌ {live_line.strip()[:70]}  UNDECLARED — sibling on old path")
        else:
            disp = str(matched.get("disposition", "")).strip()
            mid = matched.get("id", "?")
            if disp == "migrated":
                rows.append(f"  ✅ {mid:28} migrated    {matched.get('evidence','')[:40]}")
            elif disp == "carved-out":
                if not str(matched.get("evidence", "")).strip():
                    blocked.append({"kind": "UNJUSTIFIED_CARVEOUT", "member": mid,
                                    "detail": "carved-out with no reason — state why it's not in-class"})
                    rows.append(f"  ❌ {mid:28} carved-out  NO REASON")
                else:
                    rows.append(f"  ⚪ {mid:28} carved-out  {matched.get('evidence','')[:40]}")
            else:
                blocked.append({"kind": "BAD_DISPOSITION", "member": mid,
                                "detail": f"disposition '{disp}' not in {{migrated, carved-out}}"})
                rows.append(f"  ❌ {mid:28} BAD_DISPOSITION '{disp}'")

    verdict = "PASS" if not blocked else f"BLOCK ({len(blocked)} issue(s))"
    table = (f"CLASS: {desc}  ({len(live)} live member(s))\n"
             + "\n".join(rows) + f"\n  → {verdict}")
    return CompletenessResult(passed=(not blocked), blocked=blocked, coverage_table=table)


def to_delivery_finding(res: CompletenessResult) -> "dict | None":
    """AC6 teeth: convert a BLOCK into a DELIVER adversarial_review finding so the
    existing _blocked_findings HIGH gate blocks COMPLETE. None when passed/no-op."""
    if res.passed or res.noop:
        return None
    detail = "; ".join(f"{b['kind']}:{b['member']}" for b in res.blocked)
    return {
        "id": "class_completeness",
        "severity": "HIGH",
        "confidence": 9,
        "title": "Goal-run class-completeness gate blocked",
        "detail": f"Un-dispositioned class member(s): {detail}",
        "resolved": False,
    }
