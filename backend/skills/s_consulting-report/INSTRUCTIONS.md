# Consulting Report

Generate McKinsey/BCG-grade research reports through a two-phase process: first build an analysis framework, then synthesize data into a structured report. Designed for market analysis, competitive intelligence, financial assessment, and strategic planning.

## Output Location

Save reports to:
```
~/.swarm-ai/SwarmWS/Knowledge/Reports/YYYY-MM-DD-<topic>-report.md
```

## Workflow Overview

```
Phase 1: Analysis Blueprint    Phase 2: Report Generation
-------------------------    -------------------------
User provides topic    -->   Framework selected
Chapter skeleton built -->   Data collected (research)
Data requirements ID'd -->   Report synthesized
Visualization planned  -->   Quality checked
```

Phase 1 produces a structured blueprint. Data collection happens between phases (using deep-research or manual input). Phase 2 synthesizes everything into the final report.

---

## Phase 1: Analysis Blueprint

### Step 1: Understand the Research Subject

Extract from the user's request:

| Dimension | Question |
|-----------|----------|
| **Subject** | What entity/market/topic is being analyzed? |
| **Scope** | Geographic, temporal, and industry boundaries? |
| **Audience** | Who reads this? (exec team, investors, board, internal strategy) |
| **Decision** | What decision will this report inform? |
| **Language** | Output language (default: match user's language) |

### Step 2: Select Analysis Frameworks

Choose 2-4 frameworks based on the research subject. Match frameworks to the analysis type:

| Analysis Type | Recommended Frameworks |
|--------------|----------------------|
| **Market Entry** | TAM-SAM-SOM, Porter's Five Forces, PESTEL |
| **Competitive** | SWOT, Competitive Positioning Map, Value Chain |
| **Financial** | DuPont Analysis, DCF Framework, Unit Economics |
| **Product/Strategy** | BCG Matrix, Ansoff Matrix, Blue Ocean (4 Actions) |
| **Industry** | Porter's Five Forces, Industry Lifecycle, PESTEL |
| **Technology** | Technology Adoption Lifecycle, Gartner Hype Cycle, Build/Buy/Partner |
| **Organizational** | McKinsey 7S, VRIO, Core Competency Analysis |

For each selected framework, define:
- What hypothesis it will test
- What data is needed to populate it
- How it connects to the decision being made

### Step 3: Build Chapter Skeleton

Design 4-8 chapters. Each chapter needs:

```markdown
### Chapter N: {Title}

**Analysis Objective:** What question does this chapter answer?
**Hypothesis:** What we expect to find (to be validated/rejected)
**Frameworks Applied:** Which of the selected frameworks are used here
**Data Requirements:**
  - {Data point 1} -- Priority: High/Medium -- Search: "{suggested search query}"
  - {Data point 2} -- Priority: High/Medium -- Search: "{suggested search query}"
**Visualization Plan:**
  - {Chart type}: {what it shows}
```

### Step 4: Define Data Collection Plan

Consolidate all data requirements into a prioritized collection plan:

| Priority | Data Point | Source Strategy | Search Keywords |
|----------|-----------|----------------|-----------------|
| P1 (Must have) | {data} | {web search / API / user input} | "{keywords}" |
| P2 (Should have) | {data} | {source} | "{keywords}" |
| P3 (Nice to have) | {data} | {source} | "{keywords}" |

### Step 5: Deliver Blueprint & Collect Data

Present the blueprint to the user. Then either:
- **Auto-collect**: Use WebFetch to research P1 and P2 data points (recommended for most cases)
- **User-provided**: Wait for the user to supply data files or links
- **Hybrid**: Auto-collect what's available, flag gaps for user

---

## Phase 2: Report Generation

### Step 6: Synthesize Report

**Writing structure for each section:**

```
Visual Anchor --> Data Contrast --> Integrated Analysis
(chart/table)    (key numbers)     (so-what insight)
```

**Insight chain for every analytical paragraph:**

```
Data --> User Psychology --> Strategy Implication
(what happened)  (why it matters)  (what to do about it)
```

**Rules:**
- Every number must have a source
- Every chart must be referenced in the text
- Every section ends with a "So What?" paragraph (minimum 200 words) synthesizing findings into strategic judgment
- No horizontal rules within the report body
- No preamble ("In this section we will discuss...") -- start with the insight

### Step 7: Apply Formatting Standards

**Report Structure:**

```markdown
# {Report Title}

**Date:** YYYY-MM-DD | **Author:** SwarmAI | **Confidentiality:** {level}

## Abstract
{150-250 words: scope, key findings, primary recommendation}

## 1 Introduction
{Context, methodology, scope, limitations}

## 2-N {Body Chapters}
{Follow chapter skeleton from Phase 1}

## N+1 Conclusions & Recommendations
{Synthesize across all chapters, prioritized action items}

## References
{Numbered list, include URLs where available}
```

**Formatting rules:**
- Chapter numbering: `1`, `1.1`, `1.1.1` (no "Chapter/Part/Section" prefixes)
- Numbers: use English comma separators (1,000 not 1，000)
- Tables: use for data comparison, keep under 7 columns
- Charts: generate before writing, embed as `![Description](path)` or use Mermaid
- Bold key findings on first mention in each section

### Step 8: No-Hallucination Validation

Before delivering:

| Check | Action |
|-------|--------|
| Every number cited | Trace to source data or research finding |
| Every market size | Verify methodology (top-down vs bottom-up) noted |
| Every competitor claim | Cross-reference with at least one source |
| Missing data | Explicitly flagged as "Data not available" -- never fabricated |
| Framework outputs | Each populated with actual data, not generic examples |

**If a framework cannot be populated due to missing data, say so explicitly.** A partially-filled honest framework is worth more than a fully-filled fabricated one.

### Step 9: Completeness Check

- [ ] Abstract is self-contained (exec can read only this)
- [ ] All selected frameworks appear populated in the report
- [ ] Every chapter's hypothesis is addressed (confirmed/rejected/inconclusive)
- [ ] Recommendations are specific and actionable (not "consider improving X")
- [ ] Sources are numbered and referenced inline
- [ ] Visualization plan from Phase 1 is fulfilled
- [ ] Report saved to Knowledge/Reports/

---

## Framework Quick Reference

### TAM-SAM-SOM
- **TAM**: Total addressable market (everyone who could use this)
- **SAM**: Serviceable addressable market (those you can reach)
- **SOM**: Serviceable obtainable market (realistic near-term capture)

### Porter's Five Forces
1. Threat of new entrants
2. Bargaining power of suppliers
3. Bargaining power of buyers
4. Threat of substitutes
5. Competitive rivalry

### SWOT
- **Strengths**: Internal advantages
- **Weaknesses**: Internal limitations
- **Opportunities**: External favorable conditions
- **Threats**: External unfavorable conditions

### PESTEL
- Political, Economic, Social, Technological, Environmental, Legal

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Too broad a topic | Narrow scope in Step 1: pick one market, one region, one timeframe |
| Insufficient data | Flag gaps explicitly, offer to collect more, adjust confidence levels |
| Conflicting sources | Present both, note the conflict, state which is more credible and why |
| User wants quick analysis | Skip Phase 1 blueprint, go direct to Phase 2 with a single framework |
| Report too long | Cut P3 data points, reduce to 2 frameworks, tighten So What paragraphs |
