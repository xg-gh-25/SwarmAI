import type { ChatRequest, StreamEvent, ChatSession, ChatMessage, PermissionResponse, StreamingStateEntry, AskUserQuestion } from '../types';
import { toEditorContextPayload } from '../utils/uiContext';

/** Raw snake_case shape of a streaming-state entry as emitted by the backend.
 *
 *  CRITICAL: the WAITING_INPUT state is SHARED by two different prompt kinds
 *  (session_unit.py: one WAITING_INPUT state). The backend emits `pending_question`
 *  for BOTH, with DIFFERENT shapes:
 *    - AskUserQuestion (streaming_orchestrator.py:484): {tool_use_id, questions}
 *    - command-permission prompt (streaming_orchestrator.py:340): {tool_use_id,
 *      request_id, tool_name, tool_input, reason, options} — NO `questions` key.
 *  The frontend mirror only re-surfaces AskUserQuestion (permission has its own
 *  cmd_permission_request render path), so `questions` is optional here and the
 *  mapper below treats a missing/empty `questions` as "not an AskUserQuestion". */
interface RawStreamingStateEntry {
  streaming?: boolean;
  state?: string;
  waiting_input?: boolean;
  pending_count?: number;
  pending_question?: {
    tool_use_id: string;
    questions?: AskUserQuestion[];
    // permission-prompt-only fields (present when this is NOT an AskUserQuestion):
    request_id?: string;
    options?: string[];
  } | null;
  last_drained_seqs?: number[];
  post_disconnect_flushing?: boolean;
}
import api from './api';
import { getApiBaseUrl } from './tauri';

// ---------------------------------------------------------------------------
// Stall detection constant
// ---------------------------------------------------------------------------

/**
 * If no data (including heartbeats) is received on an active SSE stream for
 * this duration, the stream is considered stalled and the reader is cancelled,
 * triggering the onError path which feeds into the reconnection logic.
 *
 * 90s (not 45s): kept in sync with MessageStore's watchdog. A cold tab's
 * first-token latency can be ~38-45s due to Bedrock cache-creation of the
 * large injected context; a 45s budget raced that and aborted valid streams.
 *
 * @see Requirements 2.6 — Stall detection triggers reconnection
 */
export const STALL_TIMEOUT_MS = 90_000;

// Liveness-gated stall policy lives in the shared, dependency-free stallPolicy
// module (single authority — the MessageStore watchdog imports the SAME fn, AC4).
// Re-exported here so existing chat.ts consumers/tests keep their import path.
import {
  decideStallAction,
  UNKNOWN_REARM_BUDGET_MS,
  ALIVE_REARM_CAP_MS,
  type StallLiveness,
} from './stallPolicy';
export { decideStallAction, UNKNOWN_REARM_BUDGET_MS, ALIVE_REARM_CAP_MS };
export type { StallLiveness };

/** Optional liveness wiring passed to the streaming methods (A2). The caller
 *  (useChatStreamingLifecycle) supplies the loop-independent verdict + the
 *  affordance callback; omitting it preserves legacy bounded behaviour. */
export interface StreamLivenessOpts {
  isBackendLive?: () => StallLiveness;
  onAffordance?: () => void;
}

// ---------------------------------------------------------------------------
// Voice transcription
// ---------------------------------------------------------------------------

export interface TranscribeResult {
  transcript: string;
  language: string;
  durationMs: number;
}

// snake_case → camelCase for transcribe response (backend returns duration_ms)
interface TranscribeRawResponse {
  transcript: string;
  language: string;
  duration_ms: number;
}

/**
 * Send recorded audio to backend for transcription via Amazon Transcribe.
 *
 * @param audioBlob - Recorded audio blob from MediaRecorder
 * @param language - Optional BCP-47 language code (default: server decides)
 * @returns Transcribed text, language, and duration (camelCase)
 */
