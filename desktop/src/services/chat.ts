import type { ChatRequest, StreamEvent, ChatSession, ChatMessage, PermissionResponse } from '../types';
import api from './api';
import { getBackendPort } from './tauri';

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
    const skillName = (input.skill_name ?? input.skillName ?? input.name) as string | undefined;
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

export const chatService = {
  // Stream chat messages using SSE
  streamChat(
    request: ChatRequest,
    onMessage: (event: StreamEvent) => void,
    onError: (error: Error) => void,
    onComplete: () => void
  ): () => void {
    const controller = new AbortController();
    const port = getBackendPort();

    // --- Stall detection state (R2.6) ---
    // Shared between the fetch promise chain and the cleanup function.
    // The timer resets on every reader.read() that returns data (including
    // heartbeats). If 45s elapses with no data, the reader is cancelled
    // and onError fires, feeding into the reconnection logic.
    const stall = { timer: undefined as ReturnType<typeof setTimeout> | undefined, cleared: false };

    const clearStallTimer = () => {
      stall.cleared = true;
      if (stall.timer !== undefined) {
        clearTimeout(stall.timer);
        stall.timer = undefined;
      }
    };

    // Build request body - support both message and content
    const requestBody: Record<string, unknown> = {
      agent_id: request.agentId,
      session_id: request.sessionId,
      enable_skills: request.enableSkills,
      enable_mcp: request.enableMCP,
    };

    // If content array is provided, use it; otherwise use message
    if (request.content && request.content.length > 0) {
      requestBody.content = toSnakeCaseContent(request.content as unknown[]);
    } else if (request.message) {
      requestBody.message = request.message;
    }

    fetch(`http://localhost:${port}/api/chat/stream`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(requestBody),
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) {
          // Try to parse error response from backend
          try {
            const errorData = await response.json();
            const errorMessage = errorData.detail || errorData.message || `HTTP error! status: ${response.status}`;
            throw new Error(errorMessage);
          } catch {
            throw new Error(`HTTP error! status: ${response.status}`);
          }
        }

        const reader = response.body?.getReader();
        if (!reader) {
          throw new Error('No response body');
        }

        const decoder = new TextDecoder();
        let buffer = '';

        // --- Stall detection (R2.6) ---
        // Start the stall timer and define a reset helper. The timer
        // fires onError if no data arrives within STALL_TIMEOUT_MS.
        const startStallTimer = (readerRef: ReadableStreamDefaultReader<Uint8Array>) => {
          if (stall.cleared) return;
          if (stall.timer !== undefined) clearTimeout(stall.timer);
          stall.timer = setTimeout(() => {
            console.warn('[SSE] Stream stalled: no data received for 45 seconds');
            readerRef.cancel().catch(() => { /* ignore cancel errors */ });
            onError(new Error('Stream stalled: no data received for 45 seconds'));
          }, STALL_TIMEOUT_MS);
        };
        startStallTimer(reader);

        while (true) {
          const { done, value } = await reader.read();

          if (done) {
            clearStallTimer();
            onComplete();
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
                clearStallTimer();
                onComplete();
                return;
              }
              try {
                const event = parseSSEEvent(data);
                // Ignore heartbeat messages - they're just for keeping the connection alive
                if (event.type === 'heartbeat') {
                  continue;
                }
                try {
                  onMessage(event);
                } catch (handlerError) {
                  console.error('[SSE] Error in onMessage handler:', handlerError, 'Event:', event.type);
                  // Don't break the loop — continue processing remaining events
                }
              } catch {
                // Ignore parse errors for incomplete data
              }
            }
          }
        }
      })
      .catch((error) => {
        // Clear stall timer on any error/abort exit path
        clearStallTimer();
        if (error.name !== 'AbortError') {
          onError(error);
        }
      });

    // Return cleanup function — clears stall timer and aborts fetch
    return () => {
      clearStallTimer();
      controller.abort();
    };
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

  // Get messages for a session
  async getSessionMessages(sessionId: string): Promise<ChatMessage[]> {
    const response = await api.get<Record<string, unknown>[]>(`/chat/sessions/${sessionId}/messages`);
    return response.data.map(toMessageCamelCase);
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
    const response = await api.get<Record<string, unknown>[]>(url);
    return response.data.map(toMessageCamelCase);
  },

  // Delete chat session
  async deleteSession(sessionId: string): Promise<void> {
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
    onComplete: () => void
  ): () => void {
    const controller = new AbortController();
    const port = getBackendPort();

    // Stall detection state (R2.6) — same pattern as streamChat
    const stall = { timer: undefined as ReturnType<typeof setTimeout> | undefined, cleared: false };
    const clearStallTimer = () => {
      stall.cleared = true;
      if (stall.timer !== undefined) { clearTimeout(stall.timer); stall.timer = undefined; }
    };

    fetch(`http://localhost:${port}/api/chat/answer-question`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        agent_id: request.agentId,
        session_id: request.sessionId,
        tool_use_id: request.toolUseId,
        answers: request.answers,
        enable_skills: request.enableSkills,
        enable_mcp: request.enableMCP,
      }),
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) {
          try {
            const errorData = await response.json();
            const errorMessage = errorData.detail || errorData.message || `HTTP error! status: ${response.status}`;
            throw new Error(errorMessage);
          } catch {
            throw new Error(`HTTP error! status: ${response.status}`);
          }
        }

        const reader = response.body?.getReader();
        if (!reader) {
          throw new Error('No response body');
        }

        const decoder = new TextDecoder();
        let buffer = '';

        // Start stall timer (R2.6)
        const startStallTimerAQ = (readerRef: ReadableStreamDefaultReader<Uint8Array>) => {
          if (stall.cleared) return;
          if (stall.timer !== undefined) clearTimeout(stall.timer);
          stall.timer = setTimeout(() => {
            console.warn('[SSE] Stream stalled (answer-question): no data received for 45 seconds');
            readerRef.cancel().catch(() => {});
            onError(new Error('Stream stalled: no data received for 45 seconds'));
          }, STALL_TIMEOUT_MS);
        };
        startStallTimerAQ(reader);

        while (true) {
          const { done, value } = await reader.read();

          if (done) {
            clearStallTimer();
            onComplete();
            break;
          }

          // Data received — reset stall timer
          startStallTimerAQ(reader);

          buffer += decoder.decode(value, { stream: true });

          const lines = buffer.split('\n');
          buffer = lines.pop() || '';

          for (const line of lines) {
            if (line.startsWith('data: ')) {
              const data = line.slice(6);
              if (data === '[DONE]') {
                clearStallTimer();
                onComplete();
                return;
              }
              try {
                const event = parseSSEEvent(data);
                // Ignore heartbeat messages - they're just for keeping the connection alive
                if (event.type === 'heartbeat') {
                  continue;
                }
                onMessage(event);
              } catch {
                // Ignore parse errors
              }
            }
          }
        }
      })
      .catch((error) => {
        clearStallTimer();
        if (error.name !== 'AbortError') {
          onError(error);
        }
      });

    return () => {
      clearStallTimer();
      controller.abort();
    };
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
    onComplete: () => void
  ): () => void {
    const controller = new AbortController();
    const port = getBackendPort();

    // Stall detection state (R2.6) — same pattern as streamChat
    const stall = { timer: undefined as ReturnType<typeof setTimeout> | undefined, cleared: false };
    const clearStallTimer = () => {
      stall.cleared = true;
      if (stall.timer !== undefined) { clearTimeout(stall.timer); stall.timer = undefined; }
    };

    fetch(`http://localhost:${port}/api/chat/cmd-permission-continue`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        session_id: request.sessionId,
        request_id: request.requestId,
        decision: request.decision,
        feedback: request.feedback,
        enable_skills: request.enableSkills,
        enable_mcp: request.enableMCP,
      }),
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) {
          try {
            const errorData = await response.json();
            const errorMessage = errorData.detail || errorData.message || `HTTP error! status: ${response.status}`;
            throw new Error(errorMessage);
          } catch {
            throw new Error(`HTTP error! status: ${response.status}`);
          }
        }

        const reader = response.body?.getReader();
        if (!reader) {
          throw new Error('No response body');
        }

        const decoder = new TextDecoder();
        let buffer = '';

        // Start stall timer (R2.6)
        const startStallTimerPC = (readerRef: ReadableStreamDefaultReader<Uint8Array>) => {
          if (stall.cleared) return;
          if (stall.timer !== undefined) clearTimeout(stall.timer);
          stall.timer = setTimeout(() => {
            console.warn('[SSE] Stream stalled (cmd-permission-continue): no data received for 45 seconds');
            readerRef.cancel().catch(() => {});
            onError(new Error('Stream stalled: no data received for 45 seconds'));
          }, STALL_TIMEOUT_MS);
        };
        startStallTimerPC(reader);

        while (true) {
          const { done, value } = await reader.read();

          if (done) {
            clearStallTimer();
            onComplete();
            break;
          }

          // Data received — reset stall timer
          startStallTimerPC(reader);

          buffer += decoder.decode(value, { stream: true });

          const lines = buffer.split('\n');
          buffer = lines.pop() || '';

          for (const line of lines) {
            if (line.startsWith('data: ')) {
              const data = line.slice(6);
              if (data === '[DONE]') {
                clearStallTimer();
                onComplete();
                return;
              }
              try {
                const event = parseSSEEvent(data);
                // Ignore heartbeat messages - they're just for keeping the connection alive
                if (event.type === 'heartbeat') {
                  continue;
                }
                onMessage(event);
              } catch {
                // Ignore parse errors
              }
            }
          }
        }
      })
      .catch((error) => {
        clearStallTimer();
        if (error.name !== 'AbortError') {
          onError(error);
        }
      });

    return () => {
      clearStallTimer();
      controller.abort();
    };
  },
};
