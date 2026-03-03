# SwarmAI Custom Agents Skill

## Overview

This skill provides comprehensive guidance for creating, configuring, and troubleshooting custom agents in SwarmAI. It covers all aspects of agent configuration including tools, permissions, resources, hooks, and MCP server integration.

## What's Included

- **Complete configuration reference** - All fields and options explained
- **Step-by-step instructions** - Creating agents via interactive, CLI, or manual methods
- **Common patterns** - Infrastructure specialist, development workflow, and code review agents
- **Troubleshooting guide** - Solutions for common issues
- **Visual diagrams** - Configuration flow, permission decision tree, and agent precedence
- **Quick reference tables** - Commands, file locations, and tool patterns
- **Best practices** - Security, organization, and workflow optimization

## When to Use This Skill

Use this skill when you need to:
- Create new custom agent configurations
- Configure tool access and permissions
- Set up MCP server integrations
- Manage agent resources and context
- Troubleshoot agent loading or tool access issues
- Optimize workflows with pre-approved tools
- Create project-specific or team-shared agents

## Key Topics Covered

### Configuration
- Agent configuration file structure
- Required and optional fields
- Local vs global agents
- Agent precedence rules

### Tools
- Built-in tool references
- MCP server tool integration
- Tool aliases for collision resolution
- Permission patterns and wildcards
- Tool-specific settings

### Resources
- Including local files and documentation
- Glob patterns for multiple files
- Path resolution (relative vs absolute)

### Hooks
- Agent lifecycle hooks (agentSpawn, userPromptSubmit, stop)
- Tool execution hooks (preToolUse, postToolUse)
- Hook configuration and parameters

### Troubleshooting
- Agent not found
- Invalid JSON syntax
- Tool availability issues
- Empty tools list
- MCP server problems
- Version conflicts

## Quick Start

To use this skill, agents should load it when users ask about:
- Creating SwarmAI custom agents
- Configuring agent tools or permissions
- Setting up MCP servers
- Troubleshooting agent issues
- Agent configuration best practices

## Validation

This skill has been validated using the ai-skill-builder validator and passes all checks:
- ✅ YAML frontmatter structure
- ✅ Required fields (name, description)
- ✅ Required sections (Overview, Usage, Core Concepts)
- ✅ Name format (lowercase, hyphens, max 64 chars)
- ✅ Description length (max 1024 chars)
- ✅ Required 'skill' tag
- ✅ Mermaid diagram syntax

## Related Documentation


## Version

1.0.0 - Initial release
