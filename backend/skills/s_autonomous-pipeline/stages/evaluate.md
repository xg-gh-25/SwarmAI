# EVALUATE

Read `artifacts/knowledge.md` first: what the project DDD (PRODUCT, PROJECT) and the Library hold for this request. They are leads; check them against the code. List the ids of entries you relied on in `knowledge_refs` (optional; an id not in that file is refused).

If the request has parts that can each be committed and verified on their own, make each part its own run, one after another; do not finish one large run and split it afterwards.

Write `artifacts/requirements.json` using the full REQUIREMENTS schema. Use structured EARS fields; do not write `REQUIREMENTS.md`.

Required checks: RQ-1, RQ-2, RQ-4 (bugfix), RQ-5, RQ-6, RQ-8. Whether each acceptance criterion is verifiable and its negative / universal flags match its text is judged by the Gate-0 reviewer, in any language (no word list); fix every acceptance id it names before completing EVALUATE.

Gate-0 is dispatched only for bugfix, or when `understanding.readings` has at least two reasonable readings. Before stage completion run `"$SWARM_BACKEND_BIN" pipeline-v1 gate0-start --project <P> --run-id <R> --artifact <json>`; if `required` is true, dispatch its handout `prompt` unchanged (four lines; the expert reads `PROMPT-FILE`). While its answer is missing, `stage-complete` returns `handouts` with each one's state and the `spawn_prompt` to dispatch again. A negative/unparsed first answer requires exactly one second `gate0-start`; no third round. `next` names whichever of these is due. Ask the user only through `decision-request` when the JSON passes DC-1, then follow INSTRUCTIONS "Asking a person".

Complete with `"$SWARM_BACKEND_BIN" pipeline-v1 stage-complete --project <P> --run-id <R> --artifact <json>` and continue.
