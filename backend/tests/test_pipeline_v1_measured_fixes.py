"""Five Pipeline v1 changes that measured runs showed waste a round or lower quality.

1. A behavior / contract fix's ``reported_test`` proof carries the mutation_check result
   that reverting the fix turns the test red (``findings.mutation_summary``), or the
   reason none could be written; the audit lists that reason, nothing is refused for it.
2. A validation report row may carry ``base_result`` (the same command on
   ``pipeline-v1 base-tree``): fail there and pass on the change is a fail-to-pass test.
   A bugfix run's audit says in one line when it reported none; never a block.
3. Findings are counted and sent to R2 by cluster (one file, within 10 lines, across
   experts): one expert rechecks a cluster (a specialist such as security leads one it
   raised a finding in), every finding keeps its own text.
4. The PLAN card states the DESIGN_SPEC shapes the checks refuse, and its examples pass.
5. Every non-empty pipeline change gets a correctness review, whatever its size; the
   rubber-stamp monitor does not count a run whose only review was that one.

The rig tests run real pipeline commands (``tests/pipeline_v1_rig.py``).
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

from core.run_protocol import findings as fd
from core.run_protocol import validation_report as vr
from tests.pipeline_v1_rig import Rig, documents
from tests.test_run_protocol_findings import Stream

pytestmark = pytest.mark.timeout(400)

TEST_CMD = "python -m pytest backend/tests/test_x.py"
PLAN_CARD = (Path(__file__).resolve().parents[1] / "skills" / "s_autonomous-pipeline"
             / "stages" / "plan.md")


def _mutation(file="backend/core/mod.py", status="caught", *, path=None, timeout=False,
              **extra):
    """The JSON scripts.mutation_check prints, for one mutation of *file* (*path*: the
    absolute file it wrote, which it prints since this change)."""
    row = {"name": "take the fix out", "file": file, "status": status, "rc": 1,
           "timeout": timeout}
    if path is not None:
        row["path"] = str(path)
    return {"ok": status == "caught", "log_dir": "/tmp/x",
            "baseline": {"rc": 0, "timeout": False, "seconds": 1.0},
            "mutations": [row], "caught": int(status == "caught"),
            "missed": int(status == "missed"), "anchor": int(status == "anchor"), **extra}


# ── 1. mutation_check result on a behavior / contract proof ──────────────────────

def test_mutation_summary_accepts_only_a_clean_result_over_changed_files(tmp_path):
    changed = ["backend/core/mod.py", "backend/tests/test_x.py", "other/mod.py"]
    tests = ["backend/tests/test_x.py"]
    summary, problem = fd.mutation_summary(_mutation(), changed, tests=tests)
    assert problem is None and summary == {"caught": 1, "files": ["backend/core/mod.py"]}
    # the absolute path mutation_check prints, whatever its --cwd, inside the worktree
    root = tmp_path / "wt"
    (root / "backend" / "core").mkdir(parents=True)
    inside = _mutation("core/mod.py", path=root / "backend" / "core" / "mod.py")
    summary, problem = fd.mutation_summary(inside, changed, tests=tests, root=str(root))
    assert problem is None and summary["files"] == ["backend/core/mod.py"]
    bad = {
        "missed": _mutation(status="missed"),
        "anchor": _mutation(status="anchor"),
        "red baseline": {**_mutation(), "baseline": {"rc": 1}},
        "no baseline (--no-baseline)": {k: v for k, v in _mutation().items() if k != "baseline"},
        "a run that hit the time cap": _mutation(timeout=True),
        "error": {"ok": False, "error": "baseline is not green: nothing was mutated"},
        "nothing ran": {**_mutation(), "mutations": []},
        "not the run's file": _mutation("backend/core/unchanged.py"),
        "the test itself": _mutation("backend/tests/test_x.py"),
        "a bare file name": _mutation("mod.py"),
        "relative to another cwd, no path": _mutation("core/mod.py"),
        "outside the repo": _mutation("../core/mod.py"),
        "another checkout": _mutation("core/mod.py", path=tmp_path / "main" / "core" / "mod.py"),
        "not an object": "caught",
    }
    for label, result in bad.items():
        summary, problem = fd.mutation_summary(result, changed, tests=tests, root=str(root))
        assert summary is None and problem, label
    assert "does not detect the fix" in fd.mutation_summary(
        _mutation(status="missed"), changed, tests=tests)[1]
    assert "not in a file this run changed" in fd.mutation_summary(
        _mutation("backend/core/unchanged.py"), changed, tests=tests)[1]
    assert "not the test" in fd.mutation_summary(
        _mutation("backend/tests/test_x.py"), changed, tests=tests)[1]
    assert "baseline" in fd.mutation_summary(bad["no baseline (--no-baseline)"], changed)[1]
    assert "time cap" in fd.mutation_summary(_mutation(timeout=True), changed)[1]


BEHAVIOR = {"severity": "HIGH", "kind": "behavior", "file": "backend/core/mod.py", "line": 2,
            "claim": "operands swapped", "fix": "restore order"}
SECOND = {"severity": "MEDIUM", "kind": "behavior", "file": "backend/core/mod.py", "line": 30,
          "claim": "f9 returns the wrong value", "fix": "return 9"}


@pytest.fixture
def rig(tmp_path, monkeypatch):
    r = Rig(tmp_path / "case", monkeypatch, profile="direct")
    yield r
    if r.run_id:
        assert r.v1("status").get("ok") is True, "the event log no longer folds"
    r.close()


def _change() -> str:
    return "def add(a, b):\n    return b + a\n" + "".join(
        f"\n\ndef f{i}():\n    return {i}\n" for i in range(15))


def _at_review(r: Rig, answers: list[str], *, risk: bool = False) -> dict:
    """A direct run in REVIEW whose R1 answers are *answers* (one per handout, in order),
    the fix applied and its tests reported. Returns {claim: canonical id}."""
    r.CHANGE = {"backend/core/mod.py": _change()}
    r.create()
    r.drive(until="build")
    r.edit(r.CHANGE)
    r._edited = True
    report = r.report()
    if risk:
        report["reports"][0]["risk"] = ["security"]
    out = r.v1("stage-complete", "--artifact", r.artifact("build", report))
    assert out.get("ok") is True and r.state()["stage"] == "review", out
    started = r.v1("review-start")
    handouts = started["handouts"]
    assert len(handouts) == len(answers), started["reviewers"]
    for i, handout in enumerate(handouts):
        r.spawn_reviewer(handout["prompt"], answers[i], f"r1-{i}")
    r.edit({"backend/core/mod.py": _change().replace("b + a", "a + b")})
    r.v1("report-tests", "--artifact", r.artifact("fix", r.report()))
    from core.run_protocol import review
    engine = r.engine()
    rows = review.ReviewOrchestrator(engine.store, engine.run_dir).findings()
    return {row["claim"]: row["canonical_id"] for row in rows}


def _proof(**extra) -> dict:
    return {"evidence_type": "reported_test", "command": TEST_CMD, **extra}


def test_review_address_refuses_a_proof_without_a_clean_mutation(rig):
    r = rig
    ids = _at_review(r, ["\n".join(json.dumps(f) for f in (BEHAVIOR,))])
    cid = ids["operands swapped"]
    # a test the run changed that its report does not list as changed
    r.edit({"backend/tests/test_other.py": "from core.other import other\n\n\n"
            "def test_other():\n    assert other() == 1  # checked\n",
            "backend/core/mod_test.py": "from core.mod import add\n\n\n"
            "def test_add_here():\n    assert add(2, 2) == 4\n"})
    r.v1("report-tests", "--artifact", r.artifact("fix2", r.report()))
    before = [e["event_id"] for e in r.events()]
    cases = {
        "an unlisted changed test": (_proof(mutation=_mutation("backend/tests/test_other.py")),
                                     "not the test"),
        "an unlisted test outside tests/": (_proof(mutation=_mutation("backend/core/mod_test.py")),
                                            "not the test"),
        "neither": (_proof(), "mutation_check"),
        "missed": (_proof(mutation=_mutation(status="missed")), "does not detect the fix"),
        "anchor": (_proof(mutation=_mutation(status="anchor")), "anchor"),
        "outside the change": (_proof(mutation=_mutation("backend/core/other.py")),
                               "not in a file this run changed"),
        "both": (_proof(mutation=_mutation(), no_mutation="x"), "either"),
        "empty reason": (_proof(no_mutation="  "), "no_mutation must be the reason"),
    }
    for label, (proof, words) in cases.items():
        out = r.v1("review-address", "--artifact", r.artifact(label.replace(" ", "_"), {
            "pass": 1, "items": [{"finding_id": cid, "hunk": "@@ return a + b",
                                  "proof": proof}]}))
        assert out.get("ok") is False, (label, out)
        assert cid in out["error"] and words in out["error"], (label, out["error"])
        assert [e["event_id"] for e in r.events()] == before, label


def test_review_address_records_the_mutation_and_the_audit_the_reason(rig):
    r = rig
    answer = "\n".join(json.dumps(f) for f in (BEHAVIOR, SECOND))
    ids = _at_review(r, [answer])
    first, second = ids["operands swapped"], ids["f9 returns the wrong value"]
    out = r.v1("review-address", "--artifact", r.artifact("ok", {"pass": 1, "items": [
        {"finding_id": first, "hunk": "@@ return a + b", "proof": _proof(mutation=_mutation())},
        {"finding_id": second, "hunk": "@@ f9",
         "proof": _proof(no_mutation="the fix deletes a line; no anchor stays")}]}))
    assert out.get("ok") is True, out
    proofs = {e["payload"]["canonical_id"]: e["payload"] for e in r.events()
              if e["kind"] == "proof"}
    assert proofs[first]["mutation"] == {"caught": 1, "files": ["backend/core/mod.py"]}
    assert "no_mutation" not in proofs[first]
    assert proofs[second]["no_mutation"] == "the fix deletes a line; no anchor stays"
    # R2 sees the mutation result with the proof it judges
    prompt = Path(out["handouts"][0]["path"]).read_text(encoding="utf-8")
    assert '"caught": 1' in prompt or '\\"caught\\": 1' in prompt, prompt[-2000:]
    from core.run_protocol import review
    engine = r.engine()
    rows = review.ReviewOrchestrator(engine.store, engine.run_dir).findings()
    by_id = {row["canonical_id"]: row for row in rows}
    assert by_id[first]["mutation"]["caught"] == 1
    assert by_id[second]["no_mutation"].startswith("the fix deletes")
    from tests.test_reflect_and_audit_generic import _audit
    value = _audit(findings=rows)
    quality = value["summary"]["quality"]
    line = f"{second}: no mutation check (the fix deletes a line; no anchor stays)"
    assert line in quality["open"], quality
    assert not any(first in text for text in quality["open"]), quality
    assert quality["verdict"] != "BLOCK"


# ── 2. fail-to-pass on a bugfix run ──────────────────────────────────────────────

def _report(*rows):
    return {"reports": [{"repo": "repo", "commands": list(rows), "tests_changed": [],
                         "rationale": "why"}]}


def test_base_result_counts_fail_to_pass_rows():
    ftp = {"command": "pytest t.py::test_bug", "cwd": ".", "result": "pass", "base_result": "fail"}
    both_green = {"command": "pytest t.py", "cwd": ".", "result": "pass", "base_result": "pass"}
    (report,) = vr.parse(_report(ftp, both_green), ["repo"])
    assert report["commands"][0]["base_result"] == "fail"
    payload = vr.event_payload("build", report, "1" * 40)
    assert payload["n_fail_to_pass"] == 1
    (report,) = vr.parse(_report(both_green), ["repo"])
    assert "n_fail_to_pass" not in vr.event_payload("build", report, "1" * 40)
    with pytest.raises(vr.ReportError, match="base_result"):
        vr.parse(_report({**ftp, "base_result": "red"}), ["repo"])
    # the event schema takes the count
    stream = Stream()
    event = stream.add("validation_report", {**vr.event_payload(
        "build", vr.parse(_report(ftp), ["repo"])[0], "1" * 40)})
    assert vr.fail_to_pass([event]) == 1 and vr.fail_to_pass([]) == 0


def _bugfix_audit(profile: str, n_fail_to_pass: int | None):
    from core.run_protocol.audit import build
    payload = {"stage": "test", "repo": "repo", "code_tree": "1" * 40,
               "n_pass": 1, "n_fail": 0, "n_not_run": 0}
    if n_fail_to_pass:
        payload["n_fail_to_pass"] = n_fail_to_pass
    events = [{"kind": "run_create", "at": "2026-10-22T00:00:00+00:00", "payload": {}},
              {"kind": "validation_report", "at": "2026-10-22T00:00:20+00:00",
               "payload": payload}]
    return build(run_id="run_x", projection={
        "run_id": "run_x", "status": "completed", "landing": "landed", "reason": None,
        "human_decisions": 0, "profile": profile}, events=events, repos_isolated=1,
        contract={"run_id": "run_x", "mode": "isolated", "expected_repos": ["repo"],
                  "profile": profile},
        open_checks={}, audit_through="COMPLETED", commits={"repo": "a" * 40}, findings=[])


def test_bugfix_audit_says_when_no_fail_to_pass_test_was_reported():
    from core.run_protocol.audit import NO_FAIL_TO_PASS, render
    quality = _bugfix_audit("bugfix", None)["summary"]["quality"]
    assert quality["open"].count(NO_FAIL_TO_PASS) == 1, quality
    assert quality["fail_to_pass"] == 0 and quality["verdict"] == "WARN", quality
    value = _bugfix_audit("bugfix", 2)
    quality = value["summary"]["quality"]
    assert NO_FAIL_TO_PASS not in quality["open"] and quality["fail_to_pass"] == 2
    assert quality["verdict"] == "PASS", quality
    assert "2 fail-to-pass" in render(value, fmt="report")
    quality = _bugfix_audit("full", None)["summary"]["quality"]
    assert NO_FAIL_TO_PASS not in quality["open"] and "fail_to_pass" not in quality
    assert quality["verdict"] == "PASS", quality


def test_a_bugfix_run_without_a_fail_to_pass_test_still_lands(tmp_path, monkeypatch):
    """Late stages stay lenient: the missing test is one audit line, never a block."""
    from core.run_protocol.audit import NO_FAIL_TO_PASS
    r = Rig(tmp_path / "bugfix", monkeypatch, profile="bugfix")
    try:
        r.create()
        state = r.drive()
        assert state["status"] == "completed" and state["landing"] == "landed", state
        audit = json.loads((r.engine().run_dir / "run_audit.json").read_text(encoding="utf-8"))
        assert NO_FAIL_TO_PASS in audit["summary"]["quality"]["open"], audit["summary"]
    finally:
        r.close()


# ── 3. counting and R2 routing by cluster ────────────────────────────────────────

def _row(cid, expert, file, line, severity="HIGH", status="open", r2=None, kind="behavior"):
    return {"canonical_id": cid, "expert": expert, "file": file, "line": line,
            "severity": severity, "status": status, "r2_status": r2, "kind": kind,
            "claim": f"{expert} says {cid}"}


def test_audit_counts_findings_by_cluster():
    from core.run_protocol.audit import render
    from tests.test_reflect_and_audit_generic import _audit
    rows = [
        # correctness and security report one problem at one place, both closed
        _row("CF-1", "correctness", "a.py", 10, status="closed_proven", r2="ADEQUATE"),
        _row("CF-2", "security", "a.py", 12, status="closed_asserted", kind="docs"),
        # a pair with one finding open: the cluster is open
        _row("CF-3", "correctness", "b.py", 50, severity="LOW"),
        _row("CF-4", "security", "b.py", 55, status="refuted", r2="REFUTED"),
        _row("CF-5", "correctness", "c.py", 1, status="closed_asserted", kind="docs"),
        _row("CF-6", "correctness", "c.py", 100, severity="LOW"),
        _row("CF-7", "security", "d.py", 1, severity="LOW"),
    ]
    assert fd.cluster_counts(rows) == {"total": 5, "closed": 2, "confirmed": 1, "reported": 7}
    value = _audit(findings=rows)
    quality = value["summary"]["quality"]
    assert (quality["findings_closed"], quality["findings_total"],
            quality["findings_reported"]) == (2, 5, 7)
    assert quality["findings_confirmed"] == 1          # clusters, the same unit as total
    assert "2/5 findings closed (7 reported" in render(value, fmt="report")


def test_r2_routes_send_a_cross_expert_cluster_to_one_expert():
    rows = [
        _row("CF-a1", "correctness", "a.py", 10, severity="MEDIUM"),
        _row("CF-a2", "security", "a.py", 14, severity="HIGH"),      # the more severe
        _row("CF-b1", "correctness", "b.py", 5),
        _row("CF-b2", "security", "b.py", 9),
        _row("CF-b3", "correctness", "b.py", 12, severity="LOW"),   # tie: most findings
        _row("CF-c1", "security", "c.py", 1),
        _row("CF-d1", "correctness", "d.py", 1),                     # not addressed
    ]
    addressed = {"CF-a1", "CF-a2", "CF-b1", "CF-b2", "CF-b3", "CF-c1"}
    # a specialist leads a cluster it raised a finding in, whatever the severities: a
    # security finding is never judged without the security instructions
    assert fd.r2_routes(rows, addressed) == {
        "security": ["CF-a1", "CF-a2", "CF-b1", "CF-b2", "CF-b3", "CF-c1"]}
    assert fd.r2_experts(rows, addressed) == ["security"]
    # security leads any cluster it is in, even below another specialist's severity and
    # count; other specialists before correctness; within a rank severity, count, name
    ux = [_row("CF-1", "ux", "x.py", 1, severity="LOW"), _row("CF-2", "security", "x.py", 2),
          _row("CF-3", "ux", "y.py", 1, severity="CRITICAL"), _row("CF-4", "ux", "y.py", 2),
          _row("CF-5", "security", "y.py", 3, severity="LOW"),
          _row("CF-6", "correctness", "z.py", 1, severity="CRITICAL"),
          _row("CF-7", "ux", "z.py", 3, severity="LOW"),
          _row("CF-8", "correctness", "w.py", 1), _row("CF-9", "correctness", "w.py", 3),
          _row("CF-10", "performance", "v.py", 1), _row("CF-11", "ux", "v.py", 2,
                                                          severity="CRITICAL")]
    assert fd.r2_routes(ux, {row["canonical_id"] for row in ux}) == {
        "correctness": ["CF-8", "CF-9"],
        "security": ["CF-1", "CF-2", "CF-3", "CF-4", "CF-5"],
        "ux": ["CF-10", "CF-11", "CF-6", "CF-7"]}
    tie = [_row("CF-1", "ux", "x.py", 1), _row("CF-2", "security", "x.py", 2)]
    assert fd.r2_routes(tie, {"CF-1", "CF-2"}) == {"security": ["CF-1", "CF-2"]}
    with pytest.raises(fd.FindingError):
        fd.r2_routes(rows, {"CF-nope"})


def test_a_duplicate_pair_is_rechecked_once_by_one_expert(rig):
    """End to end: correctness and security report the same problem at the same line;
    the fixing pass yields one R2 handout listing both, with both experts' text, and
    that expert's status lines close both."""
    r = rig
    mine = {**BEHAVIOR, "claim": "correctness: operands swapped"}
    theirs = {**BEHAVIOR, "line": 2, "claim": "security: operands swapped"}
    ids = _at_review(r, [json.dumps(mine), json.dumps(theirs)], risk=True)
    a, b = ids["correctness: operands swapped"], ids["security: operands swapped"]
    out = r.v1("review-address", "--artifact", r.artifact("pass", {"pass": 1, "items": [
        {"finding_id": cid, "hunk": "@@ return a + b", "proof": _proof(mutation=_mutation())}
        for cid in (a, b)]}))
    assert out.get("ok") is True, out
    assert out["reviewers"] == ["security"], out["reviewers"]      # the specialist leads
    # every R1 dispatch records why the roster picked it (the rubber-stamp monitor reads it)
    r1 = [e for e in r.events() if e["kind"] == "handout" and e.get("round") == 1]
    assert {e["payload"]["expert_id"] for e in r1} == {"correctness", "security"}
    assert all(e["payload"].get("reason") for e in r1), r1
    from core.run_protocol.audit import _experts
    rows = _experts(r.events())
    assert {row["expert"]: row["reason"] for row in rows if row["round"] == 1} == {
        e["payload"]["expert_id"]: e["payload"]["reason"] for e in r1}
    assert all("reason" not in row for row in rows if row["round"] == 2)
    (handout,) = out["handouts"]
    assert sorted(handout["event"]["payload"]["finding_ids"]) == sorted([a, b])
    prompt = Path(handout["path"]).read_text(encoding="utf-8")
    assert "correctness: operands swapped" in prompt and "security: operands swapped" in prompt
    assert "raised by another expert" in prompt
    answer = "\n".join(json.dumps({"finding_id": cid, "status": "ADEQUATE",
                                   "kind_confirm": None, "note": "red without the fix"})
                       for cid in (a, b))
    assert r.spawn_reviewer(handout["prompt"], answer, "r2-0")
    assert "correctness: operands swapped" in prompt
    from core.run_protocol import review
    engine = r.engine()
    rows = {row["canonical_id"]: row
            for row in review.ReviewOrchestrator(engine.store, engine.run_dir).findings()}
    assert rows[a]["r2_status"] == rows[b]["r2_status"] == "ADEQUATE", rows
    assert fd.cluster_counts(list(rows.values())) == {
        "total": 1, "closed": 1, "confirmed": 1, "reported": 2}
    text = (engine.run_dir / "REVIEW.md").read_text(encoding="utf-8")
    assert "| Cluster | Expert |" in text and "| correctness |" in text


