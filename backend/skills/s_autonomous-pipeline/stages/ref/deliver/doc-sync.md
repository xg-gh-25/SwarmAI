# DELIVER — Doc Sync Check (non-blocking warning)

Read when the card points here (after the meta-review, before the report). Verifies that
shipped code has matching documentation updates, catching "code shipped, docs forgot"
drift while the author still has context.

**Project-neutral by design.** Never assume the developed project uses SwarmAI's layout
(`backend/core/`, `docs/`, `docs/post-mortems/`). Read the project's OWN conventions from
its TECH.md and WARN-skip any sub-check it does not declare, so a Go/Java/frontend-only
project produces NO false GAP. The paths below are SwarmAI's own values, shown as a
reference.

**STEP 0 — resolve the project's conventions (before any grep).** TECH.md lives at
`<project DDD root>/2-understanding/TECH.md` (for SwarmAI-self:
`~/.swarm-ai/SwarmWS/Projects/SwarmAI/2-understanding/TECH.md`); search it with the Read
tool, not git grep. A **`## Key Subsystems`** section declares the core code areas →
check those; absent → `"N/A: project declares no core-subsystem convention"` and skip
sub-check 1. Check a docs or post-mortem directory only if `## Codebase Location` /
`## Conventions` declares one.

**STEP 0.5 — set the three path vars, THEN run the block.** Each var has TWO valid
states: a real pattern (run the sub-check) or `""` (no such convention → N/A, never a
GAP). The defaults are correct for SwarmAI-self; for another project substitute or empty
them. Never run the block with placeholders unedited. The base branch is resolved INSIDE
the block (a shell variable does not survive across separate Bash calls) and never
hardcoded to `main`; no resolvable base → the block WARN-skips.

```bash
CORE_PATH='^backend/core/'      # ## Key Subsystems core-code pattern; ""=skip
DOCS_DIR='docs/'                # declared docs dir;                    ""=skip
PM_DIR='^docs/post-mortems/'    # declared post-mortem dir;             ""=skip
BASE_REF=$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null)
if [ -z "$BASE_REF" ]; then
  for _b in origin/main origin/master origin/develop; do
    git rev-parse --verify "$_b" >/dev/null 2>&1 && { BASE_REF="$_b"; break; }
  done
fi
[ -z "$BASE_REF" ] && { echo "WARN: base branch not resolvable, doc-sync skipped"; exit 0; }
if [ -n "$CORE_PATH" ]; then
  NEW_CORE_FILES=$(git diff --name-only --diff-filter=A "$BASE_REF"...HEAD | grep "$CORE_PATH" | grep -v '__pycache__\|test')
else echo "N/A: project declares no core-subsystem convention"; fi
FEAT_COMMITS=$(git log --oneline "$BASE_REF"...HEAD | grep -iE "^[a-f0-9]+ feat")
if [ -n "$DOCS_DIR" ]; then
  DOCS_IN_BRANCH=$(git diff --name-only "$BASE_REF"...HEAD -- "$DOCS_DIR" | wc -l)
else echo "N/A: project declares no docs directory"; fi
COE_FIX=$(git log --oneline "$BASE_REF"...HEAD | grep -iE "COE|P0|bilateral|deadlock|crash.*all")
if [ -n "$PM_DIR" ]; then
  PM_COUNT=$(git diff --name-only --diff-filter=A "$BASE_REF"...HEAD | grep "$PM_DIR" | wc -l)
else echo "N/A: project declares no post-mortem directory"; fi
```

**Evaluation** (each row runs ONLY if the project declares the convention; otherwise
`N/A`, never a GAP):

| Condition | Check | Gap? |
|-----------|-------|:---:|
| New core-code file (per `## Key Subsystems`; SwarmAI: `.py` in `backend/core/`, not test) | TECH.md mentions the filename stem | declared AND not found → GAP |
| Any `feat` commit in the changeset | the declared docs dir (SwarmAI: `docs/`) has ≥1 changed file | declared AND 0 → GAP |
| Commit message mentions COE/P0/crash | the declared post-mortem dir (SwarmAI: `docs/post-mortems/`) has a new file | declared AND 0 → GAP |
| New skill/module (SwarmAI: `backend/skills/s_*`) | appears in the project's index/README or a design doc | declared AND not → GAP |

Output a short `DOC SYNC CHECK:` list (✅ / ⚠️ GAP / N/A per row), `Gaps found: N`, and
a recommendation per gap.

<!-- R-deliver-013 -->
**Impact on push-ready:** Doc gaps are **WARNING, not BLOCKING.** The pipeline can still
declare push-ready with doc gaps — but gaps are:
1. Surfaced in the Pipeline Report (§10 Known Gaps)
2. Auto-created as Radar Todo (priority MED, source: pipeline-doc-check)
3. Picked up by the weekly `docs-freshness-audit` job if not addressed

Non-blocking on purpose: blocking on docs rewards low-quality docs written only to
unblock. Ship the code, surface the gap, let the weekly audit enforce it.
