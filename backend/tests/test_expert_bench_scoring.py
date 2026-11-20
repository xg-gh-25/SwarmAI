"""Expert benchmark, phase 1 step 2: parsing, matching, judging, recall and compare.

Design: docs/design/2026-11-18-expert-review-benchmark.md §5 (scoring) and §8.

Questions this answers, without a model (a fake judge stands in):
  - Does the parser read the findings an expert writes (plain, bulleted, bold, in a
    table, with an absolute path into the sealed case, with a line range) and nothing else?
  - Does the deterministic match hold inside, at the edge of and outside the defect's range
    widened by 3, and never on another path?
  - Is a defect found only when a finding on its path is judged the same failure, with the
    judge's model recorded; is location accuracy kept apart; is an unlabelled finding never
    counted as a false positive?
  - Does the expert arm combine its roster's replies of the same k, and does each expert
    count on the defects of its own category?
  - Does ``compare`` pair results case by case, and does its bootstrap interval read a
    difference smaller than the noise as no change?
"""
from __future__ import annotations

import asyncio
import json

from core.expert_bench import scoring
from core.expert_bench.scoring import Finding


def test_parse_reads_the_findings_an_expert_writes():
    reply = "\n".join([
        "Summary first, no finding here | not | one",
        "HIGH | backend/a.py:12 | the guard is inverted | flip it",
        "- **MEDIUM** | `backend/b.py:30-34` | leaks a handle | close it",
        "| LOW | /tmp/case/repo/c.py:7 | off by one | use <= |",
        "MED | d.py | no line given | x",
        "CRITICAL | e.py:9 | a | b | with | pipes",
        "UNCHECKED: the frontend",
    ])
    found = scoring.parse(reply, root="/tmp/case/repo")
    assert [(f.severity, f.path, f.line, f.end) for f in found] == [
        ("HIGH", "backend/a.py", 12, 12), ("MEDIUM", "backend/b.py", 30, 34),
        ("LOW", "c.py", 7, 7), ("MEDIUM", "d.py", None, None), ("CRITICAL", "e.py", 9, 9)]
    assert found[0].what == "the guard is inverted" and found[0].fix == "flip it"
    assert found[4].fix == "pipes" and "with" in found[4].what
    assert scoring.parse("NO FINDINGS") == [] and scoring.parse(None) == []


DEFECT = {"path": "src/a.py", "from_line": 10, "to_line": 14}


def _f(path="src/a.py", line=10, end=None):
    return Finding("HIGH", path, line, end if end is not None else line, "w", "f", "raw")


def test_match_inside_edge_and_outside_the_widened_range_and_other_path():
    assert scoring.located(_f(line=12), DEFECT)                 # inside
    assert scoring.located(_f(line=7), DEFECT)                  # from - 3: the edge
    assert scoring.located(_f(line=17), DEFECT)                 # to + 3: the edge
    assert not scoring.located(_f(line=6), DEFECT)              # from - 4
    assert not scoring.located(_f(line=18), DEFECT)             # to + 4
    assert not scoring.located(_f(path="src/b.py", line=12), DEFECT)
    assert not scoring.located(_f(line=None), DEFECT)
    assert scoring.located(_f(line=1, end=8), DEFECT)           # a range reaching the edge


def _case(cid, category="correctness", bucket_lines=50, reviewed=False):
    return {"id": cid, "diff_lines": bucket_lines, "reviewed_before": reviewed, "repo": "/r",
            "defects": [{"path": "src/a.py", "from_line": 10, "to_line": 14,
                         "category": category, "fix": "f" * 40, "note": "fix: the guard"}]}


def _row(cid, arm, expert, k, reply, cost=1.0):
    return {"case": cid, "arm": arm, "expert": expert, "k": k, "model": "m", "prompt_sha": "p",
            "reply": reply, "root": "/tmp/x", "expert_cost_usd": cost, "parent_cost_usd": 0.1,
            "expert_usage": {"input_tokens": 100, "output_tokens": 50}, "duration_ms": 1000,
            "parent_usage": {"input_tokens": 5, "output_tokens": 5}, "expert_tool_calls": 3}


