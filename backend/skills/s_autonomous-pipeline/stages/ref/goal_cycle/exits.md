# GOAL_CYCLE ref — regression protocol, stop conditions, exit outputs
Read when a cycle's tests go red, or when the loop ends for any reason other than
SUCCESS (SUCCESS → `ref/goal_cycle/final-gate.md`).
`RUN` = `perl -e 'alarm 110; exec @ARGV' backend/.venv/bin/python backend/scripts/pipeline_test.py --project <P> --run-id <R>`
## Regression Protocol (card step 6 went red)
Act on the runner's `failures[].classification`: `pre-existing` failures are logged in
IMPROVEMENT.md "Known Issues" and do not trigger this protocol; `regression` / `new` do.
`timeout` (the runner's global deadline killed the test before it finished) is NOT a
code failure and never triggers this protocol: no fix, no revert. Re-run the same
command unchanged — it re-runs only the unfinished tests. The same `timeout` set four
rounds running trips the runner's circuit breaker: stop, don't keep re-running; exit UNMET.
```
Attempt 1:
  - Diagnose: which test fails and why? (timeouts carry their stack; no manual ps/kill)
  - Fix: scoped to the failing test (≤3 file changes)
  - RUN --mode recheck
    → Pass: continue to card step 7 (progress)
    → Fail: Attempt 2
Attempt 2:
  - Different fix approach
  - RUN --mode recheck
    → Pass: continue to card step 7
    → Fail: REVERT cycle N source code changes only
      - git checkout -- <source files changed this cycle>
      - Do NOT revert the progress file (it lives in .artifacts/, it tracks state)
      - Mark the step "blocked by [test/reason]" in the progress file
      - Increment the revert counter
      - Continue to the Revert Check (card step 10)
```
## Loop termination (the only real exits)
- **DoD met** → Final Quality Gate, then SUCCESS.
- **Stuck Detection**: the last 3 cycles all had zero progress (same DoD results, no
  criterion flipped) → STOP (structural blocker).
- **Revert Check**: 2 consecutive cycles ended in revert → REVERT_LIMIT (conflicting
  constraints).
- **max_cycles** reached without DoD met → UNMET.
- Retries exhausted / circuit breaker → UNMET.
Every non-SUCCESS exit continues to DELIVER (stage-complete goal_cycle with `dod_met: false`;
it completes degraded after 3 refusals) and DELIVER records `push_ready: false` with the exit
and what is left: a marked local commit, never a checkpoint or a hand-off.
**Budget is NEVER a loop exit** (STEERING #2 + XG directive "budget 从来不应该是 block run
的重要因素"). `remaining_tokens = session_budget - tokens_consumed` is report-only: surface
it for visibility if useful, never EXIT on it. Physical context overflow is handled by SDK
autocompact, not by a token count here.
Every exit records `gm.track_goal_complete(status=...)` (`ref/goal_cycle/setup.md`).
## EXIT with SUCCESS
All DoD criteria pass (verified by running the commands/rubrics) and the Final Quality
Gate is clean. `gm.track_goal_complete(status="success", ...)` + velocity summary, then
stage-complete. DELIVER → REFLECT follow as normal stages.
## EXIT with UNMET
Max cycles reached without DoD met (or Final Quality Gate issues persisting after 3 fix
cycles). `gm.track_goal_complete(status="checkpoint", ...)` (legacy status name). Output,
then DELIVER with push_ready false:
```
Goal Loop UNMET after N cycles:
- DoD criteria: X/Y met
- Progress trend: [improving/stalled/regressing]
- What's left: [remaining criteria with current values]
- Next run should: [extend cycles / adjust DoD / a different approach]
```
The output goes into DELIVER `attention_flags` (REPORT §10).
## EXIT with STOP
3 consecutive cycles with zero DoD progress. `gm.track_goal_complete(status="stop", ...)`.
```
Goal Loop STOPPED — structural blocker detected:
- Last 3 cycles: [actions taken]
- No criterion improved
- Likely cause: [diagnosis]
- Next run should: [decompose goal / different approach]
```
## EXIT with REVERT_LIMIT
2 consecutive cycle reverts (changes break existing tests, no progress possible).
`gm.track_goal_complete(status="revert_limit", ...)`.
```
Goal Loop CONFLICT — cannot progress without regression:
- Cycle N: [action] → broke [test]
- Cycle N+1: [different action] → broke [test]
- Conflicting constraints: [what the goal requires vs what existing tests enforce]
- Suggestion: [refactor needed / relax test / redefine goal scope]
```
## EXIT with BUDGET (rare — physical context limit only, NOT a token-count gate)
Never triggered by a token count. It fires ONLY when SDK autocompact cannot reclaim
enough physical context to continue: a genuine physical ceiling, not a
budget-conservation choice. In normal operation a goal loop ends via DoD met, Stuck
Detection or max cycles, never here. `gm.track_goal_complete(status="budget", ...)`
(the `budget` label is kept for metrics continuity).
```
Goal Loop paused — physical context limit (not a budget-conservation stop):
- Cycles completed: N
- DoD: X/Y met
- Progress saved to: [progress_path]
- Resume: "resume pipeline for <PROJECT>" or scheduled mode
```
