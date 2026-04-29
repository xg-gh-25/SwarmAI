# Implementation Plan

## Bug Summary

**Bug_Condition (C):** `daemon_healthy == true AND daemon_version != app_version`.
When this holds, v1.9.0's `start_backend()` synchronously `.await`s
`sync_daemon_version()`, tearing down a healthy daemon and blocking the
Tauri command past the frontend's 60s overlay timeout.

**Expected Behavior (P):** `start_backend()` returns `DAEMON_PORT` in
under 5 seconds under C, with version reconciliation dispatched to a
background task that emits `backend-upgrading` / `backend-upgraded` /
`backend-upgrade-failed` events.

**Preservation:** All inputs where NOT C(X) — matching versions, no
daemon, no plist, dead plist, watchdog restart — produce byte-identical
observable behavior to pre-fix v1.9.0.

See `bugfix.md` for full requirements and `design.md` for the complete
fix specification (Option 1: fast-path-first with background sync).

---

- [ ] 1. Write bug condition exploration test
  - **Property 1: Bug Condition** - Healthy-Daemon Version-Drift Blocks start_backend
  - **CRITICAL**: This test MUST FAIL on unfixed (v1.9.0) code — the failure is the SUCCESS signal that confirms the bug condition described in design.md "Bug Condition" and bugfix.md §1.
  - **CRITICAL**: When this test fails on unfixed code, DO NOT attempt to fix the test or the code. The test is doing its job by failing.
  - **NOTE**: The same test, unchanged, becomes the fix-validation test once Task 3 lands — when it passes, the expected behavior from design.md "Property 1" is satisfied.
  - **GOAL**: Surface concrete counterexamples that demonstrate the synchronous-await coupling described in design.md "Hypothesized Root Cause" point 1.
  - **Scoped PBT Approach**: The bug is deterministic — any `daemon_version != app_version` pair triggers it. Scope the property to a small set of representative pairs (`"1.9.0"` vs `"1.9.0-dev"`, `"1.9.1"` vs `"1.9.0"`, `"1.9.0"` vs `""`) to keep the test fast and reproducible.
  - Create `desktop/src-tauri/tests/startup_timeout_exploration.rs` as an integration test using `proptest` (add to `[dev-dependencies]` in `Cargo.toml` if not already present).
  - Mock the daemon with a lightweight `hyper`-based HTTP server bound to `DAEMON_PORT` (18321) that serves `/health` with a configurable version string. If 18321 is in use by a real daemon on the dev machine, gate the test behind `#[cfg(feature = "integration-tests")]` or skip via env check.
  - Mock `launchctl` by prepending a test-owned directory to `PATH` containing a `launchctl` shell shim that logs `bootout`/`bootstrap` calls to a temp file and returns `0` immediately (no real launchd interaction, no real PyInstaller cold-start — the structural defect is still exercised because `sync_daemon_version` still `.await`s its inner `probe_daemon_health(10, 2)` against the unavailable torn-down daemon).
  - Property asserted: `∀ (daemon_version, app_version) where daemon_version != app_version ∧ daemon_healthy = true: start_backend elapsed_seconds < 5.0`.
  - Include a module-level `//!` docstring at the top of the test file that explains: (a) failure on v1.9.0 code is the SUCCESS signal, (b) passing indicates the regression is fixed, (c) the test must remain in-tree as a regression guard.
  - Run the test on unfixed code.
  - **EXPECTED OUTCOME**: Test FAILS with elapsed time in the 20–80s range (the mock `launchctl` shim lets bootout succeed quickly, but the subsequent `probe_daemon_health(10, 2)` waits its full 20s because the mock daemon is now "down"; combined with the 3s sleep, elapsed will be ≥23s, well above the 5s threshold). This failure proves the synchronous-await coupling.
  - Document the measured elapsed time and any counterexamples (the `(daemon_version, app_version)` pairs that triggered the longest elapsed times) in a comment at the bottom of the test file.
  - Mark task complete when the test is written, run on unfixed code, and the failure is documented with measured elapsed seconds.
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 2.1, 2.4_

