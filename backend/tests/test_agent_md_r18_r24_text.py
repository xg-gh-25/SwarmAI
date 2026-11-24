"""AGENT.md R18 / R24 carry the person-approved text, and shipped guidance has no stale pointers.

R24 once said "CDF's score and decay decide what is injected", which contradicted R22/R23,
SELF.md's Memory management flow and the MEMORY.md footer: MEMORY is injected whole and an
entry leaves only by a declared override or a withdrawal. R18 now says that independent
parts of a reply's work are dispatched in one next-step line, and R1 / § Iterative
Refinement point at it. The shipped skills and templates must not name a context-file
section that SWARMAI.md no longer has, nor send the agent to ``context_recall_cli --domains``
(kept for eval probes only; the agent's recall command is ``cognition find``).
"""
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
CTX = BACKEND / "context"
AGENT = CTX / "AGENT.md"

R24_MEMORY = ("MEMORY.md: injected whole; an entry leaves only by an override its deciding "
              "context declares or by a withdrawal, never by age, score or size.")
R18_DISPATCH = (
    "**Independent work is one dispatch line.** When the reply's work has two or more parts "
    "that wait for nothing and the person has a Hive, the first next step is one action that "
    "dispatches them all at once; the parts that wait, or that must run on this machine, come "
    "after it, never one line per part. The split (part → where it runs → what it waits for) "
    "goes in the body above."
)


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _paragraph(text: str, start: str) -> str:
    """The blank-line-delimited paragraph that starts with ``start``, whitespace-flattened."""
    idx = text.index(start)
    end = text.find("\n\n", idx)
    return _flat(text[idx:end if end != -1 else len(text)])


def _agent() -> str:
    return AGENT.read_text(encoding="utf-8")


def test_r24_memory_sentence_is_the_approved_text():
    r24 = _paragraph(_agent(), "**R24.")
    assert R24_MEMORY in r24
    # The rest of R24 stays.
    assert "KNOWLEDGE.md: index, don't inline large bodies." in r24
    assert "EVOLUTION.md: earned entries only" in r24
    assert r24.endswith("(SOUL P6)")


def test_agent_md_no_longer_says_score_or_decay_decides_injection():
    flat = _flat(_agent())
    for stale in ("score and decay", "relevance > age", "CDF's score"):
        assert stale not in flat, stale


def test_r18_ends_with_the_dispatch_line_sentence():
    r18 = _paragraph(_agent(), "**R18.")
    assert r18.endswith(R18_DISPATCH + " (SOUL P3+P4)")


def test_r1_and_iterative_refinement_point_at_the_r18_dispatch_line():
    text = _agent()
    r1 = _paragraph(text, "**R1.")
    assert "several independent parts: one line that dispatches them all, R18" in r1
    section = text[text.index("### Iterative Refinement"):text.index("### Escalation")]
    assert "proposed in one dispatch line, R18" in _flat(section)


def _shipped_guidance() -> list[Path]:
    roots = [CTX, BACKEND / "skills", BACKEND / "templates"]
    out = []
    for root in roots:
        for p in root.rglob("*"):
            if p.is_file() and p.suffix in (".md", ".py", ".yaml", ".yml", ".txt", ".json") \
                    and "node_modules" not in p.parts:
                out.append(p)
    return out


def test_shipped_guidance_names_only_context_sections_swarmai_md_has():
    swarmai = (CTX / "SWARMAI.md").read_text(encoding="utf-8")
    headings = {m.group(1).strip() for m in re.finditer(r"^#+ (.+)$", swarmai, re.MULTILINE)}
    stale = []
    for p in _shipped_guidance():
        text = p.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"SWARMAI\.md`? § \"([^\"]+)\"", text):
            name = m.group(1)
            if not any(h == name or h.startswith(name) for h in headings):
                stale.append(f"{p.relative_to(BACKEND)}: {name}")
    assert stale == []


def test_shipped_guidance_has_no_stale_context_file_count_or_recall_cli_pointer():
    count = re.compile(r"\b11 context files\b", re.IGNORECASE)
    recall_cli = re.compile(r"context_recall_cli(?:\.py)?[^\n]{0,80}--domains")
    hits = []
    for p in _shipped_guidance():
        text = p.read_text(encoding="utf-8", errors="ignore")
        if count.search(text) or recall_cli.search(text):
            hits.append(str(p.relative_to(BACKEND)))
    assert hits == []
