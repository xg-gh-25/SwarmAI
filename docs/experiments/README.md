# Experiment Log

Each SwarmAI release tests a hypothesis. This log captures what we learned — not just what we shipped.

## Format

Each entry: hypothesis → method → result → negative findings → implication.

---

### E01: 4-Platform Isolation (v1.11.0, 2026-05-08)

**Hypothesis:** Platform-specific lifecycle (compile-time + runtime isolation) > one-size-fits-all process management.
**Method:** 4 mutually exclusive modes — macOS daemon (launchd), Windows/Linux subprocess (Tauri child), Hive (systemd), Dev (manual). `#[cfg]` compile-time + `SWARMAI_MODE` runtime. No fallback between modes.
**Result:** Zero cross-platform P0s since. Previous release (v1.9.0, 60 commits) had 3 independent P0 regressions all from platform assumptions.
**Negative finding:** `lsof` hangs indefinitely under macOS sandbox — replaced with `nc -z` universally. `os.path.expandvars("${HOME}")` returns literal string in daemon env (no shell profile) — replaced with `Path.home()`.
**Implication:** Platform isolation must be both compile-time AND runtime. Either alone leaks. Daemon code must never assume shell environment variables exist.
**Ref:** `backend/core/main.py` (`_detect_run_mode`), `desktop/src-tauri/src/lib.rs`

---

### E02: Mechanical Pipeline Gates (v1.12.0, 2026-05-09)

**Hypothesis:** Mandatory stage gates > advisory "please don't skip" instructions. Silent stage skips mask quality gaps.
**Method:** Validator enforces stage completion before advancing. Pipeline without adversarial review = confidence score 0 (cannot complete). Schema strictness is a feature, not a bug to work around.
**Result:** P0 rate dropped from ~0.5/release to ~0.3/release. Adversarial sub-agent caught 4 happy-path assumptions in first week that 16 mechanical checks missed.
**Negative finding:** Agent attempted to bypass validator when schema was strict (C021). Rule added: bypassing validator ≠ bypassing the requirement. Also: switched LLM judge from Opus to Haiku for cost → wasted 5 min debugging model ID. Reverted: one model everywhere.
**Implication:** Quality gates must be mandatory, not advisory. Cost optimization on critical paths is always wrong.
**Ref:** `backend/skills/s_autonomous-pipeline/INSTRUCTIONS.md` (DELIVER stage)

---

### E03: Session Resume Enrichment (v1.10.0, 2026-05-02)

**Hypothesis:** Structured extraction of prior session context > brute-force JSONL replay for resume quality.
**Method:** 5 new extraction layers: assistant conclusions, user directives, key tool results, uncommitted git state, crash checkpoint. Budget: model-aware (150K for 1M models, 60K for 200K). Trimming order: tool_results → recent_turns → conclusions.
**Result:** Cold resume context enriched from ~3-5K to ~50-100K tokens. Agent resumes with "what was discovered" not just "what files were read." 133 tests pass, 0 regressions, 29 new tests.
**Negative finding:** `subprocess.run` in async context blocks event loop → session hangs. Fix: `asyncio.to_thread` + 3s timeout. Also: `tool_result` blocks don't exist in DB (SDK consumes internally) — function was extracting from data that doesn't exist. Caught only by E2E audit against real DB, not mock tests.
**Implication:** Mock data format ≠ production DB format. Any function reading from DB must verify data exists before writing tests. Also: subprocess in async = always `to_thread` + timeout.
**Ref:** `backend/core/prompt_builder.py` (resume context functions)

---

### E04: Release Scope Gate (v1.9.0 post-incident, 2026-04-29)

**Hypothesis:** Shipping probability of breakage scales superlinearly with commit count. Smaller, more frequent releases > large batches.
**Method:** After v1.9.0 shipped 60 commits with 3 independent P0 regressions, established scope gate: ≤20 commits release freely, 21-40 needs explicit sign-off, >40 must split. Automated smoke test: daemon health returns JSON not HTML.
**Result:** All subsequent releases ≤20 commits. Zero multi-P0 releases since.
**Negative finding:** CI on Ubuntu alone catches zero cross-platform bugs (all 3 P0s were macOS/Windows-specific). Added Windows smoke import job.
**Implication:** Release scope correlates with risk superlinearly, not linearly. CI must cover all target platforms to be meaningful — single-platform CI provides false confidence.
**Ref:** `STEERING.md` (search "Release Scope Gate"), `.github/workflows/`

---

### E05: OOM Cascade Fix (v1.8.0, 2026-04-12)