def _judge(same_words):
    asked = []

    async def judge(case, defect_group, finding):
        asked.append(finding.what)
        return {"same": any(w in finding.what for w in same_words), "model": "judge-1",
                "text": "YES" if any(w in finding.what for w in same_words) else "NO", "cost_usd": 0.01}
    return judge, asked


def test_found_needs_the_judge_and_location_is_kept_apart():
    cases = [_case("c1")]
    results = [
        _row("c1", "expert", "correctness", 0, "HIGH | src/a.py:11 | the guard is inverted | x\n"
                                                 "LOW | src/z.py:1 | an unrelated nit | y"),
        _row("c1", "baseline", "baseline", 0, "HIGH | src/a.py:40 | the guard is inverted | x"),
        _row("c1", "expert", "correctness", 1, "HIGH | src/a.py:11 | style only | x"),
    ]
    judge, asked = _judge(["guard"])
    rows = asyncio.run(scoring.score(cases, results, judge=judge))
    arm = {(r["arm"], r["k"]): r for r in rows if r["expert"] is None}
    e0, b0, e1 = arm[("expert", 0)], arm[("baseline", 0)], arm[("expert", 1)]
    assert (e0["found"], e0["found_loose"], e0["located"]) == (True, True, True)
    assert (b0["found"], b0["found_loose"], b0["located"]) == (False, True, False)   # right failure, wrong place
    assert (e1["found"], e1["located"]) == (False, True)       # right place, another failure
    # only findings on the defect's path are judged; the other is unlabelled, never false
    assert "an unrelated nit" not in asked
    assert e0["unlabelled"] == 1 and "false_positives" not in e0 and "precision" not in e0
    assert e0["judge_model"] == "judge-1" and e0["category"] == "correctness"


def test_the_expert_arm_combines_its_roster_and_each_expert_counts_in_its_category():
    cases = [_case("c1", category="concurrency")]
    results = [
        _row("c1", "expert", "correctness", 0, "HIGH | src/a.py:40 | a nit | x"),
        _row("c1", "expert", "concurrency", 0, "HIGH | src/a.py:12 | the guard races | x"),
    ]
    judge, _ = _judge(["guard"])
    rows = asyncio.run(scoring.score(cases, results, judge=judge))
    [arm] = [r for r in rows if r["expert"] is None]
    assert arm["found"] is True and arm["roster"] == ["concurrency", "correctness"]
    assert arm["expert_cost_usd"] == 2.0 and arm["complete"] is True
    per = {r["expert"]: r for r in rows if r["expert"]}
    assert per["concurrency"]["found"] and per["concurrency"]["own_category"] is True
    assert not per["correctness"]["found"] and per["correctness"]["own_category"] is False


def test_a_missing_or_failed_reply_marks_the_arm_incomplete():
    cases = [_case("c1")]
    failed = dict(_row("c1", "expert", "security", 0, None), error="timed out")
    results = [_row("c1", "expert", "correctness", 0, "NO FINDINGS"), failed]
    judge, _ = _judge([])
    rows = asyncio.run(scoring.score(cases, results, judge=judge))
    [arm] = [r for r in rows if r["expert"] is None]
    assert arm["complete"] is False


def _score_rows(arm, per_case):
    """per_case: {case: [found per k]}"""
    return [{"case": c, "arm": arm, "expert": None, "k": k, "found": bool(v), "complete": True}
            for c, vals in per_case.items() for k, v in enumerate(vals)]


def test_bootstrap_interval_is_tight_on_a_constant_and_wide_on_noise():
    mean, lo, hi = scoring.bootstrap({f"c{i}": [1.0, 1.0, 1.0] for i in range(20)}, iterations=500, seed=1)
    assert (mean, lo, hi) == (1.0, 1.0, 1.0)
    noisy = {f"c{i}": [float(i % 2), float((i + 1) % 2), 1.0] for i in range(20)}
    mean, lo, hi = scoring.bootstrap(noisy, iterations=500, seed=1)
    assert lo < mean < hi
    assert scoring.bootstrap(noisy, iterations=500, seed=1) == (mean, lo, hi)   # seeded


