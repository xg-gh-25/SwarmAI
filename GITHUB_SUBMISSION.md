# GitHub Submission Guide

This guide provides step-by-step instructions for submitting the SwarmAI project to the new GitHub repository.

## Prerequisites

- Git installed on your system
- GitHub account with access to https://github.com/xg-gh-25/SwarmAI
- All rebranding changes completed and verified

## Step 1: Prepare Local Repository

```bash
# Navigate to project root
cd /path/to/SwarmAI

# Check if git is already initialized
git status

# If not initialized, run:
git init

# Remove old remote if it exists
git remote remove origin 2>/dev/null || true

# Add new remote
git remote add origin https://github.com/xg-gh-25/SwarmAI.git

# Verify remote is set correctly
git remote -v
```

## Step 2: Stage and Commit Changes

```bash
# Check current status
git status

# Stage all changes
git add .

# Commit with descriptive message
git commit -m "chore: Complete rebrand from Owork to SwarmAI

- Update product name to SwarmAI
- Update tagline to 'Your AI Team, 24/7'
- Update app identifier to com.swarmai.app
- Update all package names to swarmai
- Update version to 1.0.0
- Update GitHub repository URL to xg-gh-25/SwarmAI
- Remove legacy Owork assets
- Update all documentation to English
- Update data directory paths for all platforms"
```

## Step 3: Push to GitHub

```bash
# Check your current branch name
git branch

# Push to main branch (if your branch is named 'main')
git push -u origin main

# If your branch is named 'master', use:
git push -u origin master

# If you need to force push (use with caution):
# git push -u origin main --force
```

## Step 4: Verify Submission

1. Open https://github.com/xg-gh-25/SwarmAI in your browser
2. Verify all files are present
3. Check that README.md displays correctly with SwarmAI branding
4. Verify no "Owork" references are visible in main files
5. Check that the repository description shows "Your AI Team, 24/7"

## Verification Checklist

- [ ] All files uploaded successfully
- [ ] README.md shows SwarmAI branding and logo
- [ ] No "Owork" references in visible documentation
- [ ] GitHub URL references point to xg-gh-25/SwarmAI
- [ ] Version shows 1.0.0 in configuration files
- [ ] Legacy DMG file (Owork_0.0.1-beta_aarch64.dmg) is not present

## Troubleshooting

### Authentication Issues

If you encounter authentication errors:

```bash
# Use HTTPS with personal access token
git remote set-url origin https://<your-token>@github.com/xg-gh-25/SwarmAI.git

# Or use SSH
git remote set-url origin git@github.com:xg-gh-25/SwarmAI.git
```

### Push Rejected

If push is rejected due to remote changes:

```bash
# Fetch and merge remote changes first
git fetch origin
git merge origin/main --allow-unrelated-histories

# Then push
git push -u origin main
```

### Large File Issues

If you encounter large file errors:

```bash
# Check for large files
find . -type f -size +100M

# Consider using Git LFS for large files
git lfs install
git lfs track "*.dmg"
git add .gitattributes
```

## Post-Submission Steps

1. Set up GitHub repository settings:
   - Add repository description: "SwarmAI - Your AI Team, 24/7"
   - Add topics: `ai`, `agent`, `claude`, `tauri`, `react`, `python`
   - Configure branch protection rules if needed

2. Set up GitHub Actions secrets:
   - `ANTHROPIC_API_KEY` - For CI/CD builds

3. Create initial release:
   - Tag the commit: `git tag v1.0.0`
   - Push tag: `git push origin v1.0.0`
   - GitHub Actions will automatically build release artifacts

---

*Last updated: January 2025*
