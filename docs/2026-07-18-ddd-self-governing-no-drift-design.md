---
created: 2026-07-18
updated: 2026-07-18
---

# DDD Self-Governing Knowledge — Full CRUD Autonomy, Uncertainty-Gated HITL, Zero Drift

> **Goal (XG directive, 2026-07-18):** SwarmAI's own DDD must be the standard-bearer.
> Ingestion, append, update, delete, decay, archive are ALL autonomous. Human-in-the-loop
> fires ONLY on genuine uncertainty — never as a blanket per-zone rule. And drift must
> become **structurally impossible to go undetected**, so the two-month staleness that
> this audit found (stack table selling dead vector search; code-intel called "not yet
> built" while in production; PROJECT.md five releases stale) can never recur.

---

## 0. Why this run exists — the audit that triggered it

A 4-subsystem fresh-context audit (recall / cultivation / code-intel / completeness),
verified against live code, found the DDD **materially drifted**:

| Subsystem | Worst drift | Root |
|-----------|-------------|------|
| Cultivation | DDD says "proposals NEVER auto-applied" — false; safe appends auto-apply, retires auto-delete (cap 2/run). Entire retire/supersession OUT-side undocumented. Decay windows wrong (90/180→ real 60/150; `ref≥10` grace is dead code). | append-only cultivation, no rewrite of trunk |
| Code-intel | TECH.md calls v2 "designed, not yet built" — production since 2026-05-31 (self-contradicts own IMPROVEMENT.md). multi-package/`packages[]` (run_a9fe5ad3) undocumented in all 4 files. | trunk description never rewritten |
| Completeness | PROJECT.md anchored at v1.21/June — system at v1.25/July (~580 commits stale). Hook count contradicts itself (9 / 21 / 13+7). Skill count 89 vs live 91. | volatile numbers stored + never refreshed |
| Recall | Core Recall section is self-aware + correct, BUT surrounding trunk (stack table :329, file-tree comments, roadmap) still sells torn-out vector/Titan as live. | append-only; trunk untouched |

**The single root cause (GUI101, already in MEMORY):** *append-only cultivation ≠ content
freshness — REWRITE is a separate problem.* Cultivation grows the BOTTOM of docs with correct
new lessons; the TRUNK descriptions (stack tables, subsystem status, versions, prose overviews)
rot because nothing detects their semantic drift and nothing rewrites them. Zone-protect makes
it *worse*: PRODUCT/TECH-Architecture are protected FROM auto-cultivation, so they can ONLY be
hand-refreshed — and hands forget for two months.

---

## 1. What ALREADY EXISTS (verified — do NOT rebuild; C042 discipline)

This run is ~80% wiring + judgment-change, ~20% new capability. Ground truth:

| CRUD op | Status today | Where |
|---------|-------------|-------|
| **Ingestion** | ✅ multi-source, 11 event channels | `ddd_orchestrator.py`, hooks: distillation / improvement_writeback / signal_ddd_bridge / code_change_feed / daily_activity |
| **Append** | ✅ auto, confidence-gated (classify threshold 0.35) | `ddd_cultivation.py:_cultivate_proposals`, `apply_to_ddd` |
| **Update/Rewrite** | ⚠️ `change_type="rewrite"` + `replacement_content` EXIST, but rewrite is **hard-escalated** (`:204` destructive→never auto) | `ddd_cultivation.py` CultivationProposal |
| **Delete/Retire** | ✅ auto-apply w/ caps (2/run, 3/day), reversible (archive+.bak+strip) | `ddd_cultivation.py:apply_retire_proposal`, `_detect_supersession` |
| **Decay** | ✅ fully auto (60d→dormant) | `ddd_entry_lifecycle.py:assess_decay` |
| **Archive** | ✅ fully auto (150d total→archived) | `ddd_entry_lifecycle.py` |
| **Uncertainty signal** | ✅ ALREADY COMPUTED: overlap score + coverage ratio + **margin over runner-up** (`second_score`) + distinguishing-token | `ddd_cultivation.py:_locate_target_entry:688-724` |
| **Doc staleness** | ⚠️ ONLY doc-level (mtime>7d + ≥3 commits) — **no entry/trunk semantic drift detection** | `jobs/handlers/ddd_refresh.py:_check_staleness` |

**Key realization:** the "uncertainty" XG wants as the HITL trigger is NOT a new concept to
invent — it is the **margin geometry already used to gate auto-retire** (`best_score` vs
`second_score`, `best_ratio`, `has_distinguishing`). We generalize that ONE signal into the
universal HITL gate across ALL ops.

---

## 2. Target model — PURE AUTONOMY, no HITL queue (XG directive 2026-07-18)

> **Simplified after XG's call:** there is NO human-in-the-loop queue, NO escalation
> backlog, NO "finding" to-do list. A backlog is itself a human-dependency (someone must
> read it) — the exact black hole that let the DDD rot for two months. Removed.
> `s_persist` is the manual entry point: a human who WANTS to intervene triggers it
> anytime. The system never pushes work TO a human. "靠人不靠谱" — the flow runs itself.

### 2.1 The universal decision — TWO tiers only

Every CRUD op resolves through ONE gate, into exactly two outcomes:

```
compute uncertainty U(op, target)  →
  U low   (confident, unambiguous, evidence-backed) → AUTO-APPLY (reversible .bak + changelog)
  U high  (ambiguous / weak-evidence / low-margin)  → SKIP — do nothing, record nothing
```

**Why "skip and record nothing" is correct, not lossy:**
- Skipping is the SAFE failure mode: never auto-rewrite trunk on a weak guess (that corrupts
  trunk — worse than drift).
- Convergence still happens WITHOUT a backlog: the drift scanner is PERIODIC. A claim that is
  ambiguous today (U high → skipped) gets auto-fixed on a LATER pass once code evolution makes
  it unambiguously false (U drops). No human clears a queue — the machine catches it when
  evidence strengthens.
- `s_persist` is the manual override for anything the auto-flow leaves untouched.

**`changelog` is KEPT (it is NOT a human-dependency):** `EVOLUTION_CHANGELOG.jsonl` records
what auto-applied + how to revert (.bak). It is passive provenance / rollback data — write-and-
forget, never a "please review" prompt, never surfaced as a to-do. Reversible autonomy requires it.

**Uncertainty U is a function of measurable signals (no LLM-vibes):**
- **Locate margin** — `best_score − second_score` (how clearly does this target ONE entry?).
  Low margin = ambiguous target = uncertain.
- **Coverage ratio** — `overlap / len(title_tokens)` (is the match substantial?).
- **Distinguishing token** — a corpus-unique shared token proves "this entry, not a generic
  collision."
- **Blast radius** — trunk/authoritative-semantic content (Vision, Non-Goals, Architecture
  overview, a stack table) has higher blast than a leaf lesson → raises U.
- **Reversibility** — append/decay/archive are cheaply reversible (low U contribution);
  rewrite-of-trunk is semantically lossy (raises U).
- **Evidence strength** — supersession/contradiction backed by a verbatim code/behavior quote
  lowers U; "seems stale" without evidence raises it.

**Zone-protect is DEMOTED to a U-contribution, not a veto:** an authoritative zone no longer
means "block." It means "**U starts higher**" — a trunk rewrite with STRONG evidence + high
margin auto-applies; a trunk rewrite on a weak guess is SKIPPED (U high → do nothing). Shift
from *zone-based veto* → *uncertainty-based auto/skip*. (SELF.md stays hard-blocked from
auto-mutation — the one artifact code-blocked by design; the drift scanner may READ it but
never writes it.)

### 2.2 Rewrite/Update becomes a first-class autonomous op

Today rewrite exists but hard-escalates (dead-ends in a queue nobody reads). Target: a rewrite
AUTO-APPLIES when U is low — (a) targets exactly one entry (high margin over runner-up),
(b) replacement backed by verbatim evidence the old text is falsified, (c) reversible (.bak).
When U is high → SKIP (not escalate). This is what lets the TRUNK self-heal instead of only
the leaves growing — with no human gate.

### 2.3 Drift detection — the missing organ (the real root fix)

`ddd_refresh._check_staleness` only knows "file old + commits happened." It cannot see that
a stack table SELLS a torn-out feature. Add **semantic drift detection**:

- **Claim extraction:** parse trunk descriptions into checkable claims ("recall uses vector
  search", "code-intel v2 not yet built", "89 skills", "version v1.21").
- **Claim verification against live source:** each claim class has a cheap verifier —
  grep/AST/file-count/VERSION-read. A claim that no longer holds → a candidate rewrite.
- **Candidate → §2.1 gate:** U low → auto-rewrite (.bak + changelog); U high → SKIP (nothing
  recorded, retried next periodic scan when evidence strengthens).
- **This dog-foods our own AI-ready-repo engine:** the same code-intel that reads any repo's
  truth is what verifies our DDD claims. The standard-bearer verifies itself with its own tool.
- **PERIODIC + SELF-HEALING (the命脉):** the scanner runs on a schedule (a real scheduled job)
  AND in `context_health` deep-check — NOT once, NOT on-demand-only. Self-repair is automatic;
  no proposal sits in a briefing waiting for a human. This is the loop that makes "zero drift"
  a running property, not a one-time cleanup.

### 2.4 Anti-drift by construction — volatile numbers

Per AGENT R30#4, volatile zero-decision numbers (LOC, skill count, test count, version)
must NOT be stored — they must be **live-measured or removed**. The drift detector treats a
stored volatile number as a drift finding by default (it WILL be wrong soon). Fix path:
replace with a live-read or a qualitative statement.

---

## 2.5 Gate-0 RESHAPE (adopted 2026-07-18, XG chose 丙) — the two verifier tiers

Gate-0 (fresh-context, live-verified) BLOCKED the original pure-auto-trunk-rewrite framing and
was RIGHT on 4 counts, all confirmed against source:
1. The uncertainty signal (`_locate_target_entry:686-743`) measures **which entry a lesson
   targets** (a LOCATOR: margin + coverage + distinguishing-token) — NOT "is this rewrite
   factually correct." Reusing it to auto-approve prose rewrites = PIT13 category error.
2. A bounded deterministic scanner can **detect** a wrong claim but for SEMANTIC prose it
   cannot **author** the correct replacement — that needs an LLM (the existing `ddd_refresh`
   flow already does this via Sonnet, `:199-227`).
3. The audit's headline drift (stack table :329 "Titan v2 + sqlite-vec") is a **FALSE POSITIVE**
   — sqlite-vec + Titan ARE live in the **Knowledge store** (`knowledge_store.py:32,134,160`,
   `main.py:1485`). The vector leg was torn out of the **recall path** only. Re-verify before
   "fixing" :329 — the nuance is recall-vs-knowledge-store, not "vector is dead."
4. The proposal queue has LIVE consumers — API (`routers/cultivation.py`, mounted `main.py:1306`)
   + UI (`EvalDashboard.tsx:809`) + the DEC19 safety invariant (`ddd_cultivation.py:974-978`
   force-escalates conversation-source knowledge). Deleting the tier strands all three (R27).

**The 丙 resolution (XG's call): TWO verifier tiers, both auto, neither invents a correctness judge.**

| Tier | What | Fix path | Safety |
|------|------|----------|--------|
| **FIXABLE** (deterministic) | version-string, file/skill counts, decay-number constants, boolean "X is built" verifiable by grep/read | detect + generate correct value + **auto-apply** (.bak + changelog) | value is machine-derived from source → provably correct |
| **SEMANTIC** (prose) | "code-intel not yet built", "proposals never auto-applied", nuanced status prose | detect drift → **route to the EXISTING LLM rewrite flow** (`ddd_refresh`), now SCHEDULED; LLM rewrite auto-applies WITH a **verbatim-source-evidence citation** requirement + .bak + changelog | reversibility (.bak) + evidence-citation gate; no new judge invented |

- **Nothing is "skipped and forgotten"** (fixes Attack 3): FIXABLE → auto-fixed; SEMANTIC →
  auto-routed to the LLM flow every periodic scan. A drift with no citable evidence yet is
  re-checked next scan (the LLM flow re-runs on schedule), not lost.
- **Queue KEPT** (fixes Attack 4): reframed as the `s_persist`/manual surface + the LLM-flow
  landing spot; conversation-source force-escalate (DEC19) preserved. R27 migration, not deletion.
- **Zone-protect** demoted to a U-contribution for FIXABLE; SEMANTIC prose in authoritative zones
  goes through the LLM flow (which already has the Sonnet author), not a blind auto-rewrite.

## 3. DoD (goal-cycle exit criteria)

- **DoD-A (FIXABLE tier — deterministic auto-fix):** a `DriftClaim` extractor + a bounded set of
  deterministic verifiers (version-string vs VERSION file, skill/file counts, decay-number
  constants, grep-provable "X is built" booleans). A failed claim → machine-generated correct
  value → **auto-apply** (.bak + changelog). Unit-tested detect + fix per claim-type.
- **DoD-B (SEMANTIC tier — route to existing LLM flow, NOT a new judge):** semantic-prose drift
  (a claim grep can falsify but not re-author) → routed to the EXISTING `ddd_refresh` LLM rewrite
  flow. That flow now (a) is SCHEDULED (was on-demand-only, unscheduled), (b) auto-applies its
  rewrite WITH a verbatim-source-evidence citation + .bak + changelog. NO new correctness-judge is
  invented; the locate-signal is NOT repurposed as a correctness gate. Conversation-source
  force-escalate (DEC19, `ddd_cultivation.py:974-978`) PRESERVED.
- **DoD-C (queue kept, R27 not deletion):** the proposal queue + its API (`routers/cultivation.py`)
  + UI (`EvalDashboard.tsx`) are KEPT and reframed as the `s_persist`/manual surface + the LLM-flow
  landing spot. Verify all three consumers still function. No stranded consumers.
- **DoD-D (self-heal the current drift):** run the pipeline on SwarmAI's OWN DDD. FIXABLE now
  (version v1.21→live, "never auto-applied" lie at TECH.md:817, decay numbers 60/150 + dead
  ref-grace at `ddd_entry_lifecycle.py:11-12`, skill count, hook-count contradiction, volatile
  counts per R30#4). SEMANTIC via the LLM flow (code-intel status, multi-package, recall-vs-
  knowledge-store nuance — RE-VERIFY :329 is actually wrong before touching; Gate-0 showed it's a
  false positive for the Knowledge store). DDD ends ALIGNED.
- **DoD-E (self-healing loop, PERIODIC — the命脉):** the FIXABLE scanner + the SEMANTIC LLM flow
  are BOTH wired to a real SCHEDULED job AND `context_health` deep-check — automatic, no human
  trigger. Proof: a seeded FIXABLE drift (flip the version string) is DETECTED + auto-repaired on
  the next scan with zero human action. A failing test that seeds-and-verifies proves the loop.
- **DoD-F (docs-truth):** this design doc + TECH.md cultivation section describe the SHIPPED model
  (two auto tiers + preserved manual/LLM queue); codebase + SwarmWS mirror synced.

## 4. Non-goals / guardrails
- NOT deleting zone-protect wholesale — demoting it to a U-signal. SELF.md stays hard-human.
- NOT LLM-vibes uncertainty — U is computed from measurable signals (margin/coverage/evidence/blast).
- NOT a new store — reuse `ddd_cultivation` + `ddd_entry_lifecycle` + `ddd_refresh` engines.
- Autonomy must stay REVERSIBLE (archive+.bak) — autonomous ≠ unrecoverable.
- Caps stay as disaster-floors (P6): auto-retire/rewrite per-run caps prevent a runaway scan
  from mass-mutating on one bad signal.

---

## 4. Semantic-drift tier — Gate-0 RESHAPE (run_b2e85d61, XG chose 甲, 2026-07-18)

**Gate-0 killed the naive "LLM auto-rewrites trunk + auto-commits" version — and was RIGHT.**
Live-verified, the fatal holes:
1. **Two disjoint pipelines.** The gates I claimed (CitationVerifier + classify_confidence)
   live on `LlmRefreshProposer`→`_apply_llm_proposal`. But the pipeline that actually
   auto-`git commit`s is `ddd_refresh.py`→`ddd-refresh-*.md`→`_auto_apply_ddd_proposals`,
   gated ONLY by an LLM self-reported `"confidence": 8` (ddd_refresh.py:286) — the prompt
   never asks for a citation. The safety story was on the wrong path.
2. **CitationVerifier is existence-theater** (auto_refresh.py:111-140): it proves "line 151
   exists" / "string appears somewhere", NOT "the sentence about line 151 is TRUE". It cannot
   bind a claim to its truth.
3. **`is_mechanical` only auto-applies APPEND-shape** (ddd_orchestrator.py:530) — a trunk
   REWRITE is currently BLOCKED. Shipping auto-rewrite would require DELETING the last real guard.

**The irreducible truth (why this is a cognition decision, not an engineering one):**
grep cannot verify whether a sentence about the system is true. Version-string is machine-
provable (read VERSION); "is code-intel v2 built" / "is this architecture description accurate"
is NOT. So fully-autonomous LLM-rewrite-of-trunk-with-auto-commit is **CLASS A automated** —
"what I wrote = correct" turned into a cron job. Refused.

### 甲 — the shipped shape
- **NEW = a semantic-drift DETECTOR only.** It emits `{claim, location, falsifying_evidence}`
  for a trunk claim a cheap grep/symbol-check falsifies, **independent of file mtime** (closes
  the real gap: a factually-wrong claim in a freshly-touched file was never flagged — the
  2-month rot). It NEVER writes a DDD doc.
- **The fix is human, via `s_persist`.** Findings surface WHERE I work (in-band, in the active
  channel / session — NEVER a passive dashboard/queue that rots unread; AGENT signal-in-channel
  rule + "靠人不靠谱" applies to CHORES, not to the one irreducible judgment: is this
  un-verifiable prose true?). I one-shot the correction with the evidence already in hand.
- **Deterministic tier (version-stamp) stays fully auto** (run_254f5e52, shipped).
- **Detect-only ⇒ 100% safe:** a wrong detection wastes a look; it can never corrupt trunk.

### Explicitly REFUSED / deferred
- **丙 (full-auto rewrite + auto-commit): REFUSED as a direction** — un-verifiable prose truth
  has no real gate; this is CLASS A wearing a self-healing costume.
- **乙 (LLM rewrite → PR I approve): a valid FUTURE step, its OWN run** — needs the two pipelines
  unified + a real claim-binding citation-verify (not existence-theater) built FIRST. Not
  smuggled into this run (C042: don't build the mechanism until it's earned).

### DoD (甲 — reshaped)
- **DoD-A:** a semantic-drift detector — extracts checkable trunk claims (status booleans like
  "not yet built", named-mechanism claims) + flags ones a grep/symbol-check falsifies,
  INDEPENDENT of mtime. Pure function → `{claim, location, evidence}`. WRITES NOTHING.
- **DoD-B:** findings surfaced in-band where the agent works (session-visible, evidence attached,
  a ready `s_persist` action) — NOT a passive panel. Verify it does NOT route to any auto-write.
- **DoD-C:** detector is periodic — wired to context_health deep-check (already daily) so drift
  is caught continuously, not on-demand-only.
- **DoD-D:** proven on the REAL current drifts (e.g. a seeded "X not yet built" in a fresh file
  → detected with the falsifying evidence). Test asserts detect + evidence, and asserts NO file
  mutation occurs.
- **DoD-E:** docs-truth — design doc + TECH.md describe the two-tier reality (deterministic auto
  + semantic detect-only-→-s_persist) and the explicit refusal of full-auto trunk rewrite.

---

## 5. Semantic-drift DETECTOR — NO-GO (run_b2e85d61 Gate-1, 2026-07-18)

Even the SAFE detect-only reshape (甲) was BLOCKED by Gate-1 and, on verification, RIGHTLY
abandoned. Two adversarial gates converged on ONE truth:

**"grep cannot verify whether a sentence about the system is true" applies to DETECTION,
not only to rewriting.** A detector that flags "X not yet built" when symbol X exists in
source false-positives on real trunk — verified live on our OWN TECH.md:
- `:1509` "PURE-FILESYSTEM… NO vector leg" is a TRUE statement, but inert `vector_*` symbols
  still exist → the rule flags a CORRECT sentence.
- `:817` is a narrated CORRECTION that still contains the old phrase → flagged.
- `:26` "ablation NOT yet built" is a deliberate NON-GOAL → flagged.
A present symbol does not prove a prose claim false (it may be inert, a non-goal, or
true-when-written). And AC5's "writes nothing" was itself false: `_get_health_highlights`
(proactive_intelligence.py:1330) auto-creates a Radar todo for `critical` findings.

**Decision: DO NOT BUILD a semantic-drift detector.** Of the 5 real audit drifts the safe
rule targeted ~1 and misfired on the 2 nearest — the honest version is either noise or
near-empty. Mechanizing this judgment at acceptable false-positive cost is not possible.

### What actually closes the semantic-drift gap (the real, shipped answer)
- **Deterministic drift** (version-stamp) → **fully auto** (run_254f5e52, shipped).
- **Semantic drift** → the **periodic full DDD self-audit** — the exact 4-subagent
  fresh-context audit (recall/cultivation/code-intel/completeness vs live code) that STARTED
  this whole effort. An LLM reading code + prose and judging "is this true" IS the only thing
  that verifies prose-truth; that is a periodic *review*, not a mechanized detector. The human
  (or a scheduled review agent) runs it and fixes findings via `s_persist`.
- **The lesson**: not everything decays into a mechanizable check. Prose-truth is one of them.
  The right response to that is a periodic judgment pass, not a grep dressed as a detector.
