"""Which experts a native review would call for a change: ``s_expert-review`` §1, as rules.

Design: docs/design/2026-11-18-expert-review-benchmark.md §4 (the expert arm). §1 of the
skill is a judgment the agent makes from the diff; the benchmark needs the same answer
every time, so each row of its table is written here as a test on the diff: what the
change touches (paths, the changed lines of source files) and how much code it changes.
The roster and the reason for each expert are recorded with the case, so a reader can
check and correct them. ``RULES`` names the rule set; change it when a rule changes.

Only source files count (``core.review_order.role``): a change to tests or docs alone
calls nobody, as the skill's last row says. Security follows the repository's own
``security:`` line in its ``.swarmai/review.md`` when it has one (the skill: "a
repository's own guidance can ask for more"; the product carries no built-in list of
sensitive paths for it), and a line that does not parse calls security, as in a
pipeline run. Without one, a short list of the APIs the skill's row names is used.

Public: ``RULES``, ``decide``, ``for_case``, ``catalog``, ``CATALOG``.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Mapping, Optional

from core.review_order import role
from core.run_protocol import review_md as _guide

RULES = "skill-s1-2026-11-18"
CATALOG = Path(__file__).resolve().parents[2] / "skills" / "s_expert-review" / "CATALOG.md"

# "more than a few lines of real code": changed source lines that are not blank and not
# only a comment
MIN_CODE_LINES = 5
_COMMENT = re.compile(r"^\s*(#|//|/\*|\*|--|<!--)")
_UI = re.compile(r"\.(tsx|jsx|vue|svelte|css|scss|less|html)$", re.I)

# One pattern per row of the skill's table (case-insensitive, over the changed source
# lines). The first match is recorded as the reason.
_ROWS: list[tuple[str, str]] = [
    ("security", r"subprocess|shell\s*=\s*true|os\.system|popen|\beval\(|\bexec\(|pickle|"
                 r"yaml\.load\(|password|secret|credential|api[_-]?key|\bauth|permission|chmod|"
                 r"requests\.|httpx|urllib|socket|https?://|realpath|abspath|\.\./|sanitiz"),
    ("concurrency", r"\basync\b|\bawait\b|thread|\block\b|lock\(|_lock\b|mutex|semaphore|asyncio|"
                    r"concurrent|\bqueue\b|atomic|goroutine|\bchan\b|promise\.all"),
    ("state-machine", r"transition|lifecycle|state[_ -]?machine|\bstates?\s*(=|==|!=)|"
                      r"[._]state\s*(=|==|!=)|\bstatus\s*(==|!=)"),
    ("api-contract", r"schema|basemodel|typeddict|@dataclass|@(app|router)\.(get|post|put|delete|"
                     r"patch)|openapi|graphql|\.proto\b"),
    ("performance", r"\bcache|lru_cache|memoiz|\bbatch|executemany|\bsql\b|\bselect\b.+\bfrom\b|"
                    r"pagina|\bchunk|latency|hot path"),
    ("operational", r"timeout|retry|retries|backoff|schedul|\bcron\b|daemon|systemd|launchd|"
                    r"background|watchdog|recover|signal\.|atexit"),
]
# a public definition removed or rewritten: an interface other code may call
_PUBLIC_DEF = re.compile(r"^\s*(?:async\s+def|def|class|func|pub\s+fn)\s+[A-Za-z]|"
                         r"^\s*export\s+(?:default\s+)?(?:async\s+)?(?:function|class|const|interface|"
                         r"type)\b|^\s*public\s+\w")
_PRIVATE_DEF = re.compile(r"^\s*(async\s+def|def|class)\s+_")


def _files(diff: str) -> list[tuple[str, list[str], list[str]]]:
    """(path, added lines, removed lines) per file of a unified diff."""
    out: list[tuple[str, list[str], list[str]]] = []
    for block in re.split(r"(?m)^diff --git ", diff):
        if not block.strip():
            continue
        m = re.search(r"(?m)^\+\+\+ (?:b/)?(.+)$", block)
        path = m.group(1).strip() if m and m.group(1).strip() != "/dev/null" else ""
        if not path:
            m = re.search(r"(?m)^--- (?:a/)?(.+)$", block)
            path = m.group(1).strip() if m else block.split()[0][2:]
        added, removed = [], []
        for line in block.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                added.append(line[1:])
            elif line.startswith("-") and not line.startswith("---"):
                removed.append(line[1:])
        out.append((path, added, removed))
    return out


def decide(diff: str, review_md: Optional[str] = None) -> dict:
    """``{"roster": [...], "why": {expert: reason}, "rules": RULES}`` for the change in
    *diff* (a unified diff), with the repository's ``.swarmai/review.md`` text if any."""
    files = _files(diff)
    sources = [(p, a, r) for p, a, r in files if role(p) == "source"]
    why: dict[str, str] = {}
    changed = [(p, line) for p, a, r in sources for line in a + r]
    code = [(p, line) for p, line in changed if line.strip() and not _COMMENT.match(line)]
    if not code:
        return {"roster": [], "why": {}, "rules": RULES}
    if len(code) >= MIN_CODE_LINES:
        why["correctness"] = f"{len(code)} changed code lines"
    security_row = None
    guide_error = None
    if review_md:
        try:
            security_row = _guide.security_line(review_md)
        except _guide.GuideError as e:
            guide_error = str(e)
    for expert, pattern in _ROWS:
        if expert == "security" and (security_row is not None or guide_error):
            continue
        rx = re.compile(pattern, re.I)
        for path, line in code:
            m = rx.search(line)
            if m:
                why[expert] = f"{m.group(0).strip()!r} in {path}"
                break
    if guide_error:
        why["security"] = f".swarmai/review.md security line does not parse: {guide_error}"
    elif security_row is not None:
        # as a pipeline run reads the declaration: the changed source paths and the diff
        if _guide.applies(security_row, [p for p, _a, _r in sources], diff):
            why["security"] = "the repository's security line matches"
    for path, _a, removed in sources:
        hit = next((l for l in removed if _PUBLIC_DEF.match(l) and not _PRIVATE_DEF.match(l)), None)
        if hit and "api-contract" not in why:
            why["api-contract"] = f"public definition changed in {path}: {hit.strip()[:60]!r}"
    dirs = {p.rsplit("/", 1)[0] if "/" in p else "" for p, _a, _r in sources}
    if len(sources) >= 3 or (len(sources) >= 2 and len(dirs) >= 2):
        why["integration"] = f"{len(sources)} source files in {len(dirs)} directories"
    ui = next((p for p, _a, _r in sources if _UI.search(p)), None)
    if ui:
        why["ux"] = f"UI file {ui}"
    order = [e for e, _ in _ROWS] + ["correctness", "api-contract", "integration", "ux"]
    roster = sorted(why, key=lambda e: (e != "correctness", order.index(e)))
    return {"roster": roster, "why": {e: why[e] for e in roster}, "rules": RULES}


def for_case(repo: Path | str, case: Mapping) -> dict:
    """The roster for *case* in *repo*: B's diff and the review guide at B's parent (the
    guide the change was written under, never a later one)."""
    from core.expert_bench.cases import _git
    diff = _git(repo, "diff", "--no-renames", "--no-ext-diff", "--no-textconv", "--no-color",
                str(case["base"]), str(case["head"]))
    return decide(diff, _guide.read_at(repo, str(case["base"])))


def catalog(path: Path | str = CATALOG) -> dict[str, str]:
    """``{expert id: absolute prompt file}`` from the skill's CATALOG.md."""
    path = Path(path)
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\|\s*`([a-z][a-z0-9-]*)`\s*\|.*\|\s*`([^`]+\.md)`\s*\|\s*$", line)
        if m:
            out[m.group(1)] = str((path.parent / m.group(2)).resolve())
    return out
