# Narrative Writing

## Overview

Structured narrative writing drives decision-making, innovation, and customer focus through well-crafted documents. This skill provides patterns, templates, and rules for effective writing across all document types.

**Core principle:** Write clear, data-driven documents that enable decisions and action.

## Output Location

**All generated documents should be saved to:**
```
~/.swarm-ai/SwarmWS/Knowledge/Notes/
```

This is the designated location for draft documents. Once finalized and approved, documents can be moved to `Knowledge/Library/`.

## Usage

Use this skill when:
- Writing 1-6 page narratives for decisions or proposals
- Creating technical documentation (architecture, features, services)
- Drafting business planning documents (OP1/OP2, interview feedback)
- Improving document clarity, structure, or data presentation
- Checking for weasel words and ambiguous language

When encountering:
- "Write a six-pager for..."
- "Create a PR/FAQ for..."
- Document contains weasel words ("generally", "usually", "might")
- Feedback: "Be more specific" or "Add metrics"
- Need to structure technical documentation
- Preparing for document review meeting

**When NOT to use:**
- Quick status updates or emails (use concise email format)
- Informal team communications (use chat/Slack)
- External customer-facing content (use marketing guidelines)
- Code documentation (use language-specific doc standards)

## Instructions

### Writing a New Document

**Recommended workflow:**

1. **Gather requirements:**
   - What document type?
   - Who is the audience?
   - What needs to be communicated?

2. **Route to a template if the type is structured.** If the document is a six-pager, PR/FAQ, HLD, LLD, ADR, SOP, or project plan, load the matching template and follow the Guided Authoring Workflow (self-answer → list → confirm → generate):

   | User asks for | Load |
   |---------------|------|
   | six-pager / narrative / decision doc | `reference/templates/six-pager.md` |
   | PR/FAQ / press release / launch doc | `reference/templates/prfaq.md` |
   | HLD / system design / high-level design | `reference/templates/hld.md` |
   | LLD / component design / low-level design | `reference/templates/lld.md` |
   | ADR / decision record | `reference/templates/adr.md` |
   | SOP / runbook / operating procedure | `reference/templates/sop.md` |
   | project plan / delivery plan / milestones | `reference/templates/project-plan.md` |

   For a free-form document with no matching type, use the section-by-section flow below.

