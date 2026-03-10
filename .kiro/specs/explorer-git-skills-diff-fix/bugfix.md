# Bugfix Requirements Document

## Introduction

The Workspace Explorer has six related bugs that prevent git status from appearing on symlinked skill directories, prevent the "Show Changes" button from working for files already modified on disk, block skill files from opening as read-only, and lose gitStatus during the double-click flow. Together these bugs make skill-related git changes invisible, the diff feature non-functional for externally modified files, and skill content inaccessible.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN a skill directory under `.claude/skills/` is a symlink AND git reports it as an untracked flat path (e.g. `?? .claude/skills/s_code-review`) THEN the system fails to match the git status entry because `_build_tree()` only checks for child paths using a `prefix + "/"` match, which never matches the symlink's own flat path

1.2 WHEN skill content changes at the symlink target (PyInstaller temp dir) THEN the system reports zero git changes because git stores symlinks as mode `120000` (recording only the target path string, not the content), so unchanged symlink path means git sees no diff

1.3 WHEN no child entries under `.claude/skills/` have a git status (due to Bug 1.1) THEN the parent `.claude/skills/` directory also shows no git status indicator because directory status is derived exclusively from child prefix matches

1.4 WHEN a file has been modified on disk by the agent (has a `gitStatus` like "modified") AND the user opens it in FileEditorModal THEN the "Show Changes" button is always disabled because both `content` and `originalContent` are initialized to `initialContent` (the current disk content), making `isDirty` always false — the modal never fetches the git-committed version to compare against

1.5 WHEN a user double-clicks a file inside a skill directory under `.claude/skills/` (e.g. `.claude/skills/s_summarize/SKILL.md`) in the Workspace Explorer THEN the file cannot be opened because skill directories are symlinks that `entry.is_dir()` follows, and even after switching to `copytree()` the child files inside skill directories have no special handling to open them as read-only. Additionally, skill files should never be editable by users since they are managed by the system (projected from source tiers), but there is no mechanism to enforce read-only mode for files under `.claude/skills/`.

### Expected Behavior (Correct)

2.1 WHEN a skill directory under `.claude/skills/` is a symlink AND git reports it as an untracked flat path THEN the system SHALL check for a direct `rel_path` match in the `git_status` dict before falling through to the prefix scan, so the symlink entry itself is recognized and the directory node receives the correct git status

2.2 WHEN skill content changes at the symlink target THEN the system SHALL use `shutil.copytree()` (or equivalent file copy) instead of `symlink_to()` in `ProjectionLayer.project_skills()` so that git tracks actual file content and detects changes

2.3 WHEN any child entry or the directory's own flat path has a git status THEN the parent directory SHALL also display a git status indicator (automatically resolved when 2.1 is implemented)

2.4 WHEN a file with a non-null `gitStatus` is opened in FileEditorModal THEN the system SHALL fetch the last committed version of that file (via `git show HEAD:<path>`) from a backend endpoint and use it as `originalContent`, so the "Show Changes" button is enabled and the diff shows the difference between the committed version and the current disk content

2.5 WHEN a user double-clicks any file inside a skill directory under `.claude/skills/` in the Workspace Explorer THEN the system SHALL open that file in the FileEditorModal in read-only mode (view and diff only, no editing), since skill files are system-managed and projected from source tiers. The backend SHALL return `readonly: true` for any file whose path starts with `.claude/skills/`, and the `PUT /workspace/file` endpoint SHALL reject writes (HTTP 403) to any path under `.claude/skills/`.

2.6 WHEN a file with a non-null `gitStatus` is double-clicked in the Workspace Explorer THEN the `gitStatus` SHALL be preserved through the `toFileTreeItem()` conversion and passed to `openFileEditor()`, so the frontend can fetch the committed version for diff comparison.

### Unchanged Behavior (Regression Prevention)

3.1 WHEN a regular (non-symlink) file has a git status THEN the system SHALL CONTINUE TO display the correct git status on the file node in the explorer tree

3.2 WHEN a directory contains children with git status (non-symlink scenario) THEN the system SHALL CONTINUE TO display a "modified" indicator on the parent directory via the existing prefix scan logic

3.3 WHEN a file has no `gitStatus` and is opened in FileEditorModal THEN the system SHALL CONTINUE TO set `originalContent` to the current disk content and only enable "Show Changes" for edits made within the modal

3.4 WHEN skills are projected into `.claude/skills/` THEN the system SHALL CONTINUE TO respect tier precedence (built-in always projected, user/plugin gated by allowed_skills) and clean up stale entries

3.5 WHEN the user edits a file within the modal (making `isDirty` true) THEN the system SHALL CONTINUE TO enable the "Show Changes" button and compute the diff between `originalContent` and the edited `content`

3.6 WHEN `_get_git_status()` parses `git status --porcelain -z -uall` output THEN the system SHALL CONTINUE TO correctly map all status codes (untracked, modified, added, deleted, renamed, conflicting) to their respective status strings
