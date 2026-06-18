/**
 * Streaming State Machine — explicit reducer for chat streaming lifecycle.
 *
 * Replaces 18+ boolean flags in useChatStreamingLifecycle.ts with a single
 * discriminated mode enum. Every (mode, event) pair has exactly one outcome.
 * Invalid transitions are no-ops (logged in dev, never crash).
 *
 * Architecture:
 * - Pure function: streamingReducer(state, event) → state
 * - Zero side effects (effects live in useStreamingEffects, future P2)
 * - Per-tab: each tab gets its own StreamingState instance
 * - Hot path bypass: text_delta/thinking_delta go direct to MessageStore,
 *   never trigger a reducer dispatch (no React re-render per token)
 *
 * Design doc: Knowledge/Designs/2026-06-18-usechatstreaminglifecycle-decomposition-design.md
 */

// ═══════════════════════════════════════════════════════════════════
// Types
// ═══════════════════════════════════════════════════════════════════

/** The 10 top-level operational modes of a streaming tab. */
export type StreamingMode =
  | 'idle'             // No active stream. User can type.
  | 'pending'          // Message sent, waiting for session_start from backend.
  | 'streaming'        // Active SSE, events flowing.
  | 'reconnecting'     // Connection-phase error, auto-retry in progress.
  | 'resuming'         // Backend subprocess respawning (cold-start resume).
  | 'self_healing'     // Mid-stream disconnect, grace period active (30s).
  | 'waiting_input'    // Agent asked a question (AskUserQuestion), waiting for answer.
  | 'permission_needed'// Agent needs permission for dangerous command.
  | 'session_busy'     // SESSION_BUSY error, polling for completion.
  | 'drain_pending'    // Result received, draining queued message before returning to idle.
  | 'error';           // Unrecoverable error. User can retry or send new message.

/** Events that drive state transitions. */
export type StreamingEvent =
  | { type: 'SEND_MESSAGE' }
  | { type: 'SESSION_START'; sessionId: string }
  | { type: 'TEXT_DELTA' }       // Hot path marker — reducer just clears stall
  | { type: 'RESULT'; hasQueuedMessage: boolean }
  | { type: 'ERROR'; phase: 'connection' | 'mid_stream'; message: string }
  | { type: 'USER_STOP' }
  | { type: 'RECONNECT_SUCCESS' }
  | { type: 'RECONNECT_FAIL' }
  | { type: 'ASK_USER_QUESTION' }
  | { type: 'PERMISSION_REQUEST' }
  | { type: 'ANSWER_SUBMITTED' }    // User answered question → resume streaming
  | { type: 'PERMISSION_DECIDED' }  // User decided permission → resume streaming
  | { type: 'HEAL_TIMEOUT' }        // 30s grace expired without reconnect
  | { type: 'HEAL_RECONNECTED' }    // Backend reconnected during grace period
  | { type: 'DRAIN_COMPLETE' }
  | { type: 'BUSY_RESOLVED' }
  | { type: 'RESUME_START' }        // Backend signals subprocess respawning
  | { type: 'RESUME_COMPLETE' }     // Backend resume done, streaming resumes
  | { type: 'STALL_DETECTED' }      // No real events for >threshold
  | { type: 'STALL_CLEARED' };      // Event received, clear stall flag

/** Immutable state for one streaming tab. */
export interface StreamingState {
  /** Current operational mode. THE source of truth for "what's happening." */
  mode: StreamingMode;
  /** Stream generation counter. Invalidates stale handlers on interrupt. */
  streamGen: number;
  /** Current reconnection attempt (1-indexed). 0 = not reconnecting. */
  reconnectAttempt: number;
  /** Max reconnection attempts before giving up. */
  maxReconnectAttempts: number;
  /** Whether a queued message is waiting to drain after current stream. */
  drainQueued: boolean;
  /** Whether stream appears stalled (no real events for >threshold). */
  isStalled: boolean;
  /** Whether a tool is currently executing (affects stall threshold). */
  toolExecuting: boolean;
  /** Last error message (null when not in error mode). */
  error: string | null;
  /** Session ID once established. */
  sessionId: string | null;
}

// ═══════════════════════════════════════════════════════════════════
// Initial State
// ═══════════════════════════════════════════════════════════════════

export const INITIAL_STATE: StreamingState = {
  mode: 'idle',
  streamGen: 0,
  reconnectAttempt: 0,
  maxReconnectAttempts: 3,
  drainQueued: false,
  isStalled: false,
  toolExecuting: false,
  error: null,
  sessionId: null,
};

