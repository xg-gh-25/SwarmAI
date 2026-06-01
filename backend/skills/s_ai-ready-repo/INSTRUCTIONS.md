# AI-Ready-Repo Engine — Full Instructions

Generate DDD-structured artifacts that make any codebase genuinely understood by AI agents.

## Overview

**Input:** Repo path + optional signal sources (docs, wikis, Slack exports)
**Output:** `.ai-ready/` directory with 7 files + `AGENTS.md` entry point

**Phases:** INPUT → INGEST → UNDERSTAND → GENERATE (M1 scope)

## Prerequisites

Helper script: `backend/skills/s_ai-ready-repo/scripts/ai_ready_helpers.py`
- `gather_repo_info(path)` — git stats, file tree, tech stack detection
- `validate_code_intel_json(doc)` — v2 schema enforcement
- `parse_git_gotchas(path)` — evidence-grounded gotchas from git history
- `render_agents_md(data)` — template rendering (≤150 lines guaranteed)
- `build_ai_ready_meta(score, name)` — ai-ready.json metadata

## Workflow

### Phase 1: INPUT

Collect from user:
1. **Repo path** (REQUIRED) — local path or URL
2. **Signal sources** (OPTIONAL):
   - Design docs / PRDs (file paths or URLs)
   - Wiki / Confluence URLs
   - Existing CLAUDE.md / AGENTS.md
   - Verbal context ("we never deploy on Fridays")

Ask: "What repo do you want to make AI-ready? Also share any design docs, wikis, or context that would help me understand the project better."

If user provides only a path, proceed — engine gracefully degrades without signals.

### Phase 2: INGEST

Run the helper script to gather deterministic repo info:

```python
import sys
sys.path.insert(0, "backend/skills/s_ai-ready-repo/scripts")
from ai_ready_helpers import gather_repo_info, parse_git_gotchas

info = gather_repo_info(Path(repo_path))
gotchas = parse_git_gotchas(Path(repo_path))
```

Or via Bash:
```bash
python -c "
import sys, json
sys.path.insert(0, 'backend/skills/s_ai-ready-repo/scripts')
from pathlib import Path
from ai_ready_helpers import gather_repo_info, parse_git_gotchas
info = gather_repo_info(Path('REPO_PATH'))
gotchas = parse_git_gotchas(Path('REPO_PATH'))
print(json.dumps({'info': info, 'gotchas': gotchas}, indent=2, default=str))
"
```

Also read:
- README (first 200 lines — already in info['readme_content'])
- Config files (already in info['config_files'])
- Any user-provided signal documents (Read them directly)

### Phase 3: UNDERSTAND

Using the gathered info, analyze the codebase to determine:

1. **Module boundaries** — group files by directory structure + import patterns
2. **Entry points** — find main files, server starts, CLI commands, event handlers
3. **Conventions** — detect patterns from code (naming, error handling, test style)
4. **Architecture** — how modules relate (from imports and directory structure)
5. **Hot zones** — use git log data from `info['git_stats']`
6. **Route detection** — look for web framework patterns (FastAPI @app.route, Express router, etc.)

For each module detected, determine:
- `name` — directory name or logical grouping
- `path` — relative path from repo root
- `responsibility` — one sentence: what this module does
- `depends_on` — which other modules it imports from
- `depended_by` — which modules import from it
- `entry_points` — exported/public functions that serve as API surface

For routes (if web framework detected):
- `method` — HTTP method
- `path` — URL pattern
- `handler` — file:function reference
- `framework` — detected framework name

### Phase 4: GENERATE

Produce all output files. Use the templates from `backend/skills/s_ai-ready-repo/templates/` as structure reference.

**Output directory:** Create `.ai-ready/` at a working location (user specifies or default to `.artifacts/ai-ready-{project}/`)

#### 4.1: AGENTS.md (entry point)

Use `render_agents_md()` from the helper script:

```python
from ai_ready_helpers import render_agents_md

agents_content = render_agents_md({
    "project_name": "...",
    "build_command": "...",
    "test_command": "...",
    "lint_command": "...",
    "test_duration": "...",
    "modules": [...],
    "entry_points": [...],
    "critical_rules": [...],
    "gotchas": [...],
    "score": 7.5,
    "generated_date": "2026-06-01",
})
```

Write to `AGENTS.md` at output root. MUST be ≤150 lines.

#### 4.2: PRODUCT.md

Generate from README + user signals. Sections:
- **Purpose** — what problem, for whom (from README + user input)
- **Audience** — primary users/consumers
- **Non-Goals** — explicitly out of scope
- **Success Criteria** — measurable outcomes
- **Constraints** — regulatory, compliance, SLA, business rules

If no user input available for a section, write `[To be filled by product owner]`.
End with: `<!-- user: Your additions below — refresh preserves this section -->`

