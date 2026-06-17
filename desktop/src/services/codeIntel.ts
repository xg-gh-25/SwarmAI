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
    const resp = await api.get<RawCodeIntelSummary>(`/code-intel/${encodeURIComponent(project)}/summary`);
    return toCamelCase(resp.data);
  } catch (err: unknown) {
    if (err && typeof err === 'object' && 'response' in err && (err as { response?: { status?: number } }).response?.status === 404) return null;
    throw err;
  }
}

/**
 * Trigger a re-index for a project. Returns immediately (202 Accepted).
 */
export async function triggerReindex(project: string): Promise<void> {
  await api.post(`/code-intel/${encodeURIComponent(project)}/reindex`);
}

// ── Graph Visualization Data ─────────────────────────────────────────────────

export interface GraphNode {
  id: string;
  name: string;
  type: string;     // "function" | "class" | "method" | "variable"
  module: string;   // 2-level dir prefix
  file_path: string;
}

export interface GraphEdge {
  source: string;
  target: string;
  type: string;     // "calls" | "imports" | "instantiates" etc.
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

/**
 * Fetch graph visualization data (top-N most-connected nodes + their edges).
 * Returns null if the project has no code_intel.db.
 */
export async function getCodeIntelGraph(project: string, limit: number = 300): Promise<GraphData | null> {
  try {
    const resp = await api.get<GraphData>(
      `/code-intel/${encodeURIComponent(project)}/graph`,
      { params: { limit } }
    );
    return resp.data;
  } catch (err: unknown) {
    if (err && typeof err === 'object' && 'response' in err && (err as { response?: { status?: number } }).response?.status === 404) return null;
    throw err;
  }
}
