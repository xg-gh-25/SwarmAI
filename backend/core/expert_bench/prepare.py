"""Readying extracted cases for a run: language, roster, defect category, and the sample.

Design: docs/design/2026-11-18-expert-review-benchmark.md §3 (a category per case,
judged once when the case is built, stored in the case) and §4 (the roster). ``prepare``
adds to each case what the run and the report split on:

- ``language``: the language of most of B's changed source lines (by file extension);
- ``roster``: what ``s_expert-review`` §1 would call for B (``roster.for_case``);
- each defect's ``category``: one id from the expert catalog, asked once of a judge
  (``category_judge`` records its model and answer); a person can correct it by hand.

``sample`` takes a fixed, seeded sample stratified by B's diff size (``BUCKETS``).

Public: ``BUCKETS``, ``bucket``, ``language``, ``prepare_case``, ``sample``.
"""
from __future__ import annotations

import random
from pathlib import Path
from typing import Awaitable, Callable, Iterable, Mapping, Optional

BUCKETS = ((1, 100, "1-100"), (101, 300, "101-300"), (301, 800, "301-800"))
_EXT = {".py": "python", ".pyi": "python", ".ts": "typescript", ".tsx": "typescript",
        ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
        ".rs": "rust", ".go": "go", ".java": "java", ".kt": "kotlin", ".swift": "swift",
        ".rb": "ruby", ".sh": "shell", ".bash": "shell", ".c": "c", ".h": "c", ".cc": "cpp",
        ".cpp": "cpp", ".hpp": "cpp", ".cs": "csharp", ".php": "php", ".scala": "scala",
        ".vue": "vue", ".svelte": "svelte", ".css": "css", ".scss": "css", ".html": "html",
        ".sql": "sql", ".lua": "lua", ".dart": "dart", ".m": "objective-c"}


def bucket(diff_lines: Optional[int]) -> str:
    n = int(diff_lines or 0)
    for lo, hi, name in BUCKETS:
        if lo <= n <= hi:
            return name
    return f"{BUCKETS[-1][1] + 1}+" if n > BUCKETS[-1][1] else "0"


def language(repo: Path | str, commit: str) -> Optional[str]:
    """The language most of *commit*'s changed source lines are in; None without any."""
    from core.expert_bench.cases import _git
    from core.review_order import role
    weight: dict[str, int] = {}
    for line in _git(repo, "diff", "--numstat", "--no-renames", "--no-textconv",
                     f"{commit}^", commit).splitlines():
        a, d, path = (line.split("\t", 2) + ["", ""])[:3]
        if role(path) != "source":
            continue
        lang = _EXT.get(Path(path).suffix.lower())
        if lang:
            n = (int(a) if a.isdigit() else 0) + (int(d) if d.isdigit() else 0)
            weight[lang] = weight.get(lang, 0) + max(n, 1)
    return max(sorted(weight), key=lambda k: weight[k]) if weight else None


Categorize = Callable[[Mapping], Awaitable[Mapping]]


async def prepare_case(repo: Path | str, case: Mapping, categorize: Optional[Categorize]) -> dict:
    """*case* with ``language``, ``roster`` and (with *categorize*) each defect's category."""
    import asyncio
    from core.expert_bench import roster
    out = dict(case)
    out["language"] = await asyncio.to_thread(language, repo, str(case["head"]))
    out["roster"] = await asyncio.to_thread(roster.for_case, repo, case)
    if categorize is not None and not all(d.get("category") for d in case.get("defects") or []):
        verdict = await categorize(case)
        out["category_judge"] = {k: verdict.get(k) for k in ("model", "answer", "text", "cost_usd")}
        out["defects"] = [{**d, "category": d.get("category") or verdict.get("answer")}
                          for d in case.get("defects") or []]
    return out


def sample(cases: Iterable[Mapping], n: int, *, seed: int, language: Optional[str] = "python",
           confirmed: bool = True) -> list[dict]:
    """*n* cases, the same for the same input and *seed*: confirmed cases of *language*,
    spread over the diff-size buckets as evenly as they allow (a bucket with too few
    gives its share to the others), sorted by id."""
    pool = [dict(c) for c in cases if (not confirmed or c.get("confirmed"))
            and (language is None or c.get("language") == language)]
    groups: dict[str, list[dict]] = {}
    for c in sorted(pool, key=lambda c: c["id"]):
        groups.setdefault(bucket(c.get("diff_lines")), []).append(c)
    names = sorted(groups)
    want = {g: 0 for g in names}
    left = min(n, len(pool))
    while left:
        open_ = [g for g in names if want[g] < len(groups[g])]
        share, extra = divmod(left, len(open_))
        for i, g in enumerate(open_):
            add = min(share + (1 if i < extra else 0), len(groups[g]) - want[g])
            want[g] += add
            left -= add
    rng = random.Random(seed)
    picked = [c for g in names for c in rng.sample(groups[g], want[g])]
    return sorted(picked, key=lambda c: c["id"])