def _r2(stream: Stream, expert: str, ids: list[str]):
    attempt = 1 + sum(1 for row in stream.rows if row["kind"] == "handout"
                      and row.get("round") == 2 and row["payload"]["expert_id"] == expert)
    handout = stream.add("handout", {"expert_id": expert, "prompt_sha": "0" * 64,
                                     "finding_ids": ids}, stage="review", round=2,
                         attempt=attempt)
    return stream.add("answer", {
        "handout_event_id": handout["event_id"], "prompt_sha": "0" * 64,
        "tool_use_id": f"tool-{stream.ordinal}", "expert_id": expert, "stage": "review",
        "round": 2, "state": "parsed"})


def _status(stream: Stream, answer, cid: str, status: str = "RESOLVED"):
    return stream.add("finding_status", {"canonical_id": cid, "status": status,
                                         "answer_event_id": answer["event_id"]},
                      producer="hook", round=2)


def _cross_expert_stream(*, foreign_status: bool) -> tuple[Stream, dict[str, str]]:
    stream = Stream()
    ids = {"mine": "CF-" + stream.finding(expert="correctness", line=10)["event_id"][:8],
           "theirs": "CF-" + stream.finding(expert="security", line=12,
                                            claim="same")["event_id"][:8],
           "far": "CF-" + stream.finding(expert="security", line=90,
                                         claim="else")["event_id"][:8]}
    answer = _r2(stream, "correctness", [ids["mine"], ids["theirs"]])
    _status(stream, answer, ids["mine"])
    _status(stream, answer, ids["theirs"])
    # far is in security's own R2 handout, not in the one correctness answered
    _r2(stream, "security", [ids["far"]])
    if foreign_status:
        _status(stream, answer, ids["far"])
    return stream, ids