#### 4.3: TECH.md

Generate from code analysis. Sections:
- **Stack** — languages, frameworks, databases (from `info['tech_stack']`)
- **Architecture** — module map (matches code-intel.json modules)
- **Conventions** — prescriptive rules detected from code patterns
- **Key Decisions** — architectural choices visible in code + git history
- **Invariants** — things that must always be true

End with: `<!-- user: Your additions below — refresh preserves this section -->`

#### 4.4: IMPROVEMENT.md

Generate from git history + gotchas. Sections:
- **What Failed** — WHEN [trigger] → RISK [what breaks] → BECAUSE [evidence]
  (Use `gotchas` from helper script — every entry has commit hash evidence)
- **What Works** — patterns that appear stable (unchanged files with high usage)
- **Known Issues** — from open issues, TODO comments, or user signals

CRITICAL: Every entry in "What Failed" MUST have commit hash evidence.
Never generate gotchas without evidence — absence of evidence = absence of entry.

End with: `<!-- user: Your additions below — refresh preserves this section -->`

#### 4.5: PROJECT.md

Generate from recent git activity + user signals. Sections:
- **Current Priorities** — what's actively being worked on (from recent commits)
- **Recent Decisions** — major changes in last 30 days
- **Blocked By** — known blockers (from user signals or stale PRs)
- **Open Questions** — things agent should NOT decide unilaterally

End with: `<!-- user: Your additions below — refresh preserves this section -->`

#### 4.6: code-intel.json

Build the v2 document from UNDERSTAND phase analysis:

```python
from ai_ready_helpers import validate_code_intel_json

doc = {
    "$schema": "https://ai-ready-repo.dev/schemas/code-intel.v2.json",
    "version": "2.0",
    "generated_at": "...",
    "repo": { "name": "...", "languages": {...}, "total_symbols": N, "total_edges": M },
    "modules": [...],
    "edges": [...],
    "entry_points": [...],
    "routes": [...],
    "hot_zones": [...],
    "risk_areas": [...],
    "dead_code": [],
    "dependencies": {...},
}

# MANDATORY: validate before writing
errors = validate_code_intel_json(doc)
if errors:
    # Fix errors before writing — never write invalid JSON
    raise ValueError(f"code-intel.json validation failed: {errors}")
```

Write as formatted JSON (indent=2).

#### 4.7: ai-ready.json (metadata)

```python
from ai_ready_helpers import build_ai_ready_meta
meta = build_ai_ready_meta(score=calculated_score, project_name="...")
```

#### 4.8: REVIEW-REPORT.md

Generate a human-readable report covering:
- Overall AI-Ready Score (9 dimensions, 0-10 each)
- Per-file confidence (High/Medium/Low) and source attribution
- Review assignments (who should verify which file)
- Improvement recommendations (prioritized)
- Known gaps (what the engine couldn't determine)

### Phase 5: DELIVER (post-generation)

Present to user:
```
✅ AI-Ready artifacts generated for {project_name}

Output: {output_path}/
├── AGENTS.md (XX lines, score: X.X/10)
├── .ai-ready/
│   ├── PRODUCT.md
│   ├── TECH.md
│   ├── IMPROVEMENT.md
│   ├── PROJECT.md
│   ├── code-intel.json (N modules, M routes)
│   ├── ai-ready.json
│   └── REVIEW-REPORT.md

Next steps:
1. Review REVIEW-REPORT.md for confidence levels and gaps
2. Have PM review PRODUCT.md, engineer review TECH.md
3. Install to your IDE: copy to project root
```

## Scoring (9 Dimensions)

| Dimension | How to Score |
|-----------|-------------|
| Navigation | AGENTS.md quality + module map completeness |
| Build/Test | Build commands found + verified working |
| Architecture | Module boundaries clear + deps mapped |
| Conventions | Rules detected + prescriptive (not descriptive) |
| Tribal Knowledge | Gotchas with evidence (count × quality) |
| Code Graph | Modules + edges + entry points populated |
| Route Coverage | Routes detected / total endpoint references |
| Test Safety | Test files found + CI config present |
| Ops Context | Deploy info + monitoring + runbook references |

Score each 0-10. Overall = average. Minimum for "AI-Ready": 6.0.

## Boundaries

### Always
- AGENTS.md ≤ 150 lines
- code-intel.json passes validate_code_intel_json() before writing
- IMPROVEMENT.md gotchas have commit evidence (WHEN/RISK/BECAUSE grammar)
- All DDD files end with `<!-- user -->` preservation marker
- Works on ANY repo — no SwarmAI-specific paths

### Never
- Never generate gotchas without commit hash evidence
- Never write invalid code-intel.json (validate first)
- Never exceed 150 lines in AGENTS.md
- Never overwrite existing AGENTS.md without asking
- Never assume specific IDE or framework
