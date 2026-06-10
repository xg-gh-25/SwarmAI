---
title: "DDD Runtime Activation — Closing the Design-to-Production Gap"
created: 2026-06-10
updated: 2026-06-10
tags: [ddd, runtime, activation, knowledge-injection, decay, pipeline]
project: SwarmAI
status: draft
---

# DDD Runtime Activation — Closing the Design-to-Production Gap

## Problem Statement

The DDD Cultivation Engine has a **structural disconnect between its write side and read side.** The write side (cultivation, proposals, auto-approval, decay transitions) works in production. The read side (type-filtered injection, reference tracking, progressive loading, trust-aware behavior) has fully-tested code that is **never called at runtime.**

This means:
1. Knowledge entries accumulate `ref:0` forever → decay engine will archive ALL entries regardless of actual usage
2. Pipeline stages read entire DDD files → no type-level precision, token waste
3. Health scores are computed but never influence agent behavior → agent treats all knowledge equally
4. Maturity promotion criteria are never satisfied → all sections stuck at `[Sparse]` forever
5. Progressive loading is aspirational text in INSTRUCTIONS.md → no code enforces it

**Root cause:** The HLD designed a "backend programmatic injection" system, but the actual architecture is "Agent reads markdown instructions + uses Read tool." The smart code exists in Python but has no integration point with the Agent's runtime.

**This is not 5 separate bugs. It's ONE architectural gap:** the Read side was built but never wired into the session lifecycle.

---

## Current State vs Target State

| Dimension | Current (broken) | Target (activated) |
|-----------|-----------------|-------------------|
| **Reference tracking** | `ref:0` on all entries. `bump_references()` exists but no caller. | Refs bumped every session from DailyActivity + pipeline output text. Active entries have ref > 0. |
| **Stage injection** | Pipeline reads whole files. `get_stage_knowledge()` exists but no caller. | Pipeline stages get type-filtered, relevance-sorted knowledge via CLI command. |
| **Progressive loading** | Agent reads full DDD files via Read tool. | Session briefing surfaces low-trust sections. Agent knows which knowledge to verify before relying on. |
| **Trust signal** | Health scores in `section_health.json`, agent never sees them. | Maturity comments reflect actual health composite → agent sees `trust: low` inline when reading DDD. |
| **Maturity promotion** | `verified_by_production=True` never set → promotion impossible. | Successful pipeline delivery sets verification flag → sections can promote sparse→growing→mature. |

---

## Architecture Principle: Activate, Don't Rewrite

All 5 features have **fully tested code.** The fix is not implementation — it's wiring:

```
Existing tested code          Missing caller              Integration point
─────────────────────         ───────────────             ─────────────────
bump_references()         →   _ch_entry_lifecycle()   →   Orchestrator Channel 8
get_stage_knowledge()     →   artifact_cli.py cmd     →   Pipeline INSTRUCTIONS.md
compute_section_health()  →   build_session_briefing  →   Session start prompt
health → trust derivation →   _update_maturity()      →   context_health_hook
verified_by_production    →   _update_maturity()      →   context_health_hook (scan completed runs)
```

---

## F1: Activate `bump_references()` — The Foundation

### Why This Is First

Without reference tracking, the decay engine is **actively harmful** — it will archive ALL entries because ref=0 on everything. F1 is the foundation that makes the entire lifecycle system work correctly.

### Current Code Path (Channel 8)

```python
# ddd_orchestrator.py :: _ch_entry_lifecycle (line ~689)
def _ch_entry_lifecycle(self, root, ws_path):
    for project in projects:
        content = read(IMPROVEMENT.md)
        entries = parse_entries(content)        # ✅ works
        transitions = assess_decay(entries)     # ✅ works (but ref=0 → everything decays)
        # ❌ MISSING: bump_references(entries, text, today)
        new_content = inject_entry_metadata(content, entries)
        write(IMPROVEMENT.md, new_content)
```

### Fix: Add Reference Bumping from DailyActivity

```python
# ddd_orchestrator.py :: _ch_entry_lifecycle — INSERT BEFORE assess_decay()

# Read recent session activity for reference matching
daily_dir = root / "Knowledge" / "DailyActivity"
if daily_dir.is_dir():
    # Gather text from today's + yesterday's DailyActivity
    today_str = date.today().isoformat()
    yesterday_str = (date.today() - timedelta(days=1)).isoformat()
    signal_texts = []
    for da_file in daily_dir.iterdir():
        if da_file.stem.startswith(today_str) or da_file.stem.startswith(yesterday_str):
            signal_texts.append(da_file.read_text(errors="ignore")[:8000])
    
    if signal_texts:
        combined_text = "\n".join(signal_texts)
        bumped = bump_references(entries, combined_text, date.today())
        if bumped:
            findings.append(f"DDD-BUMP: {bumped} entries referenced in {project_dir.name}")
```

