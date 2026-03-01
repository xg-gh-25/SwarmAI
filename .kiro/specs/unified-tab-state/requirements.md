# Requirements Document: Unified Tab State

## Status: NOT STARTED — Placeholder spec

## Introduction

This spec consolidates tab state management into a single source of truth, eliminating the triple-bookkeeping pattern across `useTabState`, `tabStateRef`, and `tabStatuses` in the SwarmAI chat experience.

## Background

Currently tab state lives in three separate stores:
1. `useTabState` (desktop/src/hooks/useTabState.ts) — `openTabs`, `activeTabId`, localStorage persistence
2. `tabStateRef` (in useChatStreamingLifecycle) — per-tab `Map<string, TabState>` with messages, sessionId, streaming state
3. `tabStatuses` (in useChatStreamingLifecycle) — `Record<string, TabStatus>` useState mirror

Any tab operation must update all three stores, creating drift risk and maintenance burden.

## Deferred from

`.kiro/specs/chat-experience-cleanup/` — Phase D Tasks 7.4, 7.5, 7.6, 7.7

## Requirements

_To be defined when this spec is activated. See chat-experience-cleanup design.md Req 17 for the original requirements._

## Key Design Decisions (from prior analysis)

- Single `useRef<Map<string, UnifiedTab>>` as authoritative store
- `useState` counter as re-render trigger
- Derived views (`openTabs`, `tabStatuses`) via `useMemo`
- localStorage persistence for serializable subset only
- Property tests: tab operation invariants + per-tab state isolation
