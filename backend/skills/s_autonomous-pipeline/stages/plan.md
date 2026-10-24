# PLAN

Write `artifacts/design_spec.json` (lite for trivial). Markdown is renderer-owned. direct has no PLAN.

Required checks: DS-2a, DS-4a (bugfix), DS-5, DS-6 (full), DS-7, DS-9a. DS-1 and DS-3 affect audit/push only.

Every task references R.m. Every executable negative/universal acceptance has a test or safe probe exit. Complete with `"$SWARM_BACKEND_BIN" pipeline-v1 stage-complete --project <P> --run-id <R> --artifact <json>` and continue.