- [ ] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** - Non-Buggy Startup Paths Are Unchanged
  - **IMPORTANT**: Follow observation-first methodology — run the UNFIXED v1.9.0 code, record its actual event sequences and return values for each non-bug-condition equivalence class, then encode those observations as property-based tests.
  - **CRITICAL**: These tests MUST PASS on unfixed code. A failure here means the test is wrong, not the code — the whole point is to lock down pre-fix behavior so the fix can be verified not to regress it.
  - Create `desktop/src-tauri/tests/startup_preservation.rs` as an integration test using `proptest`.
  - Share the mock daemon + mock `launchctl` harness with Task 1 (extract into a `tests/common/mod.rs` module if convenient).
  - Write one property-based test per equivalence class from design.md "Preservation Checking":
    - `preservation_happy_path`: `daemon_healthy=true, daemon_version == app_version`. Property: `start_backend` returns `Ok(DAEMON_PORT)` AND zero `backend-upgrading` / `backend-upgraded` / `backend-upgrade-failed` events fire in the observation window. Cover ~100 generated matching-version pairs (including identical `"1.9.0"`, identical `""`, identical unicode strings).
    - `preservation_no_daemon`: `daemon_healthy=false, plist_exists=true`. Property: `ensure_daemon_bootstrapped` is invoked AND the second probe succeeds AND `start_backend` returns `Ok(port)`. Assert the exact event sequence (`backend-mode: "daemon"`, etc.) matches the pre-fix capture.
    - `preservation_sidecar_fallback`: `daemon_healthy=false, plist_exists=false`. Property: sidecar spawn path runs, `portpicker` picks a port, `start_backend` returns `Ok(port)`. Same event sequence as pre-fix.
    - `preservation_plist_dead_daemon`: `daemon_healthy=false, plist_exists=true, bootstrap_always_fails=true`. Property: `start_backend` returns `Err(...)` with the exact error string `"Daemon is installed but not responding on port 18321. Check daemon logs: ..."` (match the current v1.9.0 string verbatim).
    - `preservation_health_watchdog`: after a successful connect, force the mock daemon to change its `boot_id`. Property: `spawn_daemon_health_watchdog` detects the change and emits `backend-restarted` identically pre-fix and post-fix.
  - For each test, first run against unfixed v1.9.0 code to produce a "golden" event sequence; store those goldens as hardcoded expected values in the test file.
  - Run all five tests on unfixed code.
  - **EXPECTED OUTCOME**: All five tests PASS (confirms the baseline observable behavior that the fix must preserve).
  - Mark task complete when all five preservation tests are written, run, and passing on unfixed code.
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7_