### Data Source: Why DailyActivity

DailyActivity JSONL captures per-session: topics discussed, files modified, decisions made, lessons learned, deliverables. If a session discussed "subprocess timeout handling", and an entry titled "Subprocess 在 async 上下文里必须 to_thread + timeout" exists, the title-matching logic (word-boundary for 8-20 chars, substring for 20+) will match.

### Verification

After implementation:
- Run one session that discusses any topic matching an IMPROVEMENT.md entry title
- Next `_ch_entry_lifecycle` run (session close) → entry ref count increments
- `grep "ref:" Projects/SwarmAI/IMPROVEMENT.md` shows non-zero values
- Entries that ARE being discussed stop decaying; entries that AREN'T decay as designed

### Effort: 30 minutes

---

## F2: Activate `get_stage_knowledge()` — Stage-Aware Injection via CLI

### Design Choice: CLI Command, Not Programmatic Injection

We cannot inject content into the Agent's context mid-conversation (SDK limitation). But the pipeline Agent already runs CLI commands at each stage (`artifact_cli.py`). Adding one more command is zero-friction.

### New CLI Command

```bash
# Usage (called by pipeline agent at each stage):
python backend/scripts/artifact_cli.py ddd-stage-inject \
    --project SwarmAI \
    --stage build \
    --context "session_unit.py,ddd_orchestrator.py"

# Output: formatted markdown of relevant entries
## DDD Knowledge for BUILD stage (SwarmAI)

### Guidelines (apply these)
- **Subprocess 在 async 上下文里必须 to_thread + timeout** [ref:7]
- **新增 pure function 是最安全的改动类型** [ref:12]

### Pitfalls (avoid these)
- **Mock 数据格式 ≠ 生产 DB 格式** [ref:5]
- **Silent fallback 是最危险的 bug 类型** [ref:4]

### Models (understand these)
- **Session 状态机：COLD → STREAMING → IDLE → DEAD** [ref:3]

(12 entries, sorted by relevance to context files)
```

### Implementation

```python
# artifact_cli.py — new subcommand
def cmd_ddd_stage_inject(args):
    """Output type-filtered DDD knowledge for a pipeline stage."""
    project_dir = find_project(args.project)
    improvement_path = project_dir / "IMPROVEMENT.md"
    tech_path = project_dir / "TECH.md"
    
    entries = []
    if improvement_path.exists():
        entries += parse_entries(improvement_path.read_text())
    if tech_path.exists():
        entries += parse_entries(tech_path.read_text())
    
    # Load knowledge graph for boost
    graph = load_graph(project_dir.parent.parent / ".context" / ".knowledge-graph.yaml")
    
    # Context entities from --context flag (files being worked on)
    context_entities = args.context.split(",") if args.context else []
    
    # Get stage-filtered, relevance-sorted entries
    stage_entries = get_stage_knowledge(entries, args.stage, context_entities, graph)
    
    # Format and print
    print(format_stage_knowledge(stage_entries, args.stage))
```

### Pipeline Integration

Update `s_autonomous-pipeline/INSTRUCTIONS.md` at each stage's "Load Context" step:

```markdown
**Before starting this stage, run:**
```bash
python backend/scripts/artifact_cli.py ddd-stage-inject --project <PROJECT> --stage <STAGE_NAME>
```
Use the output as additional context for this stage. It contains type-filtered
knowledge entries relevant to this specific stage (guidelines for BUILD, pitfalls
for REVIEW/TEST, decisions for EVALUATE, etc.)
```

### Why This Works

- Agent already calls `artifact_cli.py` for stage advancement, validation, etc.
- Adding one more call per stage is ~3 seconds overhead
- Output is informational (additive context), not blocking
- If command fails, agent still has full DDD docs (graceful degradation)
- `get_stage_knowledge()` already handles the type affinity mapping + graph boost

### Effort: 1 hour

---

## F3: Progressive Loading via Session Briefing

### Design Choice: Trust Summary in Briefing, Not File Truncation

We cannot programmatically truncate DDD files or load sections selectively (agent uses `Read` tool). But we CAN put "which sections to be careful about" in the session briefing — which the agent reads at session start.

### Implementation

```python
# proactive_intelligence.py :: build_session_briefing() — new section

def _get_ddd_trust_summary(workspace_dir: str) -> list[str]:
    """Surface DDD sections with low trust scores in session briefing."""
    lines = []
    projects_dir = Path(workspace_dir) / "Projects"
    if not projects_dir.is_dir():
        return lines
    
    for project_dir in sorted(projects_dir.iterdir()):
        if not project_dir.is_dir():
            continue
        health_file = project_dir / ".health" / "section_health.json"
        if not health_file.exists():
            continue
        
        import json
        health_data = json.loads(health_file.read_text())
        
        for section_name, scores in health_data.items():
            composite = scores.get("composite", 100)
            if composite < 40:
                trust = "low" if composite < 20 else "moderate"
                lines.append(
                    f"  - {project_dir.name}/{scores.get('doc', '?')} "
                    f"§{section_name} [trust:{trust}, score:{composite}]"
                )
    
    return lines[:5]  # Cap at 5 to avoid briefing bloat
```

