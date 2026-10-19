# INSTRUCTIONS ref — Step 4 DELIVER orchestration (Taste Gate, Quality Convergence Loop, Report & CI)
Read when: entering DELIVER with accumulated taste decisions, running or failing the 6-layer
push-ready gate, marking a finding `resolved`, or exhausting convergence iterations. The DELIVER
card (`stages/deliver.md`) owns the stage's own steps; this is the orchestrator's frame around them.

DELIVER runs 4 phases in order: (1) **Taste Decision Gate**; (2) **deliver stage execution** —
`stages/deliver.md`: Fresh User Audit (P6), Completion Audit, Gate-2 adversarial review (spawned
sub-agents, profile-aware tiering), Meta-Review ("what did the pipeline miss?"), push-ready gate;
(3) **Quality Convergence Loop**; (4) **Report & CI**. The adversarial review in deliver.md is the
FIRST pass; the convergence loop re-verifies after fixes.

## 4a. Taste decisions — recorded, never a gate
The pipeline is autonomous: taste decisions are NOT presented for approval. Collect ALL taste
and judgment decisions from ALL prior stages into the DELIVER artifact (`record.decisions`,
plus one `attention_flags` line per judgment) so REPORT §10 lists them; the user overrides
after the commit if they want (push stays theirs):
```
Decisions taken (REPORT §10):
  1. [THINK, T6]  Chose httpx built-in retry over tenacity (simpler, fewer deps)
  2. [BUILD]      Used sync retry instead of async (matches existing codebase style)
```
**Thesis tagging:** when a taste decision is informed by a thesis in `Knowledge/Learned/THESIS.md`,
tag it `[STAGE, Txx]` (only when the connection is real) so later review can trace the choice.
- Never wait for approval; never re-run a stage for an override inside this run. A user
  override after the commit is a new requirement (a new run).

## 4c. Quality Convergence Loop
After deliver execution produces a delivery candidate (code written, tests pass, adversarial
review done, meta-review complete), this loop decides whether it is truly push-ready, iterating
until it is or escalating. It is NOT a separate stage: no artifact, not in any profile's stage
list, not checked as a stage by the validator. It is an internal sub-loop of DELIVER.

### The 6-Layer Push-Ready Gate — ALL 6 must pass simultaneously (5/6 is not push-ready)
| Layer | Checks | How to verify |
|-------|--------|---------------|
| L1 Tests Pass | all new + existing tests green | `pipeline_test.py --mode recheck` (or `regression`) green |
| L2 Type-Safe | no type errors, linter clean | type checker + linter via `pipeline_test.py` (if configured) |
| L3 No Regressions | pre-existing tests still pass | `pipeline_test.py --mode regression` (pre-existing failures classified by the HEAD baseline) |
| L4 Adversarial Clean | no critical/medium findings from deliver.md's review | all findings fixed or explicitly accepted, verified on disk (below) |
| L5 DDD Conformance | no violation of TECH.md constraints or IMPROVEMENT.md anti-patterns | mechanical checklist extraction + per-item verification (below) |
| L6 Decisions Resolved | all taste/judgment decisions surfaced | decision log complete, no hidden choices |

**L4 clarification:** the specialist sub-agents were already spawned in deliver execution; L4
checks whether all their findings are resolved. Re-spawn only if a convergence fix changes code
the original review didn't cover.

<!-- R-instr-008 -->
**L4 verify-against-disk (BLOCKING — never trust the `resolved` flag):** for each finding marked
`resolved: true`, do NOT take the flag's word for it — confirm the fix is actually on disk. The
`resolved` field records intent ("I applied the fix"), not state ("the fix is on disk"). They
diverge whenever a fix is reverted between application and delivery — an external `git stash
pop`, a parallel-session commit, a linter undo. (Incident: `stages/history/instructions.md`.)

<!-- R-instr-009 -->
**Now code-enforced — [GATE·validator].** Attach a structured `disk_check` to each resolved finding
and the validator (`_verify_findings_on_disk`, called at BOTH the publish-time and completion-time
gates, R27) greps it for you — a BLOCK the model cannot rationalize past:
```json
"findings": [{"severity": "HIGH", "resolved": true,
  "finding": "core/foo.py bar() line 42: missing null guard. Fixed: added guard.",
  "disk_check": {"file": "/ABS/path/to/core/foo.py", "must_contain": "if x is None: return"}}]
```
- `disk_check.file` MUST be an **ABSOLUTE** source path. Findings reference the SOURCE repo, but
  the validator's workspace root is `~/.swarm-ai/SwarmWS` (the source-vs-workspace split) — a
  relative path resolves against the wrong tree and false-blocks. Relative/empty path → WARN, not
  a check.
