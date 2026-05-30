---
name: project-manager
description: "Create, list, edit, rename, and delete projects with DDD (Domain-Driven Design) structure. Each project gets 4 knowledge documents (PRODUCT.md, TECH.md, IMPROVEMENT.md, PROJECT.md) and an\
  \ .artifacts/ directory for pipeline outputs.\n  TRIGGER: \"add project\", \"new project\", \"create project\", \"rename project\".\n  NOT FOR: radar-todo use cases."
tier: always
---
# Project Manager

Manage projects in the SwarmWS workspace. Each project uses **DDD (Domain-Driven
Design)** structure with 4 knowledge documents that give Swarm deep understanding
of the project's domain.

## DDD Structure

Every project gets these files (templates with instructions for the user):

| File | Domain | Purpose |
|------|--------|---------|
| **PRODUCT.md** | Strategic alignment | Vision, priorities, success criteria, non-goals |
| **TECH.md** | Technical context | Architecture, stack, codebase location, dev commands, conventions |
| **IMPROVEMENT.md** | Historical patterns | What worked, what failed, known issues, security history |
| **PROJECT.md** | Current context | Current focus, open items, recent decisions, blockers |
| **.artifacts/** | Pipeline outputs | Research, design docs, changesets, reviews, test reports |

The **SwarmAI** project is the default project that ships with every installation.
It cannot be deleted but can be freely edited. It serves as a working example of
the DDD structure.

---

## Commands

### Create a New Project

User says something like:
- "Create project MyApp at ~/code/my-app"
- "New project ClientPortal"
- "Add project DataPipeline from /path/to/repo"

#### Step 1: Extract project name and optional codebase path

From the user's message, determine:
- **Name**: Short project name (PascalCase preferred, no spaces). If not given, derive from folder name.
- **Path** (optional): Absolute path to existing codebase. This goes into TECH.md, NOT as a symlink.

If the user doesn't provide a name, ask:
> "What would you like to name this project?"

**Name normalization:** If the provided name is not PascalCase (e.g., `ai_ready_repo`, `my-app`), note it informatively but accept their choice:
> "Note: Convention suggests `AiReadyRepo`. Using `ai_ready_repo` as specified."

#### Step 2: Validate

```bash
# Check no duplicate name in Projects/
test -e "Projects/ProjectName" && echo "EXISTS" || echo "AVAILABLE"
```

**If name already exists:** Tell the user and suggest an alternative.
**If name is "SwarmAI":** Tell the user this name is reserved for the default project.

#### Step 3: Gather context (BEFORE writing DDD docs)

**CRITICAL: Do NOT write blank templates. Gather available context first, then write populated docs.**

Gather from these sources in priority order:

**3a. Session conversation (highest priority):**
Scan the current conversation for project-related information:
- Decisions made about architecture, tech stack, audience → extract for DDD
- Non-goals discussed → PRODUCT.md
- Past failures mentioned → IMPROVEMENT.md
- Current work items mentioned → PROJECT.md

**3b. Codebase files (if path provided):**

Read these files (first 80 lines each, skip if not found):
```bash
# Package managers → Stack
cat "{codebase_path}/package.json" 2>/dev/null | head -50
cat "{codebase_path}/pyproject.toml" 2>/dev/null | head -50
cat "{codebase_path}/Cargo.toml" 2>/dev/null | head -30
cat "{codebase_path}/go.mod" 2>/dev/null | head -20

# README → Vision, description
cat "{codebase_path}/README.md" 2>/dev/null | head -80

# Docs → Architecture, design decisions
ls "{codebase_path}/docs/"*.md 2>/dev/null | head -5
cat "{codebase_path}/docs/design.md" 2>/dev/null | head -100
cat "{codebase_path}/docs/architecture.md" 2>/dev/null | head -100

# Existing AI context → Conventions (merge, don't overwrite)
cat "{codebase_path}/CLAUDE.md" 2>/dev/null | head -50
cat "{codebase_path}/.cursorrules" 2>/dev/null | head -30
ls "{codebase_path}/.kiro/steering/"*.md 2>/dev/null | head -5
cat "{codebase_path}/.ai-context/TECH.md" 2>/dev/null | head -80

# Build/CI → Dev commands
cat "{codebase_path}/Makefile" 2>/dev/null | head -30
cat "{codebase_path}/justfile" 2>/dev/null | head -30
ls "{codebase_path}/.github/workflows/"*.yml 2>/dev/null | head -5

# License
cat "{codebase_path}/LICENSE" 2>/dev/null | head -3

# Git log → Current activity (only if git repo)
test -d "{codebase_path}/.git" && git -C "{codebase_path}" log --oneline -10 2>/dev/null
```

**3c. Parse what you found:**

| Source | Extract → | Target Doc |
|--------|-----------|-----------|
| README.md first paragraph | Project description | PRODUCT.md Vision |
| README.md features/usage | Key capabilities | PRODUCT.md Priorities |
| package.json `dependencies` | Frameworks (react, express, etc.) | TECH.md Stack |
| package.json `scripts` | dev, test, build commands | TECH.md Dev Commands |
| pyproject.toml `[project.dependencies]` | Libraries | TECH.md Stack |
| pyproject.toml `[tool.pytest]` | Test framework | TECH.md Stack + Dev Commands |
| docs/design.md | Architecture overview | TECH.md Architecture |
| CLAUDE.md / .cursorrules | Conventions | TECH.md Conventions |
| .github/workflows/ | CI test/build commands | TECH.md Dev Commands |
| Makefile targets | Available commands | TECH.md Dev Commands |
| LICENSE | License type | PRODUCT.md (note) |
| git log -10 | Recent activity | PROJECT.md Current Focus |
| Session conversation | Decisions, goals, blockers | All 4 docs |

#### Step 4: Create project directory and write POPULATED DDD files

```bash
mkdir -p "Projects/ProjectName/.artifacts"
```

Now write DDD documents **pre-filled with extracted content**. For any field where NO context was found, use the placeholder template. For fields where context WAS found, write the actual extracted content.

**PRODUCT.md template (fill extracted fields, keep placeholders for unknowns):**
```markdown
# {ProjectName} -- Product Context

## Vision

{extracted_from_README_or_session OR "_What is this project and why does it exist?_"}

## Strategic Priorities

{extracted_priorities OR:
1. _Priority 1_
2. _Priority 2_
3. _Priority 3_}

## Success Criteria

{extracted_criteria OR "- _How do you know this project is succeeding?_"}

## Non-Goals

{extracted_non_goals OR "- _What are you explicitly NOT doing?_"}
```

**TECH.md template:**
```markdown
# {ProjectName} -- Technical Context

## Architecture

{extracted_from_docs_or_session OR "_System overview, key components, data flow._"}

## Stack

{extracted_from_package_json_or_pyproject:
- **Language:** Python 3.12
- **Framework:** FastAPI 0.104
- **Database:** SQLite
- **Testing:** pytest + vitest
OR the placeholder version}

## Codebase Location

{codebase_path}

## Dev Commands

{extracted_from_scripts_or_makefile:
- **Start:** npm run dev
- **Test:** pytest --timeout=60
- **Build:** npm run build
OR the placeholder version}

## Conventions

{extracted_from_CLAUDE_md_or_cursorrules OR "_Naming, file structure, commit message format._"}

## Key Files

| Domain | Files |
|--------|-------|
| {extracted key entry points OR "_..._" | "_..._"} |
```

**IMPROVEMENT.md template:**
```markdown
# {ProjectName} -- Lessons & Patterns

## What Worked

{extracted_from_session OR "_Patterns that succeeded. Will grow through usage._"}

## What Failed

{extracted_from_session OR "_Patterns that failed, root causes, what to do instead. Will grow through usage._"}

## Known Issues

{extracted_from_session OR "_Recurring problems to watch for._"}
```

**PROJECT.md template:**
```markdown
# {ProjectName} -- Current Context

## Current Focus

{extracted_from_git_log_or_session OR "_What are you working on right now?_"}

## Open Items

{extracted_from_session OR "- [ ] _Active work item_"}

## Recent Decisions

{extracted_from_session OR "- _YYYY-MM-DD: Decision and rationale_"}

## Blocked By

{extracted_from_session OR "_Nothing currently blocking._"}
```

**.artifacts/manifest.json:**
```json
{
  "project": "{ProjectName}",
  "pipeline_state": "think",
  "updated_at": "{ISO_TIMESTAMP}",
  "artifacts": []
}
```

**Quality check:** After writing, verify that at least ONE doc has non-placeholder content (if a codebase path was provided). If all 4 docs are still 100% placeholder despite having a codebase path, something went wrong in Step 3 — re-read the key files.

#### Step 5: Immediate PROJECTS.md refresh

Refresh PROJECTS.md immediately so the current session and sibling tabs see the new project:

```bash
# Regenerate PROJECTS.md inline (read all project dirs, rebuild the index)
python3 -c "
import sys
from pathlib import Path
sys.path.insert(0, str(Path.home() / 'Desktop/SwarmAI-Workspace/swarmai/backend'))
from core.swarm_workspace_manager import swarm_workspace_manager
import asyncio
asyncio.run(swarm_workspace_manager.refresh_projects_index(str(Path.home() / '.swarm-ai/SwarmWS')))
print('PROJECTS.md refreshed')
" 2>/dev/null || echo "Auto-refresh unavailable — will update on next session start"
```

#### Step 6: Optional — create .ai-context/ in target repo

If the user said `--with-ai-context` or the conversation indicates they want the repo itself to be AI-ready (e.g., the project IS about making repos AI-ready), offer:

> "Want me to also create `.ai-context/` in the target repo? This makes the repo itself AI-ready for Claude Code / Kiro. (The DDD in SwarmWS tracks our internal development; `.ai-context/` in the repo is what other tools read.)"

If yes: copy the same populated content into `{codebase_path}/.ai-context/` with the same 4 files (minus .artifacts/ — that's SwarmWS-only).

If the user didn't mention it: skip this step silently (don't ask every time).

#### Step 7: Confirm

> "Created project **{ProjectName}** with DDD structure.
> {populated_summary: e.g., "Pre-filled from README.md (vision), package.json (TypeScript + React + FastAPI), and 3 session decisions."}
> Remaining TODOs: {list any sections still placeholder}
> PROJECTS.md updated — visible to all tabs."

---

### List Projects

User says: "List my projects", "What projects do I have?", "Show projects"

```bash
ls -d Projects/*/  2>/dev/null | while read dir; do
  name=$(basename "$dir")
  if [ -f "$dir/PRODUCT.md" ]; then ddd="DDD"; else ddd="no DDD"; fi
  if [ -f "$dir/.artifacts/manifest.json" ]; then
    state=$(python3 -c "import json; print(json.load(open('$dir/.artifacts/manifest.json'))['pipeline_state'])" 2>/dev/null || echo "unknown")
  else
    state="-"
  fi
  echo "$name | $ddd | $state"
