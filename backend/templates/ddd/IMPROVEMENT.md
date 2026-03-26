# SwarmAI -- Lessons & Patterns

_This document captures what worked, what failed, and what to watch for when developing SwarmAI. Swarm updates this automatically after significant sessions. You can also edit it directly._

## What Worked

- **4-component session architecture (v7)** -- Replacing a monolithic manager with SessionRouter/SessionUnit/LifecycleManager/SessionRegistry gave clean separation and testability. Each component has a single job.
- **Filesystem-first for skills and context** -- No database for skills, no database for context files. Filesystem is portable, git-tracked, and human-readable. This choice has never been regretted.
- **Prevention over recovery** -- Timeouts, state guards, and guaranteed state transitions beat elaborate error handling. Making failure structurally impossible is better than catching it at runtime.
- **Property-based testing** -- Hypothesis tests catch edge cases that example-based tests miss. Especially valuable for workspace management, project CRUD, and context loading.
- **Strangler fig for large refactors** -- Running new code alongside old until behavior parity is verified prevents the cascade of bugs that big-bang rewrites cause.
- **Core Engine: design flywheels together, not as isolated features** -- Designing 6 subsystems as interconnected flywheels revealed 12 cross-flywheel feedback loops, 5 of which were completely missing. Map the loops FIRST, then build the components.
- **Core Engine: LLM for judgment, mechanical for checks** -- Using Haiku for weekly memory pruning produces smarter results than any date-based heuristic. "Is this still relevant?" is a judgment call, not a pattern match.
- **Core Engine: product-level from the start** -- Workspace-level code that's essential for the product is tech debt with a ticking clock.
- **Context file ownership model prevents brain corruption** -- Four explicit categories (system/user/agent/auto-gen) with enforced write access.
- **JSON file as inter-component data bus** -- `health_findings.json` (hook writes, briefing reads) is the standard integration pattern for Core Engine feedback loops. No coupling, survives restarts.
- **Git as ground truth for memory** -- Distillation verifies implementation claims against git log before promoting to MEMORY.md. Prevents self-reinforcing false memories.
- **Pipeline dog-fooding validates architecture** -- Using the pipeline to build itself proves the orchestration works. Self-validation is the strongest trust signal.
- **TDD catches naming mismatches early** -- RED phase catches API mismatches before code is written. Without TDD, these would be runtime production errors.

## What Failed

- **Big-bang refactor of 5,000+ line module** -- Deleting a large file before verifying all call sites were migrated caused 15+ bugs over 48 hours. No integration test gate before deletion. Fix: strangler fig is now mandatory for modules >500 lines.
- **Memory pipeline trusting its own output** -- DailyActivity captured mid-session missed later commits. Distillation froze stale snapshot into long-term memory. Fix: git cross-reference in capture + verified distillation.
- **Retry fighting resource exhaustion** -- Retrying a SIGKILL (OOM) with another spawn made things worse. Retry strategies must be failure-mode-aware: OOM != timeout != auth failure.
- **Hardcoded constants at wrong scale** -- 85% memory threshold worked at 200K context, broke at 1M. Constants need a comment: "this assumes X" so you know when the assumption breaks.
- **Sync wrappers around async cleanup** -- Async cleanup needs async callers. No shortcuts. The "convenience" sync wrapper leaked 3 file descriptors per crash.
- **Workspace scripts drift from codebase** -- Building features in `Services/` (workspace) instead of `backend/` (codebase) means new users don't get them, and the workspace version drifts. The slack-bot grew to 1,093 lines in workspace while the codebase adapter was a different 363-line implementation. Rule: prototype in workspace, productize in codebase within the same sprint.

## Known Issues

_These are known architectural decisions or technical debt, not bugs. They're documented so Swarm and contributors know about them._

- **Large files deferred** -- ChatPage.tsx, useChatStreamingLifecycle.ts, and session_unit.py are all ~2000 lines. Stable but large. Refactor when the next feature touches them, not before.
- **MCP Gateway deferred** -- Multiple sessions x multiple MCPs = many processes. A shared gateway would reduce memory. Deferred until SDK provides native support.

## Security History

_Notable security issues and their resolutions, so similar patterns are caught early._

_No entries yet. Swarm will add entries as security issues are discovered and resolved._
