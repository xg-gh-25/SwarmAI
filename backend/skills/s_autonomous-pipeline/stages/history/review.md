# REVIEW — history and rationale
Not read at runtime. The execution card is `stages/review.md`; conditional detail is in
`stages/ref/review/`. This file keeps the "why" behind REVIEW's rules, for humans and for
the evolution pipeline.

## The shortcut table that used to open the stage
The doc opened with a STOP table because "I already reviewed this during BUILD" is the
most common CLASS A rationalization. Its lessons (now one-liners in
`ref/review/exit.md` § 14):
| Thought | Why it's wrong |
|------|------|
| "I already reviewed during BUILD, I know it's correct" | You WROTE it. Re-reading your own assumptions is not review. A builder rated a change 10/10; the feature was 100% broken. |
| "Tests pass, so the code is correct" | Tests verify declared behavior. Review catches undeclared behavior: missing error paths, wrong conventions, integration wiring. |
| "This is a small change, full review is overkill" | 3 files, "simple" → 2 HIGH findings. Small changes have the highest skip rate and the highest bug density per line. |
| "I'll just do a quick self-review" | Self-review found 0; adversarial found 5 (2 HIGH) on the same code in the same session. Confidence is anti-correlated with bug presence. |
| "Only 1-2 files, no need for sub-agents" | Integration trace and the RP checklist are mechanical and need no sub-agents, but they DO need to run. Size doesn't exempt process. |
| "Low on context/budget, skip to DELIVER" | CHECKPOINT instead (STEERING R11). A skipped REVIEW makes DELIVER's adversarial the single point of failure. |
| "Valid finding, fix it in a follow-up" | There is no follow-up. The run ends at DELIVER. A deferred finding is a shipped bug. |

## REVIEW vs ADVERSARIAL — different jobs
| | REVIEW | ADVERSARIAL (inside DELIVER / goal_cycle) |
|---|---|---|
| Who | Agent self-reviews with mechanical aids + isolated sub-agents | Fresh sub-agents with zero BUILD context |
| What | Conformance: does code match spec, patterns, conventions? | Bugs: what breaks under adversarial pressure? |
| How | RP1-RP88 checklist, integration trace, DDD conformance | Free-form attack with specialist prompts |
| Catches | Missing exports, wrong naming, violated conventions | Logic errors, races, edge cases |
Both are necessary: REVIEW asks "did you follow the rules" (mechanical), ADVERSARIAL asks
"does it actually work" (creative). 12+ pipeline runs showed they find different classes.

## Why Litmus runs first
It is a 30-second structural sanity check that decides whether the code is worth ~30K
tokens of deep review. A FAIL routed back to BUILD is cheaper than an adversarial cycle
that returns 10+ issues. The 3-strike escalation exists because a builder that fails
litmus repeatedly may be cycling rather than fixing.

## Why spec compliance is a fresh, serial sub-agent
The builder's BUILD confidence pollutes spec judgment (a 10/10 self-rating on 100%-broken
code). A fresh sub-agent sees only the diff and the ACs; that isolation is the whole
value. It runs before fan-out so a missing AC is found before anyone reviews the quality
of code that should not exist yet.

## Why two axes are never merged
Provenance: `Knowledge/Reports/2026-07-12-mattpocock-skills-deep-research.md` S1
(mattpocock non-merging insight): spec ("did it do what was asked?") and quality ("is it well-built?") must be
reported separately. Clean code implementing the wrong thing is quality-PASS/spec-FAIL;
the right feature violating conventions is the reverse. Merging lets one mask the other.

## Why security is not a standing REVIEW agent
It used to be spawned in REVIEW AND again as the Gate-2 security specialist: the same
lens, checklist and diff reviewed twice. Since the pipeline-speed work, stage-start picks
ONE owner per run (Gate-2 when it dispatches security, otherwise REVIEW), and DELIVER's
stage-start re-adds security if diff drift would otherwise leave nobody reviewing it.

## Why the dispatch list and size counts come from stage-start
Runs spent dozens of calls per stage computing `git diff --stat`, BASE_REF and DIFF_LINES
by hand and then misapplying the thresholds. stage-start now returns the changed files,
non-test source counts and `review_experts`; a bugfix that adds 300 lines of tests is not
a 300-line change, so test files never count toward the thresholds. Spawns are
foreground because a background Agent call ends the visible chat stream.

