# REVIEW ref — Spec Compliance Gate
Read when: running card step 2, or the spec verdict is BLOCK/WARNING. The gate line and
the spawn rule are on the card.

## What "BLOCKING" means here
> "BLOCKING" here = review DISCIPLINE, not a code gate. `spec_compliance` /
> `spawned_as_subagent` are NOT validator-checked (`grep spec_compliance
> pipeline_validator.py` = 0 hits). `[MUST]` — the fresh-sub-agent spawn is behavioral,
> same class as Gate 1 / the M3 skeptic. (The related `runtime_patterns` + `litmus_gate`
> ARE `[GATE·validator]`.) <!-- R-review-003 -->

`NO FAN-OUT REVIEW WITHOUT SPEC SUB-AGENT FIRST`

After the Litmus Pre-Gate passes (verdict = PASS or BORDERLINE), dispatch a Spec
Compliance sub-agent FIRST. It verifies the implementation matches the PLAN acceptance
criteria — nothing more, nothing less. It runs SERIAL and BLOCKING because: if spec
fails there is no point checking code quality (saves ~45K tokens); the spec reviewer
needs zero context from the quality reviewers; and finding "AC #3 not implemented" is
cheaper than finding "AC #3 has a race condition". <!-- R-review-004 -->

**Why a FRESH sub-agent, not inline self-assessment:** you wrote this code in BUILD; your
BUILD confidence pollutes spec judgment. A fresh sub-agent sees only the diff + ACs.
**Mechanical enforcement:** the `spec_compliance` artifact MUST include
`spawned_as_subagent: true`. Inline self-assessment does NOT count — the Agent tool call
must be verifiable in the conversation. Do NOT put BUILD confidence ratings, progress
commentary or "I think this is correct" in the prompt — only raw diff + raw ACs.

## Process
1. **Extract ACs** from the PLAN artifact (authoritative source):
   `python backend/scripts/artifact_cli.py discover --project <P> --types design_doc --full`
   → `acceptance_criteria`. `run.json` / stage-start copies are for the quick Litmus
   lookup only; the spec gate MUST use artifact_cli for freshness.
2. **Prepare diff context** — the same diff used for Litmus.
3. **Spawn** with the Agent tool, foreground (`run_in_background: false`), first prompt
   line `EXPERT-ID: spec-compliance`. stage-start already wrote its prompt
   (`review_experts[]` `prompt_path` = `evidence/prompts/review-spec-compliance.md`:
   `backend/experts/code/spec-compliance.md` + the PLAN ACs read from the artifact at
   REVIEW entry, which satisfies step 1 + the diff + the `## Project Knowledge` block):
   spawn it with that entry's `spawn` text, unchanged. If the
   PLAN changed inside REVIEW, run stage-start again first. Returns JSON: verdict +
   coverage matrix.
4. This REVIEW spawn does NOT count toward the run's `required_experts` (D7 credits only
   adversarial Gate-2 spawns — PLAN ref `required-experts.md`). If `spec-compliance` is
   declared there, it must ALSO be in the DELIVER Gate-2 batch.

## Route on verdict
| Verdict | Action |
|---------|--------|
| **PASS** | Proceed to Parallel Fan-Out Review. No modifications. |
| **BLOCK** | Do NOT spawn quality sub-agents. Return to BUILD with the specific MISSING/MISUNDERSTOOD ACs as rework instructions. Increment `stages.review.spec_fail_count` (see Retry accounting). |
| **WARNING** | Proceed to fan-out, but inject the EXTRA findings into the merge phase for visibility in the delivery report. |

## Relationship to Litmus HF2
HF2 is a 5-second smoke test (can you name the function/file for >70% of ACs?) that
catches gross incompleteness (>30% unidentifiable). Spec Compliance is the rigorous
per-AC check for gaps that pass HF2 (one missing AC, partial AC, misunderstood AC).
Different precision levels, not duplicates.

## Retry accounting
- Spec BLOCK uses a SEPARATE counter, `stages.review.spec_fail_count` (not shared with
  `litmus_fail_count`). The original text also said "increment `litmus_fail_count`
  (shared counter)" at the BLOCK row; the dedicated-counter rule below is authoritative.
- Max 2 spec BLOCKs per pipeline run before escalation. On escalation the spec findings
  become MANDATORY fix items in the BUILD rework.
- Litmus escalation (3rd `litmus_fail_count`) triggers "full review with all agents";
  spec escalation triggers "mandatory BUILD rework". Independent paths.

## Profile-aware behavior — no profile skips spec review
| Profile | Spec review | Notes |
|---------|-------------|-------|
| full | Always | Standard |
| bugfix | Always | Even bugfixes must match their AC (prevents scope creep) |
| trivial | Always | Trivial still has ACs — verify them |
| goal | Per-cycle | Each goal cycle's mini-BUILD has ACs to verify |
Profile downgrades must not bypass quality gates.

## Artifact recording
Under `"spec_compliance"` in the review artifact:
`{"verdict": "PASS|BLOCK|WARNING", "spawned_as_subagent": true, "coverage_matrix":
[{"ac_number": 1, "ac_description": "...", "status": "IMPLEMENTED|MISSING|MISUNDERSTOOD",
"evidence": "..."}], "extra_work": [], "findings": []}` (write one literal enum value).
