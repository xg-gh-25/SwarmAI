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
- ≥2 unresolvable ambiguities → **CLARIFICATION-ESCALATE** (resumable MCQ, see below; NOT a
  terminal verdict).
- Any constraint conflict → **CLARIFICATION-ESCALATE** with an explicit conflict description.
## CLARIFICATION-ESCALATE is not the terminal ESCALATE (never conflate them)
EVALUATE has two escalations and they route differently.
- **CLARIFICATION-ESCALATE** (the exit above). The requirement is WORTH doing; it only has an
  unanswered ambiguity. The run is NOT dead:
  1. Publish the EVALUATE artifact with the ambiguous requirement at
     `status: needs-clarification` plus a pending entry in `clarifications[]`.
  2. Route through the Escalation Routing Protocol's **in-band MCQ** path: the live
     `AskUserQuestion` substrate, each question with A–E options plus a forced
     `X. Other (please specify)`.
  3. On answer: write it back into `clarifications[]` (`answered_by: user`), upgrade the
     resolved requirement's `source.kind` → `user-stated` AND `status` → `confirmed`, and
     **continue the SAME run at the SAME stage** (no new run, no checkpoint-and-die).
  Self-answer stays the default (recorded with `answered_by: self`); the MCQ fires only on
  the residue that genuinely cannot be self-answered ("能自答就自答,自答不了才问").
- **TERMINAL ESCALATE / DEFER / REJECT** (Exit Routing below + the orchestrator rule
  "DEFER/REJECT at evaluate ends the pipeline"). A scoring verdict that the requirement should
  NOT proceed at all. It ends the run (checkpoint for human review) and is NOT routed in-band.
  **Never** convert a terminal verdict into an MCQ, and never convert a clarification gap into
  a terminal checkpoint when a desktop session could just ask.
### Background / scheduled runs (no interactive channel): current behavior vs Phase 3
The in-band MCQ substrate (`AskUserQuestion` gate + `continue_with_answer`) is **live only for
an interactive session**: `continue_with_answer` needs a live client in `WAITING_INPUT`, so a
torn-down headless run CANNOT be resumed-with-an-answer today.
- **Current behavior:** a headless clarification-escalate CHECKPOINTS (free-text reason) and
  surfaces as a paused-run attention item. The user re-engages an **interactive** session to
  answer, then resumes.
- It NEVER self-guesses to keep going, and NEVER adds a timeout that truncates the wait
  (STEERING #2: a clarification wait is not a hang).
- **TO-BUILD (Phase 3):** a headless carrier that serializes the MCQ into the checkpoint and
  answers on resume. There is no `awaiting-input` checkpoint state yet; do NOT claim one.
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
- **TERMINAL ESCALATE**: L2 BLOCK → checkpoint. Human review is required before the pipeline
  can continue. A scoring verdict that the requirement is too risky or ambiguous at the value
  level to proceed; distinct from the clarification gap below.
- **CLARIFICATION-ESCALATE** (the ≥2-ambiguity / constraint-conflict exit): **NOT terminal.**
  Publish the EVALUATE artifact with the ambiguous requirement at `status: needs-clarification`
  plus a pending `clarifications[]` entry, then route through the Escalation Routing Protocol's
  clarification-escalate path:
  - desktop/direct → in-band MCQ (`AskUserQuestion`, forced `X. Other`) → write back
    `clarifications[]` (`answered_by: user`) + upgrade `source.kind`→`user-stated` +
    `status`→`confirmed` → **continue the SAME run at EVALUATE**. This half is LIVE
    (`AskUserQuestion` gate + `continue_with_answer` on the interactive session).
  - background/headless → checkpoint (free-text reason) + paused-run attention item, pause with
    NO timeout (STEERING #2); the **user re-engages an interactive session to answer, then
    resumes**.
  The run stays alive; only the unanswerable residue reaches the user. The headless
  serialize-MCQ-and-resume-on-answer carrier (an `awaiting-input` checkpoint state) does NOT
  exist yet; it is Phase-3 TO-BUILD.
