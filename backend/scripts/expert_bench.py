"""Review expert benchmark — command line (design docs/design/2026-11-18-expert-review-benchmark.md).

Phase 1, step 1: extract the S1 cases of a repository (no model is called).

    "$SWARM_BACKEND_BIN" py -m scripts.expert_bench extract --repo <checkout> --out cases.jsonl \\
        [--test-cmd "<command with {tests}>" [--test-cwd <dir>] [--red-pattern <regex>]] \\
        [--grep <regex>] [--reviewed-pattern <regex>] [--start N] [--limit N] \\
        [--max-files 6] [--owner-share 0.8] [--max-diff-lines 800] [--timeout 300]

Without ``--test-cmd`` the red/green check is skipped and each case says
``confirmed: false``. ``--red-pattern`` is the test runner's own mark of a failed test
(``^FAILED `` for pytest): a red run without it (tests that do not load) is dropped.
``--start`` / ``--limit`` select a slice of the fix candidates (newest first). Each case is
appended to ``--out`` as soon as it is kept, and every candidate's outcome to
``<out>.outcomes.jsonl``; a run that is stopped keeps what it decided, and the next run
skips those candidates. Prints the count of this run's outcomes as one JSON object.

Phase 1, step 2: run the experts and a baseline on the cases, score, compare.

    ... prepare --repo <checkout> --cases cases.jsonl --out prepared.jsonl [--model M] [--concurrency 4]
    ... sample  --cases prepared.jsonl --out sample.jsonl --n 30 --seed 1 [--language python]
    ... run     --repo <checkout> --cases sample.jsonl --out results.jsonl --model M --trusted \\
                [--k 3] [--arms expert,baseline] [--concurrency 4] [--timeout 1800] \\
                [--start-window S]
    ... score   --repo <checkout> --cases sample.jsonl --results results.jsonl \\
                --out scores.jsonl --judge-model M [--judge-cache judge.jsonl] [--model M] [--concurrency 4]
    ... compare <scores A> <scores B> [--arm-a expert] [--arm-b baseline] [--metric found]
    ... summary --scores scores.jsonl

``prepare`` adds each case's language, the roster ``s_expert-review`` §1 would call for
B (decided from the diff, recorded with the reasons) and, with ``--model``, the defect's
category, asked once of that model (resumable: a case already in ``--out`` is kept).
``sample`` takes a seeded sample of confirmed cases stratified by B's diff size. ``run``
replays both arms (k replies each), appending every review to ``--out`` as it ends; a
stopped run resumes and a failed review is run again; ``--trusted`` says the repository
is yours (a review is a model with Bash over its content, with no sandbox);
``--start-window`` starts no review after S seconds, so a long run goes in slices that
each end (the rest are counted ``deferred``). ``score`` parses, matches and judges
(each judged answer kept in ``--judge-cache``); ``compare`` pairs two score files case by
case with a bootstrap interval; ``summary`` prints the phase-1 tables as JSON. This is a
developer tool: never a gate, and nothing in a pipeline or in ``s_expert-review`` calls it.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shlex
import sys
from pathlib import Path

from core.expert_bench import cases as bc


def _rows(path: Path) -> list[dict]:
    """The JSON lines of *path*; a line a stopped run left half written is skipped."""
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def _write(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def _prepare(args) -> int:
    from core.expert_bench import judge, prepare
    have = {r["id"] for r in _rows(args.out)}
    categorize = judge.make_categorizer(args.repo, model=args.model) if args.model else None
    counts = {"prepared": 0, "kept": len(have)}

    async def go():
        args.out.parent.mkdir(parents=True, exist_ok=True)
        sem = asyncio.Semaphore(max(1, args.concurrency))

        async def one(case):
            async with sem:
                row = await prepare.prepare_case(args.repo, case, categorize)
            with args.out.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            counts["prepared"] += 1
        await asyncio.gather(*(one(c) for c in _rows(args.cases) if c["id"] not in have))
    asyncio.run(go())
    print(json.dumps(counts))
    return 0


def _sample(args) -> int:
    from core.expert_bench import prepare
    picked = prepare.sample(_rows(args.cases), args.n, seed=args.seed,
                            language=None if args.language == "any" else args.language)
    _write(args.out, picked)
    by = {}
    for c in picked:
        b = prepare.bucket(c.get("diff_lines"))
        by[b] = by.get(b, 0) + 1
    print(json.dumps({"sampled": len(picked), "by_bucket": by}))
    return 0


def _run(args) -> int:
    from core.expert_bench import harness
    if not args.trusted:
        print("run replays a model with Bash over the repository's content (no sandbox): "
              "say --trusted to confirm the repository is yours. An untrusted corpus needs a "
              "sandbox first (core/expert_bench/harness.py).", file=sys.stderr)
        return 2
    cases = _rows(args.cases)
    counts = asyncio.run(harness.run_all(
        cases, repo=args.repo, model=args.model, k=args.k, out=args.out,
        arms=[a for a in args.arms.split(",") if a], concurrency=args.concurrency,
        timeout=args.timeout, tmp_root=args.tmp_root, start_window=args.start_window,
        on_row=lambda r: print(json.dumps({"case": r["case"], "arm": r["arm"], "expert": r["expert"],
                                           "k": r["k"], "error": r.get("error"),
                                           "cost": r.get("total_cost_usd"), "s": r.get("wall_s")}),
                               flush=True)))
    print(json.dumps(counts))
    return 0


def _score(args) -> int:
    from core.expert_bench import judge, scoring
    j = judge.make_judge(args.repo, model=args.judge_model, cache=args.judge_cache)
    rows = asyncio.run(scoring.score(_rows(args.cases), _rows(args.results), judge=j,
                                     model=args.model, repo=str(args.repo),
                                     concurrency=args.concurrency))
    _write(args.out, rows)
    print(json.dumps({"rows": len(rows)}))
    return 0


def _compare(args) -> int:
    from core.expert_bench import scoring
    out = scoring.compare(_rows(args.a), _rows(args.b), arm_a=args.arm_a, arm_b=args.arm_b,
                          metric=args.metric, iterations=args.iterations, seed=args.seed)
    print(json.dumps(out))
    return 0


def _summary(args) -> int:
    from core.expert_bench import scoring
    print(json.dumps(scoring.summarize(_rows(args.scores), iterations=args.iterations, seed=args.seed),
                     indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("prepare")
    pr.add_argument("--repo", required=True, type=Path)
    pr.add_argument("--cases", required=True, type=Path)
    pr.add_argument("--out", required=True, type=Path)
    pr.add_argument("--model", default=None)
    pr.add_argument("--concurrency", type=int, default=4)
    sa = sub.add_parser("sample")
    sa.add_argument("--cases", required=True, type=Path)
    sa.add_argument("--out", required=True, type=Path)
    sa.add_argument("--n", type=int, default=30)
    sa.add_argument("--seed", type=int, required=True)
    sa.add_argument("--language", default="python")
    ru = sub.add_parser("run")
    ru.add_argument("--repo", required=True, type=Path)
    ru.add_argument("--cases", required=True, type=Path)
    ru.add_argument("--out", required=True, type=Path)
    ru.add_argument("--model", required=True)
    ru.add_argument("--k", type=int, default=3)
    ru.add_argument("--arms", default="expert,baseline")
    ru.add_argument("--concurrency", type=int, default=4)
    ru.add_argument("--timeout", type=float, default=1800)
    ru.add_argument("--tmp-root", type=Path, default=None)
    ru.add_argument("--start-window", type=float, default=None,
                    help="start no review later than this many seconds in; the rest are deferred")
    ru.add_argument("--trusted", action="store_true",
                    help="the repository is yours: its content may steer a model that has Bash")
    sc = sub.add_parser("score")
    sc.add_argument("--repo", required=True, type=Path)
    sc.add_argument("--cases", required=True, type=Path)
    sc.add_argument("--results", required=True, type=Path)
    sc.add_argument("--out", required=True, type=Path)
    sc.add_argument("--judge-model", required=True)
    sc.add_argument("--judge-cache", type=Path, default=None)
    sc.add_argument("--model", default=None, help="score only the reviews run with this model")
    sc.add_argument("--concurrency", type=int, default=4, help="judge questions asked at a time")
    co = sub.add_parser("compare")
    co.add_argument("a", type=Path)
    co.add_argument("b", type=Path)
    co.add_argument("--arm-a", default="expert")
    co.add_argument("--arm-b", default="baseline")
    co.add_argument("--metric", default="found", choices=["found", "found_loose", "located"])
    co.add_argument("--iterations", type=int, default=2000)
    co.add_argument("--seed", type=int, default=0)
    su = sub.add_parser("summary")
    su.add_argument("--scores", required=True, type=Path)
    su.add_argument("--iterations", type=int, default=2000)
    su.add_argument("--seed", type=int, default=0)
    ex = sub.add_parser("extract")
    ex.add_argument("--repo", required=True, type=Path)
    ex.add_argument("--out", required=True, type=Path)
    ex.add_argument("--test-cmd", default=None)
    ex.add_argument("--test-cwd", default="")
    ex.add_argument("--red-pattern", default=None)
    ex.add_argument("--grep", default=bc.DEFAULT_GREP)
    ex.add_argument("--reviewed-pattern", default=None)
    ex.add_argument("--start", type=int, default=0)
    ex.add_argument("--limit", type=int, default=None)
    ex.add_argument("--max-files", type=int, default=6)
    ex.add_argument("--owner-share", type=float, default=0.8)
    ex.add_argument("--max-diff-lines", type=int, default=800)
    ex.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args(argv)
    handler = {"prepare": _prepare, "sample": _sample, "run": _run, "score": _score,
               "compare": _compare, "summary": _summary}.get(args.cmd)
    if handler:
        return handler(args)
    test_cmd = shlex.split(args.test_cmd) if args.test_cmd else None
    args.out.parent.mkdir(parents=True, exist_ok=True)
    log = args.out.with_name(args.out.name + ".outcomes.jsonl")
    done = [r["fix"] for r in _rows(log) if "fix" in r]
    kept = {r["id"]: r.get("head") for r in _rows(args.out) if "id" in r}

    def keep(fix: str, outcome: str, case: dict | None, detail: str) -> None:
        if case is not None and case["id"] not in kept:   # a run stopped before its outcome line
            kept[case["id"]] = case["head"]
            with args.out.open("a", encoding="utf-8") as f:
                f.write(json.dumps(case, ensure_ascii=False) + "\n")
        with log.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"fix": fix, "outcome": outcome, **({"detail": detail} if detail else {})})
                    + "\n")

    _cases, counts = bc.extract(
        args.repo, test_cmd=test_cmd, grep=args.grep, test_cwd=args.test_cwd,
        max_files=args.max_files, owner_share=args.owner_share,
        max_diff_lines=args.max_diff_lines, reviewed_pattern=args.reviewed_pattern,
        start=args.start, limit=args.limit, timeout=args.timeout, red_pattern=args.red_pattern,
        skip=done, kept_b=[h for h in kept.values() if h], on_outcome=keep)
    print(json.dumps(counts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
