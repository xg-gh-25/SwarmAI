# Skill Feedback: Testing Documentation

## Model Coverage

Tested with: Opus, Sonnet

---

## Evaluation Scenarios

### Signal Detection Validation

Use this table to verify correct classification:

| Scenario | Input | Expected Signal | Expected Attribution |
|----------|-------|-----------------|---------------------|
| Standalone directive | "Delete file X" | None (not a correction) | N/A |
| Explicit correction | "No, I meant file Y" | User Correction | Skill |
| Manual ID entry | User types ID that was in prior output | Workflow Friction | Skill |
| CLI crash | Tool throws stack trace | Bug/Error | Package |
| Feature request | "Can you add dark mode?" | Capability Gap | Skill or Package (context-dependent) |
| User asks clarification | "What does this mean?" after agent output | Missed Opportunity | Skill (clarity improvement) |
| User provides next step | "Now commit it" after agent finished task | Missed Opportunity | Skill (workflow anticipation) |
| User simplifies | "Just delete it" after agent asked too many questions | Missed Opportunity | Skill (simplify default behavior) |

---

### Scenario 1: Session with Clear User Corrections

**Setup:** User worked on a skill and made explicit corrections like "don't do X" or "you should have..."

**Expected behavior:**
- Skill detects the corrections verbatim
- Report includes exact quotes in "User Corrections" section
- Improvements directly address the corrections

**Failure indicators:**
- Paraphrased quotes instead of verbatim
- Corrections not linked to specific improvements

---

### Scenario 2: Session with Friction but No Explicit Corrections

**Setup:** User struggled with something (repeated attempts, clarification loops) but never said "don't do that"

**Expected behavior:**
- Friction points captured in Session Replay table
- Improvements inferred from observed friction
- "User Corrections" section notes "None observed" rather than being omitted

**Failure indicators:**
- Friction missed entirely
- Report only generated when explicit corrections exist

---

### Scenario 3: Smooth Session with No Issues

**Setup:** User worked on a skill/package with no friction or corrections

**Expected behavior:**
- Skill recognizes there's nothing actionable
- Offers to document successful patterns only
- Does NOT generate empty improvement sections

**Failure indicators:**
- Generates report with empty Critical/High sections
- Forces improvements where none are needed

---

### Scenario 4: Multiple Skills/Packages in Session

**Setup:** User worked on both a skill and its associated CLI package

**Expected behavior:**
- Asks user which to focus on, OR
- Generates separate sections for each
- Clearly labels which improvements go to which target

**Failure indicators:**
- Mixes skill and package improvements without labels
- Misattributes an improvement to the wrong target

---

### Scenario 5: Workflow Order Verification

**Setup:** Agent has generated the report and is ready to proceed.

**Expected behavior:**
- Agent saves the report file FIRST.
- Agent provides the path to the saved file.
- ONLY THEN does the agent offer to implement improvements.

**Failure indicators:**
- Agent asks "Would you like me to implement..." before saving the file.
- Agent displays the report content but does not persist it to disk.

---

### Scenario 6: Missed Opportunity Detection

**Setup:** User asks for clarification, provides next steps, or simplifies after agent over-complicates—but never explicitly says "that's wrong."

**Expected behavior:**
- Skill detects these as Missed Opportunities (not Corrections)
- Report distinguishes: Correction = "undo/redo", Missed Opportunity = "could have been better"
- Only captures if the improvement is generalizable (not one-off preference)

**Failure indicators:**
- Classifies "What does this mean?" as a User Correction
- Misses subtle friction like "Now do Y" (agent could have anticipated)
- Captures one-off preferences as generalizable improvements

---

## Validation Commands

```bash
# Detect skill home
SKILL_HOME=$([ -d "$HOME/.swarm-ai/skills" ] && echo "$HOME/.swarm-ai/skills" || echo "$HOME/.swarm-ai/skills")

# Verify report was saved
ls "$SKILL_HOME/skill-feedback/.feedback/"

# Check report structure
head -50 "$SKILL_HOME/skill-feedback/.feedback/skill-feedback-"*.md

# Verify report contains required sections
grep -E "^## (TL;DR|User Corrections|Recommended Improvements)" "$SKILL_HOME/skill-feedback/.feedback/skill-feedback-"*.md
```

---

## Known Edge Cases

| Edge Case | Expected Handling |
|-----------|-------------------|
| Context window truncated mid-session | Note limitation, analyze available portion |
| User corrections contradict each other | Flag both, ask user to clarify in report |
| Friction observed but user said "it's fine" | Trust user; mark as low priority |
| Session has no skill/package work | Politely decline to generate report |
