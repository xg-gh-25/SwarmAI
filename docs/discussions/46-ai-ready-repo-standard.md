# Your AI Agent Can't "Just Read" Your Codebase — Here's What It Actually Needs

## The Problem Nobody's Solving Right

Every AI coding tool (Claude Code, Kiro, Codex, Cursor) faces the same cold-start problem: your agent can parse syntax but can't read your team's mind.

Current solutions:
- **CLAUDE.md** → Build commands + basic rules. Enough to not break the build. Not enough to make good decisions.
- **AGENTS.md** → A step up. Navigation + conventions. But still a flat file trying to encode everything.
- **Steering docs / System specs** → Better structure, but static. Die within weeks.

These all solve the same narrow problem: **"Where do I find things?"**

But the real question an AI agent faces is broader: **"Should I do this, and if so, how do I not make it worse?"**

That requires judgment. Judgment requires context that goes beyond file navigation.

---

## The Insight: What Agents Actually Need

After running AI-assisted development across 8 projects for 3 months, we identified 5 layers of understanding an agent needs:

| Layer | Question | Example |
|-------|----------|---------|
| **Purpose** | Why does this exist? What's out of scope? | "We don't cache exchange rates — regulatory requires real-time" |
| **Architecture** | How is it built? What patterns are sacred? | "All DB access through repository.ts, never raw SQL" |
| **History** | What failed before? What are the known gotchas? | "Event sourcing tried in Q1, reverted after 3 incidents" |
| **Current State** | What's the priority now? What shouldn't I touch? | "Auth migration in progress — don't touch identity module" |
| **Structure** | What depends on what? What's the blast radius? | Module dependency graph |

A single flat file can cover maybe 2 of these. That's why agents keep making confidently wrong decisions — they have navigation but no judgment.

---

## The Approach: Domain-Driven Documentation (DDD)

We use a 4-file structure that maps directly to these understanding layers:

```
.ai-ready/
├── PRODUCT.md       → Purpose, audience, non-goals, constraints
├── TECH.md          → Architecture, conventions, invariants, key decisions
├── IMPROVEMENT.md   → What failed, what works, evidence-grounded gotchas
└── PROJECT.md       → Current priorities, active decisions, blockers
```

Plus:
- **AGENTS.md** at project root → ≤150 line entry point that links to the above
- **code-intel.json** → Structured dependency graph (queryable, not prose)

### Why This Structure (Not a Single File)

**For agents**: Progressive loading. Different tasks need different context:
- "Add a new endpoint" → needs TECH.md only
- "Should we add caching?" → needs PRODUCT.md (constraints) + IMPROVEMENT.md (past decisions)
- "Refactor module X" → needs TECH.md + IMPROVEMENT.md + code-intel graph

A 600-line mega-file wastes context window. 4 files × 150 lines lets agents load only what they need.

**For humans**: Different people own different docs:
- PM reviews PRODUCT.md → "Does this capture our intent?"
- Engineer reviews TECH.md → "Are these our real conventions?"
- Team reviews IMPROVEMENT.md → "Are these gotchas current?"
- Lead reviews PROJECT.md → "Are these the actual priorities?"

Nobody has to read things outside their domain.

---

## 3 Levels of AI-Readiness

| Level | What's Present | Agent Capability |
|-------|---------------|-----------------|
| **Navigable** | AGENTS.md + TECH.md + code-intel | Can find files, run build, follow conventions |
| **Safe** | + IMPROVEMENT.md | + Avoids known pitfalls, understands blast radius |
| **Autonomous** | + PRODUCT.md + PROJECT.md | + Makes judgment calls, respects boundaries |

Most repos today are between 0 and Level 1. The jump from Level 1 to Level 3 is where agent behavior changes from "useful tool" to "reliable teammate."

---

## The Hard Part: Keeping It Alive

Every team that's ever written architecture docs knows the real problem: **they rot within weeks.**

Static documentation is a lie that gets more wrong every day. For AI context, it's worse — stale context doesn't just fail to help, it actively misleads. An agent that "knows" a convention that was changed 3 months ago will confidently write wrong code.

Our approach to content freshness:

### Evidence-Grounded Content
Every entry in IMPROVEMENT.md must cite its source:
```
WHEN: modifying webhook handler
RISK: order-dependent state corruption
BECAUSE: commits abc123, def456 (March 2026, 3 incidents)
```

If you can't cite evidence, don't write it. Vibes aren't knowledge.

### Decay Detection
If a gotcha references code that was refactored but the gotcha wasn't re-verified:
```
[⚠️ unverified 14d] The "pending" state can last 72h for bank transfers
```

Agents treat unverified entries as "possibly stale, verify before relying."

### Structural Change Detection
A lightweight script (bash, zero dependencies) checks:
- New directories appeared?
- Build config changed?
- New route/API files?
- 50+ commits since last refresh?

If yes → flag as stale. Doesn't require AI to detect — just file system awareness.

### Bidirectional Knowledge Flow

Most context systems only handle one direction: code changes → docs update. But there's a second direction that matters just as much:

**User provides knowledge → artifacts enriched.**

