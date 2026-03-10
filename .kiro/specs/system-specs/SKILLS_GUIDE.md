# SwarmAI Skills Guide

Skills extend your AI agents with specialized capabilities — packaged as `SKILL.md` files that Claude discovers and invokes automatically based on your request.

---

## How Skills Work

1. Skills are directories containing a `SKILL.md` file with YAML frontmatter + Markdown instructions
2. When you enable a skill for an agent, it becomes available as a tool
3. Claude reads the skill's description and autonomously decides when to use it
4. The skill's instructions guide Claude on how to complete the task

```
User sends message → Claude matches skill by description → Invokes skill tool → Follows instructions → Returns result
```

---

## Skill Structure

### Minimal Skill (single file)

```
my-skill/
└── SKILL.md
```

### Multi-file Skill

```
pdf-processor/
├── SKILL.md           # Required — main skill definition
├── REFERENCE.md       # Optional supporting docs
└── scripts/
    └── fill_form.py   # Optional helper scripts
```

---

## SKILL.md Format

YAML frontmatter followed by Markdown instructions:

```yaml
---
name: pdf-processor
description: >
  Extract text, fill forms, merge PDFs.
  Use when working with PDF files, forms, or document extraction.
---

# PDF Processing

## Instructions
1. Use Read tool to open PDF files
2. Extract text using pdfplumber
3. Return structured output

## Requirements
- pypdf
- pdfplumber
```

### Frontmatter Fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Unique skill identifier (kebab-case) |
| `description` | Yes | What the skill does — Claude uses this to decide when to invoke it |

The description is critical — it's how Claude discovers your skill. Be specific about when it should be used.

---

## Three-Tier Skill System

Skills are discovered from three directories in precedence order (first-seen folder name wins):

| Tier | Location | Precedence | How Added |
|------|----------|------------|-----------|
| Built-in | `backend/skills/` | Highest | Ships with app, always projected |
| User | `~/.swarm-ai/skills/` | Medium | Uploaded via ZIP or created with AI |
| Plugin | `~/.swarm-ai/plugin-skills/` | Lowest | Installed via Plugin Manager |

### Skill Discovery via SkillManager

`SkillManager` scans all three tier directories, applies precedence, and maintains an in-memory cache:

```python
cache = await skill_manager.scan_all()
# Returns: dict[str, SkillInfo] keyed by folder_name
# SkillInfo: name, description, path, source_tier, is_builtin
```

Cache is invalidated on CRUD operations and rebuilt with an asyncio lock to prevent races.

### Skill Projection (ProjectionLayer)

Skills are projected as symlinks into the workspace for Claude SDK discovery:

```
~/.swarm-ai/skills/my-skill/SKILL.md
        ↓ symlink
~/.swarm-ai/SwarmWS/.claude/skills/my-skill → ~/.swarm-ai/skills/my-skill
        ↓ SDK discovery
setting_sources=["project"] → SDK scans {cwd}/.claude/skills/
```

Projection rules:
- Built-in skills: always projected unconditionally
- User/plugin skills: projected based on agent's `allowed_skills` list or `allow_all` flag
- Stale symlinks (skills no longer available) cleaned up on every projection pass
- Symlink targets validated against known tier directories before creation

---

## Managing Skills

### Upload a Skill (ZIP)

1. Go to the Skills management page
2. Click "Upload ZIP"
3. Select a ZIP file containing a skill directory with `SKILL.md`
4. The skill is extracted, registered in the database, and symlinked

### Create with AI (Skill Creator Agent)

1. Click "Create with Agent"
2. Describe what you want the skill to do
3. The AI generates a `SKILL.md` with appropriate instructions

Under the hood, `POST /api/skills/generate` calls `run_skill_creator_conversation()`:

- Creates a temporary agent config with `SKILL_CREATOR_SYSTEM_PROMPT_TEMPLATE`
- Invokes the built-in `skill-creator` skill for best-practice guidance
- Creates skills in `~/.swarm-ai/skills/` (user tier)
- Supports multi-turn iteration via session_id (same resume-fallback pattern as regular chat)
- Default model: `claude-sonnet-4-5-20250929`

### Install from Plugin Marketplace

