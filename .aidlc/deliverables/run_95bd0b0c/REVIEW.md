# Review — gates and experts

**Run** `run_95bd0b0c` · **profile** full · **project** SwarmAI

> 按推荐

_Generated from the run's stage artifacts by the pipeline (not hand-written)._

## Gates

| Gate | Verdict | Notes |
|---|---|---|
| Gate 0 — understanding skeptic | SUPPORTED | framing: is the claim observed, is the root the root |
| Gate 1 — plan skeptic | WARN | plan before BUILD |
| REVIEW — spec + conformance | approved · litmus PASS | 9 finding(s) |
| Gate 2 — adversarial experts | full · 9 finding(s), 4 open | Red Team: no |
| Meta-review | RISKS | 3 risk(s) |
| Push-ready | no | findings: 2 unresolved HIGH / MEDIUM(conf>=7); unverified: evaluate: completed with 1 open gate error(s): Gate-0: understanding.skeptic_verdict is 'SUPPORTED' but no gate0-skeptic sub-agent completed… |

## Experts

| Expert | Findings | Blocking | Fixed | Refuted | Open |
|---|---|---|---|---|---|
| code-quality | 0 | 0 | 0 | 0 | 0 |
| correctness | 4 | 1 | 3 | 0 | 1 |
| fixspec-skeptic | 0 | 0 | 0 | 0 | 0 |
| gate0-skeptic | 0 | 0 | 0 | 0 | 0 |
| gate1-skeptic | 0 | 0 | 0 | 0 | 0 |
| meta-review | 3 | 2 | 0 | 0 | 3 |
| red-team | 0 | 0 | 0 | 0 | 0 |
| root-cause | 0 | 0 | 0 | 0 | 0 |
| security | 0 | 0 | 0 | 0 | 0 |
| spec-compliance | 0 | 0 | 0 | 0 | 0 |
| state-machine | 2 | 1 | 2 | 0 | 0 |

_Dispatch:_ Agent tool: 3 specialist sub-agents (EXPERT-ID: correctness, security, state-machine) in one parallel batch; fixspec-skeptic x3, root-cause (SOUND), meta-review

## Gate 2 findings

