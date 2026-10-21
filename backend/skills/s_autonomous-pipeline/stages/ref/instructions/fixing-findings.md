# INSTRUCTIONS ref — Fixing findings: one evaluation, one fixing spec, one fix pass
Read when: any review returned findings to fix — a gate skeptic (Gate 0 / Gate 1 /
the goal Final Quality Gate), the REVIEW fan-out, Gate-2 experts, meta-review — or the
TEST / goal regression run is red.

**`[MUST]` Never fix findings one by one as they arrive (no whack-a-mole).** Patching each
finding on its own fixes the symptom at the one line reported, leaves the other instances
of the same cause in place, and is why fix rounds kept reopening (XG, 2026-10-19). Every
review's findings go through the same four steps:

1. **Collect the whole round first.** Wait until every reviewer of the round (and, for
   tests, the whole run) has answered. Do not start fixing on the first answer.
2. **Evaluate them together.** Dedup across reviewers; drop the refuted (with the
   evidence); classify blocking vs not. Then group by CAUSE, not by reporter or file:
   findings that share a broken invariant, a missing owner, a duplicated writer are ONE
   cluster. Default reading: findings in one area share a cause until you can say why not.
   **`[MUST]` Scope: only what this change introduced is fixed in this run.** A finding about
   behaviour the diff did not create (pre-existing, residual, "not caused by this change")
   goes into no cluster: record it as a known issue (REPORT §10 and the commit message) for
   the user to schedule. The one exception: it makes this change's own tests red. Folding it
   in widens the diff, reopens every reviewer and the full test run, and is where new
   regressions come from (a pre-existing timeout "fixed" in passing turned a gate's deny into
   an allow; the next round's work was reverting it).
3. **Write ONE fixing spec for the round** (the artifact's fix record; for Gate-2 the
   `FIXING_SPEC` of `docs/design/pipeline-anti-whack-a-mole.md`): per cluster the findings
   it covers, the cause (`invariant` / `root_cause`), the scope (every site with the same
   cause, found by an actual grep or trace, not only the reported line), ONE solution for
   the whole cluster (prefer reusing or removing over adding a guard), and the
   `break_test` that proves the class. Every blocking finding is in exactly one cluster.
4. **Fix the whole spec in one pass, then re-verify once.** Implement every cluster, run
   the tests it affects once (`RUN --mode tdd --files` / `prove`; the full-scope
   regression runs once, after the LAST fix, at DELIVER), and send the next review round the
   spec plus the fix diff: the re-check asks "is this class closed at every site in
   scope", not "is this one line changed".

Applies everywhere a review returns findings:
| Source | One round = | Spec lives in |
|---|---|---|
| Gate 0 / Gate 1 skeptic BLOCK | the skeptic's verdict | the revised understanding / PLAN (all its checks addressed in one revision) |
| REVIEW fan-out (spec-compliance + experts + inline RP checks) | the whole fan-out | the REVIEW artifact's fixed findings (`invariant`, `root_cause`, `class_fix`, `break_test`) |
| TEST regression / recheck failures | one regression run | `exit_evidence.fixes` grouped by cause; one recheck after the pass |
| goal Final Quality Gate | the gate's findings + the ledger | the findings ledger, re-judged on the total diff |
| DELIVER Gate-2 + meta-review | one Gate-2 round (one `gate2-recheck` batch) | `gate2-fixspec` → `FIXING_SPEC.md` (legacy flow only; no accepted spec → push_ready false) |

The fix-by-class validator (`design_spec.stage_errors`, `[GATE·validator]`) already
refuses a fixed blocking finding without `invariant`, `root_cause`, `class_fix` and
`break_test`; the cluster grouping and the single pass are `[MUST]` everywhere and
checked for legacy Gate-2 by `gate2-fixspec` (`ref/deliver/gate2-findings.md`).
