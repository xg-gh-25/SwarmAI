# Claude Agent SDK Skills Guide

This document provides comprehensive guidance on how to use Skills with the Claude Agent SDK.

## What are Skills?

Skills are modular capabilities that extend Claude's functionality by packaging expertise into discoverable, reusable components.

**Key characteristics:**
- **Model-invoked (automatic)**: Claude autonomously decides when to use them based on your request
- **Packaged as `SKILL.md` files**: Each Skill is a directory containing a `SKILL.md` file with YAML frontmatter and Markdown instructions
- **Three sources**: Personal Skills (`~/.claude/skills/`), Project Skills (`.claude/skills/` in your repo), and Plugin Skills
- **Discovery-based**: Claude finds Skills based on their descriptions matching your request context

## Skill Structure

### Simple Skill (single file)

```
.claude/c/pdf-processor/
└── SKILL.md
```

### Multi-file Skill

```
.claude/skills/pdf-processor/
├── SKILL.md           # Required - main skill definition
├── REFERENCE.md       # Optional supporting documentation
├── FORMS.md           # Optional additional instructions
└── scripts/
    ├── fill_form.py   # Optional helper scripts
    └── validate.py
```

## SKILL.md Format

The `SKILL.md` file uses YAML frontmatter followed by Markdown content:

```yaml
---
name: pdf-processor
description: Extract text, fill forms, merge PDFs. Use when working with PDF files, forms, or document extraction.
---

# PDF Processing

## Instructions
1. Use Read to open PDF files
2. Extract text using pdfplumber
3. Provide structured output

## Requirements
Requires: pypdf, pdfplumber
```

### Required Frontmatter Fields

| Field | Description | Constraints |
|-------|-------------|-------------|
| `name` | Skill identifier | Lowercase, letters/numbers/hyphens only, max 64 chars |
| `description` | What the skill does and when to use it | Critical for discovery, max 1024 chars |

### Optional Frontmatter Fields (Claude Code CLI only)

| Field | Description | Note |
|-------|-------------|------|
| `allowed-tools` | List of tools the Skill can use | Only works in Claude Code CLI, NOT in Agent SDK |

## Enabling Skills in Claude Agent SDK

### Python

```python
from claude_agent_sdk import query, ClaudeAgentOptions

options = ClaudeAgentOptions(
    cwd="/path/to/project",  # Project with .claude/skills/
    setting_sources=["user", "project"],  # REQUIRED: Load Skills from filesystem
    allowed_tools=["Skill", "Read", "Write", "Bash"]  # Enable Skill tool
)

async for message in query(
    prompt="Help me process this PDF document",
    options=options
):
    print(message)
```

### TypeScript

```typescript
for await (const message of query({
  prompt: "Help me process this PDF document",
  options: {
    cwd: "/path/to/project",  // Project with .claude/skills/
    settingSources: ["user", "project"],  // REQUIRED: Load Skills from filesystem
    allowedTools: ["Skill", "Read", "Write", "Bash"]  // Enable Skill tool
  }
})) {
  console.log(message);
}
```

### Key Requirements

1. **Add `"Skill"` to `allowed_tools`** - The Skill tool must be explicitly enabled
2. **Set `setting_sources`** - Must include `["user", "project"]` to load skills from filesystem
3. **Set correct `cwd`** - Must point to directory containing `.claude/skills/`

**Important**: By default, the SDK does NOT load filesystem settings. You MUST explicitly configure `setting_sources`/`settingSources` for Skills to be available.

## How Skills Work at Runtime

1. **Initialization**: SDK loads Skills from filesystem directories specified in `setting_sources`
2. **Discovery**: Skill metadata (name, description) is discovered at startup
3. **Context Matching**: When you submit a prompt, Claude analyzes whether available Skills match your request
4. **Autonomous Invocation**: Claude autonomously invokes the `Skill` tool if it determines a Skill is relevant
5. **Content Loading**: Full Skill content (SKILL.md + supporting files) is loaded only when triggered
6. **Execution**: Claude follows Skill instructions and uses allowed tools to complete the task
7. **Progressive Disclosure**: Supporting files are loaded on-demand to manage context efficiently

### Key Behaviors

- Skills are NOT explicitly called by users - Claude decides autonomously
- The quality of the `description` field directly impacts whether Claude discovers and uses your Skill
- Multiple Skills can compose for complex tasks
- Claude respects tool permissions defined in `allowed_tools` when using Skills

## Skill Storage Locations

| Location | Path | Scope |
|----------|------|-------|
| Personal Skills | `~/.claude/skills/` | Available across all projects |
| Project Skills | `.claude/skills/` | Available only in that project |
| Plugin Skills | From installed plugins | Varies by plugin |

