# DDD Templates

These templates define the output structure for AI-Ready-Repo Engine.
The LLM uses these as structural guidance during GENERATE phase.
The `render_agents_md()` function in `scripts/ai_ready_helpers.py` handles AGENTS.md directly.

## Files

- `PRODUCT.md.tmpl` — Purpose, audience, non-goals, constraints
- `TECH.md.tmpl` — Stack, architecture, conventions, invariants
- `IMPROVEMENT.md.tmpl` — What failed (WHEN/RISK/BECAUSE), what works, gotchas
- `PROJECT.md.tmpl` — Current priorities, recent decisions, blockers
