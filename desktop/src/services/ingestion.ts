/**
 * Cognitive Distillation Flow API client.
 *
 * All backend snake_case payloads pass through the single `mapIngestionValue`
 * mapper before typed data reaches the dashboard.
 *
 * Key exports: `ingestionService`, `mapIngestionValue`, and Flow data types.
 */
import api from './api';

export type IngestionWindow = '24h' | '7d' | '30d';

export interface FlowOverview {
  window: IngestionWindow;
  since: string;
  totalUnits: number;
  denominatorUnits: number;
  writtenRate: number;
  reinforcedRate: number;
  rejectedRate: number;
  outcomes: Record<string, number>;
  extractionCoverage: Record<string, { attempts: number; zeroUnitAttempts: number; unitsEmitted: number; unitsInvalid: number }>;
  linkStatus: Record<string, number>;
  parkedUnknownDomains: Record<string, number>;
  sameOriginRerouteCount: number;
  stages: Record<string, number>;
  lanes: Record<string, number>;
  sources: Record<string, number>;
  states: Record<string, number>;
  reasons: Record<string, number>;
}

export interface IngestionItem {
  id: string;
  createdAt: string;
  source: string;
  lane: string;
  state: string;
  unitStates: string[];
  stores: string[];
  reasons: string[];
  preview: string;
}

export interface IngestionItems {
  items: IngestionItem[];
  page: number;
  pageSize: number;
  total: number;
  hasNext: boolean;
  windowDays: number;
}

export interface IngestionDetail {
  candidate: Record<string, unknown>;
  envelope: Record<string, unknown>;
  units: Array<Record<string, unknown>>;
  writes: Array<Record<string, unknown>>;
  relations: Array<Record<string, unknown>>;
  detailOffset: number;
  hasMore: boolean;
  nextOffset: number | null;
  pagedFields: string[];
}

export interface BootstrapProgress {
  project: string;
  terminal: number;
  total: number;
  pending: number;
  s7: { terminal: number; total: number };
  s4Backfill: { terminal: number; total: number };
}

export interface IngestionStats {
  days: number;
  rows: Array<Record<string, unknown>>;
}

export interface LlmSourceDay {
  utcDay: string;
  source: string;
  calls: number;
  inputTokens: number;
  outputTokens: number;
}

export interface ReviewTypeStats {
  reviews: number;
  units: number;
  invalid: number;
  sampled: number;
  missed: number;
  covered: number;
  missRate: number | null;
}

/** After-session review metrics (docs/design/2026-10-29-cdf-session-review.md §12). */
export interface ReviewHealth {
  byType: Record<'code' | 'files' | 'conversation', ReviewTypeStats>;
  agentSelfWritten: number;
  agentSelfWithdrawn: number;
  agentSelfWithdrawnRate: number | null;
}

export interface IngestionHealth {
  lanes: Record<string, { backlog: number; oldestWaitSeconds: number | null }>;
  events: Array<Record<string, unknown>>;
  breakers: Array<Record<string, unknown>>;
  lastCleanupAt: string | null;
  inboxCount: number;
  inboxBytes: number;
  memoryBytes: number;
  evolutionBytes: number;
  llmBySource: LlmSourceDay[];
  review?: ReviewHealth;
}

export interface IngestionFilters {
  source?: string;
  store?: string;
  state?: string;
  reason?: string;
  keyword?: string;
  time?: number;
  pageSize?: number;
}

function camelKey(key: string): string {
  return key.replace(/_([a-z])/g, (_, letter: string) => letter.toUpperCase());
}

export function mapIngestionValue<T>(value: unknown): T {
  if (Array.isArray(value)) return value.map((entry) => mapIngestionValue(entry)) as T;
  if (value !== null && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value as Record<string, unknown>)
      .map(([key, entry]) => [camelKey(key), mapIngestionValue(entry)])) as T;
  }
  return value as T;
}

async function getMapped<T>(path: string, params?: Record<string, unknown>): Promise<T> {
  const response = await api.get(path, { params });
  return mapIngestionValue<T>(response.data);
}

export const ingestionService = {
  flow: (window: IngestionWindow) => getMapped<FlowOverview>('/ingestion/flow', { window }),
  items: (filters: IngestionFilters, page: number) =>
    getMapped<IngestionItems>('/ingestion/items', { ...filters, page }),
  detail: (id: string, offset = 0) => getMapped<IngestionDetail>(
    `/ingestion/items/${encodeURIComponent(id)}`, { offset }),
  stats: (range = 30) => getMapped<IngestionStats>('/ingestion/stats', { range }),
  bootstrapProgress: (project: string) => getMapped<BootstrapProgress>(
    '/ingestion/bootstrap-progress', { project }),
  health: () => getMapped<IngestionHealth>('/ingestion/health'),
  redact: async (anchor: string, reason: string) => {
    const response = await api.post('/ingestion/redact', { anchor, reason }, {
      headers: { 'X-Swarm-User-Action': 'redact' },
    });
    return mapIngestionValue<{ revision: string }>(response.data);
  },
};
