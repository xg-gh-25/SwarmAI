# EVALUATE

Write `artifacts/requirements.json` using the full REQUIREMENTS schema. Use structured EARS fields; do not write `REQUIREMENTS.md`.

Required checks: RQ-1, RQ-2, RQ-4 (bugfix), RQ-5, RQ-6, RQ-8. RQ-3 and RQ-8b are recorded only.

Gate-0 is dispatched only for bugfix, or when `understanding.readings` has at least two reasonable readings. Before stage completion run `pipeline_v1.py gate0-start --project <P> --run-id <R> --artifact <json>`; if `required` is true, dispatch its exact handout. A negative/unparsed first answer requires exactly one second `gate0-start`; no third round. Ask the user only through `decision-request` when the JSON passes DC-1, then follow INSTRUCTIONS "Asking a person".

Complete with `pipeline_v1.py stage-complete --project <P> --run-id <R> --artifact <json>` and continue.
