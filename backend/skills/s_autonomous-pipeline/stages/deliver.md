# DELIVER

Write `artifacts/reflect.json` through `"$SWARM_BACKEND_BIN" pipeline-v1 reflect-prepare --project <P> --run-id <R> --artifact <json>` with `{"lessons": [...]}` (zero lessons is valid), then request deterministic landing. Each lesson's `evidence` cites this run's event ids (`"$SWARM_BACKEND_BIN" pipeline-v1 events --project <P> --run-id <R>` lists them); the CLI finds the dedup candidates itself and RF-4 names them when a `relation` must point at one. A run with more than one repo: give each lesson `"repo": "<repo name>"`, the repo it is about (used when the project has no DDD doc for it; the lesson then goes to that repo's file in the shared Library).

Normal landing evaluates RF-1–RF-4 and LG-1, LG-3, LG-4 (LG-1: the TEST report is on the tree that lands). There is no push verdict: an open audit check (RV-2, RV-1a, TS-1, DS-1, DC-2) is one line in the REPORT's Quality cell and never blocks landing; whether to push is the user's call.

The renderer creates REPORT before commit-tree. Do not hand-edit `.aidlc/deliverables/<run_id>/REPORT.md`.

Run `"$SWARM_BACKEND_BIN" pipeline-v1 land --project <P> --run-id <R> --message <message>`; on a deterministic not-landed result continue to REFLECT and report the reason.

A research run lands nothing: after `reflect-prepare`, finish with `stage-complete` (it ends `no_code`).

For an abandoned, paused, or completed-not-landed run, use the explicit recovery commands: `resume`, `restore`, or `run-land --prepare`. Human landing is two-step: prepare returns a request ID; then run `"$SWARM_BACKEND_BIN" pipeline-v1 run-land --project <P> --run-id <R> --request <request-id> --message <message>`. The second command must pass the UI approval gate and consumes one tree-bound token; any failure requires a new prepare and approval.