// ═══════════════════════════════════════════════════════════════════
// Reducer
// ═══════════════════════════════════════════════════════════════════

/**
 * Pure state machine reducer. Deterministic: same (state, event) → same result.
 *
 * Design principles:
 * 1. Invalid transitions return state unchanged (no-op, never crash)
 * 2. Mode is the ONLY discriminant (no boolean flag combinations)
 * 3. Side effects are NEVER triggered here (effects layer handles those)
 * 4. streamGen increments on any interruption to invalidate stale handlers
 */
export function streamingReducer(
  state: StreamingState,
  event: StreamingEvent,
): StreamingState {
  switch (state.mode) {
    // ─── IDLE ───────────────────────────────────────────────────
    case 'idle':
      switch (event.type) {
        case 'SEND_MESSAGE':
          return { ...state, mode: 'pending', error: null, isStalled: false };
        default:
          return state; // No-op: all other events invalid in idle
      }

    // ─── PENDING ────────────────────────────────────────────────
    case 'pending':
      switch (event.type) {
        case 'SESSION_START':
          return {
            ...state,
            mode: 'streaming',
            sessionId: event.sessionId,
            isStalled: false,
            toolExecuting: false,
          };
        case 'ERROR':
          return {
            ...state,
            mode: 'error',
            error: event.message,
            streamGen: state.streamGen + 1,
          };
        case 'USER_STOP':
          return { ...state, mode: 'idle', streamGen: state.streamGen + 1 };
        default:
          return state;
      }

    // ─── STREAMING ──────────────────────────────────────────────
    case 'streaming':
      switch (event.type) {
        case 'TEXT_DELTA':
          // Hot path — only clears stall flag if set
          return state.isStalled ? { ...state, isStalled: false } : state;
        case 'STALL_DETECTED':
          return { ...state, isStalled: true };
        case 'STALL_CLEARED':
          return { ...state, isStalled: false };
        case 'RESULT':
          if (event.hasQueuedMessage) {
            return { ...state, mode: 'drain_pending', drainQueued: true, isStalled: false };
          }
          return {
            ...state,
            mode: 'idle',
            isStalled: false,
            toolExecuting: false,
            drainQueued: false,
          };
        case 'ERROR':
          if (event.phase === 'connection') {
            return {
              ...state,
              mode: 'reconnecting',
              reconnectAttempt: 1,
              isStalled: false,
            };
          }
          // Mid-stream error → self-healing grace period
          return { ...state, mode: 'self_healing', isStalled: false };
        case 'USER_STOP':
          return {
            ...state,
            mode: 'idle',
            streamGen: state.streamGen + 1,
            isStalled: false,
            toolExecuting: false,
          };
        case 'ASK_USER_QUESTION':
          return {
            ...state,
            mode: 'waiting_input',
            streamGen: state.streamGen + 1,
            isStalled: false,
          };
        case 'PERMISSION_REQUEST':
          return {
            ...state,
            mode: 'permission_needed',
            isStalled: false,
          };
        case 'RESUME_START':
          return { ...state, mode: 'resuming', isStalled: false };
        default:
          return state;
      }

    // ─── RECONNECTING ───────────────────────────────────────────
    case 'reconnecting':
      switch (event.type) {
        case 'RECONNECT_SUCCESS':
        case 'SESSION_START':
          return { ...state, mode: 'streaming', reconnectAttempt: 0, isStalled: false };
        case 'RECONNECT_FAIL':
          if (state.reconnectAttempt >= state.maxReconnectAttempts) {
            return {
              ...state,
              mode: 'error',
              error: 'Connection failed after max retries',
              reconnectAttempt: 0,
              streamGen: state.streamGen + 1,
            };
          }
          return { ...state, reconnectAttempt: state.reconnectAttempt + 1 };
        case 'USER_STOP':
          return {
            ...state,
            mode: 'idle',
            reconnectAttempt: 0,
            streamGen: state.streamGen + 1,
          };
        default:
          return state;
      }

    // ─── RESUMING ───────────────────────────────────────────────
    case 'resuming':
      switch (event.type) {
        case 'RESUME_COMPLETE':
        case 'SESSION_START':
        case 'TEXT_DELTA':
          return { ...state, mode: 'streaming', isStalled: false };
        case 'ERROR':
          return {
            ...state,
            mode: 'error',
            error: event.message,
            streamGen: state.streamGen + 1,
          };
        case 'USER_STOP':
          return { ...state, mode: 'idle', streamGen: state.streamGen + 1 };
        default:
          return state;
      }

    // ─── SELF_HEALING ───────────────────────────────────────────
    case 'self_healing':
      switch (event.type) {
        case 'HEAL_RECONNECTED':
        case 'SESSION_START':
        case 'TEXT_DELTA':
          // Backend reconnected within grace period — resume streaming
          return { ...state, mode: 'streaming', isStalled: false };
        case 'HEAL_TIMEOUT':
          // Grace period expired — surface the error
          return {
            ...state,
            mode: 'error',
            error: 'Connection lost during streaming',
            streamGen: state.streamGen + 1,
          };
        case 'USER_STOP':
          return { ...state, mode: 'idle', streamGen: state.streamGen + 1 };
        default:
          return state;
      }

    // ─── WAITING_INPUT ──────────────────────────────────────────
    case 'waiting_input':
      switch (event.type) {
        case 'ANSWER_SUBMITTED':
          return { ...state, mode: 'streaming', isStalled: false };
        case 'USER_STOP':
          return { ...state, mode: 'idle', streamGen: state.streamGen + 1 };
        default:
          return state;
      }

    // ─── PERMISSION_NEEDED ──────────────────────────────────────
    case 'permission_needed':
      switch (event.type) {
        case 'PERMISSION_DECIDED':
          return { ...state, mode: 'streaming', isStalled: false };
        case 'USER_STOP':
          return { ...state, mode: 'idle', streamGen: state.streamGen + 1 };
        default:
          return state;
      }

    // ─── SESSION_BUSY ───────────────────────────────────────────
    case 'session_busy':
      switch (event.type) {
        case 'BUSY_RESOLVED':
          return { ...state, mode: 'idle' };
        case 'USER_STOP':
          return { ...state, mode: 'idle', streamGen: state.streamGen + 1 };
        default:
          return state;
      }

    // ─── DRAIN_PENDING ──────────────────────────────────────────
    case 'drain_pending':
      switch (event.type) {
        case 'DRAIN_COMPLETE':
          return { ...state, mode: 'idle', drainQueued: false };
        case 'SEND_MESSAGE':
          // New message while drain pending — drain becomes the send
          return { ...state, mode: 'pending', drainQueued: false };
        case 'USER_STOP':
          return {
            ...state,
            mode: 'idle',
            drainQueued: false,
            streamGen: state.streamGen + 1,
          };
        default:
          return state;
      }

    // ─── ERROR ──────────────────────────────────────────────────
    case 'error':
      switch (event.type) {
        case 'SEND_MESSAGE':
          return { ...state, mode: 'pending', error: null };
        default:
          return state;
      }

    default:
      return state;
  }
}

