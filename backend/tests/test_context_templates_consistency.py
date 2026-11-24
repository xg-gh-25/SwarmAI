"""The shipped EVOLUTION / SELF templates agree with the code and each other.

Each test reads what the code does (the retention hook's behaviour, the CDF writer's
EVOLUTION sections) and checks that the template says the same, so a later change on
either side fails here instead of shipping a template that tells every new install
something false.
"""
import re
from pathlib import Path

from core.ingestion import writer

CTX = Path(__file__).resolve().parents[1] / "context"


def _read(name: str) -> str:
    return (CTX / name).read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    match = re.search(rf"^## {re.escape(heading)}.*?(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    assert match, f"no '## {heading}' section"
    return match.group(0)


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
