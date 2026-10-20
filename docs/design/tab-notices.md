---
title: Tab notices — tab/session events stay in their tab
date: 2026-10-20
created: 2026-10-20
updated: 2026-10-20
project: SwarmAI
status: implemented (frontend)
---

# Tab notices: tab/session events stay in their tab

Status: implemented (frontend). Owner files: `desktop/src/stores/tabNoticeStore.ts`,
`desktop/src/pages/chat/components/TabNoticeBar.tsx`.

## Rule

The top-right toast stack is for **system-level** events only (backend health, rate
limits, upgrade, Settings results, file tree, opening a tab failed). An event that
belongs to one chat tab or session is shown **in that tab**: a notice bar above the
chat input, plus a dot on the tab header while the tab is in the background.
`src/__tests__/noTabScopedToasts.test.ts` fails CI if tab-scoped code calls `addToast(`.

## Isolation rules (`tabNoticeStore`)

1. `post(tab, expectedSessionId, notice)` takes the live tab object captured when the
   event happened. It never reads the active tab. It drops the write when the tab is
   gone or `tab.sessionId !== expectedSessionId`.
2. Every write gets a token. Auto-dismiss timers and callers that retract "their"
   notice pass the token back, so an older timer or a late `[DONE]` cannot remove a
   newer notice that has the same id.
3. Each notice records the tab's sessionId at write time. The bar shows only notices of
   the tab's current session. `syncSession` (run by the bar from the live tab, and by
   every `post`) drops other sessions' notices for good.
4. A notice written before the tab had a session (`undefined`, e.g. MCP warning before
   `session_start`) is adopted by the first session the tab binds.
5. SDK compaction (`session_cleared`) is the same conversation under a new id:
   `renameSession` carries the notices over.
6. `closeTab` calls `clearTab`: notices and timers die with the tab.
7. Lists are immutable (a new array per change) and capped at 5 per tab.
8. A notice action re-reads the tab at click time and runs only if the tab still exists
   and is still on the notice's session; it receives the notice's own tabId.

Notices are runtime-only (not in `open_tabs.json`). After an app restart they are gone;
the session resumes and any still-relevant condition is raised again.

## Migrated call sites

| Event | Before | Now |
|---|---|---|
| "Swarm is asking" on a background tab | toast | removed; tab header shows `waiting_input` |
| Lost question with no host bubble (reconcile) | toast | in-tab notice `ask-uq-<id>` |
| Context critical | one global toast driven by the active tab | in-tab notice per tab, once per critical episode |
| recovery_exhausted | toast + Start fresh | in-tab notice; dropped for a superseded stream handler |
| still_working | toast, active tab only | in-tab notice on its own tab; retracted at turn end by token |
| Reconnect succeeded | toast | removed; the "Reconnecting…" indicator going away is the signal |
| Requeue lost attachment | toast | in-tab notice |
| MCP health warning | toast | in-tab notice |
| Heal-grace connection lost | toast (no tab name, fired from background timers) | in-tab error on the tab, checked against the session captured when grace started |
| Silent-turn affordance (MessageStore watchdog) | one shared toast | in-tab notice, bound to each store's tabId |
| Refresh failed / screenshot failed | toast | in-tab notice (tab captured at request start) |
| Compact / Save to Memory result | toast | short note under that message |

## Start fresh session

`pages/chat/resetTab.ts` resets the tab named by id (its MessageStore + tabMapRef
entry). The old code did `selectTab(id); handleNewChat()`, and `handleNewChat` clears
the active tab via React state, which could be a different tab by then.

## Tests

- `stores/__tests__/tabNoticeStore.test.ts`: fast-check interleavings of post / session
  change / close / dismiss / timer fire, plus pinned race cases.
- `pages/chat/__tests__/resetTab.test.ts`: resetting a background tab leaves the active
  tab untouched.
- `__tests__/contextUsage.test.ts`, `__tests__/useChatStreamingLifecycle.test.ts`:
  notices land on the event's own tab; superseded handlers raise nothing.
- `__tests__/noTabScopedToasts.test.ts`: the regression guard. It bans `addToast(` in
  `useChatStreamingLifecycle.ts`, `ChatPage.tsx` and `pages/chat/**` outright. The chat
  page's system-level toasts (opening / landing in a tab failed) are named raisers in
  `hooks/useSystemToasts.ts`; a new one is added there, so there is no allowlist.
