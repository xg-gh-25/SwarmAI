# THINK — history and rationale

Not read at runtime. The execution card is `stages/think.md`; conditional detail is in
`stages/ref/think/`. This file keeps the "why" behind THINK's rules, for humans and for the
evolution pipeline.

## Why the Design Risk Probe is self-answering

It replaced an older "grill protocol" that asked the user questions about each approach. In
practice that protocol was almost always skipped. The probe keeps the useful part (stress-test
the riskiest assumptions before committing) and moves the work onto the agent: identify the
risks, then resolve them by reading code or DDD docs. The interactive grill survives only as a
rare escalation (>50% unresolved on a high-stakes choice).

## Why constraints replaced Minimal/Ideal/Creative

Generic labels produced three variations of the same design. An explicit constraint (SPEED,
QUALITY, SIMPLICITY, FLEXIBILITY, DELETION) forces genuinely different designs. The labels are
kept only as a fallback for pure research or docs-only work, where constraints don't fit.

## Why the minimum-depth gate exists (Meta-Intelligence L3)

THINK must produce substantive analysis, not a token-saving shortcut. Historical run data
showed that runs with deeper THINK (>10K tokens) had higher completion rates and fewer
adversarial findings. The `think_depth` telemetry event (alternatives, probes, self-resolved,
escalated) exists so this correlation keeps being measured.

## Why the self-Socratic re-scan exists

Probe `verification` text and recommendations tend to hide hedges ("standard pattern",
"depends") that later surface as rework in BUILD. One forced self-answer round turns each
hedge into a named file or pattern before PLAN consumes it. It mirrors EVALUATE's ambiguity
scan so both ends of the design phase use one term list (`_AMBIGUITY_TERMS`).

## Common rationalizations

| Rationalization | Reality |
|---|---|
| "I already know the best approach, skip alternatives" | Skipping alternatives means skipping the constraint that FORCES different designs. The "obvious" approach is often SPEED-optimized when the problem needs QUALITY or DELETION. One constraint you didn't consider = one class of failure you didn't prevent. |
| "Research wastes tokens, let me just build" | Past runs skipped research, read descriptions instead of code, and shipped CSS from instructional text without rendering examples. "Understanding > Delivery": building without understanding = building garbage. |
| "Only one approach exists — this is mechanical" | If truly mechanical, the evaluation scope should be "trivial" and the profile should be "trivial" (which skips THINK). If you're in THINK, the evaluation already determined multiple approaches are possible. Trust the profile. |
| "I'll research as I build" | Research in BUILD = discovery mid-construction = rework. Research is cheap (read + think). Rework is expensive (undo + redo + retest). Separate phases exist to separate costs. |

## Exit path before the split

THINK used to end with two calls: `artifact_cli.py publish --type research ... --stage think`
followed by `artifact_cli.py advance --state plan`, plus the full continuation-contract
paragraph. The card now uses `stage-start` / `stage-complete` (design
`docs/design/pipeline-speed-systematic-fix.md` §4-A); the old commands still work and are
documented in INSTRUCTIONS.md §3c. The long-form continuation contract also lives in
INSTRUCTIONS.md §3f; the card keeps a condensed version.
