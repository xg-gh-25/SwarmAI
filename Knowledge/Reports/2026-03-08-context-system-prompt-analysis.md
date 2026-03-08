---
title: "SwarmAI Context & System Prompt Management — Comprehensive Analysis"
date: 2026-03-08
tags: [architecture, context-management, system-prompts, competitive-analysis, launch-readiness]
author: SwarmAI
---

# SwarmAI Context & System Prompt Management — Comprehensive Analysis

## Executive Summary

SwarmAI's context management and system prompt pipeline is **architecturally mature and launch-ready for an initial product release**, with meaningful differentiation from frontier competitors. The 12-file priority-based context assembly, two-tier memory model, automatic context monitoring, and multi-model token budget scaling represent a well-engineered system that compares favorably to Claude Code, Cursor, and OpenAI Codex in several dimensions while having clear gaps in others.

**Verdict: GO for initial launch**, with 3 high-priority items to address in the first 2 post-launch sprints.

---

## 1. Architecture Overview — What SwarmAI Has

### 1.1 System Prompt Assembly Pipeline

```
Templates (backend/context/*.md)
    ↓ ensure_directory() — two-mode copy (system=always-overwrite, user=copy-if-missing)
~/.swarm-ai/.context/ (12 files, P0–P11)
    ↓ ContextDirectoryLoader.load_all()
    │   ├─ L1 cache check (git-first freshness, budget-tier validation)
    │   ├─ L0 compact path for <64K models
    │   ├─ _assemble_from_sources() — priority-ordered, cleaned, budget-enforced
    │   └─ _enforce_token_budget() — lowest-priority-first truncation
    ↓
_build_system_prompt() in AgentManager
    │   ├─ Injects BOOTSTRAP.md (ephemeral, first-run onboarding)
    │   ├─ Injects DailyActivity (last 2 files, 2K token cap each)
    │   ├─ Injects distillation flag if pending
    │   └─ Appends SystemPromptBuilder (safety, datetime, runtime metadata)
    ↓
Final system prompt → Claude Agent SDK
```

**12 Context Files (Priority Order):**

| Priority | File | Owner | Truncatable | Direction |
|----------|------|-------|-------------|-----------|
| P0 | SWARMAI.md | system | No | — |
| P1 | IDENTITY.md | system | No | — |
| P2 | SOUL.md | system | No | — |
| P3 | GROWTH_PRINCIPLES.md | system | Yes | tail |
| P4 | AGENT.md | system | Yes | tail |
| P5 | USER.md | user | Yes | tail |
| P6 | STEERING.md | user | Yes | tail |
| P7 | TOOLS.md | user | Yes | tail |
| P8 | MEMORY.md | agent | Yes | head |
| P9 | EVOLUTION.md | agent | Yes | head |
| P10 | KNOWLEDGE.md | user | Yes | tail |
| P11 | PROJECTS.md | user | Yes | tail |

**Key Design Properties:**
- P0–P2 are non-truncatable (identity/personality always survives budget pressure)
- MEMORY.md and EVOLUTION.md truncate from **head** (keep newest entries)
- HTML comments stripped to save ~200 tokens per file
- Redundant H1 headers dedup'd against section names
- CJK-aware token estimation (1.5 chars/token for CJK, 4/3 words/token for Latin)
- Group channel safety: MEMORY.md + USER.md excluded to prevent personal data leakage

### 1.2 Token Budget System

| Model Context Window | Token Budget | Strategy |
|---------------------|-------------|----------|
| >= 200K | 40,000 | Full L1 assembly |
| >= 64K, < 200K | 25,000 | Full L1 assembly |
| < 64K | 25,000 (L0 path) | Compact cache or aggressive truncation |
| < 32K | 25,000 | Excludes KNOWLEDGE.md + PROJECTS.md |

- Ephemeral headroom: 4,000 tokens reserved for DailyActivity injection
- L1 cache: git-first freshness check + budget-tier header validation
- L0 cache: manually-authored compact version for small models

### 1.3 Context Monitor (Runtime)

