# Explorer Git Status & Skills Diff Bugfix Design

## Overview

Six interrelated bugs prevent git status from appearing on symlinked skill directories, prevent the "Show Changes" diff from working on files already modified on disk, block skill files from opening as read-only, and lose gitStatus during the double-click flow. The root causes are: (1) `_build_tree()` only does prefix-scan for directory git status, missing flat-path entries like symlinks; (2) `ProjectionLayer` uses `symlink_to()` so git tracks the pointer not the content; (3) parent directory status inherits only from children, so if children are missed the parent is too; (4) `FileEditorModal` initializes both `content` and `originalContent` to the current disk content, so `isDirty` is always false for externally modified files; (5) the frontend double-click handler only fires for `type === "file"` nodes, so skill directories (which contain a single `SKILL.md`) cannot be opened.

The fix strategy is minimal and surgical: add a direct-match check in `_build_tree()`, switch symlinks to `shutil.copytree()`, add a backend endpoint to serve the committed file version, enforce read-only and write-protection for skill files, and bridge the `gitStatus` gap in the double-click flow.

## Glossary

- **Bug_Condition (C)**: The set of conditions that trigger each of the six bugs — symlink flat-path mismatch, symlink mode 120000 hiding content changes, missing parent propagation, identical content/originalContent in the modal, skill files not read-only/write-protected, and gitStatus lost in the double-click flow
- **Property (P)**: The desired correct behavior — git status visible on skill directories, content diffs detected, parent directories reflecting child status, "Show Changes" enabled for externally modified files, and skill files openable as read-only with diff support
- **Preservation**: Existing behaviors that must remain unchanged — regular file git status, non-symlink directory propagation, modal behavior for clean files, tier precedence in skill projection, and all git status code parsing
- **`_build_tree()`**: Function in `backend/routers/workspace_api.py` (~line 268) that recursively builds the workspace explorer tree and assigns git status to directory/file nodes
- **`_get_git_status()`**: Function in `backend/routers/workspace_api.py` (~line 197) that runs `git status --porcelain -z -uall` and returns a `{relative_path: status}` dict
- **`ProjectionLayer.project_skills()`**: Method in `backend/core/projection_layer.py` that creates symlinks (to be changed to copies) from skill source directories into `SwarmWS/.claude/skills/`
- **`FileEditorModal`**: React component in `desktop/src/components/common/FileEditorModal.tsx` that opens files for editing and provides the "Show Changes" diff view
- **`originalContent`**: State variable in `FileEditorModal` representing the baseline for diff comparison — currently set to disk content, needs to be the git-committed version when `gitStatus` is present

## Bug Details

### Fault Condition

The bugs manifest across six related scenarios. The compound fault condition is:

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input of type {node: TreeNode, gitStatus: dict, fileEditorState: FileEditorState}
  OUTPUT: boolean

  // Bug 1: Symlink directory flat-path not matched
  bug1 := input.node.type == "directory"
          AND input.node.path IN input.gitStatus  // direct key exists
          AND NOT any(gpath.startswith(input.node.path + "/") for gpath in input.gitStatus)
          // prefix scan finds nothing, so directory gets no status

  // Bug 2: Symlink content changes invisible to git
  bug2 := input.node.path STARTS_WITH ".claude/skills/"
          AND isSymlink(input.node.resolvedPath)
          AND contentChanged(symlinkTarget(input.node.path))
          AND gitMode(input.node.path) == 120000  // git stores pointer, not content

  // Bug 3: Parent directory missing status (consequence of Bug 1)
  bug3 := input.node.type == "directory"
          AND hasChildWithBug1(input.node)
          AND input.node.gitStatus == null

  // Bug 4: FileEditorModal diff disabled for externally modified files
  bug4 := input.fileEditorState.gitStatus != null
          AND input.fileEditorState.content == input.fileEditorState.originalContent
          // both initialized to current disk content, isDirty is false

  // Bug 5: Skill files not openable and not enforced as read-only
  bug5 := input.node.path STARTS_WITH ".claude/skills/"
          AND input.node.type == "file"
          AND (NOT isOpenable(input.node) OR NOT isReadonly(input.node))
          // skill files should be viewable (read-only) with diff support

  // Bug 6: gitStatus lost during TreeNode → FileTreeItem conversion
  bug6 := input.node.gitStatus != null
          AND toFileTreeItem(input.node).gitStatus == undefined
          // gitStatus is not copied, so openFileEditor never receives it

  RETURN bug1 OR bug2 OR bug3 OR bug4 OR bug5 OR bug6
