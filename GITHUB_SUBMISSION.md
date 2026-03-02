# GitHub Submission Guide

Instructions for pushing SwarmAI to the GitHub repository.

## Prerequisites

- Git installed
- GitHub account with access to https://github.com/xg-gh-25/SwarmAI

## Setup Remote

```bash
cd /path/to/SwarmAI

# Add remote (skip if already configured)
git remote add origin https://github.com/xg-gh-25/SwarmAI.git
git remote -v
```

## Push

```bash
git push -u origin main
```

## Verify

1. Open https://github.com/xg-gh-25/SwarmAI
2. Confirm README.md renders with SwarmAI branding
3. Confirm no stale references in visible docs

## Repository Settings

- Description: "SwarmAI — Your AI Team, 24/7"
- Topics: `ai`, `agent`, `claude`, `tauri`, `react`, `python`, `bedrock`

## Create Release

```bash
git tag v1.0.0
git push origin v1.0.0
```

GitHub Actions will build release artifacts for macOS, Windows, and Linux.

## Troubleshooting

```bash
# Auth issues — use SSH or personal access token
git remote set-url origin git@github.com:xg-gh-25/SwarmAI.git

# Push rejected — fetch first
git fetch origin
git merge origin/main --allow-unrelated-histories
git push -u origin main
```
