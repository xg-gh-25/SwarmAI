# REFLECT — goal profile (two-tier REFLECT)
Read this only when the run's profile is `goal`. The card (`stages/reflect.md`) points here
before its step 4. For every other profile, skip this file.
## Tier 1: mini-reflect (per cycle, NOT run here)
Handled in `stages/goal_cycle.md` Step 9. Each cycle appends a one-line insight to the
progress file. No DDD writes. It accumulates raw material for the full REFLECT below.
## Tier 2: full REFLECT (at goal completion, runs HERE)
Triggered when goal_cycle exits with SUCCESS (all DoD met + adversarial clean).
1. **Read all mini-reflects** from the progress file's "Cycle Log" section.
2. **Read the goal requirement** and the DoD criteria from the evaluation artifact.
3. **Distill patterns:**
   - Which DoD criteria were hardest, and why?
   - Which cycle actions had the highest leverage (most progress per cycle)?
   - Any recurring blockers across cycles?
   - What was the velocity curve (accelerating, decelerating, flat)?
4. **Write to IMPROVEMENT.md** (recipe: `ref/reflect/writes.md`):
   - a "What Worked" entry: the effective patterns from this goal;
   - a "What Failed" entry, if cycles stalled: the anti-patterns;
   - goal completion metadata: total cycles, wall time, criteria count.
5. **Update PROJECT.md:** goal completed, date, cycles taken, key insight.
6. **DDD cultivation**: run it normally on the distilled lessons (card step 9,
   `run-cultivate`).
Then continue with the card's standard methodology from step 4: checklist maintenance →
ADR gate → dead code → learn → record (Exit) → cultivation → report → COMPLETE.
The distilled patterns become the reflect record's `lessons` at the Exit, each with its
`[type]` prefix (see `ref/reflect/lessons.md`).