```
User turn → AgentManager.run_conversation()
    ↓ response stream completes
    ↓ effective_sid captured from result event
    ↓ _user_turn_counts[sid] incremented
    ↓ if turns % 15 == 0:
        ↓ check_context_usage()
            ├─ Find latest .jsonl transcript in ~/.claude/projects/
            ├─ Detect compaction boundary (only count post-compaction)
            ├─ Estimate tokens: content_chars / 3 + 40K baseline
            └─ Return level: ok (<70%) | warn (70-84%) | critical (>=85%)
        ↓ if warn or critical:
            yield context_warning SSE event
    ↓ Frontend: Toast (yellow 15s / red sticky)
```

### 1.4 Memory System

**Two-tier model:**
- **DailyActivity** (`Knowledge/DailyActivity/YYYY-MM-DD.md`) — raw session logs, written during every session, auto-pruned after 90 days
- **MEMORY.md** (`.context/MEMORY.md`) — curated long-term memory, auto-distilled when >7 unprocessed DailyActivity files detected

### 1.5 Test Coverage

| Test Suite | Tests | Lines |
|-----------|-------|-------|
| test_context_directory_loader.py | 65 | 769 |
| test_system_prompt_e2e.py | 47 | 823 |
| test_context_monitor.py | 26 | 346 |
| **Total context-related** | **138** | **1,938** |

---

## 2. Competitive Landscape Comparison

### 2.1 Claude Code (Anthropic — the closest competitor)

Claude Code is the most direct comparison since SwarmAI wraps the Claude Agent SDK.

| Dimension | Claude Code | SwarmAI | Verdict |
|-----------|------------|---------|---------|
| **Instruction files** | CLAUDE.md (project/user/local/managed policy) | 12 priority-ranked files with role separation | SwarmAI wins on structure; Claude Code wins on simplicity |
| **File hierarchy** | Walk-up directory tree + on-demand subdirs | Flat .context/ directory, single scope | Claude Code more flexible for monorepos |
| **Path-scoped rules** | `.claude/rules/*.md` with glob frontmatter | Not supported | Claude Code ahead |
| **Auto memory** | Per-project `~/.claude/projects/<project>/memory/`, first 200 lines loaded, topic files on demand | Two-tier DailyActivity + MEMORY.md with auto-distillation | Both capable; SwarmAI's distillation is more structured |
| **Context compaction** | Automatic when approaching limits + manual `/compact` with instructions + `/rewind` | No compaction — relies on Claude SDK's built-in compaction | Claude Code ahead (user has control) |
| **Context monitoring** | Manual tracking only; custom status line possible | Automatic post-response hook every 15 turns with SSE Toast | SwarmAI ahead — proactive, zero user effort |
| **Token budget** | Not exposed to user; managed internally | Dynamic 25K–40K with L0/L1 caching | SwarmAI ahead on transparency |
| **Multi-model support** | Single model per session | Dynamic budget scaling per model context window | SwarmAI ahead |
| **Prompt caching** | Automatic (SDK handles cache_control) | Not explicitly implemented | Gap — significant cost/latency win left on table |
| **Session management** | --continue, --resume, /clear, /rewind checkpoints | Session persistence in DB, but no compaction control | Claude Code ahead |
| **Subagents** | First-class support, separate context windows | Not implemented | Claude Code significantly ahead |
| **Skills/extensibility** | Skills + hooks + MCP + plugins ecosystem | Skills directory with SKILL.md + MCP support | Comparable core, Claude Code has richer ecosystem |
| **File imports** | `@path/to/import` in CLAUDE.md | Not supported | Claude Code ahead |
| **Team/org support** | Managed policy + monorepo exclusions | Single-user focused | Claude Code ahead for enterprise |

### 2.2 Cursor

| Dimension | Cursor | SwarmAI |
|-----------|--------|---------|
| **Rules** | `.cursorrules` / `.cursor/rules/` with path-scoped frontmatter | 12-file priority system |
| **Context indexing** | Semantic codebase indexing, @-mentions for files/symbols | File-based context loading, no codebase indexing |
| **Memory** | No persistent memory across sessions | DailyActivity + MEMORY.md |
| **Context window** | Manages internally; user has no visibility | Automatic monitoring with user-facing warnings |

