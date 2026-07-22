---
title: "Gate-2 Verdict → Environment-Labeled Arena Feed for Evolution"
status: DESIGN (no code this run — docs profile) · REVISED after Gate-1 BLOCK
run: run_e505350f
date: 2026-07-11
project: SwarmAI
anchor: Knowledge/Learned/2026-07-11-opensquilla-harness-native-data-flywheel.md
supersedes: none
---

# Gate-2 Verdict → Environment-Labeled Arena Feed for Evolution

## ⚠️ Gate-1 correction record (read first — the design changed materially)

A fresh-context Gate-1 skeptic (verified against 508 real runs + source) found **3 FATAL
flaws in the original "zero-code wire to corrections.jsonl at REFLECT" framing**. All three
are confirmed against source and reshaped the design:

1. **The signal doesn't exist in the data (dispositive).** Scanned 508 runs / 223
   `adversarial_review` blocks: only **13 carry a `findings[]` array**, and **0 carry
   `category`, 0 carry `first_pass_unresolved`**. `run.json` records the FINAL post-
   convergence state (`resolved:true`), most runs collapse to aggregate counters
   (`findings_fixed/total`). So REFLECT cannot read a "caught-but-shipped, categorized"
   negative example — it was overwritten by the convergence loop before REFLECT runs.
   → **Capture must move EARLIER (DELIVER/Gate-2 time) and use the ALREADY-EXISTING
   `run-observe adversarial_patterns` telemetry**, which the orchestrator emits *at Gate-2
   time* with `findings_by_category` intact (`artifact_cli.py:3755`).
2. **Confidence-floor blocks the record.** `governance_router.py:540` records a cognitive
   correction only if `confidence >= 0.6`; a deterministic no-LLM finding has none → it
   would silently never reach the tracker. → the aggregation path (below) sidesteps the
   per-record cognitive floor entirely.
3. **auto_seed makes the C044 garbage draft.** `eval_service.py` `auto_seed_case` builds a
   golden scenario from `correction_text[:100]` — the tautological "read the doc" draft its
   OWN docstring warns is worthless. Feeding a raw `correctness` finding there reintroduces
   the exact C044 pollution this design claims to prevent. → **v1 does NOT auto-seed from
   pipeline findings**; the golden-case Γ-view is deferred to a human-refined path.

**The revised design below (§3–§11) reflects these fixes.** The original naive framing is
kept struck-through where it helps trace the correction. The core lesson: the pipeline's
verification signal is real, but it lives in **DELIVER-time telemetry (METRICS.json /
run-observe)**, NOT in post-convergence `run.json`, and it must feed a **volume-aware
aggregation**, NOT the sparse human-correction tracker directly.

## ⚠️ Gate-2 correction record (a SECOND adversarial pass tightened the revision)

A fresh-context Gate-2 adversarial reviewer (verified against source) confirmed the 3 Gate-1
flaws are genuinely removed, but caught **that the revision over-claimed its own feasibility**
— corrected here (this is a CLASS-B "asserted capability without verifying" catch, exactly the
class this whole design is about):

- **The existing `adversarial_patterns` telemetry event CANNOT carry the §4 schema.**
  Verified: `artifact_cli.py:3755` accepts only `findings_by_category / rp_violations /
  fixed / dismissed`; there is no `--action`, `--first-pass`, or `--convergence` argparse
  (`:4068-4071`), and `deliver.md` does not emit the event at all (the only documented emit,
  `INSTRUCTIONS.md:1462`, is a POST-fix summary — flaw-1 one layer down). So the `action{}`
  block + first-pass distinction + `convergence_iterations` that §4/§7 depend on require
  **NEW code on the event** — a 4th build piece, NOT a "reuse." Only raw `findings_by_category`
  is carriable today. Corrected in §3/§4/§5/§11.
