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

## Workflow: 4-Phase Research

### Phase 1: Broad Exploration

**Goal:** Map the topic landscape and identify key dimensions before going deep.

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
