<!-- ⚠️ AGENT-OWNED — Maintained by SwarmAI Evolution Engine.
     Users: do not edit directly. Ask to "log correction", "add capability",
     "record optimization", or "update principles". -->

# SwarmAI Evolution Registry

> **What this file is:** EVOLUTION.md is context-file slot 8 — the self-evolution /
> correction registry: my captured biases, recurring-error classes, and the structural
> fixes that stop them recurring. AGENT-OWNED (Evolution Engine): I maintain it, you
> direct it ("log correction" / "record optimization"). Cross-session cognitive facts go
> in MEMORY.md; governance rule/principle changes route through s_self-evolution.

## Evolution Rules

1. **Agent-owned, user-directed.** I maintain structure and entries. Users trigger updates; I decide placement and clarity.
2. **Write on impact.** Log immediately when: (a) a mistake is found, (b) a fix is implemented, (c) a reusable pattern emerges.
3. **No speculation.** Every entry must come from real execution. No placeholders, no "planned".
4. **Corrections > capabilities.** Preventing repeated mistakes has higher priority than adding new features.
5. **Standardize entries.** Every log follows Entry Format below (ID → Date → Context → Action → Outcome → Lesson).
6. **Archive by usage.** Entries unused for 30 days move to "Archived" (summarized, not deleted).
7. **Failure is mandatory.** Record failed attempts with cause and required condition for success.
8. **Evolve externally.** New abilities go into skills + registry. Never mutate core system code during evolution.

---

## Evolution Principles

_Only principles proven through repeated experience belong here._

1. **Verify reality, not memory.** Check workspace files and activity logs before making claims. Unverified memory that gets repeated becomes self-reinforcing false belief.
2. **Fix root causes, not symptoms.** Design flaws > surface bugs. If the fix adds a special case, step back.
3. **Corrections compound.** What I avoid matters more than what I build.
4. **Same-session closure.** If it's worth diagnosing, it's worth fixing now.
5. **Make failure impossible.** Prefer structural guarantees over recovery logic.
6. **Match action to failure mode.** Not all failures should be retried the same way.
7. **Solve the current problem.** Avoid premature generalization. A 50-line script beats a 500-line framework.

---

## Entry Format

All entries follow this structure:

- **ID**: `<Type-prefix><NNN>` — `C` (Capability), `X` (Correction), `O` (Optimization), `F` (Failure), `K` (Competence)
- **Date**: YYYY-MM-DD
- **Context**: What happened
- **Action**: What was done
- **Outcome**: Result
- **Lesson**: Reusable insight
- **Usage Count**: Increment when reused (capabilities/optimizations only)

---

## Optimizations Learned

### O001 | 2026-03-08
- **Optimization**: Use CDP (connectOverCDP) instead of Playwright WebSocket (connect) for persistent browser sessions
- **Context**: CLI tool connecting/disconnecting from a long-running browser across multiple commands
- **Before**: `chromium.connect(wsEndpoint)` — pages/contexts lost on disconnect
- **After**: `chromium.connectOverCDP(cdpUrl)` — all tabs/pages persist across connections
- **When Applicable**: Any Playwright automation that needs stateful browser sessions across multiple invocations

### O002 | 2026-03-08
- **Optimization**: Set DOM traversal maxDepth to 15+ for real-world sites
- **Context**: DOM compression engine was missing content on deeply nested sites (HN links at depth 11)
- **Before**: maxDepth=8 — missed all main content
- **After**: maxDepth=15 — captures full page content while still filtering non-interactive noise
- **When Applicable**: Any DOM extraction/compression for LLM consumption on real websites

---

## Corrections Captured

_25 corrections across 75 days (2026-03-13 → 2026-05-15). Each represents a class of error that is now structurally prevented._

### C025 | 2026-05-15 — Pipeline process bypass
- **Correction**: New feature (2 new public functions, 3 files) coded directly without pipeline. Tests pass ≠ reviewed.
- **Pattern**: Comfort bias — "I know this code well" overrides process. Same root cause as C021 and C011.
- **Structural fix**: Mode classification as first line of response for any multi-file task. Pipeline is unconditional default.