export async function transcribeAudio(
  audioBlob: Blob,
  language?: string,
): Promise<TranscribeResult> {
  const form = new FormData();
  form.append('audio', audioBlob, 'recording.webm');
  if (language) form.append('language', language);

  // Do NOT set Content-Type manually — Axios detects FormData and sets
  // the correct multipart/form-data header with boundary automatically.
  // Explicit Content-Type breaks the boundary string.
  const res = await api.post<TranscribeRawResponse>('/chat/transcribe', form, {
    timeout: 60_000, // 60s for long recordings + Transcribe processing
  });

  // Convert snake_case → camelCase per project convention
  return {
    transcript: res.data.transcript,
    language: res.data.language,
    durationMs: res.data.duration_ms,
  };
}

// Convert content blocks from camelCase to snake_case for API
// The input is a generic array that may contain image/document blocks
const toSnakeCaseContent = (content: unknown[]): unknown[] => {
  return content.map((block) => {
    const b = block as Record<string, unknown>;
    const blockType = b.type as string;

    if (blockType === 'text') {
      return { type: 'text', text: b.text };
    }
    if (blockType === 'image') {
      const source = b.source as { type: string; media_type: string; data: string };
      return {
        type: 'image',
        source: {
          type: source.type,
          media_type: source.media_type,
          data: source.data,
        },
      };
    }
    if (blockType === 'document') {
      const source = b.source as { type: string; media_type: string; data: string };
      return {
        type: 'document',
        source: {
          type: source.type,
          media_type: source.media_type,
          data: source.data,
        },
      };
    }
    // Pass through other types as-is
    return block;
  });
};

// Transform session data from snake_case (backend) to camelCase (frontend)
// Exported so search.ts (GET /api/search/sessions) reuses the SAME mapping —
// both endpoints return the ChatSessionResponse shape; do not re-derive it.
export const toSessionCamelCase = (data: Record<string, unknown>): ChatSession => {
  return {
    id: data.id as string,
    agentId: data.agent_id as string,
    title: data.title as string,
    createdAt: data.created_at as string,
    lastAccessedAt: data.last_accessed_at as string,
    workDir: data.work_dir as string | undefined,
  };
};

// Transform message data from snake_case (backend) to camelCase (frontend)
const toMessageCamelCase = (data: Record<string, unknown>): ChatMessage => {
  return {
    id: data.id as string,
    sessionId: data.session_id as string,
    role: data.role as 'user' | 'assistant' | 'system',
    // Content can be various block types - cast to unknown first then to ContentBlock[]
    content: toCamelCaseContent(data.content as unknown[]) as unknown as ChatMessage['content'],
    model: (data.model as string) || undefined,
    createdAt: data.created_at as string,
    // Preserve metadata (carries client_id for optimistic message dedup)
    ...(data.metadata ? { metadata: data.metadata as ChatMessage['metadata'] } : {}),
  };
};

/** Convert snake_case fields in a content block to camelCase and enrich generic summaries.
 *
 * tool_result blocks:
 * - tool_use_id → toolUseId
 * - is_error → isError
 *
 * tool_use blocks:
 * - Enriches generic "Using Skill" summary with the actual skill name from input
 *
 * Defensively handles all block types — passes unknown blocks through unchanged.
 */
export const toCamelCaseContentBlock = (block: Record<string, unknown>): Record<string, unknown> => {
  if (block.type === 'tool_result') {
    const converted: Record<string, unknown> = { ...block };
    if ('tool_use_id' in converted) {
      converted.toolUseId = converted.tool_use_id;
      delete converted.tool_use_id;
    }
    if ('is_error' in converted) {
      converted.isError = converted.is_error;
      delete converted.is_error;
    }
    return converted;
  }
  if (block.type === 'tool_use') {
    return enrichToolUseSummary(block);
  }
  return block;
};

/**
 * Enrich tool_use summary when the SDK sends a generic label.
 *
 * The Claude Code SDK generates summaries like "Using Skill" without
 * including which skill is invoked. This extracts the skill name from
 * the tool input and rewrites the summary to e.g. "Using Skill: frontend-design".
 */
