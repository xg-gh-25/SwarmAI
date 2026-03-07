# SwarmAI Plugin Management — End-to-End Architecture

## Overview

SwarmAI's plugin system enables third-party skill distribution via git-based marketplaces. The `PluginManager` handles marketplace sync, plugin installation/uninstallation, and skill extraction. Installed plugin skills are projected into the workspace via `ProjectionLayer` for Claude SDK discovery.

The plugin system supports three formats:
1. **Marketplace repos** — git repos with `.claude-plugin/marketplace.json` listing multiple plugins
2. **Full plugins** — directories with `.claude-plugin/plugin.json` containing skills, commands, hooks, MCP servers
3. **Standalone skills** — simple directories with a SKILL.md or markdown file

---

## Architecture Diagram

```
┌──────────────────────────────────────────────────────────────┐
│                     PLUGIN LIFECYCLE                          │
│                                                               │
│  Git Repository                                               │
│  (marketplace or single plugin)                               │
│         │                                                     │
│         ▼                                                     │
│  ┌──────────────────────────────────────┐                    │
│  │  PluginManager.sync_git_marketplace  │                    │
│  │  • git clone --depth 1 (first sync)  │                    │
│  │  • git fetch + reset (subsequent)    │                    │
│  └──────────────┬───────────────────────┘                    │
│                 │                                             │
│                 ▼                                             │
│  ~/.claude/plugins/cache/{marketplace}/                       │
│  (local clone of marketplace repo)                           │
│         │                                                     │
│         ▼                                                     │
│  ┌──────────────────────────────────────┐                    │
│  │  PluginManager.install_plugin        │                    │
│  │  • Parse marketplace.json or detect  │                    │
│  │  • Copy skills/ to install dir       │                    │
│  │  • Copy commands/, hooks/, agents/   │                    │
│  └──────────────┬───────────────────────┘                    │
│                 │                                             │
│                 ▼                                             │
│  ~/.swarm-ai/plugin-skills/{skill-name}/                     │
│  (installed plugin skills)                                   │
│         │                                                     │
│         ▼                                                     │
│  ┌──────────────────────────────────────┐                    │
│  │  SkillManager.scan_all()             │                    │
│  │  • Discovers plugin-skills/ tier     │                    │
│  │  • Applies precedence (built-in >    │                    │
│  │    user > plugin)                    │                    │
│  └──────────────┬───────────────────────┘                    │
│                 │                                             │
│                 ▼                                             │
│  ┌──────────────────────────────────────┐                    │
│  │  ProjectionLayer.project_skills()    │                    │
│  │  • Symlinks into .claude/skills/     │                    │
│  │  • Validates targets in tier dirs    │                    │
│  │  • Cleans stale symlinks             │                    │
│  └──────────────────────────────────────┘                    │
│                 │                                             │
│                 ▼                                             │
│  SwarmWS/.claude/skills/{skill-name} → plugin-skills/...     │
│  (Claude SDK discovers via setting_sources=["project"])      │
└──────────────────────────────────────────────────────────────┘
```

---

## Data Models

### PluginMetadata

Parsed from `.claude-plugin/plugin.json`:

```python
@dataclass
class PluginMetadata:
    name: str
    version: str
    description: str = ""
    author: str = ""
    license: str = ""
    homepage: str = ""
    repository: str = ""
    keywords: list[str]
    skills: list[str]        # Skill directory names
    commands: list[str]      # Command file names
    agents: list[str]        # Agent config files
    hooks: list[str]         # Hook file names
    mcp_servers: list[dict]  # MCP server definitions
```

### InstallResult

Returned by `install_plugin()`:

```python
@dataclass
class InstallResult:
    success: bool
    plugin_id: Optional[str]
    name: str
    version: str
    description: str
    author: str
    installed_skills: list[str]
    installed_commands: list[str]
    installed_agents: list[str]
    installed_hooks: list[str]
    installed_mcp_servers: list[str]
    install_path: Optional[str]  # Path for Claude SDK
    error: Optional[str]
```

### AvailablePlugin

Returned by marketplace sync:

```python
@dataclass
class AvailablePlugin:
    name: str
    version: str
    description: str = ""
    author: str = ""
    keywords: list[str]
```