done
```

Present as a table:

| Project | DDD Status | Pipeline | Type |
|---------|-----------|----------|------|
| SwarmAI | Full | plan | Default |
| ClientApp | Partial (TECH.md only) | build | User |

---

### Edit a Project

User says: "Update MyApp priorities", "Add a lesson to ClientApp"

1. Read the relevant DDD document
2. Apply the user's changes using the Edit tool
3. Confirm what was changed

DDD documents are plain markdown — any edit is valid.

---

### Rename a Project

User says: "Rename project CMHK_BIZ to CMHK_SalesIntel", "Rename project X to Y"

#### Rules:
- **SwarmAI project CANNOT be renamed.** It's the default project.
- **Always confirm before renaming:**
  > "This will rename **OldName** to **NewName** across the workspace. Proceed?"

#### Step 1: Validate new name
```bash
# Check no collision
test -e "Projects/NewName" && echo "COLLISION" || echo "AVAILABLE"
```

#### Step 2: Rename directory
```bash
mv "Projects/OldName" "Projects/NewName"
```

#### Step 3: Update DDD doc titles
Edit the `# OldName` heading in each of PRODUCT.md, TECH.md, IMPROVEMENT.md, PROJECT.md to use the new name.

#### Step 4: Update manifest
Edit `.artifacts/manifest.json` — change `"project": "OldName"` to `"project": "NewName"`.