def test_compare_pairs_cases_and_reads_noise_as_no_change():
    a = _score_rows("expert", {f"c{i}": [1, 1, 1] for i in range(20)})
    b = _score_rows("baseline", {**{f"c{i}": [0, 0, 0] for i in range(20)}, "extra": [1, 1, 1]})
    out = scoring.compare(a, b, iterations=500, seed=3)
    assert out["cases"] == 20                                      # "extra" is not in A
    assert out["diff"] == 1.0 and out["ci"] == [1.0, 1.0] and out["verdict"] == "A better"
    same = scoring.compare(a, _score_rows("x", {f"c{i}": [1, 1, 0] if i % 2 else [1, 0, 1]
                                               for i in range(20)}), iterations=500, seed=3)
    assert same["ci"][0] <= same["diff"] <= same["ci"][1]
    flat = scoring.compare(_score_rows("x", {f"c{i}": [i % 2] * 3 for i in range(20)}),
                           _score_rows("y", {f"c{i}": [(i + 1) % 2] * 3 for i in range(20)}),
                           iterations=500, seed=3)
    assert flat["diff"] == 0.0 and flat["ci"][0] < 0 < flat["ci"][1] and flat["verdict"] == "no change"


def test_incomplete_arms_are_left_out_of_recall():
    rows = _score_rows("expert", {"c1": [1, 1, 1], "c2": [0, 0, 0]})
    rows[3]["complete"] = False                      # c2, k=0
    per = scoring.per_case(rows, arm="expert")
    assert per == {"c1": [1.0, 1.0, 1.0], "c2": [0.0, 0.0]}