def test_fold_takes_a_status_only_from_a_handout_that_listed_the_finding():
    stream, ids = _cross_expert_stream(foreign_status=False)
    rows = {row["canonical_id"]: row for row in stream.projected()}
    assert rows[ids["theirs"]]["r2_status"] == "RESOLVED"
    assert rows[ids["mine"]]["r2_status"] == "RESOLVED"
    bad, _ = _cross_expert_stream(foreign_status=True)
    with pytest.raises(fd.FindingError, match="listed the finding"):
        bad.projected()
    # a log from before cluster routing: an expert's own finding, listed in another of
    # its R2 handouts, still folds
    legacy = Stream()
    own = "CF-" + legacy.finding(expert="correctness", line=10)["event_id"][:8]
    other = "CF-" + legacy.finding(expert="correctness", line=80, claim="b")["event_id"][:8]
    _r2(legacy, "correctness", [own])
    later = _r2(legacy, "correctness", [other])
    _status(legacy, later, own)
    assert {row["canonical_id"]: row for row in legacy.projected()}[own]["r2_status"] \
        == "RESOLVED"


# ── 4. the PLAN card states the shapes the checks refuse ─────────────────────────

def _issues(requirements, design, profile="full"):
    from core.run_protocol import schemas
    return {issue.check_id for issue in schemas.validate_contract(
        requirements, design, requirement_variant="full", design_variant="full",
        profile=profile)}