Insert into `build_session_briefing()` after DDD escalations block:

```python
trust_lines = _get_ddd_trust_summary(str(workspace))
if trust_lines:
    sections.append(
        "**DDD low-trust sections** (verify before relying):\n"
        + "\n".join(trust_lines)
    )
```

### Effect on Agent Behavior

Agent reads at session start:
```
**DDD low-trust sections** (verify before relying):
  - SwarmAI/TECH.md §Voice Input [trust:low, score:28]
  - CMHK_SalesIntel/TECH.md §Entity Resolution [trust:moderate, score:35]
```

When working in these areas, the agent will naturally verify (pull the section, cross-check with code) instead of blindly trusting.

### Effort: 45 minutes

---

## F4: Health Scores Drive Trust Annotations

### Current Problem

Maturity comments in DDD files have a `trust:` field, but it's derived from a heuristic (`source_count` based) that doesn't reflect actual health. The agent sees `trust: high` on sections that might be stale.

### Fix: Cross-Reference Health Composite into Maturity Trust

```python
# context_health_hook.py :: _update_maturity() — AFTER update_evidence_from_changelog()

# Cross-reference health scores into maturity trust field
health_file = project_dir / ".health" / "section_health.json"
if health_file.exists():
    health_data = json.loads(health_file.read_text())
    for state in maturity_states:
        section_health = health_data.get(state.section_name, {})
        composite = section_health.get("composite")
        if composite is not None:
            state.health_composite = composite
            # Derive trust from composite (overrides heuristic)
            # 80-100: high, 60-79: moderate, 40-59: low, <40: very_low
```

```python
# ddd_maturity.py :: MaturityState.to_comment() — use health_composite if available

def to_comment(self) -> str:
    trust = self._derive_trust()
    if self.health_composite is not None:
        if self.health_composite >= 80:
            trust = "high"
        elif self.health_composite >= 60:
            trust = "moderate"
        elif self.health_composite >= 40:
            trust = "low"
        else:
            trust = "very_low"
    return f"<!-- maturity: {self.level} | ... | trust: {trust} | ... -->"
```

### Guard Against Churn

Only write if trust level actually CHANGED (not just the composite number). This prevents git diffs every session:

```python
old_trust = state._derive_trust()  # heuristic
new_trust = derive_from_composite(state.health_composite)
if old_trust == new_trust:
    state.health_composite = None  # suppress write, no change
```

### Effort: 1 hour

---

## F5: Maturity Promotion via Pipeline Delivery Verification

### Current Problem

Promotion from `[Sparse]` to `[Growing]` requires `verified_by_production=True`, but nothing sets it. Result: ALL sections permanently stuck at `[Sparse]`.

### Fix: Scan Completed Pipeline Runs

```python
# context_health_hook.py :: _update_maturity() — AFTER health cross-reference (F4)

# Scan recent completed pipeline runs → set verified_by_production
runs_dir = project_dir / ".artifacts" / "runs"
if runs_dir.is_dir():
    for run_dir in sorted(runs_dir.iterdir(), reverse=True)[:10]:  # Last 10 runs
        run_json = run_dir / "run.json"
        if not run_json.exists():
            continue
        
        run_data = json.loads(run_json.read_text())
        
        # Skip if already processed
        if run_data.get("maturity_updated"):
            continue
        
        # Only count successful deliveries
        if run_data.get("status") != "completed":
            continue
        stages = run_data.get("stages", [])
        if not any(s.get("name") == "deliver" and s.get("status") == "completed" for s in stages):
            continue
        
        # This run delivered successfully using this project's DDD
        # → All growing+ sections are verified
        for state in maturity_states:
            if state.level in ("growing", "mature", "evergreen"):
                state.verified_by_production = True
            # All sections that exist contributed to the decision
            state.used_in_decision = True
        
        # Mark run as processed (atomic write back)
        run_data["maturity_updated"] = True
        run_json.write_text(json.dumps(run_data, indent=2))
        
        findings.append(
            f"MATURITY-VERIFY: {project_dir.name} verified from run {run_dir.name}"
        )
        break  # One run is enough per session
```

### Promotion Path Unlocked

With `verified_by_production=True` set:
- `evaluate_promotion()` in `ddd_maturity.py` can now evaluate: days_at_level > 30 + source_count >= 2 + verified = True → PROMOTE sparse→growing
- Over time: growing→mature requires stability (no contradictions for 5+ sessions)
- Eventually: mature→evergreen requires self-maintenance (auto-approved updates succeeding)