- **"distinct runs per category" is not real volume normalization** — `correctness` is
  "Always" dispatched, so it saturates to ~N-of-N and can't separate signal from baseline.
  Replaced with a **baseline-deviation / HIGH-first-pass-only** signal (§6/§7#3).
- **§7 #1 still cited the deleted `auto_seed`.** Fixed to credit human-promoted coverage.
- **Honest reframing of value:** v1 delivers *automatic labeling + surfacing, human-gated
  learning* — an **assisted loop, not the autonomous flywheel**. §12 no longer claims the
  auto-flywheel is fulfilled. The gate is the right safety call; the earlier naming was inflated.

## 0. TL;DR (the one-paragraph version — REVISED)

The evolution loop is starved for labels; the pipeline's Gate-2 verifier already produces
environment-labeled outcomes but routes them only to per-run telemetry. The **fix is still a
wire**, but Gate-1 proved the naive path (write to `corrections.jsonl` at REFLECT) is
broken: the categorized "caught-but-shipped" signal is gone by REFLECT time, and the sparse
human-correction tracker + auto-seed path would flood and self-poison (C044). **Revised
design:** capture at **DELIVER/Gate-2 time** using the **already-existing
`run-observe adversarial_patterns` telemetry** (which carries `findings_by_category` before
convergence overwrites it), aggregate into a **volume-aware `arena_stats` rollup** (a
scheduled digest, not the per-correction tracker), and surface a **read-only "what to build
next" signal to the human** — findings feed *evidence for a human's judgment*, they do NOT
auto-advance the recurrence counter or auto-seed golden cases. No LLM re-judging, no eval in
the hot path, human governance-write gate fully intact, and the sparse high-signal
human-correction stream stays uncontaminated.

---

## 1. Problem & Framing (Gate-0 result)

**The disease (verified this run, code-trace):** the harness-native-flywheel principle
(`Knowledge/Learned/2026-07-11-opensquilla-harness-native-data-flywheel.md`) says: every
execution decision produces an *environment-labeled* record where the label comes from
verification, not self-judgment. Our pipeline's Gate-2 adversarial sub-agent IS that
verifier. When it finds a defect in code the pipeline just authored, that is a textbook
**environment-labeled negative example**: "this ACTION (the framing/plan/build the agent
chose) produced a defect the environment caught." It is exactly the label the evolution
loop needs — and we throw it away.

**Why it's thrown away (the load-bearing constraint):**
`backend/core/evolution/judgment_classifier.py:305-310` branches ONLY on `tool_failure` and
`user_correction`; `subagent_finding` + unknown types `return None` → `governance_router.py:249` counts them
as `skipped`. So the corpus, classifier, tracker, `escalate_class`, and `auto_seed_case`
**all already exist and work** — there is simply no record type that carries a pipeline
verification outcome INTO them.

**Reframe (Gate-0 overturned the naive framing; Gate-1 then corrected the feed path):** the
requirement said "build an arena corpus." Ground truth says the consumers (`s_golden-case`,
`s_self-evolution`, the rollup infra) **mostly already exist** — the deliverable is a **feed**,
not a store. But Gate-1 proved the feed cannot be `corrections.jsonl` (signal absent at
REFLECT, floods the sparse tracker, auto_seed garbage). The corrected feed is: **capture at
Gate-2 into the existing telemetry, aggregate volume-safely, surface to the human** (§3).

---

## 2. Approaches Considered (from THINK — recorded here for the design record)

These were the THINK-stage approaches. **Gate-1 then falsified the original recommendation
(A-as-written) and forced the revision now in §3–§8** — the surviving design is "A′":
capture at Gate-2 into telemetry → volume-aware rollup → human-gated promotion.

| # | Constraint | What | Verdict |
|---|-----------|------|---------|
| **A (as recommended in THINK)** | SIMPLICITY | `pipeline_finding` record → `corrections.jsonl` at REFLECT → deterministic classifier branch → reuse tracker/escalate/auto_seed. | ⚠️ **FALSIFIED by Gate-1** (signal absent at REFLECT; confidence-floor blocks record; auto_seed = C044 garbage). Revised → **A′** (§3). |
| **A′ (surviving, post-Gate-1)** | SIMPLICITY | Emit the EXISTING `adversarial_patterns` telemetry at **Gate-2 time** (categorized signal still present) → **volume-aware `arena_rollup`** → **human-gated** promotion via existing `s_golden-case`/`s_self-evolution`. No tracker write, no auto_seed. | ✅ **RECOMMENDED** |
| B | QUALITY | Rich arena store + LLM classifier that re-reads finding text + new post-run hook + propensity fields. | ❌ Over-built. LLM re-judging an already-structured finding = **C044 verifier-noise** for zero gain; new store duplicates telemetry (C042). |
| C | DELETION | Synthesize a `user_correction`-shaped record per finding, drop on the existing path. | ❌ False economy. Fires Bedrock per finding, **pollutes the human-correction stream**, **erases action-vs-label provenance**. |

