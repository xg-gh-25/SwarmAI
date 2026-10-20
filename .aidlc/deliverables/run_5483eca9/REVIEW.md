# Review — gates and experts

**Run** `run_5483eca9` · **profile** ? · **project** SwarmAI

> 起 pipeline：V1–V5

_Generated from the run's stage artifacts by the pipeline (not hand-written)._

## Gates

| Gate | Verdict | Notes |
|---|---|---|
| Gate 0 — understanding skeptic | SUPPORTED | framing: is the claim observed, is the root the root |
| Gate 1 — plan skeptic | WARN | plan before BUILD |
| REVIEW — spec + conformance | approved · litmus PASS | 11 finding(s) |
| Gate 2 — adversarial experts | full · 14 finding(s), 14 open | Red Team: yes |
| Meta-review | RISKS | 5 risk(s) |
| Push-ready | no | findings: 6 unresolved HIGH / MEDIUM(conf>=7); completion_audit.all_green is not true; exit criteria unmet: ['X1']; unverified: test: completed with 3 open gate error(s): Missing required field 'laye… |

## Experts

| Expert | Findings | Blocking | Fixed | Refuted | Open |
|---|---|---|---|---|---|
| code-quality | 0 | 0 | 0 | 0 | 0 |
| correctness | 3 | 2 | 0 | 0 | 3 |
| fixspec-skeptic | 0 | 0 | 0 | 0 | 0 |
| gate0-skeptic | 0 | 0 | 0 | 0 | 0 |
| gate1-skeptic | 0 | 0 | 0 | 0 | 0 |
| integration | 2 | 0 | 0 | 0 | 2 |
| meta-review | 5 | 1 | 0 | 0 | 5 |
| red-team | 3 | 2 | 0 | 0 | 3 |
| root-cause | 0 | 0 | 0 | 0 | 0 |
| security | 1 | 1 | 0 | 0 | 1 |
| spec-compliance | 0 | 0 | 0 | 0 | 0 |

_Dispatch:_ Agent tool: correctness, security, integration, red-team spawned in one message; fixing spec r1 merged 5 open items into 3 causes; root-cause review SOUND; fixspec-skeptic BLOCKed twice (K1 key canonicalization + one roots source; K2 <module>; K3 global --literal-pathspecs) → the loop stopped by rule and the items land as known issues

## Gate 2 findings

| Expert | Severity | Finding | Status |
|---|---|---|---|
| correctness | HIGH | Readers declared after PLAN can never correct an earlier declaration of the same unit, so the run can get stuck with no way out. `_readers` puts the plan's readers first and then run.json `consumer_readers`. `_covered` returns the FIRST ent | OPEN |
| correctness | MED | The 'declared migrated but unchanged' check can be skipped for any function in a file the diff touches. `_changed_units` adds `rel::<file>` for EVERY file in the diff (line 195). The final loop excuses a migrated reader whenever `path::<fil | OPEN |
| meta-review | MED | Consumers gate only warns when the run has no worktree with a recorded base; workspace (SwarmWS) projects and runs without a worktree never have it enforced | OPEN |
| red-team | MED | A reader declared 'migrated' passes without its function being edited, as long as the change touched any other line of the same file. The final loop skips the error when `<path>::<file>` is in `changed`, and `_changed_units` adds `<file>` f | OPEN |
| red-team | MED | Readers waived after PLAN never reach the audit chain. `run-update --add-consumer-reader K1\|path::fn\|unaffected\|<10+ chars>` writes run.json consumer_readers, and `_consumers_gate_errors` (artifact_cli.py:12543) accepts them as exemptions. | OPEN |
| security | MED | root_path() accepts git pathspec magic. ':!*run_docs.py', ':(exclude)*core*' and ':(icase)BACKEND' all contain no '/', so they pass as 'tree roots' (also in design_spec.constraints_errors, which imports the same rule). They then reach `git | OPEN |
| correctness | LOW | `verdict_text` keeps only the first 2000 chars of the gate1-skeptic answer, and `_gate1_points_from_text` (artifact_cli) reads only that text. The skeptic's per-check results and its `GATE 1 VERDICT:` line come at the end of the answer. In | OPEN |
| integration | LOW | gate1_points is only worked out when stage == 'build'. But the Gate-1 verdict check covers both build and goal_cycle, the consumers gate runs for both, and run_docs.render_review falls back to recs['goal_cycle']. So a goal_cycle run's REVIE | OPEN |
| integration | LOW | gate1_points is not in _DERIVED_RECORD_KEYS (line 9545). When _gate1_points returns [] (no bound round, or a marker from an older daemon without verdict_text), any gate1_points the orchestrator put in its own --stage-json stays in cand_rec. | OPEN |
| meta-review | LOW | gate1_points silently empty until the daemon restarts (verdict_text not kept by the old hook); REVIEW.md Gate-1 row then shows no points with no warning | OPEN |
| meta-review | LOW | fix/exploit/evidence stored on marker rows have no reader yet | OPEN |
| meta-review | LOW | CONSTRAINTS_CUTOVER is a fixed time already past; runs created between it and landing are judged by rules they never saw, and this run (created 07:05Z) was never gated itself | OPEN |
| meta-review | LOW | A migrated reader is credited when only its file was touched (consumers_gate.py:327-328); file-level credit the gate exists to remove | OPEN |
| red-team | LOW | For gate1-skeptic, verdict_text keeps only the first 2000 characters of the answer. `_gate1_points_from_text` (artifact_cli.py:445) takes its BLOCK/WARN lines from that text, so if the skeptic writes analysis before its mandated output bloc | OPEN |

## REVIEW findings