---

## Marketplace Sync (`sync_git_marketplace`)

```
sync_git_marketplace(marketplace_name, git_url, branch="main")
  │
  ├── Compute cache dir: ~/.claude/plugins/cache/{marketplace_name}/
  │
  ├── If .git exists in cache:
  │   ├── git fetch origin {branch}
  │   └── git reset --hard origin/{branch}
  │
  ├── If no .git:
  │   └── git clone -b {branch} --depth 1 {git_url} {cache_dir}
  │
  ├── Check for .claude-plugin/marketplace.json:
  │   │
  │   ├── EXISTS → Parse marketplace.json
  │   │   ├── Extract marketplace name
  │   │   ├── For each plugin entry:
  │   │   │   ├── Get explicit skills list
  │   │   │   └── Or auto-detect from source/skills/ directory
  │   │   └── Return SyncResult(is_marketplace=True, plugins=[...])
  │   │
  │   └── MISSING → Detect as single plugin
  │       ├── Check .claude-plugin/plugin.json → full plugin
  │       ├── Check skills/ directory → plugin with skills
  │       ├── Check for SKILL.md → standalone skill
  │       └── Fallback: scan skills/, plugins/, packages/ subdirs
  │
  └── Return SyncResult(plugins, is_marketplace, marketplace_name)
```

### marketplace.json Format

```json
{
  "name": "My Marketplace",
  "metadata": { "version": "1.0.0" },
  "owner": { "name": "Author Name" },
  "plugins": [
    {
      "name": "plugin-name",
      "description": "What it does",
      "version": "1.0.0",
      "source": "./",
      "skills": ["./skills/skill1", "./skills/skill2"]
    }
  ]
}
```

If `skills` is empty, auto-detection scans `{source}/skills/` for subdirectories.

### Source Types

Plugins can reference their source in two ways:
- **Local path**: `"source": "./"` or `"source": "./path/to/plugin"` — relative to marketplace root
- **Git URL**: `"source": {"source": "url", "url": "https://..."}` — cloned to `_sources/{plugin_name}/`

---

## Plugin Installation (`install_plugin`)

```
install_plugin(plugin_name, marketplace_name, version=None)
  │
  ├── Resolve cache dir: ~/.claude/plugins/cache/{marketplace_name}/
  │
  ├── Try marketplace.json-based install:
  │   ├── Search 3 locations for marketplace.json:
  │   │   ├── cache/.claude-plugin/marketplace.json
  │   │   ├── cache/skills/.claude-plugin/marketplace.json
  │   │   └── cache/plugins/.claude-plugin/marketplace.json
  │   ├── Find plugin entry by name
  │   ├── Resolve source directory (local path or git clone)
  │   ├── Get skill paths (explicit or auto-detect)
  │   ├── For each skill path:
  │   │   └── shutil.copytree(skill_src, ~/.claude/skills/{skill_name})
  │   └── Return InstallResult with installed_skills
  │
  ├── Fallback: search for plugin directory:
  │   ├── cache/{plugin_name}
  │   ├── cache/skills/{plugin_name}
  │   ├── cache/plugins/{plugin_name}
  │   └── cache/packages/{plugin_name}
  │
  ├── If .claude-plugin/plugin.json exists (full plugin):
  │   ├── Parse metadata
  │   ├── Copy skills/ → ~/.claude/skills/
  │   ├── Copy commands/ → ~/.claude/commands/
  │   ├── Copy agents/ → ~/.claude/agents/
  │   ├── Copy hooks/ → ~/.claude/hooks/
  │   ├── Parse .mcp.json for MCP servers
  │   └── Return InstallResult
  │
  └── If standalone skill (markdown files):
      ├── shutil.copytree(plugin_dir, ~/.claude/skills/{plugin_name})
      └── Return InstallResult
```

### Install Path

The `install_path` field in InstallResult is used by the Claude SDK:
- Git URL plugins: path to the cloned repo directory
- Local plugins: path to the first installed skill
- Used for SDK's plugin discovery mechanism

---

## Plugin Uninstallation (`uninstall_plugin`)