def test_plan_card_states_the_shapes_the_checks_refuse():
    from core.run_protocol import schemas
    card = PLAN_CARD.read_text(encoding="utf-8")
    for words in ("design.flow", "design.states", '"name"', '"created_by"', '"removed_by"',
                  '"negative": true', '"universal": true', '"kind": "test"',
                  "creates_test_files", "bugfix.unchanged", "fail-to-pass",
                  "DS-1", "DS-4a", "DS-6", "DS-9a"):
        assert words in card, words
    # the card's examples pass the validators that enforce them
    flow = re.search(r"e\.g\. `([^`]+->[^`]+)`", card).group(1)
    assert schemas._has_explicit_failure_edge(flow), flow
    state = json.loads(re.search(r"e\.g\. `(\{\"name\"[^`]+\})`", card).group(1))
    docs = documents("full")
    design = {**docs["plan"], "design": {**docs["plan"]["design"], "flow": flow,
                                         "states": [state]}}
    assert _issues(docs["evaluate"], design) == set()
    # breaking each shape the card names is refused by the check it names
    broken = copy.deepcopy(design)
    broken["design"]["flow"] = "call -> validate; valid -> sum"
    assert "DS-6" in _issues(docs["evaluate"], broken)
    broken = copy.deepcopy(design)
    broken["design"]["states"] = []
    assert "DS-6" in _issues(docs["evaluate"], broken)
    requirements = copy.deepcopy(docs["evaluate"])
    requirements["requirements"][0]["acceptance"][0]["negative"] = True
    manual = copy.deepcopy(design)
    manual["exit_criteria"] = [{"id": "X1", "kind": "manual", "ref": "read it",
                                "covers": ["R1.1"]}]
    assert "DS-9a" in _issues(requirements, manual)
    bugfix = documents("bugfix")
    no_regression = copy.deepcopy(bugfix["plan"])
    no_regression["exit_criteria"] = [row for row in no_regression["exit_criteria"]
                                      if "U1" not in row["covers"]]
    assert "DS-4a" in _issues(bugfix["evaluate"], no_regression, profile="bugfix")
    uncovered = copy.deepcopy(design)
    uncovered["tasks"][0]["requirements"] = []
    assert "DS-1" in _issues(docs["evaluate"], uncovered)