**Assessment:** Cursor excels at code-specific context (codebase indexing, symbol navigation) but has no persistent memory model. SwarmAI's strength is cross-session continuity and the personalization layer (USER.md, SOUL.md).

### 2.3 OpenAI Codex CLI

| Dimension | Codex CLI | SwarmAI |
|-----------|-----------|---------|
| **Instructions** | AGENTS.md + `.codex/` directory | 12-file .context/ system |
| **Memory** | `.codex/skills/` for persistence | Two-tier memory + evolution registry |
| **Sandbox** | Rust-based OS-level sandboxing | Security hooks + permission manager |
| **Architecture** | 96% Rust core, minimal overhead | Python backend, Claude Agent SDK |

**Assessment:** Codex CLI is architecturally simpler with stronger sandboxing. SwarmAI has richer personalization and memory.

### 2.4 Competitive Matrix Summary

```
                    Context    Memory     Monitor    Budget    Multi-    Prompt    Sub-
                    Structure  Persist.   Proactive  Scaling   Model     Caching   Agents
                    ─────────  ─────────  ─────────  ────────  ────────  ────────  ──────
Claude Code         ████░      ████░      ██░░░      ██░░░     ██░░░     █████     █████
SwarmAI             █████      █████      █████      █████     ████░     ░░░░░     ░░░░░
Cursor              ███░░      █░░░░      ██░░░      ██░░░     █████     ███░░     ░░░░░
Codex CLI           ██░░░      ██░░░      ░░░░░      █░░░░     ██░░░     ░░░░░     ░░░░░
```

---

## 3. Strengths (What SwarmAI Does Well)

### 3.1 Structured Identity Architecture
The 12-file system with explicit priority ordering, owner classification (system/user/agent), and truncation direction is **more structured than any competitor**. The separation of concerns (SWARMAI.md for core principles, SOUL.md for personality, USER.md for personalization, STEERING.md for overrides) creates a clear mental model that no other product matches.

Claude Code's CLAUDE.md is powerful but flat — a single file mixes project conventions, coding standards, and behavioral rules. SwarmAI's layered approach makes it clear what's immutable (P0-P2), what the user controls (P5-P7), and what the agent manages (P8-P9).

### 3.2 Proactive Context Monitoring
**No frontier competitor offers automatic context window monitoring with user-facing warnings.** Claude Code explicitly acknowledges this is a problem ("LLM performance degrades as context fills") but only offers manual `/clear` and a custom status line. SwarmAI's automatic post-response hook with SSE Toast notifications is a genuine differentiator for a product aimed at non-technical users.

### 3.3 Dynamic Token Budget Scaling
The budget tier system (25K default, 40K for large models, L0 path for small models, low-priority exclusion under 32K) is more sophisticated than what competitors expose. Combined with the ephemeral headroom reservation for DailyActivity, this demonstrates production-grade resource management.

### 3.4 Two-Tier Memory with Auto-Distillation
The DailyActivity → MEMORY.md distillation pipeline is architecturally sound. Claude Code's auto memory is comparable in intent but uses a different model (first 200 lines of MEMORY.md + on-demand topic files). SwarmAI's approach with a distillation trigger (>7 unprocessed files) and explicit promotion criteria is more principled.

### 3.5 Safety Separation of Concerns
The group channel exclusion (MEMORY.md + USER.md filtered out) prevents personal data leakage in multi-participant contexts. This is a thoughtful production consideration that competitors don't address because they're primarily single-user tools.

### 3.6 Comprehensive Test Coverage
138 tests covering the context assembly pipeline, with E2E tests that simulate full prompt builds, is solid engineering. The test suite covers CJK estimation, truncation edge cases, compaction detection, and level thresholds.

---

## 4. Gaps & Risks

### 4.1 CRITICAL: No Prompt Caching Strategy

**Impact: 90% cost savings left on table.**

Anthropic's prompt caching allows the system prompt to be cached for 5 minutes (or 1 hour with extended TTL), reducing input costs from $5/MTok to $0.50/MTok for cache hits. SwarmAI's system prompt is ~25K–40K tokens — caching this would save $112–$180 per 1,000 requests on Opus.

