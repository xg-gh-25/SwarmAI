"""Binds the frontend step-label adapter to the backend summary SSOT.

The desktop chat turns each tool_use's ``summary`` string (produced by
``core.tool_summarizer.summarize_tool_use``) into a verb + object for display
(``desktop/src/pages/chat/turnView.ts::stepLabel``). That adapter parses the
summary by prefix, so the two sides are one contract living in two languages.

This test keeps them bound: a committed fixture
(``desktop/src/pages/chat/__fixtures__/tool-summaries.json``) holds the EXACT
summaries the backend produces for one representative input per tool category,
and the frontend contract test (``turnView.contract.test.ts``) asserts every one
of them maps to its expected verb. If the backend wording changes, THIS test goes
red until the fixture is regenerated — and then the frontend test proves (or
refutes) that the adapter still understands the new wording.

Regenerate after an intentional backend change:
    SWARMAI_REGEN_TOOL_SUMMARY_FIXTURE=1 pytest tests/test_tool_summary_fixture.py
"""

import json
import os
from pathlib import Path

from core.tool_summarizer import get_tool_category, summarize_tool_use

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "desktop" / "src" / "pages" / "chat" / "__fixtures__" / "tool-summaries.json"
)

# (tool name, input, verb the frontend must show). One per backend category,
# plus the shapes the adapter special-cases (cd-prefixed Bash, grep, cat, MCP).
CASES = [
    ("Bash", {"command": "cd /Users/x/repo && grep -n \"needle\" src/a.ts"}, "search"),
    ("Bash", {"command": "cat desktop/src/pages/chat/turnView.ts | head -40"}, "read"),
    ("Bash", {"command": "ls -la desktop/src"}, "list"),
    ("Bash", {"command": "git log --oneline -5"}, "run"),
    ("Read", {"file_path": "/Users/x/repo/backend/core/tool_summarizer.py"}, "read"),
    ("Write", {"file_path": "/Users/x/repo/desktop/src/a.ts"}, "edit"),
    ("Edit", {"file_path": "/Users/x/repo/desktop/src/b.ts"}, "edit"),
    ("Grep", {"pattern": "useState"}, "search"),
    ("Glob", {"pattern": "**/*.tsx"}, "search"),
    ("WebFetch", {"url": "https://example.com/docs/page"}, "fetch"),
    ("WebSearch", {"query": "claude opus release notes"}, "websearch"),
    ("ToolSearch", {"query": "select:Bash,Read"}, "loadTools"),
    ("Skill", {"skill": "s_pdf"}, "skill"),
    ("LS", {"path": "/Users/x/repo/desktop"}, "list"),
    ("TodoWrite", {"todos": [{"content": "a"}, {"content": "b"}]}, "todo"),
    ("Agent", {"task_description": "inventory streaming surfaces"}, "agent"),
    ("Task", {"prompt": "review the diff for correctness", "name": "reviewer"}, "agent"),
    ("mcp__coe__search_coe", {"query": "lambda timeout"}, "mcp"),
    ("SomethingNew", {"name": "x"}, "tool"),
    # Branch coverage the adapter depends on (Gate-2 API-contract review):
    ("Bash", {}, "tool"),                                                    # "Using Bash" fallback
    ("TodoWrite", {"todos": [{"content": "a"}], "file_path": "/x/tasks.md"}, "todo"),
    ("Agent", {"name": "reviewer"}, "agent"),                                # name-only
    ("mcp__coe__search_coe", {}, "mcp"),                                    # MCP, no context
    ("mcp__fs__read_file", {"path": "/x/a.md"}, "mcp"),                     # MCP whose last token is a category
    ("Read", {"file_path": "/x/" + "d" * 220 + "/file.ts"}, "read"),         # truncated at 200 chars
]


def _build() -> list[dict]:
    return [
        {
            "name": name,
            "category": get_tool_category(name),
            "summary": summarize_tool_use(name, data),
            "expectedVerb": verb,
        }
        for name, data, verb in CASES
    ]


def test_fixture_matches_backend_summaries():
    current = _build()
    if os.environ.get("SWARMAI_REGEN_TOOL_SUMMARY_FIXTURE") == "1":
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert FIXTURE.exists(), f"missing fixture {FIXTURE} — regenerate (see module docstring)"
    committed = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert committed == current, (
        "backend tool summaries changed — regenerate the fixture and re-run the "
        "frontend turnView.contract test (see module docstring)"
    )


def test_every_backend_category_is_covered():
    # Derived from the summarizer's OWN category table, so a category added there
    # without a fixture case turns this red instead of silently skipping the contract.
    from core.tool_summarizer import _CATEGORY_SETS

    expected = {category for category, _ in _CATEGORY_SETS} | {"agent", "fallback"}
    covered = {get_tool_category(name) for name, _, _ in CASES}
    assert expected <= covered, sorted(expected - covered)