- [ ] 3. Fix for daemon-startup-timeout regression (apply Option 1: fast-path-first with background sync — see design.md "Fix Implementation")

  Task 3 sub-tasks are ordered so the code compiles after each one (with one documented exception at 3.4, called out below). Apply them in order.

  - [x] 3.1 Add `futures = "0.3"` to `desktop/src-tauri/Cargo.toml` dependencies
    - Open `desktop/src-tauri/Cargo.toml`. Under `[dependencies]`, add `futures = "0.3"` if it is not already present as a direct dependency.
    - Required for `FutureExt::catch_unwind` used by the panic-to-event conversion in Task 3.2 (see design.md Change 2).
    - Run `cargo check -p swarm-ai` (or whatever the Tauri crate name is in `Cargo.toml`) to confirm the workspace still resolves.
    - _Requirements: 2.3_

  - [x] 3.2 Implement `sync_daemon_version_background()` helper in `lib.rs`
    - In `desktop/src-tauri/src/lib.rs`, immediately after the existing `sync_daemon_version` definition (around line 806), add the new `async fn sync_daemon_version_background(app: tauri::AppHandle, app_version: String)` helper exactly as specified in design.md Change 2.
    - Key behaviors the helper MUST implement (see design.md "Correctness Properties" 3 and 4):
      - Pre-check: call `get_daemon_version()` and return early (emit no events) when `daemon_version == app_version`. This preserves Property 2 (zero upgrade events on matching-version runs).
      - Emit exactly one `backend-upgrading` event with `{from, to}` payload before dispatching.
      - Invoke `sync_daemon_version` wrapped in `std::panic::AssertUnwindSafe(...).catch_unwind().await`.
      - Match on the `Result<Result<(), String>, Box<dyn Any>>` and emit exactly one terminal event: `backend-upgraded` on `Ok(Ok(()))`, `backend-upgrade-failed` on `Ok(Err(e))` or `Err(panic_info)`.
      - Downcast panic payloads to `&str` then `String` then fall back to a generic `"panic in sync_daemon_version (unknown payload)"` message.
      - Return `()` — the function MUST NOT propagate errors, per design.md Property 4 (background task cannot crash the app).
    - Add a Rust `///` doc comment on the function following the style described in design.md Change 2 (summary + numbered list of behaviors + safety note).
    - Add the helper BEFORE changing the call site (Task 3.3) — otherwise the call site won't compile.
    - Run `cargo check` to confirm the helper compiles standalone.
    - _Bug_Condition: isBugCondition(input) where input.daemon_healthy = true AND input.daemon_version ≠ input.app_version_
    - _Expected_Behavior: design.md Property 3 — exactly one start event and exactly one terminal event_
    - _Preservation: design.md Property 2 — zero events when versions match (pre-check short-circuit)_
    - _Requirements: 2.3, 3.6_

  - [x] 3.3 Replace the Phase 1 inline `.await sync_daemon_version(...)` block in `start_backend()`
    - In `desktop/src-tauri/src/lib.rs`, locate the Phase 1 block inside `start_backend()` (around lines 1034–1053 in v1.9.0, inside the `if let Some(_port) = probe_daemon_health(5, 2).await { ... }` branch).
    - Replace the inline `match sync_daemon_version(&app, &app_version).await { ... }` with the `tauri::async_runtime::spawn(...)` dispatch shown in design.md Change 1 "After (this fix)".
    - Wrap the dispatch in the existing `if !app_version.is_empty()` guard so empty versions still short-circuit (matches v1.9.0 behavior).
    - Add the inline comment from design.md Change 1 that explains why the reconciliation MUST NOT block (references the v1.9.0 regression) — this is a load-bearing comment for future maintainers.
    - Do NOT modify Phase 2 or Phase 3 (see design.md "Preservation Requirements").
    - Run `cargo check`. The code compiles cleanly at this point — `sync_daemon_version` is still called from `sync_daemon_version_background`, just no longer from `start_backend` directly.
    - _Bug_Condition: isBugCondition(input)_
    - _Expected_Behavior: design.md Property 1 — start_backend returns DAEMON_PORT in <5s under the bug condition_
    - _Preservation: design.md Property 6 — overlay dismissal is independent of upgrade lifetime_
    - _Requirements: 2.1, 2.2, 2.4_

  - [x] 3.4 Remove duplicate `app.emit(...)` calls from inside `sync_daemon_version()`
    - In `desktop/src-tauri/src/lib.rs::sync_daemon_version`, delete the three inline `app.emit(...)` calls:
      - `app.emit("backend-upgrading", format!("{} → {}", daemon_version, app_version))` around line 717
      - `app.emit("backend-upgraded", app_version)` around line 795
      - (and any `backend-upgrade-failed` inline emit if present — design.md Change 3 only enumerates the two above, but double-check the current source)
    - Keep the surrounding `println!("[Tauri] ...")` log lines intact for debuggability.
    - This establishes `sync_daemon_version_background` as the single source of truth for event emission (see design.md Change 3 and Property 3 — "No double-emits").
    - **Compile-order note**: After this change, the `Ok(Ok(()))` arm of `sync_daemon_version_background` becomes responsible for emitting `backend-upgraded`. Update that arm now to uncomment/add:
      ```rust
      let _ = app.emit("backend-upgraded", serde_json::json!({ "version": app_version }));
      ```
      (per design.md Change 3 "Also uncomment…" instruction). The code compiles cleanly after both edits are applied together.
    - Run `cargo check` and then `cargo build -p <tauri-crate>` to confirm Rust side is clean.
    - _Bug_Condition: N/A — this change enforces Property 3 event-uniqueness invariant_
    - _Expected_Behavior: design.md Property 3 — exactly one backend-upgrading and exactly one terminal event per background dispatch_
    - _Preservation: Preserves existing event names and payload shapes so the frontend banner contract is unchanged_
    - _Requirements: 2.3_

  - [x] 3.5 Create `desktop/src/components/common/BackendUpgradeBanner.tsx`
    - Create the new file with the full implementation from design.md Change 4.
    - Include the module-level `/** */` docstring at the top (required by workspace rule swarmai-dev-rules.md "Code Documentation Standards") that describes:
      - One-line summary: "Non-blocking banner that reflects background daemon upgrade status."
      - The three Tauri events it subscribes to (`backend-upgrading`, `backend-upgraded`, `backend-upgrade-failed`) and what each triggers in the UI.
      - The key export: `BackendUpgradeBanner` (default export).
    - Implement the `BannerState` discriminated union (`idle` / `upgrading` / `upgraded` / `failed`) and the three payload interfaces (`UpgradingPayload`, `UpgradedPayload`) exactly as shown in design.md Change 4.
    - Use `listen` from `@tauri-apps/api/event` and the existing `isDesktop()` helper from `../../services/tauri` to gate the subscription (keeps web-preview / Hive mode builds clean — see design.md "Edge Cases" final bullet).
    - Auto-hide timers: 4000ms for `upgraded`, 8000ms for `failed`. Use a single `autoHideTimer` variable cleaned up in the effect's return function.
    - Styling: use existing CSS custom properties (`var(--color-card)`, `var(--color-border)`, `var(--color-text-muted)`, `var(--color-success, #22c55e)`, `var(--color-error, #ef4444)`) per workspace rule AGENTS.md "Design System" — never hardcoded dark-theme colors.
    - Accessibility: `role="status"` and `aria-live="polite"` on the banner `div`.
    - Run `cd desktop && npm run build` to confirm the file type-checks under the project's TypeScript config.
    - _Bug_Condition: N/A — this change implements the non-blocking UI channel required by Property P_
    - _Expected_Behavior: design.md Property 6 — banner renders outside the overlay's DOM subtree and does not delay dismissal_
    - _Preservation: BackendStartupOverlay.tsx is untouched_
    - _Requirements: 2.3, 2.4_

  - [x] 3.6 Mount `<BackendUpgradeBanner />` in `desktop/src/App.tsx`
    - Open `desktop/src/App.tsx`. Add the import: `import BackendUpgradeBanner from './components/common/BackendUpgradeBanner';`.
    - Mount the banner at the top level of the returned JSX, as a SIBLING to `<BackendStartupOverlay ... />` — not nested inside it, not nested inside the router, not inside any provider that might unmount on route change. The banner must outlive route changes (see design.md Change 4 final paragraph and Change 5).
    - Do NOT modify `BackendStartupOverlay.tsx` itself. Per design.md Change 5, the overlay MUST have zero diff in this fix. Verify via `git diff desktop/src/components/common/BackendStartupOverlay.tsx` before committing — the output must be empty.
    - Run `cd desktop && npm run build` to confirm the full frontend bundle builds.
    - _Bug_Condition: N/A_
    - _Expected_Behavior: design.md Property 6 — overlay dismissal independent of upgrade_
    - _Preservation: BackendStartupOverlay.tsx has zero diff; banner does not affect route or modal state_
    - _Requirements: 2.3, 2.4, 3.5_

  - [ ] 3.7 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** - Healthy-Daemon Version-Drift start_backend Returns in <5s
    - **IMPORTANT**: Re-run the SAME test from Task 1 — do NOT write a new test. The test from Task 1 encodes the expected behavior; when it passes, Property 1 from design.md is satisfied.
    - Run `cd desktop/src-tauri && cargo test --test startup_timeout_exploration`.
    - **EXPECTED OUTCOME**: Test PASSES (measured elapsed < 5s for all generated `(daemon_version, app_version)` pairs with `daemon_version != app_version`). This confirms the synchronous-await has been removed from `start_backend`'s critical path.
    - If the test still fails, DO NOT modify the test. Diagnose the fix — likely Task 3.3 wasn't applied, or the `tauri::async_runtime::spawn` call is still awaited somewhere upstream.
    - _Requirements: 2.1, 2.2, 2.4_

  - [ ] 3.8 Verify preservation tests still pass
    - **Property 2: Preservation** - Non-Buggy Startup Paths Unchanged Post-Fix
    - **IMPORTANT**: Re-run the SAME five preservation tests from Task 2 — do NOT write new tests.
    - Run `cd desktop/src-tauri && cargo test --test startup_preservation`.
    - **EXPECTED OUTCOME**: All five preservation tests PASS (event sequences and return values byte-identical to the Task 2 pre-fix captures). This confirms no regressions across the non-bug-condition equivalence classes.
    - Of particular importance: `preservation_happy_path` confirms that `backend-upgrading` / `backend-upgraded` / `backend-upgrade-failed` events fire ZERO times when versions match, enforcing the "pre-check short-circuit" invariant from Task 3.2.
    - If any preservation test fails, the fix has introduced a regression. Fix the fix before proceeding — do not relax the preservation assertions.
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7_

