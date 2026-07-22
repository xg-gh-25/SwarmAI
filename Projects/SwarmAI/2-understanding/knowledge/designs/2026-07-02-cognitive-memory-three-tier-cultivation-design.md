---
title: Cognitive MEMORY.md Three-Tier Auto-Cultivation — Govern the Live Writer
date: 2026-07-02
run: run_cbf9cab3
profile: docs
status: design v2 (post Gate-1 BLOCK — 2 load-bearing claims corrected; implementation is a follow-up bugfix run)
supersedes_framing: "add a three-tier auto-commit to cultivation routing" (Gate-0 WRONG-FRAME)
---

# Cognitive MEMORY.md Three-Tier Auto-Cultivation

## TL;DR

There is **already a live, ungated auto-writer** promoting pipeline-REFLECT
lessons straight into `.context/MEMORY.md`
(`context_health_hook._extract_lessons_to_memory`, :823). It is gated only by
`len(lesson) < 20` + "DDD cultivation accepted the project-doc version" + `[:3]`.
It has **no admission gate, no trust signal, no conflict/dedup check** — and it
**bypasses `s_persist`'s Step-0 gate, violating AGENT.md R30**.

This design does NOT build a new auto-commit path (that would be the C042
"build-a-mechanism" trap — the path exists). It **retrofits the existing writer**
with a two-outcome **ADMIT / HOLD-BACK** gate so the human is the bottleneck
ONLY for genuinely risky cognitive knowledge — never for the clear cases. Scope:
**MEMORY.md only.** EVOLUTION.md is explicitly excluded.

The design principle (XG): *the agent auto-sinks what it can judge high-confidence
and useful; the human judges only the risky/protected cases; the human is never a
bottleneck on the clear operational ones.*

> **Gate-1 correction (v2):** the original design had an **ESCALATE** tier that
> wrote a proposal to the cultivation proposal-queue. That path is a **dead-end
> for MEMORY.md** — verified against source: `SAFE_APPEND_SECTIONS` contains only
> `{IMPROVEMENT.md, TECH.md}`, so `apply_to_ddd` returns `not_safe` for MEMORY,
> and the human-approve endpoint then targets `project_dir/MEMORY.md` (nonexistent
> → `doc_missing` → HTTP 500). A human approving an escalated MEMORY proposal
> would get a 500 and the lesson would never land. So the protected/unqualified
> path is now **HOLD-BACK = REJECT-and-log (fail-closed: do NOT auto-write)**, not
> escalate-to-a-broken-queue. "Fail-closed toward NOT auto-writing" is the correct
> conservative default; a genuinely important held-back lesson is still captured
> the normal way — the human/`s_persist` writes it manually, exactly as today.
> Two other Gate-1 corrections (trust signal, classifier honesty) folded in below.

---

## The Problem (verified against source, run_cbf9cab3 Gate-0)

### What everyone assumed vs what the code does

The intuitive framing was: "cognitive lessons route to MEMORY/EVOLUTION with
`safe_auto=False`, so every one waits for human approval — remove that
bottleneck." **Three facts falsify that framing:**

1. **The cultivation routing path cannot reach MEMORY at all.**
   `ddd_cultivation._classify_lesson` (:192) calls `classify_content(..., project="SwarmAI")`.
   With a non-None `project`, `classify_content` (`persist_routing.py:244`) gates
   the `principle` / `cross_project_guideline` routes behind `if project is None`
   — so a pipeline lesson **never** classifies as a MEMORY route through
   cultivation. The `safe_auto=False` cognitive rows (`persist_routing.py:97-111`)
   are unreachable from this path. Flipping that bool changes nothing.

2. **A separate, ungated writer already promotes lessons to MEMORY.md.**
   `context_health_hook._extract_lessons_to_memory` (:823) fires whenever DDD
   cultivation applied ≥1 lesson (:785), takes `lessons[:3]`, and for each with
   `len ≥ 20` (:849) classifies via `classify_entry_type` → routes to a MEMORY
   section via `MEMORY_TYPE_TO_SECTION` (:842) → writes `.context/MEMORY.md`
   under `MEMORY.md.lock` (:835, :880). No admission gate. No trust signal beyond
   "DDD accepted the *project-doc* version". No dedup against existing MEMORY
   entries. **This is the real substrate.**

3. **`apply_to_ddd` couldn't write MEMORY anyway** — it targets
   `project_dir / target_doc` (`ddd_cultivation.py:307`); MEMORY.md is at
   `.context/` (workspace-level), so a cognitive proposal would return
   `doc_missing`. Confirms the cultivation path is not the substrate.

