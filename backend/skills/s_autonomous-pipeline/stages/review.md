# REVIEW

Run deterministic checks first. If they pass, dispatch exactly the `handouts` returned by `pipeline_v1.py review-start --project <P> --run-id <R>` in one parallel message.

R1 reviews the complete diff. After all answers, cluster findings by file and ±10 lines, fix once, then record the exact addressed findings with `pipeline_v1.py review-address --project <P> --run-id <R> --artifact <handoff.json>`. The command returns only the required R2 handouts; dispatch them together.

Behavior/contract findings close only through `pipeline_test.py --project <P> --run-id <R> --mode prove --round R1 --files <nodeid> --finding CF-1234abcd=<nodeid>`; repeat only on `continue: true`. For docs/design/security-policy or refuted findings after R2, call `pipeline_v1.py review-proof --project <P> --run-id <R> --artifact <proof.json>` where the file is exactly `{"pass": <n>, "proofs": [...]}` and each row is `{finding_id, evidence_type}` (`diff_assertion`) or `{finding_id, evidence_type, refs}` (`refuted`, concrete `path:line` refs); `pass` is the 1-based proof pass. The engine derives tree and diff facts and rejects caller-supplied verdict fields.

Required checks: DS-9b. Push checks: RV-1a, RV-2. RV-1c (the other experts' answers) is recorded only (v13 §B5). Do not invent extra experts or a third round.

Complete with `pipeline_v1.py stage-complete --project <P> --run-id <R>` and continue.