def test_the_cli_compare_reads_two_score_files(tmp_path, capsys):
    from scripts import expert_bench
    fa, fb = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    fa.write_text("".join(json.dumps(r) + "\n" for r in
                          _score_rows("expert", {f"c{i}": [1, 1, 1] for i in range(10)})
                          + _score_rows("baseline", {f"c{i}": [0, 1, 0] for i in range(10)})))
    fb.write_text(fa.read_text())
    assert expert_bench.main(["compare", str(fa), str(fb), "--arm-a", "expert", "--arm-b", "baseline",
                              "--iterations", "300"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["cases"] == 10 and out["a"] == 1.0 and abs(out["b"] - 1 / 3) < 1e-9


def test_nothing_in_the_pipeline_or_the_review_skill_calls_the_benchmark():
    from pathlib import Path
    backend = Path(__file__).resolve().parents[1]
    hits = []
    for root in ("core", "skills", "routers", "hooks", "scripts", "experts"):
        for p in (backend / root).rglob("*"):
            if (p.is_file() and p.suffix in (".py", ".md") and "expert_bench" not in p.parts
                    and p.name != "expert_bench.py"):
                if "expert_bench" in p.read_text(errors="replace"):
                    hits.append(str(p.relative_to(backend)))
    assert hits == []


# --- the review round's findings, one test each ---------------------------------------

def test_parse_reads_bracketed_scored_and_labelled_severities():
    reply = "\n".join(["HIGH (8/10) | a.py:1 | w1 | f1", "- [MEDIUM] | `b.py:2` | w2 | f2",
                       "Severity: LOW | c.py:3 | w3 | f3", "**HIGH** (conf 9) | d.py:4 | w4 | f4"])
    assert [(f.severity, f.path, f.line) for f in scoring.parse(reply)] == [
        ("HIGH", "a.py", 1), ("MEDIUM", "b.py", 2), ("LOW", "c.py", 3), ("HIGH", "d.py", 4)]


def test_a_path_relative_to_a_subdirectory_still_reaches_the_judge():
    cases = [{"id": "c1", "diff_lines": 10, "reviewed_before": False,
              "defects": [{"path": "backend/core/a.py", "from_line": 10, "to_line": 12,
                           "category": "correctness", "fix": "f", "note": "n"}]}]
    results = [_row("c1", "baseline", "baseline", 0,
                    "HIGH | core/a.py:11 | the guard is inverted | x\nLOW | the whole module | no path | y")]
    judge, asked = _judge(["guard"])
    [row] = [r for r in asyncio.run(scoring.score(cases, results, judge=judge)) if r["expert"] is None]
    assert row["found"] is True and asked == ["the guard is inverted"] and row["no_path"] == 1
    two = {"defects": [{"path": "x/a.py"}, {"path": "y/a.py"}]}
    assert scoring.on_paths(Finding("LOW", "a.py", 1, 1, "", "", ""), [d["path"] for d in two["defects"]]) is None


def test_results_of_two_prompt_versions_are_never_mixed():
    import pytest
    cases = [_case("c1")]
    old = _row("c1", "expert", "correctness", 0, "NO FINDINGS")
    new = dict(_row("c1", "expert", "correctness", 0, "NO FINDINGS"), prompt_sha="p2")
    judge, _ = _judge([])
    with pytest.raises(ValueError, match="prompt versions"):
        asyncio.run(scoring.score(cases, [old, new], judge=judge))
    other = dict(new, prompt_sha="p", model="m2")
    rows = asyncio.run(scoring.score(cases, [old, other], judge=judge, model="m2"))
    [arm] = [r for r in rows if r["expert"] is None]
    assert arm["models"] == ["m2"] and arm["prompt_shas"] == ["p"]


def test_an_unparsed_judge_answer_is_counted():
    cases = [_case("c1")]

    async def judge(case, defects, finding):
        return {"same": False, "parsed": False, "model": "j", "text": None}
    results = [_row("c1", "baseline", "baseline", 0, "HIGH | src/a.py:11 | x | y")]
    [row] = [r for r in asyncio.run(scoring.score(cases, results, judge=judge)) if r["expert"] is None]
    assert row["judge_unparsed"] == 1 and row["found"] is False


def test_the_cli_compare_defaults_to_expert_against_baseline(tmp_path, capsys):
    from scripts import expert_bench
    f = tmp_path / "s.jsonl"
    f.write_text("".join(json.dumps(r) + "\n" for r in
                         _score_rows("expert", {f"c{i}": [1, 1, 1] for i in range(5)})
                         + _score_rows("baseline", {f"c{i}": [0, 0, 0] for i in range(5)})))
    assert expert_bench.main(["compare", str(f), str(f), "--iterations", "100"]) == 0
    assert json.loads(capsys.readouterr().out)["diff"] == 1.0


def test_concurrent_judging_asks_each_pair_once_and_scores_the_same():
    cases = [_case("c1"), _case("c2")]
    results = [_row(c, arm, e, k, "HIGH | src/a.py:11 | the guard is inverted | x\n"
                                  "LOW | src/a.py:30 | another thing | y")
               for c in ("c1", "c2") for arm, e in (("expert", "correctness"), ("baseline", "baseline"))
               for k in (0, 1)]
    judge, asked = _judge(["guard"])
    serial = asyncio.run(scoring.score(cases, results, judge=judge))
    judge2, asked2 = _judge(["guard"])
    parallel = asyncio.run(scoring.score(cases, results, judge=judge2, concurrency=8))
    assert parallel == serial
    assert len(asked2) == 4                       # 2 cases x 2 distinct findings, each asked once


def test_a_header_only_finding_takes_its_description_from_the_lines_below():
    # "SEVERITY | path:line" alone on a line, the failure and fix written underneath: the
    # judge must see that text, or it answers NO for want of a description
    reply = "\n".join([
        "HIGH | backend/a.py:12",
        "The guard is inverted, so every caller is let through.",
        "Fix: flip the comparison.",
        "",
        "MEDIUM | b.py:3 |",
        "- **What breaks:** leaks a handle",
        "- **Fix:** close it",
        "LOW | c.py:1 | inline | ok",
        "## Unchecked",
        "HIGH | d.py:5",
        "## Next section",
    ])
    a, b, c, d = scoring.parse(reply)
    assert (a.what, a.fix) == ("The guard is inverted, so every caller is let through.",
                               "flip the comparison.")
    assert "The guard is inverted" in a.raw
    assert (b.what, b.fix) == ("leaks a handle", "close it")
    # a finding written on one line keeps its raw text, so judge answers cached under it hold
    assert (c.what, c.fix, c.raw) == ("inline", "ok", "LOW | c.py:1 | inline | ok")
    assert (d.what, d.raw) == ("", "HIGH | d.py:5")      # a heading ends the description
