---
name: library
description: "Manage the SwarmAI Library — mount external directories (code or docs) so the agent can recall them, brief a docs folder into cards, and search the library. Mounts are POINTERS (indexed in place, never copied). TRIGGER: \"mount <path>\", \"mount this folder\", \"brief the docs dir <path>\", \"add <folder> to my library\", \"search my library for X\", \"挂载\", \"把这个目录加进来\". NOT FOR: single-file drops (that's the Inbox), URLs (use s_learn-content), or creating a DDD project (use s_project-manager)."
input_type: text
output_type: text
tier: lazy
---
# Library Skill — mount, brief, search

The Library is the agent's bookshelf: the Native store (`Knowledge/`, already in
recall) + **mount points** — references to external directories on the user's disk,
indexed **in place, never copied** (index-not-warehouse). This skill is the
agent-facing half of the mount engines (the +Add Folder button uses the same core
functions via the API).

**Core principle:** a mount stores a `{path, kind, briefing}` pointer + an index,
never a copy of the source. Recall lands on the pointer → you `Read` the LIVE
source (progressive load). An external **directory** is mounted; a single **file**
goes to the Inbox (not this skill); a **URL** goes to s_learn-content.

## Tool

```bash
python3 {SKILL_DIR}/scripts/library.py <command> [options]
```

All commands operate on the real `library_mounts` registry + the same
`core.library_mounts` engines the API uses — this skill does NOT reinvent them.

## Commands

### mount — register + index an external directory
Judge the kind from the directory (code if it holds parseable source, else docs),
register it, and index:
```bash
python3 {SKILL_DIR}/scripts/library.py mount --path /Users/gawan/Desktop/AI-Native/some-repo --scope SwarmAI
```
- **code dir** → builds a per-mount symbol graph (index in place). Done — symbols
  are now recallable.
- **docs dir** → registers the mount, then prints the file list. YOU (the agent)
  then decide which files are worth a briefing card and call `brief` (below) with
  your per-file briefings — this is the semantic step only you can do.

### brief — write file-level briefing cards for a docs mount
After reading/judging the docs dir's files, write briefing cards (pointers +
your summaries, NOT copies):
```bash
python3 {SKILL_DIR}/scripts/library.py brief --mount-id <id> \
  --overview "All-hands corpus: Q3 plan + standup notes" \
  --card q3-plan.md="Q3 roadmap: ship widget, +2 hires" \
  --card notes.txt="Standup log; blocker is auth"
```
Cards land in `Knowledge/Library/mounts/<id>/` and are auto-indexed onto the
library recall leg (no extra step).

### search — see what recall would retrieve
```bash
python3 {SKILL_DIR}/scripts/library.py search --query "widget scoring" --scope SwarmAI
```
Runs the real recall path (library + codeintel domains) — the same thing the
Browse-tab search box shows.

### list — show registered mounts + health
```bash
python3 {SKILL_DIR}/scripts/library.py list --scope SwarmAI
```

## Workflow — "mount <path>"
1. Run `mount --path <path>` → it judges kind + registers + (code) indexes.
2. If kind=docs: read the printed file list, judge which files matter, then `brief`
   with your per-file briefings + an overview.
3. Confirm to the user: what was mounted, its kind, and that recall now reaches it.

## Boundaries
- **Never copy** an external directory into the workspace — mount = pointer + index.
- A single file → tell the user it goes to the **Inbox** (drag it, or the Inbox
  endpoint), not a mount. A **URL** → **s_learn-content**.
- Ownership is **per-scope** — a mount registered under one project scope does not
  authorize another (the cross-project contamination guard stays intact).