3. **Get initial structure** (free-form documents only. Structured types use the template's fixed structure):
   - **Option A (Preferred)**: User provides template with rough draft notes
   - **Option B**: User describes what each section should cover in one sentence
   
4. **Iterate section by section** (free-form documents only. Structured types use the Guided Authoring Workflow above):
   - Ask clarifying questions to gather information for this section
   - Write the section based on answers
   - Review with user
   - Iterate until user approves
   - Move to next section
   
5. **Apply standards during iteration:**
   - Documents **MUST** include title with document type and date
   - Documents **MUST** include executive summary (purpose + recommendation + decisions)
   - You **MUST** use logical heading hierarchy (H1 title, H2 sections, H3 subsections)
   - Documents **SHOULD** include a table of contents when they have 4+ H2 sections (the markdown proxy for "> 3 pages" — page count is not measurable in markdown)
   - You **MUST** use active voice
   - You **MUST** eliminate weasel words
   - You **MUST** support claims with specific metrics
   - You **SHOULD** front-load important information
   - You **MUST NOT** use emojis
   - Tables **MUST** include units and context
   - Charts **MUST** be labeled clearly with one message per chart
   - Documents **MUST** use minimum 10pt body text
   - Documents **MUST** maintain high contrast for accessibility
   
6. **Reader Testing (spawn cold agent):**
   - After completing all sections, spawn a sub-agent with ZERO context about the writing process
   - Give it ONLY the finished document and this prompt:
     ```
     Read this document cold. You have no context about why it was written or what preceded it.

     Report:
     1. What is this document asking me to decide/approve/understand? (If unclear = problem)
     2. Which sections confused you or required re-reading?
     3. What questions remain unanswered after reading?
     4. Which claims lack supporting evidence?
     5. Where does the document assume knowledge the reader might not have?
     ```
   - Fix any issues the reader test reveals (especially #1 and #2 — if the purpose is unclear or sections confuse a fresh reader, the document fails its primary job)
   - This step catches the #1 document failure mode: **author assumes shared context that doesn't exist for the reader**

7. **Stakeholder Simulation (spawn cold agent — a skeptical approver, NOT a confused reader):**
   - This is a DIFFERENT lens from Reader Testing. Reader Testing asks "is it clear?" (comprehension). Stakeholder Simulation asks "does the argument survive a hostile approver who understood it perfectly?" (defensibility). Run it AFTER the document reads clearly — a clear document still loses the room when the argument is weak.
   - Spawn a sub-agent with ZERO context and this prompt:
     ```
     You are a skeptical senior reviewer (a VP or Bar-Raiser) in the reading meeting.
     You understood the document fully — do NOT report confusion or missing evidence
     (that is a separate review). Your job is to decide whether to APPROVE, and to
     surface the pushback you would raise before you would.

     Report:
     1. The single weakest link in the argument — the claim that, if I dispute it,
        the recommendation collapses. Where would I push hardest?
     2. What decision or alternative did the author NOT consider that I would raise?
     3. Where does the document present a frictionless picture — a risk, cost, or
        one-way door it downplays or omits?
     4. What would I demand changed before I approve? (the blocking asks)
     5. Approve as-is, approve with changes, or send back? State which and why.
     ```
   - Fix the blocking asks (#4) and strengthen the weakest link (#1) before delivery. A "send back" verdict means the argument, not the wording, needs work — return to the relevant section, do not just polish.
   - This step catches the #2 document failure mode: **the argument is clear but not defensible — a senior reviewer sends it back in the meeting.**

8. **Final review:**
   - You **MUST** verify six-page limit (appendices excluded)
   - You **MUST** check for weasel words using `scripts/check-weasel-words.sh`
   - You **MUST** produce a consolidated **still-open list** before delivery: reconcile every `[TBD]` left in the draft, every unresolved item from Reader Testing (#3 unanswered questions) and Stakeholder Simulation (#4 blocking asks), plus each template Open Questions entry, into ONE list — each with a suggested source or owner to resolve it. This is a reconciliation pass, not a new section: it surfaces what the drafting mechanisms tracked so nothing unresolved ships silently. Deliver with the list attached, or resolve the items first.
   - You **SHOULD** have peer review before submission

**Note:** Section-by-section iteration is more effective than creating a complete draft upfront.

### Improving Existing Documents

1. **Scan for weasel words** using `scripts/check-weasel-words.sh <file>`
2. **Replace vague language** with specific metrics (see reference/weasel-words.md)
3. **Restructure if needed**:
   - You **MUST** move important information to executive summary
   - You **SHOULD** use SCQA framework (Situation, Complication, Question, Answer)
4. **Enhance data presentation** (see reference/data-presentation.md):
   - You **MUST** add context to metrics
   - You **SHOULD** convert prose to tables where appropriate
5. **Fix visual formatting** (see reference/visual-formatting.md):
   - Documents **MUST** ensure minimum 10pt text
   - You **SHOULD** add white space between sections

### Using Supporting Files

**scripts/check-weasel-words.sh:**
- You **MUST** run before finalizing any document
- Detects ambiguous language requiring specific metrics
- Exit code 1 if weasel words found, 0 if clean

**reference/weasel-words.md:**
- You **SHOULD** consult when replacing vague language
- Contains comprehensive list with specific replacements

**reference/data-presentation.md:**
- You **MUST** follow when adding tables or charts
- Provides formatting standards and examples

**reference/visual-formatting.md:**
- You **MUST** follow for document layout
- Ensures accessibility and readability

## Core Concepts

### Narrative Culture
- Teams **MUST** use written documents over presentations for decisions
- Meetings **MUST** start with silent reading (15-30 minutes) for shared context
- Documents **MUST** respect six-page limit (excluding appendices) to force concise, actionable writing
- Teams **SHOULD** use Working Backwards: Start with customer problem, not solution
- Arguments **MUST** be data-driven: supported by metrics and evidence

### Document Types

| Type | Length | Purpose |
|------|--------|---------|
| One-pager | 1 page | High-level goals, tenets, design |
| Six-pager | 6 pages + appendices | Decisions, proposals, strategy |
| PR/FAQ | 1-2 pages + FAQ | Product launches, features |
| Architecture Doc | Variable | System design, technical decisions |
| OP1/OP2 | 6 pages + appendices | Annual planning |
| Interview Feedback | 1-2 pages | Candidate assessment |
| SOP | Variable | Repeatable operational procedure, runbook |
| Project Plan | Variable | Scope, milestones, risks, dependencies |

### Choosing Document Type

```mermaid
flowchart TD
    Start[Need Document] --> Q1{Audience?}
    Q1 -->|Executives| Q2{Decision needed?}
    Q1 -->|Engineers| Tech[Architecture Doc]
    Q1 -->|Customers| PRFAQ[PR/FAQ]
    
    Q2 -->|Yes| Q3{Complexity?}
    Q2 -->|No| Update[Status Update]
    
    Q3 -->|High| SixPager[Six-Pager]
    Q3 -->|Low| OnePager[One-Pager]
    
    style SixPager fill:#90EE90
    style PRFAQ fill:#87CEEB
    style Tech fill:#FFB6C1
```

## Document Structure

### Foundation
- **Title**: Documents **MUST** have clear, descriptive titles with document type and date
- **Executive Summary**: Documents **MUST** include purpose, key points, and decisions needed
- **Objective**: You **MUST** state goal in first paragraph
- **Scope**: You **SHOULD** clarify what you will and won't cover
- **Table of Contents**: Documents **SHOULD** include a table of contents when they have 4+ H2 sections (the markdown proxy for "> 3 pages" — page count is not measurable in markdown)

### Organization
- **Logical hierarchy**: You **MUST** use H1 for title, H2 for major sections, H3 for subsections
- **Most important first**: You **MUST** lead with key information
- **Clear sections**: You **MUST** use descriptive headings and group related content
- **SCQA framework**: You **SHOULD** use Situation, Complication, Question, Answer structure
- **Consistent formatting**: You **MUST** maintain consistent headings, spacing, typography

### Conclusion
- **Clear recommendations**: You **MUST** provide recommendations, not just analysis
- **Next steps**: You **MUST** include specific actions with owners and deadlines
- **Key points summary**: You **SHOULD** include summary for lengthy documents
- **Proactive Q&A**: You **SHOULD** address potential questions

### Supporting Elements
- **Appendices**: You **SHOULD** use appendices for supporting details that disrupt main flow
- **Six-page limit**: The limit **MUST** apply to main narrative only (appendices excluded)
- **Metadata**: Documents **SHOULD** include page numbers and confidentiality in footer

## What Makes a Document Good

The section above defines a document's **structure** (its parts). This defines its **quality**: the five criteria a finished document is judged against, regardless of type. Use them as the Definition of Done, verifying each one holds before delivering any narrative. (Sourced from the Amazon technical-design guidance. They apply to every narrative, not only designs.)

1. **Self-contained.** A reader can understand and evaluate the problem, solution, decisions, and trade-offs without opening other material. If a claim depends on an external doc, summarize the load-bearing part inline.
2. **Problem and why.** The document states the problem and why it is worth solving, so the reader judges the solution against those objectives, not in a vacuum.
3. **Decisions with alternatives and rationale.** Every major decision names the alternatives considered and why the chosen option wins. A decision with no visible alternative reads as unconsidered.
4. **Known risks with mitigation.** The document names its real risks, their potential impact, and the proposed mitigation or graceful fallback. It does not present a frictionless picture.
5. **Open questions, surfaced not hidden.** Unresolved ambiguity is stated explicitly, each with a suggested approach to resolve or isolate it so the team can proceed. Hiding open questions is the most common reason a senior reviewer sends a document back.

Each template in `reference/templates/` maps its structure back to these five criteria.

## Guided Authoring Workflow

For a structured document type (six-pager, PR/FAQ, HLD, LLD, ADR, SOP, project plan), do not start writing prose immediately, and do not interview the user question-by-question. Follow this loop:

1. **Load the template.** Read `reference/templates/<type>.md` for the type's guided questions and fixed structure (routing is in "Writing a New Document").
2. **Self-answer the guided questions.** Answer each from the material the user gave you, the codebase, the DDD, and context. This is the same discipline as the Reader-Testing step and the pipeline's "interrogate the spec and your own framing, not the user" rule: derive the answer yourself first. Mark `[TBD]` **only** where the answer is genuine user intent that cannot be derived (a preference, a business constraint only the user knows), never as a shortcut to avoid reading.
3. **List all answers once for confirmation.** Present the full set of self-answered questions (and any `[TBD]`s) to the user in a single pass, and ask them to confirm or revise. This is the one human checkpoint. It does not interrupt flow with a stream of questions, and it lets the user correct a wrong assumption before you spend effort drafting.
4. **Generate the document** from the confirmed answers, using the template's fixed structure, and verify it against the five criteria in "What Makes a Document Good".

This keeps authoring autonomous (self-answer first) while giving the user exactly one high-leverage review point, rather than the question-by-question interview that stalls drafting.

## Quick Reference

| Task | Pattern | Example |
|------|---------|---------|
| Executive Summary | Purpose + Recommendation + Decisions | See Common Patterns below |
| Problem Statement | Customer + Problem + Data | "42 users reported login failures between 2-4pm" |
| Recommendation | Action + Benefits + Next Steps | "We recommend X because [metric]" |
| Weasel Word Check | Scan for vague terms | Use `check-weasel-words.sh` |
| Data Presentation | Tables with headers, units, context | See reference/data-presentation.md |
| Visual Formatting | 10pt minimum, high contrast | See reference/visual-formatting.md |

## Common Patterns

### Executive Summary
```markdown
## Executive Summary

[Purpose in 1 sentence]

[Key recommendation with 2-3 supporting points]

Key decisions needed:
1. [Decision 1 with owner]
2. [Decision 2 with owner]

[Impact: cost, timeline, resources]
```

### Problem Statement
```markdown
Today, [customers] have to [problem] when [situation]. 
Customers need a way to [need].

**Data:** [evidence with metrics]
```

### Recommendation
```markdown
We recommend [action] because:
- [Benefit 1 with metric]
- [Benefit 2 with metric]
- [Benefit 3 with metric]

Next steps:
1. [Action] by [date] (Owner: [name])
2. [Action] by [date] (Owner: [name])
```

### Before/After: Vague → Specific

**Timelines:**
- ❌ "We'll improve performance soon"
- ✅ "We'll reduce p99 latency from 500ms to 200ms by Q2 2025"

**Customer Evidence:**
- ❌ "Many customers requested this feature"
- ✅ "127 enterprise customers (23% of revenue) requested SSO in Q3 feedback"

**Impact:**
- ❌ "This will significantly reduce costs"
- ✅ "This will reduce infrastructure costs from $50K/month to $12K/month (76% reduction)"

**Scope:**
- ❌ "We'll generally support most use cases"
- ✅ "We'll support batch uploads up to 10K records and real-time sync for <100 concurrent users"

## Language Precision

### Sentence-Level Clarity
You **MUST** write for busy readers who skim. Every word must earn its place.

**Active voice over passive:**
- ❌ "The feature was implemented by the team" 
- ✅ "The team implemented the feature"

**Short sentences:**
- ❌ "We analyzed the data and found that customers who use the mobile app, which was launched last quarter, tend to complete purchases 40% faster than those using the desktop version, though this varies by region"
- ✅ "Mobile app users complete purchases 40% faster than desktop users. This varies by region."

**Front-load important information:**
- ❌ "After analyzing customer feedback and reviewing competitive offerings, we recommend implementing SSO"
- ✅ "We recommend implementing SSO. Customer feedback and competitive analysis support this."

**Cut unnecessary words:**
- ❌ "In order to improve performance" → ✅ "To improve performance"
- ❌ "Due to the fact that" → ✅ "Because"
- ❌ "At this point in time" → ✅ "Now"

**Eliminate jargon:**
- ❌ "Leverage synergies to optimize the customer journey"
- ✅ "Combine teams to improve customer experience"

### Document Structure and Style

You **MUST NOT** use mdashes or semicolons in narrative documents.

**Document organization:**
- You **MUST** split documents into main body followed by appendices
- You **MUST** use narrative format in main body (prefer prose over bullet points). Reconciliation: prose is the default for *argument and analysis*; bullets and tables are correct for genuinely list-shaped content (sequential steps, enumerable items, comparison matrices) — see `reference/visual-formatting.md` "Lists and Tables". The failure mode is a bulleted body that should be an argument, not a bulleted list that is genuinely a list.
- You **SHOULD** write fewer, longer paragraphs rather than many 2-3 sentence paragraphs

**Word choice:**
- You **MUST NOT** use fancy words or superlatives (they obscure meaning and signal weak arguments that lack data)
- You **MUST** avoid overused words like "comprehensive", "critical", and "significant"
- You **SHOULD** use simple, direct language that conveys meaning clearly

### Weasel Words
You **MUST** avoid ambiguous language that lacks commitment. You **MUST** replace vague terms with specific metrics and commitments.

**Quick examples:**
- ❌ "We will launch soon" → ✅ "We will launch on October 15, 2025"
- ❌ "Performance significantly improved" → ✅ "Response time decreased from 300ms to 120ms"
- ❌ "Many users reported issues" → ✅ "42 users reported login failures between 2-4pm"

**For comprehensive weasel words list and replacements**, see `reference/weasel-words.md`

## Removing AI-isms

Weasel words are one machine tell. There is a second, more structural class: writing that reads as machine-organized rather than human-argued. A busy senior reviewer (a Principal Engineer or leadership-team reader) spots it immediately and it undercuts the document's authority. You **MUST** run the checklist below before finalizing any narrative. Each rule is drawn from real review feedback on machine-drafted narratives.

### The 9 checks

1. **Cut structural label phrases.** Delete `The core judgment:`, `The reasoning:`, `The conclusion:`, `Thesis:`, `Here's why:`. These tag a paragraph with "I am now organizing an argument." State the point directly.
   - ❌ "The core judgment: AI has removed coding as the bottleneck."
   - ✅ "AI has removed coding as the bottleneck."

2. **Say each point once.** A summary sentence (for example "speed is proven, the deciding factor is now X") that reappears across sections is padding. Keep it in one place and delete the echoes.

3. **Do not explain a quote.** After a quotation, let the reader draw the conclusion. Delete trailing `This is why…` / `This shows…` / `This is exactly…` sentences.
   - ❌ "…the bottleneck moves to judgment. This is why EE/OE matters more."
   - ✅ "…the bottleneck moves to judgment."

4. **State the result, do not lay out the full logic chain.** A complete `Now that A, if B does not C, then D surfaces as E` derivation is a machine tell. Give the result. The reader fills the chain.
   - ❌ "Both were gated by humans at a slower cadence. Now that agents produce at machine speed, if humans do not upgrade how they gate in step, the bottleneck surfaces as incidents."
   - ✅ "Agents produce at machine speed, but our gates still run at human speed, and the gap surfaces as incidents."

5. **Avoid passive + modifier stacking.** `were gated by humans at a slower cadence` is passive voice piled with qualifiers. Rewrite as one active clause that names the result. (This extends the active-voice rule under "Sentence-Level Clarity" above — same principle, applied to the modifier pile-up that signals machine drafting.)

6. **Do not pre-empt objections.** Delete defensive framing like `These are not homegrown exceptions`, `It is worth noting that`, `To be clear`. Amazon documents state what a thing is and let it stand.
   - ❌ "These two mechanisms are not homegrown exceptions. They map onto…"
   - ✅ "Both map onto…"

7. **The closing section must not restate the Executive Summary.** If the final Recommendation repeats the summary's argument, cut it to only the new action-oriented content (owners, dates, the ask). Do not paraphrase yourself.

8. **Appendix / figure captions state meaning, not visual layout.** Describe what the reader should take away, not the arrangement of boxes on a slide. Delete `Top three cards…`, `Four-layer architecture…`, `Seven stages…`, `Left column shows…`.
   - ❌ "Top three cards: … Bottom three trend lines: …"
   - ✅ "Our metrics show the shift: speed is banked while security escapes ran to 536."

9. **Final sweep.** Before declaring done, scan once for: sentences that open with a label phrase, repeated summary sentences, explanatory sentences after quotes, and captions that describe layout. Delete what you find.

### Why this matters

These tells survive a weasel-word scan (the script cannot catch them) and survive a first draft that "reads fine" to the author. They are caught only by reading as the reviewer. Treat this checklist as a required pass, the same weight as the weasel-word check.

## Data Presentation

You **MUST** present data clearly to support decision-making:
- **Tables**: Tables **MUST** use clear headers and consistent formatting, Tables **MUST** include units
- **Charts**: Charts **MUST** use appropriate type, Charts **MUST** be labeled clearly, Charts **MUST** convey one message per chart
- **Metrics**: You **MUST** use specific numbers with context and comparisons

**For detailed data presentation guidelines**, see `reference/data-presentation.md`

## Visual Formatting

Documents **MUST** maintain professional, readable appearance:
- **Typography**: Documents **MUST** use minimum 10pt body text, Documents **MUST** use consistent fonts
- **White space**: You **SHOULD** separate ideas with clear section breaks
- **Accessibility**: Documents **MUST** use high contrast, Documents **SHOULD** include alt text, Documents **MUST** maintain logical reading order

**For complete visual formatting standards**, see `reference/visual-formatting.md`

## Supporting Files

### reference/weasel-words.md
Comprehensive list of weasel words with specific replacements.

**When to use:**
- Replacing vague language with specific metrics
- Understanding why certain words weaken arguments
- Finding concrete alternatives to ambiguous terms

**Contents:**
- Complete weasel words list organized by category
- Specific replacement patterns with examples
- Context for when vague language is acceptable

### reference/data-presentation.md
Standards and examples for presenting data in tables and charts.

**When to use:**
- Adding tables or charts to documents
- Formatting existing data visualizations
- Choosing appropriate chart types

**Contents:**
- Table formatting standards (headers, units, alignment)
- Chart selection guide (when to use each type)
- Examples of effective vs ineffective data presentation
- Accessibility requirements for data visualizations

### reference/visual-formatting.md
Document layout and typography standards for readability and accessibility.

**When to use:**
- Formatting new documents
- Improving readability of existing documents
- Ensuring accessibility compliance

**Contents:**
- Typography standards (fonts, sizes, spacing)
- White space usage guidelines
- Accessibility requirements (contrast, alt text, reading order)
- Page layout best practices

## Deterministic Scripts

### scripts/check-weasel-words.sh
Scans documents for weasel words that require specific replacements.

**When to use:**
- You **MUST** run before finalizing any document
- During document review process
- When feedback indicates vague language

**Parameters:**
- `<file>` - Path to document file to check

**Output:**
- Lists all weasel words found with line numbers
- Exit code 1 if weasel words found, 0 if clean

**Example:**
```bash
./scripts/check-weasel-words.sh my-document.md
```

**What it checks — two word classes:**
- **COMMITTED weasels** (flagged in ANY casing): vague qualifiers (generally, usually, approximately), vague descriptors (very, really, seamless, robust), unquantified quantities (many, most, several, few, various), and soft-time words (soon). These are always weasel — see `reference/weasel-words.md` for the authoritative list.
- **MODAL / hedge words** (should, may, can, would, might, could, likely, seem, appear, tend): flagged ONLY as **lowercase** hedging ("we should improve"). ALL-CAPS RFC-2119 keywords (**MUST** / **SHOULD** / **MAY**) and words inside code spans/blocks are intentionally **exempt** — instructional or code text is not prose hedging.

**What it does NOT catch (check by eye):** a sentence-initial capitalized modal ("Should we ship?"), and the full `reference/weasel-words.md` list is broader than the script — the script is a fast first-pass heuristic, `weasel-words.md` is the authoritative reference.

## Common Mistakes

### Vague Language
**Problem**: Using weasel words like "generally", "usually", "might"
**Fix**: Use specific metrics and commitments

### Burying the Lead
**Problem**: Important information deep in document
**Fix**: Executive summary upfront, recommendations early

### Weak Data Presentation
**Problem**: Unclear tables, missing context, no insights
**Fix**: Clear headers, units, context, and interpretation

### Poor Structure
**Problem**: Inconsistent headings, no logical flow
**Fix**: Logical hierarchy, descriptive headings, clear transitions

### Exceeding Page Limit
**Problem**: Trying to fit too much in main narrative
**Fix**: Move supporting details to appendices, be concise

## Working Backwards

The Working Backwards process **MUST** start with the customer and work backward to the solution. You **MUST** use the 5 Customer Questions framework:

1. **Who is the customer?** You **MUST** be specific about the target customer segment
2. **What is the customer problem or opportunity?** You **MUST** describe the pain point or unmet need
3. **What is the most important customer benefit?** You **MUST** focus on the primary value delivered
4. **How do you know what customers need?** You **MUST** cite research, data, feedback
5. **What does the customer experience look like?** You **MUST** describe the end-to-end experience

### PR/FAQ Format
- **Press Release**: The press release **MUST** be written as if the product launched today
- **FAQ**: The FAQ **MUST** include anticipated questions from customers, press, internal stakeholders
- **Customer-focused**: You **MUST** emphasize benefits, not features
- **Concrete**: You **MUST** include specific examples and use cases

## The Bottom Line

Structured narrative writing transforms complex ideas into clear, actionable documents. Key principles:

1. **Start with the customer** - Working Backwards approach
2. **Be specific** - Avoid weasel words, use metrics
3. **Structure clearly** - Executive summary, logical flow, recommendations
4. **Present data effectively** - Tables, charts, context
5. **Stay concise** - Six-page limit forces clarity
6. **Enable decisions** - Clear recommendations with next steps

Write documents that drive decisions, enable implementation, and align teams around customer needs.

