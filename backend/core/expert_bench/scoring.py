"""Scoring benchmark replies against a case's known defect: parse, match, judge, recall.

Design: docs/design/2026-11-18-expert-review-benchmark.md §5. No model is called here;
the "same failure?" judge is passed in (``core.expert_bench.judge.make_judge``).

1. ``parse`` reads the findings of a reply written as ``SEVERITY | file:line | what
   breaks | fix`` (bulleted, bold, in a table, or plain), with paths made relative to the
   sealed case's root.
2. ``located``: a finding is at the defect when the paths are equal and its line (or
   its line range) reaches the defect's range widened by 3 lines.
3. Every finding on a path of the case's defect is a candidate pair; the judge answers,
   once per pair, whether the finding and the defect name the same failure. A defect is
   **found** by a reply when one finding is both judged the same failure and located;
   ``found_loose`` drops the location, so location accuracy is found / found_loose.
4. Phase 1 measures recall only: a finding that does not match the case's defect is
   ``unlabelled`` (the case labels one defect; B may hold others), never a false positive.

``score`` gives one row per review of the expert arm (``expert`` = its id,
``own_category`` when the defect is of its category) and one row per (case, arm, reply)
with ``expert`` None: the expert arm combines the replies of its whole roster with the
same k. A combined row is ``complete`` only when every member of the roster replied.
``bootstrap`` and ``compare`` resample cases, and the k replies inside each case.

A finding whose path names a defect path relative to a subdirectory is matched to it
(``on_paths``); findings with no usable path and judge answers that did not parse are
counted (``no_path``, ``judge_unparsed``), so a silent miss shows.

Public: ``Finding``, ``parse``, ``on_paths``, ``located``, ``score``, ``per_case``,
``bootstrap``, ``compare``, ``summarize``, ``WIDEN``.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import random
import re
from dataclasses import asdict, dataclass
from typing import Any, Awaitable, Callable, Iterable, Mapping, Optional, Sequence

WIDEN = 3
_SEV = {"CRITICAL": "CRITICAL", "HIGH": "HIGH", "MEDIUM": "MEDIUM", "MED": "MEDIUM", "LOW": "LOW"}
# a finding line: an optional bullet / number / table pipe, an optional "Severity:", the
# severity (bold, bracketed or quoted), an optional "(8/10)"-style note, then a pipe
_FINDING = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+|\|\s*)?(?:[*_`]*severity[*_`]*\s*:\s*)?"
                      r"[*_`\[]*\s*(CRITICAL|HIGH|MEDIUM|MED|LOW)\b\s*[*_`\]]*"
                      r"(?:\s*\([^)|]*\))?\s*[*_`]*\s*\|(.*)$", re.I)
_LOC = re.compile(r"^(.+?):L?(\d+)(?:\s*[-–]\s*L?(\d+))?\b")


@dataclass(frozen=True)
class Finding:
    severity: str
    path: Optional[str]
    line: Optional[int]
    end: Optional[int]
    what: str
    fix: str
    raw: str


def _path(text: str, root: Optional[str]) -> Optional[str]:
    p = text.strip().strip("`*\"'[]()<>").strip()
    if not p or " " in p:
        return None
    if root and os.path.isabs(p):
        for r in {root.rstrip("/"), os.path.realpath(root).rstrip("/")}:
            if p.startswith(r + "/"):
                p = p[len(r) + 1:]
                break
    while p.startswith("./"):
        p = p[2:]
    return p or None


def parse(reply: Optional[str], root: Optional[str] = None) -> list[Finding]:
    """The findings in *reply*; *root* (the sealed case) is cut from absolute paths."""
    out: list[Finding] = []
    for raw in (reply or "").splitlines():
        m = _FINDING.match(raw)
        if not m:
            continue
        rest = m.group(2).strip()
        if rest.endswith("|"):
            rest = rest[:-1]
        cells = [c.strip() for c in rest.split("|")]
        loc = cells[0].strip().strip("`*").strip()
        lm = _LOC.match(loc.strip("`*\"'[]()"))
        if lm:
            path = _path(lm.group(1), root)
            line = int(lm.group(2))
            end = int(lm.group(3)) if lm.group(3) else line
            if end < line:
                line, end = end, line
        else:
            path, line, end = _path(loc, root), None, None
        if len(cells) >= 3:
            what, fix = " | ".join(cells[1:-1]), cells[-1]
        else:
            what, fix = (cells[1] if len(cells) > 1 else ""), ""
        out.append(Finding(_SEV[m.group(1).upper()], path, line, end, what, fix, raw.strip()))
    return out


def on_paths(f: Finding, paths: Iterable[str]) -> Optional[str]:
    """The defect path *f* names: the same path, or the only one of *paths* that ends with
    *f*'s path (or that *f*'s path ends with) at a ``/`` boundary — an expert may name a
    file relative to a subdirectory. None when it names none, or several."""
    if f.path is None:
        return None
    paths = list(paths)
    if f.path in paths:
        return f.path
    hits = [p for p in paths if p.endswith("/" + f.path) or f.path.endswith("/" + p)]
    return hits[0] if len(hits) == 1 else None


def _on(f: Finding, path: Optional[str]) -> Finding:
    return f if path is None or path == f.path else Finding(f.severity, path, f.line, f.end,
                                                            f.what, f.fix, f.raw)


def located(f: Finding, defect: Mapping, widen: int = WIDEN) -> bool:
    if f.path is None or f.line is None or f.path != defect["path"]:
        return False
    lo, hi = int(defect["from_line"]) - widen, int(defect["to_line"]) + widen
    return f.line <= hi and (f.end if f.end is not None else f.line) >= lo


Judge = Callable[[Mapping, Sequence[Mapping], Finding], Awaitable[Mapping[str, Any]]]


def _usage_total(u: Optional[Mapping]) -> int:
    if not u:
        return 0
    return sum(int(u.get(k) or 0) for k in ("input_tokens", "output_tokens",
                                             "cache_read_input_tokens", "cache_creation_input_tokens"))


def _latest(rows: Iterable[Mapping]) -> dict:
    """One row per review: the last that succeeded, else the last attempt."""
    pick: dict[tuple, Mapping] = {}
    for r in rows:
        key = (r["case"], r["arm"], r["expert"], r["k"])
        if key not in pick or not r.get("error") or pick[key].get("error"):
            pick[key] = r
    return pick


def _bucket(lines: Optional[int]) -> str:
    from core.expert_bench.prepare import bucket
    return bucket(lines)


async def score(cases: Iterable[Mapping], results: Iterable[Mapping], *, judge: Judge,
                widen: int = WIDEN, model: Optional[str] = None,
                repo: Optional[str] = None, concurrency: int = 1) -> list[dict]:
    """Score rows for *results* (with *model*, only its reviews). Every (arm, expert) must
    have been run with one model and one prompt: results of two prompt versions or models
    are two runs, compared with ``compare``, never mixed into one score (ValueError).
    A case without a recorded roster gets the one ``roster.for_case`` decides (with
    *repo*), else the experts its results name. The judge is asked *concurrency*
    questions at a time, each candidate pair once."""
    by_id = {c["id"]: c for c in cases}
    results = [r for r in results if r["case"] in by_id and (model is None or r.get("model") == model)]
    versions: dict[tuple, set] = {}
    for r in results:
        versions.setdefault((r["arm"], r["expert"]), set()).add((r.get("model"), r.get("prompt_sha")))
    mixed = {k: v for k, v in versions.items() if len(v) > 1}
    if mixed:
        raise ValueError("results mix models or prompt versions: " + "; ".join(
            f"{a}/{e}: {sorted(map(str, v))}" for (a, e), v in sorted(mixed.items())))
    reviews = _latest(results)
    parsed: dict[tuple, list[Finding]] = {}
    asks: dict[tuple, tuple] = {}
    for key, r in sorted(reviews.items(), key=lambda kv: tuple(map(str, kv[0]))):
        case = by_id[r["case"]]
        defects = case.get("defects") or []
        paths = {d["path"] for d in defects}
        ok = not r.get("error") and r.get("reply") is not None
        parsed[key] = [_on(f, on_paths(f, paths)) for f in parse(r.get("reply"), r.get("root"))] if ok else []
        for f in parsed[key]:
            if f.path in paths:
                asks.setdefault((case["id"], hashlib.sha256(f.raw.encode()).hexdigest()), (case, defects, f))
    verdicts: dict[tuple, Mapping] = {}
    sem = asyncio.Semaphore(max(1, concurrency))

    async def ask(vkey: tuple, case: Mapping, defects: Sequence[Mapping], f: Finding) -> None:
        async with sem:
            verdicts[vkey] = await judge(case, defects, f)
    if concurrency <= 1:
        for vkey, item in asks.items():
            await ask(vkey, *item)
    else:
        await asyncio.gather(*(ask(vkey, *item) for vkey, item in asks.items()))
    per_review: dict[tuple, dict] = {}
    for key, r in sorted(reviews.items(), key=lambda kv: tuple(map(str, kv[0]))):
        case = by_id[r["case"]]
        defects = case.get("defects") or []
        paths = {d["path"] for d in defects}
        ok = not r.get("error") and r.get("reply") is not None
        findings = parsed[key]
        judged = []
        for f in findings:
            if f.path not in paths:
                continue
            v = verdicts[(case["id"], hashlib.sha256(f.raw.encode()).hexdigest())]
            judged.append({"finding": asdict(f), "same": bool(v.get("same")),
                           "parsed": v.get("parsed", True),
                           "located": any(located(f, d, widen) for d in defects),
                           "judge_model": v.get("model"), "judge_text": v.get("text")})
        per_review[key] = {
            "ok": ok, "error": r.get("error"), "findings": len(findings),
            "candidates": len(judged),
            "no_path": sum(1 for f in findings if f.path is None),
            "judge_unparsed": sum(1 for j in judged if not j["parsed"]),
            "judged_same": sum(j["same"] for j in judged),
            "found": any(j["same"] and j["located"] for j in judged),
            "found_loose": any(j["same"] for j in judged),
            "located": any(located(f, d, widen) for f in findings for d in defects),
            "judgements": judged,
            "judge_model": next((j["judge_model"] for j in judged if j["judge_model"]), None),
            "expert_cost_usd": float(r.get("expert_cost_usd") or 0.0),
            "parent_cost_usd": float(r.get("parent_cost_usd") or 0.0),
            "expert_tokens": _usage_total(r.get("expert_usage")),
            "parent_tokens": _usage_total(r.get("parent_usage")),
            "duration_s": float(r.get("duration_ms") or 0) / 1000.0,
            "expert_tool_calls": r.get("expert_tool_calls"), "capped": r.get("capped"),
            "outside_paths": len(r.get("outside_paths") or []),
            "model": r.get("model"), "prompt_sha": r.get("prompt_sha"),
        }

    rows: list[dict] = []
    for cid, case in by_id.items():
        category = next((d.get("category") for d in case.get("defects") or [] if d.get("category")), None)
        common = {"case": cid, "category": category, "diff_lines": case.get("diff_lines"),
                  "bucket": _bucket(case.get("diff_lines")), "reviewed_before": case.get("reviewed_before")}
        mine = {k: v for k, v in per_review.items() if k[0] == cid}
        if not mine:
            continue
        recorded = (case.get("roster") or {}).get("roster")
        if recorded is None and repo is not None:
            from core.expert_bench import roster as _roster
            recorded = _roster.for_case(repo, case)["roster"]
        roster = sorted(recorded) if recorded is not None else sorted(
            {k[2] for k in mine if k[1] == "expert"})
        ks = sorted({k[3] for k in mine})
        for (c, arm, expert, k), pr in sorted(mine.items(), key=lambda kv: tuple(map(str, kv[0]))):
            if arm == "expert":
                rows.append({**common, "arm": arm, "expert": expert, "k": k,
                             "own_category": expert == category, "complete": pr["ok"],
                             **{f: v for f, v in pr.items() if f not in ("ok",)}})
        for arm in sorted({k[1] for k in mine} | ({"expert"} if recorded is not None else set())):
            members = roster if arm == "expert" else sorted({k[2] for k in mine if k[1] == arm})
            for k in ks:
                prs = [mine.get((cid, arm, e, k)) for e in members]
                got = [p for p in prs if p is not None and p["ok"]]
                complete = len(got) == len(members)
                rows.append({
                    **common, "arm": arm, "expert": None, "k": k, "roster": members,
                    "complete": complete,
                    "found": any(p["found"] for p in got),
                    "found_loose": any(p["found_loose"] for p in got),
                    "located": any(p["located"] for p in got),
                    "findings": sum(p["findings"] for p in got),
                    "candidates": sum(p["candidates"] for p in got),
                    "no_path": sum(p["no_path"] for p in got),
                    "judge_unparsed": sum(p["judge_unparsed"] for p in got),
                    "models": sorted({str(p["model"]) for p in got}),
                    "prompt_shas": sorted({str(p["prompt_sha"]) for p in got}),
                    "judged_same": sum(p["judged_same"] for p in got),
                    "unlabelled": sum(p["findings"] - p["judged_same"] for p in got),
                    "judge_model": next((p["judge_model"] for p in got if p["judge_model"]), None),
                    "expert_cost_usd": sum(p["expert_cost_usd"] for p in got),
                    "parent_cost_usd": sum(p["parent_cost_usd"] for p in got),
                    "expert_tokens": sum(p["expert_tokens"] for p in got),
                    "parent_tokens": sum(p["parent_tokens"] for p in got),
                    "duration_s": sum(p["duration_s"] for p in got),
                    "wall_s": max((p["duration_s"] for p in got), default=0.0),
                    "reviews": len(got),
                })
    for r in rows:
        if r["expert"] is not None:
            r["unlabelled"] = r["findings"] - r["judged_same"]
    return rows


def per_case(rows: Iterable[Mapping], *, arm: Optional[str] = None, expert: Optional[str] = None,
             metric: str = "found", own_category: Optional[bool] = None) -> dict[str, list[float]]:
    """``{case: [metric per reply, by k]}`` from score rows: the combined rows of *arm*
    (``expert`` None), or one expert's own rows; incomplete rows are left out."""
    rows = [r for r in rows if r.get("complete", True) and r.get("expert") == expert
            and (arm is None or r.get("arm") == arm)
            and (own_category is None or r.get("own_category") == own_category)]
    if arm is None and len({r.get("arm") for r in rows}) > 1:
        raise ValueError("rows of several arms: name the arm")
    out: dict[str, list[tuple[int, float]]] = {}
    for r in rows:
        out.setdefault(r["case"], []).append((int(r.get("k") or 0), float(bool(r.get(metric)))))
    return {c: [v for _k, v in sorted(vals)] for c, vals in sorted(out.items())}


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs)