END FUNCTION
```

### Examples

- **Bug 1**: Skill directory `.claude/skills/s_code-review` is a symlink. `git status` reports `?? .claude/skills/s_code-review`. `_build_tree()` checks for paths starting with `.claude/skills/s_code-review/` — finds none (it's a flat entry, not a prefix). Directory node gets `dir_status = None`. Expected: node should show `gitStatus: "untracked"`.

- **Bug 2**: User edits a skill file at the PyInstaller temp dir target. The symlink at `.claude/skills/s_code-review` still points to the same path. Git sees mode `120000` (symlink) and the target path string hasn't changed, so `git diff` shows nothing. Expected: git should track actual file content and show modifications.

- **Bug 3**: Because Bug 1 causes `.claude/skills/s_code-review` to have no status, the parent `.claude/skills/` directory also shows no status (it derives status from children via prefix scan). Expected: parent should show `"modified"` indicator.

- **Bug 4**: Agent modifies `context/GUIDELINES.md`. File now has `gitStatus: "modified"` in the tree. User double-clicks to open it. `FileEditorModal` sets `content = initialContent` (disk content) and `originalContent = initialContent` (same disk content). `isDirty = false`. "Show Changes" button is disabled. Expected: `originalContent` should be the committed version from `git show HEAD:context/GUIDELINES.md`, making `isDirty = true` and enabling the diff.

- **Edge case**: File has `gitStatus: "untracked"` (new file, no committed version). `git show HEAD:<path>` would fail. Expected: `originalContent` should fall back to empty string `""`, showing the entire file as added.

- **Bug 5**: User double-clicks `.claude/skills/s_summarize/SKILL.md` (or any file inside a skill directory) in the Workspace Explorer. After the copytree fix, these are real files inside real directories, so they have `type: "file"` and the double-click handler fires. The backend `GET /workspace/file` already returns `readonly: true` for skill files, but the `PUT /workspace/file` endpoint has no write protection — a frontend bug could overwrite skill files. Also, the `is_skill_symlink` variable name and symlink escape hatch need updating after copytree migration. Expected: skill files open read-only with write protection on the PUT endpoint as defense-in-depth.

- **Edge case (Bug 5)**: Skill directory contains multiple files (e.g., `SKILL.md`, `scripts/helper.py`). All files under the skill directory should be openable and read-only, not just `SKILL.md`.

- **Bug 6**: Agent modifies `.context/STEERING.md`. The tree node has `gitStatus: "modified"`. User double-clicks the file. `toFileTreeItem(node)` converts the `TreeNode` to a `FileTreeItem` but does not copy the `gitStatus` field (it's not in the `FileTreeItem` interface). `handleFileDoubleClick(file)` calls `openFileEditor(file)` without gitStatus. The modal opens with no knowledge of git changes, so it doesn't fetch the committed version. "Show Changes" is disabled. Expected: `gitStatus` should flow through the entire chain so the committed version is fetched.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- Regular (non-symlink) files with git status continue to display correctly in the explorer tree via the existing `rel_path in git_status` check for file nodes
- Directories containing children with git status (non-symlink scenario) continue to display "modified" via the existing prefix scan logic
- Files with no `gitStatus` opened in FileEditorModal continue to set `originalContent` to current disk content, with "Show Changes" only enabled for in-modal edits
- Skill projection continues to respect tier precedence (built-in always projected, user/plugin gated by `allowed_skills`) and clean up stale entries
- In-modal edits (user types in textarea) continue to make `isDirty = true` and enable "Show Changes" comparing `originalContent` vs edited `content`
- `_get_git_status()` continues to correctly parse all status codes from `git status --porcelain -z -uall` output

**Scope:**
All inputs that do NOT involve: (a) directory nodes with flat-path git entries, (b) symlinked skill directories, (c) files with non-null `gitStatus` opened in the modal, or (d) skill directory double-clicks — should be completely unaffected by this fix. This includes:
- Regular file git status display
- Mouse/keyboard interactions in the explorer tree (non-skill directories)
- Modal behavior for files without git changes
- All other backend API endpoints
- Regular directory expand/collapse on double-click

## Hypothesized Root Cause

Based on the bug description and code analysis, the root causes are:

1. **Missing direct-match check in `_build_tree()` for directories**: The directory git status logic (line ~325 in `workspace_api.py`) only performs a prefix scan (`gpath.startswith(prefix)` where `prefix = rel_path + "/"`). When git reports a symlink directory as a flat path (e.g. `?? .claude/skills/s_code-review` without a trailing slash), the prefix scan never matches. File nodes have a direct `rel_path in git_status` check, but directory nodes do not.

2. **Symlinks hide content changes from git**: `ProjectionLayer.project_skills()` uses `link_path.symlink_to(skill_path.resolve())` (line ~143 in `projection_layer.py`). Git stores symlinks as mode `120000`, recording only the target path string. When skill content changes at the target location, the symlink path string is unchanged, so git reports no diff. The fix is to copy files instead of symlinking.

3. **Parent directory status depends on child status (transitive)**: The parent directory `.claude/skills/` gets its status from the prefix scan of its children. Since Bug 1 causes child skill directories to have no status, the parent also shows none. This is automatically resolved when Bug 1 is fixed.

4. **`FileEditorModal` never fetches committed version**: In `ThreeColumnLayout.tsx`, `openFileEditor()` reads the file via `api.get('/workspace/file')` which returns the current disk content. This is passed as `initialContent` to `FileEditorModal`, which sets both `content` and `originalContent` to this value. There is no code path that fetches the last committed version when `gitStatus` is present. The `isDirty` check (`content !== originalContent`) is always false.

5. **Skill files not enforced as read-only on write path**: The backend `GET /workspace/file` endpoint already returns `readonly: true` for skill files (via `is_skill_symlink` check at line ~498). However: (a) the `PUT /workspace/file` endpoint has zero write protection — it will happily overwrite skill files, and (b) after switching to `copytree()`, the `is_skill_symlink` variable name and the symlink-specific escape hatch (lines 468-479 that allow reading files outside the workspace root) need updating since skill files will be real files inside the workspace, not symlinks to external paths. The readonly check itself (`path.startswith(".claude/skills/")`) is correct and will continue to work after copytree.

6. **`gitStatus` lost in the double-click flow**: `TreeNode` has a `gitStatus` field, but `toFileTreeItem()` does not copy it to `FileTreeItem`, and `FileTreeItem` doesn't even have a `gitStatus` field. So when `handleFileDoubleClick(file: FileTreeItem)` calls `openFileEditor(file)`, the gitStatus is lost. The `openFileEditor` function has an optional `gitStatus` parameter, but it's never passed. Without gitStatus, the frontend can't know to fetch the committed version for diff comparison.

## Correctness Properties

Property 1: Fault Condition - Directory nodes with flat-path git entries receive correct status

_For any_ directory node in the workspace tree where the directory's relative path exists as a direct key in the `git_status` dict (i.e., `rel_path in git_status` is true), the fixed `_build_tree()` function SHALL assign the corresponding git status to that directory node, regardless of whether any child paths also exist in the status dict.

**Validates: Requirements 2.1, 2.3**

Property 2: Fault Condition - Skill projection uses file copies instead of symlinks

_For any_ skill projected into `.claude/skills/` by `ProjectionLayer.project_skills()`, the fixed function SHALL create a directory copy (via `shutil.copytree()`) instead of a symlink, so that git tracks actual file content and detects modifications to skill files.

**Validates: Requirements 2.2**

Property 3: Fault Condition - FileEditorModal fetches committed version for files with gitStatus

_For any_ file opened in `FileEditorModal` where `gitStatus` is non-null, the fixed system SHALL fetch the last committed version of the file (via `git show HEAD:<path>`) and use it as `originalContent`, enabling the "Show Changes" button to display the diff between the committed version and the current disk content. If the file is untracked (no committed version), `originalContent` SHALL be set to an empty string.

**Validates: Requirements 2.4**

Property 4: Preservation - Non-affected inputs produce identical behavior

_For any_ input where the bug condition does NOT hold — regular file nodes, directories with only child-prefix git entries, files without `gitStatus` opened in the modal — the fixed code SHALL produce exactly the same behavior as the original code, preserving all existing git status display, directory propagation, and modal editing functionality.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6**

Property 5: Fault Condition - Skill files open as read-only with diff support

_For any_ file under `.claude/skills/` opened in `FileEditorModal`, the backend SHALL return `readonly: true` so the modal opens in read-only mode (view and diff only, no editing). The `PUT /workspace/file` endpoint SHALL reject writes (HTTP 403) to any path under `.claude/skills/`. All files inside skill directories (not just `SKILL.md`) SHALL be openable via double-click. The "Show Changes" button SHALL still function for read-only files when `committedContent` differs from disk content.

**Validates: Requirements 2.5**

Property 6: Fault Condition - gitStatus preserved through double-click flow

_For any_ file node with a non-null `gitStatus` in the workspace tree, the `toFileTreeItem()` conversion SHALL preserve the `gitStatus` field, and `handleFileDoubleClick` SHALL pass it to `openFileEditor()`, so the frontend can determine whether to fetch the committed version for diff comparison.

**Validates: Requirements 2.6**

## Fix Implementation

### Changes Required

Assuming our root cause analysis is correct:

**File**: `backend/routers/workspace_api.py`

**Function**: `_build_tree()`

**Specific Changes**:
1. **Add direct-match check for directory git status**: Before the prefix scan loop, check if `rel_path` itself exists as a key in `git_status`. If it does, set `dir_status` to that value. Then fall through to the prefix scan which can upgrade to `"modified"` if children also have status.
   - Current code (line ~325): only does `prefix = rel_path + "/"` then scans
   - New code: add `if rel_path in git_status: dir_status = git_status[rel_path]` before the prefix scan
   - The prefix scan still runs and can set `dir_status = "modified"` if children have status (this handles the case where both the directory itself and its children have entries)

---

**File**: `backend/core/projection_layer.py`

**Function**: `ProjectionLayer.project_skills()`

**Specific Changes**:
2. **Replace `symlink_to()` with `shutil.copytree()`**: Change the symlink creation to a directory copy so git tracks actual content.
   - Add `import shutil` at the top of the module
   - Replace `link_path.symlink_to(skill_path.resolve())` with `shutil.copytree(str(skill_path.resolve()), str(link_path), dirs_exist_ok=True)`
   - The `dirs_exist_ok=True` parameter (Python 3.8+) handles idempotency — if the directory already exists, files are overwritten in place
   - Update the existing-symlink check: replace `link_path.is_symlink()` with `link_path.exists()` since entries are now real directories. For the "target changed" comparison, use a content hash or simply `shutil.rmtree()` + `copytree()` (the app already recreates on every launch, so a clean re-copy is acceptable)
   - Update `_cleanup_stale_symlinks()` to handle both symlinks (legacy) and real directories: use `shutil.rmtree()` for directories, `unlink()` for symlinks. Rename to `_cleanup_stale_entries()` for clarity.

---

**File**: `backend/routers/workspace_api.py`

**New Endpoint**: `GET /workspace/file/committed`

**Specific Changes**:
3. **Add endpoint to serve committed file version**: Create a new endpoint that runs `git show HEAD:<path>` and returns the committed content.
   - Accept `path` query parameter (relative to workspace root)
   - Validate the path (reuse `_validate_relative_path()`)
   - Run `subprocess.run(["git", "show", f"HEAD:{path}"], ...)` with timeout
   - Return `{"content": <committed_content>}` on success
   - Return `{"content": ""}` if the file is untracked (git returns error)
   - Return 404 if the file doesn't exist at all

---

**File**: `desktop/src/components/layout/ThreeColumnLayout.tsx`

**Function**: `openFileEditor()`

**Specific Changes**:
4. **Fetch committed version when gitStatus is present**: After reading the current file content, if `gitStatus` is truthy, make a second API call to `GET /workspace/file/committed` to get the committed version.
   - Add the committed content to `fileEditorState` as a new field (e.g., `committedContent`)
   - Pass it to `FileEditorModal` as a new prop (e.g., `committedContent`)

---

**File**: `desktop/src/components/common/FileEditorModal.tsx`

**Function**: `FileEditorModal()`

**Specific Changes**:
5. **Use committed version as originalContent when available**: Accept a new optional prop `committedContent`. In the `useEffect` that resets state when the modal opens, if `committedContent` is provided (not undefined), set `originalContent` to `committedContent` instead of `initialContent`.
   - Add `committedContent?: string` to `FileEditorModalProps`
   - In the reset effect: `setOriginalContent(committedContent ?? initialContent)`
   - This makes `isDirty = true` when the file has been modified on disk, enabling "Show Changes"
   - The diff will show committed version vs current disk content

---

**File**: `backend/routers/workspace_api.py`

**Function**: `get_workspace_file()` (the `GET /workspace/file` endpoint)

**Specific Changes**:
6. **Update skill file handling after copytree migration**: In the existing `GET /workspace/file` endpoint, the `is_skill_symlink` variable and the symlink escape hatch (lines 468-479) need updating:
   - Rename `is_skill_symlink` to `is_skill_file` (they're no longer symlinks after copytree)
   - The `readonly: true` return already works correctly via `path.startswith(".claude/skills/")` — no change needed there
   - Remove or simplify the symlink-specific escape hatch that allows reading files outside the workspace root (after copytree, skill files are inside the workspace)
   - After the copytree fix, skill files are real files inside real directories, so they naturally appear as `type: "file"` nodes in the tree and are openable via double-click without any VirtualizedTree changes

---

**File**: `backend/routers/workspace_api.py`

**Function**: `put_workspace_file()` (the `PUT /workspace/file` endpoint)

**Specific Changes**:
7. **Block writes to skill files**: Add a guard at the top of the PUT endpoint to reject writes to paths under `.claude/skills/`. Currently there is zero write protection — a frontend bug or determined user could overwrite skill files that would be lost on next projection pass.
   - Add check: `if path.startswith(".claude/skills/") or path.startswith(".claude\\skills\\"): raise HTTPException(status_code=403, detail="Skill files are read-only")`
   - This is defense-in-depth alongside the frontend readonly mode

---

**File**: `desktop/src/components/workspace-explorer/toFileTreeItem.ts` and `desktop/src/components/workspace-explorer/FileTreeNode.tsx`

**Specific Changes**:
8. **Bridge gitStatus from TreeNode to FileTreeItem**: The `gitStatus` field exists on `TreeNode` but is lost during conversion to `FileTreeItem`. Fix:
   - Add `gitStatus?: GitStatus` to the `FileTreeItem` interface in `FileTreeNode.tsx`
   - Add `gitStatus: node.gitStatus` to the mapping in `toFileTreeItem()`
   - In `ThreeColumnLayout.tsx`, pass `file.gitStatus` to `openFileEditor(file, file.gitStatus)`
   - This enables the frontend to know the file has git changes and fetch the committed version

---

**File**: `backend/routers/workspace_api.py`

**New Endpoint**: `GET /workspace/file/committed`

**Specific Changes**:
9. **Handle binary files gracefully**: When running `git show HEAD:<path>`, catch `UnicodeDecodeError` for binary files and return HTTP 400 with a clear error message. Use `subprocess.run(..., capture_output=True)` without `text=True`, then decode manually with error handling.

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate the bugs on unfixed code, then verify the fixes work correctly and preserve existing behavior.

### Exploratory Fault Condition Checking

**Goal**: Surface counterexamples that demonstrate the bugs BEFORE implementing the fix. Confirm or refute the root cause analysis. If we refute, we will need to re-hypothesize.

**Test Plan**: Write tests that exercise each bug scenario against the unfixed code to observe failures and confirm root causes.

**Test Cases**:
1. **Directory flat-path git status test**: Create a `git_status` dict with a flat directory entry (e.g., `{"dir/subdir": "untracked"}`). Call `_build_tree()` and assert the directory node has `git_status`. (Will fail on unfixed code — directory gets `None`)
2. **Symlink content tracking test**: Create a symlink via `ProjectionLayer.project_skills()`, modify content at the target, run `git status`. Assert the skill directory shows as modified. (Will fail on unfixed code — git sees unchanged symlink pointer)
3. **Parent directory propagation test**: With a flat-path entry for a child directory, assert the parent directory also gets a status. (Will fail on unfixed code — parent inherits nothing from statusless child)
4. **FileEditorModal diff for modified file test**: Render `FileEditorModal` with `gitStatus="modified"` and `initialContent` equal to disk content. Assert "Show Changes" button is enabled. (Will fail on unfixed code — `isDirty` is false)
5. **Skill file read-only test**: Open a file under `.claude/skills/` via `GET /workspace/file`. Assert the response includes `readonly: true`. (Should pass on current code — readonly check already exists. The real gap is the PUT endpoint having no write guard.)

**Expected Counterexamples**:
- `_build_tree()` returns `dir_status = None` for directories with flat-path git entries
- `ProjectionLayer` creates symlinks that git tracks as mode 120000
- `FileEditorModal` sets `originalContent === content`, making `isDirty` always false
- Possible causes confirmed: missing direct-match check, symlink mode, identical content initialization

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed functions produce the expected behavior.

**Pseudocode:**
```
FOR ALL input WHERE isBugCondition(input) DO
  // Bug 1: directory flat-path match
  IF input.node.type == "directory" AND input.node.path IN git_status THEN
    tree := _build_tree_fixed(input)
    ASSERT tree.node.git_status == git_status[input.node.path]
  END IF

  // Bug 2: skill content tracking
  IF input.node.path STARTS_WITH ".claude/skills/" THEN
    project_skills_fixed(input)
    ASSERT NOT isSymlink(input.node.path)
    ASSERT gitTracksContent(input.node.path)
  END IF

  // Bug 4: modal diff enabled
  IF input.fileEditorState.gitStatus != null THEN
    committedContent := fetchCommittedVersion(input.filePath)
    ASSERT originalContent == committedContent
    ASSERT isDirty == (committedContent != diskContent)
  END IF

  // Bug 5: skill files open as read-only
  IF input.node.path STARTS_WITH ".claude/skills/" AND input.node.type == "file" THEN
    response := GET_workspace_file(input.node.path)
    ASSERT response.readonly == true
    ASSERT fileEditorModal.readonly == true
  END IF
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed functions produce the same result as the original functions.

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT _build_tree_original(input) == _build_tree_fixed(input)
  ASSERT project_skills_original(input).resultSet == project_skills_fixed(input).resultSet
  ASSERT FileEditorModal_original(input).isDirty == FileEditorModal_fixed(input).isDirty
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation checking because:
- It generates many tree structures and git status dicts automatically
- It catches edge cases in path matching that manual tests might miss
- It provides strong guarantees that regular file/directory behavior is unchanged