```python
async def uninstall_plugin(self, plugin_name, installed_skills,
                           installed_commands, installed_agents, installed_hooks):
    # Remove each installed component
    for skill_name in installed_skills:
        shutil.rmtree(~/.claude/skills/{skill_name})
    for cmd_name in installed_commands:
        (~/.claude/commands/{cmd_name}).unlink()
    for agent_name in installed_agents:
        (~/.claude/agents/{agent_name}).unlink()
    for hook_name in installed_hooks:
        (~/.claude/hooks/{hook_name}).unlink()
    return {"skills": [...], "commands": [...], ...}
```

After uninstall, `SkillManager.invalidate_cache()` triggers a rescan, and `ProjectionLayer` cleans up stale symlinks on the next projection pass.

---

## Three-Tier Skill Precedence

When a plugin skill has the same folder name as a built-in or user skill:

```
Precedence: built-in > user > plugin

SkillManager.scan_all() scans tiers in order:
  1. backend/skills/        (built-in)  ← wins
  2. ~/.swarm-ai/skills/    (user)      ← second
  3. ~/.swarm-ai/plugin-skills/ (plugin) ← last

First-seen folder name wins. Shadowed skills logged as warnings.
```

---

## ProjectionLayer — Symlink Projection

After installation, skills must be projected into the workspace for Claude SDK discovery:

```python
class ProjectionLayer:
    async def project_skills(self, workspace_path, allowed_skills, allow_all):
        skills_dir = workspace_path / ".claude" / "skills"

        for folder_name, info in cache.items():
            if info.source_tier == "built-in":
                target_skills[folder_name] = info.path  # Always project
            elif allow_all or folder_name in allowed_set:
                target_skills[folder_name] = info.path

        # Create/update symlinks
        for folder_name, skill_path in target_skills.items():
            if not self._validate_symlink_target(skill_path):
                continue  # Target outside known tier dirs
            link_path = skills_dir / folder_name
            link_path.symlink_to(skill_path.resolve())

        # Clean stale symlinks
        self._cleanup_stale_symlinks(skills_dir, target_names)
```

Symlink target validation: resolved path must fall within one of the three tier directories (built-in, user, plugin). Prevents symlink-based path traversal.

---

## Standalone Skill Detection

For repos without marketplace.json or plugin.json:

```python
def _detect_standalone_skill(self, skill_dir):
    # 1. Check skill.json metadata file
    # 2. Look for common markdown files: SKILL.md, README.md, {name}.md
    # 3. Extract description from first non-header line
    # 4. Return AvailablePlugin if markdown files found
```

---

## Storage Layout

```
~/.claude/                              # PluginManager base_dir
├── plugins/
│   └── cache/
│       └── {marketplace_name}/         # Git clone of marketplace repo
│           ├── .git/
│           ├── .claude-plugin/
│           │   └── marketplace.json
│           ├── skills/
│           │   ├── skill-a/
│           │   └── skill-b/
│           └── _sources/               # Cloned git URL plugin sources
│               └── {plugin_name}/
├── skills/                             # Installed plugin skills
│   ├── skill-a/
│   │   └── SKILL.md
│   └── skill-b/
│       └── SKILL.md
├── commands/                           # Installed plugin commands
├── hooks/                              # Installed plugin hooks
└── agents/                             # Installed plugin agents

~/.swarm-ai/
├── skills/                             # User-created skills (separate from plugins)
├── plugin-skills/                      # SkillManager plugin tier
└── SwarmWS/.claude/skills/             # Symlinks (ProjectionLayer)
    ├── skill-a → ~/.swarm-ai/plugin-skills/skill-a
    └── skill-b → ~/.swarm-ai/plugin-skills/skill-b
```

---

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/plugins/sync` | Sync a git marketplace |
| POST | `/api/plugins/install` | Install a plugin from cache |
| POST | `/api/plugins/uninstall` | Uninstall a plugin |
| GET | `/api/plugins/cached/{marketplace}` | List cached plugins |

---

## File Structure Reference

```
backend/core/
├── plugin_manager.py      # PluginManager, PluginMetadata, InstallResult, AvailablePlugin
├── skill_manager.py       # SkillManager (3-tier discovery, cache)
└── projection_layer.py    # ProjectionLayer (symlink projection)
```