const enrichToolUseSummary = (block: Record<string, unknown>): Record<string, unknown> => {
  const name = block.name as string | undefined;
  const summary = block.summary as string | undefined;
  const input = block.input as Record<string, unknown> | undefined;

  // Only enrich when summary is the generic SDK default
  if (name === 'Skill' && summary === 'Using Skill' && input) {
    // Known field names for the skill identifier — if the SDK changes these,
    // enrichment silently falls back to the generic "Using Skill" summary.
    const skillName = (input.skill ?? input.skill_name ?? input.skillName ?? input.name) as string | undefined;
    if (skillName) {
      // Strip "s_" prefix for readability: "s_frontend-design" → "frontend-design"
      const displayName = skillName.replace(/^s_/, '');
      return { ...block, summary: `Using Skill: ${displayName}` };
    }
  }

  return block;
};

/** Convert snake_case fields in an array of content blocks to camelCase. */
export const toCamelCaseContent = (content: unknown[]): unknown[] => {
  return content.map((block) => toCamelCaseContentBlock(block as Record<string, unknown>));
};

/** Parse an SSE data string into a StreamEvent, converting content block fields from snake_case to camelCase.
 * This ensures tool_result blocks have toolUseId (not tool_use_id) and isError (not is_error)
 * so the frontend resultMap lookup works correctly.
 */
export const parseSSEEvent = (data: string): StreamEvent => {
  const event: StreamEvent = JSON.parse(data);
  if (event.content && Array.isArray(event.content)) {
    event.content = toCamelCaseContent(event.content) as StreamEvent['content'];
  }
  return event;
};

/**
 * Shared SSE read loop — reads from a fetch ReadableStream, parses SSE events,
 * dispatches to onMessage, and handles stall detection + buffer flushing.
 *
 * Eliminates the 3x duplication of the buffer/flush/stall-timer logic across
 * streamChat, streamAnswerQuestion, and streamCmdPermissionContinue.
 */
// Exported so contract tests can drive the REAL consume loop against a real
// fetch stream (incl. the premature-close onDisconnect branch). Mirrors the
// already-exported applyTextDelta — pure async fn, no module state added.
export async function consumeSSEStream(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  startStallTimer: (r: ReadableStreamDefaultReader<Uint8Array>) => void,
  clearStallTimer: () => void,
  onMessage: (event: StreamEvent) => void,
  onComplete: () => void,
  onDisconnect?: () => void,
): Promise<void> {
  const decoder = new TextDecoder();
  let buffer = '';
  let receivedDone = false;

  startStallTimer(reader);

  while (true) {
    const { done, value } = await reader.read();

    if (done) {
      // Flush TextDecoder's internal buffer (multi-byte sequences)
      // and process any remaining SSE lines before completing.
      const remaining = decoder.decode() + buffer;
      if (remaining.trim()) {
        for (const line of remaining.split('\n')) {
          if (line.startsWith('data: ')) {
            const data = line.slice(6);
            if (data === '[DONE]') { receivedDone = true; break; }
            try {
              const event = parseSSEEvent(data);
              if (event.type !== 'heartbeat') {
                try { onMessage(event); } catch { /* swallow */ }
              }
            } catch { /* incomplete data */ }
          }
        }
      }
      clearStallTimer();
      if (receivedDone) {
        // Clean completion — backend sent [DONE] sentinel
        onComplete();
      } else if (onDisconnect) {
        // Premature disconnect — HTTP stream closed without [DONE].
        // Backend may still be streaming. Don't clear isStreaming.
        // See: 2026-04-02 SSE disconnect kill chain diagnosis.
        console.warn('[SSE] Premature disconnect detected (no [DONE] sentinel)');
        onDisconnect();
      } else {
        // No disconnect handler — fall back to complete (legacy behavior)
        onComplete();
      }
      break;
    }

    // Data received — reset stall timer (includes heartbeats)
    startStallTimer(reader);

    buffer += decoder.decode(value, { stream: true });

    // Process SSE events
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';

    for (const line of lines) {
      if (line.startsWith('data: ')) {
        const data = line.slice(6);
        if (data === '[DONE]') {
          receivedDone = true;
          clearStallTimer();
          onComplete();
          return;
        }
        try {
          const event = parseSSEEvent(data);
          // NOTE: heartbeats ARE forwarded to onMessage. The stream handler
          // resets the per-tab force-end watchdog on ANY event (its top line)
          // and already excludes 'heartbeat' from stall/data accounting. Do NOT
          // re-add a `continue` here: dropping heartbeats was why the 90s
          // watchdog force-ended a live cold --resume (only heartbeats flow
          // before the first token) — "resume 看起来根本起不来".
          try {
            onMessage(event);
          } catch (handlerError) {
            console.error('[SSE] Error in onMessage handler:', handlerError, 'Event:', event.type);
          }
        } catch {
          // Ignore parse errors for incomplete data
        }
      }
    }
  }
}

