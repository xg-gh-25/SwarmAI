# Grill Me — Full Workflow

Interview the user relentlessly about every aspect of their plan until reaching
shared understanding. Walk down each branch of the decision tree, resolving
dependencies between decisions one by one.

## Process

### 1. Understand the Plan

Read whatever plan, design, or proposal the user has shared. If there's a design
doc, PRD, or spec — read it first. If it's in the conversation context, that's
fine too.

If the plan is too vague to grill ("improve things"), say so:
> "I need something concrete to grill. What specifically are you building, and
> what's the first decision you're unsure about?"

### 2. Map the Decision Tree

Before asking anything, silently identify:
- The **major decisions** in the plan (architecture, scope, approach, data model)
- **Dependencies** between decisions (choosing X forces Y)
- **Assumptions** that aren't stated (implicit "this will be easy" or "users want this")

### 3. Grill One Branch at a Time

Ask questions **one at a time**. For each question:

1. **State what you're probing:** "Let me dig into the data model."
2. **Ask the specific question:** "You're storing signals in JSON files. What
   happens when two jobs write simultaneously?"
3. **Provide your recommended answer:** "I'd recommend file-level locking via
   fcntl — it's what we use for MEMORY.md. Alternatively, switch to SQLite."
4. **Wait for the user's response** before moving to the next question.

The user can:
- **Accept** your recommendation → move on
- **Override** with their own answer → capture their reasoning, move on
- **Discuss** → explore the tradeoff, then resolve
- **"Just pick"** → accept all remaining recommendations at once

### 4. Explore the Codebase When Possible

If a question can be answered by reading the code, **read it instead of asking**.

Bad: "Do you have a dedup mechanism?"
Good: *reads signal_fetch.py* → "You already have URL-based dedup with a 7-day
cache. This new feed will go through the same pipeline, so dedup is covered."

### 5. Capture Decisions

As decisions crystallize during the grilling, capture them:

```markdown
### Decisions Resolved

| # | Decision | Resolution | Rationale |
|---|----------|-----------|-----------|
| 1 | JSON vs SQLite for storage | JSON files | Matches existing signal pipeline pattern |
| 2 | Scraping vs API | Scraping | GitHub has no trending API |
| 3 | ... | ... | ... |
```

### 6. Summarize and Hand Off

After all branches are resolved:

```markdown
### Grilling Complete

**Decisions made:** N
**Assumptions validated:** M
**Risks identified:** K (with mitigations)

**The plan is solid / needs revision on [specific area].**

Ready to proceed to: [implementation / design doc / further research]
```

## Rules

- **One question at a time.** Never dump 5 questions. It overwhelms and gets
  shallow answers.
- **Always recommend.** "What do you think?" is lazy. "I'd recommend X because
  Y — do you agree?" is useful.
- **Max 10 questions** per grilling session. Scarcity forces you to pick the
  questions that matter most.
- **Don't re-litigate resolved decisions.** If the user already decided X in
  a previous session or it's in IMPROVEMENT.md, acknowledge it and move on.
- **Challenge assumptions, not preferences.** "You assumed this is O(1) — is it?"
  is good. "Why Python instead of Go?" is not (unless it's genuinely relevant).
- **Kill sacred cows.** If the plan has an obvious weakness everyone's avoiding,
  name it directly. Politeness is not the goal; clarity is.
