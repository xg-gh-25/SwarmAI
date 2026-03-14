# Proactive Intelligence — Level 4: Mid-Session Signals

## Status: DESIGN — awaiting review

## Problem

L0-L3 only fire at session start. Once the user is working, the agent goes silent about meta-concerns — context running out, session dragging on, repeated errors, upcoming calendar events. The user has to notice these things themselves.

## Goal

Push non-blocking signals to the user **during** an active session when conditions warrant attention. Think: a quiet tap on the shoulder, not a modal dialog.

## Architecture

### Why a new SSE endpoint

The existing chat SSE stream is request-scoped: opens when the user sends a message, closes when the agent finishes responding. Between messages, there's no channel. We need an always-on connection.

```
Frontend                          Backend
   |                                |
   |--- GET /api/proactive/stream --|  (persistent SSE, opened on app start)
   |                                |
   |        [monitor ticks]         |
   |                                |
   |<-- event: signal --------------|  (when condition triggers)
   |<-- event: heartbeat -----------|  (every 30s, keeps alive)
   |                                |
   |--- POST /api/proactive/dismiss |  (user dismisses a signal)
   |                                |
```

### Monitor architecture

```
ProactiveMonitor (singleton, started with app)
    |
    +--> _tick() runs every 30s
    |       |
    |       +--> _check_context_usage()     # context window estimate
    |       +--> _check_session_duration()   # wall clock since first message
    |       +--> _check_error_patterns()     # repeated tool failures
    |       +--> _check_focus_drift()        # user hasn't touched P0 in N messages
    |       +--> _check_external_events()    # calendar (requires MCP, optional)
    |       |
    |       +--> _apply_rate_limits()        # deduplicate, respect cooldowns
    |       |
    |       +--> _emit_signals()             # push to subscribed SSE connections
    |
    +--> _connections: dict[session_id, asyncio.Queue]
```

Single background `asyncio.Task`, not one per session. It iterates over active sessions each tick. Lightweight — no LLM calls, no file I/O on hot path.

### Signal schema

```json
{
  "type": "proactive_signal",
  "signal_id": "ctx-usage-70-abc123",
  "category": "context_usage",
  "severity": "warning",
  "title": "Context window ~75% full",
  "body": "Consider wrapping up this thread or compacting. ~25K tokens remaining.",
  "actions": [
    {"label": "Dismiss", "action": "dismiss"},
    {"label": "Compact now", "action": "compact"}
  ],
  "session_id": "abc123",
  "timestamp": "2026-03-15T01:30:00Z"
}
```

### Frontend rendering

Non-blocking toast in the bottom-right corner. Auto-dismiss after 15s unless pinned. Stacks up to 2 visible at once (older ones collapse).

```
+------------------------------------------+
| ! Context window ~75% full               |
|   Consider wrapping up or compacting.    |
|   [Dismiss]  [Compact now]               |
+------------------------------------------+
```

No modal dialogs. No interruption of typing. The signal is informational — the user decides whether to act.

## Signal Catalog

### Tier 1: Always on (no opt-in needed)

| ID | Category | Trigger | Severity | Message |
|----|----------|---------|----------|---------|
| S1 | `context_usage` | Token estimate >70% of budget | warning | "Context ~{pct}% full — consider wrapping up" |
| S2 | `context_usage` | Token estimate >90% of budget | critical | "Context nearly full — compaction imminent" |
| S3 | `error_pattern` | Same tool error 3x in a session | warning | "Repeated {tool} failures — step back?" |
| S4 | `session_duration` | Wall clock >2h since first message | info | "2+ hours in — good stopping point?" |

### Tier 2: Opt-in (requires config or MCP)

| ID | Category | Trigger | Severity | Message |
|----|----------|---------|----------|---------|
| S5 | `calendar` | Meeting starts within 30min (Outlook MCP) | warning | "Meeting '{title}' in {min}min" |
| S6 | `focus_drift` | Top P0 not mentioned in last 10 messages | info | "P0 '{title}' still open — revisit?" |
| S7 | `learning` | User followed suggestion 3 sessions in a row | info | "Nice streak on {work_type} work" |

## Rate Limiting

| Rule | Value | Rationale |
|------|-------|-----------|
| Min interval per category | 10 min | Don't nag |
| Cooldown after dismiss | 30 min for that category | Respect the user's choice |
| Max visible signals | 2 | Don't clutter the UI |
| Max signals per session | 10 | Hard cap — after that, silent |
| S4 (duration) fires once | Once per session | "2h in" is enough, don't repeat at 3h, 4h |

