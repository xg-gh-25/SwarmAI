# DELIVER

Write `artifacts/reflect.json` through `"$SWARM_BACKEND_BIN" pipeline-v1 reflect-prepare --project <P> --run-id <R> --artifact <json>` with the five-part report, then request deterministic landing. The report has exactly the keys `delivered`, `decisions`, `corrections`, `practices`, `gaps`, each a list (any may be empty; a list of lessons under `lessons` is refused). Each row is one unit with no other keys:

- `delivered`: what the run produced, as `{"kind": "event", "when", "title", "what", "impact" or "outcome"}`, or a cognition unit.
- `decisions`: `{"kind": "cognition", "type": "decision", "scope", "title", "what", "why", "rejected_alternatives"}` (write `none` when there was no alternative).
- `corrections`: cognition of `type` `correction` or `pitfall`; a pitfall's `applicability` says when it bites.
- `practices`: cognition of `type` `guideline` or `process`.
- `gaps`: `{"kind": "gap", "gap_kind": debt | risk | deferred_finding | process_issue | follow_up, "scope", "title", "what"}`.

Every row's `evidence` cites this run's anchors (`"$SWARM_BACKEND_BIN" pipeline-v1 events --project <P> --run-id <R>` lists them). `scope` says where it belongs: `project:<P>` (its DDD, or the repo's shared file when the project has none), `world`, or `user`. A unit that replaces or follows an existing entry says so in `relation_hint` (`{"kind": "supersedes" | "derived_from", "target": <anchor>}`); RF-4 names the candidates when one is needed.

Normal landing evaluates RF-1–RF-4 and LG-1, LG-3, LG-4 (LG-1: the TEST report is on the tree that lands: if main moved since TEST, test the synced tree again and send it with `"$SWARM_BACKEND_BIN" pipeline-v1 report-tests --project <P> --run-id <R> --artifact -`). There is no push verdict: an open audit check (RV-2, RV-1a, TS-1, DS-1, DC-2) is one line in the REPORT's Quality cell and never blocks landing; whether to push is the user's call.

The renderer creates REPORT before commit-tree. Do not hand-edit `.aidlc/deliverables/<run_id>/REPORT.md`.

Run `"$SWARM_BACKEND_BIN" pipeline-v1 land --project <P> --run-id <R> --message <message>`. The engine computes code_tree, the open audit checks, land_intent, the REPORT, delivery_tree, the LG checks and commit-tree in a fixed order, and never pushes. On a deterministic not-landed result continue to REFLECT and report the reason.

A research run lands nothing: after `reflect-prepare`, finish with `stage-complete` (it ends `no_code`).

For an abandoned, paused, or completed-not-landed run, use the explicit recovery commands: `resume`, `restore`, or `run-land --prepare`. Human landing is two-step: prepare returns a request ID; then run `"$SWARM_BACKEND_BIN" pipeline-v1 run-land --project <P> --run-id <R> --request <request-id> --message <message>`. `run-land --prepare` takes `[--skip-tests] [--note ...]`. The second command asks the person; their approval is recorded once, bound to session, run and request, and the landing rechecks every tree. A failed action consumes the authorization: prepare and get a new approval. Snapshot deletion and finding acceptance use the same one-use authorization. Abandoned and not-landed work keeps its `run/<id>` branch, bundle and diff.