- [ ] 4. Unit tests (targeted coverage of new and changed units)

  - [ ] 4.1 Rust — `versions_match()` pure helper
    - Extract the `daemon_version == app_version` comparison currently inlined in `sync_daemon_version` into a small pure helper: `fn versions_match(daemon: &str, app: &str) -> bool { daemon == app }`. Call it from `sync_daemon_version` and from the pre-check in `sync_daemon_version_background` so the comparison semantics are documented in one place.
    - Add unit tests in the same file under `#[cfg(test)] mod tests { ... }`:
      - `versions_match_equal`: `versions_match("1.9.0", "1.9.0") == true`.
      - `versions_match_different`: `versions_match("1.9.0", "1.9.0-dev") == false`.
      - `versions_match_empty_daemon`: `versions_match("", "1.9.0") == false`.
      - `versions_match_empty_app`: `versions_match("1.9.0", "") == false`.
      - `versions_match_both_empty`: `versions_match("", "") == true` (degenerate but well-defined).
      - `versions_match_suffix_differs`: `versions_match("1.9.0", "1.9.0 ") == false` (trailing whitespace is NOT normalized — documents the current semantics per design.md "Hypothesized Root Cause" point 4).
    - Run `cargo test versions_match`.
    - _Requirements: 2.3, 3.1_

  - [ ] 4.2 Rust — `sync_daemon_version_background` event emission with stubbed inner function
    - Add unit tests for `sync_daemon_version_background` in `desktop/src-tauri/src/lib.rs` or a companion `lib_tests.rs` that use a mock `AppHandle` capturing emitted events into a `Vec<(String, serde_json::Value)>`.
    - To isolate the wrapper from the real `sync_daemon_version`, refactor the inner call to go through a trait or function pointer injected at test time (see the design.md Change 2 signature — easiest approach: take a `dyn Fn(...) -> Pin<Box<dyn Future<...>>>` parameter in a test-only variant, or use `#[cfg(test)]`-gated module-level `static` override).
    - Test cases covering design.md Properties 3 and 4:
      - `bg_emits_upgrading_then_upgraded_on_ok`: stub returns `Ok(())`. Captured events: exactly `["backend-upgrading", "backend-upgraded"]` in that order.
      - `bg_emits_upgrading_then_failed_on_err`: stub returns `Err("boom")`. Captured events: exactly `["backend-upgrading", "backend-upgrade-failed"]`, failed payload contains `"boom"`.
      - `bg_emits_upgrading_then_failed_on_panic_str`: stub panics with `&str`. Captured events: exactly `["backend-upgrading", "backend-upgrade-failed"]`, failed payload contains the panic message and the prefix `"panic in sync_daemon_version:"`.
      - `bg_emits_upgrading_then_failed_on_panic_string`: stub panics with `String`. Same assertions as above, covers the second downcast arm.
      - `bg_emits_upgrading_then_failed_on_panic_unknown`: stub panics with a custom non-string type. Failed payload contains `"panic in sync_daemon_version (unknown payload)"`.
      - `bg_zero_events_on_version_match`: `get_daemon_version` stubbed to return the same string as `app_version`. Captured events: empty `Vec` (enforces Property 2 pre-check).
    - Run `cargo test sync_daemon_version_background`.
    - _Requirements: 2.3, 3.6_

  - [ ] 4.3 React — `BackendUpgradeBanner` state transitions via Vitest + React Testing Library
    - Create `desktop/src/components/common/BackendUpgradeBanner.test.tsx`.
    - Include a module-level `/** */` docstring describing the test file's purpose and the component under test (per workspace rule swarmai-dev-rules.md "Code Documentation Standards" — Test files).
    - Mock `@tauri-apps/api/event` so `listen` captures the handler and the test can dispatch fake events synchronously. Also mock `../../services/tauri` so `isDesktop()` returns `true`.
    - Test cases:
      - `renders_nothing_by_default`: mount the banner, assert the DOM is empty (null render).
      - `shows_upgrading_on_backend_upgrading_event`: dispatch `backend-upgrading` with `{from: "1.9.0", to: "1.9.0-dev"}`. Assert the rendered text contains both version strings and the word "Upgrading".
      - `shows_upgraded_on_backend_upgraded_event`: dispatch `backend-upgraded` with `{version: "1.9.0-dev"}`. Assert the rendered text contains "Backend upgraded" and the version.
      - `shows_failed_on_backend_upgrade_failed_event`: dispatch `backend-upgrade-failed` with a string error payload. Assert the rendered text contains "Backend upgrade failed" and the error message.
      - `idle_hidden_when_not_desktop`: mock `isDesktop()` to return `false`. Dispatch a `backend-upgrading` event. Assert the banner stays empty (the effect should have early-returned before subscribing).
      - `unsubscribes_on_unmount`: mount, unmount, assert the mock `listen` return function (unlisten) was called.
    - Run `cd desktop && npm test -- --run BackendUpgradeBanner`.
    - _Requirements: 2.3, 2.4_

  - [ ] 4.4 React — `BackendUpgradeBanner` auto-dismiss timers
    - In the same `BackendUpgradeBanner.test.tsx`, use `vi.useFakeTimers()` for two additional test cases:
      - `upgraded_auto_dismisses_after_4s`: dispatch `backend-upgraded`, advance timers by 3999ms, assert banner still visible, advance by 1ms more, assert banner has unmounted content (returned to `idle`).
      - `failed_auto_dismisses_after_8s`: dispatch `backend-upgrade-failed`, advance timers by 7999ms, assert banner still visible, advance by 1ms, assert banner has returned to `idle`.
    - After each timer-using test, call `vi.useRealTimers()` to avoid leaking fake timers into subsequent tests.
    - Also assert the timer is cleared on unmount: mount, dispatch `backend-upgraded`, unmount before 4s elapses, advance timers, assert no state updates occur on the unmounted component (Vitest will warn if this is violated).
    - Run `cd desktop && npm test -- --run BackendUpgradeBanner`.
    - _Requirements: 2.3_