def _pct(sorted_xs: Sequence[float], q: float) -> float:
    return sorted_xs[int(round((len(sorted_xs) - 1) * q))]


def _point(per: Mapping[str, Sequence[float]]) -> float:
    return _mean([_mean(v) for v in per.values()])


def bootstrap(per: Mapping[str, Sequence[float]], *, iterations: int = 2000, seed: int = 0,
              level: float = 0.95) -> tuple[float, float, float]:
    """(mean, low, high): the mean over cases of each case's mean over its replies, and a
    percentile interval from resampling the cases, then the replies inside each case."""
    per = {c: list(v) for c, v in per.items() if v}
    if not per:
        return (float("nan"),) * 3
    ids = sorted(per)
    rng = random.Random(seed)
    stats = []
    for _ in range(iterations):
        total = 0.0
        for _i in range(len(ids)):
            vals = per[ids[rng.randrange(len(ids))]]
            total += _mean([vals[rng.randrange(len(vals))] for _j in vals])
        stats.append(total / len(ids))
    stats.sort()
    a = (1 - level) / 2
    return _point(per), _pct(stats, a), _pct(stats, 1 - a)


def compare(rows_a: Iterable[Mapping], rows_b: Iterable[Mapping], *, arm_a: Optional[str] = None,
            arm_b: Optional[str] = None, metric: str = "found", iterations: int = 2000,
            seed: int = 0, level: float = 0.95) -> dict:
    """A against B, paired by case: the difference of their recall and a bootstrap
    interval over the shared cases and the replies inside each. An interval holding 0
    reads as no change."""
    pa = {c: v for c, v in per_case(rows_a, arm=arm_a, metric=metric).items() if v}
    pb = {c: v for c, v in per_case(rows_b, arm=arm_b, metric=metric).items() if v}
    shared = sorted(set(pa) & set(pb))
    out = {"metric": metric, "cases": len(shared), "iterations": iterations, "seed": seed,
           "only_a": len(set(pa) - set(pb)), "only_b": len(set(pb) - set(pa))}
    if not shared:
        return {**out, "a": None, "b": None, "diff": None, "ci": None, "verdict": "no shared cases"}
    a = _point({c: pa[c] for c in shared})
    b = _point({c: pb[c] for c in shared})
    rng = random.Random(seed)
    stats = []
    for _ in range(iterations):
        total = 0.0
        for _i in range(len(shared)):
            c = shared[rng.randrange(len(shared))]
            va, vb = pa[c], pb[c]
            total += (_mean([va[rng.randrange(len(va))] for _j in va])
                      - _mean([vb[rng.randrange(len(vb))] for _j in vb]))
        stats.append(total / len(shared))
    stats.sort()
    q = (1 - level) / 2
    lo, hi = _pct(stats, q), _pct(stats, 1 - q)
    verdict = "A better" if lo > 0 else "B better" if hi < 0 else "no change"
    return {**out, "a": a, "b": b, "diff": a - b, "ci": [lo, hi], "verdict": verdict}