| Expert | Severity | Finding | Status |
|---|---|---|---|
| spec-compliance | HIGH | artifact_cli._gate1_points took the newest gate1-skeptic row, not the bound round, so an unbound later round could supply REVIEW.md's points. Fixed: same window and bound rows as the verdict check (_gate1_prompt_unread); points shown in the Gate 1 row. | fixed · inv I10 · class fix: _gate1_points now derives since and rows exactly as _check_gate_spawn's Gate-1 branch does; one selection rule for verdict and points |
| spec-compliance | HIGH | AC5 literal: one name left under backend/ (an absence assertion). Fixed: the test pins the whole adversarial_value key set instead of naming the deleted key. | fixed · inv I7 · class fix: the key set of analyze_adversarial_value is pinned as a whole, so any re-added key fails |
| code-quality | MED | gate2-recheck and gate2-next --fixed dropped the consumers gate's 'NOT verified' warnings (RP50: a gate that cannot run passed silently). Fixed: gate2-next adds them to the printed view and gate2-v2.json; gate2-recheck prints them to stderr before any outcome. | fixed · inv I9 · class fix: every caller of _consumers_gate_errors now surfaces the warnings list: build stage-complete (warnings), gate2-next (view warnings), gate2-r… |
| code-quality | MED | without a worktree the gate diffed against HEAD, which cannot see readers migrated in earlier committed cycles or background workspace commits, so it would refuse them. Fixed: no worktree → no base → 'NOT verified' warning, never a HEAD diff. | fixed · inv I1 · class fix: _consumers_repo returns a repo only with a recorded worktree base; every caller treats None as unverified |
| code-quality | LOW | Gate-1 point regex missed WARNING / PARTIAL and matched a level named in passing ('no BLOCK found'). Fixed: level after a colon only. | fixed |
| code-quality | LOW | _expert_answer de-duplicated whole rows, so one finding with two fix texts counted twice; the comment claimed the ledger and REVIEW.md read the new fields (RP48). Fixed: de-dup on the six identity fields; comment says the ledger keeps only its MODEL_FIELDS. | fixed |
| code-quality | LOW | a reader declared migrated in a file the run deleted read as untouched. Fixed: a deleted file counts as changed. Pure-deletion hunks credit the previous unit: documented as a known limit. | fixed |
| code-quality | LOW | design_spec kept an inline copy of the tree-root rule that already disagreed (./backend). Fixed: consumers_gate.root_path is public and the only copy. | fixed |
| code-quality | LOW | lint alias / two-step blind spots undocumented. Fixed: known limits in the lint docstring. | fixed |
| spec-compliance | LOW | non-string evidence was dropped. Fixed: list / dict kept as JSON text. | fixed |
| spec-compliance | LOW | evidence_seal.check(...)[0] is banned even with a None test: kept on purpose, its second value is the reason a fail-open happened and must reach a reader. | OPEN |

## Meta-review risks

| Severity | Risk | Status |
|---|---|---|
| MEDIUM | the gate only warns for runs without a worktree base (workspace projects, shared-worktree fallback) | OPEN |
| LOW | gate1_points stay empty until the daemon restarts (the old hook keeps no verdict_text for gate1-skeptic) | OPEN |
| LOW | CONSTRAINTS_CUTOVER 2026-10-20T08:00Z is already past; no run was created after it (checked), every new run is enforced from landing | OPEN |
| LOW | fix / exploit / evidence on marker rows have no downstream reader yet | OPEN |
| LOW | file-level credit for migrated readers (= Gate-2 K2, open) | OPEN |

## Open items

- [gate2] Stopped: the fixing spec says the class did not close (spec_not_agreed: 2 skeptic BLOCKs in round 1 after the root-cause review answered SOUND); root-cause review: SOUND — ROOT-CAUSE VERDICT: SOUND 1. There has been only one Gate-2 round, so no finding has come back after a fix. The approach has not been re-checked yet, which means the "did not close" signal is weak. The five open findings fall into three causes (K1-K3 in FIXING_SPEC.md), and each sits inside one seam of the new consumers_gate.py and its renderer. None of them challenges the core design: PLAN declar. Still open (5): c…
- [runner] Not tested in this run (regression): 498 test file(s) reach a changed module only through other modules and were not run: backend/tests/test_adaptive_timeout.py, backend/tests/test_admission_backfill.py, backend/tests/test_admission_band.py, backend/tests/test_admission_calibration.py, backend/tests/test_admission_is_noise.py, backend/tests/test_admission_rootfix.py, backend/tests/test_admission_routing.py, backend/tests/test_admission_trust_field.py, backend/tests/test_admission_trust_stamp.py, backend/tests/test_adversarial_meta.py, backend/tests/test_agents.py, backend/tests/test_…
- [degraded] deliver completed with 3 open gate error(s): completion_audit.all_green=false (gaps=5). Fix gaps before declaring push-ready.; convergence.all_pass=false — Quality Convergence Loop did not pass. Iterations: 1; Depth: completion_audit has 5 fixable gap(s) — fix them or mark as unfixable_gaps before delivery
- Open HIGH: consumers_gate first-match declarations — a reader declared migrated in PLAN cannot be corrected by run-update; escape is a PLAN re-run
- Open MED: pathspec magic (':!x') in declared paths lets a plan exclude files from the search
- The CLI change is live from the main checkout as soon as it is committed; the hook change (verdict_text, finding fields) needs a daemon rebuild + restart
- Pre-existing, not this change: test_stage_start_gate_prompts.py::test_gate2_prompts_take_the_requirement_as_data; test_silent_except_baseline (baseline drift in files this change does not touch)