# ── 5. correctness for every pipeline change ─────────────────────────────────────

def test_correctness_reviews_every_non_empty_change():
    from core.run_protocol.review import reviewers
    for changed in ([{"path": "backend/core/a.py", "insertions": 2, "deletions": 0}],
                    [{"path": "docs/x.md", "insertions": 60, "deletions": 0}],
                    [{"path": "backend/tests/test_a.py", "insertions": 1, "deletions": 0,
                      "is_test": True}]):
        picked = reviewers(profile="direct", changed=changed)
        assert [row["id"] for row in picked] == ["correctness"], changed
        assert "whatever its size" in picked[0]["reason"]
    large = reviewers(profile="full", changed=[
        {"path": "backend/core/a.py", "insertions": 80, "deletions": 0}])
    assert large == [{"id": "correctness", "reason": "changeset >= 50 source lines"}]
    small_risk = reviewers(profile="direct", security=["declared"], changed=[
        {"path": "backend/auth.py", "insertions": 15, "deletions": 0}])
    assert [row["id"] for row in small_risk] == ["correctness", "security"]
    assert reviewers(profile="full", changed=[]) == []


def test_the_size_free_correctness_review_does_not_count_for_the_rubber_stamp_alarm(tmp_path):
    """Every change gets correctness now; a small change's 0 findings is expected, so a
    run whose only review was that one is not counted (adversarial_meta)."""
    from core.adversarial_meta import check_adversarial_health
    from core.run_protocol.review import ALWAYS_REASON
    runs = tmp_path / ".artifacts" / "runs"
    for i, reason in enumerate([ALWAYS_REASON] * 3 + ["changeset >= 50 source lines"]):
        run = runs / f"run_00{i}"
        run.mkdir(parents=True)
        (run / "run.json").write_text(json.dumps({
            "id": run.name, "status": "completed", "created_at": f"2026-10-01T00:00:0{i}Z"}))
        (run / "run_audit.json").write_text(json.dumps({"details": {"findings": [], "experts": [
            {"expert": "correctness", "stage": "review", "round": 1, "reason": reason},
            {"expert": "correctness", "stage": "review", "round": 2}]}}))
    result = check_adversarial_health(tmp_path / ".artifacts")
    assert [row["run_id"] for row in result["stats"]] == ["run_003"]
    assert result["degradation_warning"] is False


