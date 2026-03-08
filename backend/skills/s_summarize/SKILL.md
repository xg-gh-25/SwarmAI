---
name: Summarize
description: >
  Quickly summarize articles, documents, URLs, long text, or multi-source content into concise key points.
  TRIGGER: "summarize", "summary", "tl;dr", "key points", "what's this about", "give me the gist", "condense".
  DO NOT USE: for deep multi-source research (use deep-research), consulting-grade analysis (use consulting-report), or full document writing (use narrative-writing).
  SIBLINGS: deep-research = multi-source investigation with citations | consulting-report = strategic analysis with frameworks | summarize = fast single/multi-source condensation.
---

# Summarize

**Why?** Get the essence of any content in seconds -- no need to read a 30-page doc, sit through a 2-hour transcript, or scroll through 200 Slack messages. Fast, structured, actionable summaries.

---

## Quick Start

```
"Summarize this article: https://..."
"TL;DR this document" + file path
"Summarize the key points from this meeting transcript"
"What's this PDF about?" + file path
```

---

## Input Types

| Input | How to Handle |
|---|---|
| URL / web page | Fetch with WebFetch, then summarize |
| Local file (.md, .txt, .pdf, .docx) | Read with Read tool or appropriate skill |
| Pasted text | Summarize directly |
| Multiple URLs/files | Summarize each, then synthesize |
| Clipboard content | Ask user to paste, or read from clipboard |

---

## Workflow

### Step 1: Identify Source and Length Target

Detect from user request:

| User Says | Length Target |
|---|---|
| "TL;DR" / "one liner" / "gist" | Ultra-short: 1-2 sentences |
| "summarize" / "key points" | Standard: 3-7 bullet points |
| "detailed summary" / "comprehensive" | Detailed: structured sections |
| "compare these" | Comparison: side-by-side analysis |

Default to **Standard** if not specified.

### Step 2: Fetch Content

**URLs:**
```
Use WebFetch to retrieve and extract the page content.
```

**Files:**
```
Use Read tool for text/markdown.
Use s_pdf skill for PDF files.
Use s_docx skill for Word documents.
Use s_xlsx skill for spreadsheets.
```

**Long content (>50K chars):**
Read in sections, summarize each section, then synthesize into a final summary.

### Step 3: Analyze and Summarize

Extract these elements from the content:

| Element | Always Include? | Description |
|---|---|---|
| One-line summary | Yes | What is this about, in one sentence |
| Key points | Yes | 3-7 most important takeaways |
| Context/background | If relevant | Why this matters, what prompted it |
| Data/numbers | If present | Key metrics, stats, figures |
| Decisions/conclusions | If present | What was decided or concluded |
| Action items | If present | What needs to happen next |
| Open questions | If present | Unresolved issues |

### Step 4: Format Output

#### Ultra-Short (TL;DR)

```
TL;DR: [One or two sentences capturing the essence]
```

#### Standard (Default)

```
## Summary: [Title/Topic]

[One-line summary sentence]

**Key Points:**
- Point 1
- Point 2
- Point 3
- ...

**Action Items:** (if any)
- [ ] Item 1
- [ ] Item 2
```

#### Detailed

```
## Summary: [Title/Topic]

### Overview
[2-3 sentence overview]

### Key Points
- Point 1 with supporting detail
- Point 2 with supporting detail
- ...

### Key Data
- Metric 1: value
- Metric 2: value

### Decisions & Conclusions
- Decision 1
- ...

### Action Items
- [ ] Item 1 (owner, deadline if known)
- [ ] Item 2

### Open Questions
- Question 1
- Question 2
```

#### Comparison (Multi-Source)

```
## Comparison Summary

### Source A: [Title]
- Key point 1
- Key point 2

### Source B: [Title]
- Key point 1
- Key point 2

### Common Themes
- Shared point 1
- Shared point 2

### Key Differences
- Difference 1
- Difference 2

### Synthesis
[What to take away from both sources together]
```

---

## Content-Specific Patterns

### Article / Blog Post

Focus on: thesis, supporting arguments, conclusions, author's perspective.

Skip: author bio, ads, related articles, comment sections.

### Meeting Transcript / Recording

Focus on: decisions made, action items, key discussion points, disagreements.

Skip: small talk, repeated points, filler phrases.

Present as:
```
## Meeting Summary: [Topic] - [Date]

**Attendees:** (if identifiable)
**Duration:** ~X minutes

### Decisions
1. Decision 1
2. Decision 2

### Action Items
- [ ] @Person: Task (by date)

### Discussion Highlights
- Topic A: [key points]
- Topic B: [key points]

### Parking Lot / Open Items
- Unresolved issue 1
```

### Technical Document / RFC / Spec

Focus on: problem statement, proposed solution, trade-offs, timeline.

Skip: boilerplate, revision history, formatting artifacts.

### Email Thread / Slack Discussion

Focus on: original request, key responses, resolution/outcome.

Skip: greetings, signatures, emoji reactions, "me too" messages.

### PDF Report / Research Paper

Focus on: abstract/executive summary, methodology highlights, key findings, recommendations.

Skip: acknowledgements, detailed methodology (unless asked), appendices.

---

## Multi-Source Synthesis

When summarizing multiple inputs:

1. Summarize each source independently (internal, don't show to user)
2. Identify common themes across sources
3. Note contradictions or different perspectives
4. Synthesize into a single coherent summary
5. Attribute key points to their source when relevant

---

## Language Handling

- Summarize in the **same language** as the source content by default
- If source is mixed language, summarize in the user's preferred language
- If user asks in Chinese but source is English (or vice versa), summarize in the language the user asked in

---

## Saving Output

For substantial summaries (detailed mode or multi-source):

Save to: `~/.swarm-ai/SwarmWS/Knowledge/Notes/summaries/`

Filename: `YYYY-MM-DD-<topic-slug>.md`

For quick TL;DR or standard summaries, just present inline -- don't save unless asked.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| URL fetch fails | Try alternative URL, or ask user to paste content |
| Content too long for single pass | Split into sections, summarize each, then synthesize |
| PDF can't be read | Use s_pdf skill to extract text first |
| Source language unclear | Ask user or detect from first paragraph |
| User wants different depth | Ask: "Want a quick TL;DR or detailed breakdown?" |
| Multiple conflicting sources | Note contradictions explicitly, don't merge conflicting facts |

---

## Quality Rules

- Lead with the most important information -- don't bury the lede
- Use bullet points for scanability, not walls of text
- Include specific numbers/data when present -- don't vague them away
- Attribute claims to sources when summarizing multiple inputs
- Flag uncertainty: "The article claims..." vs "Research shows..."
- Never fabricate details not in the source material
- For action items, include owner and deadline when identifiable
- Keep standard summaries under 200 words; detailed under 500 words
- When in doubt about length, go shorter -- user can always ask for more

---

## Testing

| Scenario | Expected Behavior |
|----------|-------------------|
| "Summarize this URL" | Fetch page, standard summary with key points |
| "TL;DR" + long text | 1-2 sentence ultra-short summary |
| "Detailed summary of this PDF" | Full structured summary with all sections |
| "Compare these two articles" | Side-by-side comparison + synthesis |
| Meeting transcript summary | Decisions, action items, highlights format |
| Chinese article, user asks in Chinese | Summary in Chinese |
| Content fetch fails | Graceful fallback, ask user to paste |
| Very long document (>100 pages) | Section-by-section approach, synthesized output |
