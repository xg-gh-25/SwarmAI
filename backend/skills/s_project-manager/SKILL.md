---
name: Project Manager
description: >
  Create, list, edit, and delete projects with DDD (Domain-Driven Design)
  structure. Each project gets 4 knowledge documents (PRODUCT.md, TECH.md,
  IMPROVEMENT.md, PROJECT.md) and an .artifacts/ directory for pipeline outputs.
  TRIGGER: "add project", "new project", "create project", "remove project",
  "delete project", "list projects", "my projects", "update project".
  DO NOT USE: for task/todo management within a project (use radar-todo).
  SIBLINGS: save-memory = permanent facts -> MEMORY.md | save-context = handoff docs.
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

#### Step 2: Validate

```bash
# Check no duplicate name in Projects/
test -e "Projects/ProjectName" && echo "EXISTS" || echo "AVAILABLE"
```

**If name already exists:** Tell the user and suggest an alternative.
**If name is "SwarmAI":** Tell the user this name is reserved for the default project.

#### Step 3: Create project directory and DDD files

```bash
mkdir -p "Projects/ProjectName/.artifacts"
```

Then create each DDD document using the Write tool. Use these templates, replacing `{ProjectName}` with the actual name:

**PRODUCT.md:**
```markdown
# {ProjectName} -- Product Context

## Vision

_What is this project and why does it exist? One paragraph._

## Strategic Priorities

1. _Priority 1_
2. _Priority 2_
3. _Priority 3_

## Success Criteria

- _How do you know this project is succeeding?_

## Non-Goals

- _What are you explicitly NOT doing?_
```

**TECH.md:**
```markdown
# {ProjectName} -- Technical Context

## Architecture

_System overview, key components, data flow._

## Stack

- **Language:** _e.g., Python 3.12, TypeScript 5_
- **Framework:** _e.g., FastAPI, Next.js_
- **Database:** _e.g., SQLite, PostgreSQL_
- **Testing:** _e.g., pytest, vitest_

## Codebase Location

_{codebase_path if provided, else: "Set this to your project's source path."}_

## Dev Commands

- **Start:** _e.g., npm run dev, ./dev.sh_
- **Test:** _e.g., pytest, npm test_
- **Build:** _e.g., npm run build_

## Conventions

_Naming, file structure, commit message format._

## Key Files

| Domain | Files |
|--------|-------|
| _..._ | _..._ |
```

**IMPROVEMENT.md:**
```markdown
# {ProjectName} -- Lessons & Patterns

## What Worked

_Patterns that succeeded. Will grow through usage._

## What Failed

_Patterns that failed, root causes, what to do instead. Will grow through usage._

## Known Issues

_Recurring problems to watch for._
```

**PROJECT.md:**
```markdown
# {ProjectName} -- Current Context

## Current Focus

_What are you working on right now?_

## Open Items

- [ ] _Active work item_

## Recent Decisions

- _YYYY-MM-DD: Decision and rationale_

## Blocked By

_Nothing currently blocking._
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

#### Step 4: If codebase path was provided, enhance TECH.md

If the user provided a codebase path, scan it briefly:
```bash
# Check for common config files to auto-detect stack
ls "{codebase_path}/package.json" "{codebase_path}/pyproject.toml" "{codebase_path}/Cargo.toml" "{codebase_path}/go.mod" 2>/dev/null
```

Update TECH.md with detected stack info (language, framework, test runner).

#### Step 5: Confirm

> "Created project **{ProjectName}** with DDD structure. You can start by filling in PRODUCT.md (your vision and priorities) and TECH.md (your stack and codebase location). See `Projects/README.md` for a full guide."

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
- **DDD docs are optional but encouraged** — creating a project always generates templates. Users fill them in over time.
- **Each document owns its domain** — PRODUCT.md never estimates technical cost. TECH.md never judges business priority.
- **.artifacts/ is auto-managed** — don't manually create artifact files. The lifecycle pipeline handles this.
- **Project names** — PascalCase preferred, no spaces (use hyphens if needed).

## Verification

Before marking this task complete, show evidence for each:

- [ ] **Project directory created/updated** — `Projects/<Name>/` exists with the expected structure shown via `ls`
- [ ] **DDD docs present** — all four files (PRODUCT.md, TECH.md, IMPROVEMENT.md, PROJECT.md) exist and contain valid content or templates
- [ ] **Artifacts directory initialized** — `.artifacts/manifest.json` exists with correct project name and pipeline state
- [ ] **TECH.md populated** — if a codebase path was provided, stack auto-detection results are written to TECH.md
- [ ] **Confirmation shown** — user received the creation/update/deletion confirmation message with next-step guidance
