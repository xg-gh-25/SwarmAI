# Bugfix Requirements Document

## Introduction

Chat messages disappear from chat tabs after the app is restarted. The root cause is that tab state (including which `sessionId` each tab holds) was persisted via the Tauri WebKit webview's `localStorage`, which does not survive app restarts on macOS. The WebKit LocalStorage directory is empty — no files are written. On restart, the hook creates a fresh default tab with no `sessionId`, and the welcome screen is shown. All prior messages remain in the SQLite database but no tab references them.

The fix uses a filesystem-first approach: persist open tab state to `~/.swarm-ai/open_tabs.json` via the backend API, completely replacing `localStorage` for tab persistence. On startup, the hook creates a temporary default tab, then `ChatPage` calls `restoreFromFile()` which reads the exact tabs the user had open from the JSON file. Messages are loaded from the SQLite database on demand when a tab becomes active. Tab changes are written back to the file with a 500ms debounce.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN the app is restarted on macOS THEN the system loses all tab state because `localStorage` data written by the Tauri WebKit webview is not persisted to disk, resulting in `loadTabsFromStorage()` returning `null`

1.2 WHEN `loadTabsFromStorage()` returns `null` on startup THEN the system creates a single fresh default tab with no `sessionId`, discarding all knowledge of prior sessions

1.3 WHEN a tab has no `sessionId` after restart THEN the system shows the welcome screen instead of the user's previous conversation, even though messages exist in the database

1.4 WHEN the user had multiple tabs open before restart THEN the system restores only one empty tab instead of the previously open tabs with their conversations

### Expected Behavior (Correct)

2.1 WHEN the app is restarted THEN the system SHALL restore tabs from `~/.swarm-ai/open_tabs.json` via the backend API (`GET /api/settings/open-tabs`), restoring the exact tabs the user had open with their `sessionId`, `title`, and `activeTabId` preserved

2.2 WHEN tabs are restored from the file on startup THEN the system SHALL restore each tab with its title and `sessionId` populated, so that message loading can proceed normally from the SQLite database

2.3 WHEN a restored tab becomes the active tab THEN the system SHALL load that session's messages from the database and display the conversation, not the welcome screen

2.4 WHEN the user had multiple tabs open before restart THEN the system SHALL restore those exact tabs (up to MAX_OPEN_TABS = 6), preserving the user's multi-tab workspace

2.5 WHEN a tab's state changes during normal operation (add, close, switch, rename, sessionId update) THEN the system SHALL write the updated tab state to `~/.swarm-ai/open_tabs.json` via the backend API (`PUT /api/settings/open-tabs`) with a 500ms debounce to avoid excessive writes

2.6 WHEN `open_tabs.json` does not exist on startup (fresh install) THEN the system SHALL keep the default tab and the user starts with a clean workspace

2.7 WHEN the file restore API call fails (e.g., backend not yet started) THEN the system SHALL log a warning and keep the default tab (graceful degradation)

2.8 WHEN the `PUT /api/settings/open-tabs` endpoint receives a request without a `tabs` array THEN the system SHALL reject it with HTTP 422

### Unchanged Behavior (Regression Prevention)

3.1 WHEN a user starts a brand new conversation (no prior session) THEN the system SHALL CONTINUE TO create a fresh tab, obtain a `sessionId` from the streaming response, and function normally

3.2 WHEN the user switches between tabs THEN the system SHALL CONTINUE TO save and restore tab state from the in-memory map without disruption

3.3 WHEN the user opens the Chat History sidebar and clicks on a session THEN the system SHALL CONTINUE TO load that session's messages into a tab

3.4 WHEN a chat is actively streaming THEN the system SHALL CONTINUE TO stream messages without interruption or behavioral change

3.5 WHEN `sessionStorage` is used for pending question persistence THEN the system SHALL CONTINUE TO function independently of the tab persistence mechanism

3.6 WHEN the user closes a tab THEN the system SHALL CONTINUE TO abort any active streaming, remove the tab, and auto-create a new default tab if it was the last one

3.7 WHEN the user adds a new tab THEN the system SHALL CONTINUE TO enforce the MAX_OPEN_TABS limit of 6 tabs
