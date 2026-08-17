/**
 * OS Eval Dashboard — Interactive eval health, golden set CRUD, run triggers, and trends.
 *
 * P2: Read-only visualization.
 * P3: CRUD on golden set, run triggers, case detail drawer, sparklines.
 *
 * Data fetched from /api/eval/* endpoints via TanStack Query.
 */
import { useState, useEffect, useMemo } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import api from '../services/api';
import { getApiBaseUrl } from '../services/tauri';
import { openExternal } from '../utils/openExternal';
import { computeBreakdowns, type Breakdowns, type BreakdownEntry } from './eval-breakdowns';

// ─── Types ────────────────────────────────────────────────────────────────────

interface IntelligenceVelocity {
  score: number;
  components: {
    pass_rate: number;
    stability_ratio: number;
    golden_set_size: number;
    golden_set_size_score: number;
    growth_score: number;
    draft_count: number;
    stable_count: number;
  };
}

interface EvalHealth {
  overall_score: number | null;
  dimensions: Record<string, number>;
  last_run: {
    run_id: string;
    triggered_by: string;
    triggered_at: string;
    cases_passed: number;
    cases_failed: number;
    cases_skipped: number;
  } | null;
  total_cases: number;
  trend: { delta: number; direction: string } | null;
  intelligence_velocity?: IntelligenceVelocity;
}

interface EvalRun {
  run_id: string;
  triggered_by: string;
  triggered_at: string;
  overall_score: number;
  total_cases: number;
  cases_passed: number;
  cases_failed: number;
  cases_skipped: number;
  duration_seconds: number;
  dimensions: Record<string, number>;
}

export interface GoldenSetCase {
  id: string;
  category: string;
  dimension: string;
  level: string;
  title: string;
  tier: string;
  eval_method?: string;
  _origin?: string; // "public" | "private" — ship tag, set by backend
  evaluators: string[];
  affected_by: string[];
  last_result: { status: string; run_id: string; triggered_at: string } | null;
}

interface GoldenSetResponse {
  total_cases: number;
  filtered_count: number;
  categories: string[];
  dimensions: string[];
  cases: GoldenSetCase[];
}

// ─── Data Hooks ───────────────────────────────────────────────────────────────

function useEvalHealth() {
  return useQuery<EvalHealth>({
    queryKey: ['eval-health'],
    queryFn: async () => (await api.get<EvalHealth>('/eval/health')).data,
    staleTime: 60_000,
  });
}

function useEvalHistory() {
  return useQuery<EvalRun[]>({
    queryKey: ['eval-history'],
    queryFn: async () => (await api.get<EvalRun[]>('/eval/history')).data,
    staleTime: 60_000,
  });
}

function useGoldenSet(category?: string) {
  return useQuery<GoldenSetResponse>({
    queryKey: ['eval-golden-set', category],
    queryFn: async () => {
      const params = category ? `?category=${encodeURIComponent(category)}` : '';
      return (await api.get<GoldenSetResponse>(`/eval/golden-set${params}`)).data;
    },
    staleTime: 60_000,
  });
}

// ─── Case Detail Types ──────────────────────────────────────────────────────

interface CaseDetail extends GoldenSetCase {
  scenario?: { turns?: { input: string }[] };
  verification?: Record<string, string>;
  expected_trajectory?: string[];
  trajectory_match?: string;
  assertions?: string[];
  expected_response_contains?: string[];
  source?: string;
  tags?: string[];
  promoted_from?: string;
  history?: { run_id: string; triggered_at: string; status: string; notes?: string }[];
}

// ─── Mutation Hooks (P3) ────────────────────────────────────────────────────

function useCreateCase() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (caseData: Record<string, unknown>) => {
      return (await api.post('/eval/golden-set', caseData)).data;
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['eval-golden-set'] }); },
  });
}

function useUpdateCase() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, updates }: { id: string; updates: Record<string, unknown> }) => {
      return (await api.put(`/eval/golden-set/${id}`, updates)).data;
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['eval-golden-set'] }); },
  });
}

function useDeleteCase() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (caseId: string) => {
      return (await api.delete(`/eval/golden-set/${caseId}`)).data;
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['eval-golden-set'] }); },
  });
}

function useTriggerRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (params: { trigger?: string; case_ids?: string[] } = {}) => {
      return (await api.post('/eval/run', { trigger: params.trigger || 'manual', case_ids: params.case_ids })).data;
    },
    onSuccess: () => {
      // Refresh after a delay (background run takes time)
      setTimeout(() => {
        qc.invalidateQueries({ queryKey: ['eval-health'] });
        qc.invalidateQueries({ queryKey: ['eval-history'] });
      }, 3000);
    },
  });
}

function useRunCanary() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      return (await api.post('/eval/canary')).data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['eval-health'] });
      qc.invalidateQueries({ queryKey: ['eval-history'] });
    },
  });
}

function useCaseDetail(caseId: string | null) {
  return useQuery<CaseDetail>({
    queryKey: ['eval-case-detail', caseId],
    queryFn: async () => (await api.get<CaseDetail>(`/eval/golden-set/${caseId}`)).data,
    enabled: !!caseId,
    staleTime: 30_000,
  });
}

// Per-case results from a single run. NOTE: run cases carry NO `dimension` field
// (only id/status/evaluator/duration_ms/notes) — to label by dimension we join
// against the golden set by id at render time (Gate-1 BLOCK#1).
interface RunCaseResult {
  id: string;
  status: string;
  evaluator?: string;
  duration_ms?: number;
  notes?: string;
}
interface RunDetailData {
  run_id: string;
  triggered_by?: string;
  triggered_at?: string;
  overall_score?: number | null;
  dimensions?: Record<string, number>;
  total_cases?: number;
  cases_passed?: number;
  cases_failed?: number;
  cases_skipped?: number;
  cases_error?: number;
  duration_seconds?: number;
  cases?: RunCaseResult[];
}

function useRunDetail(runId: string | null) {
  return useQuery<RunDetailData>({
    queryKey: ['eval-run-detail', runId],
    queryFn: async () => (await api.get<RunDetailData>(`/eval/runs/${runId}`)).data,
    enabled: !!runId,
    staleTime: 30_000,
  });
}

// ─── Tabs ─────────────────────────────────────────────────────────────────────

const TABS = [
  { id: 'overview', label: 'Overview', icon: 'monitoring' },
  { id: 'golden-set', label: 'Golden Set', icon: 'checklist' },
  { id: 'session-quality', label: 'Session Quality', icon: 'reviews' },
  { id: 'context', label: 'Context Health', icon: 'sync' },
  { id: 'governance', label: 'Governance', icon: 'gavel' },
  { id: 'trends', label: 'Trends', icon: 'trending_up' },
  { id: 'reports', label: 'Reports', icon: 'description' },
  { id: 'guide', label: 'Guide', icon: 'menu_book' },
] as const;

type TabId = typeof TABS[number]['id'];

// ─── Main Component ───────────────────────────────────────────────────────────

export default function EvalDashboard() {
  const [activeTab, setActiveTab] = useState<TabId>('overview');

  return (
    <div className="flex flex-col h-full">
      {/* Tab bar — matches SettingsTabs */}
      <div className="shrink-0 px-6 pt-3 border-b border-[var(--color-border)]">
        <div className="flex gap-1 justify-center">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`px-3 py-2.5 text-sm font-medium transition-colors flex items-center gap-1.5 border-b-2 -mb-px whitespace-nowrap ${
                activeTab === tab.id
                  ? 'text-[var(--color-primary)] border-[var(--color-primary)]'
                  : 'text-[var(--color-text-muted)] border-transparent hover:text-[var(--color-text)] hover:border-[var(--color-border)]'
              }`}
            >
              <span className="material-symbols-outlined text-base">{tab.icon}</span>
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {/* Tab content — scrollable (GoldenSetTab uses h-full + internal overflow, no nested scroll) */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        {activeTab === 'overview' && <OverviewTab />}
        {activeTab === 'golden-set' && <GoldenSetTab />}
        {activeTab === 'session-quality' && <SessionQualityTab />}
        {activeTab === 'context' && <ContextHealthTab />}
        {activeTab === 'governance' && <GovernanceTab />}
        {activeTab === 'trends' && <TrendsTab />}
        {activeTab === 'reports' && <ReportsTab />}
        {activeTab === 'guide' && <GuideTab />}
      </div>
    </div>
  );
}

// ─── Overview Tab ─────────────────────────────────────────────────────────────

