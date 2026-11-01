# PLAN

Write `artifacts/design_spec.json` (lite for trivial). Markdown is renderer-owned. direct has no PLAN. Read `artifacts/knowledge_design.md` first (no THINK in trivial; search the DDD as THINK says when the change touches the domain). Ids the design relies on, from the knowledge files or from `knowledge find` / `get`, go in its optional `knowledge_refs`; searches in `knowledge_checked`.

Required checks: DS-2a, DS-4a (bugfix), DS-5, DS-6 (full), DS-7, DS-9a. DS-1 and DS-3 affect audit/push only.

Every task references R.m. Every executable negative/universal acceptance has a test exit. Complete with `"$SWARM_BACKEND_BIN" pipeline-v1 stage-complete --project <P> --run-id <R> --artifact <json>` and continue.

A valid example of this stage's document, with placeholders for your own values: `"$SWARM_BACKEND_BIN" pipeline-v1 template design_spec` (trivial: `design_spec-lite`).