**Hypothesis:** Dynamic resource-aware scheduling > static limits for multi-session memory management.
**Method:** Replaced `MAX_CONCURRENT=2` with `compute_max_tabs()` using adaptive cost model (1500MB per session, measured). Proactive threshold raised 1.2→1.8GB (below steady state = endless churn). Retry bypass of slot acquisition eliminated.
**Result:** 12 macOS jetsam SIGKILLs in 14 minutes → 0 OOM events in 30+ days. Dynamic ceiling [2,4] adapts to actual RAM pressure.
**Negative finding:** `_SPAWN_COST_MB=500` was 3x under actual (1500MB measured). Heuristic-based cost models must be calibrated against production measurements, not estimates. Also: retry logic that bypasses resource gates is always wrong — it's the "but this one is special" escape hatch that causes cascades.
**Implication:** Resource limits must be adaptive AND measured. Static limits break on hardware changes. Unmeasured estimates break on real workloads. Both are common.
**Ref:** `backend/core/session_router.py` (`compute_max_tabs`), `backend/core/resource_monitor.py`

---

### E06: Lazy Skill Loading (v1.7.0, 2026-04-14)

**Hypothesis:** Two-tier skill injection (full for always-needed, stub for on-demand) > loading all 60+ skills into every session.
**Method:** `tier: always` (15 skills, full instructions in system prompt) vs `tier: lazy` (46 skills, 25-token stub → agent reads INSTRUCTIONS.md on first use via Read tool).
**Result:** 3,650 tokens/session saved (49% reduction in skill listing). Zero degradation in skill invocation success — agent reads full instructions when needed.
**Negative finding:** Skills with complex multi-file workflows need a `manifest.yaml` declaring script entry points. Without it, the agent can't discover related files after reading the stub. Added manifest loader.
**Implication:** Token savings are real but secondary. The primary benefit is attention — 15 always-relevant skills get full attention weight in the prompt vs being buried among 60 stubs.
**Ref:** `backend/core/manifest_loader.py`, `backend/core/skill_registry.py`

---

### E07: Progressive Memory Disclosure (v1.6.0, 2026-03-31)

**Hypothesis:** Selective memory injection (keyword-triggered) > full MEMORY.md dump when memory grows past 30K tokens.
**Method:** 3-layer recall: L0 compact index (one-line summaries with keyword aliases), L1 topic-triggered selective injection, L2 on-demand Read. Keyword matching against conversation focus.
**Result:** System built and tested (222 tests). Currently MEMORY.md is ~15K tokens → full injection always fires. Selective mode not yet triggered in production.
**Negative finding:** CJK has no word boundaries — `set intersection` keyword matching returns empty for Chinese queries. Required substring fallback + shared prefix matching. Also: two separate validation systems (memory_guard + memory_validation) with non-overlapping patterns → merged into one 25-pattern scanner.
**Implication:** CJK text processing requires fundamentally different tokenization. English-first assumptions silently fail rather than loudly error. Also: two systems doing the same thing = guaranteed coverage gap.
**Ref:** `backend/core/memory_index.py` (430 lines, 26 tests)

---

### E08: Evolution Pipeline v2 (v1.5.0, 2026-04-12)

**Hypothesis:** Confidence-gated deployment (observe → recommend → act) > direct autonomous modification of skills.
**Method:** 4-phase architecture: MINE (scan 1,500+ transcripts) → ASSESS (3-signal fitness scoring) → ACT (HIGH ≥0.7 auto-deploy, MED recommend, LOW log) → AUDIT (verify + rollback if needed). Process-level fcntl lock prevents concurrent cycles.
**Result:** Pipeline runs safely every cycle. With 5-7% correction rate, HIGH threshold is unreachable by design — system accumulates observability data without premature deployment.
**Negative finding:** This is an intentional negative: the pipeline is *designed* to not deploy yet. The data accumulation phase is the product, not a failure. Premature deployment based on insufficient evidence is worse than no deployment.
**Implication:** Safe autonomy requires the system to know when it doesn't know enough. A confidence threshold that's unreachable with current data is a feature — it prevents action based on insufficient evidence. The system will deploy when evidence warrants it, not before.
**Ref:** `backend/core/evolution_optimizer.py` (1,688 lines), `Knowledge/Designs/2026-04-12-evolution-pipeline-v2-design.md`

---

## Meta-Pattern

Across 8 experiments, recurring themes:

1. **Measurement > estimation** (E01 lsof, E04 scope, E05 cost model) — Heuristics feel reasonable but are empirically wrong. Measure.
2. **Mandatory > advisory** (E02, E04) — Advisory gates get skipped under pressure. Mandatory gates catch what pressure misses.
3. **Isolation must be multi-layered** (E01 compile+runtime, E04 multi-platform CI) — Single-layer isolation leaks at boundaries.
4. **CJK breaks English-first assumptions silently** (E07) — No error, no warning, just empty results.
5. **Mock ≠ production** (E03) — Tests against mock data prove the code is correct. E2E against real data proves the code is useful. Both required.
