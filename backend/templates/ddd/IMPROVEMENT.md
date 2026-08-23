# SwarmAI -- Lessons & Patterns

> **This is section ② Knowledge of a six-section DDD** (① Identity ② Knowledge
> ③ Gates ④ Capabilities ⑤ Delivery ⑥ Refresher). This doc answers *"Have we tried?"* —
> what worked, what failed, what to watch for. Cultivation appends lessons here after
> significant sessions; you can also edit it directly. Replace the sample entries below
> with YOUR project's real lessons.

## What Worked

- **Component separation over a monolith** -- splitting a large manager into focused
  single-responsibility components gave clean boundaries and testability. Each piece has
  one job.
- **Filesystem-first for skills and context** -- no database for skills, no database for
  context files. The filesystem is portable, git-tracked, and human-readable.
- **Prevention over recovery** -- timeouts, state guards, and guaranteed state
  transitions beat elaborate error handling. Making a failure structurally impossible
  beats catching it at runtime.
- **Property-based testing** -- generative tests catch edge cases that example-based
  tests miss, especially for state machines and data-shape code.
- **Strangler-fig for large refactors** -- run new code alongside old until behavior
  parity is verified; this prevents the cascade of bugs big-bang rewrites cause.
- **Design flywheels together, not as isolated features** -- mapping the cross-component
  feedback loops FIRST surfaces the missing ones before you build.
- **LLM for judgment, mechanical for checks** -- "is this still relevant?" is a judgment
  call; a size/date heuristic is not. Use each where it fits.
- **Git as ground truth for memory** -- verify implementation claims against the git log
  before promoting them to long-term memory. Prevents self-reinforcing false memories.

## What Failed

- **Big-bang refactor of a large module** -- deleting a large file before verifying all
  call sites were migrated caused a cascade of bugs. Fix: strangler-fig is mandatory for
  large modules; never delete-first-fix-forward.
- **A pipeline trusting its own output** -- activity captured mid-session missed later
  work, and distillation froze a stale snapshot into long-term memory. Fix: cross-verify
  against git before promoting.
- **Retry fighting resource exhaustion** -- retrying an out-of-memory kill with another
  spawn made it worse. Retry strategy must be failure-mode-aware: OOM ≠ timeout ≠ auth.
- **Hardcoded constants at the wrong scale** -- a threshold tuned for a small context
  window broke at a large one. Constants need a comment naming their assumption, so you
  know when it breaks.
- **Workspace scripts drifting from the codebase** -- building an essential feature in a
  workspace scratch area instead of the product codebase means new installs don't get it
  and the two copies diverge. Prototype in the workspace, productize in the codebase.

## What to Watch For

_Risks, recurring patterns, and "keep an eye on this" observations that aren't outright
failures yet. DDD cultivation routes watch-for/risk lessons here — this section MUST
exist in the scaffold or cultivation auto-creates it per-project (template drift)._

_No entries yet. Entries are added as risks and patterns are observed._

## Known Issues

_Known architectural decisions or technical debt — not bugs. Documented so contributors
know about them._

_No entries yet._

## Security History

_Notable security issues and their resolutions, so similar patterns are caught early._

_No entries yet._