## Why REVIEW_PATTERNS.md is no longer read whole
The table is ~112K characters. `spawn_prompt.py --patterns-for` emits only the rows whose
`match:` fires for the changed paths (and, with `--diff-file`, the diff content), so the
checklist stays complete for the change without loading every row.

## Incidents behind individual checks
- **3b Input boundary (2026-05-25):** `compute_confidence()` need_signal was raised to
  0.3. A caller filtered priority skills with `min_examples=3`, so 1 correction in 3
  examples at 33% density scored 0.18 (HIGH tier) — a promotion on thin evidence. Fixed
  with an `n_corrections >= 2` guard in the deploy path; the extreme-input trace would
  have caught it at REVIEW instead of adversarial.
- **4 Replace/move parity:** a PE review of the RecallEngine activation found 2 HIGH bugs
  (a replaced function dropped TranscriptStore; a test mock hid a missing attribute).
  Desktop Update Gaps added two more: extracted cleanup placed after a fast-path
  `return`, and a duplicate method left by a parallel session.
- **5 UX review:** a run shipped with 10/10 confidence and 44/44 tests; a 5-minute user
  walkthrough found 3 bugs (scroll tracking, no discoverability hint, Escape
  propagation). Engineering-complete is not user-complete.
- **7 Wire test:** Voice Input set an explicit `Content-Type: multipart/form-data` header
  that broke the Axios boundary string; voice input would have been fully broken.
  Integration trace proved symbols connected, not that the wire format was right.
- **7b Operational context:** dev-invisible, production-fatal bugs (daemon env, kill
  semantics, no-op hook cost) were the class REVIEW + adversarial structurally missed.
- **9 Blast radius (2026-04-29):** a unified-release-pipeline run passed every stage at
  9/10; a DevOps E2E audit found 2 HIGH + 3 MED in 5 minutes, all outside the diff and
  inside the system lifecycle. For infra/release code the lifecycle IS the feature.
- **16 Platform matrix:** a run hardcoded the macOS checkout path as the only canonical
  path. On Hive (EC2 Linux) it doesn't exist; the code fell through to the
  `__file__` fallback, the exact path that is wrong in daemon context. TECH.md listed
  all 4 platforms; BUILD didn't cross-reference it.
- **17 Blocking constraints:** projects differ in compliance/security/style rules. They
  live as a TECH.md section (not a 5th DDD doc) to reuse cultivation, staleness detection
  and stage routing.
- **6 Runtime patterns:** `asyncio.get_event_loop()` (RP19) and the `date('now')` UTC
  mismatch (RP18) both passed a pipeline because they "didn't seem to apply".

## Common rationalizations (from the old doc's closing table)
| Rationalization | Reality |
|---|---|
| "Code is straightforward, self-review is sufficient" | Single-person review has structural blind spots regardless of simplicity; you validate your assumptions instead of challenging them. |
| "Integration trace is redundant — unit tests cover wiring" | Unit tests mock boundaries. The trace checks that caller A really reaches callee B with the right params; each unit worked, the data between them was wrong. |
| "Runtime patterns RP1-RP88 — most don't apply" | Check each and mark N/A explicitly (RP18/RP19 above). |
| "Small changeset, fan-out is overkill" | The fan-out threshold is calibrated; below it single-pass still runs all checks. "Small" means one reviewer does every check. |
| "Findings are low-confidence, suppress them all" | Suppressing ≤4 is valid, but suppressing everything means none was confirmed. Verify the top 3 by evidence. |

## Restructure (pipeline-speed split)
The 56K stage doc became a ≤5K execution card plus `ref/review/` (litmus, spec-compliance,
dispatch, checks-core, checks-surface, checks-system, exit) and this file. Every tagged
rule (`R-review-NNN`) lives in the card or a ref file. Dropped as pure duplication of
INSTRUCTIONS.md: the long-form continuation-contract paragraph (the card keeps the short
form plus the shared phrase) and the "Schema-first" advice (stage-start returns the
example). The `run.json`-vs-artifact_cli AC source split and the publish/advance commands
are kept in ref (spec-compliance, exit) because sibling skills still use them.
Known wording conflict carried forward: the old spec BLOCK row said "increment
`litmus_fail_count` (shared counter)" while its Retry Accounting said a separate
`spec_fail_count`; `ref/review/spec-compliance.md` follows the separate counter.
