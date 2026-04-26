---
name: Deep Research
description: >
  Thorough multi-source research with citations, analysis, and synthesis.
  TRIGGER: "research", "deep dive", "investigate", "find out about", "comprehensive analysis".
  DO NOT USE: for quick factual lookups (just use WebFetch directly).
  For GitHub repos use github-research. For consulting-grade reports use consulting-report.
  Saves output to Knowledge/Notes/ by default.
version: "2.0.0"
produces_artifact: research
tier: always
---
# Deep Research

Conduct systematic, multi-source research that produces well-cited, comprehensive analysis. Never generate content based solely on general knowledge -- the quality of output depends directly on the quality and quantity of research conducted beforehand.

## Output Location

Save research documents to:
```
~/.swarm-ai/SwarmWS/Knowledge/Notes/YYYY-MM-DD-<topic>.md
```

Once finalized, move to `Knowledge/Library/` for long-term reference.

## Workflow: 5-Phase Research

### Phase 0: Intent Classification & Strategy Planning

**Goal:** Before any search, classify the research intent and plan the optimal strategy. Output a structured plan that drives all subsequent phases.

**Step 1: Classify the research intent.** Pick the PRIMARY intent:

| Intent | Signal Words | Example |
|--------|-------------|---------|
| `factual` | "what is", "how does", "explain" | "How does Raft consensus work?" |
| `competitive` | "vs", "compare", "alternative", "竞品" | "SwarmAI vs OpenClaw" |
| `landscape` | "overview", "landscape", "what's out there", "调研" | "AI agent frameworks 2026" |
| `how_to` | "how to", "implement", "build", "tutorial" | "How to implement RAG with Bedrock" |
| `breaking_news` | "latest", "just happened", "今天", "刚刚" | "What did Anthropic announce today?" |
| `trend` | "trend", "direction", "future", "趋势" | "Where is agent memory heading?" |
| `person_org` | person name, company name, "@handle" | "Research Peter Steinberger" |
| `deep_technical` | "architecture", "internals", "source code" | "Claude Code SDK internal architecture" |

**Step 2: Determine search parameters.** Based on intent, set these BEFORE Phase 1:

| Parameter | `factual` | `competitive` | `landscape` | `how_to` | `breaking_news` | `trend` | `person_org` | `deep_technical` |
|-----------|-----------|---------------|-------------|----------|-----------------|---------|--------------|-------------------|
| **search_depth** | basic | advanced | advanced | basic | basic | advanced | advanced | advanced |
| **time_range** | none | month | none | year | day/week | year | month | none |
| **topic** | general | general | general | general | news | news | general | general |
| **source_priority** | docs→papers→blogs | product pages→HN→blogs | industry reports→news→blogs | GitHub→SO→tutorials | news→social→blogs | reports→expert blogs→news | social→GitHub→blogs→news | source code→docs→talks |
| **min_sources** | 3 | 5 (both sides) | 5 | 3 | 3 | 5 | 4 | 3 |
| **search_rounds** | 2-3 | 3-5 | 3-5 | 2-3 | 2-3 | 3-5 | 3-4 | 2-4 |

**Step 3: Output the plan.** Write this to chat before proceeding — it's your contract:

```
RESEARCH PLAN
━━━━━━━━━━━━
Intent:        <intent>
Query:         <original query>
Time range:    <time constraint>
Source focus:   <top 3 source types>
Search rounds: <N>
Key angles:    <2-4 specific angles to investigate>
Skip:          <what NOT to search — reduces noise>
```

This plan is reviewable — the user can correct it before you spend time searching. Proceed to Phase 1 only after outputting the plan.

**Step 4: Check existing knowledge.** Before searching externally, scan what we already have:
1. `Knowledge/Learned/` — glob for related tags/topics. If a card exists from last week, start from there, don't re-research.
2. `Knowledge/Notes/` — prior research on the same or adjacent topic.
3. `MEMORY.md` — Key Decisions or Lessons that provide baseline context.

Note what you found in the plan: `Prior: <file or "none">`. This prevents duplicate research and gives Phase 1 a head start.

