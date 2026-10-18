# REVIEW ref — Anti-rationalization (14), exit evidence (15), fix loop, artifact
Read when: concluding REVIEW (always — checks 14 and 15 are Tier 1), fixing findings, or
stage-complete rejects the artifact shape.

## 14. Anti-Rationalization Gate `[MUST]` <!-- R-review-009 -->
Before concluding REVIEW, reject these shortcuts:
| Agent shortcut | Required response |
|---|---|
| "Changeset is small, skip integration trace" | Small changes with unwired symbols are the #1 silent failure. Trace every new symbol. |
| "Security scan isn't needed for internal code" | Internal code with injection paths gets exploited via MCP tools and API calls. Scan it. |
| "Runtime pattern checklist doesn't apply here" | Check every pattern. Write N/A explicitly. Silence = unchecked. |
| "Operational patterns don't apply — this is just code" | If the code changes ANY lifecycle operation, OP1-OP8 apply. "Just code" that deploys without rollback is an incident waiting to happen. |
| "Wire test is overkill — the types match" | Types matching != serialization matching. Content-Type bugs are invisible to type checkers. |
| "UX review isn't needed — the UI change is trivial" | Trivial UI changes cause scroll breaks and accessibility regressions. If UI files changed, check UX. |
| "Review is clean, marking confidence 10/10" | Confidence without evidence is fiction. Score against the checklist, not gut feel. |
| "Blast radius trace not needed — I only changed scripts" | Infra/release bugs are invisible in the diff and break the system. If it touches build/deploy/CI, trace the lifecycle. |
| "Adjacent functions are fine — I only changed this one" | Bugs cluster. If this function was wrong, the one copied from it 20 lines up is wrong too. Check the neighborhood. |
| "The other config file is fine — I only changed this one" | Config drift is guaranteed if you don't verify. If two files describe the same thing, compare them. |
| "Adversarial review is redundant — REVIEW already checked everything" | REVIEW uses the builder's perspective; adversarial uses user + skeptical-reviewer perspectives. They find different bug classes. |
| "I already reviewed during BUILD" / "a quick self-review is enough" | You wrote it; re-reading your own assumptions is not review. Run the checks and the sub-agents stage-start lists. |
| "Small changeset, no need for sub-agents or the checks" | Below the fan-out threshold, single-pass STILL runs every check (trace, patterns, wire test). Size never exempts process. |
| "Low on context/budget, skip to DELIVER" | CHECKPOINT instead (STEERING R11). A skipped REVIEW leaves DELIVER's adversarial as the only safety net. |
| "Valid finding, I'll fix it in a follow-up" | There is no follow-up: the run ends at DELIVER. A deferred finding is a shipped bug. Fix now or escalate. |

## 15. Exit Evidence Checklist `[MUST]` (cross-boundary case (a) = `[GATE·artifact_cli]`) <!-- R-review-010 -->
> The checklist itself is `[MUST]` (agent self-confirms each line). Within the
> cross-boundary E2E line, ONLY **case (a)** is code-enforced: `artifact_cli.py`'s
> `cross_boundary_e2e` gate `sys.exit(1)`s at DELIVER if a `value==true` change lacks a
> passing Layer-4 E2E record. ⚠️ **Cases (b) + (c) are `[MUST]`, NOT gated
> (adversarially corrected):** the gate lives entirely inside
> `if _cb.get("value") is True:` — it fail-OPENS on a bare `false`, and `ruled_out` is
> read by NO code (0 hits). So "bare `false` → BLOCK" (b) and "`true` under
> docs/research → BLOCK" (c) are agent-discipline, not CLI-enforced. <!-- R-review-011 -->