## Platform Integration

### Backend Implementation

In the platform's `AgentManager`, skills are enabled conditionally:

```python
# backend/core/agent_manager.py
if enable_skills:
    if "Skill" not in allowed_tools:
        allowed_tools.append("Skill")
```

### Agent Configuration

Agents can be configured with skill associations:

```python
agent_config = {
    "id": "agent-1",
    "name": "Customer Service Bot",
    "skill_ids": ["skill-1", "skill-3"],  # References to skill IDs
    "allowed_tools": ["Bash", "Read", "Write"]
}
```

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/skills` | GET | List all available skills |
| `/api/skills/system` | GET | List only system-provided skills |
| `/api/skills/upload` | POST | Upload a skill as a ZIP package |
| `/api/skills/generate` | POST | AI-generate a new skill |
| `/api/skills/{id}` | DELETE | Delete custom skills (not system skills) |

### Chat Request Flow

```
Frontend ChatRequest
  ├─ enableSkills: boolean (default: false)
  └─ enableMCP: boolean (default: false)
         ↓
POST /api/chat/stream
    ├─ agent_id: string
    ├─ message: string
    ├─ enable_skills: boolean ← Passed to agent_manager
    └─ enable_mcp: boolean
         ↓
AgentManager.run_conversation()
    └─ _build_options(agent_config, enable_skills=True, enable_mcp=...)
        └─ if enable_skills:
             └─ allowed_tools.append("Skill")
         ↓
ClaudeAgentOptions
    └─ allowed_tools: ["Bash", "Read", "Write", "Skill", ...]
         ↓
ClaudeSDKClient(options=options)
    └─ Claude can now use the Skill tool
```

## Writing Effective Skills

### Best Practices for Descriptions

The `description` field is critical for skill discovery. Include:

- **What the skill does**: Clear explanation of capabilities
- **When to use it**: Trigger phrases and use cases
- **Keywords**: Terms users might mention that should trigger this skill

**Good example:**
```yaml
description: Extract text, fill forms, merge PDFs. Use when working with PDF files, forms, or document extraction. Handles invoice processing, form filling, and PDF manipulation.
```

**Bad example:**
```yaml
description: PDF helper
```

### Skill Content Guidelines

1. **Be specific**: Provide clear, step-by-step instructions
2. **Include examples**: Show expected inputs and outputs
3. **Document dependencies**: List required tools and packages
4. **Handle edge cases**: Address common failure scenarios

## Troubleshooting

### Skills Not Found

- Verify `setting_sources=["user", "project"]` is configured
- Check `cwd` points to directory containing `.claude/skills/`
- Ensure SKILL.md exists at correct path
- Verify directory structure is correct

### Skill Not Being Used

- Check `"Skill"` is in `allowedTools`
- Make description specific with keywords and triggers
- Verify YAML syntax is valid (proper indentation, no tabs)
- Test with explicit trigger phrases from description

### Tool Restrictions Not Working in SDK

- `allowed-tools` in SKILL.md only works with Claude Code CLI
- In SDK, use main `allowedTools` option to restrict all Skills
- Control tool access at the agent level, not skill level

## Example Skills

### Read-Only File Analyzer

```yaml
---
name: safe-file-reader
description: Read files without making changes. Use for read-only file access, code review, or file analysis.
---

# Safe File Reader

## Instructions
1. Use Read tool to access file contents
2. Use Grep to search for patterns
3. Use Glob to find files by pattern
4. NEVER modify any files

## Capabilities
- Read and analyze source code
- Search for patterns across files
- Generate reports about file contents
```

### Database Query Helper

```yaml
---
name: query-database
description: Execute SQL queries on the customer database. Use when users need to query data, generate reports, or analyze database contents.
---

# Database Query Helper

## Instructions
1. Parse user request to understand data needs
2. Generate appropriate SQL query
3. Execute query safely (SELECT only)
4. Format and present results

## Safety Rules
- Only execute SELECT queries
- Never modify or delete data
- Limit results to 1000 rows
```

### Report Generator

```yaml
---
name: generate-report
description: Create PDF reports from data sources. Use when users need formatted reports, summaries, or documentation.
---

# Report Generator

## Instructions
1. Gather data from specified sources
2. Apply appropriate formatting
3. Generate PDF using reportlab
4. Save to specified location

## Output Formats
- PDF (default)
- Markdown
- HTML
```

## References

- [Claude Code Skills Documentation](https://docs.anthropic.com/en/docs/claude-code/skills)
- [Claude Agent SDK Documentation](https://docs.anthropic.com/en/docs/claude-agent-sdk)
- [Agent Skills Best Practices](https://docs.anthropic.com/en/docs/agents-and-tools/agent-skills)
