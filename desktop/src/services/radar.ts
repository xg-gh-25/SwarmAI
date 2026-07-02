/**
 * Radar artifact API service layer.
 *
 * Provides read access to recently modified workspace artifacts for the Radar
 * sidebar's Artifacts section. (Todo operations live in services/todos.ts —
 * the single canonical Todo service.)
 *
 * Exports:
 * - radarService              — Object with the artifact fetch method
 * - artifactToCamelCase       — Converts backend snake_case artifact to RadarArtifact
 */

import api from './api';
import type { RadarArtifact } from '../pages/chat/components/RightSidebar/types';

// ---------------------------------------------------------------------------
// Artifact conversion helper (Spec — Right Sidebar Redesign)
// ---------------------------------------------------------------------------

/** Convert backend snake_case artifact response to frontend camelCase RadarArtifact. */
export function artifactToCamelCase(a: Record<string, unknown>): RadarArtifact {
  return {
    path: a.path as string,
    title: a.title as string,
    type: a.type as RadarArtifact['type'],
    modifiedAt: a.modified_at as string,
  };
}

export const radarService = {
  /** Fetch recently modified artifacts from the workspace git tree. */
  async fetchRecentArtifacts(workspaceId: string, limit?: number): Promise<RadarArtifact[]> {
    const params = new URLSearchParams();
    params.append('workspace_id', workspaceId);
    params.append('limit', String(limit ?? 20));
    const response = await api.get(`/artifacts/recent?${params.toString()}`);
    return response.data.map(artifactToCamelCase);
  },
};