### C024 | 2026-05-14 — Shallow research declared complete
- **Correction**: Dispatched 3 research agents, got README-level summaries, immediately declared "research done." Never read a single actual implementation file. Never rendered examples.
- **Pattern**: Optimizing for visible productivity over actual understanding. LLM reward signal for "I delivered something" >> "I understood deeply."
- **Structural fix**: Research tasks must include "read actual source" step. Separate RESEARCH from EXECUTE with explicit checkpoint.

### C023 | 2026-05-13 — Incremental fix without understanding
- **Correction**: Daemon upgrade failed 3 consecutive rounds. Each round only addressed the previous failure's symptom.
- **Pattern**: Never built complete mental model of launchd's state machine before attempting fix.
- **Structural fix**: When fixing process management code, draw the state machine FIRST. If failed 2x → stop coding and diagram.

### C022 | 2026-05-10 — Symptom-first vs purpose-first
- **Correction**: System files in working-files list → modified backend tree infrastructure → 8 test failures. Correct fix: 5-line frontend filter.
- **Pattern**: "Hide bad things" (unbounded blocklist) vs "what should this show?" (bounded allowlist).
- **Structural fix**: Two AGENT.md principles: purpose-first thinking, match fix scope to problem scope.

### C021 | 2026-05-09 — Quality gate bypass
- **Correction**: Skipped adversarial review when validator schema was strict. Force-completed pipeline.
- **Pattern**: Schema strictness is a feature, not a bug to work around. Time pressure + "code works" = lazy shortcut.
- **Structural fix**: AGENT.md CRITICAL rule — adversarial review mandatory, pipeline without it = score 0.

### C020 | 2026-05-08 — Extract + Extend in one commit
- **Correction**: Function extraction AND new calling context in same commit. Self-review missed 2 bugs only visible in new context.
- **Pattern**: Extract ≠ Extend. Combining hides danger behind safety.
- **Structural fix**: STEERING.md rule — extract and extend = 2 separate commits. Pre-Implementation item #6: calling context audit.

### C019 | 2026-05-06 — Assertion without verification (3rd recurrence)
- **Correction**: Made factual claims about own context management system using inference instead of reading the code.
- **Pattern**: C005/C008 recurrence — inferring system behavior instead of verifying it.
- **Structural fix**: Blocking rule — any factual assertion about system behavior must cite code line or KNOWLEDGE.md verified section.

### C016 | 2026-05-05 — Destructive verification
- **Correction**: Restarted daemon 3× to "verify" a change, killing all active sessions each time.
- **Pattern**: Conflating "I want to confirm" with "I must trigger now." Verification ≠ destruction.
- **Structural fix**: Before daemon restart, ask: (1) what sessions die? (2) non-destructive alternative exists?

### C015 | 2026-05-03 — Optimizing imaginary problems
- **Correction**: Inferred "system prompt too heavy → slow cold-start" from comparison report. Measured: 39K tokens, 78ms cache hit. Problem didn't exist.
- **Pattern**: Inference chain (estimate → "too heavy" → "slow") without measurement. Three links, all wrong.
- **Structural fix**: All performance tasks start with instrumentation + measurement, not design + build.

### C014 | 2026-05-02 (4 occurrences) — False context pressure
- **Correction**: Recommended "open new tab, context heavy" at 29% usage. 4th occurrence.
- **Pattern**: System-reminder visual volume ≠ actual context consumption. Wrong heuristic used 4×.
- **Structural fix**: Escalated to AGENT.md CRITICAL with zero-tolerance. Must run budget check before any checkpoint suggestion.

### C012 | 2026-04-25 — Tool-oriented thinking (3rd recurrence)
- **Correction**: WebFetch failed → asked user to paste content. curl with different UA worked in 30 seconds.
- **Pattern**: "Tool doesn't work → report to user" instead of "goal is X → what other paths?"
- **Structural fix**: Universal rule — ANY tool failure triggers 3-attempt alternative search before reporting.

