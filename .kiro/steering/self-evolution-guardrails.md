---
inclusion: fileMatch
fileMatchPattern: ["backend/skills/s_self-evolution/*", "backend/context/EVOLUTION.md", "backend/context/EVOLUTION_CHANGELOG.jsonl", "backend/core/evolution_optimizer.py", "backend/core/evolution/*.py", "backend/hooks/evolution_maintenance_hook.py", "backend/jobs/run_evolution.py", "backend/jobs/system_jobs.py"]
---

# Self-Evolution Guardrails

## Core Invariant: Evolution Improves Stability, Not Complexity

Priority ordering (ADL Protocol): Stability > Interpretability > Reusability > Extensibility > Novelty.
The model proposes; code gates. Nothing in the evolution system may auto-write the
governed judgment files (SOUL / AGENT / STEERING) — it writes proposals for a human.

## The Three Evolution Paths (current)

| Path | Trigger | Code | Writes |
|------|---------|------|--------|
| Skill-optimization cycle (MINE→ASSESS→ACT→AUDIT) | Weekly system job `evolution-cycle` (Thu 07:00, 1800s timeout) — the ONLY trigger | `evolution_optimizer.run_evolution_cycle` via `jobs/run_evolution.py` | skill_health.json, `.evolution_proposals.json`, EVOLUTION.md + changelog |
| EVOLUTION.md maintenance | Session close (`EvolutionMaintenanceHook`, post-session hook) | `hooks/evolution_maintenance_hook.py` | EVOLUTION.md, monthly archive shard, changelog |
| Governance lifecycle | Prompt-driven | `s_self-evolution` skill (CLASSIFY/CAPTURE/PROMOTE/REFINE/RETIRE/COMPRESS) | EVOLUTION.md via Read + Edit, changelog |

Never put the cycle back on the session-close hook: it takes ~5 min against a 180s hook
budget and re-fired every session (run_6ac3fc0b).

## Cycle Guardrails (code-enforced, evolution_optimizer.py)

- Process flock on `.evolution_cycle.lock` (non-blocking, stale-lock break) — one cycle at a time.
- LLM cost cap: `evolution.max_llm_skills_per_cycle` (default 5), highest confidence first.
- Confidence tiers: HIGH → proposal/deploy, MED → recommendation, LOW → log only.
  HIGH needs ≥2 corrections, else it is demoted to MED.
- Regression gate: a deployed skill whose fitness dropped >0.1 is auto-reverted from
  `SKILL.md.bak`. Deploys are atomic (tmp + `os.replace` + verify + rollback).
- Freshness re-read before applying changes (the skill may have changed since MINE).
- Thresholds: `HIGH_CONFIDENCE = 0.15`, `MED_CONFIDENCE = 0.08` in `evolution_optimizer.py`
  are the ONLY source (not configurable; a test guards against a second source).
  Recalibrate them there, with data.

## Maintenance Hook (EvolutionMaintenanceHook.execute)

Read-only since the CDF cutover: it counts active `Capabilities Built` / `Competence
Learned` entries idle past 30 days with zero usage and logs the count. It never changes
EVOLUTION.md. The quality gate, correction folding, size valve and E/K lifecycle were
removed: CDF's ingestion writer is the only autonomous writer (design
2026-10-26 §8), and the prompt is bounded by whole-entry selection
(`core/memory_injection`, 8K tokens), not by evicting entries. Do not add a mutating step
back to this hook.

Every step is error-isolated and runs under the EVOLUTION.md flock; on lock timeout it
skips rather than clobbering. All mutations log to `EVOLUTION_CHANGELOG.jsonl`
(`rotate_jsonl_if_oversized`). Run order matters: fold before the valve, or the valve
over-archives.

## Write Rules

- Nothing edits EVOLUTION.md except CDF's ingestion writer. The skill submits a
  correction or a status change with `ingest submit --source S1` (s_self-evolution
  SKILL.md "Writing to EVOLUTION.md"); backend code enqueues through a CDF source (S5).
- EVOLUTION.md stays at P8 in `CONTEXT_FILES`, `user_customized=True` (copy-if-missing).

## Configuration Boundaries

`config.json["evolution"]`, read via `AppConfigManager.get("evolution")`.

- NEVER change `auto_approve_skills/scripts/installs` defaults to `true`.
- `max_llm_skills_per_cycle` (optional, default 5) is read via `AppConfigManager.instance()`.
- SkillMetrics priority candidates (`get_evolution_candidates`) lower the eligibility bar
  from 5 to 3 examples. This path only started working on 2026-10-17.
- `max_triggers_per_session`, `max_retries`, `max_active_entries`,
  `same_type_cooldown_seconds` are NOT read by backend code. They are prompt-level limits
  only, and `deprecation_days` is not wired into the hook (it uses its constructor default
  of 30). Don't describe them as code-enforced.

## Removed Code (don't reintroduce)

- The `<!-- EVOLUTION_EVENT -->` SSE marker chain was REMOVED on 2026-10-17 (no producer
  since 39756bd5). Don't reintroduce markers in agent text as a UI channel.
- `ToolFailureTracker` (`hooks/evolution_trigger_hook.py`) was never instantiated and was
  deleted on 2026-10-17. The live repeated-failure hint is `create_error_pattern_detector`
  + `create_failure_tracker_reset` in `core/runtime_hooks.py` (per-session
  `session_context["_failure_tracker"]`) — a generic hint, not an evolution trigger.

## Regression Checklist

- [ ] The cycle is triggered only by the scheduled job, never by a session hook
- [ ] Cycle lock, LLM cap, regression revert, and atomic deploy still in place
- [ ] EvolutionMaintenanceHook stays read-only (no write to EVOLUTION.md)
- [ ] EVOLUTION over budget is handled by injection selection, never by evicting entries
- [ ] Escalation writes proposals only; never SOUL/AGENT/STEERING
- [ ] `auto_approve_*` defaults remain `false`
- [ ] EVOLUTION.md still P8, `user_customized=True`