// ═══════════════════════════════════════════════════════════════════
// Helpers
// ═══════════════════════════════════════════════════════════════════

/** Check if the current mode means "actively streaming" (spinner shows). */
export function isActivelyStreaming(state: StreamingState): boolean {
  return (
    state.mode === 'streaming' ||
    state.mode === 'pending' ||
    state.mode === 'reconnecting' ||
    state.mode === 'resuming' ||
    state.mode === 'self_healing' ||
    state.mode === 'drain_pending'
  );
}

/** Check if user input is blocked (can't type new message). */
export function isInputBlocked(state: StreamingState): boolean {
  return (
    state.mode !== 'idle' &&
    state.mode !== 'error' &&
    state.mode !== 'waiting_input' &&
    state.mode !== 'permission_needed'
  );
}

/** Get human-readable status for UI display. */
export function getStatusLabel(state: StreamingState): string | null {
  switch (state.mode) {
    case 'idle': return null;
    case 'pending': return 'Thinking...';
    case 'streaming': return state.isStalled ? 'Seems stuck...' : null;
    case 'reconnecting': return `Reconnecting (attempt ${state.reconnectAttempt})...`;
    case 'resuming': return 'Resuming session...';
    case 'self_healing': return null; // Invisible to user (grace period)
    case 'waiting_input': return 'Waiting for your answer';
    case 'permission_needed': return 'Permission required';
    case 'session_busy': return 'Waiting for backend...';
    case 'drain_pending': return null; // Invisible transition
    case 'error': return null; // Error shown via error block
    default: return null;
  }
}