### The R30 breach

AGENT.md R30(3)(a): *any add/update to MEMORY.md/EVOLUTION.md entries "goes
through **s_persist** … so I never hand-edit these stores directly."* s_persist's
**Step-0 admission gate** (`SKILL.md:12-38`) is: *persist only if
decision-relevant AND stable; reject volatile/zero-value; default SKIP.* The live
writer honors none of this. **This design's core job is to close that breach** by
putting a Step-0-equivalent predicate in front of the write.

---

## The Design: Two-Outcome Gate, In-Place Retrofit

Insert a gate at the top of `_extract_lessons_to_memory`'s per-lesson loop. Each
lesson is routed to exactly one of two outcomes (ADMIT or HOLD-BACK).

### Decision table (mechanical — any reader routes a lesson identically)

Two outcomes only: **ADMIT** (auto-write to MEMORY.md) or **HOLD-BACK** (do NOT
auto-write; log the reason). HOLD-BACK is fail-closed — the lesson is simply not
auto-sunk; a human can still persist it manually via `s_persist` exactly as today.

| Step | Check | Fail → | Pass → |
|------|-------|--------|--------|
| 0 | `len(lesson) ≥ 20` (existing :849) | **HOLD-BACK** (too thin) | ↓ |
| 1 | `is_quality_lesson(lesson)` (existing noise filter, `ddd_cultivation.py:170`) | **HOLD-BACK** (instance-log/fragment) | ↓ |
| 2 | NOT volatile — reuse `classify_content`'s existing `NOISE_PATTERNS` + `_MIN_LENGTH` reject (`persist_routing.py:207`), which already implements PART of s_persist Step-0 | **HOLD-BACK** (volatile/zero-value) | ↓ |
| 3 | NOT governance (`is_governance` — a behavioral rule belongs to s_self-evolution, not MEMORY) | **HOLD-BACK** (governance, not knowledge) | ↓ |
| 4 | `classify_entry_type(lesson)` ∈ {`guideline`, `pitfall`} (deliberately narrow: `process` is also operational/reclaimable but HELD-BACK for now — conservative) | **HOLD-BACK** (protected tier principle/correction/decision/model, OR `process`; see below) | ↓ |
| 5 | producing-run **qualified** (`run_qualified` param — see trust signal) | **HOLD-BACK** (unqualified run) | ↓ |
| 6 | NOT an exact/near-duplicate of an existing MEMORY entry in the target section | **HOLD-BACK** (dup — benign skip) | **ADMIT** (write) |

### Why the tiers split where they do

- **ADMIT = operational tiers (`guideline`, `pitfall`) from a qualified run, non-duplicate.**
  These are `layer: operational` in `MEMORY_SECTIONS` (`ddd_entry_lifecycle.py:51-52`)
  and are **NOT** in `_KEEP_TYPES` (`ddd_entry_lifecycle.py:852`) — so the decay
  engine **does** reclaim them if they go stale. Verified live: `_run_memory_lifecycle`
  (`context_health_hook.py:1594`) runs `reclaim_noise_entries` on `.context/MEMORY.md`
  with `dormant_days=45` (:1696); an auto-ADMITted guideline written with
  `ref:0 | last:{today} | decay:active` goes dormant at 45d-unused and becomes
  reclaim-eligible. So a wrong ADMIT is self-correcting (decay ages it out) + git-revert
  is a second net. Low blast radius → safe to auto-sink.

- **HOLD-BACK (protected) = `principle`, `correction`, `decision`, `model`.**
  These are `layer: meta-cognitive`, `evergreen: True`, and **`is_keep_class`
  protects them from decay FOREVER** (`_KEEP_TYPES`, `ddd_entry_lifecycle.py:852`,
  verified). A wrong auto-committed principle is **permanent** — decay never
  reclaims it; only a human `git revert` undoes it. High blast radius → **never
  auto-sunk.** This is the honest core: *we do not auto-sink knowledge our own
  decay engine can't later undo.*

- **HOLD-BACK (other) = thin / noise / volatile / governance / unqualified-run / duplicate.**
  Not auto-written; logged with the reason. The lesson is NOT lost — the normal
  manual `s_persist` path still captures anything a human deems worth keeping.