**Step 5: Bilingual search planning.** The user is bilingual (EN/CN). Decide search language:

| Condition | Action |
|-----------|--------|
| Topic is a Chinese company, person, or product | Search CN first, EN second |
| Topic is global tech | Search EN first, add CN search if relevant China angle |
| User asked in Chinese | Include CN searches |
| `person_org` intent + Chinese name | Search both `张三` and `San Zhang` |
| `competitive` with CN competitors | Both languages, compare coverage gaps |

Add `Languages: EN` or `Languages: EN+CN` to the plan output.

**Fast-path rule:** If intent is `factual` or `how_to` AND the query is specific enough to search directly (not ambiguous, not multi-faceted), compress Phase 0 to a single inline line and proceed immediately:

```
Intent: factual | Depth: basic | Sources: docs→papers→blogs | Rounds: 2-3
```

Don't build a full plan block for "How does Raft consensus work?" — just classify and go. Save the full plan for `landscape`, `competitive`, `trend`, `person_org`, and `deep_technical` where strategy actually matters.

---

### Phase 1: Broad Exploration

**Goal:** Map the topic landscape and identify key dimensions before going deep. **Use the search parameters from Phase 0** — don't override them.

**Tavily parameter mapping:** When using Tavily search, pass Phase 0 parameters directly:
- `search_depth` → Tavily `search_depth` ("basic" or "advanced")
- `time_range` → Tavily `days` (day=1, week=7, month=30, year=365)
- `topic` → Tavily `topic` ("general" or "news")

1. Identify 3-5 major dimensions/subtopics of the research question
2. Run 3-5 broad searches to understand the landscape:
   - Use specific, contextual queries (not single keywords)
   - Check the current date before forming temporal queries
   - For same-day information, include month + day + year in searches
3. Record what you find AND what's missing -- gaps drive Phase 2

| Search Strategy | Example |
|----------------|---------|
| Broad landscape | `"{topic}" overview 2026` |
| Key players | `"{topic}" companies OR organizations` |
| Recent developments | `"{topic}" March 2026` |
| Controversy/debate | `"{topic}" criticism OR challenges` |
| Expert voices | `"{topic}" expert opinion OR analysis` |

**Output:** A mental map of the topic with identified subtopics and gaps.

### Phase 2: Deep Dive

**Goal:** Conduct targeted research on each important subtopic identified in Phase 1.

For each subtopic:
1. Run 2-3 targeted searches with specific, refined queries
2. Use `WebFetch` to read full content from the most authoritative sources
3. Extract: facts, data points, quotes, methodology details
4. Note source quality (official docs > peer-reviewed > news > blogs > social)

**Source Priority Ranking:**

| Tier | Source Type | Trust Level |
|------|-----------|-------------|
| 1 | Official documentation, primary sources, peer-reviewed papers | High |
| 2 | Established news outlets, recognized industry analysts | Medium-High |
| 3 | Technical blogs from known experts, conference talks | Medium |
| 4 | Community discussions (HN, Reddit, StackOverflow) | Medium-Low |
| 5 | Social media, anonymous posts, undated content | Low |

### Phase 3: Diversity & Validation

**Goal:** Ensure comprehensive coverage and cross-validate key claims.

For each major finding, verify you can confidently answer:

| Dimension | Question |
|-----------|----------|
| **Facts** | What are the established, verifiable facts? |
| **Examples** | What real-world cases illustrate the points? |
| **Expert views** | What do recognized authorities say? |
| **Trends** | What direction is this heading? |
| **Comparisons** | How does this relate to alternatives/competitors? |
| **Challenges** | What are the known problems, criticisms, or risks? |

**Validation rules:**
- Key claims need 2+ independent sources
- If only one source exists, note it explicitly
- Conflicting information gets both sides presented with assessment
- Never suppress inconvenient findings

### Phase 4: Synthesis Check

**Goal:** Verify research is comprehensive BEFORE writing anything.

Ask yourself these questions. If any answer is "no", go back to Phase 2 or 3:

- [ ] Can I state the key facts without hedging?
- [ ] Do I have real-world examples (not hypothetical)?
- [ ] Can I cite at least one expert perspective?
- [ ] Do I know the current trends and trajectory?
- [ ] Have I identified the main challenges/criticisms?
- [ ] Could I explain the relevance to the user's context?

**If you cannot check all boxes, do NOT proceed to writing.** Return to earlier phases and fill the gaps.

---

## Writing the Research Document

Only after Phase 4 is complete:

### Document Structure

```markdown
# {Research Topic}

**Date:** YYYY-MM-DD
**Research depth:** {number of sources consulted}

## Summary
{3-5 sentences covering the key findings -- self-contained for skimmers}

## Key Findings

### {Finding 1}
{Analysis with inline citations}

### {Finding 2}
{Analysis with inline citations}

...

## Open Questions
{What remains unclear or under-researched}

## Sources
1. {Title} -- {URL} -- {accessed date}
2. ...
```

### Writing Rules

- Lead with insights, not background
- Every factual claim needs a citation
- Distinguish clearly between facts, analysis, and speculation
- Use tables for comparisons, not prose
- Keep paragraphs focused -- one idea each
- Flag uncertainty explicitly ("Source X claims... but this is unverified")

---

## Intent-Specific Search Strategies

Use these as Phase 1 templates based on the Phase 0 intent:

| Intent | Phase 1 Searches | Phase 2 Focus |
|--------|-----------------|---------------|
| `factual` | Definition + mechanism + edge cases | Official docs, verify claims across sources |
| `competitive` | Each product separately + "X vs Y" + user reviews | Feature matrix, pricing, real user experiences |
| `landscape` | Category overview + key players + recent entrants + market reports | Each player's differentiator, adoption signals |
| `how_to` | Tutorial search + GitHub repos + SO questions | Implementation details, common pitfalls, working examples |
| `breaking_news` | News search (topic=news, time=day) + social reactions + expert commentary | Primary source, timeline of events, impact analysis |
| `trend` | Historical trajectory + current state + expert predictions | Data-backed claims, not opinion-only; adoption metrics |
| `person_org` | Recent activity + projects + talks/writing + community mentions | Cross-platform presence, contribution patterns |
| `deep_technical` | Source code + architecture docs + design decisions + conference talks | Implementation details, trade-offs, performance data |

## Search Technique Reference

| Technique | When to Use | Example |
|-----------|-------------|---------|
| Quoted phrase | Finding exact matches | `"exact phrase here"` |
| Site-scoped | Targeting specific sources | `site:arxiv.org "{topic}"` |
| Temporal | Recent information | `"{topic}" 2026` or `"{topic}" March 2026` |
| Exclusion | Filtering noise | `"{topic}" -pinterest -youtube` |
| Comparison | Evaluating alternatives | `"{topic}" vs "{alternative}"` |
| Technical depth | Implementation details | `"{topic}" implementation OR architecture OR design` |

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Too many surface-level results | Add technical terms, use site: for authoritative sources |
| Topic is too broad | Narrow to a specific angle before Phase 2 |
| Conflicting information | Note both sides, assess source credibility, present honestly |
| Very recent topic (<1 week) | Include exact dates in searches, check social media (Tier 5), note limited sourcing |
| User wants speed over depth | Do Phase 1 + abbreviated Phase 2, skip Phase 3, note reduced confidence |

## Verification

Before marking this task complete, show evidence for each:

- [ ] **Sources cited with URLs** — every factual claim links to a specific source, with access date noted
- [ ] **Multi-source validation** — key findings backed by 2+ independent sources; single-source claims flagged
- [ ] **Findings synthesized** — research document follows the template (Summary, Key Findings, Open Questions, Sources)
- [ ] **Output saved to Knowledge/** — research file exists at `Knowledge/Notes/YYYY-MM-DD-<topic>.md` (or `Knowledge/Library/` if finalized)
- [ ] **Phase 4 checklist passed** — all six synthesis-check questions answered affirmatively before writing