- [ ] 5. Integration tests (end-to-end scenarios covering design.md "Integration Tests")

  - [ ] 5.1 `it_reporter_scenario` — exact reporter reproduction
    - Create `desktop/src-tauri/tests/it_reporter_scenario.rs` as a Rust integration test.
    - Reuse the mock daemon + mock `launchctl` harness from Task 1/2 (via `tests/common/mod.rs`).
    - Scenario setup: mock daemon reports `version: "1.9.0"`, app config reports `"1.9.0-dev"`.
    - Drive `start_backend` through a minimal Tauri test harness; measure wall-clock elapsed from call to `Ok(port)` return.
    - Assert: `elapsed < 10.0` seconds (design.md "Integration Tests" uses 10s as the overlay-dismissal latency bound; the property bound from Task 1 is 5s, this integration test allows slightly more headroom for fixture overhead).
    - Assert: a `backend-upgrading` event was emitted with payload containing both `"1.9.0"` and `"1.9.0-dev"`.
    - Assert: within a 60s await window, exactly one terminal event (`backend-upgraded` in this happy case, since the mocks succeed) is received.
    - Assert: during the 60s await window, a second call to `start_backend` (simulating the overlay health-poll path) still returns `Ok(DAEMON_PORT)` in <1s — confirms the app is "usable" while the background upgrade is in flight (design.md "Integration Tests" `it_reporter_scenario` final assertion).
    - _Requirements: 2.1, 2.2, 2.3, 2.4_

  - [ ] 5.2 `it_upgrade_failure_banner` — bootstrap failure path
    - Create `desktop/src-tauri/tests/it_upgrade_failure_banner.rs`.
    - Scenario: mock daemon version-drifts as in 5.1, but the mock `launchctl` shim returns non-zero on `bootstrap`.
    - Assert: `start_backend` still returns `Ok(DAEMON_PORT)` in <5s (overlay still dismisses — design.md "Edge Cases" "Upgrade fails permanently").
    - Assert: within a 60s await window, exactly one `backend-upgrading` event followed by exactly one `backend-upgrade-failed` event is emitted.
    - Assert: the `backend-upgrade-failed` payload contains a non-empty error string.
    - Assert: no `backend-upgraded` event is emitted.
    - Coordinate with Task 4.3: the banner's 8s auto-hide is tested in unit tests; this integration test only verifies the event-emission side.
    - _Requirements: 2.3, 3.6_

  - [ ] 5.3 `it_panic_failure_banner` — panic inside sync_daemon_version
    - Create `desktop/src-tauri/tests/it_panic_failure_banner.rs`.
    - Scenario: stub `sync_daemon_version` to panic (use the same test-only injection mechanism introduced in Task 4.2).
    - Assert: `start_backend` returns `Ok(DAEMON_PORT)` in <5s.
    - Assert: exactly one `backend-upgrading` event then exactly one `backend-upgrade-failed` event with payload containing `"panic in sync_daemon_version"`.
    - Assert: the test process is still alive and the Tauri runtime has not aborted — this is the live-fire test of design.md Property 4 ("Background Task Cannot Crash the App"). Concretely: after the panic event is received, call `start_backend` again and assert it returns `Ok(...)` — if the runtime crashed, this call would panic.
    - _Requirements: 2.3_

  - [ ] 5.4 `it_happy_path_no_banner` — version match produces zero banner activity
    - Create `desktop/src-tauri/tests/it_happy_path_no_banner.rs`.
    - Scenario: mock daemon version matches app version exactly (both `"1.9.0-dev"`).
    - Assert: `start_backend` returns `Ok(DAEMON_PORT)` in <2s (fast path, no probe retry).
    - Assert: across a 5s observation window after return, ZERO `backend-upgrading` / `backend-upgraded` / `backend-upgrade-failed` events are emitted.
    - Assert: the mock `launchctl` shim was never invoked (no `bootout`, no `bootstrap` entries in its log file).
    - This is the primary preservation-invariant integration test — it enforces design.md Property 2 at the full-stack level.
    - _Requirements: 3.1, 3.5_

