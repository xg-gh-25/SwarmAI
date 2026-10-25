# REVIEW

Dispatch exactly the `handouts` of `"$SWARM_BACKEND_BIN" pipeline-v1 review-start --project <P> --run-id <R>` in one parallel message, each `prompt` unchanged (four lines; the expert reads `PROMPT-FILE`; a changed prompt is not counted). If `next.why` says a spawn or answer was rejected, dispatch that handout's `spawn_prompt` again.

R1 reviews the complete diff. After all answers, fix the findings your own way and record them with `"$SWARM_BACKEND_BIN" pipeline-v1 review-address --project <P> --run-id <R> --artifact <handoff.json>` (findings within ±10 lines are one cluster; it names any you left out). Dispatch the R2 handouts it returns together.

A behavior/contract fix is proven by a test you ran: record it with `"$SWARM_BACKEND_BIN" pipeline-v1 report-tests --project <P> --run-id <R> --artifact -`, then give the item `"proof": {"evidence_type": "reported_test", "command": "<the command as in that report>"}`. It must pass on the current code; R2 judges whether it shows the fix. For docs/design/security-policy or refuted findings after R2, call `"$SWARM_BACKEND_BIN" pipeline-v1 review-proof --project <P> --run-id <R> --artifact <proof.json>` where the file is exactly `{"pass": <n>, "proofs": [...]}` and each row is `{finding_id, evidence_type}` (`diff_assertion`) or `{finding_id, evidence_type, refs}` (`refuted`, concrete `path:line` refs); `pass` is the 1-based proof pass. The engine derives tree and diff facts and rejects caller-supplied verdict fields.

RV-4: REVIEW completes only on code a review round saw. If the code changed since (after R2, or TEST sent the run back), `review-start` hands out an incremental review of that change only. Push checks: RV-1a, RV-2. RV-1c is recorded only. Do not invent extra experts or a third round.

Complete with `"$SWARM_BACKEND_BIN" pipeline-v1 stage-complete --project <P> --run-id <R>` and continue.
