# THINK

Read `artifacts/knowledge_design.md` first: the project DDD's TECH and IMPROVEMENT entries and this repo's lessons, recalled for the request and REQUIREMENTS when EVALUATE completed, and a map of the DDD. Those were found with the request's words; the knowledge that decides a design is often found only with the words of the design itself. So once you have candidate approaches, search the DDD with their terms (`"$SWARM_BACKEND_BIN" knowledge find --project <P> --query "<words>"`, 2-3 searches) before choosing, and record what you searched in DESIGN_SPEC's optional `knowledge_checked` (`[{"query": ..., "result_ids": [...]}]`, `[]` if you searched nothing). Entries are leads; check them against the code, and list ids you rely on in DESIGN_SPEC's optional `knowledge_refs`.

Compare feasible approaches using code and existing evidence. Put alternatives and the chosen approach into the DESIGN_SPEC source prepared for PLAN.

If evidence cannot choose and the tradeoff is irreversible or high risk, submit one DC-1 decision with 2–3 options and consequences. Mechanical failures never become questions.

Complete with `"$SWARM_BACKEND_BIN" pipeline-v1 stage-complete --project <P> --run-id <R>` and continue.
