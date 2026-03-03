/**
 * Filesystem-based skills service for the SwarmAI desktop app.
 *
 * Communicates with the backend Skills API which operates directly on the
 * three-tier filesystem (built-in, user, plugin). Skills are identified by
 * folder name (kebab-case) instead of database UUIDs.
 *
 * Key exports:
 * - ``skillsService``               — API methods for skill CRUD and rescan
 * - ``SkillGenerateWithAgentRequest`` — Request type for AI skill generation
 * - ``toCamelCase``                  — Snake-to-camel field mapper (exported for testing)
 */
import api from './api';
import { getBackendPort } from './tauri';
import type { Skill, SkillCreateRequest, StreamEvent } from '../types';

// Request type for skill generation with agent
export interface SkillGenerateWithAgentRequest {
  skillName: string;
  skillDescription: string;
  sessionId?: string;
  message?: string;
  model?: string;
}

// Convert snake_case backend response to camelCase frontend Skill
export const toCamelCase = (data: Record<string, unknown>): Skill => {
  return {
    folderName: (data.folder_name as string) ?? '',
    name: (data.name as string) ?? '',
    description: (data.description as string) || '',
    version: (data.version as string) || '1.0.0',
    sourceTier: (data.source_tier as 'built-in' | 'user' | 'plugin') || 'user',
    readOnly: (data.read_only as boolean) ?? false,
    content: data.content as string | undefined,
  };
};

export const skillsService = {
  // List all skills (cached, without content)
  async list(): Promise<Skill[]> {
    const response = await api.get<Record<string, unknown>[]>('/skills');
    return response.data.map(toCamelCase);
  },

  // Get skill by folder name (includes content)
  async get(folderName: string): Promise<Skill> {
    const response = await api.get<Record<string, unknown>>(`/skills/${folderName}`);
    return toCamelCase(response.data);
  },

  // Create a new user skill
  async create(data: SkillCreateRequest): Promise<Skill> {
    const response = await api.post<Record<string, unknown>>('/skills', {
      folder_name: data.folderName,
      name: data.name,
      description: data.description,
      content: data.content,
    });
    return toCamelCase(response.data);
  },

  // Update an existing user skill
  async update(folderName: string, data: { name?: string; description?: string; content?: string }): Promise<Skill> {
    const response = await api.put<Record<string, unknown>>(`/skills/${folderName}`, data);
    return toCamelCase(response.data);
  },

  // Rescan filesystem and return fresh skill list
  async rescan(): Promise<Skill[]> {
    const response = await api.post<Record<string, unknown>[]>('/skills/rescan');
    return response.data.map(toCamelCase);
  },

  // Delete skill by folder name
  async delete(folderName: string): Promise<void> {
    await api.delete(`/skills/${folderName}`);
  },

  // Stream skill generation with agent
  streamGenerateWithAgent(
    request: SkillGenerateWithAgentRequest,
    onMessage: (event: StreamEvent) => void,
    onError: (error: Error) => void,
    onComplete: () => void
  ): () => void {
    const controller = new AbortController();
    const port = getBackendPort();

    fetch(`http://localhost:${port}/api/skills/generate-with-agent`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        skill_name: request.skillName,
        skill_description: request.skillDescription,
        session_id: request.sessionId,
        message: request.message,
        model: request.model,
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

};
