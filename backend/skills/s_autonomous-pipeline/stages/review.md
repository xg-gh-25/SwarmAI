# REVIEW

Dispatch exactly the `handouts` of `"$SWARM_BACKEND_BIN" pipeline-v1 review-start --project <P> --run-id <R>` in one parallel message, each `prompt` unchanged (a changed prompt is not counted). If `next.why` says a spawn or answer was rejected, dispatch that handout's `spawn_prompt` again.

R1 reviews the complete diff. After all answers, fix the findings your own way and record them with `"$SWARM_BACKEND_BIN" pipeline-v1 review-address --project <P> --run-id <R> --artifact <handoff.json>` (findings within ±10 lines are one cluster; it names any you left out). Dispatch the R2 handouts it returns together.

A behavior/contract fix is proven by a test you ran: record it with `"$SWARM_BACKEND_BIN" pipeline-v1 report-tests --project <P> --run-id <R> --artifact -`, then give the item `"proof": {"evidence_type": "reported_test", "command": "<the command as in that report>"}`. It must pass on the current code; R2 judges whether it shows the fix. For docs/design/security-policy or refuted findings after R2, call `"$SWARM_BACKEND_BIN" pipeline-v1 review-proof --project <P> --run-id <R> --artifact <proof.json>` where the file is exactly `{"pass": <n>, "proofs": [...]}` and each row is `{finding_id, evidence_type}` (`diff_assertion`) or `{finding_id, evidence_type, refs}` (`refuted`, concrete `path:line` refs); `pass` is the 1-based proof pass; the engine derives every tree and diff fact.

RV-4: REVIEW completes only on code a review round saw; after a code change `review-start` hands out an incremental review. RV-5: every addressed finding reached an R2; `review-address` with `"items": []` dispatches a missing R2 or re-asks a wrong R2 word once. `pipeline-v1 findings` (same `--project`/`--run-id`) lists each finding's id, status and `close_with`; `next` lists the open ones. Audit checks: RV-1a, RV-2. No extra experts, no third round.

Complete with `"$SWARM_BACKEND_BIN" pipeline-v1 stage-complete --project <P> --run-id <R>` and continue.
