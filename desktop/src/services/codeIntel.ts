/**
 * Code Intelligence service — fetch project-scoped codebase health stats.
 */

import api from './api';

export interface CodeIntelSummary {
  symbolCount: number;
  edgeCount: number;
  fileCount: number;
  unusedExportsCount: number;
  unusedExportsPct: number;
  entryPoints: number;
  languages: Record<string, number>;
  modulesTop5: Array<{ name: string; function_count: number; class_count: number; file_count: number }>;
  lastIndexedAt: string | null;
  freshnessStatus: 'fresh' | 'stale' | 'unknown';
}

interface RawCodeIntelSummary {
  symbol_count: number;
  edge_count: number;
  file_count: number;
  unused_exports_count: number;
  unused_exports_pct: number;
  entry_points: number;
  languages: Record<string, number>;
  modules_top5: Array<{ name: string; function_count: number; class_count: number; file_count: number }>;
  last_indexed_at: string | null;
  freshness_status: 'fresh' | 'stale' | 'unknown';
}

function toCamelCase(raw: RawCodeIntelSummary): CodeIntelSummary {
  return {
    symbolCount: raw.symbol_count,
    edgeCount: raw.edge_count,
    fileCount: raw.file_count,
    unusedExportsCount: raw.unused_exports_count,
    unusedExportsPct: raw.unused_exports_pct,
    entryPoints: raw.entry_points,
    languages: raw.languages,
    modulesTop5: raw.modules_top5,
    lastIndexedAt: raw.last_indexed_at,
    freshnessStatus: raw.freshness_status,
  };
}

/**
 * Fetch code intelligence summary for a project.
 * Returns null if the project has no code_intel.db (404).
 */
export async function getCodeIntelSummary(project: string): Promise<CodeIntelSummary | null> {
  try {
    const resp = await api.get<RawCodeIntelSummary>(`/api/code-intel/${encodeURIComponent(project)}/summary`);
    return toCamelCase(resp.data);
  } catch (err: any) {
    if (err?.response?.status === 404) return null;
    throw err;
  }
}

/**
 * Trigger a re-index for a project. Returns immediately (202 Accepted).
 */
export async function triggerReindex(project: string): Promise<void> {
  await api.post(`/api/code-intel/${encodeURIComponent(project)}/reindex`);
}