**Test Plan**: Observe behavior on UNFIXED code first for regular files, non-symlink directories, and clean-file modal opens, then write property-based tests capturing that behavior.

**Test Cases**:
1. **Regular file git status preservation**: Verify that files with git status continue to display correctly after the `_build_tree()` change
2. **Child-prefix directory status preservation**: Verify that directories deriving status from child prefix matches continue to work
3. **Clean file modal preservation**: Verify that files without `gitStatus` continue to set `originalContent` to disk content
4. **Skill tier precedence preservation**: Verify that `project_skills()` still respects built-in/user/plugin tier precedence after switching to `copytree()`

### Unit Tests

- Test `_build_tree()` with flat-path directory entries in `git_status` dict
- Test `_build_tree()` with mixed flat-path and child-prefix entries
- Test `_build_tree()` with regular files (no change expected)
- Test new `GET /workspace/file/committed` endpoint with tracked, untracked, and nonexistent files
- Test `ProjectionLayer.project_skills()` creates real directories instead of symlinks
- Test `FileEditorModal` with `committedContent` prop sets `originalContent` correctly
- Test `FileEditorModal` without `committedContent` prop preserves existing behavior
- Test `GET /workspace/file` returns `readonly: true` for paths under `.claude/skills/`
- Test `GET /workspace/file` returns `readonly: false` (or absent) for regular file paths not under `.claude/skills/`
- Test skill files (SKILL.md, scripts/helper.py, etc.) are all openable and return readonly
- Test `PUT /workspace/file` returns 403 for paths under `.claude/skills/`
- Test `PUT /workspace/file` succeeds for regular file paths
- Test `toFileTreeItem()` copies `gitStatus` from `TreeNode` to `FileTreeItem`
- Test `GET /workspace/file/committed` returns 400 for binary files (not valid UTF-8)

