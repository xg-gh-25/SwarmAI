# GOAL_CYCLE ref — pre-cycle setup, DoD quality rules, GoalMetrics
Read at stage entry (card "Setup") and whenever you call `gm.track_cycle()`.
## Pre-Cycle Setup (runs once, at stage entry)
1. **Load the evaluation artifact** (stage-start lists it with its id): extract
   `dod_criteria`, `max_cycles`, `progress_path`, `cycle_scope`, `review_cadence`.
2. **Initialize or load the progress file** at `progress_path`:
   - file exists → resume from the last recorded state;
   - file missing → create it with the DoD criteria + an empty metrics table
     (format: `ref/goal_cycle/findings-ledger.md` § Progress File Format).
3. **Record the start commit**: `git rev-parse HEAD`. It is the diff base for the
   periodic REVIEW and the final ADVERSARIAL review.
4. **Set the cycle counter** to the last recorded cycle + 1 (or 1 if fresh).
5. **Initialize GoalMetrics**: create ONE instance and reuse it for the whole stage:
   ```python
   from scripts.goal_metrics import GoalMetrics
   dod_criteria = evaluation_artifact["dod_criteria"]   # from step 1
   gm = GoalMetrics(run_dir=Path(run_dir))
   gm.track_goal_start(dod_criteria=dod_criteria)
   # cross-run velocity for cycle_scope auto-tuning:
   recommended_scope = gm.get_recommended_cycle_scope()
   # if recommended_scope differs from the evaluation's cycle_scope, log it
   ```
   Reuse this `gm` for every `track_cycle()` call and the final
   `track_goal_complete()`. Do NOT re-instantiate it per cycle.
6. **Apply the DoD Quality Rules below** before cycle 1.
## DoD Quality Rules (BLOCKING — enforced at Pre-Cycle Setup) `[MUST]` <!-- R-goal_cycle-001 -->
> "BLOCKING" here = agent discipline at setup (the agent scans `dod_criteria` and adds a
> negative test). No validator enforces the 3 DoD-quality rules — `[MUST]`. <!-- R-goal_cycle-002 -->
The DoD criteria from EVALUATE are the goal profile's primary quality lever: weak DoD =
weak output. The three rules below prevent the most common DoD failure modes. (The
incident behind them: `stages/history/goal_cycle.md`.)
### Rule 1: Mandatory Negative Test
At least ONE DoD criterion must be a **negative test**: it verifies that a failure case
is handled correctly, not just that the happy path works.
```
❌ Weak DoD (all positive):   "script exists", "script runs without error",
                              "output contains expected text"
✅ Strong DoD (has negative): the three above +
                              "script exits 1 (not crash) when target unreachable"  ← NEGATIVE
```
**Enforcement:** at Pre-Cycle Setup, scan `dod_criteria`. If zero criteria test a
failure/error/edge case, the agent MUST add one before starting cycles. Heuristic: the
criterion text contains "fail", "error", "invalid", "not", "without", "missing",
"crash", "timeout", or it tests exit code != 0 on a deliberately broken input.
If the evaluation artifact has no negative test, announce:
```
⚠️ DoD has no negative test. Adding: [auto-generated criterion].
   Override with "skip negative" if intentional.
```
### Rule 2: Cross-Path Coverage
When the deliverable has **multiple input paths** (e.g. `--scope full` vs
`--scope frontend-only`, or `--backend` vs `--frontend` vs `--all`), at least one DoD
criterion must exercise a **non-default path**. Detection heuristic: if the PLAN's
`files_planned` introduces code that branches on a user-supplied flag/arg/scope, require
DoD for ≥2 branches. A path that exists in code but no criterion runs could be
syntactically broken and the DoD would still pass.
### Rule 3: Behavioral Over Existential
Prefer DoD criteria that verify **behavior** over **existence**:
```
❌ Existential: grep -q "function_name" file.py
✅ Behavioral:  python3 file.py --action 2>&1 | grep -q "expected output"
```
Existential criteria pass on dead code; behavioral criteria require the code to run.
A DoD criterion that is itself a test command (pytest/vitest/tsc) runs through the
runner (card step 2), never raw.
## Track Cycle Metrics (card step 8)
After the mini-reflect, record this cycle with the `gm` instance from setup step 5:
```python
gm.track_cycle(
    cycle_num=current_cycle,
    progress_delta=<fraction of DoD newly met this cycle>,  # 0.0-1.0
    files_changed=<count of source files modified>,
    tests_added=<count of new tests written>,
    regression=<True if the regression protocol fired>,
)
```
This feeds velocity auto-tuning: better data here → better `cycle_scope`
recommendations for future goals.
**progress_delta (strict definition):**
```
progress_delta = (criteria_met_after - criteria_met_before) / total_criteria
```
- 1 of 3 criteria flipped this cycle → `1/3 ≈ 0.33`.
- No criterion flipped → `0.0`, even if partial progress occurred.
- Partial progress inside a criterion (e.g. coverage 73% → 76% toward 90%) is logged in
  the progress file text but NOT encoded in `progress_delta`. Velocity stays a clean
  integer-step metric: each delta is a criterion-completion event, not an estimate.
## Record goal completion (every exit)
```python
gm.track_goal_complete(status="<success|checkpoint|stop|revert_limit|budget>",
                       total_cycles=current_cycle, dod_met=<met>, dod_total=<total>)
```
On SUCCESS also write the velocity summary to the progress file:
```python
velocity = gm.get_velocity()
# append: "## Velocity Summary\n- Avg delta/cycle: {velocity['avg_delta_per_cycle']}\n..."
```
Per-exit output templates: `ref/goal_cycle/exits.md`.