| Expert | Severity | Finding | Status |
|---|---|---|---|
| correctness | HIGH | The decay pass is now a second destructive strip path, and it skips the age-proof guard that reclaim deliberately enforces. `_is_reclaimable_noise` (ddd_entry_lifecycle.py:1423) refuses to strip an entry with no `created_date` ('date-less = | fixed · inv I3 · class fix: one predicate may_leave_live_doc used by the decay move, reclaim_noise_entries and compute_reclaimable_noise |
| correctness | MED | Every non-superseded live entry whose metadata says decay:archived is moved without checking anything else. The usage bump just above it (lines 903-913) can set last_referenced to a recent recall hit, but nothing looks at it for these entri | fixed |
| meta-review | MED | The signature parse in _archived_signatures takes 13.3s on the real 41.8MB archive (about 26s or more across all 82MB). It runs inside the entry_lifecycle channel, which has a 10s ChannelExecutor budget, so it times out. The thread keeps ru | OPEN |
| meta-review | MED | The parse runs while md_lock on IMPROVEMENT.md is held. Other writers take that lock non-blocking: apply_to_ddd returns 'locked' and maturity updates skip, so for 13s or more per project those writes are lost or deferred. | OPEN |
| state-machine | MED | Keep-class entry stuck in a transition that repeats every pass. A COE-tagged [guideline]/[process] entry is not in EVERGREEN_TYPES, so assess_decay still moves it dormant to archived (line 925-935), and line 928 sets decay_state='archived' | fixed · inv I3 · class fix: decision before state change, via may_leave_live_doc |
| correctness | LOW | `_strip_entries_by_line` ends a block at the first blank line unless a metadata comment follows it immediately. `parse_entries` (lines 570-577) keeps going past a blank line when the next non-blank line is not `- ` or `## `. For an entry sh | OPEN |
| correctness | LOW | The comment is wrong (RP48), and keep-class entries cycle forever. A COE-tagged guideline/process entry that decays to archived is filtered out of the move list and also left out of active_entries, so inject never writes its new state. Its | fixed |
| meta-review | LOW | The 'moved' count in ENTRY_ARCHIVE findings includes entries skipped as already archived. | OPEN |
| state-machine | LOW | The new step that picks up entries already marked decay:archived uses a different guard than the decay path. assess_decay never lets an EVERGREEN_TYPES entry (pitfall included) reach archived, but the pickup at 921-924 only filters supersed | fixed |

### Fixed by class

- **correctness · HIGH** — The decay pass is now a second destructive strip path, and it skips the age-proof guard that reclaim deliberately enforces. `_is_reclaimable_noise` (ddd_entry_lifecycle.py:1423) refuses to strip an entry with no `created_date` ('date-less =
  - invariant: I3
  - root cause: decay move used keep-class only, not reclaim's age-proof rule
  - class fix: one predicate may_leave_live_doc used by the decay move, reclaim_noise_entries and compute_reclaimable_noise
  - break test: backend/tests/test_ddd_orchestrator.py::TestEntryLifecycleArchiveIsAMove::test_entry_that_may_not_leave_keeps_state
- **state-machine · MED** — Keep-class entry stuck in a transition that repeats every pass. A COE-tagged [guideline]/[process] entry is not in EVERGREEN_TYPES, so assess_decay still moves it dormant to archived (line 925-935), and line 928 sets decay_state='archived'
  - invariant: I3
  - root cause: state was set before the move decision and never persisted
  - class fix: decision before state change, via may_leave_live_doc
  - break test: backend/tests/test_ddd_orchestrator.py::TestEntryLifecycleArchiveIsAMove::test_entry_that_may_not_leave_keeps_state

## REVIEW findings

| Expert | Severity | Finding | Status |
|---|---|---|---|
| code-quality | HIGH | core/ddd_orchestrator.py _ch_entry_lifecycle: the new move path stripped keep-class entries (2 COE entries in real docs). Fixed: is_keep_class entries are filtered out of to_archive; test_keep_class_coe_entry_is_never_moved_out_of_the_live_doc. | fixed · inv I1 · class fix: the decay-pass move applies the same is_keep_class guard reclaim uses |
| code-quality | MEDIUM | core/ddd_entry_lifecycle.py _archived_signatures: re-parsed a 41.7MB archive on every call (3.86s, up to twice per pass, under md_lock). Fixed: signature set cached by (mtime_ns, size) and refreshed after our own append. Real-data replay: pass 1 2.5s, pass 2 0.1s. | fixed |
| code-quality | MEDIUM | core/ddd_entry_lifecycle.py archive_entries: read+rewrite of the whole archive could truncate it on a failed write. Fixed: append mode; existing bytes never read back or rewritten (replay confirms archive prefix unchanged). | fixed |
| code-quality | LOW | _archived_signatures parsed without prose bullets, so retire_entry(include_prose=True) archives never deduped. Fixed: include_prose=True. | fixed |
| code-quality | LOW | _strip_entries_by_line docstring named only reclaim_duplicate_entries. Fixed: names the decay pass too. | fixed |
| spec-compliance | LOW | test_second_pass_appends_nothing did not check the live doc after pass 2. Fixed: asserts the entry is absent from the live doc. | fixed |
| spec-compliance | LOW | Requirement said reuse _archive_and_strip; built archive_and_strip_lines instead. Left: _archive_and_strip strips by (title, section) and writes the doc itself, which would remove a same-title twin and add a second write before metadata injection; line-precise strip is the correct identity. | OPEN |
| code-quality | LOW | Per-project except in _ch_entry_lifecycle logs at DEBUG only. Pre-existing, out of this change's scope; recorded as a known issue. | OPEN |
| code-quality | LOW | Test constant OLD hardcodes [decision] but _doc rewrites the type. Cosmetic, left. | OPEN |

## Open items

- Follow-up: one-time dedup of the 9 bloated *-archive.md files (about 82MB, about 407 distinct entries)
- Follow-up: _strip_entries_by_line block boundary differs from parse_entries
- Pre-existing red on base: test_silent_except_baseline (6 files), test_lint_skills_body (plan.md over 5,000 chars)
- Pre-existing: 14 date-titled active->dormant transitions in SwarmAI IMPROVEMENT.md re-fire every pass (title-keyed inject cannot match them)
- [gate2] Stopped: the fixing spec says the class did not close (spec_not_agreed: 2 skeptic BLOCKs in round 1 after the root-cause review answered SOUND); root-cause review: SOUND — ROOT-CAUSE VERDICT: SOUND Evidence: - The approach goes after the cause the requirement names. Before, the decay pass appended to the archive and then only annotated the live doc, so the entry stayed live and got archived again every pass. Now, in `~/.swarm-ai/worktrees/run_95bd0b0c/swarmai/backend/core/ddd_orchestrator.py` (about lines 935-965), the pass calls `archive_and_strip_lines`. Still open (3): c…
- [runner] Not tested in this run (regression): 567 test file(s) reach a changed module only through other modules and were not run: backend/tests/test_active_run_commit_gate.py, backend/tests/test_adaptive_timeout.py, backend/tests/test_admission_backfill.py, backend/tests/test_admission_band.py, backend/tests/test_admission_calibration.py, backend/tests/test_admission_is_noise.py, backend/tests/test_admission_rootfix.py, backend/tests/test_admission_routing.py, backend/tests/test_admission_trust_field.py, backend/tests/test_admission_trust_stamp.py, backend/tests/test_advance_continuation.py,…
- [degraded] deliver completed with 8 open gate error(s): Fix by class: adversarial_review.findings[0] (correctness, HIGH) names no invariant of the design (got 'I3'). Name the I<n> it violated, or add the missed invar; Fix by class: adversarial_review.findings[4] (state-machine, MED) names no invariant of the design (got 'I3'). Name the I<n> it violated, or add the missed inva; Red Team required: 1 specialist HIGH/CRITICAL finding(s) but adversarial_review shows no Red Team run — [HIGH by correctness] The decay pass is now a second des (+5)
