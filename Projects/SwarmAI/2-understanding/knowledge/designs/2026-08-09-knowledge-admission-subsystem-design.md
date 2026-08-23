---
title: Knowledge Admission Subsystem — Architecture Design
date: 2026-08-09
run: run_d32623aa
status: DESIGN (docs-profile pipeline; no code this run)
supersedes: the 5-stage LLM-judge design (rejected at Gate-0 as C042 over-build)
owner: SwarmAI cognitive system (knowledge cultivation ingestion)
---

# Knowledge Admission Subsystem

## 1. Problem (root, verified against source)

Every distilled lesson/decision becomes a **proposal** that the system must route to
one of three fates: **auto-apply** (sediment into a DDD/MEMORY section), **human-review**
(escalate to the Need-You queue), or **discard**. Today that decision is wrong in three
concrete, source-verified ways:

1. **Machine broadcasts pollute the human queue.** `code_intel_feed.py:127-170` emits
   "Undocumented module X" / "Stale ref Y" as *proposals* (`source_stage=code_intel_feed`,
   confidence 0.8-0.9). These are drift **observations**, not judgments — yet they land in
   the same review queue as hard-won reflect insight. (This is the noise XG saw.)
2. **The "trust" signal is a lie.** `_auto_cultivate_pipeline_lessons` (context_health_hook)
   cultivates any *completed* run with `reflect.lessons` populated — it **never checks the
   run passed Gate-2 adversarial**. So `source_stage=reflect` is a *label*, not a trust
   signal; a reflect lesson from an un-vetted run is treated identically to one from a
   Gate-2-passed run.
3. **The calibration loop is wired to the wrong consumer.** `proposal_feedback.get_adjusted_threshold`
   works and IS consumed — but **only by `code_intel_feed.py:53`**. The *main* cultivation
   decision (`_cultivate_proposals` in `ddd_cultivation.py`) never reads it, and
   `check_self_correction`'s fix recommendation is consumed by nobody. So human rejections
   never make the main path smarter → human involvement never shrinks.

**Non-problem (explicitly rejected, Gate-0):** "there is no quality score." FALSE — the
keyword `confidence` (`persist_routing.py:437`) is a *routing* signal (which section), and
`ddd_auto_approval` (`maturity/magnitude/no_conflict/circuit_breaker`) is a *real* quality
gate. Adding an LLM-judge scorer was **over-build** (C042/C044 family) and is dropped.

## 2. Design principle

> Admission is a **filter → trust → reuse-existing-gate → self-calibrate** flow.
> The system must **reuse** the three mechanisms that already exist (auto-approval gate,
> threshold-adjuster, noise filters) and add only the **two missing wires** (real trust
> stamp; calibration on the main path) plus **one source reclassification** (feeds →
> health signals). No new scoring mechanism.

Guardrails (load-bearing):
- **DEC19 (False > Stale > Imperfect):** a wrong AUTO-write to an evergreen zone
  (Architecture/Vision/principle/decision) is decay-permanent → the auto band for evergreen
  demands the STRONGEST evidence and fails **closed** on any gate error.
- **STEERING #2:** no cost/LLM in the zero-LLM session-close hot path; no truncating
  disaster-recovery masquerading as logic.
- **R26 strangler-fig:** the existing section-protection gate stays live until the new
  admission path passes integration tests. No big-bang.
