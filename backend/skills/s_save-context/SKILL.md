---
name: Save Context
description: Create a structured handoff document for the next agent session. Use when switching agents, ending work, or when user says "save context", "handoff", or "wrap up". Not for persistent memory (use save-memory instead).
---

# Save Context

Generate a conversation summary for seamless agent handoffs.

## Scope

This skill captures what happened in the current session AND maintains continuity across sessions:

**Capture from THIS session:**
- User requests and goals
- Actions taken and decisions made
- The reasoning behind pivots or changes in direction
- Open questions that weren't resolved
- What was built but not yet validated

**Maintain continuity:**
- Note items carried forward from previous context that weren't addressed
- Preserve user working style observations for future agents

**DO NOT:**
- Run git commands (no `diff`, `log`, `commit`, `status`)
- Explore the filesystem with `ls`, `find`, etc.
- Gather information about the broader project/repository state
- Make any commits

The conversation history is already in your context. Extract from it directly.

## Core Principle: Detail Proportional to Openness

Not all topics deserve equal attention. Calibrate detail based on topic state:

| Topic State | Detail Level | What to Include |
|-------------|--------------|-----------------|
| **Closed/Solved** | Brief | Outcome only. "We decided X because Y." |
| **Attempted, still open** | Detailed | What was tried, why it didn't work, what insights emerged, what to try next |
| **Identified, not started** | Minimal | Just mention it exists and why it matters |
| **Rejected** | Brief + reason | What was proposed, why user rejected it - prevents next agent from repeating |

The next agent needs context for what's **actionable**, not a history of everything discussed. Closed topics are background; open topics are foreground.

## Workflow

1. **Check for loaded context** — Was a previous `.context/` file loaded at session start? Note any pending items from it.
2. **Analyze the conversation** — Review what was discussed, requested, and done.
3. **Identify reasoning pivots** — Were there moments where initial proposals were challenged and direction changed?
4. **Note open items** — What was raised but not resolved? What was built but not tested?
5. **Observe user style** — Look for patterns:
   - Do they push back on proposals? (values simplicity, challenges assumptions)
   - Do they ask "is this state of the art?" (expects rigor)
   - Do they prefer brief responses or detailed explanations?
   - Do they redirect tangents quickly? (values focus)
6. **Ask clarifying questions** (only if critical gaps):
   - "What's the most important thing the next agent should know?"
   - "Any blockers or decisions pending your input?"
7. **Generate the context document**
8. **Save to `.context/`**
9. **Ensure .gitignore protection**

## Template

Generate **only sections with content**. Omit empty sections entirely - don't include placeholder text or "[N/A]".

```markdown
# Session Handoff: [Brief Topic]
**Date:** YYYY-MM-DD HH:MM
**Status:** [ready to continue | blocked on X | needs decision | changes uncommitted]

## TL;DR
[2-3 sentences: what was requested, what happened, what's next]

---

## User Working Style
[How this user communicates and what they value. Examples: "Pushes back on over-engineering", "Prefers brevity", "Challenges assumptions with 'is this state of the art?'", "Values simplicity over completeness". Helps next agent calibrate communication.]

## What Was Requested
[User's goal/task from this conversation - be specific]

## Key Quotes
[1-2 exact quotes from the user that capture critical intent or reasoning. Use when the user's own words convey something that paraphrase would dilute. Omit if no quotes are essential.]

## Carried Forward
[Items from a previously-loaded .context/ file that were mentioned but not addressed this session.]

## What Was Done
- [Specific action - include file paths if files were edited]
- [Another action]

**Validation status:** [Tested / Untested / Partially tested - list what needs verification]

## Topics Closed
[Brief summary of topics that are resolved. Just outcomes, not process. Example: "Router vs convention debate → decided on convention-based approach because it's simpler and avoids middleware."]

## Topics In Progress
[Detailed coverage of topics attempted but not resolved. Include: what was tried, why it didn't work or wasn't decided, what insights emerged, recommended next steps. This is where the next agent should focus.]

## Topics Identified (Not Started)
[Brief list of topics that came up but weren't addressed. Just enough for next agent to know they exist.]

## Approaches Rejected
[Approaches that were proposed and explicitly rejected by the user, with brief reason why. Prevents next agent from retreading failed paths. Omit if no approaches were rejected.]

## Open Questions
[Decisions or topics raised but explicitly left unresolved. Different from "Pending" - these are discussed but undecided, not work remaining.]

## Files Touched This Session

| File | Action | What Changed |
|------|--------|--------------|
| `path/to/file` | Created/Modified | Brief description |

**Commit status:** [Committed / Uncommitted / Partially committed]

## Known Issues Discovered
- [Issue found during this session]

## Pending / Blocked
- [Work remaining to be done]
- **Blocked on:** [if applicable]

## Continue From
[Direct instruction: where to pick up, what to do first]

---

**For next agent:** [One-line instruction on immediate next step]
```

## File Management

### Location (in project root) & Naming

```
.context/
├── 2025-01-03-1430.md
└── 2025-01-02-0900.md
```

```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
mkdir -p "$PROJECT_ROOT/.context"
FILENAME="$PROJECT_ROOT/.context/$(date +%Y-%m-%d-%H%M).md"
```

### Gitignore Protection

Context files should NEVER be pushed to remote repositories.

After saving the context file, ensure `.context/` is protected:

```bash
if [ ! -f .gitignore ]; then
    echo ".context/" > .gitignore
    echo "Created .gitignore with .context/"
elif grep -q "^\.context" .gitignore; then
    echo "Already protected"
else
    echo "" >> .gitignore
    echo ".context/" >> .gitignore
    echo "Added .context/ to .gitignore"
fi
```

## Quality Checklist

Before saving:
- [ ] TL;DR alone gives clear picture of the session
- [ ] No empty sections or placeholders - omit instead
- [ ] Only includes work from THIS session (not repo-wide state)
- [ ] "Continue From" is specific and actionable
- [ ] Can be understood without access to this chat
- [ ] Detail is proportional to openness (closed=brief, in-progress=detailed)
- [ ] Topics In Progress includes what was tried and why it didn't resolve
- [ ] User Working Style is observational, not judgmental
- [ ] Validation status is explicit (tested vs untested)

## Edge Cases

| Situation | Approach |
|-----------|----------|
| No meaningful work done | Don't generate a context file - tell the user |
| Only exploration/research | Document findings and recommendations |
| Multiple unrelated tasks | Create sections for each topic |
| Session was debugging | Emphasize findings, root cause, fix status |
| Previous context was loaded | Check which items were addressed vs carried forward |
| User challenged initial approach | Capture in Topics In Progress or Topics Closed - the reasoning is valuable context |
| First session with user | Omit User Working Style, or note "First session - no patterns yet" |
| Everything was tested | Note "Validation status: All changes tested" in What Was Done |

## Anti-Patterns

| Don't | Do Instead |
|-------|------------|
| Run `git diff` or `git log` | Extract from conversation what YOU did |
| Paste chat history | Summarize key points |
| Include repo-wide file listings | Only files YOU touched this session |
| Vague "continue working" | Specific next action |
| Include "[TBD]" or "[N/A]" placeholders | Omit the section entirely |
| List decisions without reasoning | Explain why each choice was made |
| Confuse "unresolved" with "pending" | Open Questions = undecided; Pending = work remaining |
| Judge user style | Observe objectively: "pushes back on complexity" not "is picky" |
| Bury validation status | Make it explicit - next agent needs to know what's tested |
| Omit rejected approaches | Next agent will waste time proposing the same thing |
| Paraphrase when exact words matter | Use Key Quotes for critical user intent |
