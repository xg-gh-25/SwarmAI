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
  sameOriginRerouteCount: number;
  lanes: Record<string, number>;
  sources: Record<string, number>;
  states: Record<string, number>;
  reasons: Record<string, number>;
}

/** Where kept units landed (one store / project / file). */
export interface LandedRow { store: string; project: string | null; path: string | null; written: number; reinforced: number }
export interface MovedRow { from: string; to: string; count: number }
/** An anomaly worth a person's look; the view phrases it by `code`. */
export interface FlowAttention { code: string; count?: number | null; example?: string | null; bytes?: number | null; since?: string | null }

/** One bucket of the window's trend (an hour for 24h, a day for 7d / 30d; UTC prefix). */
export interface TrendPoint { bucket: string; captured: number; learned: number; reinforced: number; notKept: number }

/** GET /ingestion/landed — what the Cognition flow view shows first (backend flow_view.py). */
export interface LandedOverview {
  window: IngestionWindow;
  since: string;
  total: number;
  series: TrendPoint[];
  /** The window before, for the change; null when the store does not reach that far. */
  previous: Omit<TrendPoint, 'bucket'> | null;
  totals: { new: number; adds: number; reinforced: number; notKept: number; waiting: number };
  groups: Record<string, number>;
  reasons: Record<string, Record<string, number>>;
  sources: Record<string, number>;
  steps: { captured: number; basicDropped: number; placeDropped: number; moved: number; reinforced: number; known: number; withdrawn: number; older: number; written: number };
  landed: LandedRow[];
  moved: MovedRow[];
  attention: FlowAttention[];
  storeBytes: number;
  /** The oldest item still in the store (the 30-day cleanup keeps it younger than that). */
  oldestAt: string | null;
}

export type UnitOutcome = 'all' | 'new' | 'adds' | 'reinforced' | 'not_kept' | 'waiting';
export type UnitPlace = 'every_turn' | 'project' | 'general';

export interface UnitFilters {
  window: IngestionWindow;
  outcome: UnitOutcome;
  place?: UnitPlace;
  source?: string;
  store?: string;
  project?: string;
  path?: string;
  /** Where the unit really landed starts with this (case-sensitive): one project's files. */
  pathPrefix?: string;
  keyword?: string;
}

/** One unit as a person reads it (GET /ingestion/units). */
export interface FlowUnit {
  unitId: string;
  candidateId: string;
  createdAt: string;
  source: string;
  sourceRef: string;
  kind: string;
  type: string | null;
  title: string;
  text: string;
  scope: string | null;
  state: string;
  reason: string | null;
  /** The decision step behind the outcome (flow_view.reason_group). */
  group: string;
  store: string | null;
  project: string | null;
  path: string | null;
  section: string | null;
  movedFrom: string | null;
  relation: 'same' | 'adds' | null;
  target: string | null;
  basis: string | null;
}

export interface UnitsPage { items: FlowUnit[]; page: number; pageSize: number; total: number; hasNext: boolean; searchCapped: boolean }

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
  landed: (window: IngestionWindow) => getMapped<LandedOverview>('/ingestion/landed', { window }),
  units: (filters: UnitFilters, page: number, pageSize = 25) => getMapped<UnitsPage>('/ingestion/units', {
    window: filters.window, outcome: filters.outcome, place: filters.place, source: filters.source,
    store: filters.store, project: filters.project, path: filters.path, path_prefix: filters.pathPrefix,
    keyword: filters.keyword?.trim() || undefined, page, page_size: pageSize,
  }),
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
