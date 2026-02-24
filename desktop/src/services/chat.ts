import type { ChatRequest, StreamEvent, ChatSession, ChatMessage, PermissionResponse } from '../types';
import api from './api';
import { getBackendPort } from './tauri';

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
    content: data.content as unknown as ChatMessage['content'],
    model: (data.model as string) || undefined,
    createdAt: data.created_at as string,
  };
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

    // Add optional add_dirs if provided
    if (request.addDirs && request.addDirs.length > 0) {
      requestBody.add_dirs = request.addDirs;
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

        while (true) {
          const { done, value } = await reader.read();

          if (done) {
            onComplete();
            break;
          }

          buffer += decoder.decode(value, { stream: true });

          // Process SSE events
          const lines = buffer.split('\n');
          buffer = lines.pop() || '';

          for (const line of lines) {
            if (line.startsWith('data: ')) {
              const data = line.slice(6);
              if (data === '[DONE]') {
                onComplete();
                return;
              }
              try {
                const event: StreamEvent = JSON.parse(data);
                // Ignore heartbeat messages - they're just for keeping the connection alive
                if (event.type === 'heartbeat') {
                  continue;
                }
                onMessage(event);
              } catch {
                // Ignore parse errors for incomplete data
              }
            }
          }
        }
      })
      .catch((error) => {
        if (error.name !== 'AbortError') {
          onError(error);
        }
      });

    // Return cleanup function
    return () => {
      controller.abort();
    };
  },

  // List chat sessions
  async listSessions(agentId?: string): Promise<ChatSession[]> {
    const params = agentId ? { agent_id: agentId } : {};
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

  // Delete chat session
  async deleteSession(sessionId: string): Promise<void> {
    await api.delete(`/chat/sessions/${sessionId}`);
  },

  // Stop a running chat session
  async stopSession(sessionId: string): Promise<{ status: string; message: string }> {
    const response = await api.post<{ status: string; message: string }>(`/chat/stop/${sessionId}`);
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

        while (true) {
          const { done, value } = await reader.read();

          if (done) {
            onComplete();
            break;
          }

          buffer += decoder.decode(value, { stream: true });

          const lines = buffer.split('\n');
          buffer = lines.pop() || '';

          for (const line of lines) {
            if (line.startsWith('data: ')) {
              const data = line.slice(6);
              if (data === '[DONE]') {
                onComplete();
                return;
              }
              try {
                const event: StreamEvent = JSON.parse(data);
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
        if (error.name !== 'AbortError') {
          onError(error);
        }
      });

    return () => {
      controller.abort();
    };
  },

  // Submit permission decision for dangerous command approval (non-streaming)
  async submitPermissionDecision(
    request: PermissionResponse
  ): Promise<{ status: string; requestId: string }> {
    const response = await api.post<{ status: string; request_id: string }>(
      '/chat/permission-response',
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

  // Submit permission decision and continue streaming
  streamPermissionContinue(
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

    fetch(`http://localhost:${port}/api/chat/permission-continue`, {
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

        while (true) {
          const { done, value } = await reader.read();

          if (done) {
            onComplete();
            break;
          }

          buffer += decoder.decode(value, { stream: true });

          const lines = buffer.split('\n');
          buffer = lines.pop() || '';

          for (const line of lines) {
            if (line.startsWith('data: ')) {
              const data = line.slice(6);
              if (data === '[DONE]') {
                onComplete();
                return;
              }
              try {
                const event: StreamEvent = JSON.parse(data);
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
        if (error.name !== 'AbortError') {
          onError(error);
        }
      });

    return () => {
      controller.abort();
    };
  },
};
