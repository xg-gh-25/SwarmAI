# Bugfix Requirements Document

## Introduction

After app restart, the chat UI appears to lose its Chat Tabs and Chat Message area — the input box and right sidebar shift up as if the middle content vanished. HTML DOM inspection reveals the actual root cause: the entire `<div id="root">` app tree is being duplicated 10+ times in the document. Each copy renders correctly (tabs, messages, input are all present), but the massive vertical stacking of `h-screen` containers pushes visible content off-screen, creating the illusion of disappearing UI elements.

The duplication occurs in `desktop/src/main.tsx` where `createRoot(document.getElementById('root')!)` is called without any idempotency guard. During Tauri webview reload/restart cycles, the entry script re-executes and mounts a new React root without unmounting or clearing the previous one, causing the DOM to accumulate duplicate app trees.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN the Tauri app restarts or the webview reloads THEN the system mounts a new React root via `createRoot` without checking for or cleaning up any previously mounted React tree, resulting in multiple full app trees coexisting in the DOM

1.2 WHEN multiple React roots are mounted into the same `#root` div THEN the system renders 10+ duplicate copies of the entire UI (tabs, messages, input, sidebar) stacked vertically, each claiming `100vh` height via `h-screen`

1.3 WHEN the duplicated app trees overflow the viewport THEN the system visually hides the Chat Tabs and Chat Message area from the user's view because the visible viewport only shows a fraction of the stacked content, making it appear as if those elements have disappeared while the input box and right sidebar shift upward

### Expected Behavior (Correct)

2.1 WHEN the Tauri app restarts or the webview reloads THEN the system SHALL ensure only a single React root is mounted into the `#root` element by clearing the container's existing children via `replaceChildren()` before mounting, so that no duplicate app trees accumulate in the DOM. The system SHALL log a warning when stale children are detected and cleared.

2.2 WHEN the React entry point script executes THEN the system SHALL mount exactly one instance of the App component, and the `#root` div SHALL contain a single React-managed subtree at all times

2.3 WHEN the app completes its restart/reload cycle THEN the system SHALL render the Chat Tabs, Chat Message area, input box, and right sidebar in their correct layout positions within a single `h-screen` container, with no vertical overflow caused by duplicate trees

### Unchanged Behavior (Regression Prevention)

3.1 WHEN the app starts for the first time (cold start, no prior mount) THEN the system SHALL CONTINUE TO mount the React app normally into the `#root` div and render the full three-column layout

3.2 WHEN the app is running normally without any restart/reload THEN the system SHALL CONTINUE TO maintain a single React root with correct layout, tab state, and message rendering

3.3 WHEN tab state is persisted in `open_tabs.json` and the app restarts THEN the system SHALL CONTINUE TO restore tabs and their messages correctly via the existing `doRestore` → `restoreFromFile` → `loadSessionMessages` flow

3.4 WHEN the BackendStartupOverlay is shown during production startup THEN the system SHALL CONTINUE TO delay route mounting until the backend is ready, preserving the existing startup sequence

3.5 WHEN React StrictMode is enabled in development THEN the system SHALL CONTINUE TO function correctly with StrictMode's intentional double-invocation of effects and renders (this must not be confused with the bug's duplicate mounting)