**Why A′ wins:** it is the true *extend-not-duplicate* AFTER Gate-1 corrected where the signal
lives. The finding is already category-tagged by the Gate-2 sub-agent — so we capture it via
the telemetry that ALREADY carries categories, aggregate volume-safely, and let a **human**
turn a validated trend into a golden case or rule. No second LLM (no verifier-noise), no
tracker flood, no auto_seed garbage. ACTION-vs-LABEL separation is preserved in the event.

---

## 3. Architecture (REVISED — capture at DELIVER, aggregate, surface to human)

The dividing insight: **the pipeline's verification signal is a HIGH-VOLUME machine stream,
not a sparse human correction.** It must NOT be poured into the human/tool `correction_tracker`
(which is tuned for 1–2 sparse corrections/session and would flood + false-escalate). It gets
its OWN volume-aware rollup, and it reaches the recurrence loop only through a **human-reviewed
promotion**, never automatically.

```
  ┌─────────────────── PIPELINE RUN — DELIVER / Gate-2 (already exists) ────────────────┐
  │  Gate-2 adversarial sub-agent returns findings (category+severity) — STILL CATEGORIZED│
  │  at this moment, BEFORE the Quality-Convergence Loop resolves them.                   │
  │  ★ NEW: orchestrator emits ONE run-observe call at Gate-2 time (see §5) carrying the  │
  │     first-pass finding categories + the ACTION context — using the EXISTING           │
  │     `adversarial_patterns` event (artifact_cli.py:3755, findings_by_category).        │
  └───────────────────────────────┬──────────────────────────────────────────────────────┘
                                   │  writes to METRICS.json (EXISTS — per-run telemetry)
                                   ▼
        ┌────────────────────────────────────────────────────────────────┐
        │  ★ NEW: arena_rollup — a scheduled/idempotent digest job that     │
        │  scans completed runs' METRICS.json adversarial_patterns +        │
        │  ACTION context → aggregates into arena_stats.json                │
        │  {class → {count, runs[], example_findings[], first_seen, last}}  │
        │  (volume-aware: dedups per-run, counts DISTINCT runs not findings)│
        └───────────────────────────┬──────────────────────────────────────┘
                                     │  read-only signal, NO auto-write
                                     ▼
        ┌────────────────────────────────────────────────────────────────┐
        │  ★ NEW: growth_report / briefing surfaces the arena signal to the │
        │  HUMAN as a "what to build next" hint:                            │
        │  "Gate-2 caught `correctness` defects in 6 of last 10 runs —      │
        │   candidate blind spot. Promote to a golden case?  [human acts]"  │
        └───────────────────────────┬──────────────────────────────────────┘
                                     │  HUMAN decides (the gate)
              ┌──────────────────────┼───────────────────────────┐
              ▼                      ▼                             ▼
     s_golden-case ADD        s_self-evolution           (or: dismiss — noise)
     (human refines a         PROMOTE (human proposes
      real pressure case)      a rule from the evidence)
     — EXISTS, human-driven   — EXISTS, human-driven
```

**Why this shape (each fix maps to a Gate-1 flaw):**
- **Fixes flaw 1 (signal absent at REFLECT):** capture moves to **Gate-2 time** where findings
  are still categorized, via the EXISTING `run-observe adversarial_patterns` telemetry — not
  a post-convergence `run.json` read.
