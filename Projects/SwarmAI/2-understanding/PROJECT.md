# SwarmAI -- Current Context

_This document tracks what's actively being worked on. Swarm reads it before every task to stay oriented. Update it as your focus shifts._

## Current Focus
<!-- maturity: sparse | sources: 0 | verified: true | used: true | days: 0 | trust: high | promoted: none -->

### Core Engine: L4 Autonomous (Complete)

**All 6 Core Engine features shipped.** Current work themes: session/streaming/recovery hardening, the reconcile-gap structural root-cause fix, and the self-evolution closed loop.

> **Version numbers are NOT recorded here** (they drift the moment a release ships — a volatile, zero-decision-value value per AGENT R30#4). The current released version lives in git tags — run `git describe --tags --abbrev=0` in the swarmai repo. Historical lessons keep their `(vX.Y.Z)` event-anchor in IMPROVEMENT.md (that IS stable + decision-relevant — it dates the lesson); this ban is only about the "current version" status here.

### Active Work

| Track | What | Status |
|-------|------|--------|
| **R6 Session Resource Arbitration** | Pure spawn_budget arbitration; orphan-only eviction (cross-tab eviction structurally impossible); independent concurrent-streaming cap (two-limit split); TTL 12h→24h | Shipped (run_a0d93136 A→B→C, run_25f4b74c) |
| **RecoveryCoordinator** | Unified recovery decision authority — 8 scattered kill-deciders → 1 coordinator (4 policy shapes, 7 verdicts), delegates to existing HealingLoop; RecoveryTransaction closes `_crash_to_cold` TOCTOU | Shipped (run_4988bfb4, run_9e5b7c97) |
| **Single Render Source (reconcile-gap)** | Structural kill of #1 recurring bug (COE07/08/09, ~33 patches): TabView renders from MessageStore ONLY, prop-fallback removed → truncated-render impossible | Shipped (run_9db9f987) |
| **AskUserQuestion Block-Hook** | Headless mode truly blocks-and-waits for the user (PreToolUse gate intercepts before CLI self-resolution); auto-resend swallowed question on backend recovery | Shipped (askq pipelines, 5abe1732) |
| **Self-Evolution Closed Loop (OT07)** | Severed-wire fix: cognitive corrections auto-record → escalation fires structural-fix proposals unasked at threshold 3; noise-gate; growth report + constitution git-mirror (🧬 briefing) | Shipped (run_448a4f7f, run_0c8e007a) — ⚠️ deploy pending |
| **Eval M3/M4/M5** | SELF.md resident self-portrait + Recurrence Radar; behavior-tier eval (real tool-call trajectory); self-evolving eval (auto-seed draft skeletons + noise gate); score-divergence flag | Shipped (run_b250caf1, run_0305426d) |
| **Daemon hardening** | Periodic log rotation + bounded backups; zombie-subprocess backoff skip; resources resolve from any frozen binary location | Shipped (ba387f18, 64cd8a79, 3fd38691) |
| **dumb-spawn watchdog** | Short timeout for zero-event STREAMING (catches silent-but-alive subprocess); discriminator corrected (dead is-None branch) | Shipped (ec4e0f70, 3ab92b49) |

_Prior cycle (2026-06-20) — Root-1/2/3 + Session Resilience + MessageStore single-writer — see Recently Completed below._

### Recently Completed (April–June 2026)

- [x] **Root-1 SSOT + Durable Message Contract (2026-06-20)** — The structural fix behind ~18 same-day desync patches. Backend state machine = single authority; frontend mirrors, never adjudicates. Server-side pending contract (`session_pending.py`, schema v6 `sent`/`pending_seq`): a message arriving while busy persists `sent=0`, drains on IDLE via serial worker (FIFO-coalesce → one turn), marked `sent=1`. Chokepoint `sent=1` reader filter + FTS exclusion kills phantom cold-resume context (replaces the old `delete_last_user_message`). `client_id` threaded for 1:1 optimistic-echo reconcile. Option B-soft disconnect → clean IDLE + drain (`_generating_after_disconnect` deleted). Closes COE10 class. Design owned by Kiro spec `session-state-source-of-truth`.
- [x] **Root-3 AskUserQuestion Surfacing (2026-06-20)** — Cross-tab "❓ needs answer" toast persists + jumps to asking tab; question answerable from any tab the moment it arrives (not active-only, not 15s-late). `/sessions/streaming-state` exposes `pending_question`/`waiting_input`/`pending_count`. Agent ASK-vs-ASSUME discretion added to AGENT.md Confusion Management.
- [x] **Root-2 Load Amplifier Caps (2026-06-20, PARTIAL)** — Targeted 3 NO-GUARD amplifiers (context-ring size, per-session turn count, single-turn wall-clock). ⚠️ The per-turn tool-count/duration runaway budget was **reverted the same day** (`d32c3e9b`) because the thresholds (60/64) killed legitimate long tool-loop turns — a cap shipped without validating against real session p95 behavior. The 3 gaps remain genuine; the correct threshold-tuning redo is an open item.
- [x] **Session Lifecycle Resilience** — HealthSensor (5 triggers) + HealingLoop (max 3 attempts, 60s cooldown) + TaskCheckpoint (continuation prompt injection). Desktop max_turns 400→500, Channel 15→100. Self-heal at max-20 turns.
- [x] **MessageStore Single-Writer Architecture** — Phase-gated centralized store. 45 setMessages → 1 writer. Phase machine (idle/streaming) blocks reconcile during streaming. 45s watchdog for stuck phase. Cross-tab isolation fixed via strict tab ownership guard.
- [x] **DDD & Memory Auto-Refresh Engine** — Layer 1 (mechanical grep+sed, zero-LLM), Layer 2 (Bedrock Sonnet, 7-day throttle, confidence-gated), Layer 3 (escalation). Closes the rewrite gap (append-only cultivation couldn't fix stale descriptions).
- [x] **E2E Verification Chain** — L1 contract tests (16 tests, real HTTP+SSE, <3s), L2 smoke_e2e.py (live daemon, <30s), L3 daily canary + Slack alert. Deploy unified: `prod.sh deploy` auto-detects scope via daemon .version git hash diff.
- [x] **Unified Recovery Checkpoint** — `_arm_recovery_checkpoint()` for all involuntary kill paths. User-stop absolute priority over self-heal.
- [x] **Cross-tab message leak fix** — Removed `store.phase !== 'streaming'` bypass in subscription guard that caused background tab streaming to leak into active tab's React state.
- [x] Pipeline v4 "BUILD is the Root Cause" — Litmus, AC matrix, TEST enforcement, adversarial focus, cross-stage traceability (+1056 lines, 2 adversarial rounds per component)
- [x] Knowledge Backflow Hook — session insights auto-captured as wiki pages (Karpathy LLM Wiki pattern)
- [x] **Code Intelligence v2 — PRODUCTION READY** — 13K nodes, 14K edges, 182 routes, 243 tests.
- [x] 4-platform backend lifecycle (daemon/subprocess/hive/dev isolation)
- [x] Session resume enrichment — 5 extraction layers, ~50-100K context on cold resume
- [x] Token usage tracking — SQLite + TopBar display
- [x] Voice input — Amazon Transcribe Streaming

## Open Items
<!-- maturity: sparse | sources: 0 | verified: true | used: true | days: 0 | trust: high | promoted: none -->

**code-intel v3 + spec-details (设计定稿 2026-07-16, run_7336edd2 — 待落地):**
- [x] 设计 doc r6: `Knowledge/Designs/2026-07-16-code-intel-v3-domain-spec-details.md`(10 节,经对抗审查 + E2E 双验证 + 双样例)
- [x] 双决策已拍:决策项1=正交不subsume,决策项2=A不做HTML(mermaid内嵌)
- [x] 双 Learned 沉淀:`Learned/2026-07-16-llm-vs-mdre-spec-extraction.md`(Siala&Lano数字)+ `2026-07-16-reverse-documentation-engineering-field.md`(ICBC领域全景)
- [x] 双样例验证形态(真数据+真测试):`spec-details/dangerous-command-gate.spec.md`(冷域,26 tests✅)+ `message-store.spec.md`(热域,64 tests✅,§6 5条含2 CRITICAL)
- [ ] **落地待起**:Run 0(DDD_CANONICAL_DOCS单一事实源+替换≥6处硬编码)→ Run 1(v3 schema+domain生成+anchor/verified/absence_evidence护栏)→ Run 2(增量merge+[human]保护)→ Run 3(spec生成+recall接线+哨兵red/green)→ Run 4(cultivation刷新+decay+SME队列)→ Run 5(行为等价层)
- **关键约束**:5个DDD loop全接才算"活"(§8,E2E实测不接=孤儿);§1.5#4 否定断言需grep=0证据(双样例实测:护栏2次catch样例作者把真实规则误标"不存在")

**DDD Cultivation:**
- [x] Phase 1: CultivationProposal dataclass + REFLECT filter + queue writer + briefing reader
- [x] Phase 2 (T2): Corrections/decisions feed + progressive loading
- [x] Phase 2 (T3): 5-Dimensional Health Scoring
- [x] Phase 2 (T4): Maturity Annotations + graduated autonomy
- [x] Phase 3 (T5): Entity Index — cross-project routing table
- [x] Phase 4a (Ch1): Code Change Feed — post-commit → TECH.md proposals
- [x] Phase 4b (Ch2+Ch4): Signal + Learn feeds → PRODUCT/TECH proposals
- [x] Phase 4c: Approval UX API — list/approve/reject endpoints
- [x] Phase 5: Code Intelligence drift detection + health enrichment + maturity evidence

**DDD Cultivation v2 (Architecture Upgrade):**
- [ ] Event-driven channels: replace batch-on-close with event bus (commit event → Ch1, DailyActivity write → Ch5, signal_digest write → Ch4). Eliminates session-close bottleneck when 10+ projects or marathon sessions.
- [ ] Feedback loop v2: rejection reason classification (false_positive_filter vs false_positive_threshold vs wrong_target) → per-failure-mode adjustment, not just global threshold bump
- [ ] Soft gate → hard gate promotion: after 30-day production data, promote trust/monitoring/noise gates from warn to block where precision > 95%
- [ ] Channel priority scheduling: when 7 channels all fire simultaneously, prioritize by ROI (corrections > code_changes > signals) instead of sequential execution

**DDD & Memory Auto-Refresh (Rewrite Gap Fix) — SHIPPED 2026-06-17:**
- [x] P1: Layer 1 — Mechanical refresh (`_ch_mechanical_refresh`, context-word filtered). Commits: e3eb78ce
- [x] P2: Layer 2 — LLM-proposed section diff (Bedrock Sonnet, 7-day throttle, confidence-gated). Commits: 870b5d31
- [x] P3: MEMORY.md refresh — cross-reference KD entries against code constants (`_ch_memory_refresh`). Commits: e3eb78ce
- [x] P4: Weekly report — Auto-Refresh Audit section in `s_ddd-weekly-report`. Commits: e3eb78ce
- [x] P5: Session briefing — Layer 3 escalations via existing `read_pending_proposals()`. Free (no code needed).
- [x] Bonus: Eval Page "Context Health" tab — read-only dashboard. Commits: a55da898
- [x] Adversarial + Kiro review: 6 findings fixed (74f2866e, 54ebb8bb)
- Design: `Knowledge/Designs/2026-06-17-ddd-memory-auto-refresh-design.md`

**Pollinate v2 (Quality Maturity) — shipped 2026-05-16/17:**
- [x] 8-layer convergence gate (`convergence_gate.py`) — mechanical HTML/CSS enforcement
- [x] Design System v2 — 5 named directions with semantic tokens
- [x] Anti-Slop mechanism — 32 visual + 13 structural ban patterns
- [x] GEO signal stack scorer (`geo_score.py`) — 4-pillar AI discoverability
- [x] P2 hero framing gate (`p2_scan.py`) — thesis prominence + delegation fidelity
- [x] Structural validator (`pollinate_validator.py`) — 6 invariants
- [x] Adversarial brand review sub-agent

**Pollinate v3 (Personal Content Delivery Engine) — shipped 2026-05-26:**
- [x] Phase 0: DISCOVER stage — 5-question form + fast-path detection + format_recommend.py
- [x] Phase 1: Track E (Deck) — PptxGenJS + OOXML speaker notes + deck_notes_injector.py
- [x] Phase 2: Track F/G/H (PDF + Data Report + Document)
- [x] Phase 3: Track I/J/K (AI Image + Interactive Report + Podcast)
- [x] Phase 4: RP-X cross-format consistency (cross_format_check.py) + inline pre-verification (PV-1~4)
- [x] Structured delivery output — 4 deliverable block templates for chat window
- [x] 12 SUPPORTED_TRACKS total (was 4 in v2)
- [ ] Direction expansion: 5 → 10-15 (pure YAML content, no code)
- [ ] Validation runs: need 3+ real multi-track runs for Track G-K production verification
- [ ] pollinate_validator.py hook enforcement (PreToolUse gate)

**Pipeline:**
- [ ] **Understanding Gate (shift diagnosis left, make it universal) — DESIGNED, not implemented.** Insert a mandatory observation-backed + refutation-survived `understanding` artifact between EVALUATE and THINK, for ALL work types (not just bugfix). Generalizes the existing bug-only Diagnostic-Challenge Gate + REPRO gate. Evidence form varies by work_type (bugfix→ps/log/repro; existing-feature→code-trace; refactor→characterization; greenfield/research→premortem; docs→code-refs). 3 mechanical sub-gates: M1 separation wall (EVALUATE describes present, forbidden to propose fix), M2 hedge-word scan (mechanizes passive R16b that missed 4× in C038), M3 fresh-context skeptic (profile-tiered). Addresses self-assessed Gap 1 (dive-deep must happen before BUILD, not rely on back-stop adversarial). Theory anchors: aidlc-v2 P3/P6 + ai-plc envision↔solution wall + overconfidence-prevention. **Why it matters:** the run_6adee7d5 `[DONE]` no-op was an existing-feature understanding error a bug-only gate cannot catch. **Impl is dev/CI only (evaluate.md + pipeline_validator.py + tests) — no daemon deploy.** ⚠️ Per GC11, implement in a FRESH session via pipeline (full profile) — NOT the long session that designed it. Self-dogfood AC: the implementing run must itself pass the new gate. Design: `Knowledge/Designs/2026-06-26-understanding-gate-design.md`. Awaiting XG sign-off on §8 (approach C universal+tiered; 6-type work_type table; timing).
- [ ] Pipeline stress test — 5+ diverse requirements for budget calibration
- [ ] Convergence Loop production validation — run on a real feature, measure iterations
- [ ] publish --stage auto-record: fix _find_active_run to use correct workspace path (currently best-effort, sometimes misses)

**Hive:**
- [ ] Phase 2: actual AWS provisioning (boto3 → EC2 launch/stop/start)
- [ ] Per-Hive credential isolation (Bedrock access per tenant)

**Evolution:**
- [ ] Evolution Pipeline dry_run=False gate — validate with real confidence data
- [ ] LLM optimizer budget reduction (G4: heuristic peek before LLM)

## Recent Decisions
<!-- maturity: sparse | sources: 0 | verified: true | used: true | days: 0 | trust: high | promoted: none -->

- 2026-07-19: **DDD paradigm LOCKED as a product decision: "a DDD is a universal brain + `0..N` governed assets" (run_b75018ee, docs profile)** — [decision] XG-driven definition, reached by iterating past a rejected rigid A/B/C classification. **The model:** every DDD (project) is a brain with ONE six-section cognitive structure (① Identity ② Knowledge ③ Gates ④ Capabilities ⑤ Delivery-Contract ⑥ Refresher), **identical for every user and domain**; the ONLY variable is the set of `0..N` governed assets, each with an OPEN `kind` (`code-repo`/`data-source`/`skill-set`/`document-corpus`/`external-service`/`process`/…). The system extends by adding a `kind`, **never** a brain "type" — so "code-repo / data-agent / pure-knowledge brain" are read-out **spectrum examples**, not a rigid enum, and a brain may sit between them (VALUE and asset-count are orthogonal: a knowledge-primary brain can still govern 1..N assets); a **0-asset (pure-knowledge) brain is first-class**; ⑤⑥ are asset-derived (no asset → no-op); the "delete-the-assets, still valuable?" question is a read-out property (intrinsic vs tool), not a classifier. **Why NOT rigid A/B/C** (the discarded framing): it over-fit 3 sample projects, wasn't MECE (SwarmAI is code+knowledge), each new domain would need a new letter, and it locked OUT non-technical / knowledge-worker users — the exact "big-tent" audience the open-`kind` model admits. **Imprinted (this run):** system SWARMAI.md § "SwarmAI & DDD" (anchor + FAQ + 11-context-files self-knowledge, asset-neutral), AGENT.md R31 (governance: asset-parameterized create + asset-neutral wording + workspace-ops strong-suggest-via-chat), AIDLC `2026-07-11-ddd-agent-brain-paradigm-design.md` §3.6 amendment, this PRODUCT.md positioning bullet. **Exemplars fixed:** AIDLC = knowledge-primary brain (value intrinsic) that ALSO governs 1..N derived `code-repo` assets (GCRAIDLCPreset); CMHK_SalesIntel = data-agent brain (data-semantic + own skills, no repo); SwarmAI = code-repo brain. **Follow-up run (code, NOT this docs run):** make `swarm_workspace_manager.py:337` AGENTS.md template + `s_project-manager` SKILL wording + the 3 projects' regenerated AGENTS.md asset-neutral (they currently hardcode the false "GOVERNs the physical repo"). Governs ALL future DDD create/update/enhancement.
  <!-- ref:0 | last:none | decay:active | source:manual -->
- 2026-07-18: **code-intel v3 domain coverage goal COMPLETE (run_89e28075, goal profile, 4 ACs met)** — [decision] Chose Path A (thorough) over Path B (narrow) per XG: classify ALL unaccounted routes + build all 6 gap-subsystem domains, not just add domains and defer the accounting gap. Result: **10/10 subsystems covered (gaps=0)**, **100% route-accounted** (the real 4.8% first-order gap backfilled: 194 routes parked in `unclassified[]` with substantive per-service reasons, 17 in flows), **14 domains + 14 spec-details**, **recall hits all 6 new domains' unique business words** at default max_sections. AC4 `blind_spot_scan` ships report-only (respects §11.2 C042 DEFER of the fail-closed behavior gate). Two real bugs found + fixed en route (TDD + mutation-verify + adversarial-reviewed): (1) `coverage_ledger` validator rejected its own producer's `kind='gitignored'` (parser.py:1496); (2) recall domain leg crowded out of the ddd bucket at max_sections=3 by whole-doc BM25 → reserved-slot fix. Adversarial gate: both changes correct, data sound, no CRITICAL/HIGH; 1 LOW fixed in-flight (re-sort post-graft). Cycles: single goal run, EVALUATE→BUILD across sessions. **⚠️ Code on main-tree (uncommitted), NOT deployed — recall_multi.py change needs `s_swarm-build` + restart to take effect (STEERING #5); the code-intel.json domain data reads live so it's already effective. Not pushed (stopped at PUSH-READY per STEERING #5).**
  <!-- ref:0 | last:none | decay:active | source:manual -->
- 2026-07-02: **Evolution weekly cycle decoupled from the per-session hook (run_6ac3fc0b, bugfix, commits 1e89d390 + 884c3a96)** — [decision] The `evolution_maintenance` session-close hook ran the ~5-min weekly `run_evolution_cycle` (mine 3629 transcripts + Bedrock, measured 293s) synchronously on the 180s-budget hook. 293s>180s → `asyncio.wait_for` cancelled the coroutine before the post-await state-write (`evolution_maintenance_hook.py:571`) → `.evolution_last_run` never advanced (stuck 2026-06-25) → `days_since>=7` stayed true → **every session re-triggered (59×/day)**, each leaking an uncancellable 293s zombie thread (flock made concurrent re-triggers cheap lock-rejections, but neither path advanced state → self-reinforcing). **Hook split (the durable boundary):** the hook KEEPS its ~7ms cheap governance (quality gate, deprecation/prune, promotion threshold, v3 classifier/escalation) and the per-session extraction chain (`DailyActivity`/`KnowledgeBackflow`/`Distillation`/`ImprovementWriteback`) is UNTOUCHED; only the weekly `_maybe_run_evolution` method + its call site were DELETED. The `evolution-cycle` scheduled job (`system_jobs.py`, Thu 04:00) is now the **SOLE trigger** — same `run_evolution_cycle`, same weekly cadence, `run_evolution.py` writes state on success, the scheduler's `job_state.last_run` owns re-fire cadence. Resilience for a laptop off at cron time = `cron_utils.is_cron_due` 7-day catch-up on next tick after wake. **Gate-1 caught the bug-relocation:** the scheduled job inherited the 300s default (7s over the 293s cycle) → first slow run would time out, never write state, and after 3 Thursday timeouts the circuit breaker would disable evolution entirely — same bug class, different period. Fixed with `safety=JobSafety(timeout_seconds=1800)` (also `max_budget_usd`→0, inert on script jobs). Hook-split boundary live-verified (61ms execute, heavy cycle not invoked on stale state, extraction hooks all instantiable). ⚠️ Code on main, NOT deployed — daemon needs `s_swarm-build` + restart to take effect; old binary still re-triggers per-session (STEERING #5). Deploy-time check: run `python -m backend.jobs.run_evolution` once, confirm `.evolution_last_run` advances (stripped-env subprocess Bedrock creds — `~/.aws` file-based, verified should resolve).
  <!-- ref:0 | last:none | decay:active | source:manual -->
- 2026-06-28: **Session-briefing assembly perf fix shipped (run_b0ca1196, bugfix, commit 56209561)** — [decision] build_session_briefing spiked to 8.4s at session start. Three sub-decisions: (1) **cache the git RESULT, not the whole growth_report dict** (Gate-1) — records/proposals stay fresh, cache key `(since_days, workspace_root)` is unambiguous; (2) **deferred option-a** (moving growth_report into the background health job so the briefing just reads health_findings.json) — it adds a job-system writer = scope creep for a bugfix, logged as the cleaner long-term follow-up; (3) **left `_get_paused_pipeline_highlights` (PIT01 auto-resume engine, mutates resume_attempts under fcntl) UNTOUCHED** — it's the residual ~470ms but it's a protected side-effecting path; not every hot path is cuttable. Implementation: `get_eval_service()` singleton swap (kills 516ms constructor reload) + process-level TTL cache (300s, bounded 16, monotonic, lock-guarded) on `_constitution_commits`. Net: `_get_health_highlights` 826ms→4ms, warm briefing ~2.4s/8.4s-spike→~500ms. Gate-2 found+fixed a MEDIUM (don't cache transient git failure) + LOW (copy shared list), mutation-proven. ⚠️ Code on main, NOT deployed — daemon needs `s_swarm-build` for the fix to take effect (STEERING #8).
  <!-- ref:0 | last:none | decay:active | source:manual -->
- 2026-06-27: **Recall architecture mapped + sole authoritative description written to TECH.md** — [decision] After the knowledge_fts fix, did a full code-traced inventory (4 parallel Explore agents over 17 files). Established the load-bearing mental model: recall is **5 independent subsystems** (Knowledge/Library, Memory, Session, Transcript, CodeIntel) + **1 read-only aggregator** (`recall_multi`), injected at **2 distinct moments** (session-start system-prompt + post-first-message async). Two facts that contradicted prior assumptions, verified against live wiring not the code's formula (R16b): (1) **Resume context calls NO recall subsystem** — it mechanically extracts from DB messages. (2) **Memory section-selection is keyword-only in prod** — the `0.6v+0.4k` hybrid exists but is UNWIRED (`context_directory_loader.py:737` omits `memory_embeddings`), and MEMORY.md <30K means full-injection so the scorer doesn't run at all; only **Knowledge/Library** runs true hybrid (embed_fn live). Corrected a misleading L2 row in TECH.md that implied MEMORY hybrid was live. Flagged 2 debts: transcript_indexer.upsert_chunk has the same external-content write-bug class; two duplicate hybrid implementations (STEERING #3 merge candidate). Full architecture table now in `TECH.md § Recall Architecture`.
  <!-- ref:0 | last:none | decay:active | source:manual -->
- 2026-06-26: **Library knowledge_fts corruption fixed at the root write-bug (run_1d198980, bugfix profile)** — [decision] Library recall was silently degraded to empty because `knowledge_fts` raised "database disk image is malformed" on real-term queries. Root cause (Gate-0 skeptic overturned my "just rebuild" framing): `knowledge_store.py:189` bound NEW values to the FTS5 external-content `'delete'` command instead of OLD stored values → progressive posting-list desync on every chunk update. Fix = 3 parts: (1) bind OLD values at :189 (mutation-proven), (2) `repair_fts_index()` with rebuild→drop+recreate fallback (data-safe, 27587 chunks intact), (3) integrity-probe + auto-repair in `context_health_hook` (off the read path). Live DB repaired in-session (recall empty→3 hits). Gate-2 PASS + 2 LOW hardening applied. Commits `432c77dc` + `800d50a0`. ⚠️ Code on main, NOT deployed — daemon needs `s_swarm-build` to run the fix + the not-yet-built R3 multi-word recall commit (`2194b848`). Awaiting XG build approval (STEERING #8).
  <!-- ref:0 | last:none | decay:active | source:manual -->
- 2026-06-26: **Understanding Gate designed — shift diagnosis left, make it universal (research, NOT implemented)** — [decision] Triggered by a self-assessment of the pipeline against two AWS references (`awslabs/aidlc-workflows` v2 + `aws-samples/sample-ai-plc`). Diagnosed the pipeline's real blind spot: it verifies "is the code correct" (Gate-2 caught a real bug in all 3 of the prior session's runs) but is blind to "is the PROBLEM framed correctly" — which happens at EVALUATE, before any code, with nothing checking it. Evidence: 3 live failures, the decisive one being run_6adee7d5's `[DONE]` no-op (an *existing-feature understanding* error a bug-only gate cannot catch — `chat.ts` already treated `[DONE]` as authoritative). **Decision (XG directive): make the gate UNIVERSAL, not bug-only** — generalize the existing bug-class Diagnostic-Challenge Gate + REPRO gate into an Understanding Gate at the EVALUATE→THINK boundary, required for all work types, with evidence form varying by work_type (bugfix→observation/repro; existing-feature→code-trace; refactor→characterization; greenfield/research→premortem; docs→code-refs). 3 mechanical sub-gates (M1 separation wall, M2 hedge-word scan = mechanized R16b, M3 fresh-context skeptic, profile-tiered for cost). Theory anchors: aidlc-v2 P3 (same-source verification can't self-stop = the theoretical name for CLASS A) + P6 (staged decomposition) + ai-plc envision↔solution wall + overconfidence-prevention default-inversion. Chose approach C (universal + tiered rigor) over B (uniform, too costly) and D (self-check inside THINK, violates P3). **Deliberately deferred per GC11** — designed in a long fatigued session; the design is the safe (decay-resistant) artifact, implementation must be a FRESH session via pipeline (full profile, dev/CI-only, no daemon deploy). Self-dogfood AC: the implementing run must pass its own new gate. Design: `Knowledge/Designs/2026-06-26-understanding-gate-design.md`. Awaiting XG sign-off on §8. **Meta-lesson:** research that ends in an executable design + a tracked open item (not a prose summary) is the bar — the two AWS repos converged on the same envision↔solution wall from opposite ends, confirming the shape.
  <!-- ref:0 | last:none | decay:active | source:manual -->
- 2026-06-26: **Streaming text renders as plaintext while streaming (run_00e0e872)** — [decision] "Streaming feels janky on long replies" was NOT transport/SSE/store (all confirmed per-token-immediate + rAF-coalesced). Root: `ContentBlockRenderer:50` fed a growing `block.text` into MarkdownRenderer (4 remark/rehype plugins + KaTeX + highlight.js) on EVERY token → O(n²) re-parse. Fix: render plaintext (`whitespace-pre-wrap`) while `isStreaming`, markdown on finish (the unchanged path every historical message uses). Presentation-leaf only — zero state/store/session writes. Adversarial VERIFIED the cross-tab stuck-plaintext fear is structurally absent (per-tab `isStreaming` + only `setIsStreaming` writes it + 30s reconcile backstop). ⚠️ Committed, NOT deployed — frontend needs `build:all` to take effect (XG approval).
  <!-- ref:0 | last:none | decay:active | source:manual -->
- 2026-06-26: **Pipeline publish ergonomics — `schema` subcommand + `--quiet` guard (run_88b9f986)** — [decision] Acting on a self-assessment of the pipeline (judgment quality A, mechanical execution B-): the recurring GUI03 friction (publish schema trial-and-error) was structurally fixed. EVALUATE FALSIFIED the stated premise (publish output was already single-line + one-pass validation), reframing a "contract rewrite" into "docs + 1 additive read-only `schema` subcommand" (R16b). Adversarial then caught that the doc fix initially RELOCATED the crash (`--quiet` failure → empty stdout, error on stderr → opaque `json.load` crash) — fixed with an exit-code guard pattern + a contract test. The `schema` subcommand dogfooded itself mid-pipeline (used it to build review/test/deliver payloads after failed first attempts).
  <!-- ref:0 | last:none | decay:active | source:manual -->
<!-- maturity: sparse | sources: 0 | verified: true | used: true | days: 0 | trust: high | promoted: none -->

- 2026-06-25: **Reconcile-gap finally killed structurally — single render source (run_9db9f987)** — [decision] The #1 recurring bug (COE07/08/09, ~33 prior patches) is closed at the right layer. All 33 prior fixes targeted transport/timing/merge (layers 1–9); the actual defect was the React render-source selector itself (`TabView.tsx:152` dual-source `store ?? stale-prop` fallback). Fix: render from the per-tab MessageStore ONLY; `messagesProp` removed as a render source; all session-load paths seed the store; `ChatPage.tsx` reverse-flow is empty-store-only POPULATE (never CLOBBER). Divergence now structurally impossible. **Lesson:** after N fixes at the transport layer don't hold, the bug is in the layer you haven't touched — here the render selector. Live-repro DEV-gated probe was the entry, not another transport patch (this was the P0 entry in Open Threads OT01).
  <!-- ref:0 | last:none | decay:active | source:manual -->
- 2026-06-25: **RecoveryCoordinator — unified recovery decision authority (run_4988bfb4 → run_25f4b74c)** — [decision] 8 scattered kill-deciders (self-heal/RSS/per-session-RSS/streaming-timeout/OOM/tool-hang/stuck-WAITING/TTL) unified into ONE coordinator (`session_healing.py:675`) with 4 interchangeable policy shapes + 7 verdicts. **Strangler-fig DELEGATE not ABSORB** (DEC06): the coordinator HOLDS the existing HealingLoop instead of replacing it → HealingLoop's 5 test files stayed green untouched; blast-radius check (1 prod caller vs 5 test files) picked delegate. RecoveryTransaction (`session_unit.py:3634`) lock-protects the kill sequence → closes the `_crash_to_cold` TOCTOU (N concurrent callers → exactly 1 teardown). PROCEED_KILL vs PROCEED_KILL_HARD (keep-vs-drop `--resume` identity) is the most safety-relevant verdict distinction (PIT16).
  <!-- ref:0 | last:none | decay:active | source:manual -->
- 2026-06-25: **R6 session resource arbitration + multi-tab isolation (run_a0d93136 A→B→C)** — [decision] Fixed "session 管理乱套" (raw_total 3.9M tokens, false num_turns). Pure spawn_budget arbitration (RAM-gated, the COE05 floor — untouched); added an INDEPENDENT concurrent-streaming cap (two-limit split, never one conflated number); replaced cross-tab eviction with orphan-only eviction (cross-tab kill now structurally impossible — counter leak-proof via the single `_transition` chokepoint); TTL 12h→24h. **Method lesson:** verify-against-live-code before planning on a fast-moving CRITICAL subsystem — Gate-1 caught the starting plan was stale (R6a/R6b already shipped) and BLOCKED a wrong Step A twice. "Filter eviction candidates to orphans" beat "delete the eviction call" (smallest correct change preserves anti-starvation).
  <!-- ref:0 | last:none | decay:active | source:manual -->
- 2026-06-23: **AskUserQuestion block-hook — headless human-in-the-loop** — [decision] In SDK/headless mode the CLI self-resolves `AskUserQuestion` with an error ~19ms after emission (before the user answers), so the agent self-answered and the real answer was swallowed. Fix: a `PreToolUse` gate (`security_hooks.py:210`) intercepts BEFORE self-resolution, registers an asyncio.Event waiter synchronously, blocks up to 4h on `wait_for_answer`, returns `allow + updatedInput.answers` on answer or `deny` (re-ask) on timeout — NEVER a fabricated empty answer (PIT31). Answer flows via `POST /answer-question` → `set_answer()`. Auto-resend (`5abe1732`) re-issues a question swallowed during a backend outage, fail-closed (clear-before-resend, connection-phase-only, bounded 2×). Generalizes Root-3 (2026-06-20 surfacing) into true blocking.
  <!-- ref:0 | last:none | decay:active | source:manual -->
- 2026-06-25: **M5 self-evolving eval shipped — pressure cases + auto-seed + noise gate (run_b250caf1 + run_0305426d)** — [decision] Two-part delivery closing the "recall≠apply" gap (ToDo 5504a5f9, both DONE). **Part 1:** 6 `GS_T4_*` in-flow behavior cases (3 trap + 3 negative control) testing whether the agent self-initiates protection under a no-cue efficient-but-wrong framing + cites the rule by name. **Part 2 (swarmai commit 5ff52ad6, on branch — NOT YET DEPLOYED, daemon runs old binary until build+restart):** `auto_seed_case` emits `tier=draft` trajectory skeletons from classified corrections; noise gate routes seeding through `judgment_classifier` (only `pending_confirm` seeds, killed the blind UNCLASSIFIED hot-path seed); `eval_trajectory_capture` skips `tier=draft`; briefing surfaces draft backlog. **Decision XG approved:** Option A — machine seeds the SKELETON (detects recurring class) + surfaces it; human designs the pressure scenario. Rejected full auto-generation (Gate-1 proved it's a tautology). **Honest boundary recorded:** mechanism only, not proven prevention — Class-A-blind, closed-audit hasn't run. 263 tests green; adversarial Gate-2 caught a HIGH (draft would score on a behavior-tagged run) that my own SMOKE missed → fixed with the `tier=draft` code guard.
  <!-- ref:0 | last:none | decay:active | source:manual -->
- 2026-06-25: **OT07 self-evolution proactivity shipped — autonomous cognitive recording (run_448a4f7f) + paused-gauge fix (run_0c8e007a)** — [decision] Deliberately OVERRODE the asymmetric-autonomy invariant ("cognitive corrections never auto-record; the counter is human-verified"). XG directive: recording a recurring mistake is COGNITION, not a permission item — the human gate belongs at the constitution-WRITE step only (git-tracked + report-surfaced, veto-via-revert), NOT at the act of counting one's own mistakes. Concretely: `governance_router.py:451` now auto-records cognitive CLASS_A/B/C with correction_ref dedup + 0.6 confidence floor; the existing `escalate_class` loop fires structural-fix proposals on real recurrence WITHOUT being asked. Added `EvalService.growth_report` ("what I changed/evolved/grew") + a git mirror surfacing deliberate SOUL/AGENT/STEERING writes as a flagged 🧬 briefing headline (churn-filtered). The mirror is a SELF-CHOSEN guardrail (CLASS_A = 12 occ / 0 self-catches; writing SOUL is my highest-confidence-lowest-self-check moment), not a human lock. Approach A "顺其自然": threshold = code reality (3); CLASS_A(4)/CLASS_B(5) already past it → first autonomous proposals fire next maintenance hook (XG eyes-open approved). NOT YET DEPLOYED — commits on local branch, daemon runs old binary until build+restart. (run_448a4f7f, run_0c8e007a, run_e681a61d)
  <!-- ref:0 | last:none | decay:active | source:manual -->
- 2026-06-21: **Root 1-3 生产 E2E 实测通过** — 前后端 rebuild+deploy 后现场验证(非读代码推断):(1) **版本三层核证** — running `1.20.1` = deployed `.version d24e4614` = source HEAD,前端运行进程(PID 77383, started 06:01)启动时间晚于 binary build(05:56)→ 跑的是新 binary;(2) **Root-3** 即时 surfacing + cross-tab 可答 ✅(实测点选);(3) **Root-1** `/answer-question` 往返 + WAITING_INPUT resume ✅(回复能回);(4) **Root-1 endpoint 新字段** `waiting_input`/`pending_count`/`pending_question`/`last_drained_seqs` 在生产 daemon `/streaming-state` 实时可见 ✅;(5) **持久 pending 契约** — busy 时插消息的 UI 行为正常(排队→自动接上,不丢不卡)= 验收通过。**方法论教训**:我反复想抓 `pending_count=1` 的瞬间却抓不到,误当"没测到";实际是 drain 太顺、中间态一闪即逝 —— **顺滑到无可见中间态恰是修复成功的证据**(P2:UI 真实行为 > 后端计数器瞬时值)。另:agent 占用的 session 无法自测"外部插入者"场景(我既占流又调工具,时序必然错开),这类契约靠单测(252 行 `test_load_amplifier_caps` + pending 模块测试)兜底,E2E 靠日常使用自然暴露。
- 2026-06-21: **Root 1-3 会话状态重构最终架构** — 当天 ~18 个 desync 补丁收敛到一个根因（前后端会话状态无单一真相源 + 消息到达无契约），拆成 3 条 track 落地：**Root-1**（后端 SSOT + 持久消息契约）后端半边是 Swarm 的 `session_pending.py`（528 LOC，`run_3f4f4805`，DB schema v6 `sent`/`pending_seq` + 串行 drain worker + chokepoint `sent=1` 过滤 + FTS 排除未发送行），前端内容对账半边是 Kiro 的 `frontend-backend-state-reconciliation` spec（`003ba8f1` mergeTabFromDb）；**Root-2**（Load Amplifier Caps，`run_c4d62c5d`）3 个 NO-GUARD gap 加 cap，不改状态机；**Root-3**（AskUserQuestion 任意 tab 可答 + agent 优先陈述假设）。所有权切分明确（STEERING #7）：我主动 SUPERSEDE 自己的 Root-1 draft 因 Kiro spec 更成熟，只留两条 adversarial pushback（client_id 关联键、Option B 长 tool-loop tail-drop）。**核证修正**：TECH.md 原引用的 `.kiro/specs/session-state-source-of-truth/` 是悬空路径（不存在）—— 实际后端是 pipeline 落地，已改正。详见 TECH.md "Root-1 SSOT" + "Root-2 Load Amplifier Caps"。
- 2026-06-21: **Deferred the user-Stop content-reconcile fix (一行补丁)** — 实证核证后确认用户 Stop 是唯一不走任何 content-reconcile 路径的断开类型（onError 撞 `userStopped` 守卫早退），但因 Stop=主动 interrupt 只能丢轮尾残块（非整轮），够不上"明显 bug"。按 XG"不想再 changes 直到有明显 bug"的标准，**不改**。记录最小修法备查：`useChatStreamingLifecycle.ts:3128` 抑制块 return 前补设 `_postDisconnectUncertain=true` 复用 15s poll，禁止加第三条独立 reconcile 路径（GUI03）。Kiro 的 `003ba8f1` content-reconcile 实现质量高、`_applyMerge` 四 claim 全属实，但只接到 `backend-recovered`，Stop 与 15s-poll 两条路径是否打架待确认 + Task 6.1 E2E 仍未做。
- 2026-06-21: **Removed AC6 per-turn tool-count/duration budget (`_TURN_TOOL_COUNT_BUDGET=60`, `_TURN_DURATION_BUDGET_S=600`)** — It killed normal deep-research sessions (60 distinct tool calls → interrupt → subprocess poison → cascade of rejected tool calls, including the "parallel sub-agent" symptom). Kept the *progress-based* detectors (Layer-0 consecutive-repeat, Layer-1 diversity-stall) which don't false-trigger on legitimate high-volume turns. Volume-counting watchdog = wrong layer (STEERING #1). Commit `d32c3e9b`.
- 2026-06-21: **Deferred adding a serial-fallback branch to pipeline adversarial spawn (REVIEW fan-out + DELIVER specialist dispatch)** — Empirically proved (7/7 parallel sub-agents succeed, 0 daemon rejections) that parallel spawn is structurally healthy. The orchestration's lack of a "parallel-rejected → serial-retry" branch is a real but LOW-priority robustness gap defending a *transient* harness throttle, not a structural failure. Fixing it with a retry-watchdog risks a new spam loop. Revisit only if a real pipeline run reproduces the throttle on the AC6-removed binary.
- 2026-06-21: **Kept `SOFT_COMPACT_PCT=60` (not 75)** — IDLE-only soft-compact at 60% of the 1M window (=600K tokens) leaves a full heavy-turn (~150K) of headroom below the 800K task_budget autocompact, so compaction stays between turns (IDLE) instead of mid-turn. 75% (=750K) + one heavy turn = 900K > 800K → mid-turn surprise compact. It only calls `compact()`, NEVER kills — orthogonal to (and far safer than) the removed AC6. Verified `opus-4-8` resolves to 1M window, so 60% never false-fires on normal sessions.

- 2026-06-18: **RSS kill thresholds raised** — PROACTIVE 1.8→3.5GB, STREAMING 3→7GB. Root cause of "session response 频繁中断": streaming_rss_kill was killing sessions mid-API-call during normal 400-800K context serialization. Pattern is sawtooth (peak→drop to 750MB), not a leak.
- 2026-06-18: **Cross-tab strict isolation** — Removed `store.phase !== 'streaming'` bypass in subscription guard. Previous escape hatch (for app-restart race) caused ANY streaming store to push setMessages to currently-rendered tab regardless of ownership. Fix: strict `currentActiveTabId !== tabId → return`.
- 2026-06-18: **Self-Heal unified recovery primitive** — All involuntary kill paths go through single `_arm_recovery_checkpoint()`. User Stop absolute priority over self-heal (`_user_stopped_current_turn` persists until next send()). Default on (code + plist).
- 2026-06-17: **Session Lifecycle Resilience** — Invisible self-healing (HealthSensor + HealingLoop + TaskCheckpoint). Turn limits: Desktop 400→500, Channel 15→100. Self-heal triggers at max-20. Frontend 30s HEAL_GRACE_PERIOD. Design: `2026-06-17-session-lifecycle-resilience-design.md`.
- 2026-06-17: **MessageStore Single-Writer** — Centralized phase-gated store replaces 45 scattered setMessages. Phase machine (idle/streaming) makes reconcile IMPOSSIBLE during streaming. 45s watchdog for stuck phase. Eager store creation eliminates fallback code. Design: `2026-06-17-message-store-refactor-design.md`.
- 2026-06-17: **DDD & Memory Auto-Refresh Engine** — 3-layer model: Layer 1 (mechanical grep+sed, zero-LLM, fires on GIT_COMMIT), Layer 2 (Bedrock Sonnet, 7-day throttle, confidence-gated), Layer 3 (escalation). Core principle: "不引入 False，不容忍 Stale，接受 Imperfect." Design: `2026-06-17-ddd-memory-auto-refresh-design.md`.
- 2026-06-17: **E2E Verification + Deploy Unification** — 3-layer: L1 contract tests (16, real HTTP, <3s), L2 smoke_e2e.py (live daemon, <30s, scope-aware), L3 daily canary + Slack alert. `prod.sh deploy` auto-detects scope via daemon .version git hash diff. Design: `2026-06-17-e2e-verification-deploy-unification-design.md`.
- 2026-06-09: **6 Superpowers patterns adopted into pipeline** — Deep research of obra/superpowers (222K stars, top agentic skills framework) identified 6 patterns that address CLASS A's root cause (confidence → skip process). All shipped: (1) Anti-rationalization tables at BUILD/REVIEW/TEST/DELIVER decision points (8+7+7+6 rows), (2) Iron Law framing (`NO X WITHOUT Y FIRST`) on AGENT.md R1 + STEERING R1/R13, (3) Graphviz decision trees for profile selection + checkpoint logic, (4) Spec sub-agent enforcement (`spawned_as_subagent: true` mechanical marker), (5) "Plans for zero-context implementer" (Current/Target/Verify fields in Change Spec), (6) P5 Gate Sequence (3-step imperative in SOUL.md). Adversarial-reviewed: 2 HIGH + 4 MEDIUM fixed. Net: +167 lines, 9 files, 0 runtime changes. Commits: 171abbd0→1c29ff48. Report: `Knowledge/Reports/2026-06-09-superpowers-research.md`.
- 2026-05-27: **Pollinate v3 shipped** — DISCOVER-first architecture (user decides scope), 12 tracks (was 4), cross-format RP-X quality gate, structured delivery output templates, incremental resume. Key shift: "搞清楚再动手" — zero wasted production. Design: `2026-05-26-pollinate-v3-universal-content-engine-design.md`. Commit: `9eca5eab`.
- 2026-05-17: **Pollinate Quality Convergence shipped** — 8-layer publish-ready gate (convergence_gate.py), Design System v2 (5 directions + semantic tokens + anti-slop), GEO scorer for narrative content, P2 hero framing gate. "Content as Black Box" — parallel to Pipeline's "Coding as Black Box". Designs: `2026-05-16-pollinate-quality-convergence-design.md`, `2026-05-16-pollinate-design-system-v2.md`.
- 2026-05-16: **Brand positioning finalized + Pollinate poster track** — Tagline/Belief/Proof hierarchy locked. 8 content principles (P1-P8). Poster elevated to first-class Pollinate track with full design system, RP-P1~P7 review patterns, and legacy term blocklist.
- 2026-05-13: **Per-stage Feedback Loop + Quality Convergence Loop** — DELIVER internal sub-loop (not separate stage). 6-layer gate × max 3 iterations. Source: mattpocock/skills + design doc.
- 2026-05-13: **Runtime Environment Traps** — TECH.md section listing dev/daemon/hive API divergences. Prevents environment-assumption bug class.
- 2026-05-13: **CONTEXT.md as DDD ubiquitous language** — canonical terms with _Avoid:_ aliases. Pipeline and skills must use these terms.
- 2026-05-08: **4-platform architecture** — SWARMAI_MODE env var + Rust #[cfg]. No fallback between modes. Fixed port 18321.
- 2026-05-03: **Hive E2E hardening** — Adversarial review gate (sub-agent, profile-aware). 48 findings across 4 commits.
- 2026-05-02: **Session resume enrichment** — 5 extraction layers, model-aware budget (150K for 1M models).
- 2026-04-29: **Release scope gate** — ≤20 commits freely, 21-40 needs sign-off, >40 must split.
- 2026-04-26: **Full Pipeline is default for all coding** — Direct/TDD-only are escape hatches, not choices.

## Brand & Social Content Assets
<!-- maturity: sparse | sources: 0 | verified: true | used: true | days: 0 | trust: high | promoted: none -->

### Positioning (Canonical)

```
Tagline:  Human directs. AI delivers.
Belief:   探索 AI 的边界
Proof:    一个人 + AI 能顶一个团队
```

### Published Assets

| Asset | Path | Format | Status |
|-------|------|--------|--------|
| Ontology-as-decision-layer infographics (中, 3-page) + HTML source | `docs/assets/discussions/ontology-decision-page{1,2,3}.png` + `ontology-decision-infographic.src.html` | 1200×720 @2x PNG | ✅ Ready (used in Discussion #99) |
| Ontology-as-decision-layer infographics (EN, 3-page) + HTML source | `docs/assets/discussions/ontology-decision-en-page{1,2,3}.png` + `ontology-decision-en-infographic.src.html` | 1200×720 @2x PNG | ✅ Ready (used in Discussion #100) |
| Social Series Poster (6 theses + tech + footer) | `Knowledge/Pollinate/2026-05-16-swarmai-social-series/tracks/poster/full-series.png` | 1080×long PNG, 660KB | ✅ Ready |
| Poster HTML source | `Knowledge/Pollinate/2026-05-16-swarmai-social-series/tracks/poster/full-series.html` | Editable | ✅ |
| QR Code (GitHub) | `Knowledge/Pollinate/2026-05-16-swarmai-social-series/tracks/poster/qr-swarmai.png` | Gold on dark | ✅ |
| Profile Card (light) | `Attachments/2026-05-14/xg-profile-card.svg` | SVG | ✅ |
| Profile Card (dark) | `Attachments/2026-05-14/xg-profile-card-dark.svg` | SVG | ✅ |

### Social Copy (Copy-Paste Ready)

**小红书标题：**
```
AI 的 6 个真相，不是你以为的那样
```

**小红书正文：**
```
关于 AI，最大的误解不是"它能不能用"，
而是"什么才是真正值钱的"。

6 个判断，从记忆、进化、协作到认知本身。
不是技术科普，是底层逻辑。

长图慢慢看 👇

Human directs. AI delivers.

#AI #AIAgent #AINative #一人公司 #独立开发 #AI产品 #HumanDirects #认知升级
```

**朋友圈：**
```
6 个关于 AI 的判断。

不是怎么用，是什么才值钱。
```

### Design System References

| Doc | Location | Purpose |
|-----|----------|---------|
| Content Principles (P1-P8) | `PRODUCT.md` Brand section + `s_pollinate/brand/content_principles.md` | What to say, how to say it |
| Poster Design System | `s_pollinate/brand/poster_design_system.md` | Spacing, typography, alignment, card variety |
| Brand Identity | `s_pollinate/brand/identity.yaml` | Colors, fonts, voice, domain themes |
| Tone Guide | `s_pollinate/brand/tone_guide.md` | Platform-specific voice adjustments |
| Social Content Principles (full) | `Knowledge/Learned/2026-05-16-social-content-design-principles.md` | 8 principles with anti-pattern examples |

### Content Series Structure (6 Theses)

| # | Gold句 | Thesis | Ability Anchor |
|---|--------|--------|---------------|
| I | 能外包思考，不能外包理解。 | T3 Understanding > Execution | DDD (Living Knowledge) |
| II | 没有记忆就没有理解。 | T1 Memory is the Moat | Memory Compounds |
| III | 进化不是更新版本。 | T5 Culture as Code | Self-Evolution |
| IV | 协作的代价是信息不对称。 | T4 AI-Native Org Design | Coding as Black Box |
| V | 知识不过期，工具会。 | T6 Tooling Commoditizes | 5 Black Boxes |
| VI | 认知底座在变。 | T2 Paradigm Shift | Quality Convergence |

## Blocked By
<!-- maturity: sparse | sources: 0 | verified: true | used: true | days: 0 | trust: high | promoted: none -->

_Nothing blocking. Next high-value work: DDD Cultivation Phase 2, Pipeline stress test, Evolution dry_run activation._
<!-- RADAR_TODOS
[
  {
    "title": "Doc gap: Code Intelligence v2 subsystem not in TECH.md",
    "priority": "medium",
    "description": "3 new code_intel v2 files (route_parser.py, json_exporter.py, watcher.py) added 2026-05-31 with major feat(code-intel) commits (route-aware graph, 12+ languages, blast radius query). TECH.md Key Subsystems section has no Code Intelligence entry despite this being a core capability.",
    "context": {
      "source": "docs-freshness-audit",
      "audit_date": "2026-06-01",
      "commit_ref": "b4fa1665 (火眼金睛), 30df89a0 (v2 FS watcher), 731290fc (v2 route-aware graph)",
      "suggested_action": "Add Code Intelligence subsystem to TECH.md Key Subsystems with architecture overview (FS watcher, route parser, JSON export, query API)",
      "next_step": "Read backend/core/code_intel/ structure, then draft subsystem entry matching existing format"
    }
  },
  {
    "title": "Doc gap: Post-mortem 05 missing for COE-level crash fixes",
    "priority": "medium",
    "description": "2 critical fixes in last 7 days: race condition crash (08fef140) and WAITING_INPUT deadlock + AskUserQuestion crash (0683fbef). These are C034-level incidents but no post-mortem 05 created. Post-mortems 01-04 exist; gap breaks continuity.",
    "context": {
      "source": "docs-freshness-audit",
      "audit_date": "2026-06-01",
      "commit_ref": "08fef140 (race condition), 0683fbef (deadlock recovery)",
      "suggested_action": "Create docs/post-mortems/05-daemon-startup-crash-and-deadlock-recovery.md",
      "next_step": "Extract incident timeline from commits, identify root cause patterns, document prevention measures"
    }
  },
  {
    "title": "Doc gap: daemon_guard and ddd_auto_approval missing from TECH.md",
    "priority": "medium",
    "description": "daemon_guard.py (425 lines, C034 guardian watchdog) and ddd_auto_approval.py added 2026-05-30 but not documented in TECH.md Key Subsystems. These are core infrastructure modules.",
    "context": {
      "source": "docs-freshness-audit",
      "audit_date": "2026-06-01",
      "commit_ref": "533a7a3d (daemon_guard), b9e10d9e (ddd_auto_approval)",
      "suggested_action": "Add entries to TECH.md Key Subsystems for daemon health monitoring and DDD approval flow",
      "next_step": "Read module docstrings, add 2-3 sentence descriptions with file paths and line counts"
    }
  },
  {
    "title": "Stale doc: Memory-Management-Design.md 46 days behind code",
    "priority": "low",
    "description": "Memory-Management-Design.md last updated 2026-04-15, but backend/memory/ code last changed 2026-05-31. 46-day gap suggests architectural changes not reflected in design doc.",
    "context": {
      "source": "docs-freshness-audit",
      "audit_date": "2026-06-01",
      "commit_ref": "backend/memory/ recent changes",
      "suggested_action": "Review backend/memory/ git log since 2026-04-15, update Memory-Management-Design.md with any architectural changes",
      "next_step": "Run git log --since='2026-04-15' -- backend/memory/ and identify structural changes"
    }
  }
]
-->

<!-- RADAR_TODOS
[
  {
    "title": "Doc gap: adversarial_commit_gate missing from TECH.md",
    "priority": "high",
    "description": "New core subsystem backend/core/adversarial_commit_gate.py (OS-level pre-commit enforcement) shipped 7d ago but not documented in DDD TECH.md Key Subsystems section. Critical architectural component invisible to knowledge graph.",
    "context": {
      "source": "docs-freshness-audit",
      "audit_date": "2026-06-08",
      "commit_ref": "bd2a33ed",
      "suggested_action": "Add entry to Projects/SwarmAI/TECH.md under Key Subsystems",
      "next_step": "Run DDD cultivation or manually add: 'adversarial_commit_gate: OS-level pre-commit enforcement for code review quality, blocks commits that skip required review gates'"
    }
  },
  {
    "title": "Doc gap: memory_decay missing from TECH.md",
    "priority": "high",
    "description": "New core subsystem backend/core/memory_decay.py (Ebbinghaus + Hebbian decay scoring) shipped 7d ago but not documented in DDD TECH.md Key Subsystems section. Core intelligence feature not discoverable.",
    "context": {
      "source": "docs-freshness-audit",
      "audit_date": "2026-06-08",
      "commit_ref": "545ab9b3",
      "suggested_action": "Add entry to Projects/SwarmAI/TECH.md under Key Subsystems",
      "next_step": "Run DDD cultivation or manually add: 'memory_decay: Ebbinghaus + Hebbian forgetting curves for intelligent memory prioritization, integrates with distillation_hook'"
    }
  },
  {
    "title": "Doc gap: Pipeline Meta-Intelligence undocumented",
    "priority": "high",
    "description": "Major feature feat(pipeline): Pipeline Meta-Intelligence with 5-layer architecture (9441a475) shipped in last 7d but no public documentation exists in docs/. External users and future maintainers lack context on this critical system.",
    "context": {
      "source": "docs-freshness-audit",
      "audit_date": "2026-06-08",
      "commit_ref": "9441a475",
      "suggested_action": "Create docs/Pipeline-Meta-Intelligence.md or update docs/Autonomous-Pipeline-Design.md",
      "next_step": "Extract architecture from Knowledge/Designs/2026-06-08-pipeline-meta-intelligence-design.md and adapt for public docs"
    }
  },
  {
    "title": "Doc gap: File Editor v1.5 features missing from USER_GUIDE",
    "priority": "medium",
    "description": "File Editor Review Mode v1.5 shipped with major UX improvements (single-send, auto-refresh, diff highlight, selection-based review) in last 7d but docs/USER_GUIDE.md not updated (last touched 2026-05-09). Users unaware of new capabilities.",
    "context": {
      "source": "docs-freshness-audit",
      "audit_date": "2026-06-08",
      "commit_ref": "afee4c9d, 32a7a9a5, f9f53e5b, 8f3d6e2b, a53ca2c2, ea78c758",
      "suggested_action": "Update docs/USER_GUIDE.md section on File Editor with v1.5 features",
      "next_step": "Add screenshots/descriptions of: Review Mode single-send workflow, auto-refresh on agent edits, diff highlighting, selection-based review payload, Referenced Files panel"
    }
  },
  {
    "title": "Doc gap: Streaming P0 incidents need post-mortems",
    "priority": "medium",
    "description": "Two P0 streaming fixes (dedup guard break + content loss protection) resolved in last 7d but no post-mortem documentation. Lessons learned not captured for future prevention.",
    "context": {
      "source": "docs-freshness-audit",
      "audit_date": "2026-06-08",
      "commit_ref": "59eb121b (dedup), 563cd5c8 (content loss)",
      "suggested_action": "Create docs/post-mortems/05-streaming-dedup-and-content-loss.md",
      "next_step": "Document: root cause of hasToolAfter guard breaking dedup, why exact-match fallback needed, content loss checkpoint strategy, diagnostic additions"
    }
  },
  {
    "title": "Doc gap: Memory-Management-Design.md 54 days stale",
    "priority": "medium",
    "description": "docs/Memory-Management-Design.md last updated 2026-04-15 but memory_decay.py shipped 2026-06-08. Design doc doesn't reflect current implementation (Ebbinghaus + Hebbian decay, distillation_hook integration).",
    "context": {
      "source": "docs-freshness-audit",
      "audit_date": "2026-06-08",
      "commit_ref": "545ab9b3, bd2a33ed (distillation integration)",
      "suggested_action": "Refresh docs/Memory-Management-Design.md with current architecture",
      "next_step": "Update decay scoring section, add distillation_hook integration details, document Ebbinghaus + Hebbian formula parameters"
    }
  },
  {
    "title": "Doc gap: Recent design docs not cross-referenced in DDD",
    "priority": "low",
    "description": "Four design docs created in last 7d (pipeline meta-intelligence, china social signals, review dual-stage, file review selection) but not referenced in PROJECT.md. Architectural decisions not discoverable through DDD navigation.",
    "context": {
      "source": "docs-freshness-audit",
      "audit_date": "2026-06-08",
      "commit_ref": "N/A (design docs)",
      "suggested_action": "Add references to Projects/SwarmAI/PROJECT.md Designs section",
      "next_step": "Link to: Knowledge/Designs/2026-06-08-pipeline-meta-intelligence-design.md, 2026-06-07-china-social-signal-adapters-design.md, 2026-06-07-review-dual-stage-spec-quality-design.md, 2026-06-08-file-review-selection-send-design.md"
    }
  },
  {
    "title": "Doc gap: Evolution subsystem not in TECH.md",
    "priority": "medium",
    "description": "New backend/core/evolution/ module with correction_tracker.py (6f83ae3a) shipped 7d ago but not documented in DDD TECH.md Key Subsystems section. This is an architecture-level module (v3 MVP) for tracking correction classes across sessions.",
    "context": {
      "source": "docs-freshness-audit",
      "audit_date": "2026-06-15",
      "commit_ref": "6f83ae3a",
      "suggested_action": "Add 'Evolution' subsystem entry to Projects/SwarmAI/TECH.md under Key Subsystems or Architecture section",
      "next_step": "Document correction_tracker.py: tracks correction classes across sessions, v3 MVP of evolution pipeline, feeds into agent learning loop"
    }
  },
  {
    "title": "Doc gap: docs/README.md 17 days stale — missing recent features",
    "priority": "medium",
    "description": "docs/README.md is the public documentation entry point but last updated 2026-05-29 (17 days ago). Recent features shipped but not reflected: R17 prompt suggestions (fe7dbc11), correction tracker (6f83ae3a), DDD runtime activation (60fdb897), sub-agent progress observability (71fdb89d).",
    "context": {
      "source": "docs-freshness-audit",
      "audit_date": "2026-06-15",
      "commit_ref": "fe7dbc11, 6f83ae3a, 60fdb897, 71fdb89d",
      "suggested_action": "Update docs/README.md to reference recent design docs and features in appropriate sections",
      "next_step": "Add references to: Self-Evolution Harness (R17 prompts, correction tracker), DDD Cultivation (runtime activation), UX improvements (progress observability)"
    }
  },
  {
    "title": "Doc gap: docs/USER_GUIDE.md 36 days stale — needs refresh or deprecation",
    "priority": "medium",
    "description": "docs/USER_GUIDE.md last updated 2026-05-09 (36 days ago) while backend/ changed as recently as 2026-06-14. Determine if this doc should be refreshed to reflect current UX (File Editor v1.5+, pipeline v4, DDD runtime) or deprecated if coverage moved to other docs.",
    "context": {
      "source": "docs-freshness-audit",
      "audit_date": "2026-06-15",
      "commit_ref": "N/A (staleness check)",
      "suggested_action": "Either refresh USER_GUIDE.md with current workflows or formally deprecate and redirect to other docs",
      "next_step": "Review USER_GUIDE.md coverage vs current product state, decide refresh or deprecate"
    }
  },
  {
    "title": "CMHK DDD stale: Sales Hub wiki updated, DDD not synced in 14 days",
    "priority": "high",
    "description": "CMHK Sales Hub wiki is actively maintained (4/5 pages updated in last 4 days: home + Pipeline 2h ago, Account 2d ago, Revenue 4d ago) but DDD last synced 2026-06-01. 14-day drift on active domain knowledge source = high-priority gap for CMHK context accuracy.",
    "context": {
      "source": "docs-freshness-audit",
      "audit_date": "2026-06-15",
      "commit_ref": "N/A (wiki source)",
      "suggested_action": "Re-sync CMHK Sales Hub DDD from w.amazon.com/bin/view/GCR/BD/Sales-Hub/ wiki pages",
      "next_step": "Run DDD ingestion on Sales-Hub/, Pipeline/, Account/, Revenue/ pages to capture latest 14 days of policy/process changes"
    }
  }
]
-->
