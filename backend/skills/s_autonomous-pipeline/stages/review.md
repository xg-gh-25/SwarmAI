# REVIEW

Run deterministic checks first. If they pass, dispatch exactly the `handouts` returned by `"$SWARM_BACKEND_BIN" pipeline-v1 review-start --project <P> --run-id <R>` in one parallel message. Give each expert its `prompt` unchanged: its `PIPELINE-HANDOUT` line is how the expert's answer reaches the run, and a changed prompt is not counted.

R1 reviews the complete diff. After all answers, cluster findings by file and ±10 lines, fix once, then record the exact addressed findings with `"$SWARM_BACKEND_BIN" pipeline-v1 review-address --project <P> --run-id <R> --artifact <handoff.json>`. The command returns only the required R2 handouts; dispatch them together.

A behavior/contract finding's fix is proven by a test you ran: record its report with `"$SWARM_BACKEND_BIN" pipeline-v1 report-tests --project <P> --run-id <R> --artifact -`, then give the item `"proof": {"evidence_type": "reported_test", "command": "<the command as in that report>"}`. The command must pass on the current code; R2 judges whether it shows the fix (ADEQUATE / TEST_INADEQUATE). For docs/design/security-policy or refuted findings after R2, call `"$SWARM_BACKEND_BIN" pipeline-v1 review-proof --project <P> --run-id <R> --artifact <proof.json>` where the file is exactly `{"pass": <n>, "proofs": [...]}` and each row is `{finding_id, evidence_type}` (`diff_assertion`) or `{finding_id, evidence_type, refs}` (`refuted`, concrete `path:line` refs); `pass` is the 1-based proof pass. The engine derives tree and diff facts and rejects caller-supplied verdict fields.

RV-4: REVIEW completes only on code a review round saw. If the code changed since (after R2, or TEST sent the run back), `review-start` hands out an incremental review of that change only. Push checks: RV-1a, RV-2. RV-1c is recorded only. Do not invent extra experts or a third round.

Complete with `"$SWARM_BACKEND_BIN" pipeline-v1 stage-complete --project <P> --run-id <R>` and continue.