- [ ] 6. Manual verification script — `scripts/verify-startup-regression-fix.sh`
  - Create `scripts/verify-startup-regression-fix.sh` (executable, `chmod +x`).
  - Include a header docstring (as shell comments) explaining the script's purpose: belt-and-braces manual verification that the fix works against real macOS launchctl, real PyInstaller cold start, and a real running daemon — the one scenario the mocked Rust integration tests in Task 5 cannot fully capture.
  - Steps the script MUST perform:
    1. Pre-flight: verify macOS (`[[ "$(uname)" == "Darwin" ]]` or exit 1), verify `~/.swarm-ai/daemon` exists (a real daemon is installed), verify the daemon is currently running (`curl -sf http://127.0.0.1:18321/health | grep -q '"status":"healthy"'` or exit 1 with a hint to run `./prod.sh deploy` first).
    2. Record the daemon's current `/health` version string (e.g., `"1.9.0"`) — this is the version that will mismatch the app.
    3. Build the fixed app with `./prod.sh build`. Abort on non-zero exit.
    4. Override the app's bundled version to a drift string (e.g., `"1.9.0-verify"`) by patching `desktop/src-tauri/tauri.conf.json` in-place; save a backup first and restore in an `EXIT` trap so the script is re-runnable.
    5. Rebuild with the overridden version (`./prod.sh build` again).
    6. Launch the built app in the background with stderr redirected to a temp log file. Capture the start timestamp with `date +%s%3N`.
    7. Poll the Tauri log (or a pre-agreed sentinel file the app writes on `backend-ready`) every 100ms for up to 15 seconds, looking for the overlay-dismissal signal. Capture the dismissal timestamp.
    8. Compute `latency_ms = dismissal_ts - start_ts`. Assert `latency_ms < 10000` (10 seconds, matches design.md "Integration Tests" overlay-dismissal bound).
    9. Verify a `backend-upgrading` event was emitted (grep the stderr log for the Rust-side `println!` or a frontend console log that the banner wrote).
    10. Within 90 seconds (PyInstaller cold-start ceiling per design.md "Hypothesized Root Cause" point 2), verify a `backend-upgraded` event was emitted.
    11. Verify the daemon's `/health` version string now matches the app's overridden version — confirms the end-to-end upgrade pipeline worked.
    12. Quit the app cleanly, restore `tauri.conf.json` from backup via the `EXIT` trap, exit 0.
  - On any assertion failure, print the relevant log excerpt and exit non-zero.
  - Document in the script header that this is a manual-run script, not part of CI — it mutates the user's local daemon installation and takes 2–5 minutes per run.
  - _Requirements: 2.1, 2.2, 2.3, 2.4_

