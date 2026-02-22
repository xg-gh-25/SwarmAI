import api from './api';
import { getBackendPort } from './tauri';
import type { Skill, SkillCreateRequest, SyncResult, StreamEvent, SkillVersionList } from '../types';

// Request type for skill generation with agent
export interface SkillGenerateWithAgentRequest {
  skillName: string;
  skillDescription: string;
  sessionId?: string;
  message?: string;
  model?: string;
}

// Convert snake_case response to camelCase
const toCamelCase = (data: Record<string, unknown>): Skill => {
  return {
    id: data.id as string,
    name: data.name as string,
    description: (data.description as string) || '',
    folderName: data.folder_name as string | undefined,
    localPath: data.local_path as string | undefined,
    // Source tracking
    sourceType: (data.source_type as 'user' | 'plugin' | 'marketplace' | 'local') || 'user',
    sourcePluginId: data.source_plugin_id as string | undefined,
    sourceMarketplaceId: data.source_marketplace_id as string | undefined,
    sourcePluginName: data.source_plugin_name as string | undefined,
    sourceMarketplaceName: data.source_marketplace_name as string | undefined,
    // Git tracking
    gitUrl: data.git_url as string | undefined,
    gitBranch: data.git_branch as string | undefined,
    gitCommit: data.git_commit as string | undefined,
    // Metadata
    createdBy: data.created_by as string | undefined,
    createdAt: data.created_at as string,
    updatedAt: data.updated_at as string,
    version: (data.version as string) || '1.0.0',
    isSystem: (data.is_system as boolean) || false,
    // Version control fields
    currentVersion: (data.current_version as number) ?? 0,
    hasDraft: (data.has_draft as boolean) ?? false,
  };
};

// Convert sync result snake_case to camelCase
const toSyncResultCamelCase = (data: Record<string, unknown>): SyncResult => {
  return {
    added: (data.added as string[]) || [],
    updated: (data.updated as string[]) || [],
    removed: (data.removed as string[]) || [],
    errors: (data.errors as { skill: string; error: string }[]) || [],
    totalLocal: (data.total_local as number) || 0,
    totalPlugins: (data.total_plugins as number) || 0,
    totalDb: (data.total_db as number) || 0,
  };
};

export const skillsService = {
  // List all skills
  async list(): Promise<Skill[]> {
    const response = await api.get<Record<string, unknown>[]>('/skills');
    return response.data.map(toCamelCase);
  },

  // Get skill by ID
  async get(id: string): Promise<Skill> {
    const response = await api.get<Record<string, unknown>>(`/skills/${id}`);
    return toCamelCase(response.data);
  },

  // Upload skill ZIP
  async upload(file: File, name: string): Promise<Skill> {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('name', name);

    const response = await api.post<Record<string, unknown>>('/skills/upload', formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
    });
    return toCamelCase(response.data);
  },

  // Refresh/sync skills between local, S3 and database
  async refresh(): Promise<SyncResult> {
    const response = await api.post<Record<string, unknown>>('/skills/refresh');
    return toSyncResultCamelCase(response.data);
  },

  // Generate skill with AI
  async generate(data: SkillCreateRequest): Promise<Skill> {
    const response = await api.post<Record<string, unknown>>('/skills/generate', data);
    return toCamelCase(response.data);
  },

  // Delete skill
  async delete(id: string): Promise<void> {
    await api.delete(`/skills/${id}`);
  },

  // List system skills
  async listSystem(): Promise<Skill[]> {
    const response = await api.get<Record<string, unknown>[]>('/skills/system');
    return response.data.map(toCamelCase);
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

  // Finalize skill creation (sync to S3 and save to DB)
  async finalize(skillName: string, displayName?: string): Promise<Skill> {
    const response = await api.post<Record<string, unknown>>('/skills/finalize', {
      skill_name: skillName,
      display_name: displayName,
    });
    return toCamelCase(response.data);
  },

  // Publish draft as new version
  async publish(skillId: string, changeSummary?: string): Promise<Skill> {
    const response = await api.post<Record<string, unknown>>(`/skills/${skillId}/publish`, {
      change_summary: changeSummary,
    });
    return toCamelCase(response.data);
  },

  // Discard unpublished draft
  async discardDraft(skillId: string): Promise<void> {
    await api.delete(`/skills/${skillId}/draft`);
  },

  // Rollback to a specific version
  async rollback(skillId: string, version: number): Promise<Skill> {
    const response = await api.post<Record<string, unknown>>(`/skills/${skillId}/rollback`, {
      version,
    });
    return toCamelCase(response.data);
  },

  // List all versions of a skill
  async listVersions(skillId: string): Promise<SkillVersionList> {
    const response = await api.get<Record<string, unknown>>(`/skills/${skillId}/versions`);
    const data = response.data;
    return {
      skillId: data.skill_id as string,
      skillName: data.skill_name as string,
      currentVersion: data.current_version as number,
      hasDraft: data.has_draft as boolean,
      versions: ((data.versions as Record<string, unknown>[]) || []).map((v) => ({
        id: v.id as string,
        skillId: v.skill_id as string,
        version: v.version as number,
        gitCommit: v.git_commit as string | undefined,
        localPath: v.local_path as string | undefined,
        createdAt: v.created_at as string,
        changeSummary: v.change_summary as string | undefined,
      })),
    };
  },
};
