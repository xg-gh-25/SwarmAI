# DELIVER — meta-review handling, convergence, push-ready, exit

Read when the card points here: the meta-review returned risks, the convergence loop
does not converge, `stage-complete` caps `push_ready`, or you need the exit shape.

## Meta-review handling

The meta-review is a sub-agent that does not review the CODE — it reviews the
PIPELINE'S BLIND SPOTS for this changeset: deployment context, operational scaling,
cross-boundary format, first-run vs steady-state, architectural integrity (No-Patch
backstop), cross-fix interaction. Review and Gate-2 reliably catch code-correctness bugs
but miss deployment-context mismatches (an O(n) no-op scan in a per-session hook,
`sys.executable` inside a daemon, a JSON spacing assumption across languages): code
correct in dev, wrong in production.

Prompt: `data/gate-prompts/meta-review.md`, emitted by
`spawn_prompt.py --gate meta-review --project <P> --files <changed files...>` (same exit
contract as gate2: exit 0 = use it; 2 = no cap; 3 = no `--project`; stdout empty on
non-zero). Fill `<requirement from run.json>` and the "where this runs" line. One Agent
call, `run_in_background: false`, AFTER Gate-2 passes and BEFORE push-ready.

- `CLEAR` → proceed.
- `RISKS_IDENTIFIED` with a HIGH → fix before push-ready (same as an adversarial HIGH).
  A HIGH you cannot resolve escalates as a **MID-STAGE** exit: always **checkpoint**,
  never in-band (INSTRUCTIONS § Escalation Routing Protocol, "Mid-stage rule": blocking a
  4h question mid-loop would freeze a half-written convergence iteration).
- Only MED/LOW → note them in the report (§10) and proceed.
- Record `meta_review: {"verdict": "CLEAR" | "RISKS", "risks": [...]}` (the artifact
  enum spells the prompt's `RISKS_IDENTIFIED` as `RISKS`).
- Profiles: full, bugfix and every other strict profile run it; trivial, research, docs
  skip it (no operational context to analyze).

## Quality Convergence Loop (max 3 iterations)

Not a separate stage and no artifact of its own: an internal sub-loop bridging "delivery
candidate" to "push-ready". ALL 6 layers must pass at once; 5/6 is not push-ready.

| Layer | Passes when |
|---|---|
| L1 Tests pass | runner evidence green (`pipeline_test.py --mode recheck` after fixes) |
| L2 Type-safe | type checker + linter clean (if configured) |
| L3 No regressions | pre-existing tests still pass (TEST's regression evidence) |
| L4 Adversarial clean | every HIGH / conf≥7 MED fixed or explicitly accepted, each fix confirmed ON DISK (`disk_check`) |
| L5 DDD conformance | no violation of TECH.md constraints or IMPROVEMENT.md anti-patterns (mechanical extraction; no TECH.md → auto-pass) |
| L6 Decisions resolved | every taste/judgment decision is in the decision log |

Converged in 1 iteration on a non-trivial change → scrutinize harder, not less: the
gates may be too lenient. Not converged after 3 → checkpoint with the remaining gaps.

## Push-ready — binary, evaluated LAST, computed by the tool

No numeric score: a number between "push" and "don't push" creates false gradients (a
10/10 confidence once sat on 100%-broken code — it measured process compliance, not
correctness). PUSH-READY requires ALL of:

```
□ All acceptance criteria have passing tests (no AC without evidence)
□ Zero HIGH/MED findings (confidence >= 7) from adversarial review (or all fixed)
□ Completion audit: all_green = true (deliverables match requirement)
□ Zero regressions on existing tests
□ Meta-review completed with no unaddressed HIGH risks
□ PLAN exit contract met (exit_criteria_met)
```

NOT-PUSH-READY triggers: an unfixed HIGH; an AC without a passing test; a completion
audit gap; `ac_verification` `unverified`; an unaddressed meta-review HIGH (escalate as
above); a User-Value Probe FAIL (agent discipline, see `ref/deliver/audits.md`).

`stage-complete --stage deliver` computes the upper bound from runner evidence and the
artifact (`push_ready_upper_bound`: tests, unresolved HIGH / MEDIUM conf≥7, completion
audit, regressions, meta-review, exit contract; a `direct` run, or one whose PLAN
recorded an exit contract, is capped false when no contract can be judged). The stored value is `bound AND NOT lowered`. You may only
LOWER it: send `"push_ready": false` with a `push_ready_reason` in the record. Sending
`true` cannot raise a false bound; the reasons are returned and stored as
`push_ready_reason`.

## Exit

`/tmp/<R>/deliver.json` = `{"artifact": {...}, "record": {...}}`, shaped like the
`artifact_example` / `record_example` from `stage-start`:
- artifact: `title`, `quality` (`tests_pass`, `regressions`, `smoke_pass`),
  `adversarial_review`, `completion_audit`, `ac_verification`, `meta_review`,
  `convergence` (`iterations`, `all_pass`, `final_status`), optionally
  `user_value_probe`, `shared_state_audit`.
- record: `decisions`, `exit_evidence` for manual exit criteria, and only to lower it,
  `push_ready: false` + `push_ready_reason`.

`stage-complete` derives `findings_total/fixed/remaining`, `token_cost`,
`consumed_artifacts`, `exit_criteria_met` and `push_ready`; it auto-generates REPORT.md
when missing. Preflight with `--check`. When it reports `exit_criteria_met: true`, stop
adding verification and move on.