#### Step 5: Update shared path constant (if applicable)
If the project is referenced in `backend/skills/_shared/project_paths.py` (e.g., `CMHK_PROJECT`), update that constant. Also update `backend/core/project_registry.py` if it has a named constant for this project.

#### Step 6: Trigger refresh
```python
# The context_health_hook will auto-detect Projects/ mtime change on next session.
# For immediate effect in current session, manually refresh:
from core.swarm_workspace_manager import swarm_workspace_manager
import asyncio
asyncio.run(swarm_workspace_manager.refresh_projects_index(str(root)))
```

#### Step 7: Confirm
> "Renamed **OldName** → **NewName**. DDD docs updated, manifest updated. PROJECTS.md and KNOWLEDGE.md will auto-refresh on next session (or call refresh manually)."

**Note:** Historical references in DailyActivity, Signals, Reports, and .artifacts/runs/ are NOT updated (they record what the name was at that time).

---

### Delete a Project

User says: "Remove project ClientApp", "Delete project Foo"

#### Rules:
- **SwarmAI project CANNOT be deleted.** If the user tries, respond:
  > "The SwarmAI project is the default project and can't be deleted. You can edit its DDD documents freely."
- For all other projects, **always confirm before deleting:**
  > "This will remove **ProjectName** and its DDD documents from Projects/. Proceed?"

