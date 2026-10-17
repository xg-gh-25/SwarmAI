# GOAL_CYCLE ref — inline vs scheduled mode
Read only when EVALUATE estimates >10 cycles or the user asks for overnight/unattended
execution. Default is **inline** (the loop runs in-session).
## Inline vs Scheduled Decision
| Factor | Inline | Scheduled |
|--------|--------|-----------|
| Goal completable in ~10 cycles | ✅ Use this | Overkill |
| Goal needs 30+ cycles | Too expensive | ✅ Use this |
| Context continuity matters | ✅ Full context | ❌ Fresh each time |
| Overnight/unattended | ❌ Needs session open | ✅ Runs via cron |
| Quality gates (REVIEW, ADVERSARIAL) | Integrated per cycle | Only at completion |
## Scheduled Mode (Job System)
> **⚠️ SwarmAI-SELF feature — NOT available to other projects.** Scheduled mode depends on
> SwarmAI's own job scheduler (`Services/swarm-jobs/`), which a non-SwarmAI project does
> not have. **If you are developing another project:** use **inline mode**, or wire the
> resume-cycle prompt below into YOUR OWN scheduler (an external `cron` entry, a CI cron
> trigger, a systemd timer, GitHub Actions `schedule:`) pointing at your own runner. The
> cross-session state carrier (the progress file) is portable; only the SwarmAI
> `user-jobs.yaml` mechanism is SwarmAI-specific. Do NOT write to the SwarmAI path from
> another project: it won't be picked up.
For goals spanning multiple sessions (SwarmAI-self), create a job after EVALUATE+PLAN:
```yaml
# SwarmAI-self ONLY — append to ~/.swarm-ai/SwarmWS/Services/swarm-jobs/user-jobs.yaml
jobs:
  - id: goal-<slug>
    name: "<goal description>"
    type: agent_task
    schedule: "0 */4 * * *"  # every 4 hours (adjust as needed)
    enabled: true
    category: user
    config:
      prompt: |
        Resume goal loop for SwarmAI project.
        Progress file: <progress_path>
        Steps:
        1. Read progress file
        2. Check DoD criteria — if ALL met, disable this job and notify owner
        3. If not met: execute ONE cycle (pick step, implement, test, update progress)
        4. Save progress and exit
        Constraints:
        - One cycle only per execution
        - Do not run REVIEW or ADVERSARIAL (those run at cycle boundaries inline)
        - If stuck (same state 3 runs): disable job and create Radar todo
      create_todos: false
    safety:
      max_budget_usd: 1.50
      timeout_seconds: 600
```
Scheduled mode loses context between executions. The progress file is the ONLY state
carrier (format: `ref/goal_cycle/findings-ledger.md` § Progress File Format); write it
clearly enough that a fresh agent can pick up where the last one left off.
