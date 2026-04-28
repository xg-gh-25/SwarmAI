import type { ChatRequest, StreamEvent, ChatSession, ChatMessage, PermissionResponse } from '../types';
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
 * @see Requirements 2.6 — Stall detection triggers reconnection
 */
export const STALL_TIMEOUT_MS = 45_000;

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
const toSessionCamelCase = (data: Record<string, unknown>): ChatSession => {
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
    role: data.role as 'user' | 'assistant',
    // Content can be various block types - cast to unknown first then to ContentBlock[]
    content: toCamelCaseContent(data.content as unknown[]) as unknown as ChatMessage['content'],
    model: (data.model as string) || undefined,
    createdAt: data.created_at as string,
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
async function consumeSSEStream(
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
          if (event.type === 'heartbeat') {
            continue;
          }
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

/** Create stall detection state + helpers for an SSE stream. */
function createStallDetection(
  onError: (error: Error) => void,
  label: string = '',
) {
  const stall = { timer: undefined as ReturnType<typeof setTimeout> | undefined, cleared: false };

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
    stall.timer = setTimeout(() => {
      const msg = `Stream stalled${label ? ` (${label})` : ''}: no data received for ${STALL_TIMEOUT_MS / 1000} seconds`;
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
}): () => void {
  const controller = new AbortController();
  const apiBase = getApiBaseUrl();
  const { clearStallTimer, startStallTimer } = createStallDetection(opts.onError, opts.label);

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
  ): () => void {
    // Build request body - support both message and content
    const requestBody: Record<string, unknown> = {
      agent_id: request.agentId,
      session_id: request.sessionId,
      enable_skills: request.enableSkills,
      enable_mcp: request.enableMCP,
      ...(request.editorContext && {
        editor_context: {
          file_path: request.editorContext.filePath,
          file_name: request.editorContext.fileName,
        },
      }),
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

  // Trigger manual compaction of a session's context window
  async compactSession(sessionId: string, instructions?: string): Promise<{ status: string; message: string }> {
    const body = instructions ? { instructions } : undefined;
    const response = await api.post<{ status: string; message: string }>(`/chat/compact/${sessionId}`, body);
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
    });
  },

  // Submit command permission decision for dangerous command approval (non-streaming)
  async submitCmdPermissionDecision(
    request: PermissionResponse
  ): Promise<{ status: string; requestId: string }> {
    const response = await api.post<{ status: string; request_id: string }>(
      '/chat/cmd-permission-response',
      {
        session_id: request.sessionId,
        request_id: request.requestId,
        decision: request.decision,
        feedback: request.feedback,
      }
    );
    return {
      status: response.data.status,
      requestId: response.data.request_id,
    };
  },

  // Submit command permission decision and continue streaming
  streamCmdPermissionContinue(
    request: PermissionResponse & {
      enableSkills?: boolean;
      enableMCP?: boolean;
    },
    onMessage: (event: StreamEvent) => void,
    onError: (error: Error) => void,
    onComplete: () => void,
    onDisconnect?: () => void,
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
    });
  },
};