- **Anti-drift (R30#4):** thresholds are **config**, adjusted by the calibration loop from
  real feedback — never frozen magic numbers.

## 3. Architecture — three bands, four components

```
proposal
   │
   ▼
① is_noise()  ───────────────▶ DISCARD  (never queued, never scored, never shown)
   │ (survivor)
   ▼
② trust stamp: passed_adversarial_gate ∈ {passed, failed, n/a}   (verifiable, from run.json)
   │
   ▼
③ decision band  = reuse ddd_auto_approval gate  ×  trust  ×  calibrated threshold
   │
   ├─▶ AUTO      (sediment now)          — high trust AND gate-clean AND ≥ auto threshold
   ├─▶ REVIEW    (Need-You queue)        — the residual that genuinely needs a human
   └─▶ DISCARD   (low value, not noise)  — below review threshold
   │
   ▼
④ calibration loop: human approve/reject  ──▶ adjust thresholds + priors  ──▶ shrink REVIEW over time
```

### Component A — Source filtering (`is_noise()` SSOT + feed reclassification)
- **Consolidate** the scattered noise filters (`is_quality_lesson`, `_INSTANCE_LOG_RE`,
  `_NARRATION_RE`) into **one `is_noise(proposal) -> (bool, reason)`** SSOT in
  `ddd_cultivation.py`. Noise → DISCARD before scoring/queue.
- **Reclassify `code_intel_feed`:** it stops calling `write_proposal`; instead it emits a
  **health signal**. Drift stays observable; it no longer clogs the human-review queue.
  (Kills the machine-broadcast noise at the SOURCE.)
  - **Health sink (Gate-1 R6 — was under-specified):** the drift records
    (undocumented-module, stale-ref) are written to the **existing health surface** that
    `context_health_hook` already produces + `get_code_coverage_for_health` already feeds —
    i.e. the same channel the system-health / DDD-health report reads, NOT a new store.
    Requirement: drift MUST remain visible to a human who *looks at health* (a coverage %
    that moves + an enumerable drift list), but MUST NOT generate a Need-You review item.
    The distinction is **pull (health report, you look when you want) vs push (Need-You,
    it interrupts you)** — drift is pull-only. If no existing health record can hold the
    enumerable drift list, that record is the one new artifact this component adds (small,
    append-only, GC'd like other health data).

### Component B — Real trust stamp (`passed_adversarial_gate`)
- New tri-valued field on `CultivationProposal`, stamped **at creation** by reading the
  source run's stages:
  - `passed` — the source run has an `adversarial`/Gate-2 stage with a machine-readable
    non-blocking outcome.
  - `failed` — adversarial stage exists but its outcome is blocking.
  - `n/a` — the run's profile has no adversarial stage by design (docs/research), OR the
    proposal is not run-sourced (session decision, correction).
- This **replaces the source_stage-label lie**: trust is now a verifiable property of the
  originating run, not a string label. `reflect` from a Gate-2-passed run earns the auto
  band; `reflect` from an un-vetted run does not.

- **⚠️ PREREQUISITE SUB-TASK (Gate-1 R1 — decisive, verified against source): the
  adversarial verdict is NOT machine-readable today.** Real run.json samples show three
  incompatible shapes for the same concept: `gate2_verdict:"PASS_WITH_FIXES"` (enum-ish),
  `gate2_verdict:"2 CRIT (mine) fixed + 3 findings adjudicated"` (free prose), and
  `adversarial_review:{spawned, profile_tier, evidence}` (dict). A trust stamp derived by
  parsing free prose is non-deterministic — it would make Component B unreliable. Therefore
  the implementation MUST first:
  1. **Emit a canonical machine-readable outcome** from the adversarial/deliver stage —
     `gate2_outcome ∈ {pass, pass_with_fixes, block}` — as a first-class field (the existing
     prose `gate2_verdict` may remain as human-facing detail, but the derivation reads the
     enum, never the prose).
  2. **FAIL CLOSED on ambiguity:** any proposal whose source run has an absent, un-parseable,
     or `block` outcome → `passed_adversarial_gate = failed` or `n/a` (never `passed`). Only
     an explicit `pass`/`pass_with_fixes` enum earns `passed`. (DEC19: a wrong `passed` is a
     permanent evergreen auto-write; err toward `n/a`.)
  - This canonicalization is an ordered prerequisite in the migration (§5, step 0) — the
    trust stamp cannot be reliable until it lands.

### Component C — Decision band (REUSE the quality checks; REPLACE the hardcoded doc-whitelist)
- The band composes **existing quality signals** — NO new scorer:
  - `is_noise` → DISCARD (Component A).
  - `ddd_auto_approval` **quality checks** (`maturity`/`magnitude`/`no_conflict`/`circuit_breaker`)
    → reused UNCHANGED.
  - `passed_adversarial_gate` (Component B) → the trust prior.
  - calibrated threshold (Component D) → the cut points.
- **⚠️ THE ONE THING THAT IS NOT PURE REUSE (self-review finding — reconciles the design
  with XG's directive "放开 PROJECT.md/TECH.md/Architecture/Vision, let ≥trust go auto"):**
  the current gate hard-blocks those exact zones by a **static doc-whitelist**, NOT by trust:
  - `ddd_auto_approval._check_safe_doc` → `target_doc in ("IMPROVEMENT.md","TECH.md")` — so
    **PROJECT.md / PRODUCT.md never auto-apply, by hardcode.**
  - `SAFE_APPEND_SECTIONS` → only `TECH.md:{Architecture,Conventions,Runtime Traps}` +
    `IMPROVEMENT.md:{What Worked/Failed/Watch}`; **`_PROTECTED_ZONES` additionally blocks
    TECH.md/Architecture, PRODUCT.md/Vision, all of SELF.md.**
  - These static whitelists are **exactly what XG asked to open**. So Component C **REPLACES
    the hardcoded doc/section whitelist with the trust stamp as the gating authority**:
    - `trust=passed` (Gate-2-passed source run) → **MAY auto-apply into the formerly-locked
      zones** (PROJECT.md/Recent Decisions, TECH.md/Architecture, PRODUCT.md/Vision) —
      provided the reused quality checks (maturity/magnitude/conflict/circuit-breaker) also
      pass and confidence ≥ auto threshold.
    - `trust ∈ {failed, n/a}` → those zones stay locked → REVIEW (never auto). This is the
      OLD behavior, preserved as the fail-closed floor for un-vetted sources.
  - **NO doc is carved out — trust is the SOLE authority (XG directive 2026-08-10, "所有
    docs 都可以改, 别留尾巴").** The static doc/section whitelist is DELETED wholesale:
    `_check_safe_doc`, `SAFE_APPEND_SECTIONS`, `_PROTECTED_ZONES`, and the SELF.md
    code-block are ALL replaced by the single trust test. A `trust=passed` proposal MAY
    auto-write into ANY doc it targets — PROJECT.md, PRODUCT.md/Vision, TECH.md/Architecture,
    **and SELF.md** included. There is no "human-only" doc anymore: a Gate-2-passed run has
    already cleared the bar that human-review existed to enforce.
  - Net: authority shifts from "which doc is it?" (static, wrong) to "did the producing run
    survive adversarial review?" (trust, correct). This IS the root the whole run is about.
- **AUTO** requires: `trust=passed` **AND** reused quality checks clean **AND** confidence ≥
  calibrated-auto-threshold. **Any gate error → REVIEW, never AUTO** (DEC19 fail-closed).
- **REVIEW** = the residual (trust `n/a`/`failed`, or a quality check soft-blocked, or
  between thresholds). The ONLY thing a human sees.
- **DISCARD** = below the review threshold and not noise (low-value, not garbage).
- The current section-protection gate stays live in parallel (shadow) until the new
  trust-driven band passes integration tests (strangler-fig).

### Component D — Calibration loop (close the existing 60%)
- `get_adjusted_threshold` already works + is consumed by `code_intel_feed` — **wire it into
  the main `_cultivate_proposals` decision** so reflect/decision proposals get the same
  precision-driven threshold adjustment.
- **Consume `check_self_correction`'s recommendation** (currently consumed by nobody):
  when a channel accumulates rejections with a dominant reason, apply the mapped fix
  (raise threshold / tighten a source). This is the mechanism by which **human review
  monotonically shrinks**: reasons the human keeps rejecting become auto-discards; reasons
  the human keeps approving relax toward auto.

### Component E — Per-source routing (Gate-1 R8 — was under-specified)
There are **five** proposal sources; the band does NOT treat them uniformly. Explicit table
so the implementation has no split-brain:

| Source | Fate under the new admission flow |
|--------|-----------------------------------|
| `reflect` | Full band (A→B→C→D). Trust from source run's Gate-2 outcome. |
| `decision` | Full band (A→B→C→D). Same as reflect. |
| `correction` | Full band (A→B→C→D). Trust = `n/a` (session-sourced, not run-gated) → cannot auto to evergreen; routes to REVIEW or a safe append per the gate. |
| `conversation` | **UNCHANGED — force-escalate to REVIEW** (raw, low-trust by design; already `_cultivate_proposals` line ~1906). Never auto. The band does not relax this. |
| `code_intel_feed` | **Leaves the proposal system entirely** → health signal (Component A). Not banded. |

Coherence: only reflect/decision/correction flow through the AUTO/REVIEW/DISCARD band;
conversation stays force-escalate; feeds become health. This is a **coherent** partial
state, not split-brain — each source's treatment is a deliberate function of its trust.

## 4. Why this is scalable / maintainable / root
- **Root:** replaces the two actual defects (feed-as-proposal; label-as-trust) and closes
  the actual open loop — not a new mechanism over the symptom.
- **Scalable:** noise dropped before any work; feeds off the human queue entirely; the
  calibration loop makes the queue shrink as the system learns XG's taste.
- **Maintainable:** ONE `is_noise()` SSOT + ONE band function composing existing gates +
  config thresholds. No LLM in the hot path. Every threshold is inspectable + calibrated,
  not hardcoded.

## 5. Migration (strangler-fig)
0. **PREREQUISITE (Gate-1 R1):** canonicalize the adversarial outcome — emit
   `gate2_outcome ∈ {pass, pass_with_fixes, block}` from the adversarial/deliver stage.
   Component B's trust stamp is blocked on this; it must land first. Fail-closed: no enum → `n/a`.
1. Land Component B (trust stamp) + A (`is_noise` SSOT) additively — no behavior change yet.
2. Add the new band function behind a flag; run it in **shadow** (log its verdict vs the
   live section-protection verdict) until the divergence is understood.
3. Flip the band to authoritative, then **DELETE the old section-protection gate**
   (`_check_safe_doc`/`SAFE_APPEND_SECTIONS`/`_PROTECTED_ZONES`). Shadow is a temporary
   verification phase, NOT a permanent parallel path — once integration tests pass, the old
   gate is removed, not left as a backstop (a permanent second gate is a tail). Trust is the
   only authority that remains.
4. Reclassify `code_intel_feed` to health signals (independently shippable).
5. Wire Component D into the main path last (needs A/B/C stable to calibrate against).
6. **Backfill the existing 340 proposals through the new admission flow** (no tail):
   re-run each surviving (non-terminal) proposal through is_noise → trust-stamp → band.
   Machine-broadcast/noise → discarded; trust=passed high-value → auto-applied; the rest →
   the (now-small) review residual. Terminal proposals (applied/rejected/expired) are
   GC'd from disk in the same pass (the disk-hygiene gap). The queue you inherit is the
   queue the new rules would have produced — not a legacy pile grandfathered under old rules.

## 6. Acceptance Criteria
- **AC1 (A-noise):** `is_noise()` is the SINGLE consolidated filter; `is_quality_lesson` +
  `_INSTANCE_LOG_RE` + `_NARRATION_RE` route through it; a noise proposal is DISCARDED
  before it can reach `write_proposal`/the review queue. Test: an instance-log string never
  produces a queued proposal.
- **AC2 (A-feed):** `code_intel_feed` produces ZERO human-review proposals; its drift
  surfaces as a health signal instead. Test: after a code_intel scan, `read_pending_proposals`
  contains no `code_intel_feed`-sourced items; the health record reflects the drift.
- **AC3 (C-trust):** every proposal carries `passed_adversarial_gate ∈ {passed,failed,n/a}`,
  derived by reading the source run's stages (not the source_stage label). Test: a proposal
  from a Gate-2-passed run = `passed`; from a docs run = `n/a`; from a blocked run = `failed`.
- **AC4 (band-AUTO fail-closed):** a proposal targeting an evergreen zone auto-applies ONLY
  when trust=passed AND auto-approval gate clean AND ≥ auto threshold; ANY gate error →
  REVIEW, never AUTO. Test: force a gate error → verdict is REVIEW, not AUTO (mutation).
- **AC5 (band-REVIEW residual):** the review queue contains ONLY items that are neither
  auto-qualified nor discardable — no machine broadcasts, no noise. Test: re-run against
  the current 24 surfaced proposals → the code_intel_feed items are gone, the Gate-2-passed
  reflect insights auto-qualify, the residual is strictly the human-judgment set.
- **AC6 (D-calibration on main path):** `_cultivate_proposals` reads `get_adjusted_threshold`
  for reflect/decision channels (not only code_intel_feed). Test: simulate N rejections with
  a dominant reason → the main-path threshold rises; a subsequent borderline proposal that
  previously escalated now discards (or vice-versa per reason).
- **AC7 (D-self-correction consumed):** `check_self_correction`'s recommendation is consumed
  by a decision-maker (not just logged). Test: drive a channel past `SELF_CORRECTION_BATCH`
  rejections → the mapped fix is applied and observable.
- **AC8 (strangler-fig safety → clean removal):** during shadow mode the new band's verdict
  is logged against the live section-protection verdict with zero behavior change; after the
  flip, the old gate (`_check_safe_doc`/`SAFE_APPEND_SECTIONS`/`_PROTECTED_ZONES`) is
  DELETED, not retained as a backstop. Test: shadow run emits divergence log + live behavior
  unchanged until flip; post-flip grep confirms the old gate functions/constants are gone
  (no permanent parallel path).
- **AC12 (backfill — no legacy pile):** the existing 340 proposals are re-run through the new
  flow; after backfill, `read_pending_proposals` returns only what the new rules produce
  (noise/machine-broadcast discarded, trust=passed auto-applied, terminal files GC'd from
  disk). Test: post-backfill, zero code_intel_feed items + zero terminal .json remain on
  disk; the surviving review set == the band's REVIEW verdict for each.
- **AC9 (anti-drift):** all thresholds are config + calibrated; no new hardcoded magic
  number enters the decision. Test: grep the band function — cut points come from
  config/calibration, not literals.
- **AC11 (trust is the SOLE authority — no doc carved out):** a `trust=passed` proposal
  targeting ANY doc (PROJECT.md, PRODUCT.md/Vision, TECH.md/Architecture, **and SELF.md**)
  that also passes the reused quality checks + threshold → AUTO-applies. The hardcoded
  `_check_safe_doc`/`SAFE_APPEND_SECTIONS`/`_PROTECTED_ZONES` (incl. the SELF.md block) are
  DELETED. Test: a Gate-2-passed reflect → TECH.md/Architecture AND a SELF.md-targeted
  proposal both auto-apply (previously escalated/blocked). Mutation: flip trust→n/a on the
  SAME proposal → it reverts to REVIEW (proves trust, not doc, is the gate). Grep: no
  doc-name literal remains as an auto-apply gate anywhere in the decision path.
- **AC10 (verdict machine-readability — Gate-1 R1 prerequisite):** the adversarial/deliver
  stage emits a canonical `gate2_outcome ∈ {pass, pass_with_fixes, block}`; Component B
  derives trust from that enum, NEVER by parsing prose; an absent/un-parseable outcome →
  `n/a`, never `passed`. Test: a run with `gate2_outcome:block` → proposal trust `failed`;
  a run with only prose `gate2_verdict` and no enum → trust `n/a` (fail-closed), asserted by
  mutation (strip the enum → trust must drop to n/a, not passed).

## 6b. Trust model — the on-disk-stamp question (Gate-2 adjudicated, run_8d5fe9d1 step C)

A Gate-2 adversarial pass flagged "on-disk proposal JSON is a trust boundary — a forged
`passed_adversarial_gate:passed` file could auto-write to SELF.md." Adjudicated against
source (C041 — verify the framing, don't just adopt):

- **The AUTO path never reads a stamp from disk.** `filter_lessons_for_ddd` stamps trust
  FRESH in-memory via `stamp_trust_from_run` (path-traversal-hardened, step B), and
  `_cultivate_proposals` decides on THAT in-memory object; `write_proposal` persists the
  JSON only AFTER the decision. So a forged on-disk stamp CANNOT trigger auto-apply. The
  auto gate (`admission_band`) is airtight.
- **"Forge a proposal JSON" is not a privilege escalation.** It requires write access to
  `<workspace>/.artifacts/proposals/` — the same workspace that contains SELF.md itself
  (`.context/SELF.md`) and the source tree. Anyone with that access can edit SELF.md (or
  the code) directly; routing through a forged proposal is strictly WEAKER than the access
  already held. It is the "attacker who can write your files can write your files"
  tautology, not a boundary crossing the code must defend.
- **The human-approve path applies regardless of trust — BY DESIGN, correctly.** A review
  proposal is trust≠passed by construction (that is WHY it is in review). The human
  approving it IS the authority; re-deriving/​re-gating trust there would make human
  approval of any review item IMPOSSIBLE (it would perpetually re-block). So the approve
  endpoint deliberately does not re-gate. This is not a hole; it is the review workflow.

Conclusion: no code change — the step-C auto path is sound; the "trust boundary" is the
pre-existing property that workspace files are workspace-trusted. Documented here so the
next reader doesn't "fix" the human-approve path into uselessness.

## 7. Non-goals
- Fixing the Q1 存量 pitfall/guideline skew (separate; XG explicitly said keep as-is —
  this is a scoped user decision, NOT a self-imposed tail).
- _(Removed as tails per XG directive 2026-08-10 "别留尾巴": the 340-proposal backfill is now
  migration step 6 + AC12; the old-gate removal is now step 3 + AC8; the SELF.md carveout is
  gone — trust gates all docs, AC11. The implementation IS a non-goal of THIS docs run only
  because a docs-profile run produces the design; the follow-up goal run implements it fully
  with no scope carved off.)_
- An LLM-judge quality scorer (rejected at Gate-0 as over-build).