/** Create stall detection state + helpers for an SSE stream.
 *
 *  A2 (run_d2f25153): the stall timer is a TRIGGER, not a cancel authority. When
 *  it fires (STALL_TIMEOUT_MS of byte-silence, INCLUDING no heartbeat), it does
 *  NOT unconditionally cancel — it consults the loop-independent backend-liveness
 *  verdict (`isBackendLive`) via {@link decideStallAction}:
 *    - alive (under cap)   → re-arm, keep waiting (a live-but-silent long turn /
 *                            cold-resume prefill is NEVER blind-cancelled).
 *    - alive (past cap)    → surface a 'still working — Stop?' affordance via
 *                            onError-adjacent onAffordance; do NOT auto-cancel.
 *    - dead                → cancel + onError (proven death → authorized).
 *    - unknown (past budget)→ cancel (bounded; covers app-boot with no verdict yet).
 *
 *  `isBackendLive` is optional: when omitted (Hive/browser with no Tauri
 *  watchdog, or a caller that hasn't wired it), the verdict defaults to 'unknown'
 *  so behaviour is bounded — it re-arms up to UNKNOWN_REARM_BUDGET_MS then
 *  cancels, i.e. a longer-but-still-finite version of the legacy single 90s
 *  cancel (never worse: it only ever waits LONGER before the same cancel). */
function createStallDetection(
  onError: (error: Error) => void,
  label: string = '',
  isBackendLive?: () => StallLiveness,
  onAffordance?: () => void,
) {
  const stall = {
    timer: undefined as ReturnType<typeof setTimeout> | undefined,
    cleared: false,
    // Total silence accumulated across consecutive re-arms WITHOUT any real data.
    // Reset to 0 each time real data resets the timer (startStallTimer called
    // fresh); grows by STALL_TIMEOUT_MS per silent re-arm. This is the elapsed
    // input to decideStallAction's cap/budget checks.
    silentMs: 0,
    affordanceShown: false,
  };

  const clearStallTimer = () => {
    stall.cleared = true;
    if (stall.timer !== undefined) {
      clearTimeout(stall.timer);
      stall.timer = undefined;
    }
  };

  const startStallTimer = (readerRef: ReadableStreamDefaultReader<Uint8Array>) => {
    if (stall.cleared) return;
    if (stall.timer !== undefined) clearTimeout(stall.timer);
    // Called fresh by consumeSSEStream on EVERY real data chunk (incl. heartbeat)
    // — that means the stream is alive right now, so reset the silence accumulator
    // and the affordance latch.
    stall.silentMs = 0;
    stall.affordanceShown = false;
    arm(readerRef);
  };

  const arm = (readerRef: ReadableStreamDefaultReader<Uint8Array>) => {
    stall.timer = setTimeout(() => {
      stall.silentMs += STALL_TIMEOUT_MS;
      const liveness = isBackendLive ? isBackendLive() : 'unknown';
      const action = decideStallAction(liveness, stall.silentMs);
      if (action === 'rearm') {
        // Backend is alive (or unknown-under-budget): keep waiting. NEVER cancel
        // a live-but-silent stream — this is the whole fix.
        arm(readerRef);
        return;
      }
      if (action === 'affordance') {
        // Alive but silent past the turn-liveness cap: surface "still working —
        // Stop?" once, then keep re-arming (user decides via Stop; we never
        // auto-kill a backend that is still proving itself alive).
        if (!stall.affordanceShown) {
          stall.affordanceShown = true;
          console.warn(
            `[SSE] Long silent turn${label ? ` (${label})` : ''}: backend alive `
            + `${Math.round(stall.silentMs / 1000)}s with no output — offering Stop.`,
          );
          onAffordance?.();
        }
        arm(readerRef);
        return;
      }
      // action === 'cancel' — proven dead, or unknown past its budget.
      const msg = `Stream stalled${label ? ` (${label})` : ''}: backend not alive after ${Math.round(stall.silentMs / 1000)}s of silence`;
      console.warn(`[SSE] ${msg}`);
      readerRef.cancel().catch(() => {});
      onError(new Error(msg));
    }, STALL_TIMEOUT_MS);
  };

  return { stall, clearStallTimer, startStallTimer };
}