```bash
# After confirmation — use trash (recoverable) not rm
mv "Projects/ProjectName" ~/.Trash/ 2>/dev/null || rm -rf "Projects/ProjectName"
```

---

## Rules

- **No symlinks** — projects are real directories with DDD documents. Codebase paths go in TECH.md.
- **SwarmAI is protected** — cannot be deleted or renamed. Can be edited.
- **Pre-fill over placeholder** — ALWAYS gather available context before writing DDD docs. Blank templates are the LAST RESORT when zero context exists.
- **Each document owns its domain** — PRODUCT.md never estimates technical cost. TECH.md never judges business priority.
- **.artifacts/ is auto-managed** — don't manually create artifact files. The lifecycle pipeline handles this.
- **Project names** — PascalCase preferred, no spaces (use hyphens if needed). Non-PascalCase accepted with informational note.
- **Read, don't ls** — when codebase path is provided, READ key files (README, package.json, docs/). Don't just check if they exist.
- **No secrets in DDD** — never copy credentials, API keys, passwords, or tokens from codebase files into DDD docs. If a file appears to contain secrets (AWS_SECRET, API_KEY, PASSWORD, TOKEN patterns), skip that content.
- **Immediate refresh** — after create/rename/delete, refresh PROJECTS.md immediately. Don't say "next session."
- **Don't overwrite existing .ai-context/** — if `--with-ai-context` and `.ai-context/` already exists in the target repo, warn and skip (don't overwrite).

## Verification

Before marking this task complete, show evidence for each:

- [ ] **Project directory created/updated** — `Projects/<Name>/` exists with the expected structure shown via `ls`
- [ ] **DDD docs present AND populated** — all four files exist. If codebase path provided: at least one doc has non-placeholder content extracted from repo files
- [ ] **Artifacts directory initialized** — `.artifacts/manifest.json` exists with correct project name and pipeline state
- [ ] **TECH.md populated** — if a codebase path was provided, stack auto-detection results (actual deps from package.json/pyproject.toml) are written to TECH.md
- [ ] **PROJECTS.md refreshed** — the new project appears in PROJECTS.md (not deferred to "next session")
- [ ] **Confirmation shown** — user received the creation confirmation with pre-fill summary and remaining TODOs