Confirm each before stage-complete:
- [ ] Spec compliance gate passed (coverage matrix shows all ACs IMPLEMENTED, or WARNING findings injected into merge)
- [ ] Integration trace output present (`N symbols checked, M connected, K warnings`)
- [ ] Runtime pattern checklist complete (every applicable RP has pass or N/A)
- [ ] Operational pattern checklist complete (every applicable OP has pass or N/A, or "no lifecycle ops, N/A")
- [ ] Security scan ran with confidence scores (or "no security-relevant changes" stated)
- [ ] Wire test results shown (or "single-layer change, N/A" stated)
- [ ] **Cross-boundary E2E:** read the EVALUATE `cross_boundary` object. (a) `value == true`: the TEST artifact's `cross_boundary_e2e.run == true` with a real-system test that does NOT mock the seam AND a stated mutation (revert the contract line → RED). BLOCK if the flag is set but the E2E is absent, mocks the thing-under-change, or has no mutation proof. (b) `value == false`: a `ruled_out` attestation MUST be present (the negative statement that the 6 kinds were checked and none fire); a bare `false` with no `ruled_out` is an unjustified skip → BLOCK back to EVALUATE. (c) Cross-check the profile: `value == true` under `docs`/`research` is a scope contradiction → BLOCK (wrong profile).
- [ ] Depth & seam analysis completed for new files (or "no new files, N/A" stated)
- [ ] UX review completed (or "no frontend files, N/A" stated)
- [ ] Blast radius trace completed (or "no infra/release/deploy files, N/A" stated)
- [ ] Inverse operations checked (or "no state transitions, N/A" stated)
- [ ] Cross-file consistency checked (or "no related files found, N/A" stated)
- [ ] Neighborhood review done (or "single-function change, N/A" stated)
- [ ] Blocking constraints checked (or "project has no ## Blocking Constraints section, N/A" stated)

## Fix loop — what gets fixed in REVIEW, what goes back
| Finding | Action |
|---------|--------|
| Litmus FAIL | Back to BUILD with the failed HF + rework instructions (`litmus.md`) |
| Spec BLOCK | Back to BUILD with the MISSING/MISUNDERSTOOD ACs (`spec-compliance.md`) |
| Security confidence ≥ 8 + Critical/High | Auto-fix now (mechanical) |
| Security confidence 5-7 | Warning only (taste decision, logged) |
| UX1-UX5 finding | Auto-fix now |
| Blast-radius ❌ | Fix before advancing to TEST |
| Missing inverse operation | P0 — fix now |
| Blocking-constraint or RP violation | Blocking — fix now |
| 0-caller symbol | Wire it, or document "deferred — caller planned for Phase X" |
| Cross-file drift / shallow module / hypothetical seam | WARN — sync or document; informational |
No finding is deferred to a follow-up: fix it in this stage or escalate it (Escalation
Routing Protocol). A BLOCK verdict means fix first, then re-verify; GO advances to TEST.

**Re-review rule.** Fixes made inside REVIEW are re-verified for the checks they touch
(re-run `spawn_prompt.py --patterns-for` on the files the fix changed, `--patterns-rows` for any
newly triggered row, redo the affected
checks, and update the matching artifact blocks). A return to BUILD (litmus FAIL or spec
BLOCK) re-enters REVIEW from step 1 with `stage-start` (the diff and `review_experts` are
recomputed); `litmus_fail_count` and `spec_fail_count` persist across attempts, and both
consume the REVIEW retry budget.

## Artifact (the `artifact` half of `/tmp/<R>/review.json`)
Required (validator BLOCKS if absent): `approved`, `litmus_gate`, `integration_trace`,
`runtime_patterns`, `findings_count`. Recommended: `findings`, `security_findings`,
`ux_review`, `operational_patterns`, `wire_test`. Also record `spec_compliance`, and each
conditional check's block when it ran: `code_intel`, `operational_context`,
`depth_analysis`, `seam_audit`, `inverse_operations`, `cross_file_consistency`,
`neighborhood_review`. Start from stage-start's `artifact_example` (enum values are
listed in its `enums`; write ONE literal value, never `"PASS|BLOCK"`):
`{"approved": true, "findings_count": N, "findings": [...], "litmus_gate": {"verdict":
"PASS", "hf_checked": [true, true, true, true], "soft_signal_count": 0, "weak_areas": [],
"evidence": "HF1: … HF2: … HF3: … HF4: …"}, "spec_compliance": {...}, "security_findings":
[], "integration_trace": {"checked": N, "clean": true, "details": "..."},
"runtime_patterns": {"checked": N, "violations": 0, "patterns": [...]}, "ux_review":
{"triggered": false, "checks": 5, "findings": []}, "wire_test": {"boundaries": N,
"verified": M, "findings": []}}`.
Record half: `{"decisions": [{"description", "classification", "reasoning"}]}`.
`python backend/scripts/artifact_cli.py schema --stage review` prints the full shape.

**Legacy path** (sibling skills only; the card teaches stage-complete):
`artifact_cli.py publish --project <P> --run-id <R> --type review --producer
s_autonomous-pipeline --summary "Review: <N findings>, <M auto-fixed>, <K integration
warnings>" --stage review --data '<artifact JSON>'` then
`artifact_cli.py advance --project <P> --state test --run-id <R>`.
