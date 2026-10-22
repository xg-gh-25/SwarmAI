# REFLECT

Delivery is already available. Background processing judges and routes each lesson, records an outcome, and never changes the landing result.

RF-5 is recorded only. Exhaustion produces `reflect: degraded`, not a blocked delivery.

The SwarmAI daemon does this after your next pipeline command: it judges each lesson, writes it to its destination (DDD doc, or the project's proposal queue), records the outcome, and completes REFLECT (`reflect: degraded` when its bounded budget ends). There is no command for you to run; `pipeline_v1.py status --project <P> --run-id <R>` shows when it is done.
