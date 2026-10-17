# GOAL_CYCLE ref — periodic REVIEW, cross-cycle re-judgment, progress file
Read when the periodic REVIEW fires (`cycle_number % review_cadence == 0`), in the Final
Quality Gate step 0, and when creating or updating the progress file.
## Periodic REVIEW Gate (card step 9)
```
if cycle_number % review_cadence == 0:
    git_diff = git diff <last_review_commit>..HEAD
    → run REVIEW stage behavior on this diff
    → if findings: append EACH to the Findings Ledger (shape below) with a new ID,
      Cycle Found, severity, confidence, status=APPLICABLE, file:line.
      Do NOT dump them into free-text Blockers: a plain-text note cannot be
      re-judged across cycles. The ledger is the tracked home.
    → set last_review_commit = HEAD
    → then run the re-judgment below (same cadence)
```
## 10.5 Cross-Cycle Finding Re-Judgment(AutoSDE #2) `[MUST]` → `[GATE·validator]` at DELIVER <!-- R-goal_cycle-003 -->
> The re-judgment loop itself is `[MUST]` (agent re-checks the ledger). Its TEETH are at
> DELIVER: an APPLICABLE-unresolved finding carried into `adversarial_review.findings[]`
> is code-blocked by `_blocked_findings` (pipeline_validator.py). The ledger is working
> memory (`[MUST]`); the DELIVER artifact is the enforcement surface (`[GATE·validator]`). <!-- R-goal_cycle-004 -->
`A FINDING IS NOT DONE UNTIL RE-JUDGED AGAINST THE LATEST DIFF`
**Runs every cycle that the Periodic REVIEW gate fires (same cadence), and once more in
the Final Quality Gate.** It catches "fixed in cycle 3, regressed in cycle 7". A fresh
adversarial on the total diff does NOT catch this: it has no memory that a finding was
ever raised and marked resolved, so a silent regression reads as clean. Re-judgment
does, because it re-checks each KNOWN finding.
**Process (for each ledger finding NOT already OBSOLETE):**
```
current_diff = git diff <start_commit>..HEAD
for finding in Findings Ledger where status != OBSOLETE:
    re-judge finding against current_diff + current file state:
      - the code it flags still exists AND still exhibits the problem
            → status = APPLICABLE   (even if it was RESOLVED in an earlier cycle —
                                     this is the regression catch)
      - the problem is verifiably fixed in the current code
            → status = RESOLVED (record cycle)
      - the code it referenced no longer exists at all
            → status = OBSOLETE
    write the updated status + re-judged cycle back to the ledger row
```
**Rule — this must have TEETH (Gate-1 finding):** re-judgment that only updates a
markdown table changes nothing. Every finding that ends this step as **APPLICABLE and
unresolved** MUST be carried forward into the DELIVER stage's
`adversarial_review.findings[]`, verbatim, with its `severity` + `confidence`, so the
finding-level confidence gate (`_blocked_findings`, pipeline_validator.py) blocks
completion on it exactly as if a fresh adversarial had just raised it. Writing it only
to the progress file is a no-op: the progress file never reaches `_blocked_findings`.
The ledger is the working memory; the DELIVER artifact is the enforcement surface.
## Progress File Format
Scheduled mode (`ref/goal_cycle/scheduled.md`) has no other state carrier, so write it
clearly enough that a fresh agent can resume from it alone.
```markdown
# Goal: <goal description>
## Definition of Done
- [x] <criterion 1> (met cycle N)
- [ ] <criterion 2> (current: <value>, target: <value>)
## Configuration
- Max cycles: <N>
- Review cadence: every <N> cycles
- Cycle scope: <description>
- Start commit: <hash>
- Last review commit: <hash>
## Metrics
| Cycle | Date | Metric | Delta | Action |
|-------|------|--------|-------|--------|
| 1 | 2026-05-14 | 73% → 76% | +3% | Added tests for memory.py |
## Current State
- Next target: <what to work on>
- Lowest-hanging fruit: <specific file/module>
## Blockers
(none, or list of blocked items with reasons)
## Findings Ledger
| ID | Cycle Found | Severity | Confidence | Status | File:Line | Finding |
|----|-------------|----------|------------|--------|-----------|---------|
| F1 | 2 | MEDIUM | 8 | RESOLVED (cycle 4) | foo.py:12 | missing null guard |
| F2 | 3 | HIGH | - | APPLICABLE | bar.py:88 | race on shared dict |
## Cycle Log
**Cycle 1:** <insight>
```
## Ledger statuses and per-cycle rows
Ledger status values: **APPLICABLE** (still reproduces against the current diff) |
**RESOLVED** (verified fixed against the current diff) | **OBSOLETE** (the code it
referenced no longer exists). Every REVIEW/adversarial finding from ANY cycle lives in
the ledger, with severity + confidence carried so it can be handed to the DELIVER
confidence gate verbatim. Only APPLICABLE + unresolved rows are carried into the DELIVER
`adversarial_review.findings[]`.
Per-cycle rows the card writes:
- Metrics row (card step 7): `| N | YYYY-MM-DD | <metric_before> → <metric_after> | +<delta> | <action taken> |`,
  then update "Current State" with the new targets.
- Mini-reflect (card step 7): ONE line in Cycle Log,
  `**Cycle N:** <what worked or didn't> → <insight for next cycle>`. No DDD write, no
  LLM call for distillation, just text. The goal-profile REFLECT reads these lines
  (`stages/ref/reflect/goal-mode.md`).