1. Go to the Plugins page
2. Sync a marketplace repository (git-based)
3. Browse available plugins and install
4. Plugin skills are extracted to `~/.swarm-ai/plugin-skills/`

The `PluginManager` handles the full lifecycle:

- **Marketplace sync**: `sync_git_marketplace()` clones or pulls a git repo containing a `marketplace.json` manifest
- **Plugin install**: Extracts skill directories from the marketplace repo to `~/.swarm-ai/plugin-skills/`, writes `PluginMetadata` JSON
- **Plugin uninstall**: Removes skill directory and cleans up symlinks
- **Standalone detection**: Can detect a single skill repo as a plugin (no manifest needed)
- **Cache**: Marketplace data cached locally in `~/.swarm-ai/marketplace-cache/{marketplace_name}/`

### Enable Skills for an Agent

1. Go to Agent configuration
2. Check the skills you want to enable (or toggle "Allow All Skills")
3. Only enabled skills are available during chat — others are ignored

### Delete a Skill

1. Click the delete icon on the skill row
2. The skill files are removed from disk and the database record is deleted
3. Workspace symlinks are re-synced automatically

---

## Writing Effective Skills

### Description Tips

The `description` field determines when Claude uses your skill. Good descriptions:

```yaml
# ✅ Good — specific trigger conditions
description: >
  Generate PowerPoint presentations from outlines or data.
  Use when the user asks to create slides, presentations, or .pptx files.

# ❌ Bad — too vague
description: Help with documents
```

### Instruction Tips

- Be specific about which tools to use (Read, Write, Bash, etc.)
- Include step-by-step procedures
- Specify output formats and file paths
- Add safety rules for destructive operations
- Reference helper scripts if included

### Example: Code Review Skill

```yaml
---
name: code-review
description: >
  Review code for bugs, security issues, and best practices.
  Use when the user asks for a code review or wants feedback on their code.
---

# Code Review

## Instructions
1. Use Read tool to examine the specified files
2. Analyze for:
   - Logic errors and edge cases
   - Security vulnerabilities
   - Performance issues
   - Code style and readability
3. Provide structured feedback with severity levels
4. Suggest specific fixes with code examples

## Output Format
- Group findings by severity (Critical, Warning, Info)
- Include file path and line number for each finding
- Provide a summary at the end
```

### Example: Report Generator Skill

```yaml
---
name: weekly-report
description: >
  Generate weekly status reports from project data.
  Use when the user asks for a weekly report, status update, or progress summary.
---

# Weekly Report Generator

## Instructions
1. Gather data from the current project context
2. Identify completed tasks, in-progress work, and blockers
3. Generate a structured report with:
   - Executive summary (2-3 sentences)
   - Completed items
   - In-progress items with status
   - Blockers and risks
   - Next week priorities

## Safety Rules
- Never include sensitive credentials or API keys in reports
- Summarize rather than copy raw data
```

---

## Security

Skills run within the agent's security sandbox with multiple protection layers:

### PreToolUse Hook (Layer 4: skill_access_checker)

```python
# Only added when enable_skills=True AND allow_all_skills=False
def create_skill_access_checker(allowed_skills, builtin_skills):
    async def checker(tool_name, tool_input):
        skill_name = tool_input.get("skill_name")
        if skill_name in builtin_skills:
            return "allow"  # Built-in always allowed
        if skill_name not in allowed_skills:
            return "deny"   # Not in agent's allowed list
        return "allow"
```

### Additional Protections

- **Workspace isolation**: File access restricted to agent's sandbox directory
- **Bash command protection**: Regex blocks operations outside workspace boundary
- **Symlink validation**: Targets verified against known tier directories
- **System-managed folder protection**: HTTP 403 on delete/rename of system folders

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Skill not appearing in agent config | Check that `SKILL.md` exists and has valid YAML frontmatter |
| Skill not being invoked | Improve the `description` — Claude matches on this text |
| Skill invocation blocked | Verify the skill is enabled for the agent |
| Symlinks missing after upload | Skills are re-synced on CRUD operations; restart app if needed |
| Plugin skill shadowed | Built-in > user > plugin precedence; rename to avoid conflicts |
| Skill cache stale | CRUD operations auto-invalidate; restart app for manual refresh |