Think about it: your PM tells the agent "we never deploy on Fridays because of provider maintenance." Your engineer corrects the agent "no, always use the repository pattern." A new design doc lands in the workspace.

Without a path for these to flow INTO the DDD files, they're lost next session.

Our approach: the refresh skill has a "learn" mode with three trigger paths:

1. **Explicit**: `"ai-ready learn: feature flags required for all new endpoints"` → classified → appended to TECH.md conventions
2. **Document ingest**: New design doc appears → agent asks "extract into DDD context?" → user confirms → relevant claims distributed to correct files
3. **Correction capture**: Agent is corrected → "Want me to remember this?" → yes → persisted to appropriate DDD file

Each entry gets a source tag: `[added: 2026-05-29, source: user]` or `[source: design-auth-v2.md]`. This means:
- The system accumulates knowledge from daily work (not just initial generation)
- Tier 3 re-gen reads all learned entries as baseline (never loses them)
- Human-provided knowledge has clear provenance (who said what, when)

The lifecycle becomes:
```
Day 1:  Engine generates baseline from code + signals
Day 5:  User drops PRD → ingest → PRODUCT.md enriched
Day 8:  Agent corrected → captured → TECH.md gains convention
Day 30: Tier 2 refresh incorporates code changes (preserves all learned entries)
Day 90: Full re-gen builds on everything accumulated — never starts from zero
```

This is the difference between "documentation that you write once and maintain manually" and "knowledge that compounds from every interaction."

---

## The Scoring Rubric (Self-Assessment)

We score AI-readiness across 8 dimensions (0-10 each):

| Dimension | What It Measures |
|-----------|-----------------|
| Navigation | Can agent find things without asking? |
| Build/Test | Can agent verify its work? |
| Architecture | Does agent understand boundaries? |
| Conventions | Does agent follow your patterns? |
| Tribal Knowledge | Does agent know the gotchas? |
| Code Graph | Can agent predict blast radius? |
| Test Safety | Is there a safety net for mistakes? |
| Ops Context | Does agent understand production? |

**Most repos score 2-3 out of 10.** Adding just AGENTS.md + TECH.md typically jumps to 5-6. Full DDD gets to 7-9.

---

## Real Scenarios

### The Engineer
Agent keeps adding direct DB calls (violating repository pattern) because nothing told it not to. After adding TECH.md with conventions: problem disappears on day 1.

### The PM
Agent proposed caching exchange rates (regulatory violation). Would have shipped if not caught in code review. After adding PRODUCT.md with constraints: agent cites the constraint and refuses.

### The Knowledge Expert
Two senior engineers leaving. 4 years of tribal knowledge in their heads. After mining git history into IMPROVEMENT.md: 15 evidence-grounded gotchas preserved. New developers' agents warn them automatically.

### The Director
12 services, 4 teams. Some teams' agents work great, others constantly make mistakes. Scoring all repos reveals: the struggling teams have zero documentation (score 2/10). Investment prioritized by data.

---

## Design Principles

1. **Every line earns its place** → "Will the agent make a systemic mistake without this line?" If no, delete it.
2. **Judgment > Description** → "Never call Stripe API directly — use stripe-client.ts (has retry + idempotency)" beats "stripe-client.ts exists."
3. **Evidence-grounded** → Backed by commit hash, issue number, or incident. Can't ground it? Don't write it.
4. **Two human touchpoints** → Generation needs just two inputs from humans. Everything else can be automated.
5. **≤150 lines entry point** → Longer context files show diminishing returns on accuracy. Depth goes in linked docs.
6. **Detect, don't assume** → Never ask "is this Java or Python?" — infer from code.
7. **Knowledge must evolve or die** → Built to be refreshed, not a one-time write.

---

## The Open Question

We've been using this structure with success, but we're curious what the community thinks:

1. **4 files vs fewer?** Is the PRODUCT/TECH/IMPROVEMENT/PROJECT split the right granularity? Or would 2-3 files suffice?
2. **Who maintains PROJECT.md?** It has a ~2-4 week half-life. Is it worth maintaining, or should priorities stay in issue trackers?
3. **Code intelligence format?** We use a JSON dependency graph. Would a simpler format (markdown with links) be more portable?
4. **The flat-file argument**: agents.md is gaining adoption BECAUSE it's simple. Are we over-engineering this?

---

## Try It Yourself

Even without tooling, you can manually create this structure in 30 minutes:

1. Write AGENTS.md (≤150 lines): build commands, module map, critical rules
2. Write TECH.md: conventions (format: "ALWAYS do X" / "NEVER do Y"), architecture sketch
3. Write IMPROVEMENT.md: ask your team "what burned you?" — write 5 evidence-grounded gotchas
4. Write PROJECT.md: current quarter priorities + "don't touch" list

Score yourself against the 8 dimensions. If you jump from 2 to 6, you'll feel the difference in agent behavior immediately.

---

*We're building automated tooling around this (generation from code + git history, self-maintaining refresh, multi-package support), but the standard itself is open. What matters is that your codebase has this context — not how it got there.*

What's your experience? Does your agent struggle with judgment, or just navigation? What's worked for you?
