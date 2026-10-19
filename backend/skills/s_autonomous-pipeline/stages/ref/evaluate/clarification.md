# EVALUATE ref — Requirement Clarification, Ambiguity Re-Scan, Exit Routing
Read when: the requirement has an ambiguity or a constraint conflict, you take any escalation
exit, or you need the `ambiguity_scan` shape. The card (`stages/evaluate.md` step 1) has the
core procedure.
## Requirement Clarification Check (P0)
Run **before scoring**. A vague requirement scored as GO produces under-specified ACs: the
pipeline builds something that technically passes but misses the real need.
1. Parse the requirement into WHO (actor), WHAT (action), WHY (value), WHEN (trigger).
2. For each undefined element:
   - Can it be unambiguously derived from DDD docs (PRODUCT.md scope, TECH.md constraints)?
   - Yes → fill it in and note the derivation source.
   - No → flag it as an ambiguity.
3. List edge cases the requirement does not address (empty state, error path, concurrent
   access, scale).
4. Cross-reference TECH.md "Constraints" / "Runtime Traps": does the requirement implicitly
   violate any? If so → flag a conflict.
**Exit conditions:**
- 0 ambiguities, 0 conflicts → proceed to scoring.
- 1 ambiguity, derivable from context → resolve inline, note the assumption in the artifact.
- ≥2 unresolvable ambiguities → **CLARIFICATION-ASSUME** (below; NOT a terminal verdict).
- Any constraint conflict → **CLARIFICATION-ASSUME** with an explicit conflict description.
## CLARIFICATION-ASSUME: the run never waits for an answer (autonomous)
The pipeline is fully autonomous: it never asks the user and never pauses for an answer
(2026-10-19; it used to MCQ in a desktop tab and checkpoint headless runs, which parked them).
- The requirement is WORTH doing and only has an ambiguity → answer it yourself:
  1. Pick the **most conservative reading**: the one that changes the least behaviour, keeps
     existing contracts, and is easiest to reverse (a flag, an additive change).
  2. Record it in `clarifications[]` (`answered_by: self`, `answer` = the reading picked, the
     alternatives in the question), keep the requirement at `status: assumed`, and add one
     `attention_flags` line in DELIVER: "[assumption] Q… → chose …; other reading …".
  3. Continue the SAME run. The assumption is visible in REPORT §10 and the user can
     override it after the commit (push stays theirs).
- A constraint conflict → keep the stricter constraint, record the conflict the same way.
- Only when EVERY reading is unsafe or unbuildable is it a value verdict: DEFER (the run ends,
  reason logged). Never a checkpoint, never `AskUserQuestion`.
## Self-Socratic Ambiguity Re-Scan (after filling the clarification output)
`INTERROGATE THE SPEC AND YOUR OWN FRAMING — NOT THE USER`
After filling WHO/WHAT/WHY/WHEN and noting assumptions, **re-scan THAT output** (and the ACs
you are about to write) for residual ambiguity. One self-answer round: not a loop, not a user
interview. In an autonomous pipeline the Socratic method interrogates the requirement and your
own framing; it is the Understanding Gate's "refute your claim" discipline shifted left to the
clarification layer. We self-answer (read code / DDD) and escalate only what is genuinely
unknowable, which is why this step is mechanical and validator-enforced.
**Process (ONE round):**
1. Scan the filled clarification fields for ambiguity/hedge terms: `depends`, `maybe`,
   `not sure`, `mix of`, `somewhere between`, `standard`, `typical`, plus CJK
   `看情况 / 可能 / 大概 / 差不多 / 视情况 / 标准做法 / 一般`.
   (Canonical list: `pipeline_validator._AMBIGUITY_TERMS`, the single source of truth.)
2. For each hit, force it concrete with a **self-answer** (read the code / DDD docs to pin the
   exact meaning). Escalate to the user ONLY if the answer is genuinely user intent that
   cannot be derived. Self-answer is the default.
3. Record the scan in the evaluation artifact.
**Output (`ambiguity_scan` field):**
```json
{
  "ambiguity_scan": {
    "scanned_fields": ["who", "what", "why", "when", "acceptance_criteria"],
    "terms_checked": ["depends", "maybe", "standard", "typical", "可能", "..."],
    "hits": [
      {"term": "standard", "where": "what — 'standard retry'",
       "resolution": "self-answer: read retry_manager.py:40 — means exponential backoff w/ --resume, NOT a generic 'standard'.",
       "kind": "self-answer"}
    ],
    "hit_count": 1,
    "all_resolved": true
  }
}
```
**Rules `[GATE·validator]` (`pipeline_validator._check_ambiguity_scan`, publish-time BLOCK):** <!-- R-evaluate-001 -->
- Strict profiles (full/bugfix/goal) REQUIRE the `ambiguity_scan` block; **trivial/docs/research
  are exempt** (anti-ceremony, the same rigor tiers as the Understanding Gate).
- **Every hit MUST carry a non-empty `resolution`** (self-answer OR escalation reason,
  ≥12 chars). An unresolved hit BLOCKS: that is what proves the loop RAN.
- `hits: []` is valid (scanned, found nothing). Record it anyway: it proves the scan ran, not
  that it was skipped.
- This is DISTINCT from the Acceptance Criteria Quality Gate (catches weak ACs) and the
  Understanding Gate (scans the *diagnosis* for hedge). Here you scan the *requirement
  clarification output* for residual spec ambiguity.
## Exit Routing
- **DEFER or REJECT**: the pipeline ends. Log the reason and exit. A terminal scoring verdict
  (the requirement should not proceed), NOT a clarification gap; never routed in-band.
- **ESCALATE** (legacy verdict): treat as DEFER with the reason logged; the pipeline does not
  hand a run to a human.
- **CLARIFICATION-ASSUME** (the ≥2-ambiguity / constraint-conflict exit): **NOT terminal.**
  Self-answer with the most conservative reading (above), record it, continue the SAME run.