- **Fixes flaw 2 (auto-seed C044 garbage):** v1 **does NOT auto-seed**. The golden-case Γ-view
  becomes a **human-reviewed promotion** through the existing `s_golden-case` skill (a human
  crafts the pressure scenario — exactly what `auto_seed_case`'s own docstring says must
  happen). No machine-generated tautological draft.
- **Fixes flaw 3 (confidence floor / tracker flood):** pipeline findings never touch
  `correction_tracker` directly. They live in their OWN `arena_stats.json` rollup that counts
  **distinct runs** (volume-normalized), and only reach the recurrence/escalate loop via a
  human promotion — the sparse human-correction stream stays clean.

**Everything marked (EXISTS) is reused. New code (a FUTURE build run) is 3 small pieces:
(1) one `run-observe` emit at Gate-2 in `deliver.md`; (2) an `arena_rollup` digest job over
METRICS.json; (3) a read-only growth_report line. NO classifier branch, NO auto-seed, NO
tracker write.**

---

## 4. The Arena Record — ACTION vs LABEL kept structurally separate (AC1)

The record is an **`adversarial_patterns` telemetry event emitted at Gate-2 time.** The
event NAME + its `findings_by_category` field already exist (`artifact_cli.py:3755`); the
ACTION block, first-pass distinction, and `convergence_iterations` are a **schema EXTENSION
that must be built** (the event does NOT carry them today — Gate-2 correction). The ACTION
context is read from the in-memory run state at Gate-2 collect time (NOT from post-convergence
run.json):

```jsonc
// run-observe --event adversarial_patterns  (EXISTING event, ★ extended with action + first_pass)
{
  "run_id": "run_e505350f",
  "ts": 1752...,

  // ─── ACTION block — what the PIPELINE CHOSE (in-memory at Gate-2 time) ───
  "action": {
    "profile": "full",                    // run.json .profile (in scope during the run)
    "work_type": "existing-feature",      // evaluation.understanding.work_type
    "framing": "<understanding.claim>",   // the frame the agent chose
    "approach": "<research approach_chosen>"
  },

  // ─── LABEL block — what the ENVIRONMENT VERIFIED (Gate-2, first pass) ───
  "label": {
    "source": "gate2_adversarial",
    "findings_by_category": {"correctness": 2, "security": 1},  // EXISTING field, first-pass
    "first_pass_high": 1,                 // count of HIGH/CRITICAL caught before convergence
    "convergence_iterations": 2           // credit-assignment signal (which rework cost)
  },
  "verifier_confidence": "structured"     // never a re-judged LLM score (reward-decoupling)
}
```

**Field sourcing (AC1 — availability verified against source; carriability status honest):**

| Field | Source (data IS available at Gate-2 collect time) | Carriable by event TODAY? |
|---|---|---|
| `label.findings_by_category` | Gate-2 sub-agents' per-finding `category` (`deliver.md:396`), merged before Step-5 fix | ✅ YES — the `--categories` arg exists (`artifact_cli.py:3757`) |
| `label.first_pass_high` | counted from the Gate-2 return **in-memory**, before convergence resolves | ❌ NO — needs a new `--first-pass-high` arg (build piece) |
| `label.convergence_iterations` | `run.json convergence.iterations` (persisted) | ❌ NO — needs a new arg on the event (build piece) |
| `action.*` | in-scope run state at Gate-2 (`.profile`, evaluation/research artifacts loaded) | ❌ NO — needs a new `--action` arg (build piece) |

**Honest status (Gate-2 correction):** the *data* is all available in memory at Gate-2 collect
time (verified — `deliver.md:396` Step-3 merge holds categorized findings before Step-5 fixes
them). But only `findings_by_category` rides the event as it exists; the ACTION block +
first-pass + convergence fields are a **schema extension = build work** (§11 build scope,
piece 1). The doc no longer claims these "already exist on the event."

**The separation still holds (once built):** `action.*` = what the agent decided; `label.*` =
what the environment verified. The rollup (§6) counts the LABEL; the ACTION is carried for
credit-assignment/selection-bias tagging (§7), never as self-supervision.

---

## 5. Capture Point — DELIVER / Gate-2 time, via EXISTING telemetry (AC2, the FIX for flaw 1)

