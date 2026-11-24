"""The shipped KNOWLEDGE / EVOLUTION / SELF templates agree with the code and each other.

Each test reads what the code does (the retention hook's behaviour, the CDF writer's
EVOLUTION sections) and checks that the template says the same, so a later change on
either side fails here instead of shipping a template that tells every new install
something false.
"""
import re
from datetime import datetime, timedelta
from pathlib import Path

from core.ingestion import writer
from hooks.context_health_hook import ContextHealthHook

CTX = Path(__file__).resolve().parents[1] / "context"


def _read(name: str) -> str:
    return (CTX / name).read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    match = re.search(rf"^## {re.escape(heading)}.*?(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    assert match, f"no '## {heading}' section"
    return match.group(0)


def _day(age: int) -> str:
    return (datetime.now() - timedelta(days=age)).strftime("%Y-%m-%d")


def test_knowledge_md_archives_retention_matches_the_hook(tmp_path):
    archives = _section(_read("KNOWLEDGE.md"), "Archives")
    stated = [int(n) for n in re.findall(r"(\d+) days", archives)]
    assert len(stated) == 2, f"the Archives entry should state a move age and a delete age: {stated}"
    move_after, delete_after = stated

    da = tmp_path / "Knowledge" / "DailyActivity"
    arc = tmp_path / "Knowledge" / "Archives"
    da.mkdir(parents=True)
    (arc / "Signals").mkdir(parents=True)
    old_da, young_da = f"{_day(move_after + 2)}.md", f"{_day(move_after - 2)}.md"
    old_arc, young_arc = f"{_day(delete_after + 2)}.md", f"{_day(delete_after - 2)}.md"
    for name in (old_da, young_da):
        (da / name).write_text("x", encoding="utf-8")
    for name in (young_arc, "MEMORY-archive-2001-01.md"):
        (arc / name).write_text("x", encoding="utf-8")
    (arc / old_arc).write_text("x", encoding="utf-8")
    (arc / "Signals" / f"{_day(delete_after + 2)}.md").write_text("x", encoding="utf-8")

    ContextHealthHook()._enforce_retention_policies(str(tmp_path))

    assert (arc / old_da).exists() and not (da / old_da).exists()   # moved after move_after days
    assert (da / young_da).exists()
    assert not (arc / old_arc).exists()                               # deleted after delete_after days
    assert (arc / young_arc).exists()
    assert (arc / "MEMORY-archive-2001-01.md").exists()
    assert (arc / "Signals" / f"{_day(delete_after + 2)}.md").exists()
    assert "Signals/" in archives                                     # the kept folder is named


def _sections_after(text: str, heading: str) -> list[str]:
    start = re.search(rf"^## {re.escape(heading)}\s*$", text, re.MULTILINE)
    assert start, f"no '## {heading}' section"
    return re.findall(r"^## (.+?)\s*$", text[start.end():], re.MULTILINE)


def test_evolution_md_entry_sections_are_the_ones_the_writer_writes():
    text = _read("EVOLUTION.md")
    assert set(_sections_after(text, "Entry Format")) == set(writer.product_sections("EVOLUTION"))


def test_evolution_md_says_cdf_writes_it():
    rules = _section(_read("EVOLUTION.md"), "Evolution Rules")
    assert "CDF" in rules
    assert not re.search(r"\bI maintain\b.*\bentries\b|Log immediately", rules)


def test_no_template_asks_to_count_corrections():
    # SELF.md § Self-evolution: nothing classifies or counts corrections into buckets.
    assert "counts corrections into buckets" in _read("SELF.md")
    pattern = re.compile(r"how often|occurrence counts?\b", re.IGNORECASE)
    hits = [name for name in ("EVOLUTION.md", "SELF.md") if pattern.search(_read(name))]
    assert hits == []
