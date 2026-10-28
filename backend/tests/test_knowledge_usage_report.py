"""knowledge_usage_report counts on-demand use from transcripts and runs (§5)."""
import json

from scripts import knowledge_usage_report as kur


def _line(obj):
    return json.dumps(obj) + "\n"


def test_transcript_counts_commands_reads_and_followed_hints(tmp_path):
    t = tmp_path / "s.jsonl"
    hint = {"type": "user", "message": {"content": "## Project DDD — [DDD:P] hint"}}
    call = lambda inp: {"type": "assistant", "message": {"content": [{"type": "tool_use", "input": inp}]}}
    t.write_text(_line(hint)
                 + _line(call({"command": "ls"}))
                 + _line(call({"command": '"$SWARM_BACKEND_BIN" knowledge find --project P --query x'}))
                 + _line(call({"file_path": "/w/Projects/P/2-understanding/TECH.md"}))
                 + "not json\n", encoding="utf-8")
    c = kur.scan_transcript(t)
    assert c == {"knowledge_calls": 1, "ddd_reads": 1, "hints": 1, "hints_followed": 1}


def test_runs_count_citations_and_searches(tmp_path):
    art = tmp_path / "Projects" / "P" / ".artifacts" / "runs" / "run_abc" / "artifacts"
    art.mkdir(parents=True)
    (art / "knowledge.md").write_text("x")
    (art / "design_spec.json").write_text(json.dumps(
        {"knowledge_refs": ["0a1b2c3d"], "knowledge_checked": [{"query": "q", "result_ids": []}]}))
    rows = kur.scan_runs(tmp_path, 0)
    assert rows == [{"run": "run_abc", "project": "P", "has_knowledge_md": True, "cited": 1, "searches": 1}]


def test_only_delivered_hints_count_and_writes_are_not_reads(tmp_path):
    """Correctness review, round 2: a tool_result that shows the heading (reading
    session_router.py) was counted as a hint; a cold spawn's hint lives in the system
    prompt snapshot (recorded twice) and was missed; an s_persist write counted as a
    read; ``knowledge get <path>`` counted twice."""
    t = tmp_path / "s.jsonl"
    heading = "## Project DDD — [DDD:P]"
    echo = {"type": "user", "message": {"content": [
        {"type": "tool_result", "content": f'f"\\n\\n{heading}\\n"'}]}}
    snap = {"type": "attachment", "attachment": {"type": "prompt_snapshot",
                                                 "systemPrompt": f"rules\n\n{heading}\n> hint"}}
    call = lambda name, inp: {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": name, "input": inp}]}}
    ddd = "/w/Projects/P/2-understanding/TECH.md"
    t.write_text(_line(echo) + _line(snap) + _line(snap)
                 + _line(call("Bash", {"command": f"py scripts/locked_write.py --file {ddd} --prepend x"}))
                 + _line(call("Write", {"file_path": ddd, "content": "x"}))
                 + _line(call("Bash", {"command": f'"$B" knowledge get --project P {ddd}:3'}))
                 + _line(call("Grep", {"pattern": "x", "path": ddd})), encoding="utf-8")
    assert kur.scan_transcript(t) == {"knowledge_calls": 1, "ddd_reads": 1, "hints": 1,
                                      "hints_followed": 1}
