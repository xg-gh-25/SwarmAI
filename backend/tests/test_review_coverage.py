"""Review coverage from the expert's own transcript (design 2026-11-01-pipeline-
delivery-friction D2): spawn_evidence counts the distinct prompt-file lines its Reads
returned and its tool calls; review.coverage_rows turns recorded answers into rows; RV-6
(audit only) says which expert did not read its whole prompt or hit its cap; REPORT
lists them."""
import json

from core import subagent_transcript as st
from core.run_protocol import checks, review
from core.run_protocol.audit import render


def _numbered(start, n):
    return "\n".join(f"{i:>6}\tline {i}" for i in range(start, start + n))


def _transcript(tmp_path, reads, prompt, extra_calls=0, arrow=False):
    """reads: [(offset, n_lines)] Reads of *prompt*, each answered with numbered lines."""
    rows = [{"type": "user", "message": {"content": "EXPERT-ID: x\nPROMPT-FILE: p"}}]
    for i, (offset, n) in enumerate(reads):
        inp = {"file_path": prompt} | ({"offset": offset} if offset else {})
        rows.append({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": f"r{i}", "name": "Read", "input": inp}]}})
        text = _numbered(offset or 1, n)
        if arrow:
            text = text.replace("\t", "→")
        rows.append({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": f"r{i}", "content": text}]}})
    for j in range(extra_calls):
        rows.append({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": f"g{j}", "name": "Grep", "input": {"pattern": "x"}}]}})
        rows.append({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": f"g{j}", "content": "a.py:1: x"}]}})
    path = tmp_path / "t.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return str(path)


def test_paged_reads_count_the_union_of_lines(tmp_path):
    prompt = str(tmp_path / "prompt")
    t = _transcript(tmp_path, [(None, 2000), (2001, 2000), (1500, 1000)], prompt, extra_calls=3)
    ev = st.spawn_evidence(t, prompt)
    assert ev["read_ok"] is True
    assert ev["prompt_lines_read"] == 4000          # 1500-2500 overlaps the two pages
    assert ev["tool_calls"] == 6


def test_a_half_read_prompt_and_the_arrow_format(tmp_path):
    prompt = str(tmp_path / "prompt")
    ev = st.spawn_evidence(_transcript(tmp_path, [(None, 500)], prompt, arrow=True), prompt)
    assert ev["prompt_lines_read"] == 500


def test_reads_of_other_files_do_not_count(tmp_path):
    prompt = str(tmp_path / "prompt")
    ev = st.spawn_evidence(_transcript(tmp_path, [(None, 300)], str(tmp_path / "other")), prompt)
    assert ev["prompt_lines_read"] == 0 and ev["read_ok"] is False and ev["tool_calls"] == 1


def _answer(expert, rnd, lines, read, calls, cap):
    return {"kind": "answer", "stage": "review", "round": rnd,
            "payload": {"expert_id": expert, "coverage": {
                "prompt_lines": lines, "prompt_lines_read": read,
                "tool_calls": calls, "cap": cap}}}


def _ctx(events):
    # RV-6 reads only the events; the chain itself is checked by make_context elsewhere
    return checks.CheckContext("full", "full", None, {}, None, tuple(events), None, {}, {},
                               {}, frozenset(), frozenset())


def test_coverage_rows_and_rv6():
    events = [_answer("correctness", 1, 1000, 460, 30, 25),
              _answer("security", 1, 1000, 1000, 20, 25),
              {"kind": "answer", "stage": "review", "round": 1,
               "payload": {"expert_id": "relayed"}}]          # no coverage: not listed
    rows = review.coverage_rows(events)
    assert [(r["expert"], r["read_pct"], r["budget_hit"], r["complete"]) for r in rows] == [
        ("correctness", 46, True, False), ("security", 100, False, True)]
    passed, detail = checks.evaluate("RV-6", _ctx(events))
    assert not passed
    assert "correctness r1 read 46% of its prompt, stopped at its 25-call cap" in detail
    assert "security" not in detail
    ok, _ = checks.evaluate("RV-6", _ctx([_answer("security", 1, 10, 10, 3, 24)]))
    assert ok


def test_rv6_is_audit_only():
    spec = checks.CHECKS["RV-6"]
    assert spec.effects == frozenset({"audit"})
    assert "RV-6" not in {s.check_id for s in checks.stage_checks("full", "review")}


def test_report_lists_coverage_rows_and_quality_names_the_short_review():
    from core.run_protocol import audit
    answer = {**_answer("correctness", 1, 1000, 460, 30, 25), "at": "2026-11-01T00:00:10+00:00"}
    events = [{"kind": "run_create", "at": "2026-11-01T00:00:00+00:00", "payload": {}}, answer,
              {"kind": "land", "at": "2026-11-01T00:00:30+00:00", "payload": {}}]
    detail = checks.evaluate("RV-6", _ctx(events))[1]
    value = audit.build(run_id="run_x", projection={
        "run_id": "run_x", "status": "completed", "landing": "landed", "reason": None,
        "human_decisions": 0}, events=events, repos_isolated=1, findings=[],
        contract={"run_id": "run_x", "mode": "isolated", "expected_repos": ["repo"]},
        open_checks={"RV-6": detail}, audit_through="COMPLETED", commits={"repo": "a" * 40})
    text = render(value, fmt="report")
    quality = next(line for line in text.splitlines() if line.startswith("| Quality |"))
    assert quality.startswith("| Quality | WARN |") and "correctness r1 read 46%" in quality
    assert "## Review coverage" in text
    assert "correctness · review r1: read 46% of its prompt · 30/25 tool calls · stopped at the cap" in text


def test_a_whole_read_that_hit_the_cap_is_still_short():
    rows = review.coverage_rows([_answer("security", 1, 100, 100, 30, 25)])
    assert rows[0]["read_pct"] == 100 and rows[0]["budget_hit"] and not rows[0]["complete"]
    passed, detail = checks.evaluate("RV-6", _ctx([_answer("security", 1, 100, 100, 30, 25)]))
    assert not passed and "stopped at its 25-call cap" in detail