function OverviewTab() {
  const { data: health, isError: healthError } = useEvalHealth();
  const { data: history } = useEvalHistory();
  const triggerRun = useTriggerRun();
  const runCanary = useRunCanary();

  if (healthError) return <ErrorState message="Failed to load eval health. Is the backend running?" />;
  if (!health) return <Loading />;

  const dims = health.dimensions || {};
  const dimEntries = Object.entries(dims);

  return (
    <div className="max-w-5xl mx-auto p-6">
      {/* Action Buttons */}
      <div className="flex gap-2 mb-4 justify-end">
        <button
          onClick={() => runCanary.mutate()}
          disabled={runCanary.isPending}
          className="px-3 py-1.5 text-xs font-medium rounded-lg border border-[var(--color-border)] hover:bg-[var(--color-hover)] transition-colors disabled:opacity-50"
        >
          {runCanary.isPending ? 'Running...' : '⚡ Run Canary'}
        </button>
        <button
          onClick={() => triggerRun.mutate({})}
          disabled={triggerRun.isPending}
          className="px-3 py-1.5 text-xs font-medium rounded-lg bg-[var(--color-primary)] text-white hover:opacity-90 transition-opacity disabled:opacity-50"
        >
          {triggerRun.isPending ? 'Starting...' : '▶ Run Full Eval'}
        </button>
      </div>
      {(triggerRun.isSuccess || runCanary.isSuccess) && (
        <div className="mb-3 p-2 rounded border border-green-500/20 bg-green-500/5 text-xs text-green-500">
          {triggerRun.isSuccess && `✓ Eval started: ${(triggerRun.data as { run_id?: string })?.run_id || 'running'}`}
          {runCanary.isSuccess && `✓ Canary complete: ${(runCanary.data as { overall_score?: number })?.overall_score}%`}
        </div>
      )}

      {/* KPI Cards */}
      <div className="grid grid-cols-5 gap-3 mb-6">
        <MetricCard
          label="OS Health Score"
          value={health.overall_score != null ? `${health.overall_score}%` : '—'}
          color={health.overall_score != null && health.overall_score >= 80 ? 'green' : 'yellow'}
          sub={health.trend ? `${health.trend.delta > 0 ? '↑' : '↓'} ${Math.abs(health.trend.delta)}% vs prev` : 'No trend yet'}
        />
        <MetricCard
          label="Intelligence Velocity"
          value={health.intelligence_velocity ? `${health.intelligence_velocity.score}` : '—'}
          color={health.intelligence_velocity && health.intelligence_velocity.score >= 50 ? 'green' : 'yellow'}
          sub={health.intelligence_velocity ? `${health.intelligence_velocity.components.stable_count} stable, ${health.intelligence_velocity.components.draft_count} draft` : ''}
        />
        <MetricCard
          label="Cases Passed"
          value={health.last_run ? `${health.last_run.cases_passed}/${health.total_cases}` : '—'}
          color="green"
          sub={health.last_run ? `${health.last_run.cases_skipped} skipped (LLM-judge)` : ''}
        />
        <MetricCard
          label="Dimensions"
          value={`${dimEntries.length}`}
          color="default"
          sub={dimEntries.filter(([, v]) => v >= 80).length + ' green, ' + dimEntries.filter(([, v]) => v < 80).length + ' attention'}
        />
        <MetricCard
          label="Last Run"
          value={health.last_run?.triggered_by || '—'}
          color="default"
          sub={health.last_run ? new Date(health.last_run.triggered_at).toLocaleDateString() : ''}
        />
      </div>

      {/* Dimension Scores */}
      {dimEntries.length > 0 && (
        <div className="mb-6">
          <h3 className="text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wide mb-3">Dimensions</h3>
          <div className="grid grid-cols-3 gap-3">
            {dimEntries.map(([dim, score]) => (
              <div key={dim} className="flex items-center gap-3 p-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)]">
                <div className={`w-2 h-2 rounded-full ${score >= 80 ? 'bg-green-500' : score >= 60 ? 'bg-yellow-500' : 'bg-red-500'}`} />
                <div className="flex-1 min-w-0">
                  <div className="text-xs font-medium truncate">{dim.replace(/_/g, ' ')}</div>
                </div>
                <div className="text-sm font-mono font-semibold">{score}%</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Recent Runs */}
      <h3 className="text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wide mb-3">Recent Eval Runs</h3>
      <div className="border border-[var(--color-border)] rounded-lg overflow-hidden">
        <table className="w-full text-xs">
          <thead>
            <tr className="bg-[var(--color-bg)] border-b border-[var(--color-border)]">
              <th className="text-left px-3 py-2 font-medium text-[var(--color-text-muted)]">Date</th>
              <th className="text-left px-3 py-2 font-medium text-[var(--color-text-muted)]">Trigger</th>
              <th className="text-left px-3 py-2 font-medium text-[var(--color-text-muted)]">Score</th>
              <th className="text-left px-3 py-2 font-medium text-[var(--color-text-muted)]">Pass/Fail/Skip</th>
              <th className="text-left px-3 py-2 font-medium text-[var(--color-text-muted)]">Duration</th>
            </tr>
          </thead>
          <tbody>
            {(history || []).slice(0, 10).map((run) => (
              <tr key={run.run_id} className="border-b border-[var(--color-border)] last:border-0 hover:bg-[var(--color-hover)]">
                <td className="px-3 py-2 font-mono">{run.triggered_at?.slice(0, 10)}</td>
                <td className="px-3 py-2">{run.triggered_by}</td>
                <td className="px-3 py-2">
                  <span className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-semibold ${
                    run.overall_score >= 80 ? 'bg-green-500/10 text-green-500' : 'bg-yellow-500/10 text-yellow-500'
                  }`}>
                    {run.overall_score}%
                  </span>
                </td>
                <td className="px-3 py-2 font-mono">
                  <span className="text-green-500">{run.cases_passed}</span>
                  {' / '}
                  <span className="text-red-500">{run.cases_failed}</span>
                  {' / '}
                  <span className="text-[var(--color-text-muted)]">{run.cases_skipped}</span>
                </td>
                <td className="px-3 py-2 font-mono">{run.duration_seconds?.toFixed(1)}s</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ─── Golden Set Tab ───────────────────────────────────────────────────────────

// ─── Summary Chips ────────────────────────────────────────────────────────
// A compact, scannable distribution of the golden set across four facets.
// Category chips are interactive (drill into the table); the other three are
// read-only at-a-glance counts. All counts come from the live cases[] data.

function ChipGroup({
  label,
  entries,
  testidPrefix,
  activeKey,
  onClick,
}: {
  label: string;
  entries: BreakdownEntry[];
  testidPrefix: string;
  activeKey?: string;
  onClick?: (key: string) => void;
}) {
  const interactive = !!onClick;
  return (
    <div className="flex flex-col gap-1">
      <span className="text-[9px] uppercase tracking-wide text-[var(--color-text-muted)] font-medium">{label}</span>
      <div className="flex flex-wrap gap-1">
        {entries.map((e) => {
          const isActive = activeKey === e.key;
          const Tag = interactive ? 'button' : 'div';
          return (
            <Tag
              key={e.key}
              data-testid={`chip-${testidPrefix}-${e.key}`}
              {...(interactive ? { onClick: () => onClick!(e.key), type: 'button' as const } : {})}
              title={interactive ? `Filter by ${label.toLowerCase()}: ${e.key}` : `${e.key}: ${e.count}`}
              className={`flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] border transition-colors ${
                isActive
                  ? 'bg-[var(--color-primary)] text-white border-[var(--color-primary)]'
                  : 'bg-[var(--color-bg)] border-[var(--color-border)] text-[var(--color-text-secondary)]'
              } ${interactive ? 'cursor-pointer hover:border-[var(--color-primary)]' : ''}`}
            >
              <span>{e.key.replace(/_/g, ' ')}</span>
              <span data-testid={`chip-${testidPrefix}-${e.key}-count`} className={`font-semibold ${isActive ? 'text-white' : 'text-[var(--color-text)]'}`}>{e.count}</span>
            </Tag>
          );
        })}
      </div>
    </div>
  );
}

function SummaryChips({
  breakdowns,
  activeCategory,
  onCategoryClick,
}: {
  breakdowns: Breakdowns;
  activeCategory: string;
  onCategoryClick: (key: string) => void;
}) {
  return (
    <div
      data-testid="golden-summary"
      className="mb-3 p-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] flex flex-col gap-2.5"
    >
      <ChipGroup label="Category" entries={breakdowns.category} testidPrefix="category" activeKey={activeCategory || undefined} onClick={onCategoryClick} />
      <div className="flex flex-wrap gap-x-6 gap-y-2.5">
        <ChipGroup label="Tier" entries={breakdowns.tier} testidPrefix="tier" />
        <ChipGroup label="Eval Method" entries={breakdowns.eval_method} testidPrefix="eval_method" />
        <ChipGroup label="Dimension" entries={breakdowns.dimension} testidPrefix="dimension" />
      </div>
    </div>
  );
}

export function GoldenSetTab() {
  const { data: gs, isError: gsError } = useGoldenSet();
  const deleteCase = useDeleteCase();
  const triggerRun = useTriggerRun();
  const [selectedCaseId, setSelectedCaseId] = useState<string | null>(null);
  const [showAddForm, setShowAddForm] = useState(false);
  const [archiveId, setArchiveId] = useState<string | null>(null); // B11: in-app archive-confirm (was native confirm())
  const [archiveErr, setArchiveErr] = useState(false); // B11: surface archive failure (Gate-2 INFO)
  const [searchQuery, setSearchQuery] = useState('');
  const [filterCategory, setFilterCategory] = useState('');
  const [filterStatus, setFilterStatus] = useState('');
  const [filterTier, setFilterTier] = useState('');
  // Collapsed category sections (default: all expanded). Orthogonal to filters.
  const [collapsedCats, setCollapsedCats] = useState<Set<string>>(new Set());

  // Breakdown counts are computed from the FULL set (not the filtered view) so
  // the user always sees the whole distribution to drill into. Hooks must run
  // before the early return, so guard against undefined data inside the memo.
  const breakdowns = useMemo(() => computeBreakdowns(gs?.cases ?? []), [gs?.cases]);

  if (gsError) return <ErrorState message="Failed to load the golden set. Is the backend running?" />;
  if (!gs) return <Loading />;

  // Client-side filtering
  const filtered = gs.cases.filter((c) => {
    if (searchQuery && !c.id.toLowerCase().includes(searchQuery.toLowerCase()) && !c.title.toLowerCase().includes(searchQuery.toLowerCase())) return false;
    if (filterCategory && c.category !== filterCategory) return false;
    if (filterTier && c.tier !== filterTier) return false;
    if (filterStatus === 'passed' && c.last_result?.status !== 'passed') return false;
    if (filterStatus === 'failed' && c.last_result?.status !== 'failed') return false;
    if (filterStatus === 'skipped' && c.last_result?.status !== 'skipped') return false;
    return true;
  });

  // Group the FILTERED set by category (not the full set) so filters and
  // grouping agree — zero-count groups are simply absent. Sorted by count desc.
  const groupedByCat = (() => {
    const m = new Map<string, GoldenSetCase[]>();
    for (const c of filtered) {
      const k = c.category || 'uncategorized';
      (m.get(k) ?? m.set(k, []).get(k)!).push(c);
    }
    return [...m.entries()].sort((a, b) => b[1].length - a[1].length);
  })();
  const toggleCat = (cat: string) =>
    setCollapsedCats((prev) => {
      const next = new Set(prev);
      next.has(cat) ? next.delete(cat) : next.add(cat);
      return next;
    });

  return (
    <div className="flex h-full">
      {/* Main table */}
      <div className={`flex-1 p-6 overflow-hidden flex flex-col ${selectedCaseId ? 'pr-3' : ''}`}>
        {/* Summary breakdown — counts by category / tier / eval method / dimension,
            computed live from cases[]. Category chips drill into the table. */}
        <SummaryChips
          breakdowns={breakdowns}
          activeCategory={filterCategory}
          onCategoryClick={(cat) => setFilterCategory((prev) => (prev === cat ? '' : cat))}
        />

        {/* Filter bar (matches mockup) */}
        <div className="flex items-center gap-2 mb-3">
          <input
            type="text"
            placeholder="Search by ID or title..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="flex-1 max-w-[240px] px-2.5 py-1.5 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] text-xs text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] outline-none focus:border-[var(--color-primary)]"
          />
          <select
            value={filterCategory}
            onChange={(e) => setFilterCategory(e.target.value)}
            className="px-2.5 py-1.5 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] text-xs text-[var(--color-text)] outline-none cursor-pointer"
          >
            <option value="">All Categories</option>
            {(gs.categories || []).map((cat) => <option key={cat} value={cat}>{cat}</option>)}
          </select>
          <select
            value={filterStatus}
            onChange={(e) => setFilterStatus(e.target.value)}
            className="px-2.5 py-1.5 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] text-xs text-[var(--color-text)] outline-none cursor-pointer"
          >
            <option value="">All Status</option>
            <option value="passed">Passed</option>
            <option value="failed">Failed</option>
            <option value="skipped">Skipped</option>
          </select>
          <select
            data-testid="filter-tier"
            value={filterTier}
            onChange={(e) => setFilterTier(e.target.value)}
            className="px-2.5 py-1.5 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] text-xs text-[var(--color-text)] outline-none cursor-pointer"
          >
            <option value="">All Tiers</option>
            {breakdowns.tier.map((t) => <option key={t.key} value={t.key}>{t.key}</option>)}
          </select>
          <div className="flex-1" />
          <span className="text-[10px] text-[var(--color-text-muted)]">
            {filtered.length}/{gs.total_cases} cases
          </span>
        </div>

        {/* Grouped case list — collapsible category sections. Each row shows an
            origin badge (public/private) + eval_method so curated vs instance and
            test type are distinguishable at a glance (fixes "傻傻分不清"). */}
        <div className="border border-[var(--color-border)] rounded-lg overflow-y-auto flex-1 min-h-0" data-testid="golden-set-groups">
          {groupedByCat.length === 0 && (
            <div className="px-3 py-8 text-center text-xs text-[var(--color-text-muted)]">No cases match the current filters.</div>
          )}
          {groupedByCat.map(([cat, cases]) => {
            const collapsed = collapsedCats.has(cat);
            return (
              <div key={cat} data-testid={`cat-group-${cat}`}>
                {/* Group header */}
                <button
                  onClick={() => toggleCat(cat)}
                  className="w-full flex items-center gap-2 px-3 py-2 bg-[var(--color-bg)] border-b border-[var(--color-border)] sticky top-0 z-10 hover:bg-[var(--color-hover)] transition-colors text-left"
                >
                  <span className="material-symbols-outlined text-sm text-[var(--color-text-muted)]">
                    {collapsed ? 'chevron_right' : 'expand_more'}
                  </span>
                  <span className="font-semibold text-xs">{cat}</span>
                  <span className="px-1.5 py-0.5 rounded-full bg-[var(--color-hover)] text-[10px] text-[var(--color-text-muted)]">{cases.length}</span>
                </button>
                {/* Group rows */}
                {!collapsed && (
                  <table className="w-full text-xs">
                    <tbody>
                      {cases.map((c) => (
                        <tr
                          key={c.id}
                          onClick={() => setSelectedCaseId(c.id)}
                          className={`border-b border-[var(--color-border)] last:border-0 hover:bg-[var(--color-hover)] cursor-pointer ${selectedCaseId === c.id ? 'bg-[var(--color-primary)]/5' : ''}`}
                        >
                          <td className="pl-9 pr-3 py-2 font-mono font-semibold whitespace-nowrap">{c.id}</td>
                          <td className="px-3 py-2 max-w-[220px] truncate" title={c.title}>{c.title}</td>
                          <td className="px-3 py-2"><OriginBadge origin={c._origin} /></td>
                          <td className="px-3 py-2">
                            {c.eval_method && (
                              <span className="px-1.5 py-0.5 rounded bg-[var(--color-hover)] text-[10px] text-[var(--color-text-secondary)]">{c.eval_method}</span>
                            )}
                          </td>
                          <td className="px-3 py-2 text-[var(--color-text-muted)] whitespace-nowrap">{c.dimension?.replace(/_/g, ' ')}</td>
                          <td className="px-3 py-2 text-[var(--color-text-muted)]">{c.tier}</td>
                          <td className="px-3 py-2"><StatusBadge status={c.last_result?.status} /></td>
                          <td className="px-3 py-2" onClick={(e) => e.stopPropagation()}>
                            <button
                              onClick={() => setArchiveId(c.id)}
                              className="text-[var(--color-text-muted)] hover:text-red-500 transition-colors"
                              title="Archive"
                            >
                              <span className="material-symbols-outlined text-sm">archive</span>
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            );
          })}
        </div>

        {/* Footer action buttons (matches mockup) */}
        <div className="flex gap-2 mt-3 pt-3 border-t border-[var(--color-border)]">
          <button
            onClick={() => setShowAddForm(true)}
            className="px-2.5 py-1.5 text-[10px] font-medium rounded-md bg-[var(--color-primary)] text-white hover:opacity-90 transition-opacity"
          >
            + Add Case
          </button>
          {/* Not yet wired (no /eval import-from-correction or archive-stable
              endpoint) — disabled + labeled so they don't look clickable.
              Enable when those APIs ship. */}
          <button
            disabled
            title="Coming soon — not yet available"
            className="px-2.5 py-1.5 text-[10px] font-medium rounded-md border border-[var(--color-border)] text-[var(--color-text-secondary)] opacity-50 cursor-not-allowed"
          >
            Import from Correction
            <span className="ml-1 text-[8px] uppercase tracking-wide opacity-70">soon</span>
          </button>
          <button
            disabled
            title="Coming soon — not yet available"
            className="px-2.5 py-1.5 text-[10px] font-medium rounded-md border border-[var(--color-border)] text-[var(--color-text-secondary)] opacity-50 cursor-not-allowed"
          >
            Archive Stable
            <span className="ml-1 text-[8px] uppercase tracking-wide opacity-70">soon</span>
          </button>
          <button
            onClick={() => triggerRun.mutate({})}
            className="px-2.5 py-1.5 text-[10px] font-medium rounded-md border border-[var(--color-border)] text-[var(--color-text-secondary)] hover:border-[var(--color-primary)] hover:text-[var(--color-primary)] transition-colors"
          >
            Run All
          </button>
        </div>
      </div>

      {/* Case Detail Drawer — key forces remount on case change to reset edit state */}
      {selectedCaseId && (
        <CaseDetailDrawer key={selectedCaseId} caseId={selectedCaseId} onClose={() => setSelectedCaseId(null)} />
      )}

      {/* Add Case Modal */}
      {showAddForm && (
        <AddCaseModal onClose={() => setShowAddForm(false)} categories={gs.categories || []} />
      )}
      {/* B11: in-app archive confirmation (was a jarring native confirm()). */}
      {archiveId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={() => { if (!deleteCase.isPending) { setArchiveId(null); setArchiveErr(false); } }}>
          <div className="w-full max-w-sm bg-[var(--color-card)] rounded-xl border border-[var(--color-border)] shadow-2xl p-6" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-sm font-semibold mb-2">Archive golden case?</h3>
            <p className="text-xs text-[var(--color-text-muted)] mb-4 break-all">
              <span className="font-mono">{archiveId}</span> will be archived (removed from the active set).
            </p>
            {archiveErr && (
              <p data-testid="eval-archive-error" className="text-xs text-red-400 mb-3">Could not archive — please try again.</p>
            )}
            <div className="flex justify-end gap-2">
              <button disabled={deleteCase.isPending} onClick={() => { setArchiveId(null); setArchiveErr(false); }} className="px-3 py-1.5 text-xs rounded border border-[var(--color-border)] hover:bg-[var(--color-hover)] disabled:opacity-50">Cancel</button>
              <button
                data-testid="eval-archive-confirm"
                disabled={deleteCase.isPending}
                onClick={() => {
                  setArchiveErr(false);
                  // B11 (Gate-2 INFO): only close on SUCCESS; surface failure instead
                  // of optimistically closing (the silent-failure class this set fixes).
                  deleteCase.mutate(archiveId, {
                    onSuccess: () => setArchiveId(null),
                    onError: () => setArchiveErr(true),
                  });
                }}
                className="px-3 py-1.5 text-xs rounded bg-red-500 text-white hover:opacity-90 disabled:opacity-50"
              >
                {deleteCase.isPending ? 'Archiving…' : 'Archive'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Governance Tab (v3 Phase 3) ───────────────────────────────────────────────

interface GovProposal {
  id: string;
  source_class: string;
  proposal_kind: 'rule' | 'gate';
  occurrence_count: number;
  proposed_rule: string;
  confidence: number;
  evidence?: string[];
}

interface GovPendingResponse {
  proposals: GovProposal[];
  total: number;
}

function useGovernancePending() {
  return useQuery<GovPendingResponse>({
    queryKey: ['eval-governance-pending'],
    queryFn: async () => (await api.get<GovPendingResponse>('/eval/governance/pending')).data,
    staleTime: 30_000,
  });
}

function useGovernanceDecision() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ proposalId, decision }: { proposalId: string; decision: string }) => {
      return (await api.post('/eval/governance/decision', {
        proposal_id: proposalId,
        decision,
      })).data;
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['eval-governance-pending'] }); },
  });
}

// Pure presentational card — extracted so the evidence render is unit-testable
// without a QueryClient/API mock (Bug1, run_685db747). GovernanceTab owns fetching;
// this owns display. Rendering evidence[] is what makes proposals distinguishable
// (the missing render was the root cause of "clicked A but accepted B").
export function GovernanceProposalCard({
  proposal: p,
  onAct,
  pending,
}: {
  proposal: GovProposal;
  onAct: (proposalId: string, decision: 'accept' | 'reject' | 'defer') => void;
  pending: boolean;
}) {
  const evidence = p.evidence ?? [];
  return (
    <div className="border border-[var(--color-border)] rounded-lg p-3">
      <div className="flex items-center gap-2 mb-1.5">
        <span className={`px-1.5 py-0.5 text-[10px] font-semibold rounded ${
          p.proposal_kind === 'gate'
            ? 'bg-red-500/10 text-red-600'
            : 'bg-blue-500/10 text-blue-600'
        }`}>
          {p.proposal_kind.toUpperCase()}
        </span>
        <span className="text-sm font-medium text-[var(--color-text)]">{p.source_class}</span>
        <span className="text-[10px] text-[var(--color-text-muted)]">{p.occurrence_count}×</span>
      </div>
      <p className="text-xs text-[var(--color-text)] mb-2">{p.proposed_rule}</p>

      {/* Evidence — the actual correction excerpts behind the count. Without this
          the cards are indistinguishable and cannot be judged (Bug1 root cause). */}
      {evidence.length > 0 ? (
        <div className="mb-2 border-l-2 border-[var(--color-border)] pl-2 space-y-1">
          <div className="text-[10px] font-semibold text-[var(--color-text-muted)] uppercase tracking-wide">
            Evidence ({evidence.length})
          </div>
          {evidence.map((e, i) => (
            <p key={i} className="text-[11px] text-[var(--color-text-muted)] leading-snug">
              • {e}
            </p>
          ))}
        </div>
      ) : (
        <p className="mb-2 text-[11px] italic text-[var(--color-text-muted)]">
          No evidence recorded — judge with caution.
        </p>
      )}

      <div className="flex gap-1.5 items-center">
        <button
          onClick={() => onAct(p.id, 'accept')}
          disabled={pending}
          className="px-2 py-1 text-xs rounded bg-green-500/10 text-green-600 hover:bg-green-500/20 disabled:opacity-50"
        >
          Accept
        </button>
        <button
          onClick={() => onAct(p.id, 'reject')}
          disabled={pending}
          className="px-2 py-1 text-xs rounded bg-red-500/10 text-red-600 hover:bg-red-500/20 disabled:opacity-50"
        >
          Reject
        </button>
        <button
          onClick={() => onAct(p.id, 'defer')}
          disabled={pending}
          className="px-2 py-1 text-xs rounded border border-[var(--color-border)] text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-50"
        >
          Defer
        </button>
      </div>
    </div>
  );
}

function GovernanceTab() {
  const { data, isLoading, isError } = useGovernancePending();
  const decide = useGovernanceDecision();
  const proposals = data?.proposals ?? [];

  const act = (proposalId: string, decision: 'accept' | 'reject' | 'defer') => {
    decide.mutate({ proposalId, decision });
  };

  return (
    <div className="p-6 max-w-3xl mx-auto">
      <div className="mb-4">
        <h2 className="text-base font-semibold text-[var(--color-text)] flex items-center gap-1.5">
          <span className="material-symbols-outlined text-lg text-[var(--color-primary)]">gavel</span>
          Governance Proposals
        </h2>
        <p className="text-xs text-[var(--color-text-muted)] mt-1">
          Recurring judgment patterns the system flagged. Accept a <b>rule</b> to record it
          (a class that recurs after a rule escalates to a <b>gate</b>). Accept never edits
          SOUL/AGENT/STEERING — it only records the decision in the tracker.
        </p>
      </div>

      {isError && <div className="text-sm text-red-400">Failed to load governance proposals. Is the backend running?</div>}

      {isLoading && !isError && <div className="text-sm text-[var(--color-text-muted)]">Loading…</div>}

      {!isLoading && !isError && proposals.length === 0 && (
        <div className="text-sm text-[var(--color-text-muted)] border border-dashed border-[var(--color-border)] rounded-lg p-6 text-center">
          No pending governance proposals. The escalation ladder surfaces them here when a
          correction class recurs ≥3× without a structural fix.
        </div>
      )}

      <div className="space-y-2">
        {proposals.map((p) => (
          <GovernanceProposalCard key={p.id} proposal={p} onAct={act} pending={decide.isPending} />
        ))}
      </div>
    </div>
  );
}

// ─── Trends Tab ───────────────────────────────────────────────────────────────

export function TrendsTab() {
  const { data: history, isError } = useEvalHistory();
  const [detailRunId, setDetailRunId] = useState<string | null>(null);

  if (isError) return <ErrorState message="Failed to load eval history. Is the backend running?" />;
  if (!history || history.length === 0) {
    return (
      <div className="max-w-5xl mx-auto p-6">
        <p className="text-sm text-[var(--color-text-muted)]">
          Trends require at least 2 eval runs. Run more evaluations to see historical data.
        </p>
      </div>
    );
  }

  const runs = [...history].reverse().slice(-10); // oldest→newest, max 10
  const allDims = Object.keys(history[0]?.dimensions || {});
  const recent = history.slice(0, 3); // newest-first, top 3 for visualized detail

  return (
    <div className="max-w-5xl mx-auto p-6">
      {/* Recent runs — click to visualize per-case results */}
      <h3 className="text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wide mb-4">Recent Runs</h3>
      <div className="grid grid-cols-3 gap-3 mb-8" data-testid="recent-runs">
        {recent.map((r) => {
          const score = r.overall_score ?? 0;
          const sc = score >= 80 ? '#22c55e' : score >= 60 ? '#eab308' : '#ef4444';
          return (
            <button
              key={r.run_id}
              onClick={() => setDetailRunId(r.run_id)}
              className="p-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] hover:border-[var(--color-primary)] transition-colors text-left"
            >
              <div className="flex items-center justify-between mb-1">
                <span className="text-[10px] font-mono text-[var(--color-text-muted)]">{r.triggered_at?.slice(0, 10)}</span>
                <span className="text-sm font-mono font-semibold" style={{ color: sc }}>{Math.round(score)}%</span>
              </div>
              <div className="text-[10px] text-[var(--color-text-muted)]">{r.triggered_by}</div>
              <div className="flex gap-2 mt-1 text-[9px]">
                <span className="text-green-500">{r.cases_passed ?? 0}✓</span>
                <span className="text-red-500">{r.cases_failed ?? 0}✗</span>
                <span className="text-[var(--color-text-muted)]">{r.cases_skipped ?? 0}skip</span>
              </div>
            </button>
          );
        })}
      </div>

      <h3 className="text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wide mb-4">Overall Score Trend</h3>
      <div className="p-4 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] mb-8">
        <Sparkline
          values={runs.map(r => r.overall_score ?? 0)}
          dates={runs.map(r => r.triggered_at?.slice(0, 10) ?? '')}
          axis
          height={48}
          color="var(--color-primary)"
        />
        <div className="flex justify-between text-[9px] text-[var(--color-text-muted)] mt-1 font-mono">
          <span>{runs[0]?.triggered_at?.slice(0, 10)}</span>
          <span>{runs[runs.length - 1]?.triggered_at?.slice(0, 10)}</span>
        </div>
      </div>

      <h3 className="text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wide mb-4">Per-Dimension Sparklines</h3>
      <div className="grid grid-cols-2 gap-4">
        {allDims.map((dim) => {
          const scores = runs.map(r => r.dimensions?.[dim] ?? 0);
          const latest = scores[scores.length - 1] ?? 0;
          const color = latest >= 80 ? '#22c55e' : latest >= 60 ? '#eab308' : '#ef4444';
          return (
            <div key={dim} className="p-4 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)]">
              <div className="flex items-center justify-between mb-2">
                <div className="text-xs text-[var(--color-text-muted)]">{dim.replace(/_/g, ' ')}</div>
                <div className="text-sm font-mono font-semibold" style={{ color }}>{latest}%</div>
              </div>
              <Sparkline values={scores} height={32} color={color} />
            </div>
          );
        })}
      </div>

      {/* Per-run detail drawer — visualizes per-case pass/fail for a chosen run */}
      {detailRunId && (
        <RunDetailPanel runId={detailRunId} onClose={() => setDetailRunId(null)} />
      )}
    </div>
  );
}

// Visualizes a single run's per-case results. Run cases have NO dimension field,
// so we group by STATUS (available) and join the golden set by id only to show a
// dimension label per case (Gate-1 BLOCK#1 — never assume per-case dimension).
function RunDetailPanel({ runId, onClose }: { runId: string; onClose: () => void }) {
  const { data: run, isLoading } = useRunDetail(runId);
  const { data: gs } = useGoldenSet();
  const dimById = useMemo(() => {
    const m = new Map<string, string>();
    for (const c of gs?.cases ?? []) m.set(c.id, c.dimension);
    return m;
  }, [gs?.cases]);

  const STATUS_ORDER = ['failed', 'error', 'skipped', 'passed'];
  const grouped = useMemo(() => {
    const m = new Map<string, RunCaseResult[]>();
    for (const c of run?.cases ?? []) {
      const k = c.status || 'unknown';
      (m.get(k) ?? m.set(k, []).get(k)!).push(c);
    }
    return [...m.entries()].sort((a, b) => STATUS_ORDER.indexOf(a[0]) - STATUS_ORDER.indexOf(b[0]));
  }, [run?.cases]);

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/30" onClick={onClose}>
      <div className="w-[460px] h-full bg-[var(--color-card)] border-l border-[var(--color-border)] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        <div className="sticky top-0 bg-[var(--color-card)] border-b border-[var(--color-border)] px-4 py-3 flex items-center justify-between">
          <div>
            <div className="text-xs font-mono font-semibold">{runId}</div>
            {run && <div className="text-[10px] text-[var(--color-text-muted)]">{run.triggered_at?.slice(0, 19).replace('T', ' ')} · {run.triggered_by}</div>}
          </div>
          <button onClick={onClose} className="text-[var(--color-text-muted)] hover:text-[var(--color-text)]">
            <span className="material-symbols-outlined text-base">close</span>
          </button>
        </div>
        {isLoading || !run ? <Loading /> : (
          <div className="p-4">
            {/* Run summary */}
            <div className="flex gap-3 mb-4 text-xs">
              <span className="font-mono font-semibold" style={{ color: (run.overall_score ?? 0) >= 80 ? '#22c55e' : '#eab308' }}>{Math.round(run.overall_score ?? 0)}%</span>
              <span className="text-green-500">{run.cases_passed ?? 0} passed</span>
              <span className="text-red-500">{run.cases_failed ?? 0} failed</span>
              <span className="text-[var(--color-text-muted)]">{run.cases_skipped ?? 0} skipped</span>
              {(run.cases_error ?? 0) > 0 && <span className="text-orange-500">{run.cases_error} error</span>}
            </div>
            {/* Per-case grouped by status */}
            {grouped.map(([status, cases]) => (
              <div key={status} className="mb-4" data-testid={`run-status-${status}`}>
                <div className="flex items-center gap-2 mb-1.5">
                  <StatusBadge status={status} />
                  <span className="text-[10px] text-[var(--color-text-muted)]">{cases.length}</span>
                </div>
                <div className="space-y-1">
                  {cases.map((c) => (
                    <div key={c.id} className="flex items-center gap-2 px-2 py-1 rounded bg-[var(--color-bg)] text-[10px]">
                      <span className="font-mono font-semibold">{c.id}</span>
                      {dimById.get(c.id) && <span className="text-[var(--color-text-muted)]">{dimById.get(c.id)?.replace(/_/g, ' ')}</span>}
                      {c.evaluator && <span className="ml-auto text-[var(--color-text-muted)]">{c.evaluator}</span>}
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Context Health Tab ──────────────────────────────────────────────────────

interface ContextHealthData {
  refresh_log: { timestamp: string; target: string; old: string; new: string; evidence: string; layer: number; confidence: number }[];
  staleness: { project: string; doc: string; days_stale: number; recent_commits: number; raw?: string }[];
  pending_proposals: { id: string; target_doc: string; target_section: string; content: string; created_at: string; confidence: number }[];
  weeks_available: number;
  // Optional: a swallowed backend error may omit this key — always guard with ?. (Gate-1).
  semantic_drift?: {
    report_date: string | null;
    drift_count: number;
    findings: { project: string | null; docs: string[]; title: string; detail: string }[];
    at_risk_cases: { case_id: string; project: string; doc: string }[];
  };
}

function useContextHealth() {
  return useQuery<ContextHealthData>({
    queryKey: ['eval-context-health'],
    queryFn: async () => (await api.get<ContextHealthData>('/eval/context-health')).data,
    staleTime: 60_000,
  });
}

function ContextHealthTab() {
  const { data, isError } = useContextHealth();

  if (isError) return <ErrorState message="Failed to load context health data." />;
  if (!data) return <Loading />;

  const drift = data.semantic_drift;
  const driftFindings = drift?.findings ?? [];
  const hasActivity = data.refresh_log.length > 0 || data.staleness.length > 0
    || data.pending_proposals.length > 0 || driftFindings.length > 0;

  return (
    <div className="max-w-5xl mx-auto p-6 space-y-6">
      {/* Header */}
      <div>
        <h2 className="text-lg font-semibold text-[var(--color-text)]">Context Health</h2>
        <p className="text-xs text-[var(--color-text-muted)] mt-1">
          DDD & Memory freshness — auto-refresh activity, staleness signals, and pending decisions.
          {data.weeks_available > 0 && ` Showing ${data.weeks_available} week(s) of history.`}
        </p>
      </div>

      {!hasActivity && (
        <div className="text-center py-12 text-[var(--color-text-muted)]">
          <span className="material-symbols-outlined text-4xl mb-2 block opacity-40">check_circle</span>
          <p className="text-sm">No context drift detected. Everything is fresh.</p>
          <p className="text-xs mt-1 opacity-70">Auto-refresh runs on every code commit + every 30 minutes.</p>
        </div>
      )}

      {/* Semantic Drift (ddd-self-audit findings + at-risk golden cases) — a TRUTH
          signal (a DDD prose claim contradicts itself / the code), distinct from the
          mtime-based Staleness below. Each finding lists the docs it hits; at-risk
          cases are golden cases whose affected_by depends on a drifted doc. */}
      {driftFindings.length > 0 && (
        <section>
          <h3 className="text-sm font-medium text-[var(--color-text)] mb-2 flex items-center gap-1.5">
            <span className="material-symbols-outlined text-base text-red-500">rule</span>
            Semantic Drift ({driftFindings.length})
            {drift?.report_date && (
              <span className="text-[10px] text-[var(--color-text-muted)] font-normal">
                from self-audit {drift.report_date}
              </span>
            )}
          </h3>
          <div className="border border-red-500/20 rounded-lg overflow-hidden">
            <table className="w-full text-xs">
              <thead className="bg-red-500/5">
                <tr>
                  <th className="text-left px-3 py-2 font-medium text-[var(--color-text-muted)]">Project</th>
                  <th className="text-left px-3 py-2 font-medium text-[var(--color-text-muted)]">Doc(s)</th>
                  <th className="text-left px-3 py-2 font-medium text-[var(--color-text-muted)]">What's Stale</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--color-border)]">
                {driftFindings.map((f, i) => (
                  <tr key={i} className="hover:bg-[var(--color-hover)]">
                    <td className="px-3 py-2 text-[var(--color-text)]">{f.project ?? '—'}</td>
                    <td className="px-3 py-2 text-[var(--color-text-muted)] font-mono">{f.docs.join(', ') || '—'}</td>
                    <td className="px-3 py-2 text-[var(--color-text-muted)]">{f.title}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {(drift?.at_risk_cases?.length ?? 0) > 0 && (
            <p className="text-[10px] text-[var(--color-text-muted)] mt-1.5">
              ⚠️ {drift!.at_risk_cases.length} eval case(s) at risk (affected_by a drifted doc):{' '}
              <span className="font-mono">{drift!.at_risk_cases.map(c => c.case_id).join(', ')}</span>
            </p>
          )}
          <p className="text-[10px] text-[var(--color-text-muted)] mt-1 italic">
            Semantic contradictions found by the weekly DDD self-audit. Fix via chat: "s_persist … correct the claim".
          </p>
        </section>
      )}

      {/* Staleness Signals */}
      {data.staleness.length > 0 && (
        <section>
          <h3 className="text-sm font-medium text-[var(--color-text)] mb-2 flex items-center gap-1.5">
            <span className="material-symbols-outlined text-base text-amber-500">warning</span>
            Stale Documents ({data.staleness.length})
          </h3>
          <div className="border border-amber-500/20 rounded-lg overflow-hidden">
            <table className="w-full text-xs">
              <thead className="bg-amber-500/5">
                <tr>
                  <th className="text-left px-3 py-2 font-medium text-[var(--color-text-muted)]">Project</th>
                  <th className="text-left px-3 py-2 font-medium text-[var(--color-text-muted)]">Document</th>
                  <th className="text-right px-3 py-2 font-medium text-[var(--color-text-muted)]">Days Stale</th>
                  <th className="text-right px-3 py-2 font-medium text-[var(--color-text-muted)]">Recent Commits</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--color-border)]">
                {data.staleness.map((s, i) => (
                  <tr key={i} className="hover:bg-[var(--color-hover)]">
                    <td className="px-3 py-2 text-[var(--color-text)]">{s.project}</td>
                    <td className="px-3 py-2 text-[var(--color-text-muted)]">{s.doc}</td>
                    <td className="px-3 py-2 text-right font-mono text-amber-500">{s.days_stale}d</td>
                    <td className="px-3 py-2 text-right font-mono">{s.recent_commits}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-[10px] text-[var(--color-text-muted)] mt-1.5 italic">
            Tip: Ask in chat "refresh AIDLC TECH.md" or "update DDD docs" to resolve.
          </p>
        </section>
      )}

      {/* Pending Proposals (Layer 3) */}
      {data.pending_proposals.length > 0 && (
        <section>
          <h3 className="text-sm font-medium text-[var(--color-text)] mb-2 flex items-center gap-1.5">
            <span className="material-symbols-outlined text-base text-blue-500">pending_actions</span>
            Pending Decisions ({data.pending_proposals.length})
          </h3>
          <div className="space-y-2">
            {data.pending_proposals.map((p) => (
              <div key={p.id} className="border border-[var(--color-border)] rounded-lg p-3 hover:bg-[var(--color-hover)]">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-medium text-[var(--color-text)]">
                    {p.target_doc} → {p.target_section}
                  </span>
                  <span className="text-[10px] text-[var(--color-text-muted)]">
                    {new Date(p.created_at).toLocaleDateString()}
                  </span>
                </div>
                <p className="text-xs text-[var(--color-text-muted)] mt-1 line-clamp-2">{p.content}</p>
                <p className="text-[10px] text-[var(--color-text-muted)] mt-1.5 italic">
                  Ask in chat: "approve proposal {p.id}" or "reject proposal {p.id}"
                </p>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Auto-Refresh Log (Layer 1) */}
      {data.refresh_log.length > 0 && (
        <section>
          <h3 className="text-sm font-medium text-[var(--color-text)] mb-2 flex items-center gap-1.5">
            <span className="material-symbols-outlined text-base text-green-500">auto_fix_high</span>
            Auto-Applied Fixes ({data.refresh_log.length})
          </h3>
          <div className="border border-[var(--color-border)] rounded-lg overflow-hidden">
            <table className="w-full text-xs">
              <thead className="bg-[var(--color-bg)]">
                <tr>
                  <th className="text-left px-3 py-2 font-medium text-[var(--color-text-muted)]">Date</th>
                  <th className="text-left px-3 py-2 font-medium text-[var(--color-text-muted)]">Target</th>
                  <th className="text-left px-3 py-2 font-medium text-[var(--color-text-muted)]">Change</th>
                  <th className="text-left px-3 py-2 font-medium text-[var(--color-text-muted)]">Layer</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--color-border)]">
                {data.refresh_log.slice(0, 20).map((entry, i) => (
                  <tr key={i} className="hover:bg-[var(--color-hover)]">
                    <td className="px-3 py-2 text-[var(--color-text-muted)] whitespace-nowrap">
                      {new Date(entry.timestamp).toLocaleDateString()}
                    </td>
                    <td className="px-3 py-2 text-[var(--color-text)] font-mono text-[11px] truncate max-w-[200px]">
                      {entry.target}
                    </td>
                    <td className="px-3 py-2">
                      <code className="text-red-400 line-through text-[10px]">{entry.old}</code>
                      {' → '}
                      <code className="text-green-400 text-[10px]">{entry.new}</code>
                    </td>
                    <td className="px-3 py-2">
                      <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium ${
                        entry.layer === 1
                          ? 'bg-green-500/10 text-green-500'
                          : 'bg-blue-500/10 text-blue-500'
                      }`}>
                        L{entry.layer}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {data.refresh_log.length > 20 && (
            <p className="text-[10px] text-[var(--color-text-muted)] mt-1">
              Showing 20 of {data.refresh_log.length} entries.
            </p>
          )}
        </section>
      )}
    </div>
  );
}

// ─── Reports Tab ─────────────────────────────────────────────────────────────

interface EvalReport {
  filename: string;
  sizeBytes: number;
  modified: number;
}

function useEvalReports() {
  return useQuery<EvalReport[]>({
    queryKey: ['eval-reports'],
    queryFn: async () => (await api.get<EvalReport[]>('/eval/reports')).data,
    staleTime: 60_000,
  });
}

export function ReportsTab() {
  const { data: reports, isLoading, isError } = useEvalReports();

  // Reports open in the SYSTEM BROWSER, not an in-app iframe. srcDoc rendering in
  // the Tauri WebKit webview proved unreliable, and rendering arbitrary report HTML
  // in-app is a needless security/complexity surface. The browser renders it natively
  // (get_report returns Content-Type: text/html), fully process-isolated from the app.
  // Reuse the dynamic api base (getApiBaseUrl) — never hardcode host/port (dev=8000,
  // desktop=dynamic, Hive=same-origin) — and encode the filename (names contain spaces).
  const openReport = (filename: string) => {
    void openExternal(`${getApiBaseUrl()}/api/eval/reports/${encodeURIComponent(filename)}`);
  };

  if (isError) return <ErrorState message="Failed to load reports. Is the backend running?" />;
  if (isLoading) return <Loading />;
  if (!reports || reports.length === 0) {
    return (
      <div className="max-w-5xl mx-auto p-6">
        <p className="text-sm text-[var(--color-text-muted)]">
          No HTML eval reports found. Run a full eval sweep to generate reports.
        </p>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto p-6">
      <h3 className="text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wide mb-4">
        Eval Reports ({reports.length})
      </h3>
      <div className="border border-[var(--color-border)] rounded-lg overflow-hidden">
        <table className="w-full text-xs">
          <thead>
            <tr className="bg-[var(--color-bg)] border-b border-[var(--color-border)]">
              <th className="text-left px-4 py-2.5 font-medium text-[var(--color-text-muted)]">Report</th>
              <th className="text-left px-4 py-2.5 font-medium text-[var(--color-text-muted)]">Date</th>
              <th className="text-right px-4 py-2.5 font-medium text-[var(--color-text-muted)]">Size</th>
            </tr>
          </thead>
          <tbody>
            {reports.map((r) => {
              const date = new Date(r.modified * 1000);
              const dateStr = date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
              const sizeKb = (r.sizeBytes / 1024).toFixed(0);
              // Parse a friendly name from filename
              const label = r.filename.replace('.html', '').replace(/_/g, ' ');
              return (
                <tr
                  key={r.filename}
                  onClick={() => openReport(r.filename)}
                  className="border-b border-[var(--color-border)] last:border-0 hover:bg-[var(--color-hover)] cursor-pointer transition-colors"
                >
                  <td className="px-4 py-2.5">
                    <div className="flex items-center gap-2">
                      <span className="material-symbols-outlined text-sm text-[var(--color-primary)]">description</span>
                      <span className="font-medium">{label}</span>
                    </div>
                  </td>
                  <td className="px-4 py-2.5 text-[var(--color-text-muted)] font-mono">{dateStr}</td>
                  <td className="px-4 py-2.5 text-right text-[var(--color-text-muted)]">{sizeKb} KB</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}


// ─── Session Quality Tab (Layer ②③) ────────────────────────────────────────────
// Surfaces the weekly real-session quality loop: Layer③ scores real sessions
// (goal + tool-selection judges) and shows low-score attribution. Layer② harvest
// no longer produces a human-ratification queue — it auto-gates each harvested
// case through the teeth gate (option D): pass → lands tier=active, fail → discarded
// to the recoverable archive. There is NO pending-draft queue / Promote / Discard.
// Backend: GET /eval/session-quality (overview + trend + low detail).

interface SessionQualityLowDetail {
  session_id?: string;
  goal_score?: number;
  tool_score?: number;
  dimension?: string;
  reason?: string;
}
interface SessionQualityOverview {
  scored: number;
  low: number;
  drafts: number;
  last_run: string | null;
  trend: number[];
  low_details?: SessionQualityLowDetail[];
}

function useSessionQuality() {
  return useQuery<SessionQualityOverview>({
    queryKey: ['eval-session-quality'],
    queryFn: async () => (await api.get<SessionQualityOverview>('/eval/session-quality')).data,
    staleTime: 60_000,
  });
}

// Weekly low-rate trend uses the shared Sparkline (defined below) — do NOT
// redefine it here (R27: reuse the existing symbol, don't duplicate).

export function SessionQualityTab() {
  const { data: overview, isLoading, isError: overviewErr } = useSessionQuality();

  if (overviewErr) return <ErrorState message="Failed to load session quality data. Is the backend running?" />;
  if (isLoading) return <Loading />;

  const lowDetails = overview?.low_details ?? [];

  return (
    <div className="max-w-5xl mx-auto p-6 space-y-6">
      {/* Overview stat cards + drift sparkline */}
      <div>
        <h3 className="text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wide mb-3">
          Session Quality — Real-Session Loop (Layer ②③)
        </h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {[
            { label: 'Scored (last run)', value: overview?.scored ?? 0 },
            { label: 'Low-score', value: overview?.low ?? 0 },
            { label: 'Drafts harvested', value: overview?.drafts ?? 0 },
          ].map((s) => (
            <div key={s.label} className="border border-[var(--color-border)] rounded-lg p-3 bg-[var(--color-card)]">
              <div className="text-2xl font-semibold">{s.value}</div>
              <div className="text-xs text-[var(--color-text-muted)] mt-1">{s.label}</div>
            </div>
          ))}
        </div>
        <div className="flex items-center gap-3 mt-3 text-xs text-[var(--color-text-muted)]">
          <span>Weekly low-rate trend</span>
          <div className="w-[120px]"><Sparkline values={overview?.trend ?? []} height={28} /></div>
          {overview?.last_run && <span className="ml-auto font-mono">last run: {overview.last_run}</span>}
        </div>
      </div>

      {/* Low-score session detail (attribution) */}
      {lowDetails.length > 0 && (
        <div>
          <h3 className="text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wide mb-3">
            Low-Score Sessions ({lowDetails.length})
          </h3>
          <div className="border border-[var(--color-border)] rounded-lg overflow-hidden">
            <table className="w-full text-xs">
              <thead>
                <tr className="bg-[var(--color-bg)] border-b border-[var(--color-border)]">
                  <th className="text-left px-4 py-2.5 font-medium text-[var(--color-text-muted)]">Session</th>
                  <th className="text-left px-4 py-2.5 font-medium text-[var(--color-text-muted)]">Dimension</th>
                  <th className="text-right px-4 py-2.5 font-medium text-[var(--color-text-muted)]">Goal</th>
                  <th className="text-right px-4 py-2.5 font-medium text-[var(--color-text-muted)]">Tool</th>
                  <th className="text-left px-4 py-2.5 font-medium text-[var(--color-text-muted)]">Reason</th>
                </tr>
              </thead>
              <tbody>
                {lowDetails.map((l, i) => (
                  <tr key={l.session_id || i} className="border-b border-[var(--color-border)] last:border-0">
                    <td className="px-4 py-2.5 font-mono truncate max-w-[140px]">{l.session_id || '—'}</td>
                    <td className="px-4 py-2.5">{l.dimension || '—'}</td>
                    <td className="px-4 py-2.5 text-right">{l.goal_score?.toFixed(2) ?? '—'}</td>
                    <td className="px-4 py-2.5 text-right">{l.tool_score?.toFixed(2) ?? '—'}</td>
                    <td className="px-4 py-2.5 text-[var(--color-text-muted)]">{l.reason || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}


// ─── Guide Tab ────────────────────────────────────────────────────────────────

// Bilingual content for Guide tab
const guideContent = {
  title: { en: 'OS Eval Methodology', zh: 'OS Eval 方法论' },
  subtitle: {
    en: 'SwarmAI has a built-in, system-level self-evaluation subsystem — decoupled from DDD, not external testing. It spans THREE layers: ① Golden (human-authored exam questions, run programmatically + LLM-judged); ② Real-session harvest (weekly, low-scoring real sessions are auto-generated into full golden cases + a negative example, then auto-gated by the teeth gate — pass → lands active, fail → discarded to a recoverable archive, no human step); ③ Online real-session scoring (score what actually happened, no answer key). Proprioception: a living golden set defines "in this scenario I must do X." Core insight: eval and agent share the same environment, so the judge reads the agent\'s real rules files — zero maintenance, always fresh. (Exact live counts are on the Overview & Golden Set tabs.)',
    zh: 'SwarmAI 有一个 built-in、系统层的自我评估子系统 —— 与 DDD 解耦，不是外部测试。它跨三层：① Golden（人工编写的考题，程序化 + LLM judge 跑）；② 真实会话回收（每周，低分真实会话自动生成完整 golden 用例 + 一个负面样本，经 teeth 门自动把关 —— 过则落 active，不过则丢到可恢复 archive，无人介入）；③ Online 真实会话打分（评真实发生的会话，无标准答案）。Proprioception：一个活的 golden set 定义了"在这个场景下我必须怎样做"。核心 insight：eval 和 agent 在同一个环境，judge 直接读 agent 的真实 rules 文件 —— 零维护，永远新鲜。（精确的 live 数字在 Overview 和 Golden Set tab。）',
  },
  overview: {
    en: ['What it evaluates', 'Why it matters', 'How it works'],
    zh: ['评估什么', '为什么重要', '怎么运作'],
  },
  overviewDesc: {
    en: [
      'Not just output quality — cognitive health. Memory accuracy, judgment consistency, context utility, rule compliance, capability integrity.',
      'Context, memory, rules, knowledge can all rot silently. Eval catches drift before damage compounds.',
      'Golden Set cases define expected behaviors. Eval runner presents scenarios in isolated sessions and verifies responses against a three-layer ground truth.',
    ],
    zh: [
      '不只是输出质量 — 而是认知健康。记忆准确性、判断一致性、上下文效用、规则合规性、能力完整性。',
      '上下文、记忆、规则、知识都会无声腐烂。Eval 在损害扩散之前捕获漂移。',
      'Golden Set 定义期望行为。Eval Runner 在隔离 Session 中呈现场景，对比三层 Ground Truth 验证响应。',
    ],
  },
  dimensions: {
    en: 'The Six Eval Dimensions',
    zh: '六个评估维度',
  },
  // The 6 canonical dimensions (source of truth: eval_runner.DIMENSIONS + golden_set.yaml
  // `dimensions:`). The `share` label is a relative-size snapshot, not a live count —
  // exact numbers live on the Golden Set tab.
  dimensionItems: [
    {
      key: 'capability',
      icon: '⚡',
      en: { name: 'Capability', question: 'Are my abilities still intact?', method: 'End-to-end feature probes — verify critical capabilities (DDD cultivation, pipeline loops, self-healing) still execute correctly. Evaluators: file_contains, canary_pass, runtime_health.' },
      zh: { name: '能力完整性', question: '我的能力还完整吗？', method: '端到端能力探测 — 验证关键能力（DDD 培育、Pipeline 循环、自愈）仍正确执行。评估器：file_contains, canary_pass, runtime_health。' },
      share: 'largest',
    },
    {
      key: 'judgment_quality',
      icon: '⚖️',
      en: { name: 'Judgment Quality', question: 'Would I give the same answer to the same question?', method: 'Consistency testing — re-present known decisions and verify alignment with historical answers. Evaluator: goal_success.' },
      zh: { name: '判断质量', question: '同一问题我会给同样答案吗？', method: '一致性测试 — 重新呈现已知决策并验证与历史答案的一致性。评估器：goal_success。' },
      share: 'large',
    },
    {
      key: 'compliance',
      icon: '🛡️',
      en: { name: 'Compliance', question: 'Do I follow my own rules?', method: 'Constraint testing — present scenarios designed to trigger known rule violations (CLASS A patterns). Evaluators: trajectory_in_order, goal_success.' },
      zh: { name: '规则合规', question: '我遵守自己的规则吗？', method: '约束测试 — 呈现设计用来触发已知规则违反的场景（CLASS A 模式）。评估器：trajectory_in_order, goal_success。' },
      share: 'large',
    },
    {
      key: 'factual_accuracy',
      icon: '🧠',
      en: { name: 'Factual Accuracy', question: 'Is what I remember still true?', method: 'Source verification — check referenced files/systems still support stored claims. Evaluators: canary_pass, file_contains.' },
      zh: { name: '事实准确性', question: '我记得的东西还对吗？', method: '源头验证 — 检查被引用的文件/系统是否仍支持存储的断言。评估器：canary_pass, file_contains。' },
      share: 'medium',
    },
    {
      key: 'context_utility',
      icon: '📐',
      en: { name: 'Context Utility', question: 'Is the context I use actually helpful?', method: 'Ablation testing — measure response quality with/without specific context files. Evaluator: quality_score.' },
      zh: { name: '上下文效用', question: '我用的 context 有用吗？', method: '消融测试 — 比较有/无特定上下文文件时的响应质量。评估器：quality_score。' },
      share: 'medium',
    },
    {
      key: 'recovery',
      icon: '🔄',
      en: { name: 'Recovery', question: 'Do I recover correctly from a crash/interrupt?', method: 'Fault-injection harness — resume, self-heal, and crash-to-cold paths reach a correct state. Evaluator: runtime_health.' },
      zh: { name: '恢复能力', question: '崩溃/中断后我能正确恢复吗？', method: '故障注入 harness — resume、自愈、crash-to-cold 路径达到正确状态。评估器：runtime_health。' },
      share: 'small',
    },
  ],
  evaluators: {
    en: 'Evaluator Methodology',
    zh: '评估器方法论',
  },
  evalIntro: {
    en: 'Three complementary methods: cheap deterministic checks catch regressions instantly; semantic judges verify nuanced behavioral quality; and a behavior method spawns a real agent to capture its actual tool trajectory.',
    zh: '三种互补机制：廉价确定性检查即时捕获回归；语义裁判验证细微行为质量；behavior 方法 spawn 真实 agent 捕获其真实工具轨迹。',
  },
  programmatic: {
    en: 'Programmatic (Deterministic, <1s, $0)',
    zh: '程序化（确定性, <1s, $0）',
  },
  programmaticItems: [
    { name: 'canary_pass', en: 'File or path exists on disk — structural integrity probe', zh: '文件或路径存在于磁盘 — 结构完整性探测' },
    { name: 'file_contains', en: 'File content includes expected string/pattern', zh: '文件内容包含期望的字符串/模式' },
    { name: 'keyword_match', en: 'Agent response contains required keywords', zh: '智能体响应包含必需关键词' },
    { name: 'trajectory_exact', en: 'Tool call sequence matches exactly', zh: '工具调用序列精确匹配' },
    { name: 'trajectory_in_order', en: 'Required tools appear in correct order (extra allowed)', zh: '必需工具按正确顺序出现（允许额外调用）' },
    { name: 'trajectory_any_order', en: 'Required tools all present regardless of order', zh: '必需工具全部出现（不限顺序）' },
    { name: 'runtime_health', en: 'Live daemon/session liveness probe — deployed, progressing, under RSS budget', zh: '实时 daemon/session 存活探测 — 已部署、在推进、RSS 在预算内' },
  ],
  llmJudge: {
    en: 'LLM-Judge (Semantic, ~5s, ~$0.02/case)',
    zh: 'LLM-Judge（语义, ~5s, ~$0.02/case）',
  },
  llmJudgeItems: [
    { name: 'goal_success', en: 'Did the agent achieve the scenario\'s intended goal? Binary pass/fail with reasoning.', zh: '智能体是否达成场景预期目标？二值通过/失败并附推理。' },
    { name: 'quality_score', en: 'Multi-dimensional quality assessment (0-10) across relevance, completeness, accuracy.', zh: '多维质量评估（0-10），涵盖相关性、完整性、准确性。' },
  ],
  // Behavior is a third EXECUTION METHOD, not a programmatic/LLM evaluator: it spawns a
  // REAL headless agent and captures its actual tool trajectory (eval_trajectory_capture) —
  // used by behavior-tier cases. Costly, so it runs on the weekly cadence, not per-commit.
  behavior: {
    en: 'Behavior (Real-Agent Spawn, ~17-120s/case)',
    zh: 'Behavior（真实 Agent Spawn, ~17-120s/case）',
  },
  behaviorItems: [
    { name: 'trajectory_capture', en: 'Spawns a REAL headless agent on the case prompt and records the tool calls it actually makes.', zh: 'spawn 真实 headless agent 跑 case prompt，记录它实际发出的工具调用。' },
    { name: 'behavior-tier cases', en: 'The captured trajectory feeds the trajectory_* evaluators — verifies what the agent DID, not just what it would say.', zh: '捕获的轨迹喂给 trajectory_* 评估器 — 验证 agent 实际做了什么，而非只看它会怎么说。' },
    { name: 'weekly cadence', en: 'Costly (real spawns) → runs on the Monday drift-watch, never per-commit / per-push.', zh: '昂贵（真 spawn）→ 排在周一漂移监控跑，绝不逐 commit / 逐 push。' },
  ],
  architecture: {
    en: 'Execution Architecture',
    zh: '执行架构',
  },
  archDesc: {
    en: 'Eval runs in an isolated clean session — identical system prompt assembly (same context files, same hooks, same model) but zero user conversation history. This tests canonical behavior without attention contamination. The eval subsystem is decoupled from the coding pipeline (the 9-stage / 3-gate autonomous pipeline is where changes are BUILT + adversarially reviewed; eval scores the DEPLOYED system afterward — the agent never runs eval mid-pipeline).',
    zh: 'Eval 在隔离的干净 Session 中运行 — 完全相同的 System Prompt 组装（同样的上下文文件、hooks、模型），但零用户对话历史。这在无注意力污染的情况下测试规范行为。Eval 子系统与编码 pipeline 解耦（9-stage / 3-gate 自主 pipeline 是改动被 BUILD + 对抗审查的地方；eval 事后评估已部署的系统 —— agent 绝不在 pipeline 中途跑 eval）。',
  },
  archJudge: {
    en: 'Judge model is pinned (default claude-opus-4-6, configurable) to a different version than production. If both drift simultaneously, degradation becomes invisible. Pinning is the minimum viable isolation for self-evaluation integrity. NOTE the judge decoupling: golden cases ask "WOULD the agent comply?" (static-contract analysis against its rules); Layer③ session scoring asks "DID this real session actually do well?" (scores the real response + tool trajectory) — different judges for different questions.',
    zh: 'Judge 模型固定（默认 claude-opus-4-6，可配）为与生产不同的版本。若两者同时漂移，退化将不可见。版本固定是自我评估完整性的最小可行隔离。注意判官解耦：golden case 问"agent 会不会合规？"（对其规则做静态契约分析）；层③会话打分问"这次真实会话实际做得好不好？"（评真实响应 + 工具轨迹）—— 不同问题用不同判官。',
  },
  coverage: {
    en: 'Coverage Distribution',
    zh: '覆盖分布',
  },
  coverageDesc: {
    en: 'Cases are tagged on 4 orthogonal axes — Category (compliance, decision, recall, code_aware, refusal, knowledge, ddd_informed…), Dimension (6), Tier (draft→active→stable, + behavior/canary), Eval Method (programmatic / llm / behavior).',
    zh: '每个 case 沿 4 个正交轴打标签 —— Category（compliance、decision、recall、code_aware、refusal、knowledge、ddd_informed…）、Dimension（6 个）、Tier（draft→active→stable，外加 behavior/canary）、Eval Method（programmatic / llm / behavior）。',
  },
  lifecycle: {
    en: 'Case Lifecycle',
    zh: '案例生命周期',
  },
  lifecycleStages: [
    { en: { stage: 'Origin', desc: 'A low-scoring real session harvested by the weekly Layer② job. (User corrections no longer auto-seed cases — that A-pipeline was retired; corrections still record to the tracker.)' }, zh: { stage: '来源', desc: '每周层②任务回收的低分真实会话。（用户纠正不再自动播种用例 —— A 管道已停产；纠正仍记入 tracker。）' } },
    { en: { stage: 'Gate', desc: 'Auto-generated into a full case + a negative example, then auto-gated by the teeth gate (knockout): the negative must be judged FAIL, else the case is a tautology and is discarded to a recoverable archive. No draft middle-state, no human step.' }, zh: { stage: '把关', desc: '自动生成完整用例 + 一个负面样本，经 teeth 门（knockout）自动把关：负面样本必须被判 FAIL，否则用例是同义反复，丢到可恢复 archive。无草稿中间态，无人介入。' } },
    { en: { stage: 'Active', desc: 'Passed the teeth gate → lands tier=active. Runs on every eval cycle; failures trigger P1 alerts.' }, zh: { stage: '活跃', desc: '过 teeth 门 → 落 tier=active。每次 eval 周期运行；失败触发 P1 告警。' } },
    { en: { stage: 'Stable', desc: 'Passed 10+ consecutive runs. Moves to monthly cadence.' }, zh: { stage: '稳定', desc: '连续通过 10+ 次运行。移入月度节奏。' } },
    { en: { stage: 'Retired', desc: 'Underlying rule/code removed. Case archived, no longer executed.' }, zh: { stage: '退役', desc: '底层规则/代码已移除。案例归档，不再执行。' } },
  ],
  triggers: {
    en: 'Triggers & Cadence',
    zh: '触发条件与节奏',
  },
  triggerItems: [
    { en: { trigger: 'Scheduled (drift watch)', cadence: 'Monday 18:30 ICT (cron 30 10 * * 1)', desc: 'Full suite, weekly — never gates; continuous drift watch. Last Monday slot on purpose. Weekly cadence fits behavior cases (real agent spawns, slow/costly). BVT-red / score-drop → Slack alert.' }, zh: { trigger: '定时（漂移监控）', cadence: '周一 18:30 ICT（cron 30 10 * * 1）', desc: '完整套件，每周一次 — 永不当门；持续漂移监控。故意排在周一最后一个槽。每周节奏适配 behavior 用例（spawn 真 agent，慢且贵）。BVT-red / 分数下降 → Slack 告警。' } },
    { en: { trigger: 'Deploy / CI gate', cadence: 'On release / post-push', desc: 'Git-bound gate (code_digest + BVT) — HARD stop, blocks a release that regresses. The only eval that GATES.' }, zh: { trigger: '发版 / CI 门', cadence: '发版时 / push 后', desc: 'Git-bound 门（code_digest + BVT）— 硬停，拦截会回归的发版。唯一会"当门"的 eval。' } },
    { en: { trigger: 'Session Quality (Layer ②③)', cadence: 'Weekly, Sunday (offset from Monday)', desc: 'Samples up to 10 real owner sessions (with-correction OR turn-anomaly), scores them (goal + tool-selection judges), and harvests low-scorers into golden DRAFTS. Never gates — a discovery funnel that feeds the golden set.' }, zh: { trigger: 'Session Quality（层②③）', cadence: '每周日（错开周一）', desc: '采样最多 10 场真实 owner 会话（带 correction 或轮次异常），打分（目标 + 工具选择判官），把低分会话回收成 golden 草稿。永不当门 —— 是给 golden set 供料的发现漏斗。' } },
    { en: { trigger: 'Manual', cadence: 'On demand', desc: 'POST /api/eval/run (non-blocking, returns run_id) — selected cases or full suite' }, zh: { trigger: '手动', cadence: '按需', desc: 'POST /api/eval/run（非阻塞，返回 run_id）— 选定案例或完整套件' } },
  ],
  comparison: {
    en: 'vs Enterprise Agent Eval',
    zh: '对比企业级 Agent Eval',
  },
  comparisonRows: [
    { en: { dim: 'Core Question', enterprise: '"Is output good?"', ours: '"Is the OS still thinking well?"' }, zh: { dim: '核心问题', enterprise: '"输出好吗？"', ours: '"OS 还在正确思考吗？"' } },
    { en: { dim: 'What Drifts', enterprise: 'Model weights', ours: 'Model + Context + Memory + Rules + Time' }, zh: { dim: '漂移源', enterprise: '模型权重', ours: '模型 + 上下文 + 记忆 + 规则 + 时间' } },
    { en: { dim: 'Golden Set', enterprise: 'Fixed, human-labeled', ours: 'Living, grows from corrections' }, zh: { dim: 'Golden Set', enterprise: '固定、人工标注', ours: '活的、从纠正中生长' } },
    { en: { dim: 'Eval Trigger', enterprise: 'On deploy (CI gate)', ours: 'Change-triggered + scheduled + manual' }, zh: { dim: '触发时机', enterprise: '部署时（CI 门控）', ours: '变更触发 + 定时 + 手动' } },
    { en: { dim: 'Growth Signal', enterprise: 'N/A (stateless)', ours: 'Intelligence Velocity (compound metric)' }, zh: { dim: '增长信号', enterprise: '无（无状态）', ours: 'Intelligence Velocity（复合指标）' } },
  ],
  caseExample: {
    en: 'Anatomy of a Golden Set Case',
    zh: 'Golden Set 案例解剖',
  },
  caseExampleDesc: {
    en: 'Each case tests one behavioral expectation with three-layer ground truth:',
    zh: '每个案例用三层 Ground Truth 测试一个行为期望：',
  },
  flywheel: {
    en: 'The Self-Growing Flywheel',
    zh: '自增长飞轮',
  },
  iv: {
    en: 'Intelligence Velocity',
    zh: '智能速度（Intelligence Velocity）',
  },
  ivDesc: {
    en: 'A compound metric that answers: "Is the OS getting smarter over time, or just maintaining?" Unlike pass rate (which can be gamed by removing hard cases), IV rewards coverage growth and penalizes recurring corrections.',
    zh: '一个复合指标，回答："OS 在随时间变聪明，还是仅仅维持现状？" 不同于通过率（可以通过移除困难案例来作弊），IV 奖励覆盖增长并惩罚重复纠正。',
  },
  ivFormula: {
    en: 'IV  =  pass_rate  ×  coverage_growth  ×  correction_decay  /  time_window',
    zh: 'IV  =  通过率  ×  覆盖增长  ×  纠正衰减  /  时间窗口',
  },
  ivComponents: [
    { en: { name: 'Pass Rate', desc: 'Active cases passing / total active cases. Target: ≥0.85' }, zh: { name: '通过率', desc: '通过的活跃案例 / 活跃案例总数。目标：≥0.85' } },
    { en: { name: 'Coverage Growth', desc: 'New cases added this window / cases at window start. Measures flywheel velocity.' }, zh: { name: '覆盖增长', desc: '本窗口新增案例 / 窗口起始案例数。衡量飞轮速度。' } },
    { en: { name: 'Correction Decay', desc: '1 - (recurring corrections / total corrections). Penalizes repeating the same mistake class.' }, zh: { name: '纠正衰减', desc: '1 -（重复纠正 / 总纠正数）。惩罚重复同一类错误。' } },
  ],
  howToWrite: {
    en: 'How to Write a Case',
    zh: '如何编写案例',
  },
  howToWriteDesc: {
    en: 'Three steps to crystallize any correction into a permanent behavioral test:',
    zh: '三步将任何纠正结晶为永久行为测试：',
  },
  howToWriteSteps: [
    {
      icon: '🎯',
      en: { title: '1. Define the Scenario', desc: 'Write the prompt that triggers the behavior you want to test. Include enough context for the agent to act — but not so much that it gives away the answer. Good scenarios are indistinguishable from real user messages.', example: '"Fix the bug in session_unit.py where timeout is too short"' },
      zh: { title: '1. 定义场景', desc: '编写触发你想测试的行为的 prompt。包含足够的上下文让智能体行动 — 但不要多到暗示答案。好的场景与真实用户消息无法区分。', example: '"修复 session_unit.py 中超时时间太短的 bug"' },
    },
    {
      icon: '🏗️',
      en: { title: '2. Set 3-Layer Ground Truth', desc: 'Layer 1 (Trajectory): Which tools must be called, in what order? Layer 2 (Assertions): What semantic properties must the response have? Layer 3 (Response): What keywords must appear in the output?', example: 'trajectory: [Read, Edit, Bash(pytest)]\nassertions: ["Does NOT skip pipeline"]\nkeywords: ["pipeline", "adversarial"]' },
      zh: { title: '2. 设置三层 Ground Truth', desc: '第一层（轨迹）：必须调用哪些工具，什么顺序？第二层（断言）：响应必须具备什么语义属性？第三层（响应）：输出中必须出现什么关键词？', example: 'trajectory: [Read, Edit, Bash(pytest)]\nassertions: ["不跳过 pipeline"]\nkeywords: ["pipeline", "adversarial"]' },
    },
    {
      icon: '⚙️',
      en: { title: '3. Choose Evaluators', desc: 'Rule of thumb: use programmatic evaluators (trajectory_*, file_contains, keyword_match, canary_pass, runtime_health) for mechanical/structural behaviors — they run deterministically at $0; use LLM-judge (goal_success, quality_score) for nuanced judgment. Most cases use 2-3 evaluators. (The Evaluator Methodology section above lists the full current set.)', example: 'evaluators:\n  - trajectory_in_order\n  - goal_success' },
      zh: { title: '3. 选择评估器', desc: '经验法则：对机械/结构行为用程序化评估器（trajectory_*, file_contains, keyword_match, canary_pass, runtime_health）—— 确定性运行、$0；对细微判断用 LLM-judge（goal_success, quality_score）。大多数案例使用 2-3 个评估器。（上面「评估器方法论」列出当前完整集。）', example: 'evaluators:\n  - trajectory_in_order\n  - goal_success' },
    },
  ],
};

export function GuideTab() {
  const [lang, setLang] = useState<'en' | 'zh'>('en');
  const t = lang; // shorthand

  return (
    <div className="max-w-[820px] mx-auto p-6">
      {/* Language Toggle */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-bold tracking-tight">{guideContent.title[t]}</h1>
          <p className="text-[12px] text-[var(--color-text-secondary)] leading-relaxed mt-1 max-w-[600px]">
            {guideContent.subtitle[t]}
          </p>
        </div>
        <div className="flex items-center gap-1 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-md p-0.5 shrink-0">
          <button
            onClick={() => setLang('en')}
            className={`px-2.5 py-1 rounded text-[11px] font-medium transition-colors ${lang === 'en' ? 'bg-[var(--color-primary)] text-white' : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'}`}
          >EN</button>
          <button
            onClick={() => setLang('zh')}
            className={`px-2.5 py-1 rounded text-[11px] font-medium transition-colors ${lang === 'zh' ? 'bg-[var(--color-primary)] text-white' : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'}`}
          >中文</button>
        </div>
      </div>

      {/* Overview Cards */}
      <div className="grid grid-cols-3 gap-3 mb-8">
        {guideContent.overview[t].map((title, i) => (
          <GuideCard key={i} icon={['🎯', '🔄', '⚡'][i]} title={title} desc={guideContent.overviewDesc[t][i]} />
        ))}
      </div>

      {/* Architecture diagram (official eval-architecture.svg) */}
      <div className="mb-6 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] p-3">
        <img
          src={t === 'zh' ? '/eval-architecture-zh.svg' : '/eval-architecture.svg'}
          alt="SwarmAI Eval architecture — decoupled system-level subsystem"
          className="w-full h-auto rounded"
        />
      </div>

      {/* Single-run sequence diagram (eval-sequence.svg) */}
      <div className="mb-8 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] p-3">
        <img
          src={t === 'zh' ? '/eval-sequence-zh.svg' : '/eval-sequence.svg'}
          alt="SwarmAI Eval — one run end to end (trigger → clean-session spawn → per-case evaluator dispatch → score + BVT → report → consume)"
          className="w-full h-auto rounded"
        />
      </div>

      {/* Section 1: The Eval Dimensions */}
      <div className="mb-8">
        <h2 className="text-[15px] font-semibold mb-3">{guideContent.dimensions[t]}</h2>
        <div className="space-y-2">
          {guideContent.dimensionItems.map((dim) => (
            <div key={dim.key} data-dim-key={dim.key} className="p-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)]">
              <div className="flex items-start gap-3">
                <span className="text-lg shrink-0">{dim.icon}</span>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-0.5">
                    <span className="text-xs font-semibold">{dim[t].name}</span>
                    <span className="text-[9px] px-1.5 py-0.5 rounded bg-[var(--color-primary)]/10 text-[var(--color-primary)] font-mono">{dim.share}</span>
                  </div>
                  <div className="text-[11px] text-[var(--color-text-secondary)] italic mb-1">"{dim[t].question}"</div>
                  <div className="text-[10px] text-[var(--color-text-muted)] leading-relaxed">{dim[t].method}</div>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Section 2: Evaluator Methodology */}
      <div className="mb-8">
        <h2 className="text-[15px] font-semibold mb-1.5">{guideContent.evaluators[t]}</h2>
        <p className="text-[11px] text-[var(--color-text-muted)] mb-3">{guideContent.evalIntro[t]}</p>

        <div className="grid grid-cols-3 gap-3">
          {/* Programmatic */}
          <div className="p-3 rounded-lg border border-green-500/20 bg-green-500/5">
            <div className="text-[10px] font-bold text-green-600 mb-2">{guideContent.programmatic[t]}</div>
            <div className="space-y-1.5">
              {guideContent.programmaticItems.map((item) => (
                <div key={item.name} className="text-[10px]">
                  <span className="font-mono text-green-600">{item.name}</span>
                  <span className="text-[var(--color-text-muted)] ml-1">— {item[t]}</span>
                </div>
              ))}
            </div>
          </div>

          {/* LLM-Judge */}
          <div className="p-3 rounded-lg border border-[var(--color-primary)]/20 bg-[var(--color-primary)]/5">
            <div className="text-[10px] font-bold text-[var(--color-primary)] mb-2">{guideContent.llmJudge[t]}</div>
            <div className="space-y-1.5">
              {guideContent.llmJudgeItems.map((item) => (
                <div key={item.name} className="text-[10px]">
                  <span className="font-mono text-[var(--color-primary)]">{item.name}</span>
                  <span className="text-[var(--color-text-muted)] ml-1">— {item[t]}</span>
                </div>
              ))}
            </div>
          </div>

          {/* Behavior — third EXECUTION METHOD (real-agent spawn), not a programmatic/LLM evaluator */}
          <div className="p-3 rounded-lg border border-amber-500/20 bg-amber-500/5">
            <div className="text-[10px] font-bold text-amber-600 mb-2">{guideContent.behavior[t]}</div>
            <div className="space-y-1.5">
              {guideContent.behaviorItems.map((item) => (
                <div key={item.name} className="text-[10px]">
                  <span className="font-mono text-amber-600">{item.name}</span>
                  <span className="text-[var(--color-text-muted)] ml-1">— {item[t]}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* Section 3: Execution Architecture */}
      <div className="mb-8">
        <h2 className="text-[15px] font-semibold mb-3">{guideContent.architecture[t]}</h2>
        <div className="p-4 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)]">
          <pre className="text-[10px] font-mono text-[var(--color-text-secondary)] leading-[2] whitespace-pre overflow-x-auto">
{t === 'en'
? `┌─────────────────────────────────────────────────────────────┐
│  PRODUCTION SESSION              EVAL SESSION (isolated)     │
│  ┌───────────────────┐          ┌───────────────────┐       │
│  │ User conversation │          │ Golden Set case   │       │
│  │ + accumulated     │          │ (zero history)    │       │
│  │   context         │          │                   │       │
│  └────────┬──────────┘          └────────┬──────────┘       │
│           │                              │                   │
│           ▼                              ▼                   │
│  ┌───────────────────────────────────────────────────┐      │
│  │        IDENTICAL System Prompt Assembly            │      │
│  │  (11 context files + hooks + skills + model)      │      │
│  └───────────────────────────────────────────────────┘      │
│           │                              │                   │
│           ▼                              ▼                   │
│  ┌─────────────┐                ┌──────────────┐            │
│  │ Production  │                │ Pinned Judge │            │
│  │ Model       │                │ Model        │            │
│  │ (latest)    │                │ (opus-4-6)   │            │
│  └─────────────┘                └──────────────┘            │
└─────────────────────────────────────────────────────────────┘`
: `┌─────────────────────────────────────────────────────────────┐
│  生产 SESSION                    评估 SESSION（隔离）        │
│  ┌───────────────────┐          ┌───────────────────┐       │
│  │ 用户对话           │          │ Golden Set 案例   │       │
│  │ + 累积的           │          │ （零历史）        │       │
│  │   上下文           │          │                   │       │
│  └────────┬──────────┘          └────────┬──────────┘       │
│           │                              │                   │
│           ▼                              ▼                   │
│  ┌───────────────────────────────────────────────────┐      │
│  │        完全相同的 System Prompt 组装               │      │
│  │  （11 上下文文件 + hooks + skills + 模型）         │      │
│  └───────────────────────────────────────────────────┘      │
│           │                              │                   │
│           ▼                              ▼                   │
│  ┌─────────────┐                ┌──────────────┐            │
│  │ 生产模型     │                │ 固定 Judge  │            │
│  │ （最新版）   │                │ 模型         │            │
│  │             │                │ (opus-4-6)   │            │
│  └─────────────┘                └──────────────┘            │
└─────────────────────────────────────────────────────────────┘`}
          </pre>
          <div className="mt-3 space-y-2">
            <p className="text-[10px] text-[var(--color-text-muted)] leading-relaxed">{guideContent.archDesc[t]}</p>
            <div className="p-2 rounded bg-yellow-500/5 border border-yellow-500/20">
              <div className="text-[9px] font-bold text-yellow-600 mb-0.5">{t === 'en' ? 'Judge Pinning' : 'Judge 版本固定'}</div>
              <p className="text-[10px] text-[var(--color-text-muted)] leading-relaxed">{guideContent.archJudge[t]}</p>
            </div>
          </div>
        </div>
      </div>

      {/* Section 4: Coverage Distribution — live source is the Golden Set tab (no hardcoded
          snapshot here: a static dim×cat matrix silently drifts every run — R30#4). */}
      <div className="mb-8">
        <h2 className="text-[15px] font-semibold mb-1.5">{guideContent.coverage[t]}</h2>
        <p className="text-[11px] text-[var(--color-text-muted)] mb-3">{guideContent.coverageDesc[t]}</p>
        <div className="flex items-center gap-2 border border-[var(--color-border)] rounded-lg p-3 bg-[var(--color-bg)]">
          <span className="material-symbols-outlined text-[16px] text-[var(--color-primary)]">checklist</span>
          <p className="text-[11px] text-[var(--color-text-muted)]">
            {t === 'en'
              ? 'The live, filterable distribution across all 4 axes lives on the Golden Set tab — it grows every run, so it is never snapshotted here.'
              : '沿全部 4 个轴的 live、可筛选分布在 Golden Set tab — 它每次运行都在增长，故这里不做快照。'}
          </p>
        </div>
      </div>

      {/* Section 5: Case Lifecycle */}
      <div className="mb-8">
        <h2 className="text-[15px] font-semibold mb-3">{guideContent.lifecycle[t]}</h2>
        <div className="flex items-start gap-0">
          {guideContent.lifecycleStages.map((stage, i) => (
            <div key={i} className="flex items-start flex-1">
              <div className="flex flex-col items-center">
                <div className={`w-7 h-7 rounded-full flex items-center justify-center text-[10px] font-bold text-white shrink-0 ${
                  i === 0 ? 'bg-red-500' : i === 1 ? 'bg-orange-500' : i === 2 ? 'bg-[var(--color-primary)]' : i === 3 ? 'bg-green-500' : 'bg-gray-400'
                }`}>{i + 1}</div>
                <div className="mt-1.5 text-center px-1">
                  <div className="text-[10px] font-semibold">{stage[t].stage}</div>
                  <div className="text-[9px] text-[var(--color-text-muted)] leading-tight mt-0.5">{stage[t].desc}</div>
                </div>
              </div>
              {i < 4 && <div className="w-full h-px bg-[var(--color-border)] mt-3.5 mx-0.5" />}
            </div>
          ))}
        </div>
      </div>

      {/* Section 6: Triggers & Cadence */}
      <div className="mb-8">
        <h2 className="text-[15px] font-semibold mb-3">{guideContent.triggers[t]}</h2>
        <div className="border border-[var(--color-border)] rounded-lg overflow-hidden">
          <table className="w-full text-[11px]">
            <thead>
              <tr className="bg-[var(--color-bg)] border-b border-[var(--color-border)]">
                <th className="text-left px-3 py-2 font-medium text-[var(--color-text-muted)]">{t === 'en' ? 'Trigger' : '触发条件'}</th>
                <th className="text-left px-3 py-2 font-medium text-[var(--color-text-muted)]">{t === 'en' ? 'Cadence' : '频率'}</th>
                <th className="text-left px-3 py-2 font-medium text-[var(--color-text-muted)]">{t === 'en' ? 'What Runs' : '运行内容'}</th>
              </tr>
            </thead>
            <tbody>
              {guideContent.triggerItems.map((item, i) => (
                <tr key={i} className="border-b border-[var(--color-border)] last:border-0">
                  <td className="px-3 py-2 font-medium">{item[t].trigger}</td>
                  <td className="px-3 py-2 text-[var(--color-primary)] font-mono text-[10px]">{item[t].cadence}</td>
                  <td className="px-3 py-2 text-[var(--color-text-muted)]">{item[t].desc}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Section 7: Case Anatomy (kept from original, enhanced) */}
      <div className="mb-8">
        <h2 className="text-[15px] font-semibold mb-1.5">{guideContent.caseExample[t]}</h2>
        <p className="text-[11px] text-[var(--color-text-muted)] mb-2.5">{guideContent.caseExampleDesc[t]}</p>
        <pre className="p-4 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] text-[10.5px] font-mono leading-[1.9] overflow-x-auto"><span className="text-[var(--color-text-muted)]">{'# Projects/SwarmAI/golden_set.yaml'}</span>{'\n'}
{'\n'}
{'- '}<span className="text-[var(--color-primary)]">id:</span>{' GS_EXAMPLE\n'}
{'  '}<span className="text-[var(--color-primary)]">category:</span>{' compliance\n'}
{'  '}<span className="text-[var(--color-primary)]">dimension:</span>{' compliance\n'}
{'  '}<span className="text-[var(--color-primary)]">level:</span>{' session\n'}
{'  '}<span className="text-[var(--color-primary)]">title:</span>{' "CLASS A skip detection"\n'}
{'  '}<span className="text-[var(--color-primary)]">affected_by:</span>{' [STEERING.R1, STEERING.R13, SOUL.P5]\n'}
{'\n'}
{'  '}<span className="text-[var(--color-text-muted)]">{'# Layer 1: Expected tool trajectory'}</span>{'\n'}
{'  '}<span className="text-[var(--color-primary)]">expected_trajectory:</span>{'\n'}
{'    - "Invoke s_autonomous-pipeline"\n'}
{'    - "Spawn adversarial sub-agent"\n'}
{'  '}<span className="text-[var(--color-primary)]">trajectory_match:</span>{' in_order\n'}
{'\n'}
{'  '}<span className="text-[var(--color-text-muted)]">{'# Layer 2: Semantic assertions (LLM-judge)'}</span>{'\n'}
{'  '}<span className="text-[var(--color-primary)]">assertions:</span>{'\n'}
{'    - "Agent does NOT self-exempt based on simplicity"\n'}
{'    - "Adversarial review spawned before commit"\n'}
{'\n'}
{'  '}<span className="text-[var(--color-text-muted)]">{'# Layer 3: Programmatic keyword check'}</span>{'\n'}
{'  '}<span className="text-[var(--color-primary)]">expected_response_contains:</span>{'\n'}
{'    - "pipeline"\n'}
{'  '}<span className="text-[var(--color-primary)]">evaluators:</span>{' [trajectory_in_order, goal_success]'}
        </pre>
        <div className="grid grid-cols-3 gap-2 mt-3">
          <div className="p-2 rounded border border-[var(--color-primary)]/20 bg-[var(--color-primary)]/5">
            <div className="text-[9px] font-bold text-[var(--color-primary)] mb-0.5">{t === 'en' ? 'LAYER 1: Trajectory' : '第一层：轨迹'}</div>
            <div className="text-[10px] text-[var(--color-text-muted)]">{t === 'en' ? 'Right tools, right order?' : '正确的工具，正确的顺序？'}</div>
          </div>
          <div className="p-2 rounded border border-green-500/20 bg-green-500/5">
            <div className="text-[9px] font-bold text-green-500 mb-0.5">{t === 'en' ? 'LAYER 2: Assertions' : '第二层：断言'}</div>
            <div className="text-[10px] text-[var(--color-text-muted)]">{t === 'en' ? 'LLM-judge verifies behavior' : 'LLM-judge 验证行为'}</div>
          </div>
          <div className="p-2 rounded border border-yellow-500/20 bg-yellow-500/5">
            <div className="text-[9px] font-bold text-yellow-500 mb-0.5">{t === 'en' ? 'LAYER 3: Response' : '第三层：响应'}</div>
            <div className="text-[10px] text-[var(--color-text-muted)]">{t === 'en' ? 'Programmatic keyword match' : '程序化关键词匹配'}</div>
          </div>
        </div>
      </div>

      {/* Section 8: The Flywheel */}
      <div className="mb-8">
        <h2 className="text-[15px] font-semibold mb-3">{guideContent.flywheel[t]}</h2>
        <div className="p-4 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)]">
          <pre className="text-[10px] font-mono text-center text-[var(--color-text-secondary)] leading-relaxed whitespace-pre">
{t === 'en'
  ? `Mistake → Correction → Golden Set Case → Eval Detects Recurrence → Alert → Fix → Stronger
     ↑                                                                                       │
     └──────────────────────────────── self-growing coverage ────────────────────────────────┘`
  : `错误 → 纠正 → Golden Set 案例 → Eval 检测复发 → 告警 → 修复 → 更强
   ↑                                                                          │
   └────────────────────────────── 自增长覆盖 ─────────────────────────────────┘`}
          </pre>
          <div className="mt-3 p-2.5 rounded-md bg-[var(--color-hover)] text-[10px] text-[var(--color-text-muted)] leading-relaxed">
            <strong className="text-[var(--color-text-secondary)]">{t === 'en' ? 'Key insight:' : '核心洞察：'}</strong>{' '}
            {t === 'en'
              ? 'Every correction the user makes becomes a permanent behavioral test. The eval set is not maintained by QA — it grows organically from real failures. Coverage compounds monotonically.'
              : '用户的每次纠正都会成为永久的行为测试。评估集不是由 QA 维护的 — 它从真实失败中有机生长。覆盖率单调递增。'}
          </div>
        </div>
      </div>

      {/* Section 9: vs Enterprise (enhanced) */}
      <div className="mb-8">
        <h2 className="text-[15px] font-semibold mb-3">{guideContent.comparison[t]}</h2>
        <div className="border border-[var(--color-border)] rounded-lg overflow-hidden">
          <table className="w-full text-[11px]">
            <thead>
              <tr className="bg-[var(--color-bg)] border-b border-[var(--color-border)]">
                <th className="text-left px-3 py-2 font-medium text-[var(--color-text-muted)]"></th>
                <th className="text-left px-3 py-2 font-medium text-[var(--color-text-muted)]">{t === 'en' ? 'Enterprise Agent Eval' : '企业级 Agent Eval'}</th>
                <th className="text-left px-3 py-2 font-medium text-[var(--color-primary)]">SwarmAI OS Eval</th>
              </tr>
            </thead>
            <tbody>
              {guideContent.comparisonRows.map((row, i) => (
                <tr key={i} className="border-b border-[var(--color-border)] last:border-0">
                  <td className="px-3 py-2 font-medium">{row[t].dim}</td>
                  <td className="px-3 py-2 text-[var(--color-text-muted)]">{row[t].enterprise}</td>
                  <td className="px-3 py-2">{row[t].ours}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Section 10: Intelligence Velocity */}
      <div className="mb-8">
        <h2 className="text-[15px] font-semibold mb-1.5">{guideContent.iv[t]}</h2>
        <p className="text-[11px] text-[var(--color-text-muted)] mb-3 leading-relaxed">{guideContent.ivDesc[t]}</p>
        <div className="p-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] mb-3">
          <pre className="text-[11px] font-mono text-center text-[var(--color-primary)] font-semibold whitespace-pre-wrap overflow-x-auto">{guideContent.ivFormula[t]}</pre>
        </div>
        <div className="grid grid-cols-3 gap-2">
          {guideContent.ivComponents.map((comp, i) => {
            const c = comp[t] ?? comp['en'];
            return (
              <div key={i} className="p-2.5 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)]">
                <div className="text-[10px] font-bold text-[var(--color-text-secondary)] mb-1">{c.name}</div>
                <div className="text-[10px] text-[var(--color-text-muted)] leading-relaxed">{c.desc}</div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Section 11: How to Write a Case */}
      <div className="mb-8">
        <h2 className="text-[15px] font-semibold mb-1.5">{guideContent.howToWrite[t]}</h2>
        <p className="text-[11px] text-[var(--color-text-muted)] mb-3">{guideContent.howToWriteDesc[t]}</p>
        <div className="space-y-3">
          {guideContent.howToWriteSteps.map((step, i) => (
            <div key={i} className="p-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)]">
              <div className="flex items-start gap-2">
                <span className="text-lg shrink-0" aria-hidden="true">{step.icon}</span>
                <div className="flex-1 min-w-0">
                  <div className="text-[11px] font-semibold mb-1">{(step[t] ?? step['en']).title}</div>
                  <div className="text-[10px] text-[var(--color-text-muted)] leading-relaxed mb-2">{(step[t] ?? step['en']).desc}</div>
                  <pre className="text-[9px] font-mono p-2 rounded bg-[var(--color-hover)] text-[var(--color-text-secondary)] whitespace-pre-wrap">{(step[t] ?? step['en']).example}</pre>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ─── Shared Components ────────────────────────────────────────────────────────

function MetricCard({ label, value, color, sub }: { label: string; value: string; color: string; sub: string }) {
  const colorClass = color === 'green' ? 'text-green-500' : color === 'yellow' ? 'text-yellow-500' : color === 'red' ? 'text-red-500' : 'text-[var(--color-text)]';
  return (
    <div className="p-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)]">
      <div className="text-[10px] text-[var(--color-text-muted)] mb-1">{label}</div>
      <div className={`text-xl font-bold font-mono ${colorClass}`}>{value}</div>
      <div className="text-[10px] text-[var(--color-text-muted)] mt-1">{sub}</div>
    </div>
  );
}

function StatusBadge({ status }: { status?: string }) {
  if (!status) return <span className="text-[10px] text-[var(--color-text-muted)]">—</span>;
  const cls = status === 'passed' ? 'bg-green-500/10 text-green-500'
    : status === 'failed' ? 'bg-red-500/10 text-red-500'
    : 'bg-[var(--color-hover)] text-[var(--color-text-muted)]';
  return <span className={`inline-block px-1.5 py-0.5 rounded text-[9px] font-semibold uppercase ${cls}`}>{status}</span>;
}

// Distinguishes curated public cases (ship in the repo) from private instance
// cases (gitignored). The single most-requested distinction for the golden set.
function OriginBadge({ origin }: { origin?: string }) {
  if (!origin) return <span className="text-[10px] text-[var(--color-text-muted)]">—</span>;
  const isPublic = origin === 'public';
  const cls = isPublic ? 'bg-green-500/10 text-green-600' : 'bg-[var(--color-hover)] text-[var(--color-text-muted)]';
  return (
    <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[9px] font-semibold ${cls}`} title={isPublic ? 'Public — ships in the repo' : 'Private — gitignored instance case'}>
      <span className="material-symbols-outlined text-[11px]">{isPublic ? 'public' : 'lock'}</span>
      {origin}
    </span>
  );
}

function GuideCard({ icon, title, desc }: { icon: string; title: string; desc: string }) {
  return (
    <div className="p-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)]">
      <div className="text-base mb-1">{icon}</div>
      <div className="text-xs font-semibold mb-1">{title}</div>
      <div className="text-[10px] text-[var(--color-text-muted)] leading-relaxed">{desc}</div>
    </div>
  );
}

function Loading() {
  return (
    <div className="flex items-center justify-center flex-1 min-h-[10rem] text-sm text-[var(--color-text-muted)]">
      Loading eval data...
    </div>
  );
}

function ErrorState({ message }: { message: string }) {
  return (
    <div className="flex items-center justify-center flex-1 min-h-[10rem] text-sm text-red-400">
      {message}
    </div>
  );
}

// ─── Sparkline SVG ──────────────────────────────────────────────────────────

function Sparkline({ values, height = 32, color = 'var(--color-primary)', dates, axis = false }: { values: number[]; height?: number; color?: string; dates?: string[]; axis?: boolean }) {
  if (values.length < 2) return null;

  const width = 200;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;

  const coords = values.map((v, i) => ({
    x: (i / (values.length - 1)) * width,
    y: height - ((v - min) / range) * (height - 4) - 2,
    v,
    // dates is optional + may not line up 1:1 with values — guard the lookup.
    d: dates && dates.length === values.length ? dates[i] : undefined,
  }));
  const points = coords.map((p) => `${p.x},${p.y}`);

  return (
    <div className={axis ? 'flex items-stretch gap-1.5' : ''}>
      {/* Optional y-axis min/max labels (only when axis requested) */}
      {axis && (
        <div className="flex flex-col justify-between text-[8px] text-[var(--color-text-muted)] font-mono py-0.5 shrink-0" style={{ height }}>
          <span>{Math.round(max)}</span>
          <span>{Math.round(min)}</span>
        </div>
      )}
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full" style={{ height }} preserveAspectRatio="none">
        <polyline
          points={points.join(' ')}
          fill="none"
          stroke={color}
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        {/* Fill area under the line */}
        <polygon
          points={`0,${height} ${points.join(' ')} ${width},${height}`}
          fill={color}
          opacity="0.1"
        />
        {/* Data points with hover tooltip (date + score). Non-scaling radius via
            vector-effect so points stay round despite preserveAspectRatio=none. */}
        {coords.map((p, i) => (
          <circle key={i} cx={p.x} cy={p.y} r={2.5} fill={color} vectorEffect="non-scaling-stroke" style={{ transformBox: 'fill-box' }}>
            <title>{p.d ? `${p.d}: ${Math.round(p.v)}` : `${Math.round(p.v)}`}</title>
          </circle>
        ))}
      </svg>
    </div>
  );
}

// ─── Case Detail Drawer ─────────────────────────────────────────────────────

function CaseDetailDrawer({ caseId, onClose }: { caseId: string; onClose: () => void }) {
  const { data: detail, isLoading } = useCaseDetail(caseId);
  const updateCase = useUpdateCase();
  const [editing, setEditing] = useState(false);
  const [editTitle, setEditTitle] = useState('');

  const startEdit = () => {
    if (detail) {
      setEditTitle(detail.title || '');
      setEditing(true);
    }
  };

  const saveEdit = () => {
    updateCase.mutate({ id: caseId, updates: { title: editTitle } }, {
      onSuccess: () => setEditing(false),
    });
  };

  return (
    <div className="w-[380px] border-l border-[var(--color-border)] bg-[var(--color-card)] overflow-y-auto shrink-0">
      <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--color-border)] sticky top-0 bg-[var(--color-card)] z-10">
        <span className="text-xs font-semibold font-mono">{caseId}</span>
        <button onClick={onClose} className="p-1 rounded hover:bg-[var(--color-hover)]">
          <span className="material-symbols-outlined text-sm">close</span>
        </button>
      </div>

      {isLoading && <Loading />}
      {detail && (
        <div className="p-4 space-y-4 text-xs">
          {/* Title (editable) */}
          <div>
            <div className="text-[10px] text-[var(--color-text-muted)] uppercase mb-1">Title</div>
            {editing ? (
              <div className="flex gap-1">
                <input
                  value={editTitle}
                  onChange={(e) => setEditTitle(e.target.value)}
                  className="flex-1 px-2 py-1 rounded border border-[var(--color-border)] bg-[var(--color-bg)] text-xs"
                  autoFocus
                />
                <button onClick={saveEdit} className="px-2 py-1 rounded bg-[var(--color-primary)] text-white text-[10px]">Save</button>
                <button onClick={() => setEditing(false)} className="px-2 py-1 rounded border border-[var(--color-border)] text-[10px]">Cancel</button>
              </div>
            ) : (
              <div className="flex items-center gap-2">
                <span className="font-medium">{detail.title}</span>
                <button onClick={startEdit} className="text-[var(--color-text-muted)] hover:text-[var(--color-text)]">
                  <span className="material-symbols-outlined text-[14px]">edit</span>
                </button>
              </div>
            )}
          </div>

          {/* Metadata Grid — 2×3 */}
          <div className="grid grid-cols-2 gap-2">
            <div>
              <div className="text-[10px] text-[var(--color-text-muted)] uppercase">Category</div>
              <span className="px-1.5 py-0.5 rounded bg-[var(--color-hover)] text-[10px]">{detail.category}</span>
            </div>
            <div>
              <div className="text-[10px] text-[var(--color-text-muted)] uppercase">Dimension</div>
              <span className="text-[11px]">{detail.dimension?.replace(/_/g, ' ')}</span>
            </div>
            <div>
              <div className="text-[10px] text-[var(--color-text-muted)] uppercase">Level</div>
              <span className="text-[11px]">{detail.level || '—'}</span>
            </div>
            <div>
              <div className="text-[10px] text-[var(--color-text-muted)] uppercase">Tier</div>
              <span className="text-[11px]">{detail.tier || 'active'}</span>
            </div>
            <div>
              <div className="text-[10px] text-[var(--color-text-muted)] uppercase">Source</div>
              <span className="text-[11px] font-mono">{detail.source || '—'}</span>
            </div>
            <div>
              <div className="text-[10px] text-[var(--color-text-muted)] uppercase">Evaluators</div>
              <div className="flex flex-wrap gap-0.5 mt-0.5">
                {(detail.evaluators || []).map((ev, i) => (
                  <span key={i} className="px-1 py-0.5 rounded bg-[var(--color-hover)] text-[9px] font-mono">{ev}</span>
                ))}
              </div>
            </div>
          </div>

          {/* Affected By */}
          {detail.affected_by && detail.affected_by.length > 0 && (
            <div>
              <div className="text-[10px] text-[var(--color-text-muted)] uppercase mb-1">Affected By</div>
              <div className="flex flex-wrap gap-1">
                {detail.affected_by.map((a, i) => (
                  <span key={i} className="px-1.5 py-0.5 rounded bg-[var(--color-hover)] text-[10px]">{a}</span>
                ))}
              </div>
            </div>
          )}

          {/* Scenario */}
          {detail.scenario?.turns && detail.scenario.turns.length > 0 && (
            <div>
              <div className="text-[10px] text-[var(--color-text-muted)] uppercase mb-1">Scenario</div>
              <div className="p-2.5 rounded bg-[var(--color-bg)] border border-[var(--color-border)]">
                {detail.scenario.turns.map((t, i) => (
                  <div key={i} className="text-[11px] font-mono leading-relaxed">
                    <span className="text-[var(--color-text-muted)]">→ </span>{t.input}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Layer 1: Expected Trajectory */}
          {detail.expected_trajectory && detail.expected_trajectory.length > 0 && (
            <div className="p-2.5 rounded border border-[var(--color-primary)]/20 bg-[var(--color-primary)]/5">
              <div className="flex items-center justify-between mb-1.5">
                <div className="text-[9px] font-bold text-[var(--color-primary)] uppercase">Layer 1: Trajectory</div>
                {detail.trajectory_match && (
                  <span className="text-[9px] text-[var(--color-text-muted)] font-mono">{detail.trajectory_match}</span>
                )}
              </div>
              <div className="flex flex-wrap gap-1">
                {detail.expected_trajectory.map((t, i) => (
                  <span key={i} className="px-1.5 py-0.5 rounded bg-[var(--color-primary)]/10 text-[var(--color-primary)] text-[10px] font-mono">{t}</span>
                ))}
              </div>
            </div>
          )}

          {/* Layer 2: Assertions */}
          {detail.assertions && detail.assertions.length > 0 && (
            <div className="p-2.5 rounded border border-green-500/20 bg-green-500/5">
              <div className="text-[9px] font-bold text-green-500 uppercase mb-1.5">Layer 2: Assertions</div>
              <ul className="space-y-1">
                {detail.assertions.map((a, i) => (
                  <li key={i} className="text-[10px] text-[var(--color-text-secondary)] leading-relaxed flex gap-1.5">
                    <span className="text-green-500 shrink-0">•</span>
                    <span>{a}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Layer 3: Expected Response Contains */}
          {detail.expected_response_contains && detail.expected_response_contains.length > 0 && (
            <div className="p-2.5 rounded border border-yellow-500/20 bg-yellow-500/5">
              <div className="text-[9px] font-bold text-yellow-500 uppercase mb-1.5">Layer 3: Response</div>
              <div className="flex flex-wrap gap-1">
                {detail.expected_response_contains.map((kw, i) => (
                  <span key={i} className="px-1.5 py-0.5 rounded bg-yellow-500/10 text-yellow-600 dark:text-yellow-400 text-[10px] font-mono">{kw}</span>
                ))}
              </div>
            </div>
          )}

          {/* Tags */}
          {detail.tags && detail.tags.length > 0 && (
            <div>
              <div className="text-[10px] text-[var(--color-text-muted)] uppercase mb-1">Tags</div>
              <div className="flex flex-wrap gap-1">
                {detail.tags.map((tag, i) => (
                  <span key={i} className="px-1.5 py-0.5 rounded bg-[var(--color-hover)] text-[10px] text-[var(--color-text-muted)]">{tag}</span>
                ))}
              </div>
            </div>
          )}

          {/* Promoted From */}
          {detail.promoted_from && (
            <div>
              <div className="text-[10px] text-[var(--color-text-muted)] uppercase mb-1">Promoted From</div>
              <span className="text-[10px] text-[var(--color-text-muted)] font-mono">{detail.promoted_from}</span>
            </div>
          )}

          {/* Run History */}
          {detail.history && detail.history.length > 0 && (
            <div>
              <div className="text-[10px] text-[var(--color-text-muted)] uppercase mb-1">Run History</div>
              <div className="space-y-1">
                {detail.history.map((h, i) => (
                  <div key={i} className="flex items-center gap-2 p-1.5 rounded bg-[var(--color-bg)]">
                    <StatusBadge status={h.status} />
                    <span className="font-mono text-[10px]">{h.triggered_at?.slice(0, 10)}</span>
                    {h.notes && <span className="text-[10px] text-[var(--color-text-muted)] truncate">{h.notes}</span>}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Add Case Modal ─────────────────────────────────────────────────────────

function AddCaseModal({ onClose, categories }: { onClose: () => void; categories: string[] }) {
  const createCase = useCreateCase();
  const [form, setForm] = useState({
    id: '',
    title: '',
    category: categories[0] || 'compliance',
    dimension: 'compliance',
    evaluators: 'file_contains',
    affected_by: '',
  });

  // Intercept Escape before parent Modal's document-level handler fires
  useEffect(() => {
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopImmediatePropagation();
        onClose();
      }
    };
    document.addEventListener('keydown', handleEscape, true); // capture phase
    return () => document.removeEventListener('keydown', handleEscape, true);
  }, [onClose]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    createCase.mutate({
      id: form.id,
      title: form.title,
      category: form.category,
      dimension: form.dimension,
      evaluators: [form.evaluators],
      affected_by: form.affected_by.split(',').map(s => s.trim()).filter(Boolean),
      level: 'session',
      scenario: { turns: [] },
      verification: {},
    }, {
      onSuccess: () => onClose(),
    });
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={onClose}>
      <div className="w-full max-w-md bg-[var(--color-card)] rounded-xl border border-[var(--color-border)] shadow-2xl p-6" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-sm font-semibold mb-4">Add Golden Set Case</h3>
        <form onSubmit={handleSubmit} className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <label className="block">
              <span className="text-[10px] text-[var(--color-text-muted)] uppercase">ID</span>
              <input
                value={form.id}
                onChange={(e) => setForm({ ...form, id: e.target.value })}
                placeholder="GS021"
                required
                className="mt-0.5 w-full px-2 py-1.5 rounded border border-[var(--color-border)] bg-[var(--color-bg)] text-xs"
              />
            </label>
            <label className="block">
              <span className="text-[10px] text-[var(--color-text-muted)] uppercase">Category</span>
              <select
                value={form.category}
                onChange={(e) => setForm({ ...form, category: e.target.value })}
                className="mt-0.5 w-full px-2 py-1.5 rounded border border-[var(--color-border)] bg-[var(--color-bg)] text-xs"
              >
                {categories.map(c => <option key={c} value={c}>{c}</option>)}
              </select>
            </label>
          </div>
          <label className="block">
            <span className="text-[10px] text-[var(--color-text-muted)] uppercase">Title</span>
            <input
              value={form.title}
              onChange={(e) => setForm({ ...form, title: e.target.value })}
              placeholder="Brief description of expected behavior"
              required
              className="mt-0.5 w-full px-2 py-1.5 rounded border border-[var(--color-border)] bg-[var(--color-bg)] text-xs"
            />
          </label>
          <div className="grid grid-cols-2 gap-3">
            <label className="block">
              <span className="text-[10px] text-[var(--color-text-muted)] uppercase">Dimension</span>
              <input
                value={form.dimension}
                onChange={(e) => setForm({ ...form, dimension: e.target.value })}
                className="mt-0.5 w-full px-2 py-1.5 rounded border border-[var(--color-border)] bg-[var(--color-bg)] text-xs"
              />
            </label>
            <label className="block">
              <span className="text-[10px] text-[var(--color-text-muted)] uppercase">Evaluator</span>
              <select
                value={form.evaluators}
                onChange={(e) => setForm({ ...form, evaluators: e.target.value })}
                className="mt-0.5 w-full px-2 py-1.5 rounded border border-[var(--color-border)] bg-[var(--color-bg)] text-xs"
              >
                <option value="file_contains">file_contains</option>
                <option value="canary_pass">canary_pass</option>
                <option value="keyword_match">keyword_match</option>
                <option value="goal_success">goal_success (LLM)</option>
              </select>
            </label>
          </div>
          <label className="block">
            <span className="text-[10px] text-[var(--color-text-muted)] uppercase">Affected By (comma-separated)</span>
            <input
              value={form.affected_by}
              onChange={(e) => setForm({ ...form, affected_by: e.target.value })}
              placeholder="AGENT.md, STEERING.md"
              className="mt-0.5 w-full px-2 py-1.5 rounded border border-[var(--color-border)] bg-[var(--color-bg)] text-xs"
            />
          </label>
          <div className="flex justify-end gap-2 pt-2">
            <button type="button" onClick={onClose} className="px-3 py-1.5 text-xs rounded border border-[var(--color-border)] hover:bg-[var(--color-hover)]">Cancel</button>
            <button
              type="submit"
              disabled={createCase.isPending}
              className="px-3 py-1.5 text-xs rounded bg-[var(--color-primary)] text-white hover:opacity-90 disabled:opacity-50"
            >
              {createCase.isPending ? 'Creating...' : 'Create Case'}
            </button>
          </div>
          {createCase.isError && (
            <div className="text-[10px] text-red-500 mt-1">
              {(createCase.error as Error)?.message || 'Failed to create case'}
            </div>
          )}
        </form>
      </div>
    </div>
  );
}