def _cell(per: Mapping[str, Sequence[float]], iterations: int, seed: int) -> dict:
    per = {c: v for c, v in per.items() if v}
    if not per:
        return {"cases": 0, "mean": None, "ci": None}
    m, lo, hi = bootstrap(per, iterations=iterations, seed=seed)
    return {"cases": len(per), "mean": m, "ci": [lo, hi]}


def summarize(rows: Sequence[Mapping], *, iterations: int = 2000, seed: int = 0) -> dict:
    """The phase-1 tables: recall per arm (and loose / located), split by diff-size
    bucket and by reviewed_before; each expert on the defects of its own category;
    location accuracy; tokens, time and cost per case; expert arm against baseline."""
    arm_rows = [r for r in rows if r.get("expert") is None]
    arms = sorted({r["arm"] for r in arm_rows})
    out: dict[str, Any] = {"arms": {}, "by_bucket": {}, "by_reviewed_before": {}, "experts": {},
                           "location": {}, "cost": {}, "incomplete": {}, "compare": None}
    for arm in arms:
        mine = [r for r in arm_rows if r["arm"] == arm]
        out["incomplete"][arm] = sum(1 for r in mine if not r.get("complete"))
        out["arms"][arm] = {m: _cell(per_case(mine, arm=arm, metric=m), iterations, seed)
                            for m in ("found", "found_loose", "located")}
        for field, target in (("bucket", "by_bucket"), ("reviewed_before", "by_reviewed_before")):
            for value in sorted({str(r.get(field)) for r in mine}):
                part = [r for r in mine if str(r.get(field)) == value]
                out[target].setdefault(value, {})[arm] = _cell(per_case(part, arm=arm), iterations, seed)
        loose = [r for r in mine if r.get("complete") and r.get("found_loose")]
        out["location"][arm] = {"found_loose": len(loose), "found": sum(1 for r in loose if r.get("found")),
                                "accuracy": (sum(1 for r in loose if r.get("found")) / len(loose)) if loose else None,
                                "located_any": sum(1 for r in mine if r.get("complete") and r.get("located"))}
        done = [r for r in mine if r.get("complete")]
        per: dict[str, list[Mapping]] = {}
        for r in done:
            per.setdefault(r["case"], []).append(r)

        def avg(field: str) -> Optional[float]:
            vals = [_mean([float(x.get(field) or 0) for x in rs]) for rs in per.values()]
            return _mean(vals) if vals else None
        out["cost"][arm] = {"cases": len(per), "reviews_per_reply": avg("reviews"),
                            "expert_cost_usd": avg("expert_cost_usd"), "parent_cost_usd": avg("parent_cost_usd"),
                            "expert_tokens": avg("expert_tokens"), "parent_tokens": avg("parent_tokens"),
                            "duration_s": avg("duration_s"), "wall_s": avg("wall_s"),
                            "total_cost_usd": sum(float(x.get("expert_cost_usd") or 0) + float(x.get("parent_cost_usd") or 0)
                                                  for x in mine)}
    expert_rows = [r for r in rows if r.get("expert") is not None]
    for e in sorted({r["expert"] for r in expert_rows}):
        own = per_case(expert_rows, arm="expert", expert=e, own_category=True)
        all_ = per_case(expert_rows, arm="expert", expert=e)
        out["experts"][e] = {"own_category": _cell(own, iterations, seed),
                             "all_called": _cell(all_, iterations, seed)}
    cats: dict[str, dict[str, int]] = {}
    for r in arm_rows:
        if r["arm"] == "expert" and r.get("k") == 0 and r.get("category"):
            c = cats.setdefault(r["category"], {"cases": 0, "on_roster": 0})
            c["cases"] += 1
            c["on_roster"] += int(r["category"] in (r.get("roster") or []))
    out["categories"] = cats
    if "expert" in arms and "baseline" in arms:
        out["compare"] = compare(arm_rows, arm_rows, arm_a="expert", arm_b="baseline",
                                 iterations=iterations, seed=seed)
    return out
