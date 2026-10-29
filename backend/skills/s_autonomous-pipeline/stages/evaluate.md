# EVALUATE

Read `artifacts/knowledge.md` first: what the project DDD (PRODUCT, PROJECT) and the Library hold for this request, and a map of everything the DDD holds. It is a starting set, not the whole DDD: when a requirement depends on the domain, look it up with your own words (`"$SWARM_BACKEND_BIN" knowledge find --project <P> --query "<words>"`). Entries are leads; check them against the code. List the ids of entries you relied on in `knowledge_refs` (optional): an id from knowledge.md or from `knowledge find` / `get`; any other id is refused.

One requirement is one run, however many parts it has: list every part as a task in PLAN, do them all in BUILD, and REVIEW and DELIVER the whole change once. Make separate runs only for requirements that do not depend on each other (each useful and revertible on its own), or when the user asks for separate landings.

Write `artifacts/requirements.json` using the full REQUIREMENTS schema. Use structured EARS fields; do not write `REQUIREMENTS.md`.

Required checks: RQ-1, RQ-2, RQ-4 (bugfix), RQ-5, RQ-6, RQ-8. Whether each acceptance criterion is verifiable and its negative / universal flags match its text is judged by the Gate-0 reviewer, in any language (no word list); fix every acceptance id it names before completing EVALUATE.

Gate-0 is dispatched only for bugfix, or when `understanding.readings` has at least two reasonable readings. Before stage completion run `"$SWARM_BACKEND_BIN" pipeline-v1 gate0-start --project <P> --run-id <R> --artifact <json>`; if `required` is true, dispatch its handout `prompt` unchanged (four lines; the expert reads `PROMPT-FILE`). While its answer is missing, `stage-complete` returns `handouts` with each one's state and the `spawn_prompt` to dispatch again. A negative/unparsed first answer requires exactly one second `gate0-start`; no third round. `next` names whichever of these is due. Ask the user only through `decision-request` when the JSON passes DC-1, then follow INSTRUCTIONS "Asking a person".

Complete with `"$SWARM_BACKEND_BIN" pipeline-v1 stage-complete --project <P> --run-id <R> --artifact <json>` and continue.