**Where:** `stages/deliver.md`, at the Gate-2 Step-3 merge point (`deliver.md:432-449`) — the
moment the orchestrator holds the merged categorized findings, BEFORE Step-5 fixes them. It
gains ONE emit step. **Note (Gate-2 correction):** `deliver.md` does NOT emit this event today,
and the only documented emit (`INSTRUCTIONS.md:1462`) is a POST-fix summary — so the build must
(a) extend the event schema (§4) and (b) add the emit at Step-3 pre-fix, not reuse the existing
post-fix example (which would recapture the useless post-resolution state = flaw-1 one layer down).

**Why NOT REFLECT (the original error):** by REFLECT time the Quality-Convergence Loop has
resolved the findings and `run.json` collapses them to aggregate counters with no `category`
(verified: 508 runs, 0 categorized persisted findings). The categorized signal exists ONLY at
Gate-2 time, in memory. Capture must happen there or the signal is gone.

**Eval-decoupling boundary respected (R6/R9, AC2):** emitting a telemetry event is **not**
running eval — `run-observe` writes METRICS.json, a `<100ms` local file append
(`INSTRUCTIONS.md:1434`). The `arena_rollup` digest (§6) runs **later**, as a scheduled job
over completed runs — never in the pipeline hot path. NO `eval_runner`/`ci_eval_gate` is
invoked mid-pipeline. The human-promotion step (§6) is the only path to a golden case, and it
runs when the human acts, not during the run.

---

## 6. Signal-Compiler-Γ Mapping — one record → the 3 views, via HUMAN promotion (AC4, the FIX for flaws 2+3)

The OpenSquilla `Γ` maps to our 3 existing consumers — but v1 routes the two write-side views
through a **human gate**, not an automatic path (that is the C044/flood fix):

