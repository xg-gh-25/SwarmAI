---
title: "DDD Knowledge Governance — Practical Ontology for AI Agents"
created: 2026-06-05
updated: 2026-06-17
discussion: https://github.com/xg-gh-25/SwarmAI/discussions/59
---

# DDD Knowledge Governance — Practical Ontology for AI Agents

> 引用 = 自然选择。被用的留存，没用的衰减。~100 行 Python 就够。

## Core Claim

Manage AI Agent domain knowledge using Darwinian principles. No Neo4j, no manual cleanup, no quarterly reviews needed.

## 7-Type MECE Schema (Three Layers)

**Updated 2026-06-17:** Extended from 5 to 7 types to cover meta-cognitive knowledge (design philosophy, behavioral corrections).

| Layer | Type | Description | inject_when | Example |
|-------|------|-------------|-------------|---------|
| **Operational** | `guideline` | "这样做是对的" | BUILD, REVIEW | "All DB access must go through repository layer" |
| **Operational** | `pitfall` | "这样做会出问题" | BUILD, REVIEW, TEST | "fcntl.flock on symlink path locks wrong file" |
| **Operational** | `process` | "步骤是这些" | BUILD, DELIVER | "Release: build→verify→tag→publish→smoke" |
| **Cognitive** | `decision` | "选了 A 因为 B" | EVALUATE, PLAN | "Chose Amazon Transcribe over Whisper — reuses AWS SSO" |
| **Cognitive** | `model` | "结构/状态是这样" | BUILD, DEBUG | "Session state machine: COLD → STREAMING → IDLE → DEAD" |
| **Meta-cognitive** | `principle` | "为什么/怎么想" | ALL stages | "引用=自然选择。被用的留存，没用的衰减" |
| **Meta-cognitive** | `correction` | "我有这个偏差" | ALL stages | "CLASS A: confidence → skip process (11 occurrences)" |

**Why 7 not 5?** Original 5 types covered "knowledge about code/systems." But an evolving agent also needs "knowledge about how to think" (principle) and "knowledge about its own failure patterns" (correction). These are the meta-cognitive layer — they govern decision-making quality, not task execution.

**Priority chain:** pitfall → decision → correction → principle → guideline → process → model

**Why fixed types > free tags?** Because you can write code saying: "BUILD stage injects all guideline + pitfall + correction." Free tags can't provide this — no query contract exists.

## Lifecycle (Darwinian Decay)

```yaml
lifecycle:
  ref_count: "被引用次数（系统自动 +1）"
  last_referenced: "最后被引用日期"
  decay_state: "active | dormant | archived"
  created_at: "创建日期"

decay_rules:
  active_to_dormant: "ref=0 + 90天无引用"
  dormant_to_archived: "ref=0 + 180天无引用"
  immunity: "创建 < 30 天 → 免疫"
  veteran_bonus: "ref ≥ 10 → 双倍宽限（180天才 dormant）"
```

Inline metadata format:
```markdown
- [guideline] **Subprocess must use to_thread + timeout in async** — blocks event loop otherwise (2026-05-02)
  <!-- ref:7 | last:2026-06-01 | decay:active -->
```

## Where Knowledge Lives (4 DDD Files + MEMORY + KNOWLEDGE)

| File | Contents | Scope |
|------|----------|-------|
| PRODUCT.md | Why, for whom, what NOT to do | Per-project |
| TECH.md | Architecture, state machines, conventions | Per-project |
| IMPROVEMENT.md | Lessons, experiences, failures | Per-project |
| PROJECT.md | Current state, next steps, blockers | Per-project |
| MEMORY.md | Decisions, lessons, corrections | Cross-project |
| KNOWLEDGE.md | Facts, constraints, reference | Cross-project |

**Same engine, multiple targets.** `ddd_entry_lifecycle.py` runs on ALL of these with the same parse/bump/decay mechanics.

## Injection Strategy (Stage-Driven)

```python
STAGE_INJECTION = {
    "EVALUATE": {"types": ["decision", "principle"]},
    "PLAN":     {"types": ["decision", "model", "principle"]},
    "BUILD":    {"types": ["guideline", "pitfall", "model", "process", "correction"]},
    "REVIEW":   {"types": ["guideline", "pitfall", "correction"]},
    "TEST":     {"types": ["pitfall"]},
    "DELIVER":  {"types": ["process", "principle", "correction"]},
}
```

Each stage only sees what it needs. No wasted tokens, no noise.

## Decay Engine (~100 lines)

```python
def run_daily_decay(entries):
    today = date.today()
    for entry in entries:
        days_idle = (today - entry.last_referenced).days
        age = (today - entry.created_at).days
        if age < 30: continue  # 免疫期
        threshold = 180 if entry.ref_count >= 10 else 90
        if entry.state == "active" and days_idle > threshold:
            entry.state = "dormant"
        elif entry.state == "dormant" and days_idle > threshold + 90:
            entry.state = "archived"
```

Zero human maintenance. Mechanical. Daily.

## Confident-Only Extraction

Only extract what the system is confident about:

| Confidence | Source | Action |
|-----------|--------|--------|
| HIGH | Pipeline REFLECT (structured) | Auto-extract immediately |
| HIGH | User correction (ground truth) | Auto-extract immediately |
| HIGH | Adversarial finding (confirmed CRITICAL/HIGH) | Auto-extract |
| MEDIUM | Pattern observed 2-3x | Extract but flag |
| LOW | First-time observation | Don't extract (stays in DailyActivity) |
| ZERO | Speculation | Never extract |

**Principle:** Better to have gaps humans fill naturally than noise that drowns signal. Humans are the "last gate" — they supplement what the system missed, not filter what the system over-produced.

## Real Data (3+ months production)

| Metric | Value |
|--------|-------|
| Active knowledge entries | ~80-115 (steady state after decay) |
| Token cost | ~12K / 1M context (1.2%) |
| Human maintenance | 0 times/month (decay auto-manages) |
| Decay transitions | ~3/week (active → dormant) |
| Resurrections | ~0.5/week (dormant → active on re-reference) |

## Design Principles (Takeaways)

1. **Think "how to delete" before "how to store"** — forgetting is a feature
2. **Reference = natural selection** — used survives, unused fades
3. **Fixed types > free tags** — 7 MECE types = programmable query contract
4. **File = coarsest classification** — where knowledge lives = its nature
5. **Context window IS your database** — below 100K, full injection > RAG
6. **Mechanical > Aspirational** — daily auto-decay, not quarterly review
7. **Three layers cover all knowledge** — Operational (DO) + Cognitive (UNDERSTAND) + Meta-cognitive (EVOLVE)
8. **Confident-only extraction** — don't produce noise, humans fill gaps naturally

---

**Author:** XG | SwarmAI — Human directs, AI delivers
**Implementation:** `backend/core/ddd_entry_lifecycle.py` (665+ lines)
**Design:** `Knowledge/Designs/2026-06-17-living-knowledge-os-design.md`