### Property-Based Tests

- Generate random directory trees with random `git_status` dicts containing both flat-path and child-prefix entries; verify all directory nodes receive correct status
- Generate random skill sets and verify `project_skills()` creates non-symlink directories matching the expected skill set (extends existing `test_property_skill_symlinks.py`)
- Generate random file content pairs (committed vs disk) and verify `isDirty` is true when they differ and false when they match
- Generate random `git_status` dicts with only child-prefix entries (no flat paths) and verify `_build_tree()` output is identical to the original implementation (preservation)

### Integration Tests

- Test full flow: project skills via `copytree()`, run `git status`, verify `_build_tree()` shows correct status on skill directories and parent
- Test full flow: modify a file, open in `FileEditorModal`, verify "Show Changes" is enabled and diff shows correct changes
- Test full flow: open an untracked file in `FileEditorModal`, verify `originalContent` is empty string and diff shows entire file as added
- Test full flow: open a clean file (no git changes) in `FileEditorModal`, verify "Show Changes" remains disabled until user edits in the modal
- Test full flow: double-click a file inside a skill directory, verify it opens in FileEditorModal in read-only mode with readonly banner visible
- Test full flow: double-click a regular (non-skill) file, verify it opens in editable mode
- Test full flow: open a skill file with git changes, verify "Show Changes" works in read-only mode (diff is viewable)
