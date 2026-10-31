"""CDF v2 whole-unit working/episodic memory injection tests."""
from __future__ import annotations

import json
from datetime import date

from core.memory_injection import select_daily_activity, select_timeline


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n")


def test_daily_admits_whole_rows_counts_overflow_and_hides_resolved(tmp_path):
    directory = tmp_path / "Knowledge" / "DailyActivity"
    rows = [
        {"session_id": "s1", "timestamp": "10:00", "title": "一", "summary_sentence": "完整摘要甲。",
         "next_items": [{"next_id": "n1", "text": "完整下一步甲"}], "resolves": []},
        {"session_id": "s2", "timestamp": "11:00", "title": "二", "summary_sentence": "完整摘要乙。",
         "next_items": [], "resolves": ["n1"]},
    ]
    _write_jsonl(directory / "2026-10-27.jsonl", rows)
    newest = "- 2026-10-27 11:00 二: 完整摘要乙。"
    result = select_daily_activity(tmp_path, today=date(2026, 10, 27), estimate=len,
                                   budget=len(newest))
    assert result.text.startswith(newest)           # newest row first, whole
    assert "完整摘要甲" not in result.text and "[n1]" not in result.text
    assert "(1 more sessions not shown; they are in Knowledge/DailyActivity/)" in result.text


def test_the_two_most_recent_days_with_rows_and_no_followup_lines(tmp_path):
    """Design 2026-10-31-continue-lookback-todo D1: the rows of the two most recent days
    that have rows (a day without rows is skipped, not counted), no Next lines; work
    left for later is a ToDo. An old day summary block is never a row."""
    directory = tmp_path / "Knowledge" / "DailyActivity"
    _write_jsonl(directory / "2026-10-27.jsonl", [
        {"session_id": "a", "timestamp": "09:00", "title": "Parser fix",
         "summary_sentence": "Fixed the blank-line crash.",
         "next_items": [{"next_id": "x1", "text": "Release 2.4"}], "resolves": [],
         "deliverables": ["a very long deliverable that must not be injected"]}])
    _write_jsonl(directory / "2026-10-24.jsonl", [
        {"session_id": "b", "timestamp": "18:00", "title": "Docs", "summary_sentence": "Wrote the guide."}])
    (directory / "2026-10-24.md").write_text("## 日汇总\n主线：旧的日汇总。\n")
    _write_jsonl(directory / "2026-10-22.jsonl", [
        {"session_id": "c", "timestamp": "08:00", "title": "Older", "summary_sentence": "Not shown."}])
    result = select_daily_activity(tmp_path, today=date(2026, 10, 27), estimate=len, budget=10_000)
    assert result.text == (
        "- 2026-10-27 09:00 Parser fix: Fixed the blank-line crash.\n"
        "- 2026-10-24 18:00 Docs: Wrote the guide."
    )


def test_a_row_without_a_summary_shows_its_title_and_never_its_topics(tmp_path):
    directory = tmp_path / "Knowledge" / "DailyActivity"
    _write_jsonl(directory / "2026-10-27.jsonl", [
        {"session_id": "a", "timestamp": "09:00", "title": "Old row",
         "topics": ["what the user typed"], "next_items": [], "resolves": []}])
    result = select_daily_activity(tmp_path, today=date(2026, 10, 27), estimate=len, budget=10_000)
    assert result.text == "- 2026-10-27 09:00 Old row"


def test_resolved_next_is_never_injected_even_with_room(tmp_path):
    directory = tmp_path / "Knowledge" / "DailyActivity"
    rows = [
        {"session_id": "s1", "timestamp": "10:00", "title": "一", "summary_sentence": "摘要甲。",
         "next_items": [{"next_id": "n1", "text": "已完成事项"}], "resolves": []},
        {"session_id": "s2", "timestamp": "11:00", "title": "二", "summary_sentence": "摘要乙。",
         "next_items": [], "resolves": ["n1"]},
    ]
    _write_jsonl(directory / "2026-10-27.jsonl", rows)
    result = select_daily_activity(tmp_path, today=date(2026, 10, 27), estimate=len, budget=10_000)
    assert "[n1]" not in result.text and "已完成事项" not in result.text


def test_timeline_summary_first_excludes_not_injected_and_keeps_units_whole(tmp_path):
    timeline = tmp_path / ".context" / "TIMELINE.md"
    timeline.parent.mkdir()
    # Fixture uses the CDF writer's real TIMELINE block format (writer._render, store=TIMELINE).
    summary = (
        "### 2026-09-30 九月汇总\n- what: 月度内容\n- outcome: 汇总\n"
        "<!-- cand:summary-9 | event_key:k9 | source:S0 | event_kind:month_summary -->"
    )
    newest = (
        "### 2026-10-27 十月事件\n- what: 完整事件正文\n- outcome: 完成\n"
        "<!-- cand:event-10 | event_key:k10 | source:S2 | event_kind:event -->"
    )
    private = (
        "### 2026-10-27 私密事件\n- what: 不应注入\n- outcome: 被污染\n"
        "<!-- cand:private | event_key:kp | source:S2 | event_kind:event | taint:external -->"
    )
    timeline.write_text(
        f"# Timeline\n\n## 2026-09\n{summary}\n\n## 2026-10\n{newest}"
        f"\n\n## Not injected\n{private}\n"
    )
    result = select_timeline(timeline, today=date(2026, 10, 27), estimate=len, budget=10_000)
    assert result.text.index("九月汇总") < result.text.index("十月事件")
    assert "私密事件" not in result.text

    from core.ingestion.units import parse_units
    parsed_summary = next(unit for unit in parse_units(timeline) if "九月汇总" in unit.text)
    shown = "### 2026-09-30 九月汇总\n- what: 月度内容\n- outcome: 汇总"
    assert len(shown) < len(parsed_summary.text)    # the unit carries a metadata comment
    small = select_timeline(timeline, today=date(2026, 10, 27), estimate=len, budget=len(shown))
    assert small.text.startswith(shown)             # counted on what the prompt shows
    assert "完整事件正文" not in small.text
    assert "(1 more events not shown; read .context/TIMELINE.md)" in small.text


