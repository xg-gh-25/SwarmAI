# EVALUATE ref — Acceptance Criteria Quality, Pre-mortem, Working-Backwards
Read when: writing the acceptance criteria, the verdict is GO (pre-mortem), or
`understanding.work_type == "greenfield"`. The card (`stages/evaluate.md` step 3) has the core
procedure.
### Acceptance Criteria Quality Gate `[MUST]` <!-- R-evaluate-009 -->
> The *presence* of `acceptance_criteria` is `[GATE·validator]` (required in the PLAN <!-- R-evaluate-010 -->
> artifact). The three quality FILTERS below (no-op / user-value / garbage-in) are
> `[MUST]` — agent discipline, not code-checked.
**Every AC must describe an observable outcome, not a mechanism.** ALL three filters must pass
for every AC:
- **Filter 1: No-op test.** "If I implement a no-op that produces the named artifact (file,
  output, endpoint) but delivers zero user value, does this AC still pass?" Yes → the AC is too
  weak.
- **Filter 2: User-value test.** "Would a real user pay $5 for this AC being true?" "They'd
  expect that for free / they wouldn't notice" → the AC measures an implementation detail, not
  value delivery.
- **Filter 3: Garbage-in test.** "Could this AC pass with trivially wrong content?" If the
  output is a document/report/analysis, can it pass by being structurally correct but
  factually empty? ACs that FAIL this filter:
  - "Produces TECH.md file" ← passes with an empty template
  - "code-intel.json has valid schema" ← passes with fabricated module names
  - "AGENTS.md is ≤150 lines" ← passes with Lorem Ipsum
**Fix: add a QUALITY qualifier to every content-producing AC:**
- "TECH.md conventions each cite 2+ source files where the pattern was observed"
- "code-intel.json edges verified against actual import statements (edge count > 0)"
- "AGENTS.md Critical Rules are backed by code evidence (not README paraphrase)"
| ❌ Mechanism/Existence AC | ✅ Outcome + Quality AC |
|---|---|
| "Save .full_data.json to disk" | "Re-render with insights completes in <2s without network calls" |
| "Produces TECH.md" | "TECH.md conventions cite 2+ source files each; not derivable from README alone" |
| "code-intel.json valid schema" | "code-intel.json modules match actual directory structure; edges from verified imports" |
| "Filter incomplete month" | "No MTD partial data appears in insights_data.json monthly_trend" |
| "Works on external repo" | "Output contains at least 3 facts discoverable ONLY by reading source code" |
**Rules:**
- Each AC must be verifiable by a command, assertion, or observation, not by reading code.
- "Does X exist?" is never sufficient; "Does X achieve Y?" is required.
- AC about a cache/optimization: measure the speedup, not the cache existence.
- AC about data quality: measure the output quality, not the query change.
- **AC about generated content: measure content quality, not just structure.**
- **At least 1 AC per feature must be a "user would notice" criterion**: something that fails
  if the output is trivially wrong.
### Pre-mortem Gate `[GATE·validator]` (greenfield) / `[MUST]` (else) <!-- R-evaluate-011 -->
> `pre_mortem` is code-required (`_check_working_backwards`) ONLY when <!-- R-evaluate-012 -->
> `work_type=greenfield` + strict profile. For every other work_type it is `[MUST]`
> (produced by discipline, not validator-blocked).
After scoring, if the initial recommendation is GO, the base methodology's Step 3.5
(Pre-mortem, `backend/skills/s_evaluate/SKILL.md`) is **mandatory** in the pipeline. The
`pre_mortem` array MUST be included in the evaluation artifact JSON. If the pre-mortem
triggers a score adjustment or escalation, update the artifact accordingly before publishing.
### Working-Backwards Lens (GREENFIELD ONLY) `[GATE·validator]` <!-- R-evaluate-013 -->
`INTERROGATE THE CUSTOMER VALUE — SELF-ANSWER, RECORDED FOR REPORT §10`
**Trigger:** ONLY when `understanding.work_type == "greenfield"` AND the profile is strict
(full/goal, not trivial/docs/research). For every other work type (bugfix, existing-feature,
refactor, research, docs) skip it. Customer/value framing is only meaningful for a NET-NEW
feature; that is the deliberate difference from the always-on Understanding Gate and Ambiguity
Scan.
The Understanding Gate already captures *the problem and who has it* (greenfield row) and the
Pre-mortem Gate the *top-3 failure reasons*, so this lens does not re-ask those. It adds the
**economic / value framing** neither captures: you self-answer (Intelligent-Default); the
answers are recorded and shown in REPORT §10. Never a user interview.
**Process (Intelligent-Default — self-answer each, do NOT block on the user):** <!-- R-evaluate-014 -->
For each field below, write your **best-guess answer derived from PRODUCT.md / DDD /
the requirement**. They are *taste decisions* (recorded, overridable after the commit),
NOT a `⛔` blocking gate.
| Field | Question | Enforced? | Self-answer from |
|-------|----------|-----------|------------------|
| `target_customer` | Who specifically has this problem? (a precise segment, not "users") | context only¹ | PRODUCT.md Target Users / Audience Map |
| `current_workaround` | How do they solve / work around it TODAY? | ✅ required | the requirement + domain knowledge |
| `why_better` | Why is this faster / cheaper / better than that workaround? | ✅ required | PRODUCT.md differentiation |
| `must_be_true` | What adoption assumption MUST hold for this to succeed? | ✅ required | pre-mortem inversions |
¹ `target_customer` is **not validator-enforced**: the "who has it" is already captured by the
greenfield Understanding row. Record it for framing context; the gate enforces only the 3
economic fields the other gates don't capture.
The **top-3 failure reasons** are NOT re-asked here; they are **reused** from the mandatory
`pre_mortem` array (Pre-mortem Gate above). This gate also makes `pre_mortem` code-enforced for
greenfield (required non-empty when work_type=greenfield).
**Output (`working_backwards` field, plus the existing `pre_mortem` array):**
```json
{
  "working_backwards": {
    "target_customer": "solo technical founders who want team-scale output without hiring",
    "current_workaround": "juggling ChatGPT tabs + manual copy-paste, losing context each switch",
    "why_better": "persistent memory + autonomous pipeline = 10x less context re-establishment",
    "must_be_true": "users must trust an autonomous agent to act without per-step confirmation"
  }
}
```
**Validator-enforced (`_check_working_backwards`):** greenfield + strict → the block is
required, each of the 3 economic fields (`current_workaround`, `why_better`, `must_be_true`) must be a real answer (≥12 chars), and `pre_mortem` must be
non-empty. Every other work_type / relaxed profile → not required, never false-blocked. Distinct
`Working-Backwards:` error tag (no overlap with the Understanding / Ambiguity / REPRO gates).
Fail-open on a missing/typo'd work_type: this is a framing-quality lens, not a safety gate.