- [ ] 7. Build + smoke test (final integration check across the whole repo)

  - [ ] 7.1 Frontend build
    - Run `cd desktop && npm run build`.
    - Assert zero TypeScript errors, zero Vite warnings that would block a production build.
    - This catches any residual type mismatches between `BackendUpgradeBanner` and the rest of the frontend.
    - _Requirements: All_

  - [ ] 7.2 Tauri production build
    - Run `cd desktop && npm run tauri build`.
    - Assert the build produces the `.app` bundle successfully. This exercises the Rust `cargo build --release` path, confirming the new `futures = "0.3"` dep and the new `sync_daemon_version_background` helper compile under release profile.
    - _Requirements: All_

  - [ ] 7.3 Backend regression suite
    - Run `cd backend && pytest`.
    - This is defensive — the fix is Tauri+frontend only, so the Python backend suite should be 100% green with no changes. A red result here indicates an unintended side effect (e.g., a shared schema/event-contract file the fix touched).
    - _Requirements: 3.1–3.7 (preservation)_

  - [ ] 7.4 Frontend unit tests
    - Run `cd desktop && npm test -- --run`.
    - Confirms Tasks 4.3 and 4.4 pass in the full suite alongside existing tests, and confirms no existing tests regressed.
    - _Requirements: All_

  - [ ] 7.5 Deploy to daemon dir and verify status
    - Run `./prod.sh deploy` to install the fixed app's daemon binary to `~/.swarm-ai/daemon`.
    - Run `./prod.sh status` and assert the daemon reports healthy on port 18321.
    - This closes the loop on the rollout flow described in design.md "Migration / Rollout Notes": the first launch of the fixed v1.9.1 against a pre-existing v1.9.0 daemon does exactly one successful background upgrade; subsequent launches are happy-path.
    - Optionally run `./scripts/verify-startup-regression-fix.sh` from Task 6 as the final belt-and-braces check.
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 3.6_

- [ ] 8. Checkpoint — Ensure all tests pass
  - Confirm Tasks 1, 2, 3.7, 3.8 all pass on the fixed code (bug-condition exploration test now PASSES, all five preservation tests still PASS).
  - Confirm Tasks 4.1–4.4, 5.1–5.4, and 7.1–7.5 all pass.
  - Confirm Task 6 manual verification script runs cleanly end-to-end on at least one developer's macOS machine.
  - If any test fails, diagnose the root cause — do not relax assertions or skip tests. The preservation tests in particular are load-bearing for "no regressions introduced by the fix".
  - If questions or ambiguities arise (e.g., a preservation test captures behavior that turns out to be itself a latent bug), surface them to the user before proceeding — do not silently adjust the expected behavior.