def test_timeline_private_for_group_and_nonowner_prompts(tmp_path):
    from core.context_directory_loader import (
        CHANNEL_LIGHT_EXCLUDE,
        GROUP_CHANNEL_EXCLUDE,
        ContextDirectoryLoader,
    )

    context = tmp_path / ".context"
    context.mkdir()
    (context / "SWARMAI.md").write_text("# SwarmAI\npublic")
    (context / "TIMELINE.md").write_text(
        "# Timeline\n\n## 2026-10\n- [event] **秘密事件** private <!-- cand:p -->\n"
        "  <!-- ref:0 | last:2026-10-27 | decay:active -->\n"
    )
    loader = ContextDirectoryLoader(context)
    group = loader._assemble_from_sources(exclude_filenames=set(GROUP_CHANNEL_EXCLUDE))
    nonowner = loader._assemble_from_sources(exclude_filenames=set(CHANNEL_LIGHT_EXCLUDE))
    assert "秘密事件" not in group
    assert "秘密事件" not in nonowner


def test_dailyactivity_injection_remains_desktop_only():
    from pathlib import Path
    import core.prompt_builder as prompt_builder

    source = Path(prompt_builder.__file__).read_text(encoding="utf-8")
    assert "daily_activity_dir.is_dir() and not is_channel" in source


def test_owned_memory_paths_have_no_fixed_semantic_truncation():
    import ast
    from pathlib import Path
    import core.memory_injection as memory_injection
    import core.summarization as summarization
    import core.prompt_builder as prompt_builder
    import core.ingestion.summary_lane as summary_lane

    files = [
        Path(summarization.__file__), Path(prompt_builder.__file__),
        Path(memory_injection.__file__), Path(summary_lane.__file__),
    ]
    offenders = []
    semantic_functions = {
        "_extract_topics", "_extract_decisions", "_row", "_shown",
        "_format_conversation_for_llm", "select_daily_activity", "select_timeline",
        "enqueue_month_summary",
    }
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        functions = [node for node in ast.walk(tree)
                     if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and node.name in semantic_functions]
        for function in functions:
            for node in ast.walk(function):
                if isinstance(node, ast.Call):
                    name = getattr(node.func, "attr", getattr(node.func, "id", ""))
                    if name in {"shorten", "truncate", "_truncate_daily_content"}:
                        offenders.append((path.name, node.lineno, name))
                if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice):
                    bounds = (node.slice.lower, node.slice.upper)
                    if any(isinstance(bound, ast.Constant) and isinstance(bound.value, int)
                           for bound in bounds if bound is not None):
                        base = getattr(node.value, "id", "")
                        if base in {"text", "sentence", "content", "result", "topics", "decisions"}:
                            offenders.append((path.name, node.lineno, "fixed_slice"))
    assert offenders == []


def test_timeline_is_p10_whole_unit_private_and_governed(tmp_path, monkeypatch):
    from core.context_directory_loader import CONTEXT_FILES, WHOLE_FILE_PRIVATE
    from core.ingestion import materializer

    spec = next(item for item in CONTEXT_FILES if item.filename == "TIMELINE.md")
    assert spec.priority == 10
    assert spec.selector == "timeline" and spec.inject_budget_tokens == 1500
    assert spec.user_customized is True
    assert "TIMELINE.md" in WHOLE_FILE_PRIVATE

    workspace = tmp_path / "SwarmWS"
    timeline = workspace / ".context" / "TIMELINE.md"
    timeline.parent.mkdir(parents=True)
    timeline.write_text("# Timeline\n")
    monkeypatch.setattr(materializer, "_workspace_root", lambda: workspace)
    assert materializer.is_governed(timeline)


def test_timeline_template_is_copy_if_missing_and_mode_0644(tmp_path):
    import stat
    from pathlib import Path
    from core.context_directory_loader import ContextDirectoryLoader

    context = tmp_path / ".context"
    templates = Path(__file__).resolve().parents[1] / "context"
    loader = ContextDirectoryLoader(context, templates_dir=templates)
    loader.ensure_directory()
    timeline = context / "TIMELINE.md"
    original = timeline.read_text(encoding="utf-8")
    assert stat.S_IMODE(timeline.stat().st_mode) == 0o644
    timeline.write_text(original + "user event\n", encoding="utf-8")
    loader.ensure_directory()
    assert timeline.read_text(encoding="utf-8").endswith("user event\n")
