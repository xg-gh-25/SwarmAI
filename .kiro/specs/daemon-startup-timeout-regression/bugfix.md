# Bugfix Requirements Document

## Introduction

SwarmAI Desktop v1.9.0 introduced a regression where the app fails to start
even when the backend daemon is demonstrably healthy. Users see the
`BackendStartupOverlay` error — "Backend service failed to start within 60
seconds — Please check the logs at: ~/.swarm-ai/logs/" — despite
`curl http://127.0.0.1:18321/health` returning `{"status":"healthy", ...}`.

The regression was introduced in commit `434da64` ("feat: Desktop Update
Gaps — DB migration runner + daemon sync + UX safety") which added the
`sync_daemon_version()` routine to `desktop/src-tauri/src/lib.rs`. That
routine is invoked from `start_backend()` **synchronously before the Tauri
command returns to the frontend**. When the bundled app version and the
running daemon version do not match exactly, `sync_daemon_version()` tears
down the healthy daemon with `launchctl bootout`, copies a new binary,
`launchctl bootstrap`s a fresh process, and re-probes health for up to
~20 seconds. Combined with PyInstaller cold-start time (loading all
bundled Python modules can take 30–60 seconds), the total blocking work
inside `start_backend()` routinely exceeds the frontend's hard 60-second
timeout (`TIMING.maxHealthAttempts = 60` × `pollInterval = 1000ms` in
`BackendStartupOverlay.tsx`), causing the overlay to time out and error
out — even though the daemon is objectively running and healthy the entire
time.

Pre-v1.9.0, `start_backend()` returned the port immediately after a
successful `probe_daemon_health()` probe; no teardown, no blocking
upgrade. The fix must restore the fast-path guarantee: when a healthy
daemon is already running, the frontend overlay must dismiss promptly.

User impact: the app is functionally unusable on restart for any user whose
daemon version string drifts from the app version string, regardless of
whether the daemon is healthy. User reproduction is verbatim: 「我们的
Daemon 都起来了 没问题 但是app restart failed 老说 'Backend service
failed to start within 60 seconds Please check the logs at:
~/.swarm-ai/logs/' … 这个问题是在V1.9.0 出现的regressions, 以前版本没有」.

## Bug Analysis

### Current Behavior (Defect)

When the daemon is already running and healthy on `DAEMON_PORT` (18321),
but the app's bundled version string does not exactly equal the daemon's
reported version string, the Tauri `start_backend()` command performs a
blocking tear-down and re-spawn of the healthy daemon that routinely
exceeds the frontend's 60-second startup budget.

1.1 WHEN the daemon is healthy on `DAEMON_PORT` AND the app's version string does not exactly equal the daemon's `/health` version string THEN the system invokes `sync_daemon_version()` synchronously inside `start_backend()` before returning the port to the frontend
1.2 WHEN `sync_daemon_version()` runs with a version mismatch THEN the system sends a shutdown request to the healthy daemon, issues `launchctl bootout`, sleeps 3 seconds, copies a new binary, issues `launchctl bootstrap`, and re-probes health for up to 20 seconds, all while the frontend's 60-second timeout clock is already running
1.3 WHEN the combined tear-down, binary copy, PyInstaller cold-start re-spawn, and re-probe time exceed 60 seconds THEN the frontend `BackendStartupOverlay` exits the health-poll loop, transitions to the `error` state, and displays "Backend service failed to start within 60 seconds" to the user
1.4 WHEN the user encounters this timeout THEN the user is blocked from using the app on restart even though the daemon process remains running and healthy on port 18321 throughout

### Expected Behavior (Correct)

When the daemon is already running and healthy on `DAEMON_PORT`, the app
must connect to it promptly regardless of any version mismatch. Version
reconciliation, if needed, must not block the frontend startup path and
must not cause the 60-second overlay timeout to fire when the daemon is
objectively healthy.

2.1 WHEN the daemon is healthy on `DAEMON_PORT` AND the app's version string does not exactly equal the daemon's `/health` version string THEN the system SHALL return the daemon port to the frontend within the time budget of a healthy-daemon connect (measured in single-digit seconds, well under 60s), independent of whether a version reconciliation is required
2.2 WHEN the daemon is healthy on `DAEMON_PORT` and an upgrade or reconciliation is genuinely required THEN the system SHALL NOT cause the frontend to display "Backend service failed to start within 60 seconds" while the daemon is healthy and reachable
2.3 WHEN the daemon is healthy on `DAEMON_PORT` THEN the system SHALL communicate any in-progress upgrade state to the frontend through a non-blocking channel (e.g., a Tauri event such as `backend-upgrading`/`backend-upgraded`, or an equivalent mechanism) so that the user experience reflects the true healthy state of the backend rather than a spurious startup-timeout error
2.4 WHEN the user launches the app while the daemon is healthy THEN the system SHALL dismiss the `BackendStartupOverlay` promptly based on the daemon's health, without the dismissal being gated on the completion of a version-sync operation

### Unchanged Behavior (Regression Prevention)

The 99% happy path — daemon already running, version strings match — must
remain unchanged: no teardown, no binary copy, no bootstrap. All other
pre-v1.9.0 startup paths (no daemon installed, daemon plist exists but
daemon is down, sidecar fallback, fresh install) must continue to behave
as they did before the fix.

3.1 WHEN the daemon is healthy on `DAEMON_PORT` AND the app's version string exactly equals the daemon's `/health` version string THEN the system SHALL CONTINUE TO return the daemon port to the frontend without tearing down the daemon, copying a binary, or calling `launchctl bootout`/`bootstrap`
3.2 WHEN no daemon is running on `DAEMON_PORT` AND the daemon plist is installed THEN the system SHALL CONTINUE TO attempt auto-bootstrap via `ensure_daemon_bootstrapped()` and connect after a successful probe, per the existing Phase 2 logic in `start_backend()`
3.3 WHEN the daemon plist exists BUT the daemon is not responding on `DAEMON_PORT` after bootstrap attempts THEN the system SHALL CONTINUE TO return the existing diagnostic error "Daemon is installed but not responding on port {DAEMON_PORT}. Check daemon logs: …" rather than silently falling back to a sidecar
3.4 WHEN no daemon plist is installed AND no daemon is running THEN the system SHALL CONTINUE TO fall back to the sidecar path with a freshly-picked port via `portpicker`
3.5 WHEN the backend is healthy and the frontend polls `/health` successfully within 60 seconds THEN the `BackendStartupOverlay` SHALL CONTINUE TO transition through `starting` → `fetching_status` → `waiting_for_ready`/`connected` and dismiss via the existing fade-out path
3.6 WHEN the daemon is healthy on `DAEMON_PORT` and a genuine binary upgrade is required THEN the system SHALL CONTINUE TO eventually reconcile the daemon binary to match the app version (either in the background or on a subsequent launch), so that the upgrade capability introduced in commit 434da64 is preserved rather than removed
3.7 WHEN the daemon health watchdog detects a daemon outage after successful connection THEN the system SHALL CONTINUE TO emit `backend-mode` and related events per the existing `spawn_daemon_health_watchdog` behavior

## Deriving the Bug Condition

### Bug Condition Function

```pascal
FUNCTION isBugCondition(X)
  INPUT: X of type AppStartupContext
    // X.daemon_healthy : boolean  — /health returns status=="healthy" on DAEMON_PORT
    // X.daemon_version : string   — version reported by /health
    // X.app_version    : string   — app.config().version from tauri.conf.json
    // X.overlay_timeout_s : int   — frontend TIMING.maxHealthAttempts * pollInterval/1000 (= 60)
  OUTPUT: boolean

  // Bug triggers when the daemon is healthy but the version strings differ,
  // causing sync_daemon_version() to block start_backend() past the
  // frontend's startup-overlay timeout.
  RETURN X.daemon_healthy = true
      AND X.daemon_version ≠ X.app_version
END FUNCTION
```

### Fix-Checking Property

```pascal
// Property: Fix Checking — healthy daemon must not trip the startup-overlay timeout
FOR ALL X WHERE isBugCondition(X) DO
  result ← start_backend'(X)     // F' = fixed start_backend
  ASSERT result.returned_ok = true
     AND result.returned_port = DAEMON_PORT
     AND result.elapsed_seconds < X.overlay_timeout_s
     AND result.frontend_error ≠ "Backend service failed to start within 60 seconds"
END FOR
```

Concrete counterexample demonstrating the bug pre-fix:

```
AppStartupContext {
  daemon_healthy   = true,
  daemon_version   = "1.9.0",
  app_version      = "1.9.0-dev",   // any non-exact-equal string drifts the comparison
  overlay_timeout_s = 60
}
→ Pre-fix:  start_backend() blocks ~30–80s inside sync_daemon_version(),
            frontend overlay times out at 60s with
            "Backend service failed to start within 60 seconds"
→ Post-fix: start_backend() returns DAEMON_PORT within a few seconds;
            overlay dismisses normally; version reconciliation (if any)
            runs asynchronously without blocking the frontend
```

### Preservation-Checking Property

```pascal
// Property: Preservation Checking — non-buggy startup paths are unchanged
FOR ALL X WHERE NOT isBugCondition(X) DO
  ASSERT start_backend(X) = start_backend'(X)
END FOR
```

`NOT isBugCondition(X)` covers:
- Healthy daemon with matching version strings (99% happy path).
- No daemon running, plist installed (Phase 2 auto-bootstrap path).
- No daemon running, no plist (Phase 3 sidecar fallback).
- Daemon plist installed but unreachable after bootstrap (Phase 3 error path).

All of these paths must produce identical observable behavior to the
pre-fix version so the upgrade capability is preserved and no other
regressions are introduced.

### Key Definitions

- **F** — the v1.9.0 `start_backend()` function after commit 434da64 (the
  unfixed function that synchronously calls `sync_daemon_version()` and
  causes the timeout).
- **F'** — the fixed `start_backend()` function, to be defined during the
  design phase. Candidate strategies (to be evaluated in design.md):
  (a) fast-path-first: return the daemon port immediately on healthy
  probe and run version reconciliation in a background task that emits
  `backend-upgrading`/`backend-upgraded` events; (b) extend the
  frontend's timeout budget via `backend-upgrading` events when a
  genuine upgrade is in progress; (c) a hybrid of both. The design
  phase will pick a concrete F' and justify the choice against the
  preservation and fix-checking properties above.
