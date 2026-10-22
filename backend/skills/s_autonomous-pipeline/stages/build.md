# BUILD

Implement DESIGN_SPEC tasks in dependency order (direct: the requirement given at create; it has no DESIGN_SPEC). Use TDD only for the files changed now:

`pipeline_test.py --project <P> --run-id <R> --mode tdd --files <up to 5 test files>`

Probe exits run with `pipeline_test.py --project <P> --run-id <R> --mode probe` (the DESIGN_SPEC argv only, §A2.5).

Required checks: DS-2b, DS-4b (bugfix), DS-9b, BX-1; direct only BX-2 (a change exists; unless it is pure-doc, every test the runner selects for it passed tdd on the current tree). Do not run full regression here. Fix a whole failure cluster in one pass, then re-run only affected tests.

Complete with `pipeline_v1.py stage-complete --project <P> --run-id <R>` and continue.