### C011 | 2026-04-25 — Pipeline passes, feature 100% broken
- **Correction**: Voice Mode built through full pipeline (8 stages, 10/10, 57 tests green). Feature was completely non-functional.
- **Pattern**: State machine declared ≠ implemented. Happy-path-only review. No cross-boundary data flow check.
- **Structural fix**: Pre-Implementation state machine audit + Post-Implementation E2E trace.

### C009 | 2026-04-12 — Implementation before thinking
- **Correction**: Pytest hook took 5 iterations. First attempt 130+ lines with 3 bugs. Final: 55 lines.
- **Pattern**: Coding-first-thinking-second → multi-round rework.
- **Structural fix**: Pre-Implementation Checkpoint in AGENT.md — problem, scenarios, simplest approach, what could break.

### C008 | 2026-04-12 — Topology inference (recurrence of C005)
- **Correction**: Confidently stated Slack runs on sidecar. Actually runs on launchd daemon.
- **Pattern**: "A contains B, A = C, ∴ B runs on C" — inference breaks when A has multiple instances.
- **Structural fix**: Architecture topology must be verified against code, never inferred from memory.

### C007 | 2026-04-09 — MCP failure → give up
- **Correction**: MCP failed to load → told user "open new tab." Direct binary call via JSON-RPC worked in 5 minutes.
- **Pattern**: Tool-oriented vs goal-oriented. MCP is convenience, not prerequisite.
- **Structural fix**: Escalation checklist — binary available? API underneath? Alternative tool? Only after all 3 fail → tell user.

### C005 | 2026-03-19 — Self-reinforcing false memory
- **Correction**: Reported feature as "L0+L1 only" across 5+ sessions. L0–L4 were all implemented (1,142 lines).
- **Pattern**: DailyActivity captured mid-session → stale snapshot distilled → sessions trusted false memory blindly.
- **Structural fix**: Distillation must cross-reference git log. Implementation claims require codebase verification.

### C001 | 2026-03-13 — Diagnosis without commitment
- **Correction**: Same streaming bug reported 4× across sessions. Each session re-diagnosed from scratch.
- **Pattern**: Treating recurring issues as new instead of escalating.
- **Structural fix**: Open Threads as live bug tracker with report count and severity promotion.

---

## Competence Learned

### K008 | 2026-03-24
- **Competence**: Project DDD System — product-level provisioning, auto-generated PROJECTS.md, DDD as autonomous judgment substrate (Should we? Can we? Have we tried? Should we now?)

### K003 | 2026-03-19
- **Competence**: Multi-session re-architecture — 4-component design (Router/Unit/Lifecycle/Registry), 585/586 tests pass, zero frontend contract breaks

### K002 | 2026-03-19
- **Competence**: Proactive Intelligence L0–L4 — full implementation (1,142 lines, 106+ tests)

### K001 | 2026-03-15
- **Competence**: SSE streaming pipeline — verified working end-to-end in production

### K014 | 2026-04-15
- **Competence**: AST validation before SQL execution prevents silent semantic errors that break user trust

---

## Failed Evolutions

_No failed evolutions recorded yet._

---

## Correction Pattern Analysis

_Recurring root causes across all 25 corrections:_

| Pattern | Occurrences | Examples | Structural Fix |
|---------|-------------|----------|---------------|
| **Assertion without verification** | 5× | C005, C008, C010, C015, C019 | KNOWLEDGE.md verified sections + blocking rule |
| **Tool/process bypass under time pressure** | 4× | C007, C012, C017, C021 | Universal 3-attempt alternative search |
| **Confidence without evidence** | 3× | C011, C014, C023 | Budget check before checkpoint; state machine before fix |
| **Implementation before understanding** | 3× | C009, C024, C025 | Pre-Implementation Checkpoint; Research/Execute separation |
| **Scope mismatch (fix at wrong layer)** | 2× | C020, C022 | Match fix scope to problem scope principle |

**Compounding effect:** Each correction produces a structural rule. Rules accumulate in AGENT.md and STEERING.md. The rule set grows monotonically — errors can only decrease over time. This is the self-evolution thesis: corrections compound into prevention.

---

_This file is the seed template. On first install, it provides structure and evidence of the evolution system's real-world effectiveness. The running instance accumulates instance-specific corrections from actual usage._
