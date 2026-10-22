# BUILD

Implement DESIGN_SPEC tasks in dependency order. Use TDD only for the files changed now:

`pipeline_test.py --project <P> --run-id <R> --mode tdd --files <up to 5 test files>`

Required checks: DS-2b, DS-4b (bugfix), DS-9b, BX-1. Do not run full regression here. Fix a whole failure cluster in one pass, then re-run only affected tests.

Complete with `pipeline_v1.py stage-complete --project <P> --run-id <R>` and continue.