Claude Code gets this for free because the SDK handles it internally. SwarmAI, by injecting context into the system prompt before passing to the SDK, may be inadvertently invalidating the cache on every turn (if DailyActivity changes, or if the distillation flag toggles).

**Recommendation:** Separate the system prompt into stable segments (P0–P7, rarely change) and volatile segments (DailyActivity, runtime metadata). Apply `cache_control` breakpoints between them. Verify the SDK's cache behavior with the current injection pattern.

**Priority: HIGH — impacts unit economics at scale.**

### 4.2 HIGH: No Compaction Control

SwarmAI relies entirely on the Claude SDK's built-in compaction when context fills up. Claude Code offers:
- Manual `/compact` with custom instructions ("Focus on the API changes")
- `/rewind` with summarize-from-here
- CLAUDE.md instructions for compaction behavior
- Automatic compaction with smart preservation

SwarmAI has **none of these**. When context runs out, the SDK compacts silently, and the user has no control over what survives.

**Recommendation:** Implement a `/compact` equivalent in the web UI — either a button or automatic with user-configurable preservation rules in STEERING.md.

**Priority: HIGH — directly affects long session quality.**

### 4.3 MEDIUM: No Path-Scoped or On-Demand Context Loading

Claude Code loads CLAUDE.md files in subdirectories on-demand when working with files in those directories. It also supports path-scoped rules (only loaded when working with matching file patterns). This means context budget is spent only on relevant instructions.

SwarmAI loads all 12 files at session start, unconditionally. For a coding assistant this is fine since the files are small (~27KB total). But as user content grows (MEMORY.md, PROJECTS.md, KNOWLEDGE.md can grow significantly), this becomes wasteful.

**Recommendation:** Not urgent for launch. Monitor MEMORY.md growth and consider on-demand loading for P8+ files when they exceed a threshold.

**Priority: MEDIUM — not a launch blocker but affects long-term scalability.**

### 4.4 MEDIUM: No `@import` Support in Context Files

Claude Code's `@path/to/import` in CLAUDE.md is powerful for modular organization. SwarmAI's fixed 12-file structure doesn't support imports. If a user wants to reference a large API specification or a team wiki page, they can't include it in their system prompt without editing one of the 12 files directly.

**Recommendation:** Consider supporting `@path` syntax in STEERING.md and TOOLS.md (the user-owned files) as a post-launch enhancement.

**Priority: LOW — power-user feature, not needed for initial launch.**

### 4.5 LOW: No Subagent / Parallel Session Support

Claude Code's subagent model (separate context windows, delegated tasks, focused exploration) is a significant architectural advantage for complex tasks. SwarmAI doesn't have this.

**Recommendation:** This is a fundamental architecture decision, not a quick fix. Not a launch blocker — most users work in single-session patterns. Plan for post-launch.

**Priority: LOW for launch, HIGH for product roadmap.**

### 4.6 LOW: Context Monitor Reads Claude's Internal Transcripts

The context monitor reads `.jsonl` files from `~/.claude/projects/`, which is Claude Code's internal transcript format. This is:
- **Fragile**: Any change to Claude Code's transcript format breaks the monitor
- **Coupling**: Ties SwarmAI to Claude Code's internal implementation details
- **Not portable**: Won't work if the underlying SDK changes

**Recommendation:** Acceptable for initial launch since SwarmAI is built on the Claude SDK. Add a version check or graceful fallback. Long-term, consider tracking token usage from the SDK's response metadata directly.

**Priority: LOW — works now, technical debt to address later.**

---

## 5. Launch Readiness Assessment

### 5.1 Scoring Matrix

