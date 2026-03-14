---
name: Project Manager
description: >
  Add, remove, list, and describe projects in the SwarmWS workspace.
  Symlinks existing folders into Projects/ so they're visible without moving files.
  TRIGGER: "add project", "new project", "remove project", "list projects", "my projects", "link workspace", "connect workspace".
  DO NOT USE: for creating brand-new empty projects from scratch (just mkdir), or for task/todo management within a project.
  SIBLINGS: save-memory = permanent facts -> MEMORY.md | save-context = handoff docs.
---

# Project Manager

Manage projects in the SwarmWS workspace. Projects are symlinked from their original
location — no files are moved, no structure is changed. The original workspace stays
exactly as it is.

## Commands

### Add a Project

User says something like:
- "Add project AIDLC from /Users/me/workplace/aidlc"
- "Link my marketing workspace at ~/Documents/Marketing"
- "Connect /path/to/repo as ProjectName"

#### Step 1: Extract project name and source path

From the user's message, determine:
- **Name**: Short project name (e.g., "AIDLC", "Marketing", "Website"). If not given, derive from the folder name.
- **Path**: Absolute path to the existing folder. Expand `~` to the user's home directory.

If the user doesn't provide a path, ask:
> "What's the path to the project folder?"

If the user doesn't provide a name, use the last segment of the path (cleaned up):
- `/Users/me/workplace/my-cool-project` -> "my-cool-project"

#### Step 2: Validate

Run these checks using Bash:

```bash
# Check source path exists
test -d "/path/to/source" && echo "OK" || echo "NOT_FOUND"

# Check no duplicate name in Projects/
test -e "Projects/ProjectName" && echo "EXISTS" || echo "AVAILABLE"
```

**If source doesn't exist:** Tell the user the path wasn't found. Ask them to double-check.
**If name already exists:** Tell the user and suggest an alternative name, or ask if they want to replace it.

#### Step 3: Create symlink

```bash
ln -s "/absolute/path/to/source" "Projects/ProjectName"
```

Use absolute paths only. Never use relative paths for symlinks.

#### Step 4: Update PROJECTS.md

Read `.context/PROJECTS.md`, then use the Edit tool to add the new project under `## Active Projects`:

```markdown
### ProjectName
- **Status:** Active
- **Path:** `Projects/ProjectName/` -> `/absolute/path/to/source`
- **Added:** YYYY-MM-DD
```

If the user provides a description or goal, include it:
```markdown
### ProjectName — Short Description
- **Status:** Active
- **Goal:** User's description of the project
- **Path:** `Projects/ProjectName/` -> `/absolute/path/to/source`
- **Added:** YYYY-MM-DD
```

#### Step 5: Confirm

Tell the user it's done in one line:
> "Added **ProjectName** to your projects. It's visible at `Projects/ProjectName/`."

---

### Remove a Project

User says something like:
- "Remove project Marketing"
- "Unlink the AIDLC project"
- "Disconnect project Foo"

#### Step 1: Find the project

Check if the symlink exists in `Projects/`:

```bash
ls -la "Projects/ProjectName"
```

If it doesn't exist, list available projects and let the user pick.

#### Step 2: Confirm removal

**Always ask before removing**, even though it's just a symlink:
> "This will remove **ProjectName** from your SwarmWS projects. The original folder at `/path/to/source` stays untouched. Proceed?"

#### Step 3: Remove symlink

```bash
rm "Projects/ProjectName"
```

This removes ONLY the symlink. The original folder is never touched.

#### Step 4: Update PROJECTS.md

Use the Edit tool to remove the project's section from `.context/PROJECTS.md`.

#### Step 5: Confirm

> "Removed **ProjectName** from projects. Original files at `/path/to/source` are untouched."

---

### List Projects

User says something like:
- "List my projects"
- "What projects do I have?"
- "Show projects"

#### Step 1: Read current state

```bash
ls -la Projects/ | grep "^l\|^d" | grep -v "^\."
```

This shows both symlinked projects and regular directories.

#### Step 2: Format output

Present a clean table:

| Project | Path | Type |
|---------|------|------|
| AIDLC | /Users/me/workplace/aidlc | linked |
| SwarmAI | /Users/me/Desktop/SwarmAI | linked |

- **linked** = symlink to external path
- **local** = folder lives directly in Projects/

---

### Describe / Update a Project

User says something like:
- "Update AIDLC project description"
- "Set AIDLC goal to AI-driven development lifecycle"
- "Mark project X as paused"

Use the Edit tool to update the project's section in `.context/PROJECTS.md` with the new information.

---

## Rules

- **Never move files** — only create/remove symlinks
- **Never modify the target workspace** — the linked project's files stay untouched
- **Always use absolute paths** for symlinks
- **Always update PROJECTS.md** when adding or removing projects
- **Always confirm before removing** — even though it's just a symlink
- **Project names** — use PascalCase or the user's preferred casing, no spaces preferred (use hyphens)
- **PROJECTS.md location** — always at `.context/PROJECTS.md`
