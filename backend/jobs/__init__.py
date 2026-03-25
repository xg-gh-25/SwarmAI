"""
Swarm Job System — Product-Level Package

Scheduler, executor, adapters, models, and self-tune engine for
background automation. Manages signal pipeline, agent tasks,
maintenance jobs, and user-defined recurring jobs.

Key components:
  - scheduler: Evaluates which jobs are due and executes them
  - executor: Routes jobs to handlers (signal_fetch, signal_digest, agent_task, script, maintenance)
  - models: Pydantic data models (Job, Feed, RawSignal, SchedulerState, etc.)
  - adapters/: Signal feed adapters (RSS, HN, GitHub releases, web search)
  - self_tune: Auto-evolves feed config from user context
  - cron_utils: Lightweight stdlib-only cron evaluator
  - dedup: URL + title-similarity signal deduplication
  - job_manager: CRUD operations for user jobs
  - system_jobs: Default system job definitions (code, not YAML)
"""
