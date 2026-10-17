# REVIEW ref — Surface checks: UX (5), wire test (7), operational context (7b), platform matrix (16)
Read when: frontend files changed (5), frontend AND backend endpoints changed (7),
operational code changed (7b), or path / env / filesystem / process code changed (16).

## 5. UX Review (only when the changeset has `.tsx`, `.jsx`, `.css`, `.html`, `.svelte`, `.vue`)
Skip entirely for backend-only changesets. Walk every new/changed user-facing interaction:
| # | Check | What to verify | Example failure |
|---|-------|---------------|-----------------|
| UX1 | **Discoverability** | How does the user discover it? Hint, tooltip, visual affordance? | Diff lines became clickable with no visual cue |
| UX2 | **Feedback** | New interactive elements have hover, active and disabled states | Clickable rows with no hover highlight |
| UX3 | **Behavioral contracts** | Reused components: are reactive props actually reactive (update on scroll/resize/state change)? | `topOffset` passed as a DOM snapshot instead of React state |
| UX4 | **Escape / click-outside** | Correct in every context (modal, panel, portal)? Does Escape propagate unexpectedly? | Escape in a portal popover also closed the parent editor |
| UX5 | **Scroll tracking** | Positioned elements (popover, tooltip, dropdown) follow a scrolling container | Popover stays put while diff content scrolls away |
Every UX finding → **auto-fix** (always bugs, not taste). Record under `"ux_review"`
(`{triggered, checks, findings}`).

## 7. Cross-Boundary Wire Test (only when BOTH frontend API calls AND backend endpoints changed)
E.g. a new `.ts` service function + a new `@router.post`. Skip single-layer changes.
**Scope:** this is the STATIC frontend↔backend HTTP slice of cross-boundary. If EVALUATE
set `cross_boundary.value = true` with another kind (event-bus, ACT/SENSE, data
migration, multi-subsystem shared path), that seam is verified DYNAMICALLY by TEST
Layer 4 (real system + mutation proof), not here. Check 7 = "does the wire format line
up on paper"; Layer 4 = "does the real seam fire end-to-end". Both can apply to one run;
check 15 gates that the Layer-4 test EXISTS when `cross_boundary` is set.
Per boundary, answer (REST/FastAPI/Axios shape shown; translate the columns for gRPC
proto fields, GraphQL schema or WebSocket message shape):
| # | Question | How to verify | Example failure |
|---|----------|--------------|-----------------|
| WR1 | **Content-Type match?** | Frontend sends X — backend parser expects X | Axios sends `application/json`, backend expects `multipart/form-data` |
| WR2 | **Field names match?** | `form.append('audio', …)` ↔ `form.get("audio")` | Frontend sends `audioFile`, backend reads `audio` → None |
| WR3 | **Response shape match?** | Backend keys ↔ frontend type fields | Backend returns `text`, frontend reads `transcript` → undefined |
| WR4 | **Error shape match?** | `HTTPException(400, detail=…)` ↔ handler reads `response.data.detail` | Backend returns `{"message": …}`, frontend reads `detail` |
Output, one block per boundary: `Wire: POST /api/chat/transcribe` then `WR1: pass — <why>`
… `WR4: pass — <why>`. Code-level trace only (read the service function and the endpoint
side by side), no live requests. Record under `"wire_test"` (`{boundaries, verified, findings}`).

## 7b. Operational Context Check (auto-selected by path)
**BLOCKING when changeset touches operational code. Skip for pure business logic.** <!-- R-review-008 -->
Infer the execution context from the changed paths and apply the matching checklist:
it catches bugs invisible in dev but fatal in production. The `*.py` patterns are
SwarmAI-self examples — match the project's paths/extensions from TECH.md
`## Codebase Location`; the categories are language-neutral (a Go cron handler needs the
CRON checklist too). No changed file matches any context → skip.
| File path pattern (SwarmAI-self) | Context | Checklist |
|-------------------|---------|-----------|
| `hooks/*.py` | Per-session recurring | HOOK |
| `jobs/*.py`, `scheduler.py` | Cron/background | CRON |
| `routers/*.py`, `channels/*.py` | Request/message handler | ENDPOINT |
| `main.py`, `lifecycle_*.py`, `daemon*` | Startup/shutdown | LIFECYCLE |
| `scripts/*.py`, CLI tools | One-shot invocation | SCRIPT |
- **HOOK:** no-op cost when it fires and finds nothing (RP30) · scaling: does no-op cost
  grow with accumulated data? · time bound: an mtime/count filter caps the scan · error
  isolation: failure logged + skipped, never blocks the session · idempotent: re-run
  gives the same result (no double-writes).
- **CRON:** concurrent guard (previous run still running?) · stale data (source returns
  yesterday's data?) · failure notification (log, health) · resource cleanup (temp files,
  connections closed on every exit path) · clock drift (UTC vs local assumptions).
- **ENDPOINT:** auth (behind the correct middleware?) · concurrency (2 requests racing on
  shared state?) · timeout (wall-clock upper bound?) · error response (structured JSON,
  not 500 HTML) · input validation (all path/query/body params validated before use).
- **LIFECYCLE:** daemon env (no `$HOME`/`$USER`/`$SHELL` assumptions; launchd strips
  them) · kill semantics (SIGTERM vs SIGKILL vs bootout — which fires here?) · partial
  startup (step 3/5 fails → earlier steps cleaned up?) · reentrancy (called twice on a
  KeepAlive restart?) · port conflict (port 18321 already occupied?).
- **SCRIPT:** idempotent (safe to re-run if killed midway) · output is structured JSON
  for agent consumption · non-zero exit code on failure · path assumptions use
  `Path.home()` or explicit args, not `$HOME`.
Record under `"operational_context"`: `Context: HOOK (<file>)` then one `□ <item>: pass —
<evidence>` line per checklist item.

## 16. Platform Matrix Verification
**Trigger:** the changeset touches path resolution, env var usage, filesystem operations,
process lifecycle, or any code that can behave differently across deployment targets.
**Skip when** it is purely logic/algorithm with no platform-dependent behavior.
**Process:** read TECH.md for the declared platform list; verify the change on each.
SwarmAI-self example (its platforms and paths are its own — another project declares
its own matrix in ITS TECH.md; never treat a local checkout path as universal):
```
TECH.md declares platforms: daemon (macOS), subprocess (Win/Linux), hive (EC2 Linux), dev
This change touches: path resolution
  macOS daemon:       <local checkout> → ✓ (candidate list)
  Hive (Linux EC2):   ~/swarmai or /opt/swarmai → ✓ (candidate list)
  Windows subprocess: N/A (script jobs don't run on Windows)
  Dev mode:           __file__ fallback → ✓ (works from source tree)
```
Per platform: does the path/env var exist there? Is the fallback correct when it
doesn't? Does the code silently return a wrong value instead of raising?
**Severity:** a platform with 0 working resolution paths = **HIGH** (guaranteed
failure); a platform needing undocumented env var setup = **MEDIUM** (fragile); a
platform covered by a less optimal fallback = **LOW** (acceptable).