```python
@dataclass
class RateLimit:
    last_fired: dict[str, datetime]     # category -> last fire time
    dismissed: dict[str, datetime]       # category -> dismiss time
    session_count: int = 0              # total signals this session

    def can_fire(self, category: str, now: datetime) -> bool:
        if self.session_count >= 10:
            return False
        last = self.last_fired.get(category)
        if last and (now - last).total_seconds() < 600:
            return False
        dismissed_at = self.dismissed.get(category)
        if dismissed_at and (now - dismissed_at).total_seconds() < 1800:
            return False
        return True
```

## Context Usage Estimation

L4 needs to estimate context window usage without calling the API. Options:

### Option A: Count from system prompt + messages (recommended)

```python
def _estimate_context_usage(session_id: str) -> float:
    """Estimate context usage as fraction 0.0-1.0."""
    # System prompt: known at session start (~8K tokens for SwarmAI)
    # Messages: sum of user + assistant message lengths / 4
    # Tool calls: rough estimate from call count * avg size
    # Budget: model's context window (200K for Sonnet)
    system_tokens = len(system_prompt) // 4
    message_tokens = sum(len(m.content) // 4 for m in messages)
    return (system_tokens + message_tokens) / context_budget
```

Accuracy: ~80% (good enough for warning thresholds). No API call needed.

### Option B: Piggyback on SDK usage headers

If the SDK returns token usage in response metadata, cache it. Most accurate but only updates after each agent response.

**Decision needed:** A, B, or both (B when available, A as fallback)?

## Data Flow

```
Session start
    |
    v
ProactiveMonitor.register_session(session_id, context_budget)
    |
    v
[Every 30s tick]
    |
    +--> For each active session:
    |       +--> Collect sensor readings (context, duration, errors)
    |       +--> Evaluate triggers against thresholds
    |       +--> Apply rate limits
    |       +--> If signal passes all gates → emit to session's queue
    |
    v
[SSE endpoint reads from queue]
    |
    v
Frontend renders toast

Session end
    |
    v
ProactiveMonitor.unregister_session(session_id)
```

## Implementation Plan

### Files

| File | Purpose | Est. lines |
|------|---------|-----------|
| `backend/core/proactive_monitor.py` | Monitor singleton, sensors, rate limiter | ~200 |
| `backend/routers/proactive.py` | SSE endpoint + dismiss endpoint | ~60 |
| `backend/tests/test_proactive_monitor.py` | Unit tests for sensors + rate limiting | ~150 |
| `desktop/src/hooks/useProactiveSignals.ts` | SSE subscription hook | ~40 |
| `desktop/src/components/ProactiveToast.tsx` | Toast renderer | ~60 |

### Phases

**Phase 1: Core (S1-S4, ~3h)**
- ProactiveMonitor with tick loop
- Context usage + session duration + error pattern sensors
- Rate limiter
- SSE endpoint
- Unit tests

**Phase 2: Frontend (~2h)**
- `useProactiveSignals` hook
- Toast component
- Dismiss → POST callback
- Compact action wiring

**Phase 3: Opt-in signals (S5-S7, ~2h)**
- Calendar check via Outlook MCP (if connected)
- Focus drift detection
- Learning streak detection

## Open Questions

1. **Context estimation:** Option A (count chars), Option B (SDK headers), or both?
2. **Toast position:** Bottom-right (standard) or top-right (closer to tab bar)?
3. **Compact action:** Should "Compact now" trigger automatic compaction, or just remind the user?
4. **S6 focus drift:** Is nudging about P0 helpful or annoying? Could feel like nagging.
5. **Monitor lifecycle:** Start on app launch (even before first message) or on first session start?

## Design Decisions Already Made

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Separate SSE endpoint | Yes | Chat SSE is request-scoped, can't push between messages |
| 30s tick interval | Yes | Balance between responsiveness and overhead |
| No LLM calls | Yes | Must be fast — sensors are pure computation |
| Rate limit per category | 10min | Don't nag — one signal per topic is usually enough |
| Max 2 visible toasts | Yes | UI clutter is worse than missing a signal |
| Singleton monitor | Yes | One background task, not one per session |

## Tradeoffs

| Approach | Pro | Con |
|----------|-----|-----|
| **Separate SSE (chosen)** | Always-on, independent of chat flow | Extra connection per tab |
| Heartbeat piggyback | No new connection | Only fires during active streaming |
| Polling endpoint | Simpler frontend | Unnecessary load, latency |
| WebSocket | Bidirectional | Overkill, Tauri complicates WS |

## Changelog

- 2026-03-15: v1.0 — Initial L4 design doc.
