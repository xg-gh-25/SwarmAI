# REFLECT

Delivery is already available. Background processing judges and routes each lesson, records an outcome, and never changes the landing result.

RF-5 is recorded only. Exhaustion produces `reflect: degraded`, not a blocked delivery.

The `land` command does this before it returns, with no daemon: it sends the run's five-part report to CDF (source S3) with no model call, completes REFLECT (`reflect: degraded` when the report cannot be queued) and removes the run's worktree and branch. CDF's worker then checks each lesson and writes it where it declares: the project's DDD (PROJECT, IMPROVEMENT, TECH or PRODUCT, by topic) or TIMELINE, or the General Brain (`Knowledge/Library/<subject>.md`) when no project document fits. There is no command for you to run. If the run is still `reflecting` (that command stopped early), `next` is `"$SWARM_BACKEND_BIN" pipeline-v1 resume --project <P> --run-id <R>`, which finishes it.