> **Honest caveat on classifier reliability (Gate-1 Attack-3):** `classify_entry_type`
> (`ddd_entry_lifecycle.py:221`) is a first-match **keyword** classifier defaulting
> to `guideline`. A principle phrased without a principle-keyword can mis-classify
> as `guideline` and thus get ADMITted. This is a **graceful degradation, not a
> catastrophe**: the mis-classified principle lands in the Guidelines section — a
> *reclaimable* slot — so the guarantee is precisely **"no *permanent* auto-commit
> of protected knowledge,"** NOT "no principle is ever auto-written." A
> mis-classified principle-as-guideline is decay-reclaimable + git-revertable, same
> as any operational entry. The design does not claim a hard classifier guarantee.

### The trust signal (AC3) — run-outcome, NOT keyword confidence

The only "confidence" in the codebase is `classify_content`'s
`min(0.4 + total_hits*0.1, …)` (`persist_routing.py:296`) — **keyword-hit-count.**
Gating auto-commit on it is theater (stuff more buzzwords → higher score). The
design instead keys ADMIT on **run-outcome**.

> **Gate-1 correction (Attack-2):** `push_ready` is **NOT a run.json field**
> (verified across 297 runs — it lives only in the deliver *artifact*, not
> run.json). And `_extract_lessons_to_memory` does **not** receive `run_data`
> (it gets `root, lessons, run_id, project` — :823). So the trust gate is:
> **compute `run_qualified` at the call site** (`context_health_hook.py:787`,
> where `run_data` IS in scope, :731) as `run_data["status"] == "completed"`
> (+ `adversarial_review` present in the deliver stage when it exists — a partial
> Gate-2 proxy available in ~61% of completed runs), and **thread it in as a new
> `run_qualified: bool` param.** Resolves the open calibration question:
> `status==completed` is the obtainable signal; push-ready is not available here.
> *A lesson from a run that itself completed is trustworthy; a lesson from an
> abandoned/blocked run is held back.*

### Dedup (Step 6) — exact/near-dup only

Before ADMIT, compare the candidate against existing entries in the target MEMORY
section via `parse_entries` (`ddd_entry_lifecycle.py:277`, verified — parses
`.context/MEMORY.md` by `## section`, already used by `_run_memory_lifecycle`).
Exact/near-duplicate → HOLD-BACK (benign skip). Near-dup can optionally use the
existing `memory_embeddings.py` vector similarity.

> **Gate-1 correction (Attack-5):** the original design claimed "semantic conflict
> (contradicts an existing entry) → escalate." There is **no no-LLM mechanism** for
> contradiction detection in a fast hook (the only existing `_check_no_conflict` in
> `ddd_auto_approval.py:135` merely checks for another *pending proposal* on the same
> section — not contradiction). So semantic-conflict detection is **dropped** from
> this design — claiming it would be hand-waving. Step 6 is **exact/near-dup only.**
> Contradiction between a new lesson and an old one is caught the same way it is
> today: a human reading MEMORY, or the decay engine aging out the loser. This is an
> honest limitation, not a silent gap.

---

## Scope Boundaries

### EVOLUTION.md is EXCLUDED (AC4)

- **The only production writer of `.context/EVOLUTION.md` is
  `evolution_optimizer._log_to_evolution`** (`:936`, resolves `ws/.context/EVOLUTION.md`
  at :949, locked-appends to "Competence Learned" at :980) — owned by the evolution
  thread. That is exactly why a **second** (cultivation) writer would collide.
- The **correction closed-loop** owns EVOLUTION's Corrections substance via
  `CorrectionClassTracker` → `~/.swarm-ai/state/correction_tracker.json` (a JSON
  state machine) + `s_self-evolution` for the doc-level governance. That is a
  **separate thread** (threshold=3 auto-escalation), deliberately out of scope.
- So EVOLUTION.md is **governance-reserved with an existing owner-writer**; adding a
  cultivation writer = collision. The ADMIT path hardcodes `root/.context/MEMORY.md`
  (`context_health_hook.py:835`) so no ADMIT can leak to EVOLUTION. Scope: **MEMORY.md only.**

### Non-Goals

- NOT building a unified auto+manual cognitive-persist module (Approach B —
  rejected as C042 over-build for a ≤3-lessons-per-run path).
- NOT changing the manual `s_persist` path users invoke directly.
- NOT auto-committing principles (the whole point of the protected-tier HOLD-BACK).

---

## Safety Nets (honest accounting — AC5)

| Tier | Decay reclaims a bad entry? | git-revert? | → policy |
|------|:--:|:--:|:--:|
| operational (`guideline`/`pitfall`) | ✅ yes (`layer:operational`, not keep-class; verified live via `_run_memory_lifecycle` `dormant_days=45`) | ✅ yes | ✅ ADMIT (if qualified + non-dup) |
| protected (`principle`/`correction`/`decision`/`model`) | ❌ **NO** (`_KEEP_TYPES` protects forever, verified :852) | ✅ yes (git only) | ❌ HOLD-BACK (never auto-write) |

