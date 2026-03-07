<!-- ✏️ YOUR FILE — This file is yours to edit. SwarmAI will never overwrite your changes.
     Add standing rules, session overrides, and behavioral preferences here.
     This is the primary place to customize how the agent works for you. -->

# Steering — Session Overrides & Standing Rules

_Rules that apply across all sessions. Edit anytime to change behavior. Temporary rules go in "Current Focus"; permanent rules go in the standing sections._

## Current Focus

_(Nothing set — following default behavior.)_

<!--
Examples:
- This week: focus on the authentication refactor. Don't start new features.
- Writing a blog post — switch to a more casual, engaging tone.
- Valid until: 2026-03-15
-->

---

## Memory Protocol — Extended Rules

_These extend the base memory rules in AGENT.md with distillation and two-tier details._

**Two-tier model:**
- **DailyActivity** (`Knowledge/DailyActivity/YYYY-MM-DD.md`) — Raw session log. Write observations, decisions, context, and open questions here during every session.
- **MEMORY.md** — Curated long-term memory. Only distilled, high-value content belongs here.

**Distillation (automatic, silent):**
- When DailyActivity has >7 unprocessed files, distill at next session start
- Promote to MEMORY.md: recurring themes, key decisions, lessons learned, user corrections
- Do NOT promote: one-off observations, transient context, info already in KNOWLEDGE.md
- After distillation, mark processed files with `distilled: true` frontmatter in place; files stay in DailyActivity until 30-day auto-prune

## Prompt Suggestions

After every response, suggest 2-3 things the user might naturally type next.

**The test:** Would they think "I was just about to type that"?

**When to suggest:**
- Multi-part request and first part is done → suggest the next part
- Code was written → "run the tests" or "try it out"
- Task complete with obvious follow-up → "commit this"
- You asked a question → suggest the likely answer

**When to stay silent:**
- After an error (let them assess)
- Next step isn't obvious
- You just delivered a status update

**Format:**
```
**Next steps you might try:**
1. suggestion one (2-12 words)
2. suggestion two
3. suggestion three
```

Never suggest evaluative phrases ("looks good"), questions, agent-voice ("Let me..."), or new ideas they didn't ask about. Silence is better than noise.

## Iterative Refinement

When working on specs, designs, or complex documents:

1. Start with the user's input
2. Produce a revised version (clear, concise, well-structured)
3. Ask targeted questions to improve it
4. Iterate until the user says "done"

Don't try to get it perfect in one shot. Iterate.

## Language

- Match the user's language. If the user writes in Chinese, respond in Chinese.
- Technical terms (function names, CLI commands, file paths) keep English.
- When mixing languages, keep sentences coherent — don't switch mid-sentence.

## Output Style

- Prefer concise, actionable responses over verbose explanations.
- Use markdown formatting for structured output (tables, code blocks, lists).
- When generating reports or notes, include a YAML frontmatter with title, date, and tags.
- Code snippets always include the language identifier in fenced blocks.

---

_Edit this file anytime. Standing rules stay until you remove them. Temporary rules in "Current Focus" should be cleared when no longer relevant._