def test_mutation_check_prints_the_absolute_path_it_wrote(tmp_path):
    """The proof check resolves ``path`` against the run's worktree (mutation_summary)."""
    import sys
    from scripts import mutation_check
    target = tmp_path / "pkg" / "fix.py"
    target.parent.mkdir()
    target.write_text("VALUE = 1\n", encoding="utf-8")
    out = mutation_check.check([{"file": "pkg/fix.py", "name": "drop the fix",
                                 "old": "VALUE = 1", "new": "VALUE = 2"}],
                               [sys.executable, "-c", "raise SystemExit(1)"], tmp_path,
                               30, baseline=False)
    (entry,) = out["mutations"]
    assert entry["status"] == "caught" and entry["path"] == str(target.resolve())
    assert target.read_text(encoding="utf-8") == "VALUE = 1\n"



def test_one_test_path_rule_for_any_language():
    """doc_paths.is_test_path: the roster's is_test and the mutation proof's test rule."""
    from core.doc_paths import is_test_path
    for path in ("backend/tests/test_x.py", "tests/a.py", "desktop/src/__tests__/x.test.ts",
                 "web/app.spec.js", "pkg/handler_test.go", "lib/thing_spec.rb",
                 "jobs/adapters/test_http_client.py", "src/test/java/FooTest.java"):
        assert is_test_path(path), path
    for path in ("backend/core/mod.py", "desktop/src/App.tsx", "contest.py", "latest/x.py",
                 "", "attestation_test_helpers/x.py"):
        assert not is_test_path(path), path
