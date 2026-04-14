# Workspace Init

Bootstraps a workspace context file so SwarmAI agents can be productive in this project faster.

## Workflow

1. Scan the workspace for build configs, READMEs, existing agent rules, and project structure.
2. If a context file already exists, suggest improvements rather than rewriting from scratch.
3. Generate (or update) the file with the sections below.

## What to Include

- Common commands: build, lint, test, run a single test, dev server, etc.
- High-level architecture: the "big picture" that requires reading multiple files to understand.
- Key dependencies and their purposes.
- Important conventions or patterns used in the codebase.

## What to Avoid

- Repeating yourself or restating obvious dev practices.
- Listing every component or file when the structure is easily discoverable.
- Making up sections unless the repo's own docs explicitly include them.

## Formatting Rules

- Never use em-dashes (--). Use commas, semicolons, colons, or parentheses instead.

## Output

Write the context to the workspace's ContextFiles directory or as a project README enhancement, depending on what exists.
