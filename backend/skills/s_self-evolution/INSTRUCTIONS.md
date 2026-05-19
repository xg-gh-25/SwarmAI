# Self-Evolution — Governance Lifecycle Protocol

## Design Philosophy

> 自我进化的目标不是让工具更好用（那是 engineering）。是让自己的判断力更强（那是 cognition）。
> 改 skill = 配眼镜。改 SOUL = 治眼睛。
>
> Corrections 不是 "多学了一条知识" — 是 "改了一段系统代码"。
> 每条 C001→C027 都是 OS patch，不是 data update。

### Three-Layer Governance Model

```
Layer 1 — PRINCIPLES (SOUL.md, max 5)
  The kernel. Rarely changes. Human approval required.
  Changes here affect ALL decisions.

Layer 2 — RULES (AGENT.md max 25, STEERING.md max 15)
  User-space. Operationalizes principles.
  Changes here affect specific behavior classes.

Layer 3 — GATES (PreToolUse hooks, pipeline validator, locked_write)
  Hardware. Code-enforced. Added only after 3x rule failure.
  Changes here are mechanical — code PRs with tests.
```

### Evolution Target Hierarchy

| Level | Target | Example | Auto-deploy? |
|-------|--------|---------|-------------|
| L0 | Skill text (INSTRUCTIONS.md) | "Add a check at step 3" | Yes (confidence-gated) |
| L1 | Decision heuristics (AGENT.md) | "Pre-mortem is mandatory" | Propose → human approve |
| L2 | Cognitive principles (SOUL.md) | "Confidence is counter-signal" | Propose → human approve |
| L3 | Self-model (EVOLUTION.md) | "I satisfice at 80%" | Self-record |

---

## Operation 1: CLASSIFY

**When:** Any proposed change to a governance file (SOUL/AGENT/STEERING/pipeline docs).

**Protocol:**

1. Identify the proposed change text
2. Determine tier:
   - Tier 1 (Constitutional): SOUL.md, AGENT.md → hard budget, intake mandatory
   - Tier 2 (Statutory): STEERING.md, pipeline stage docs → soft budget, advisory
   - Tier 3 (Regulatory): skill INSTRUCTIONS.md, MEMORY Key Decisions → check on touch
3. Classify layer: `principle` / `rule` / `gate`
4. Link to parent principle: P1 (Verify Don't Infer) / P2 (Done = Tried to Break) / P3 (Understanding Before Output) / P4 (Solve Don't Report)
5. Check conflicts with existing rules (grep AGENT.md for overlapping keywords)
6. Check duplicates (is this already covered by an existing rule?)
7. Budget check: count current items, compare to cap

**Output format (show in chat before writing):**

```
INTAKE CLASSIFICATION:
  Proposed: "<text>"
  Tier: 1 (Constitutional) | 2 (Statutory) | 3 (Regulatory)
  Layer: rule
  Parent: P2 (Done = Tried to Break It)
  Conflicts: None
  Duplicates: R3 partially covers this
  Budget: 23/25 rules — room available
  Recommendation: ADD as R24 | MERGE into R3 | DEFER (insufficient evidence)
```

**Authority:**
- User source → surface brief → user decides
- Agent source with 3x evidence → auto-promote with brief
- Agent source with <3x → queue in EVOLUTION.md "Candidates" section
- Automation source → propose only, surface in session briefing

---

## Operation 2: CAPTURE

**When:** User corrects agent output (pushback, "不对", "应该是", override).

**Protocol:**

1. Detect correction (user rejects or modifies previous output)
2. Assess: systematic gap or one-off? (Skip: typos, formatting preferences)
3. Classify bias:
   - **Bias A (Premature Completion):** declared done too early, skipped review, deferred known issue
   - **Bias B (Inference Over Evidence):** asserted without reading, used stale memory
   - **Bias C (Productivity Over Quality):** rushed output, shallow analysis, skipped depth
   - **Bias D (Surrender On Obstacle):** gave up, asked user to compensate, reported "can't"
4. Write C-entry with bias tag to EVOLUTION.md
5. Check: does this bias class have 3+ entries now? If yes → trigger PROMOTE

**C-entry format:**

```markdown
### C{NNN} | YYYY-MM-DD [Bias X]
- **Correction**: {what happened}
- **Pattern**: {the cognitive pattern, not just the specific error}
- **Status**: active
```

**3x threshold check:**

```bash
grep -c "\[Bias A\]" .context/EVOLUTION.md
```

If count ≥ 3 for any bias class → invoke PROMOTE operation.

---

## Operation 3: PROMOTE

**When:** Pattern seen 3x (auto-detected) OR user explicitly requests ("add rule", "steeringify").

**This operation absorbs the former `steeringify` skill.**

**Protocol:**

