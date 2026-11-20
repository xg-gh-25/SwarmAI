"""
Swarm Job System — Product-Level Package

Scheduler, executor and models for background automation: agent tasks, scripts,
maintenance jobs and user-defined recurring jobs.

Key components:
  - scheduler: Evaluates which jobs are due and executes them
  - executor: Routes jobs to handlers (agent_task, script, maintenance, notify, ...)
  - models: Pydantic data models (Job, Feed, RawSignal, SchedulerState, etc.)
  - cron_utils: Lightweight stdlib-only cron evaluator
  - job_manager: CRUD operations for user jobs
  - system_jobs: Default system job definitions (code, not YAML)

The feed fetchers live in ``core/inbox/feeds`` (the Inbox's feed source); the feed
configuration stays in ``Services/swarm-jobs/config.yaml`` (``config_io``).
"""