| Γ view | Consumer | v1 path (REVISED) |
|---|---|---|
| **Recurrence signal** | `arena_stats.json` rollup (NEW) | Automatic, into its OWN store, NOT `correction_tracker`. Read-only aggregation — no `escalate_class` wire, so it cannot false-escalate. **Signal ≠ raw count (Gate-2 correction):** counting distinct-runs-per-category saturates (`correctness` fires ~every run → N-of-N is baseline, not signal). The rollup surfaces a category ONLY on **deviation from its own trailing baseline rate**, OR restricted to **HIGH/CRITICAL first-pass catches**, OR a **novel finding-text cluster** — never raw frequency. A category at its expected rate is silent. |
| **Golden-case candidate** | `s_golden-case` ADD (EXISTS, human-driven) | **Human-reviewed promotion**, NOT `auto_seed_case`. The growth_report surfaces "category X spiked above baseline / a HIGH-severity blind spot recurred — promote?"; a human crafts the real pressure scenario (what auto_seed's docstring says must happen). Fixes flaw 2 (no garbage draft). |
| **Rule-proposal evidence** | `s_self-evolution` PROMOTE (EXISTS, human-driven) | The arena signal becomes **evidence a human weighs**, feeding the SAME Intake Gate — the human decides to promote, the machine never auto-writes. Fixes flaw 3 (no tracker flood → no auto-escalation). |

**Reward decoupling preserved (AC4):** the rollup aggregates **judgment-quality** signal (which
category of defect the verifier keeps catching), never cost/latency. `convergence_iterations`
is stored as a credit-assignment aid, not a reward to minimize. No LLM re-judges the finding
(`verifier_confidence:"structured"`) — the Gate-2 sub-agent already labeled it.

**Why human-in-the-loop for the two write views is the RIGHT call, not a cop-out:** a pipeline
finding is a *weaker* signal than a human correction (it's one verifier's catch on one run,
high-volume, noisy per-item). The OpenSquilla anchor is explicit: "the routed action is weak
evidence, not a gold label." Aggregation makes it a *trend*; the human turns a trend into a
governance change. That is exactly the action-≠-label discipline — the environment supplies
the label (the catch), the human supplies the *judgment* about what to do with the trend.

**Reward decoupling preserved (AC4):** we feed **quality/judgment** signal (did the verifier
catch a defect?) into the loop that trains judgment. We do **NOT** feed cost/token/latency
into it (that would be C042 — optimizing the wrong axis). `label.convergence_iterations` is
recorded as a *credit-assignment* aid (which stage's decision cost the rework), **not** as a
reward to minimize. The finding's `verifier_confidence` is always `"structured"` — we never
attach a re-judged LLM quality score (that is the B-approach verifier-noise we rejected).

---

## 7. The 5 Anti-Self-Poisoning Failure Modes — concrete mechanism for each (AC5)

(From `IMPROVEMENT.md § What to Watch For` + the OpenSquilla anchor. C044 maps onto #3+#5.)

| # | Failure mode | Concrete mechanism in THIS design |
|---|---|---|
| **1. Coverage** | The loop only learns from findings that Gate-2 *happened to* catch; blind spots Gate-2 never probes stay unlabeled. | Accept the honest bound: this feed adds coverage of *the verifier's* catches, not of what it misses. Each **human-promoted** golden case (§6, NOT auto_seed) turns a caught blind spot into a permanent probe — coverage expands via human promotion, not machine seeding. Log the bound; never claim full coverage. |
| **2. Selection bias** | Records come from the *current* pipeline/gate policy — a systematically-blind gate produces a biased sample. | The record carries `action.*` (profile/framing/approach) so the sample is *attributable* to the policy that produced it. When the gate policy changes, old records stay tagged with the old policy → we can filter, not silently mix. (Propensity-style tagging, lightweight.) |
| **3. Label variance** | If every finding groups to the same class, the distribution is degenerate — `correctness` fires ~every run → no separating signal (Gate-2 caught this: raw frequency saturates). | The FIX is the baseline-deviation signal (§6): a category surfaces only when it **deviates from its own trailing rate** or is a **HIGH/CRITICAL first-pass** catch — so "normal correctness noise" is silent and only an *anomalous* spike or a severe catch is signal. This restores separability that raw distinct-run counts destroy. Non-judgment categories (style/maintainability) are tallied but flagged non-cognitive so a human doesn't promote them as a CLASS. |
| **4. Credit assignment** | A run fails at the end; which decision (framing? plan? build?) caused it is lost. | The `action.framing`+`action.approach`+`label.convergence_iterations` are captured **at Gate-2 time per event** — the rollup can attribute a recurring category to the framing/approach shape that keeps producing it, not just "runs fail." This is why ACTION is a structured block carried in the telemetry event. |
| **5. Verifier noise** | The LABEL itself is unreliable (**C044**: an LLM judge fed the answer rules on general knowledge → false green/red). | **Three structural defenses.** (a) We use the Gate-2 sub-agent's **already-structured** category — we do NOT run a second LLM to re-judge (that second judge is the C044 noise source; the rejected B approach). (b) `verifier_confidence:"structured"` marks provenance; a re-judged score is forbidden. (c) **The human-promotion gate (§6) IS the verifier-noise backstop** — a noisy single catch never becomes a golden case or a rule automatically; only an aggregated *trend* a human validates does. Multiple runs must corroborate (rollup counts distinct runs) before the signal even surfaces. |

**Rule of thumb enforced throughout:** ACTION (what the agent chose) is structurally
separate from LABEL (what the environment verified). The rollup counts the LABEL; the human
turns a validated trend into a change. Neither the ACTION nor a single noisy catch ever
supervises itself. That is the authorship trap (CLASS A) defused at the corpus layer.

---

## 8. The class grouping (uses the verifier's OWN categories — no remap needed)

The original design proposed a deterministic `category→CLASS_A/B/C` remap in a classifier
branch. **Gate-1 killed it** on two counts: (a) it never reaches the classifier (findings
aren't in run.json at REFLECT), and (b) mapping every `correctness` finding to CLASS_A
over-counts the authorship trap (most correctness bugs are ordinary defects, not "confidence
overrode process"). 

**Revised:** the rollup groups by the Gate-2 sub-agent's OWN `findings_by_category`
(correctness / security / performance / maintainability — the categories it already emits).
It does NOT assert these ARE CLASS_A/B/C. The mapping "a recurring `correctness`-catch trend
*might* indicate a CLASS_A authorship pattern" is a **hypothesis surfaced to the human**, who
judges it against the actual finding texts before promoting. The machine reports the category
trend; the human assigns the cognitive class. This removes the single unsound judgment Gate-1
flagged and keeps class-assignment where it belongs (human, at promotion).

---

## 9. Boundaries

### Always (non-negotiable)
- The telemetry event keeps ACTION and LABEL as distinct fields — never merged.
- Capture happens at Gate-2 time (categorized signal) — never a post-convergence run.json read.
- Emit is telemetry only (`run-observe` → METRICS.json); it NEVER invokes `eval_runner`/`ci_eval_gate`.
- The rollup counts **distinct runs** per category (volume-normalized), never raw finding count.

### Ask First (human gate stays — this is the load-bearing safety property)
- A golden case from an arena trend goes through `s_golden-case` ADD with a **human-crafted**
  scenario — never `auto_seed_case`.
- A rule from an arena trend goes through `s_self-evolution` PROMOTE + the Intake Gate —
  **human decides**, machine only supplies evidence.
- Assigning a cognitive CLASS to a category trend is a human judgment at promotion time.

### Never (hard constraints)
- NEVER re-judge a finding with a second LLM (C044 verifier-noise — the rejected B approach).
- NEVER write pipeline findings to `corrections.jsonl` / `correction_tracker` (flaws 2+3 —
  floods the sparse human stream + hits the confidence floor + fires auto_seed garbage).
- NEVER auto-seed a golden case from a raw finding (C044 tautological-draft — `auto_seed_case`'s
  own docstring forbids it).
- NEVER run eval inside the pipeline (R6/R9/`eval_command_guard`).
- NEVER auto-write governance from this feed (human gate is the only promotion path).
- NEVER build a parallel corrections corpus (C042).

---

## 10. Success Criteria (exit conditions for a FUTURE build run)

- SC1: A completed run whose Gate-2 caught ≥1 finding emits an `adversarial_patterns`
  telemetry event carrying `findings_by_category` + the ACTION block (requires the schema
  extension, build piece 1) at Gate-2 Step-3 time, verifiable in METRICS.json.
- SC2: The `arena_rollup` digest aggregates completed runs into `arena_stats.json` keyed by
  category, storing per-category **trailing-baseline rate** + example finding texts (not just
  a raw count).
- SC3: A category surfaces ONE read-only growth_report line ONLY when it **deviates above its
  baseline** OR is a HIGH/CRITICAL first-pass catch — a category at its expected rate stays
  SILENT (proves the saturation fix). It does NOT auto-create anything.
- SC4: No pipeline finding ever appears in `corrections.jsonl` or advances
  `correction_tracker` (grep both — the sparse human stream stays clean).
- SC5: No `auto_seed_case` call originates from an arena finding (grep — human promotion only).
- SC6: No `eval_runner`/`ci_eval_gate` appears in any pipeline-run trace (grep the run).

---

## 11. Definition of Done (this docs run) & Out of Scope

**DoD (this run — docs only):**
- [x] This design doc exists, covers AC1–AC6, REVISED twice (Gate-1: 3 FATAL flaws; Gate-2:
      feasibility over-claim + saturation + value framing).
- [x] Arena-record schema (§4) with ACTION/LABEL separation. **AC1 honest status:** the *data*
      is available at Gate-2 collect time (verified: `deliver.md:396` Step-3 merge); of the
      fields, only `findings_by_category` rides the event today — the ACTION block + first-pass +
      convergence fields are a documented **schema-extension build piece**, NOT "already exists"
      (the earlier "✅ verified to exist" claim was wrong; corrected). The 508-run scan verified
      the *absence* of the signal in post-convergence run.json (why capture moves to Gate-2), it
      did NOT verify the new fields exist.
- [x] Capture point pinned to DELIVER/Gate-2 Step-3 pre-fix (NOT REFLECT — the fix), with the
      honest note that deliver.md doesn't emit it today = build work (§5).
- [x] Feeds the EXISTING human-driven consumers (`s_golden-case`, `s_self-evolution`) via a
      baseline-deviation rollup, each cited (§3, §6) — no tracker/auto_seed write.
- [x] Γ mapping to the 3 views, write-views gated by human (§6).
- [~] All 5 anti-poison modes with mechanisms (§7). **AC5 honest status:** #5 (verifier noise)
      is fully sound as-built (human gate + no 2nd LLM); #1/#2/#4 mechanisms DEPEND on the
      ACTION/first-pass schema-extension being built (they are correct once piece-1 ships, not
      before); #3 (label variance) is sound only WITH the baseline-deviation signal (§6), not
      raw counts. Not all 5 are "done today" — 4 are contingent on the build pieces below.
- [x] 3 approaches + recommendation (§2); Gate-1 AND Gate-2 correction records (top of doc).
- [x] Human governance-write gate confirmed intact + strengthened to the primary safety property (§9).

**HONEST VALUE STATEMENT (Gate-2 correction):** v1 delivers **automatic labeling + automatic
surfacing, but human-gated learning** — an *assisted loop*, NOT the fully-autonomous OpenSquilla
flywheel. Two of the three Γ write-views require a human to act on a briefing line. This is the
correct safety tradeoff (a single verifier catch is weak evidence, per the anchor), but it is
honestly a **dashboard-that-assists**, not a self-closing loop. That is the right v1.

**OUT OF SCOPE / BUILD PIECES (explicitly — NO code this run):**
The FUTURE build run (est. 1 run) has **4 pieces**, not 3 (Gate-2 correction — the event
schema-extension is real work, not a reuse):
1. **Extend the `adversarial_patterns` event schema** — add `--action`, `--first-pass-high`,
   `--convergence-iterations` args (`artifact_cli.py:3755` + argparse `:4068`).
2. **Emit at deliver.md Step-3** (post-merge, pre-fix) — new emit, not the post-fix example.
3. **`arena_rollup` digest job** — baseline-deviation signal (NOT raw distinct-run counts).
4. **growth_report read-only line** — the human-facing "promote?" hint.
- Any automatic write to `corrections.jsonl` / `correction_tracker` / `auto_seed_case` from
  pipeline findings — deliberately REMOVED (Gate-1), not deferred.
- `label.source=tests/ci` feeds — v2 extension; v1 wires only the Gate-2 adversarial path.
- Full propensity/doubly-robust estimation — lightweight policy-tagging in v1; v3 deferred.

---

## 12. Traceability to the anchor

**Honest mapping — what v1 delivers of the anchor, and what it deliberately does NOT:**

| OpenSquilla concept | This design (v1) — delivered? |
|---|---|
| Action separate from environment-supplied label | ✅ (once built) §4 ACTION vs LABEL blocks |
| Label from verification, never self-judgment | ✅ §7 #5 + §8 (structured category, no re-judge, human assigns class) |
| Signal Compiler Γ (one record → many views) | ⚠️ PARTIAL — 1 automatic view (rollup) + 2 **human-gated** write views (§6). Not the automatic multi-view compiler. |
| Reward decoupling (quality trains judgment, cost trains orchestration) | ✅ §6 (feed judgment-category only; convergence_iterations is credit-signal, not reward) |
| Does-not-self-poison (off-policy negative examples) | ✅ §7 all 5 modes + the human-promotion backstop |
| Extend, don't rebuild | ✅ mostly — existing telemetry name + existing human skills reused; **4 new build pieces** (§11), the event schema-extension being real work |
| **Automatic flywheel** (the loop closes without a human) | ❌ **NOT delivered by design** — v1 is an *assisted loop* (auto label + auto surface, human-gated learn). The autonomous close is a deliberate v2+ decision, gated on trust in the aggregated signal. |
| "Routed action is weak evidence, not a gold label" (§5.2 anchor) | ✅ §6 — a single catch is weak; only a baseline-deviation trend a human validates becomes a change. This is WHY v1 is assisted, not autonomous. |