Both docs are git-tracked (verified `git ls-files`), so **no write is a hard
one-way ratchet** — but for the protected tier, git-revert is the *only* net, and
a silent auto-commit of a wrong principle would sit permanently until a human
notices. That asymmetry is exactly why the protected tier is **HOLD-BACK** (never
auto-sunk). The one residual: a keyword-mis-classified principle-as-guideline can
slip into the reclaimable Guidelines slot (see classifier caveat) — reclaimable +
revertable, so bounded.

---

## Fail-Loud Requirement

The current writer is wrapped in `try/except: pass` (best-effort, :790). The gate
must **not** be silently swallowed: a gate error should log LOUD (reason + lesson)
and default to **HOLD-BACK** (never silent-ADMIT). Fail-closed toward NOT
auto-writing, per P7 — a lesson that couldn't be gate-checked is not auto-sunk;
the human/`s_persist` still captures it manually if it matters.

---

## R30 compliance (Gate-1 Attack-6 — honest)

R30 requires MEMORY writes go "through s_persist." **s_persist has no callable
entrypoint** — it is `SKILL.md` prose + `locked_write.py`; a Python hook cannot
invoke it. So replicating Step-0 as a code predicate is the *only* mechanically
feasible option, and it satisfies R30 **in spirit** (an admission gate now guards
the write), not literally. To **minimize the two-implementation drift risk**, the
predicate REUSES the Step-0 logic that already exists in code —
`classify_content`'s `NOISE_PATTERNS` + `_MIN_LENGTH` reject (`persist_routing.py:207`)
+ `is_quality_lesson` — rather than writing a third copy. Documented as a known
(bounded) drift surface, not a clean win.

## Implementation Sketch (for the follow-up bugfix run)

1. `_admission_ok(lesson) -> bool` — reuse `is_quality_lesson` (floor) +
   `classify_content`'s existing NOISE/`_MIN_LENGTH` reject; do NOT write a third
   Step-0 copy (R30 drift minimization). NOTE: `is_governance` is a **key in the
   `classify_content(...)` return dict** (`persist_routing.py:200`), NOT a callable
   — Steps 2 AND 3 come from ONE `classify_content` call: `r = classify_content(lesson)`
   → `r["is_governance"]` (Step 3) and reuse its NOISE reject (Step 2).
2. Compute `run_qualified` **at the call site** (`context_health_hook.py:787`, where
   `run_data` is in scope): `run_data["status"] == "completed"` (+ deliver-stage
   `adversarial_review` present when available). Thread it in as a new
   `run_qualified: bool` param to `_extract_lessons_to_memory`. (NOT `push_ready` —
   that field does not exist in run.json.)
3. `_is_dup(lesson, section, existing_entries) -> bool` — reuse `parse_entries`;
   exact/near-dup only (optional `memory_embeddings` for near-dup). No
   semantic-conflict detection (no no-LLM mechanism exists).
4. In `_extract_lessons_to_memory`, replace the bare `len < 20` skip with the
   6-step decision table; **ADMIT → existing write path; every HOLD-BACK → log the
   reason + drop (do NOT auto-write, do NOT write a proposal — the MEMORY
   proposal-queue path is broken: `not_safe`/`doc_missing`/HTTP-500).**
5. Fail-loud: the writer's `try/except: pass` (:790) must not swallow the gate — a
   gate error logs LOUD and defaults to HOLD-BACK (never silent-ADMIT).
6. Tests, one per boundary: HOLD-BACK-thin, HOLD-BACK-volatile, HOLD-BACK-governance,
   **HOLD-BACK-principle** (protected tier), HOLD-BACK-unqualified-run, HOLD-BACK-dup,
   ADMIT-operational-qualified. **Mutation-proof:** reverting the tier-gate makes the
   HOLD-BACK-principle test go RED (proves protected-tier auto-commit is actually
   blocked, not theater — the exact CLASS-A lesson from run_c5935199).
7. R30 co-update: the writer's docstring must state it now enforces a
   Step-0-equivalent admission gate + the tier policy.

## Resolved Calibration (was Ask-First)

Operational-tier ADMIT keys on **`status==completed`** (+ adversarial_review-present
when it exists) — NOT push-ready, because `push_ready` is not a run.json field
(Gate-1 Attack-2, verified). This is the obtainable signal.