| Criterion | Score (1-5) | Notes |
|-----------|:-----------:|-------|
| **Functionality completeness** | 4 | All core features working end-to-end |
| **Architectural soundness** | 5 | Clean separation of concerns, priority system, budget enforcement |
| **Test coverage** | 4 | 138 tests, E2E coverage, but no integration tests with real SDK |
| **Competitive parity** | 3 | Ahead on monitoring/memory, behind on caching/compaction/subagents |
| **Production resilience** | 4 | All IO wrapped in try/except, graceful degradation throughout |
| **User experience** | 4 | Context warnings are proactive, Toast UI is clean |
| **Cost efficiency** | 2 | Missing prompt caching is a significant cost concern at scale |
| **Scalability** | 3 | Single-session model, no parallel execution, all-at-once loading |
| **Overall** | **3.6** | **Ready for initial launch with known gaps** |

### 5.2 Launch Decision

**GO** — with the following conditions:

#### Must-Have Before Launch (Sprint 1)
1. **Verify prompt caching behavior** — Confirm whether the Claude SDK applies prompt caching to the injected system prompt automatically, or if explicit `cache_control` is needed. If caching isn't happening, this must be fixed before launch to avoid unsustainable API costs.

#### Should-Have Within 2 Weeks Post-Launch (Sprint 2)
2. **Add basic compaction control** — At minimum, a "Clear context" button in the UI that maps to `/clear`. Ideally, expose `/compact` with a user-facing option to specify what to preserve.
3. **Context usage indicator** — Add a persistent (non-toast) context usage indicator to the chat header, so users can see % usage at any time rather than only at 15-turn intervals.

#### Nice-to-Have (Roadmap)
4. Path-scoped context loading for large MEMORY.md files
5. `@import` support in user-owned context files
6. Subagent architecture for parallel execution
7. Prompt caching optimization with stable/volatile segment separation

### 5.3 Competitive Positioning

For initial launch, SwarmAI's context/prompt system should be positioned as:

> **"The AI assistant that remembers you."**

The 12-file identity system, two-tier memory, and proactive context monitoring are genuine differentiators against Claude Code (developer-focused, no personalization layer), Cursor (code-only, no memory), and Codex (minimal persistence). SwarmAI's strength is the **relationship layer** — the idea that the AI develops a working relationship with the user over time through USER.md, MEMORY.md, SOUL.md, and DailyActivity.

The gaps (caching, compaction, subagents) are less visible to end users in early usage and can be addressed iteratively.

---

## 6. Technical Recommendations

### 6.1 Immediate (Pre-Launch)

```python
# In _build_system_prompt or chat service layer:
# Verify whether Claude SDK auto-applies prompt caching to system messages.
# If not, structure the system prompt as an array of content blocks
# with cache_control on the stable prefix:
system=[
    {
        "type": "text",
        "text": context_text,  # P0-P11 assembled (stable between turns)
        "cache_control": {"type": "ephemeral", "ttl": "1h"},
    },
    {
        "type": "text",
        "text": runtime_sections,  # datetime, DailyActivity (volatile)
    },
]
```

### 6.2 Short-Term (Post-Launch Sprint)

1. **Persistent context gauge** in chat header showing `{pct}% context used`
2. **"New session" button** that saves context and starts fresh (maps to save-context skill + new session)
3. **Compaction hook** — when SDK auto-compacts, emit an SSE event so the frontend can notify the user

### 6.3 Medium-Term (Roadmap)

1. **Prompt caching optimization** — separate stable vs volatile segments, apply cache breakpoints
2. **On-demand context loading** — lazy-load P8+ files only when relevant to the current task
3. **Session branching** — fork a session to explore alternatives without polluting main context
4. **Token usage tracking** — capture `cache_read_input_tokens` from SDK responses for cost analytics

---

## 7. Conclusion

SwarmAI's context and system prompt management system is a **well-architected, production-grade pipeline** that stands up against frontier competitors. Its structured 12-file identity model, proactive context monitoring, and two-tier memory with distillation represent genuine product differentiation.

The primary gap is **cost efficiency** (prompt caching) — a backend optimization that's invisible to users but critical to unit economics. The secondary gap is **compaction control** — something power users will want within the first few weeks.

For an initial product launch targeting personal productivity users (not enterprise dev teams), the current system is ready to ship. The competitive moat isn't in any single feature but in the **holistic relationship layer** that no competitor has assembled in the same way.

**Launch confidence: 8/10.**
