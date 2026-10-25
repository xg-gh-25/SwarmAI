# REFLECT

Delivery is already available. Background processing judges and routes each lesson, records an outcome, and never changes the landing result.

RF-5 is recorded only. Exhaustion produces `reflect: degraded`, not a blocked delivery.

The `land` command does this before it returns, with no daemon: it judges each lesson, writes it to its destination (DDD doc, or the project's proposal queue; with no project or DDD doc for it, the repo's file `Knowledge/Library/pipeline-lessons/<repo>.md`), records the outcome, completes REFLECT (`reflect: degraded` when its bounded budget ends) and removes the run's worktree and branch. There is no command for you to run. If the run is still `reflecting` (that command stopped early), `next` is `"$SWARM_BACKEND_BIN" pipeline-v1 resume --project <P> --run-id <R>`, which finishes it.