// ---------------------------------------------------------------------------
// Shared SSE fetch helper
// ---------------------------------------------------------------------------

/**
 * Fire a POST to an SSE endpoint, wire up stall detection, and pipe events
 * through ``consumeSSEStream``.  Returns an abort function.
 *
 * All three streaming methods (streamChat, streamAnswerQuestion,
 * streamCmdPermissionContinue) share this exact fetch → error-check →
 * getReader → consumeSSEStream → catch pattern.
 */
function startSSEFetch(opts: {
  path: string;
  body: Record<string, unknown>;
  label: string;
  onMessage: (event: StreamEvent) => void;
  onError: (error: Error) => void;
  onComplete: () => void;
  onDisconnect?: () => void;
  /** Loop-independent backend-liveness verdict (A2). When provided, the stall
   *  timer becomes a trigger that never cancels a live stream — see
   *  {@link createStallDetection}. Omitted → defaults to bounded 'unknown'. */
  isBackendLive?: () => StallLiveness;
  /** Fired once when an alive stream has been silent past the turn-liveness cap
   *  (surface a 'still working — Stop?' hint). */
  onAffordance?: () => void;
}): () => void {
  const controller = new AbortController();
  const apiBase = getApiBaseUrl();
  const { clearStallTimer, startStallTimer } = createStallDetection(
    opts.onError, opts.label, opts.isBackendLive, opts.onAffordance,
  );

  fetch(`${apiBase}${opts.path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(opts.body),
    credentials: 'include',
    signal: controller.signal,
  })
    .then(async (response) => {
      if (!response.ok) {
        let errorMessage = `HTTP error! status: ${response.status}`;
        try {
          const errorData = await response.json();
          errorMessage = errorData.detail || errorData.message || errorMessage;
        } catch { /* JSON parse failed */ }
        throw new Error(errorMessage);
      }
      const reader = response.body?.getReader();
      if (!reader) throw new Error('No response body');
      await consumeSSEStream(reader, startStallTimer, clearStallTimer, opts.onMessage, opts.onComplete, opts.onDisconnect);
    })
    .catch((error) => {
      clearStallTimer();
      if (error.name !== 'AbortError') opts.onError(error);
    });

  return () => { clearStallTimer(); controller.abort(); };
}

export const chatService = {
  // Stream chat messages using SSE
  streamChat(
    request: ChatRequest,
    onMessage: (event: StreamEvent) => void,
    onError: (error: Error) => void,
    onComplete: () => void,
    onDisconnect?: () => void,
    liveness?: StreamLivenessOpts,
  ): () => void {
    // Build request body - support both message and content
    const requestBody: Record<string, unknown> = {
      agent_id: request.agentId,
      session_id: request.sessionId,
      enable_skills: request.enableSkills,
      enable_mcp: request.enableMCP,
      // UI-state proprioception (SENSE): serialize the full snapshot via the
      // SSOT mapper — NEVER hand-pick fields here (that dropped canvas +
      // active_overlay silently — Gate-1 CRITICAL). Mapper returns null for an
      // empty snapshot, so we omit editor_context exactly as the legacy null did.
      ...((() => {
        const p = toEditorContextPayload(request.editorContext);
        return p ? { editor_context: p } : {};
      })()),
      ...(request.terminalContext && {
        terminal_context: {
          buffer_tail: request.terminalContext.bufferTail,
          cwd: request.terminalContext.cwd,
        },
      }),
      // Correlation ID for optimistic message dedup (AC2/AC3)
      ...(request.clientId && { client_id: request.clientId }),
    };

    // If content array is provided, use it; otherwise use message
    if (request.content && request.content.length > 0) {
      requestBody.content = toSnakeCaseContent(request.content as unknown[]);
    } else if (request.message) {
      requestBody.message = request.message;
    }

    // Invalidate ETag cache — new message means history will change.
    if (request.sessionId) {
      this.invalidateMessageCache(request.sessionId);
    }

    return startSSEFetch({
      path: '/api/chat/stream',
      body: requestBody,
      label: 'streamChat',
      onMessage, onError, onComplete, onDisconnect,
      isBackendLive: liveness?.isBackendLive,
      onAffordance: liveness?.onAffordance,
    });
  },

  // List chat sessions
  async listSessions(agentId?: string, limit?: number): Promise<ChatSession[]> {
    const params: Record<string, string | number> = {};
    if (agentId) {
      params.agent_id = agentId;
    }
    if (limit !== undefined) {
      params.limit = limit;
    }
    const response = await api.get<Record<string, unknown>[]>('/chat/sessions', { params });
    return response.data.map(toSessionCamelCase);
  },

  // Get streaming state for all sessions (reconciliation endpoint).
  // Root-1 SSOT Phase 3: widened from {streaming,state} to the full
  // StreamingStateEntry. The backend (chat.py:776-786) emits 4 extra fields
  // (waiting_input/pending_count/pending_question/last_drained_seqs) that the
  // frontend mirror consumes; the prior narrow type silently dropped them.
  // snake_case → camelCase conversion stays in this services layer (convention).
  async getStreamingState(): Promise<Record<string, StreamingStateEntry>> {
    const response = await api.get<{ sessions: Record<string, RawStreamingStateEntry> }>(
      '/chat/sessions/streaming-state',
    );
    const out: Record<string, StreamingStateEntry> = {};
    for (const [sid, raw] of Object.entries(response.data.sessions ?? {})) {
      const rawPq = raw.pending_question;
      // Only map an AskUserQuestion payload (non-empty `questions`). A
      // command-permission prompt also sets pending_question on WAITING_INPUT but
      // carries NO questions — it must map to null here so the mirror's AC5
      // re-surface never mistakes it for a question (it has its own render path).
      const isAskUserQuestion =
        !!rawPq && Array.isArray(rawPq.questions) && rawPq.questions.length > 0;
      out[sid] = {
        streaming: raw.streaming ?? false,
        state: raw.state ?? 'idle',
        waitingInput: raw.waiting_input ?? false,
        pendingCount: raw.pending_count ?? 0,
        pendingQuestion: isAskUserQuestion
          ? { toolUseId: rawPq!.tool_use_id, questions: rawPq!.questions! }
          : null,
        lastDrainedSeqs: raw.last_drained_seqs ?? [],
        // Fail-safe: an older backend omits this → false → behavior never worse
        // than before the honest-signal fix (OT01).
        postDisconnectFlushing: raw.post_disconnect_flushing ?? false,
      };
    }
    return out;
  },

  // Get sub-agent progress for a session (polls while Agent tool running)
  async getSubAgentProgress(sessionId: string): Promise<{
    active: boolean;
    elapsed_s: number;
    label: string | null;
    count: number;
  }> {
    const response = await api.get<{ active: boolean; elapsed_s: number; label: string | null; count: number }>(
      `/chat/sessions/${sessionId}/sub-agent-progress`,
    );
    return response.data;
  },

  // Get a specific session
  async getSession(sessionId: string): Promise<ChatSession> {
    const response = await api.get<Record<string, unknown>>(`/chat/sessions/${sessionId}`);
    return toSessionCamelCase(response.data);
  },

  // ── Per-session ETag cache for message endpoint ──
  // Messages are append-only → ETag "session:count" changes only on new
  // messages.  Avoids refetching identical history on tab switches / restarts.
  _messageEtags: new Map<string, { etag: string; messages: ChatMessage[] }>(),

  // Get messages for a session
  async getSessionMessages(sessionId: string): Promise<ChatMessage[]> {
    const cached = this._messageEtags.get(sessionId);
    const headers: Record<string, string> = {};
    if (cached?.etag) headers['If-None-Match'] = cached.etag;

    const response = await api.get<Record<string, unknown>[]>(
      `/chat/sessions/${sessionId}/messages`,
      { headers, validateStatus: (s: number) => s === 200 || s === 304 },
    );

    // 304 = unchanged. Guard: if cache was cleared between send and
    // response (race), fall through to empty array instead of crash.
    if (response.status === 304) {
      return cached?.messages ?? [];
    }

    const messages = (response.data ?? []).map(toMessageCamelCase);
    const etag = response.headers?.['etag'];
    if (etag) {
      this._messageEtags.set(sessionId, { etag, messages });
    }
    return messages;
  },

  // Get messages for a session with cursor-based pagination
  async getSessionMessagesPaginated(
    sessionId: string,
    limit?: number,
    beforeId?: string,
  ): Promise<ChatMessage[]> {
    const params = new URLSearchParams();
    if (limit !== undefined) params.set('limit', String(limit));
    if (beforeId !== undefined) params.set('before_id', beforeId);
    const url = `/chat/sessions/${sessionId}/messages?${params.toString()}`;

    // ETag only for non-cursor queries (initial load)
    const cached = !beforeId ? this._messageEtags.get(sessionId) : undefined;
    const headers: Record<string, string> = {};
    if (cached?.etag) headers['If-None-Match'] = cached.etag;

    const response = await api.get<Record<string, unknown>[]>(url, {
      headers,
      validateStatus: (s: number) => s === 200 || s === 304,
    });

    if (response.status === 304) {
      return cached?.messages ?? [];
    }

    const messages = (response.data ?? []).map(toMessageCamelCase);
    // Only cache non-cursor responses (full initial loads)
    if (!beforeId) {
      const etag = response.headers?.['etag'];
      if (etag) {
        this._messageEtags.set(sessionId, { etag, messages });
      }
    }
    return messages;
  },

  /**
   * Turn-end RECONCILE tail fetch — the newest `limit` rows, deliberately
   * BYPASSING the ETag cache (never sends If-None-Match, never writes the
   * shared cache slot).
   *
   * WHY reconcile must NOT touch the cache: the single per-session cache slot
   * (`_messageEtags`) is keyed by the backend ETag `"session:count"`, which is
   * LIMIT-AGNOSTIC (chat.py:1158-1163 computes it from count only and returns
   * 304 on a match regardless of `limit`). If a `limit`-capped reconcile wrote
   * its small slice into that slot, a subsequent FULL initial-load
   * (`getSessionMessagesPaginated(sid, 200)`) would send the same etag, get a
   * 304 (count unchanged), and receive the cached *50-row* slice instead of 200
   * — silently truncating visible history on tab-switch/restore AND hiding the
   * "Load earlier messages" button (len === 200 → false). The bug surfaces
   * OUTSIDE the reconcile path, so a persist-lag regression test can't catch it.
   *
   * Cost of bypassing: ~zero. Reconcile always runs after a send + a preceding
   * `invalidateMessageCache`, so the count has changed and a 304 would almost
   * never hit anyway. Leaving the cache exclusively to initial-load removes the
   * poisoning vector entirely.
   */
  async getSessionMessagesReconcileTail(
    sessionId: string,
    limit: number,
  ): Promise<ChatMessage[]> {
    // No If-None-Match header → backend never returns 304 → always 200 with the
    // capped tail. No cache write → the shared slot stays owned by initial-load.
    const response = await api.get<Record<string, unknown>[]>(
      `/chat/sessions/${sessionId}/messages?limit=${limit}`,
      { validateStatus: (s: number) => s === 200 },
    );
    return (response.data ?? []).map(toMessageCamelCase);
  },

  // Invalidate ETag cache for a session (call after sending a message)
  invalidateMessageCache(sessionId: string): void {
    this._messageEtags.delete(sessionId);
  },

  // Delete chat session
  async deleteSession(sessionId: string): Promise<void> {
    this._messageEtags.delete(sessionId);
    await api.delete(`/chat/sessions/${sessionId}`);
  },

  // Stop a running chat session
  async stopSession(sessionId: string): Promise<{ status: string; message: string }> {
    const response = await api.post<{ status: string; message: string }>(`/chat/stop/${sessionId}`);
    return response.data;
  },

  // Release a session's backend slot on tab close (R6b). Best-effort, idempotent:
  // frees the concurrency slot without deleting messages, so a closed idle tab
  // doesn't hold a slot until the 12h TTL. `force` confirms closing a streaming
  // tab (routes through the generation-safe interrupt path server-side).
  async releaseSession(
    sessionId: string,
    force = false,
  ): Promise<{ status: string; alive_count?: number }> {
    const response = await api.post<{ status: string; alive_count?: number }>(
      `/chat/release/${sessionId}`,
      force ? { force: true } : undefined,
    );
    return response.data;
  },

  // Trigger manual compaction of a session's context window
  async compactSession(sessionId: string, instructions?: string): Promise<{ status: string; message: string }> {
    const body = instructions ? { instructions } : undefined;
    const response = await api.post<{ status: string; message: string }>(`/chat/compact/${sessionId}`, body);
    return response.data;
  },

  // User-triggered context refresh — kills subprocess for resume with summary
  async refreshSession(sessionId: string): Promise<{ status: string; message: string }> {
    const response = await api.post<{ status: string; message: string }>(`/chat/refresh/${sessionId}`);
    return response.data;
  },

  // Submit AskUserQuestion answer and continue streaming
  streamAnswerQuestion(
    request: {
      agentId: string;
      sessionId: string;
      toolUseId: string;
      answers: Record<string, string>;
      enableSkills?: boolean;
      enableMCP?: boolean;
    },
    onMessage: (event: StreamEvent) => void,
    onError: (error: Error) => void,
    onComplete: () => void,
    onDisconnect?: () => void,
    liveness?: StreamLivenessOpts,
  ): () => void {
    // Invalidate ETag — answer continuation produces new assistant messages.
    if (request.sessionId) {
      this.invalidateMessageCache(request.sessionId);
    }

    return startSSEFetch({
      path: '/api/chat/answer-question',
      body: {
        agent_id: request.agentId,
        session_id: request.sessionId,
        tool_use_id: request.toolUseId,
        answers: request.answers,
        enable_skills: request.enableSkills,
        enable_mcp: request.enableMCP,
      },
      label: 'answer-question',
      onMessage, onError, onComplete, onDisconnect,
      isBackendLive: liveness?.isBackendLive,
      onAffordance: liveness?.onAffordance,
    });
  },

  // Submit command permission decision and continue streaming.
  // (The old non-streaming submitCmdPermissionDecision + /cmd-permission-response
  // endpoint were removed in run_74992978 — both approve AND deny now stream through
  // this method so the agent continues after any decision.)
  streamCmdPermissionContinue(
    request: PermissionResponse & {
      enableSkills?: boolean;
      enableMCP?: boolean;
    },
    onMessage: (event: StreamEvent) => void,
    onError: (error: Error) => void,
    onComplete: () => void,
    onDisconnect?: () => void,
    liveness?: StreamLivenessOpts,
  ): () => void {
    // Invalidate ETag — permission continuation produces new assistant messages.
    if (request.sessionId) {
      this.invalidateMessageCache(request.sessionId);
    }

    return startSSEFetch({
      path: '/api/chat/cmd-permission-continue',
      body: {
        session_id: request.sessionId,
        request_id: request.requestId,
        decision: request.decision,
        feedback: request.feedback,
        enable_skills: request.enableSkills,
        enable_mcp: request.enableMCP,
      },
      label: 'cmd-permission-continue',
      onMessage, onError, onComplete, onDisconnect,
      isBackendLive: liveness?.isBackendLive,
      onAffordance: liveness?.onAffordance,
    });
  },
};