### Effort: 1.5 hours

---

## Build Order & Dependencies

```
F1 (bump_references)    ← No deps. MUST go first.
   │                       Foundation: makes ref counts real.
   │                       Without this, F2's sort-by-ref is meaningless
   │                       and decay engine slowly kills all knowledge.
   │
   ├── F4 (health→trust) ← No deps on F1, but benefits from it.
   │      │                  Fresh health scores need usage data (from F1).
   │      │
   │      └── F3 (trust in briefing) ← Depends on F4.
   │                                    Needs health scores to be meaningful.
   │
   ├── F5 (delivery→verified) ← No hard deps.
   │                              But works best after F1 (ref counts inform decay immunity)
   │                              and F4 (health composite enriches maturity).
   │
   └── F2 (stage injection) ← Benefits from F1 (entries sorted by ref_count).
                               Can be built any time after F1.
```

### Recommended Sequence

| Step | Fix | Why this order | Cumulative time |
|------|-----|---------------|----------------|
| 1 | **F1** | Foundation — everything depends on ref counts being real | 30 min |
| 2 | **F4** | Makes trust annotations honest. Quick win, low risk. | 1h 30m |
| 3 | **F5** | Unlocks promotion path. Requires F4 for full effect. | 3h |
| 4 | **F3** | Surfaces trust in briefing. Needs F4's scores to be meaningful. | 3h 45m |
| 5 | **F2** | Highest polish. Entries now have real ref counts (F1) for sorting. | 4h 45m |

**Total: ~5 hours in one pipeline run.**

---

## Verification Plan

| Fix | How to verify | Expected signal |
|-----|--------------|-----------------|
| F1 | After session close: `grep "ref:" Projects/SwarmAI/IMPROVEMENT.md` | At least 3-5 entries with ref > 0 |
| F2 | `python artifact_cli.py ddd-stage-inject --project SwarmAI --stage build` | Outputs 5-15 filtered entries grouped by type |
| F3 | Start new session → check session briefing for "DDD low-trust sections" | Shows sections with composite < 40 (or empty if all healthy) |
| F4 | Read any DDD file → maturity comment `trust:` matches health composite | `trust: low` on sections with composite < 40 |
| F5 | After pipeline run completes → check maturity comments | `verified: true` on sections in that project |

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| F1 false-positive bumps (short title matches wrong text) | Low | Low (ref inflated but no harm) | Existing < 8 char skip + word-boundary matching |
| F2 CLI command fails | Low | Zero (agent still reads full DDD) | Graceful: pipeline instructions say "use output as additional context" |
| F3 briefing bloat | Low | Low (capped at 5 lines, ~50 tokens) | Hard cap + only shows < 40 composite |
| F4 trust churn (git diffs every session) | Medium | Low (noise) | Only write if trust LEVEL changes, not score |
| F5 over-verification (mark all sections verified from one run) | Medium | Low (promotion still requires days+sources) | Acceptable: promotion has 3 criteria, verified is just one |
| All: DDD docs empty (new project) | N/A | Zero (all guards: `if not entries: return`) | All functions gracefully handle empty state |

---

## What This Enables (Compound Effects)

After all 5 fixes are active:

```
Session N:
  Agent discusses "async timeout handling"
    → DailyActivity captures it
    → Session close: bump_references() matches entry title
    → Entry ref:0 → ref:1 → NOT decaying anymore
    → Health score improves (usage dimension +)
    → Trust annotation updates: trust:moderate → trust:high
    
Session N+1:
  Pipeline BUILD stage runs:
    → ddd-stage-inject outputs this entry (type=guideline, stage=build, high ref)
    → Agent sees it ranked #1 in relevant knowledge
    → Uses it correctly in implementation
    → Pipeline DELIVERS successfully
    → _update_maturity: verified_by_production=True
    → Section promotes: sparse → growing
    
Session N+2:
  New session starts:
    → Briefing: no low-trust warnings for this section (it's growing+healthy)
    → Agent trusts this knowledge fully
    → Knowledge compounds: used → ref++ → healthier → more trusted → used more
```

**This is the flywheel the HLD promised.** It was never broken — just disconnected.

---

## Relationship to Other Work

| Related | Relationship |
|---------|-------------|
| **U2 (Sub-agent progress)** | Independent. Can be built in parallel. |
| **Discussion #59 (Knowledge Governance)** | This makes the claims in #59 TRUE in production (type-filtered injection, ref-based decay, progressive loading) |
| **DDD HLD** | This implements what the HLD designed. After F1-F5, code matches design. |
| **Pipeline v4/v5** | F2 enriches pipeline stages. Compatible, not conflicting. |

---

*Author: XG + Swarm | SwarmAI*
*Triggered by: Implementation audit 2026-06-10 — "code exists, no callers"*
