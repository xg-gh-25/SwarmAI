# TEST

Run one final regression after the last code change. Repeat the identical command only when it returns `continue: true`.

Then run every required validation using `pipeline_test.py --project <P> --run-id <R> --mode validation --name <id>`.

TS-1 completes TEST for green, red, or incomplete final. TS-2 affects push. Never call red/incomplete green and never shrink risk expansion.

Complete with `pipeline_v1.py stage-complete --project <P> --run-id <R>` and continue.