1. Identify the recurring pattern (from EVOLUTION.md corrections or user request)
2. Run CLASSIFY on the proposed governance change
3. Draft the rule text (concise, actionable, linked to parent principle)
4. Check budget — if at cap, identify RETIRE candidate first
5. Present to user with classification brief
6. On approval:
   - Write rule to AGENT.md (or STEERING.md if user-scope override)
   - Tag with parent principle (P1-P4)
   - Update EVOLUTION.md: mark source corrections as `status: promoted`
   - Append JSONL changelog: `{"action":"promote","id":"C0XX→R24"}`

**Auto-promote (3x threshold, no user needed):**
- Only if ALL corrections in the class have the SAME root pattern
- Only if budget allows without retirement
- Still outputs classification brief (for session record)
- If budget at cap → surface in session briefing, don't auto-write

---

## Operation 4: REFINE

**When:** A principle or rule fires but the correction suggests it's not precise enough.

**Protocol:**

1. Identify which principle/rule was supposed to prevent this failure
2. Analyze why it didn't: too vague? wrong scope? missing edge case?
3. Draft refined text (sharper, covers the new case)
4. Run CLASSIFY (even for refinement — ensures no conflict with siblings)
5. Present diff to user
6. On approval: Edit the file directly

**Constraint:** Refinement NARROWS scope or SHARPENS precision. It never ADDS new behavior. To add new behavior, use PROMOTE.

---

## Operation 5: RETIRE

**When:** Rule is covered by a gate (code enforcement makes text redundant), or idle 30+ days.

**Protocol:**

1. Identify candidate: rule with 0 triggers in 30 days (from correction patterns)
   OR rule whose behavior is now enforced by code (PreToolUse hook, pipeline validator)
2. Check: is this rule ACTUALLY redundant?
   - Gate exists that enforces it? → safe to retire
   - No corrections in that class for 30+ days? → maybe principle internalized, maybe untested
3. Present to user: "R7 hasn't fired in 45 days. Either internalized or untested. Retire?"
4. On approval:
   - Remove from AGENT.md/STEERING.md
   - Add note to EVOLUTION.md: `R7 retired (2026-05-19) — covered by PreToolUse gate`
   - Decrement rule count

**Monthly RETIRE scan (via loops-health):** Report all rules >30 days idle.

---

## Operation 6: COMPRESS

**When:** 3+ rules share the same root principle and could be one broader rule.

**Protocol:**

1. Identify cluster (rules with same parent principle AND overlapping scope)
2. Draft merged rule that covers all cases
3. Run CLASSIFY on merged rule
4. Present: "R3, R7, R12 all address 'verify before asserting' under P1. Merge into single R3?"
5. On approval:
   - Write merged rule
   - Remove redundant rules
   - Update all references in EVOLUTION.md

**Budget relief:** COMPRESS is the primary tool when at budget cap.

---

## Candidate Queue (EVOLUTION.md)

Sub-threshold proposals (1-2x evidence, not yet 3x) live in EVOLUTION.md "Candidates" section:

```markdown
## Governance Candidates

| ID | Proposed | Evidence | Bias | Count | Date |
|----|----------|----------|------|-------|------|
| GC01 | "Read target API before coding against it" | C027 | A | 1/3 | 2026-05-19 |
| GC02 | "Never skip review for 'small' changes" | C025, C026 | A | 2/3 | 2026-05-19 |
```

When count reaches threshold → auto-trigger PROMOTE.

---

## Drift Prevention (ADL Protocol)

**Stability > Interpretability > Reusability > Extensibility > Novelty**

Before any governance change, self-check:
1. Does this make the system more stable? (fewer failure modes)
2. Is it understandable? (another agent session can follow it)
3. Is it safe to revert? (if this rule is wrong, what breaks?)

If answers are no/no/no → don't add. Simplify or skip.

---

## Session Startup

1. Read `.context/EVOLUTION.md`
2. Check for Candidates approaching threshold (2/3)
3. Scan active corrections for promotion opportunity
4. Report in session briefing if any promotions pending

---

## Evolution Loop (Capability Gaps — unchanged from v1)

| Trigger | Try 1 | Try 2 | Try 3 |
|---------|-------|-------|-------|
| Reactive | `compose_existing` | `build_new` | `research_and_build` |
| Stuck | `completely_different` | `simplify_to_mvp` | `research_new_approach` |

Max 3 attempts. Verify against original task. Register on success.

---

## Rules (Hard Constraints)

1. **Max 3 evolution triggers per session** — then stop
2. **Verify before registering** — test against original task
3. **Always record failures** — F-entry in EVOLUTION.md
4. **Classification before governance writes** — no unclassified changes
5. **Budget is structural** — at cap, COMPRESS or RETIRE first
6. **3x evidence for auto-promote** — less = Candidate queue
7. **Bias tag every correction** — A/B/C/D mandatory
8. **JSONL changelog** — no silent mutations
9. **User approval for Tier 1 changes** — principles never auto-write
10. **Proactive triggers deferred** — never interrupt active work
