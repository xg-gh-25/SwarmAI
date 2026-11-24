"""AGENT.md R18 / R24 carry the person-approved text, and shipped guidance has no stale pointers.

R24 once said "CDF's score and decay decide what is injected", which contradicted R22/R23,
SELF.md's Memory management flow and the MEMORY.md footer: MEMORY is injected whole and an
entry leaves only by a declared override or a withdrawal. R1 ends with the conductor
paragraph (independent parts run at once on the carrier there is: the person's Hives,
otherwise background sub-agents or separate session worktrees here), R18 says that the
first next step is the one action that starts all those parts, and R1 / § Iterative
Refinement point at both. The earlier R18 sentence, which covered only a person with a
Hive, is gone. The shipped skills and templates must not name a context-file
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
R18_INDEPENDENT = (
    "When the reply's work has independent parts, the first next step is the one action that "
    "starts them all, never one line per part. The split (part → where it runs → what it "
    "waits for) goes in the body above."
)
R1_CONDUCTOR = (
    "*Independent parts run at once, on the carrier there is.* Work with two or more parts "
    "that wait for nothing is run as a conductor: this session splits it into packets (what, "
    "where it runs, what it waits for), starts the independent ones at the same time, follows "
    "each, answers its waits, lands and judges its result, and reports row by row. Carriers, "
    "in order: the person's Hives (code in a repository with a remote, long work); otherwise "
    "background sub-agents here for investigations and reviews, and separate session "
    "worktrees for code changes whose files do not overlap. Only a real dependency or a "
    "shared file runs one after another. With no Hive, propose one once when the work is "
    "long, never per task."
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


def _r1_block(text: str) -> str:
    """R1 up to R2, whitespace-flattened (R1 spans more than one paragraph)."""
    return _flat(text[text.index("**R1."):text.index("**R2.")])


def test_r18_ends_with_the_independent_parts_sentence():
    r18 = _paragraph(_agent(), "**R18.")
    assert r18.endswith(R18_INDEPENDENT + " (SOUL P3+P4)")


def test_r1_ends_with_the_conductor_paragraph():
    r1 = _r1_block(_agent())
    assert r1.endswith(R1_CONDUCTOR + " (SOUL P5+P10)")


def test_replaced_hive_only_dispatch_line_is_gone():
    flat = _flat(_agent())
    for stale in ("Independent work is one dispatch line", "and the person has a Hive, the first",
                  "proposed in one dispatch line", "one line that dispatches them all",
                  "I propose none"):
        assert stale not in flat, stale


def test_r1_and_iterative_refinement_point_at_r1_and_r18():
    text = _agent()
    r1 = _r1_block(text)
    assert "several independent parts: one action that starts them all, R18" in r1
    assert "with no Hive, everything runs locally, and I propose one once when the work is long" in r1
    section = _flat(text[text.index("### Iterative Refinement"):text.index("### Escalation")])
    assert "on the carrier there is, R1" in section
    assert "started by one next-step action, R18" in section
    assert "only a real dependency or a shared file puts one after another" in section


def test_worktree_rule_allows_one_worktree_per_parallel_code_part():
    text = _agent()
    worktree = _flat(text[text.index("- **Worktree:**"):text.index("- **Tests (any language")])
    assert "independent code changes run at the same time (one worktree per part, R1)" in worktree


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
