# PLAN

Write `artifacts/design_spec.json`; direct also writes lite `artifacts/requirements.json`. Markdown is renderer-owned.

Required checks: RQ-1, RQ-2, RQ-8 (direct); DS-2a, DS-4a (bugfix), DS-5, DS-6 (full), DS-7, DS-9a. DS-1 and DS-3 affect audit/push only.

Every task references R.m. Every executable negative/universal acceptance has a test or safe probe exit. Complete with `pipeline_v1.py stage-complete --project <P> --run-id <R> --artifact <json>` and continue.