- `must_contain` — for a fix that ADDED code: string ABSENT from the readable file → **BLOCK** (fix
  reverted). Use the durable line the fix introduced.
- `must_not_contain` — for a fix that REMOVED/refactored code: string STILL PRESENT → **BLOCK**. A
  deleted file passes vacuously.
- Fail-open on uncertainty: missing file (for must_contain) / unreadable / binary / oversized →
  **WARN, never BLOCK** (a locus we can't verify is "can't check", not "reverted").
- A resolved HIGH/CRITICAL finding with NO `disk_check` → **WARN** (add one); LOW/unstructured
  findings are silent.
`disk_check` is the machine-verified locus, distinct from the free-text path/line in `finding`.

**L5 mechanism (Constitution Pattern)** — a mechanical extraction + verification, not an
honor-system self-assessment:
1. From TECH.md: "Runtime Traps" and "Constraints" items → MUST-NOT-VIOLATE; "Design Principles" →
   SHOULD-FOLLOW.
2. From IMPROVEMENT.md: "What Failed" and "Anti-Patterns" (if present) → MUST-NOT-REPEAT.
3. Verify the diff: MUST-NOT-VIOLATE — does the diff introduce or rely on it? Binary YES/NO.
   MUST-NOT-REPEAT — does the approach structurally resemble a failed one? If yes, explain why it
   differs this time or flag a violation. SHOULD-FOLLOW — deviation only with a justification
   logged in the decision record.
4. Any MUST violation = L5 FAIL; justified SHOULD deviations are noted, non-blocking.
No TECH.md or IMPROVEMENT.md → L5 auto-passes (richer DDD docs = stronger gates).

### Convergence behavior
```
LOOP (max 3 iterations):
  1. EVALUATE — check all 6 gate layers; collect failures.
  2. ALL PASS + agent self-assessment positive + goal aligned → EXIT: push-ready
  3. Failures → identify the SPECIFIC layer and the SPECIFIC issue, apply a MINIMAL, TARGETED
     fix (not a rewrite), re-verify the ENTIRE gate (a fix may introduce new failures)
  4. Increment the iteration counter
```
Fixes are targeted — the loop never re-runs all stages. Each iteration must NARROW the gap; one
that widens it (more failures introduced than fixed) → STOP and escalate. Specialists are
re-spawned only for new code paths the original review didn't cover, scoped to the delta.

**Exit when ALL THREE hold:** all 6 layers pass; agent self-assessment positive ("I am satisfied
with this delivery"); task goal alignment confirmed ("this solves what was asked"). Then declare
push-ready and go to 4d. On the new path `stage-complete --stage deliver` computes `push_ready`
from the gate results (tests, no unresolved HIGH / high-confidence MEDIUM, completion audit green,
no regressions, no meta-review HIGH, exit contract met) — you do not declare it yourself.

**Max iterations exhausted (3 without convergence):** stop fixing; the run stays autonomous, so do
NOT checkpoint and do NOT ask the user. Record it precisely in the DELIVER artifact
(`attention_flags`, which REPORT §10 and the completion summary list):
```
CONVERGENCE NOT REACHED after 3 iterations:
  Remaining failures: [L3: test_foo_bar regresses, L4: race condition in async path]
  Attempted fixes: [iteration 1: ..., iteration 2: ..., iteration 3: ...]
  Root cause: [why this isn't converging — Gate-2 uses the root-cause review's verdict]
  Next step: [what a follow-up run should change]
```
then complete DELIVER with `push_ready: false` (reason recorded) and let `run-commit` commit
locally. Gate-2's own non-convergence is handled by the tool (one root-cause review after
round 3; SOUND or WRONG-APPROACH → local commit, verdict + open items in REPORT §10, no
redesign, no 4th round); push stays gated by push_ready.

Per-stage feedback loops are shift-left prevention; the convergence loop is the final gate for
issues that only emerge at the system level. Neither alone is sufficient.

## 4d. Report & CI
After the loop declares push-ready:
1. REPORT.md at `.artifacts/runs/<RUN_ID>/REPORT.md` (`stage-complete --stage deliver`
   auto-generates it when missing).
2. Record the push-ready verdict and meta-review result in run.json.
3. Record convergence metadata: `{"convergence": {"iterations": 2, "initial_failures": ["L3",
   "L4"], "final_status": "push-ready"}}`.
4. Advance to the next stage (reflect).

**Pipeline boundary:** PUSH-READY = the pipeline's quality guarantee is complete. Push to remote,
CI verification and PR creation are **user-initiated post-pipeline actions**, not pipeline steps;
the pipeline MUST be completable without network. After COMPLETE, suggest "push to remote + verify
CI" but do NOT execute it. STEERING.md "Post-Push CI Ownership" governs push→CI→green separately.
