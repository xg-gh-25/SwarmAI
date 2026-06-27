# COMPLETE Stage — Output Format Spec

Pipeline-owned stage (no sibling skill). This is the **terminal output** of every
pipeline run — the completion summary box + executive summary the user sees in chat.

> **Why this is a fresh-read stage doc (read it AT Step 6, not from memory):**
> Every other stage (evaluate…reflect) reads its `stages/<stage>.md` at the moment
> it executes, so the spec is fresh in the reading path at the decision point.
> COMPLETE used to be the ONE exception — its format lived ~750 lines deep inside
> INSTRUCTIONS.md, far past the file-head execution entry, so by the time a run
> reached COMPLETE the spec had decayed out of the attention window (F004) and the
> summary box silently "disappeared." This doc fixes that: Step 6 reads it fresh,
> exactly like the other stages.

This doc owns the format ONLY. The COMPLETE mechanics (run-report, run-update
`--status completed`, the mechanical completion gate, and the final STOP) stay in
INSTRUCTIONS.md Step 6 — read both.

---

## Output the completion summary to chat (MANDATORY — never skip)

Pick the variant matching the run's profile.

**full / standard:**
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ Pipeline COMPLETE — run_<id>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

<2-3 sentence TL;DR: what was built, what problem it solves>

Profile: <profile> | Stages: <N>/10 completed
Commit: <git hash> | Files: <N> changed, +<A>/-<D> lines

Phase A (Decision):
  ① EVALUATE → GO | ★ Gate 0 → <PASS/BLOCK> (diagnose-before-build, in EVALUATE)
  ② THINK → <approach> | ③ PLAN → <N> AC
  ④ ★ Gate 1 → <PASS/WARN/BLOCK>

Phase B (Execution):
  ⑤ BUILD → <N>R→<N>G, <N> tests | ⑥ REVIEW → <N> findings
  ⑦ TEST → <N> passed, 0 failed

Phase C (Delivery):
  ⑧ ★ Gate 2 → <N> findings, <M> fixed | convergence: <iter>/3
  ⑨ DELIVER → 6L pass, push-ready
  ⑩ REFLECT → <N> lessons → IMPROVEMENT.md

Report: .artifacts/runs/<run_id>/REPORT.md
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

For **goal profile**, use this variant:
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ Pipeline COMPLETE — run_<id>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

<2-3 sentence TL;DR>

Profile: goal | DoD: <X>/<Y> met in <N> cycles

Phase A (Decision):
  ① EVALUATE → GO | ★ Gate 0 → <PASS/BLOCK> (diagnose-before-build, in EVALUATE)
  ② THINK → <approach> | ③ PLAN → DoD defined
  ④ ★ Gate 1 → <verdict>

Phase B (Execution — <N> cycles):
  ⑤⑦ BUILD+TEST × <N> cycles | ⑥ REVIEW (periodic, <M> times)

Phase C (Delivery):
  ⑧ ★ Gate 2 (on total diff) → <N> findings, all fixed
  ⑨ DELIVER → 6L pass, push-ready
  ⑩ REFLECT → <N> lessons (aggregated from mini-reflects)

Commit: <git hash> | Files: <N> changed
Report: .artifacts/runs/<run_id>/REPORT.md
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

For **trivial/bugfix** (compact — no phase headers in body):
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ Pipeline COMPLETE — run_<id>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

<1-2 sentence TL;DR>

A: ①GO ★G0<verdict> ③<N>AC ④★<verdict> | B: ⑤<N>R<N>G ⑥<findings> ⑦<pass> | C: ⑧★<findings> ⑨push-ready ⑩<N>lessons
Commit: <hash> | Files: <N> changed
Report: .artifacts/runs/<run_id>/REPORT.md
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## Output executive summary (MANDATORY — immediately after the stage box)

The stage box shows process. The executive summary shows **outcome**.
Without it, the user has to ask "so what changed?" every time.

```
## Executive Summary

**Before → After:**
| Aspect | Before | After |
|--------|--------|-------|
| <key dimension 1> | <old state> | <new state> |
| <key dimension 2> | <old state> | <new state> |

**Key Decisions:**
- <decision 1 — what was chosen and why (1 line)>
- <decision 2>

**Lessons Learned:**
- <lesson 1 — reusable insight (1 line)>
- <lesson 2>

**Next Steps:**
- <suggested action 1 — what to do next>
- <suggested action 2>
- <suggested action 3>
```

**Rules for Executive Summary:**
- Before→After table: 2-4 rows, each showing a measurable change. No "N/A→implemented" filler.
- Key Decisions: only taste/judgment decisions (not mechanical). Max 3.
- Lessons: insights that apply beyond this specific task. Max 3.
- Next Steps: actionable prompts the user could type next. Always include 2-3.
- Total length: 10-20 lines. Not a report — a briefing.

**Skip Executive Summary ONLY for trivial/bugfix profiles** (they're too small to have
meaningful before/after or lessons).
