/**
 * OS Eval Dashboard — Interactive eval health, golden set CRUD, run triggers, and trends.
 *
 * P2: Read-only visualization.
 * P3: CRUD on golden set, run triggers, case detail drawer, sparklines.
 *
 * Data fetched from /api/eval/* endpoints via TanStack Query.
 */
import { useState, useEffect, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import api from '../services/api';
import { getApiBaseUrl } from '../services/tauri';
import { openExternal } from '../utils/openExternal';
import { computeBreakdowns, type Breakdowns, type BreakdownEntry } from './eval-breakdowns';

// ─── Types ────────────────────────────────────────────────────────────────────

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
  follow_ups?: FollowUp[];
  lines?: EvalLinesData;
}

/** /api/eval/health ``lines`` (backend EvalService.get_lines). */
export interface EvalLinesData {
  quick: { last_run: string | null; passed: number | null; total: number | null };
  full: { last_run: string | null; score: number | null; next_run: string | null };
  online: { last_run: string | null; sessions_checked: number | null; next_run: string | null };
  golden_set: { product: number; private: number; total: number; product_cap: number; total_cap: number };
}

/** One "needs your attention" item (backend core/eval_followups.build_follow_ups). */
export interface FollowUp {
  severity: 'high' | 'medium' | 'low';
  rule: number;
  what: { en: string; zh: string };
  why: string;
  action: { en: string; zh: string };
  case_ids: string[];
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
  purpose?: { en?: string; zh?: string } | null;
  tier: string;
  eval_method?: string;
  _origin?: string; // "product" | "private" — set by backend
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
  cases_timeout?: number;
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
  { id: 'session-quality', label: 'Online', icon: 'reviews' },
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
              role="tab"
              aria-selected={activeTab === tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`px-3 py-2.5 text-body transition-colors flex items-center gap-1.5 border-b-2 -mb-px whitespace-nowrap ${
                activeTab === tab.id
                  ? 'text-[var(--color-primary)] border-[var(--color-primary)]'
                  : 'text-[var(--color-text-muted)] border-transparent hover:text-[var(--color-text)] hover:border-[var(--color-border)]'
              }`}
            >
              <span className="material-symbols-outlined icon-xs">{tab.icon}</span>
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


  return (
    <div className="max-w-5xl mx-auto p-6">
      {/* Action Buttons */}
      <div className="flex gap-2 mb-4 justify-end">
        <button
          onClick={() => runCanary.mutate()}
          disabled={runCanary.isPending}
          className="btn-md btn-secondary transition-colors disabled:opacity-50"
        >
          {runCanary.isPending ? 'Running...' : 'Run Canary'}
        </button>
        <button
          onClick={() => triggerRun.mutate({})}
          disabled={triggerRun.isPending}
          className="btn-md btn-primary hover:opacity-90 transition-opacity disabled:opacity-50"
        >
          {triggerRun.isPending ? 'Starting...' : '▶ Run Full Eval'}
        </button>
      </div>
      {(triggerRun.isSuccess || runCanary.isSuccess) && (
        <div className="mb-3 p-2 rounded border border-[color-mix(in_srgb,var(--color-status-success)_20%,transparent)] bg-[color-mix(in_srgb,var(--color-status-success)_5%,transparent)] text-small text-[var(--color-status-success)]">
          {triggerRun.isSuccess && `✓ Eval started: ${(triggerRun.data as { run_id?: string })?.run_id || 'running'}`}
          {runCanary.isSuccess && `✓ Canary complete: ${(runCanary.data as { overall_score?: number })?.overall_score}%`}
        </div>
      )}

      <FollowUps items={health.follow_ups} />

      <EvalLines lines={health.lines} />

      {/* Recent Runs */}
      <h3 className="text-small font-semibold text-[var(--color-text-muted)] uppercase tracking-wide mb-3">Recent Eval Runs</h3>
      <div className="border border-[var(--color-border)] rounded-lg overflow-hidden">
        <table className="w-full text-small">
          <thead>
            <tr className="bg-[var(--color-bg)] border-b border-[var(--color-border)]">
              <th className="text-left px-3 py-2 text-[var(--color-text-muted)]">Date</th>
              <th className="text-left px-3 py-2 text-[var(--color-text-muted)]">Trigger</th>
              <th className="text-left px-3 py-2 text-[var(--color-text-muted)]">Score</th>
              <th className="text-left px-3 py-2 text-[var(--color-text-muted)]">Pass/Fail/Skip</th>
              <th className="text-left px-3 py-2 text-[var(--color-text-muted)]">Duration</th>
            </tr>
          </thead>
          <tbody>
            {(history || []).slice(0, 10).map((run) => (
              <tr key={run.run_id} className="border-b border-[var(--color-border)] last:border-0 hover:bg-[var(--color-hover)]">
                <td className="px-3 py-2 tabular-nums">{run.triggered_at?.slice(0, 10)}</td>
                <td className="px-3 py-2">{run.triggered_by}</td>
                <td className="px-3 py-2">
                  <span className={`inline-block px-1.5 py-0.5 rounded text-meta font-semibold ${
                    run.overall_score >= 80 ? 'bg-[color-mix(in_srgb,var(--color-status-success)_10%,transparent)] text-[var(--color-status-success)]' : 'bg-[color-mix(in_srgb,var(--color-status-warning)_10%,transparent)] text-[var(--color-status-warning)]'
                  }`}>
                    {run.overall_score}%
                  </span>
                </td>
                <td className="px-3 py-2 tabular-nums">
                  <span className="text-[var(--color-status-success)]">{run.cases_passed}</span>
                  {' / '}
                  <span className="text-[var(--color-status-error)]">{run.cases_failed}</span>
                  {' / '}
                  <span className="text-[var(--color-text-muted)]">{run.cases_skipped}</span>
                </td>
                <td className="px-3 py-2 tabular-nums">{run.duration_seconds?.toFixed(1)}s</td>
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
      <span className="text-meta uppercase tracking-wide text-[var(--color-text-muted)] ">{label}</span>
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
              className={`flex items-center gap-1 px-1.5 py-0.5 rounded text-meta border transition-colors ${
                isActive
                  ? 'bg-[var(--color-primary)] text-[var(--color-on-accent)] border-[var(--color-primary)]'
                  : 'bg-[var(--color-bg)] border-[var(--color-border)] text-[var(--color-text-secondary)]'
              } ${interactive ? 'cursor-pointer hover:border-[var(--color-primary)]' : ''}`}
            >
              <span>{e.key.replace(/_/g, ' ')}</span>
              <span data-testid={`chip-${testidPrefix}-${e.key}-count`} className={`font-semibold ${isActive ? 'text-[var(--color-on-accent)]' : 'text-[var(--color-text)]'}`}>{e.count}</span>
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

const LINE_NAMES = {
  quick: { en: 'Quick check', zh: '快速检查' },
  offline: { en: 'Offline tests', zh: '离线考题' },
} as const;

/** Which line a case belongs to (design eval-as-product §3.3, from eval_method). */
export function caseLine(c: { eval_method?: string }): 'quick' | 'offline' {
  return c.eval_method === 'behavior' || c.eval_method === 'llm' ? 'offline' : 'quick';
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
  // Collapsed line sections (default: all expanded). Orthogonal to filters.
  const [collapsedCats, setCollapsedCats] = useState<Set<string>>(new Set());
  const { i18n } = useTranslation();
  const lang: 'en' | 'zh' = i18n?.language?.startsWith('zh') ? 'zh' : 'en';

  // Breakdown counts are computed from the FULL set (not the filtered view) so
  // the user always sees the whole distribution to drill into. Hooks must run
  // before the early return, so guard against undefined data inside the memo.
  const breakdowns = useMemo(() => computeBreakdowns(gs?.cases ?? []), [gs?.cases]);

  if (gsError) return <ErrorState message="Failed to load the golden set. Is the backend running?" />;
  if (!gs) return <Loading />;

  // Client-side filtering
  const filtered = gs.cases.filter((c) => {
    const q = searchQuery.toLowerCase();
    const text = [c.id, c.title, c.purpose?.en, c.purpose?.zh].filter(Boolean).join(' ').toLowerCase();
    if (q && !text.includes(q)) return false;
    if (filterCategory && c.category !== filterCategory) return false;
    if (filterTier && c.tier !== filterTier) return false;
    if (filterStatus === 'passed' && c.last_result?.status !== 'passed') return false;
    if (filterStatus === 'failed' && c.last_result?.status !== 'failed') return false;
    if (filterStatus === 'skipped' && c.last_result?.status !== 'skipped') return false;
    if (filterStatus === 'timeout' && c.last_result?.status !== 'timeout') return false;
    return true;
  });

  // Group the FILTERED set by line (design eval-as-product §3.3): a programmatic
  // case is a quick check, a behavior / llm case an offline test. Filters and
  // grouping agree; an empty line is simply absent.
  const groupedByCat = (['quick', 'offline'] as const)
    .map((line) => [line, filtered.filter((c) => caseLine(c) === line)] as const)
    .filter(([, cases]) => cases.length > 0);
  const toggleCat = (cat: string) =>
    setCollapsedCats((prev) => {
      const next = new Set(prev);
      if (next.has(cat)) next.delete(cat); else next.add(cat);
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
            className="flex-1 max-w-[240px] px-2.5 py-1.5 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] text-small text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] outline-none focus:border-[var(--color-primary)]"
          />
          <select
            value={filterCategory}
            onChange={(e) => setFilterCategory(e.target.value)}
            className="px-2.5 py-1.5 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] text-small text-[var(--color-text)] outline-none cursor-pointer"
          >
            <option value="">All Categories</option>
            {(gs.categories || []).map((cat) => <option key={cat} value={cat}>{cat}</option>)}
          </select>
          <select
            value={filterStatus}
            onChange={(e) => setFilterStatus(e.target.value)}
            className="px-2.5 py-1.5 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] text-small text-[var(--color-text)] outline-none cursor-pointer"
          >
            <option value="">All Status</option>
            <option value="passed">Passed</option>
            <option value="failed">Failed</option>
            <option value="skipped">Skipped</option>
            <option value="timeout">Timed out</option>
          </select>
          <select
            data-testid="filter-tier"
            value={filterTier}
            onChange={(e) => setFilterTier(e.target.value)}
            className="px-2.5 py-1.5 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] text-small text-[var(--color-text)] outline-none cursor-pointer"
          >
            <option value="">All Tiers</option>
            {breakdowns.tier.map((t) => <option key={t.key} value={t.key}>{t.key}</option>)}
          </select>
          <div className="flex-1" />
          <span className="text-meta text-[var(--color-text-muted)]">
            {filtered.length}/{gs.total_cases} cases
          </span>
        </div>

        {/* Grouped case list — collapsible category sections. Each row shows an
            origin badge (product/private) + eval_method so shipped vs own cases and
            test type are distinguishable at a glance (fixes "傻傻分不清"). */}
        <div className="border border-[var(--color-border)] rounded-lg overflow-y-auto flex-1 min-h-0" data-testid="golden-set-groups">
          {groupedByCat.length === 0 && (
            <div className="px-3 py-8 text-center text-small text-[var(--color-text-muted)]">No cases match the current filters.</div>
          )}
          {groupedByCat.map(([cat, cases]) => {
            const collapsed = collapsedCats.has(cat);
            return (
              <div key={cat} data-testid={`line-group-${cat}`}>
                {/* Group header */}
                <button
                  onClick={() => toggleCat(cat)}
                  className="w-full flex items-center gap-2 px-3 py-2 bg-[var(--color-bg)] border-b border-[var(--color-border)] sticky top-0 z-10 hover:bg-[var(--color-hover)] transition-colors text-left"
                >
                  <span className="material-symbols-outlined icon-xs text-[var(--color-text-muted)]">
                    {collapsed ? 'chevron_right' : 'expand_more'}
                  </span>
                  <span className="font-semibold text-small">{LINE_NAMES[cat][lang]}</span>
                  <span className="px-1.5 py-0.5 rounded-full bg-[var(--color-hover)] text-meta text-[var(--color-text-muted)]">{cases.length}</span>
                </button>
                {/* Group rows */}
                {!collapsed && (
                  <table className="w-full text-small">
                    <tbody>
                      {cases.map((c) => (
                        <tr
                          key={c.id}
                          onClick={() => setSelectedCaseId(c.id)}
                          className={`border-b border-[var(--color-border)] last:border-0 hover:bg-[var(--color-hover)] cursor-pointer ${selectedCaseId === c.id ? 'bg-[var(--color-primary)]/5' : ''}`}
                        >
                          <td className="pl-9 pr-3 py-2" title={c.id}>
                            {c.purpose?.[lang] || c.purpose?.en || c.title}
                            {!c.purpose && (
                              <span className="ml-2 text-meta text-[var(--color-text-muted)]">{lang === 'zh' ? '未写目的' : 'no purpose written'}</span>
                            )}
                          </td>
                          <td className="px-3 py-2"><OriginBadge origin={c._origin} /></td>
                          <td className="px-3 py-2"><StatusBadge status={c.last_result?.status} /></td>
                          <td className="px-3 py-2" onClick={(e) => e.stopPropagation()}>
                            <button
                              onClick={() => setArchiveId(c.id)}
                              className="text-[var(--color-text-muted)] hover:text-[var(--color-status-error)] transition-colors"
                              title="Archive"
                            >
                              <span className="material-symbols-outlined icon-sm">archive</span>
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
            className="btn-md btn-primary hover:opacity-90 transition-opacity"
          >
            + Add Case
          </button>
          <button
            onClick={() => triggerRun.mutate({})}
            className="btn-md btn-secondary transition-colors"
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
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-[var(--color-scrim-strong)] p-4" onClick={() => { if (!deleteCase.isPending) { setArchiveId(null); setArchiveErr(false); } }}>
          <div className="w-full max-w-sm bg-[var(--color-card)] rounded-xl border border-[var(--color-border)] shadow-2xl p-6" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-body font-semibold mb-2">Archive golden case?</h3>
            <p className="text-small text-[var(--color-text-muted)] mb-4 break-all">
              <span className="font-mono">{archiveId}</span> will be archived (removed from the active set).
            </p>
            {archiveErr && (
              <p data-testid="eval-archive-error" className="text-small text-[var(--color-status-error)] mb-3">Could not archive — please try again.</p>
            )}
            <div className="flex justify-end gap-2">
              <button disabled={deleteCase.isPending} onClick={() => { setArchiveId(null); setArchiveErr(false); }} className="btn-md btn-secondary disabled:opacity-50">Cancel</button>
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
                className="btn-md btn-danger hover:opacity-90 disabled:opacity-50"
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

// A Governance Candidate (EVOLUTION.md GC row) that reached 3× evidence and whose
// Checked column says what it was checked against — the only kind of proposal.
interface GovProposal {
  id: string;
  gc_id: string;
  title?: string;
  occurrence_count: number;
  proposed_rule: string;
  checked?: string;
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
        <span className="px-1.5 py-0.5 text-meta font-semibold rounded bg-[color-mix(in_srgb,var(--color-primary)_10%,transparent)] text-[var(--color-primary)]">
          {p.gc_id}
        </span>
        <span className="text-meta text-[var(--color-text-muted)]">{p.occurrence_count}×</span>
      </div>
      <p className="text-small text-[var(--color-text)] mb-2">{p.proposed_rule}</p>
      {p.checked && (
        <p className="text-meta text-[var(--color-text-muted)] mb-2">{p.checked}</p>
      )}

      {/* Evidence — the actual correction excerpts behind the count. Without this
          the cards are indistinguishable and cannot be judged (Bug1 root cause). */}
      {evidence.length > 0 ? (
        <div className="mb-2 border-l-2 border-[var(--color-border)] pl-2 space-y-1">
          <div className="text-meta font-semibold text-[var(--color-text-muted)] uppercase tracking-wide">
            Evidence ({evidence.length})
          </div>
          {evidence.map((e, i) => (
            <p key={i} className="text-meta text-[var(--color-text-muted)] leading-snug">
              • {e}
            </p>
          ))}
        </div>
      ) : (
        <p className="mb-2 text-meta italic text-[var(--color-text-muted)]">
          No evidence recorded — judge with caution.
        </p>
      )}

      <div className="flex gap-1.5 items-center">
        <button
          onClick={() => onAct(p.id, 'accept')}
          disabled={pending}
          className="btn-sm btn-success disabled:opacity-50"
        >
          Accept
        </button>
        <button
          onClick={() => onAct(p.id, 'reject')}
          disabled={pending}
          className="btn-sm btn-danger disabled:opacity-50"
        >
          Reject
        </button>
        <button
          onClick={() => onAct(p.id, 'defer')}
          disabled={pending}
          className="btn-sm btn-secondary disabled:opacity-50"
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
        <h2 className="text-title font-semibold text-[var(--color-text)] flex items-center gap-1.5">
          <span className="material-symbols-outlined icon-sm text-[var(--color-primary)]">gavel</span>
          Governance Proposals
        </h2>
        <p className="text-small text-[var(--color-text-muted)] mt-1">
          Governance Candidates that reached 3× evidence and were checked against the
          existing rules. Accept or reject records your verdict in the candidate's row so it
          does not come back; accept never edits SOUL/AGENT/STEERING — write the rule with
          s_self-evolution.
        </p>
      </div>

      {isError && <div className="text-body text-[var(--color-status-error)]">Failed to load governance proposals. Is the backend running?</div>}

      {isLoading && !isError && <div className="text-body text-[var(--color-text-muted)]">Loading…</div>}

      {!isLoading && !isError && proposals.length === 0 && (
        <div className="text-body text-[var(--color-text-muted)] border border-dashed border-[var(--color-border)] rounded-lg p-6 text-center">
          No pending governance proposals. A Governance Candidate appears here once it has
          3× evidence and its Checked column is filled.
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
        <p className="text-body text-[var(--color-text-muted)]">
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
      <h3 className="text-small font-semibold text-[var(--color-text-muted)] uppercase tracking-wide mb-4">Recent Runs</h3>
      <div className="grid grid-cols-3 gap-3 mb-8" data-testid="recent-runs">
        {recent.map((r) => {
          const score = r.overall_score ?? 0;
          const sc = score >= 80 ? 'var(--color-status-success)' : score >= 60 ? 'var(--color-status-warning)' : 'var(--color-status-error)';
          return (
            <button
              key={r.run_id}
              onClick={() => setDetailRunId(r.run_id)}
              className="p-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] hover:border-[var(--color-primary)] transition-colors text-left"
            >
              <div className="flex items-center justify-between mb-1">
                <span className="text-meta tabular-nums text-[var(--color-text-muted)]">{r.triggered_at?.slice(0, 10)}</span>
                <span className="text-body tabular-nums font-semibold" style={{ color: sc }}>{Math.round(score)}%</span>
              </div>
              <div className="text-meta text-[var(--color-text-muted)]">{r.triggered_by}</div>
              <div className="flex gap-2 mt-1 text-meta">
                <span className="text-[var(--color-status-success)]">{r.cases_passed ?? 0}✓</span>
                <span className="text-[var(--color-status-error)]">{r.cases_failed ?? 0}✗</span>
                <span className="text-[var(--color-text-muted)]">{r.cases_skipped ?? 0}skip</span>
              </div>
            </button>
          );
        })}
      </div>

      <h3 className="text-small font-semibold text-[var(--color-text-muted)] uppercase tracking-wide mb-4">Overall Score Trend</h3>
      <div className="p-4 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] mb-8">
        <Sparkline
          values={runs.map(r => r.overall_score ?? 0)}
          dates={runs.map(r => r.triggered_at?.slice(0, 10) ?? '')}
          axis
          height={48}
          color="var(--color-primary)"
        />
        <div className="flex justify-between text-meta text-[var(--color-text-muted)] mt-1 tabular-nums">
          <span>{runs[0]?.triggered_at?.slice(0, 10)}</span>
          <span>{runs[runs.length - 1]?.triggered_at?.slice(0, 10)}</span>
        </div>
      </div>

      <h3 className="text-small font-semibold text-[var(--color-text-muted)] uppercase tracking-wide mb-4">Per-Dimension Sparklines</h3>
      <div className="grid grid-cols-2 gap-4">
        {allDims.map((dim) => {
          const scores = runs.map(r => r.dimensions?.[dim] ?? 0);
          const latest = scores[scores.length - 1] ?? 0;
          const color = latest >= 80 ? 'var(--color-status-success)' : latest >= 60 ? 'var(--color-status-warning)' : 'var(--color-status-error)';
          return (
            <div key={dim} className="p-4 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)]">
              <div className="flex items-center justify-between mb-2">
                <div className="text-small text-[var(--color-text-muted)]">{dim.replace(/_/g, ' ')}</div>
                <div className="text-body font-mono font-semibold" style={{ color }}>{latest}%</div>
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

  const STATUS_ORDER = ['failed', 'error', 'timeout', 'skipped', 'passed'];
  const grouped = useMemo(() => {
    const m = new Map<string, RunCaseResult[]>();
    for (const c of run?.cases ?? []) {
      const k = c.status || 'unknown';
      (m.get(k) ?? m.set(k, []).get(k)!).push(c);
    }
    return [...m.entries()].sort((a, b) => STATUS_ORDER.indexOf(a[0]) - STATUS_ORDER.indexOf(b[0]));
  }, [run?.cases]);

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-[var(--color-scrim-medium)]" onClick={onClose}>
      <div className="w-[460px] h-full bg-[var(--color-card)] border-l border-[var(--color-border)] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        <div className="sticky top-0 bg-[var(--color-card)] border-b border-[var(--color-border)] px-4 py-3 flex items-center justify-between">
          <div>
            <div className="text-small font-mono font-semibold">{runId}</div>
            {run && <div className="text-meta text-[var(--color-text-muted)]">{run.triggered_at?.slice(0, 19).replace('T', ' ')} · {run.triggered_by}</div>}
          </div>
          <button onClick={onClose} className="text-[var(--color-text-muted)] hover:text-[var(--color-text)]">
            <span className="material-symbols-outlined icon-sm">close</span>
          </button>
        </div>
        {isLoading || !run ? <Loading /> : (
          <div className="p-4">
            {/* Run summary */}
            <div className="flex gap-3 mb-4 text-small">
              <span className="tabular-nums font-semibold" style={{ color: (run.overall_score ?? 0) >= 80 ? 'var(--color-status-success)' : 'var(--color-status-warning)' }}>{Math.round(run.overall_score ?? 0)}%</span>
              <span className="text-[var(--color-status-success)]">{run.cases_passed ?? 0} passed</span>
              <span className="text-[var(--color-status-error)]">{run.cases_failed ?? 0} failed</span>
              <span className="text-[var(--color-text-muted)]">{run.cases_skipped ?? 0} skipped</span>
              {(run.cases_error ?? 0) > 0 && <span className="text-[var(--color-status-warning)]">{run.cases_error} error</span>}
              {(run.cases_timeout ?? 0) > 0 && <span className="text-[var(--color-status-warning)]">{run.cases_timeout} timed out</span>}
            </div>
            {/* Per-case grouped by status */}
            {grouped.map(([status, cases]) => (
              <div key={status} className="mb-4" data-testid={`run-status-${status}`}>
                <div className="flex items-center gap-2 mb-1.5">
                  <StatusBadge status={status} />
                  <span className="text-meta text-[var(--color-text-muted)]">{cases.length}</span>
                </div>
                <div className="space-y-1">
                  {cases.map((c) => (
                    <div key={c.id} className="flex items-center gap-2 px-2 py-1 rounded bg-[var(--color-bg)] text-meta">
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
        <h2 className="text-title font-semibold text-[var(--color-text)]">Context Health</h2>
        <p className="text-small text-[var(--color-text-muted)] mt-1">
          DDD & Memory freshness — auto-refresh activity, staleness signals, and pending decisions.
          {data.weeks_available > 0 && ` Showing ${data.weeks_available} week(s) of history.`}
        </p>
      </div>

      {!hasActivity && (
        <div className="text-center py-12 text-[var(--color-text-muted)]">
          <span className="material-symbols-outlined icon-xl mb-2 block opacity-40">check_circle</span>
          <p className="text-body">No context drift detected. Everything is fresh.</p>
          <p className="text-small mt-1 opacity-70">Auto-refresh runs on every code commit + every 30 minutes.</p>
        </div>
      )}

      {/* Semantic Drift (ddd-self-audit findings + at-risk golden cases) — a TRUTH
          signal (a DDD prose claim contradicts itself / the code), distinct from the
          mtime-based Staleness below. Each finding lists the docs it hits; at-risk
          cases are golden cases whose affected_by depends on a drifted doc. */}
      {driftFindings.length > 0 && (
        <section>
          <h3 className="text-body text-[var(--color-text)] mb-2 flex items-center gap-1.5">
            <span className="material-symbols-outlined icon-xs text-[var(--color-status-error)]">rule</span>
            Semantic Drift ({driftFindings.length})
            {drift?.report_date && (
              <span className="text-meta text-[var(--color-text-muted)] font-normal">
                from self-audit {drift.report_date}
              </span>
            )}
          </h3>
          <div className="border border-[color-mix(in_srgb,var(--color-status-error)_20%,transparent)] rounded-lg overflow-hidden">
            <table className="w-full text-small">
              <thead className="bg-[color-mix(in_srgb,var(--color-status-error)_5%,transparent)]">
                <tr>
                  <th className="text-left px-3 py-2 text-[var(--color-text-muted)]">Project</th>
                  <th className="text-left px-3 py-2 text-[var(--color-text-muted)]">Doc(s)</th>
                  <th className="text-left px-3 py-2 text-[var(--color-text-muted)]">What's Stale</th>
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
            <p className="text-meta text-[var(--color-text-muted)] mt-1.5">
              <span className="material-symbols-outlined icon-xs align-middle text-[var(--color-status-warning)]">warning</span> {drift!.at_risk_cases.length} eval case(s) at risk (affected_by a drifted doc):{' '}
              <span className="font-mono">{drift!.at_risk_cases.map(c => c.case_id).join(', ')}</span>
            </p>
          )}
          <p className="text-meta text-[var(--color-text-muted)] mt-1 italic">
            Semantic contradictions found by the weekly DDD self-audit. Fix via chat: "s_persist … correct the claim".
          </p>
        </section>
      )}

      {/* Staleness Signals */}
      {data.staleness.length > 0 && (
        <section>
          <h3 className="text-body text-[var(--color-text)] mb-2 flex items-center gap-1.5">
            <span className="material-symbols-outlined icon-xs text-[var(--color-status-warning)]">warning</span>
            Stale Documents ({data.staleness.length})
          </h3>
          <div className="border border-[color-mix(in_srgb,var(--color-status-warning)_20%,transparent)] rounded-lg overflow-hidden">
            <table className="w-full text-small">
              <thead className="bg-[color-mix(in_srgb,var(--color-status-warning)_5%,transparent)]">
                <tr>
                  <th className="text-left px-3 py-2 text-[var(--color-text-muted)]">Project</th>
                  <th className="text-left px-3 py-2 text-[var(--color-text-muted)]">Document</th>
                  <th className="text-right px-3 py-2 text-[var(--color-text-muted)]">Days Stale</th>
                  <th className="text-right px-3 py-2 text-[var(--color-text-muted)]">Recent Commits</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--color-border)]">
                {data.staleness.map((s, i) => (
                  <tr key={i} className="hover:bg-[var(--color-hover)]">
                    <td className="px-3 py-2 text-[var(--color-text)]">{s.project}</td>
                    <td className="px-3 py-2 text-[var(--color-text-muted)]">{s.doc}</td>
                    <td className="px-3 py-2 text-right tabular-nums text-[var(--color-status-warning)]">{s.days_stale}d</td>
                    <td className="px-3 py-2 text-right tabular-nums">{s.recent_commits}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-meta text-[var(--color-text-muted)] mt-1.5 italic">
            Tip: Ask in chat "refresh AIDLC TECH.md" or "update DDD docs" to resolve.
          </p>
        </section>
      )}

      {/* Pending Proposals (Layer 3) */}
      {data.pending_proposals.length > 0 && (
        <section>
          <h3 className="text-body text-[var(--color-text)] mb-2 flex items-center gap-1.5">
            <span className="material-symbols-outlined icon-xs text-[var(--color-primary)]">pending_actions</span>
            Pending Decisions ({data.pending_proposals.length})
          </h3>
          <div className="space-y-2">
            {data.pending_proposals.map((p) => (
              <div key={p.id} className="border border-[var(--color-border)] rounded-lg p-3 hover:bg-[var(--color-hover)]">
                <div className="flex items-center justify-between">
                  <span className="text-small text-[var(--color-text)]">
                    {p.target_doc} → {p.target_section}
                  </span>
                  <span className="text-meta text-[var(--color-text-muted)]">
                    {new Date(p.created_at).toLocaleDateString()}
                  </span>
                </div>
                <p className="text-small text-[var(--color-text-muted)] mt-1 line-clamp-2">{p.content}</p>
                <p className="text-meta text-[var(--color-text-muted)] mt-1.5 italic">
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
          <h3 className="text-body text-[var(--color-text)] mb-2 flex items-center gap-1.5">
            <span className="material-symbols-outlined icon-xs text-[var(--color-status-success)]">auto_fix_high</span>
            Auto-Applied Fixes ({data.refresh_log.length})
          </h3>
          <div className="border border-[var(--color-border)] rounded-lg overflow-hidden">
            <table className="w-full text-small">
              <thead className="bg-[var(--color-bg)]">
                <tr>
                  <th className="text-left px-3 py-2 text-[var(--color-text-muted)]">Date</th>
                  <th className="text-left px-3 py-2 text-[var(--color-text-muted)]">Target</th>
                  <th className="text-left px-3 py-2 text-[var(--color-text-muted)]">Change</th>
                  <th className="text-left px-3 py-2 text-[var(--color-text-muted)]">Layer</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--color-border)]">
                {data.refresh_log.slice(0, 20).map((entry, i) => (
                  <tr key={i} className="hover:bg-[var(--color-hover)]">
                    <td className="px-3 py-2 text-[var(--color-text-muted)] whitespace-nowrap">
                      {new Date(entry.timestamp).toLocaleDateString()}
                    </td>
                    <td className="px-3 py-2 text-[var(--color-text)] font-mono text-meta truncate max-w-[200px]">
                      {entry.target}
                    </td>
                    <td className="px-3 py-2">
                      <code className="text-[var(--color-status-error)] line-through text-meta">{entry.old}</code>
                      {' → '}
                      <code className="text-[var(--color-status-success)] text-meta">{entry.new}</code>
                    </td>
                    <td className="px-3 py-2">
                      <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-meta ${
                        entry.layer === 1
                          ? 'bg-[color-mix(in_srgb,var(--color-status-success)_10%,transparent)] text-[var(--color-status-success)]'
                          : 'bg-[color-mix(in_srgb,var(--color-primary)_10%,transparent)] text-[var(--color-primary)]'
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
            <p className="text-meta text-[var(--color-text-muted)] mt-1">
              Showing 20 of {data.refresh_log.length} entries.
            </p>
          )}
        </section>
      )}
    </div>
  );
}

// ─── The three lines ─────────────────────────────────────────────────────────
// What the eval is made of, said once (design eval-as-product §3.3): the quick
// check, the offline tests and online scoring. Each row: one sentence, when it
// last ran (and when the next full run is due), and what it found. Times and
// counts come from /api/eval/health, never from text written here.

export const EVAL_LINES = [
  { id: 'quick' as const, name: { en: 'Quick check', zh: '快速检查' },
    what: { en: 'Fixed inputs check the product\'s key functions. Deterministic, no model, seconds.',
            zh: '用固定输入检查产品的关键功能。结果确定，不调模型，几秒跑完。' },
    broken: { en: 'A failure means a product function is broken.', zh: '失败意味着产品某个功能坏了。' } },
  { id: 'full' as const, name: { en: 'Offline tests', zh: '离线考题' },
    what: { en: 'A real agent takes fixed tests in a sample workspace; checks its judgment and its way of working.',
            zh: '真实 agent 在一个示例工作区里做固定的题，看它的判断和做法对不对。' },
    broken: { en: 'A failure means the agent\'s judgment slipped; find which rule.', zh: '失败意味着 agent 的判断力退步了，先看是哪条规则。' } },
  { id: 'online' as const, name: { en: 'Online scoring', zh: '线上评分' },
    what: { en: 'Scores your real sessions from the past week. No expected answer.',
            zh: '给你过去一周的真实会话打分，没有标准答案。' },
    broken: { en: 'A rising rate means quality in real use is dropping; open those sessions.', zh: '失败率上升意味着真实使用质量在下降，去看那几个会话。' } },
];

const when = (iso: string | null | undefined) => (iso ? iso.slice(0, 16).replace('T', ' ') : null);

export function EvalLines({ lines }: { lines?: EvalLinesData }) {
  const { i18n } = useTranslation();
  const lang: 'en' | 'zh' = i18n?.language?.startsWith('zh') ? 'zh' : 'en';
  const zh = lang === 'zh';
  if (!lines) return null;
  const never = zh ? '还没运行过' : 'not run yet';
  const result = {
    quick: lines.quick.total ? `${lines.quick.passed ?? 0}/${lines.quick.total} ${zh ? '通过' : 'passed'}` : '—',
    full: lines.full.score != null ? `${lines.full.score}%` : '—',
    online: lines.online.sessions_checked != null ? `${lines.online.sessions_checked} ${zh ? '个会话' : 'sessions'}` : '—',
  };
  const next = { quick: null, full: lines.full.next_run, online: lines.online.next_run };
  return (
    <section className="mb-8" data-testid="eval-lines">
      <ul className="space-y-4">
        {EVAL_LINES.map((l) => (
          <li key={l.id} data-testid={`eval-line-${l.id}`} className="grid grid-cols-[10rem_1fr_auto] gap-x-4">
            <span className="text-small font-semibold">{l.name[lang]}</span>
            <div>
              <div className="text-small">{l.what[lang]}</div>
              <div className="text-meta text-[var(--color-text-muted)]">
                {(zh ? '上次：' : 'Last: ') + (when(lines[l.id].last_run) ?? never)}
                {next[l.id] && ` · ${zh ? '下次：' : 'next: '}${when(next[l.id])}`}
                {' · '}{l.broken[lang]}
              </div>
            </div>
            <span className="text-small tabular-nums">{result[l.id]}</span>
          </li>
        ))}
      </ul>
      <p className="text-meta text-[var(--color-text-muted)] mt-3">
        {zh
          ? `golden set：产品 ${lines.golden_set.product} 条、你自己的 ${lines.golden_set.private} 条（上限 ${lines.golden_set.total_cap}）`
          : `Golden set: ${lines.golden_set.product} product, ${lines.golden_set.private} yours (limit ${lines.golden_set.total_cap})`}
      </p>
    </section>
  );
}

// ─── Suggested follow-up ─────────────────────────────────────────────────────
// What needs the user's attention, at the top of Overview. The list is decided by
// the backend (the HTML report shows the same one); this only renders it: one line
// per item, the high ones in the error colour, why + what to do muted beneath.

export function FollowUps({ items }: { items?: FollowUp[] }) {
  const { i18n } = useTranslation();
  const lang: 'en' | 'zh' = i18n?.language?.startsWith('zh') ? 'zh' : 'en';
  if (!items) return null;
  return (
    <section className="mb-6" data-testid="eval-follow-ups">
      <h3 className="text-small font-semibold mb-2">
        {lang === 'zh' ? '需要你注意的事' : 'Suggested follow-up'}
      </h3>
      {items.length === 0 ? (
        <p className="text-small text-[var(--color-text-muted)]">
          {lang === 'zh' ? '没有需要处理的事' : 'Nothing needs handling'}
        </p>
      ) : (
        <ul className="space-y-2.5">
          {items.map((f, i) => (
            <li key={`${f.rule}-${i}`} data-severity={f.severity}>
              <div className={`text-small ${f.severity === 'high' ? 'font-semibold text-[var(--color-status-error)]' : ''}`}>
                {f.what[lang]}
              </div>
              <div className="text-meta text-[var(--color-text-muted)]">
                {[f.why, f.action[lang]].filter(Boolean).join(' · ')}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

// ─── Reports Tab ─────────────────────────────────────────────────────────────

interface EvalReport {
  filename: string;
  sizeBytes: number;
  modified: number;
  trigger?: string;
  routine?: boolean;
}

function useEvalReports() {
  return useQuery<EvalReport[]>({
    queryKey: ['eval-reports'],
    queryFn: async () => (await api.get<EvalReport[]>('/eval/reports')).data,
    staleTime: 60_000,
  });
}

export function ReportsTab() {
  const { data: allReports, isLoading, isError } = useEvalReports();
  // Scheduled / deploy reports by default; hand-run and debug ones behind "show all".
  const [showAll, setShowAll] = useState(false);
  const routine = (allReports ?? []).filter((r) => r.routine);
  const reports = showAll ? allReports : routine;
  const hidden = (allReports?.length ?? 0) - routine.length;

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
  const toggle = hidden > 0 && (
    <button onClick={() => setShowAll((v) => !v)} className="text-meta text-[var(--color-primary)] hover:underline"
            data-testid="reports-show-all">
      {showAll ? 'Show scheduled only' : `Show all (${hidden} hand-run)`}
    </button>
  );
  if (!reports || reports.length === 0) {
    return (
      <div className="max-w-5xl mx-auto p-6">
        <p className="text-body text-[var(--color-text-muted)]">
          No scheduled eval reports yet. The full run writes one every time it runs.
        </p>
        {toggle && <div className="mt-2">{toggle}</div>}
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto p-6">
      <div className="flex items-baseline justify-between mb-4">
        <h3 className="text-small font-semibold text-[var(--color-text-muted)] uppercase tracking-wide">
          Eval Reports
        </h3>
        {toggle}
      </div>
      <div className="border border-[var(--color-border)] rounded-lg overflow-hidden">
        <table className="w-full text-small">
          <thead>
            <tr className="bg-[var(--color-bg)] border-b border-[var(--color-border)]">
              <th className="text-left px-4 py-2.5 text-[var(--color-text-muted)]">Report</th>
              <th className="text-left px-4 py-2.5 text-[var(--color-text-muted)]">Date</th>
              <th className="text-right px-4 py-2.5 text-[var(--color-text-muted)]">Size</th>
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
                      <span className="material-symbols-outlined icon-xs text-[var(--color-primary)]">description</span>
                      <span className="">{label}</span>
                    </div>
                  </td>
                  <td className="px-4 py-2.5 text-[var(--color-text-muted)] tabular-nums">{dateStr}</td>
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


// ─── Online Tab ───────────────────────────────────────────────────────────────
// Online scoring (backend jobs/handlers/session_quality.py, GET /eval/session-quality):
// every week, the owner's sessions from the last 7 days are checked against the
// rules (O1–O3, every session) and judged (O4–O5, a sample). There is no expected
// answer. The decision here: which rule is slipping in real use, and in which
// sessions. So one row per check, its failure rate against last week, and the
// example sessions under it; everything else stays out.

interface OnlineExample {
  session_id?: string;
  title?: string;
  snippet?: string;
  reason?: string;
}
interface OnlineCheck {
  checked?: number;
  failed?: number;
  rate?: number | null;
  examples?: OnlineExample[];
}
interface OnlineLowDetail {
  session_id?: string;
  title?: string;
  goal_score?: number;
  tool_score?: number;
  dimension?: string;
  reason?: string;
}
export interface OnlineScoring {
  last_run: string | null;
  window_days?: number | null;
  sessions_checked?: number;
  checks?: Record<string, OnlineCheck>;
  low_details?: OnlineLowDetail[];
  sampler_error?: string | null;
  judge_error?: string | null;
  trend?: { week?: string; rates?: Record<string, number> }[];
}

/** ISO-week labels (2026-W43 → 2026-W44) exactly one week apart. */
export function consecutiveWeeks(before?: string, after?: string): boolean {
  const monday = (label?: string) => {
    const m = /^(\d{4})-W(\d{2})$/.exec(label ?? '');
    if (!m) return null;
    const jan4 = new Date(Date.UTC(Number(m[1]), 0, 4));
    const day = (jan4.getUTCDay() + 6) % 7;
    return jan4.getTime() - day * 86_400_000 + (Number(m[2]) - 1) * 7 * 86_400_000;
  };
  const b = monday(before), a = monday(after);
  return b != null && a != null && a - b === 7 * 86_400_000;
}

function useSessionQuality() {
  return useQuery<OnlineScoring>({
    queryKey: ['eval-session-quality'],
    queryFn: async () => (await api.get<OnlineScoring>('/eval/session-quality')).data,
    staleTime: 60_000,
  });
}

const ONLINE_CHECKS: { id: string; en: string; zh: string }[] = [
  { id: 'O1_LANGUAGE', en: 'Replies in the language you asked in', zh: '用你提问的语言回答' },
  { id: 'O2_SILENT_TOOLS', en: 'No chatter before tool calls', zh: '工具调用前不写多余的字' },
  { id: 'O3_CORRECTIONS', en: 'Sessions where you corrected it', zh: '被你纠正过的会话' },
  { id: 'O4_GOAL', en: 'Delivered what you asked for (judged, sample)', zh: '交付了你要的东西（judge 抽样）' },
  { id: 'O5_TOOLS', en: 'Chose sensible tools (judged, sample)', zh: '工具选得合理（judge 抽样）' },
];

const pct = (r?: number | null) => (r == null ? '—' : `${Math.round(r * 100)}%`);

export function SessionQualityTab() {
  const { data: online, isLoading, isError } = useSessionQuality();
  const { i18n } = useTranslation();
  const lang: 'en' | 'zh' = i18n?.language?.startsWith('zh') ? 'zh' : 'en';
  const zh = lang === 'zh';

  if (isError) return <ErrorState message="Failed to load online scoring. Is the backend running?" />;
  if (isLoading) return <Loading />;

  const trend = online?.trend ?? [];
  // "last week" only when the entry before this one is the week before
  const lastWeek = trend.length >= 2 && consecutiveWeeks(trend[trend.length - 2].week, trend[trend.length - 1].week)
    ? trend[trend.length - 2].rates ?? {} : {};
  const low = online?.low_details ?? [];
  let status: string | null = null;
  if (!online?.last_run) status = zh ? '还没有运行过。它每周运行一次，也可以在 Jobs & Runs 里手动运行。' : 'Not run yet. It runs weekly; you can also run it from Jobs & Runs.';
  else if (online.sampler_error) status = (zh ? '这周没评成：' : 'This week did not run: ') + online.sampler_error;
  else if (!online.sessions_checked) status = zh ? '过去 7 天没有可评的会话。' : 'No sessions to check in the last 7 days.';

  return (
    <div className="max-w-5xl mx-auto p-6" data-testid="online-tab">
      <h3 className="text-small font-semibold mb-1">{zh ? '线上评分' : 'Online'}</h3>
      <p className="text-meta text-[var(--color-text-muted)] mb-5">
        {zh ? '每周检查你过去 7 天的真实会话，没有标准答案，看规则有没有守住、事情有没有办成。'
            : 'Every week, checks your real sessions from the last 7 days. There is no expected answer: it looks at whether rules held and tasks got done.'}
        {online?.last_run && !online.sampler_error && (
          <span className="tabular-nums"> {zh ? '上次' : 'Last run'}: {online.last_run.slice(0, 16).replace('T', ' ')} · {online.sessions_checked ?? 0} {zh ? '个会话' : 'sessions'}</span>
        )}
      </p>
      {status ? (
        <p className="text-small text-[var(--color-text-muted)]" data-testid="online-status">{status}</p>
      ) : (
        <ul className="space-y-4">
          {ONLINE_CHECKS.map((c) => {
            const check = online?.checks?.[c.id] ?? {};
            const before = lastWeek[c.id];
            const worse = check.rate != null && before != null && check.rate > before;
            return (
              <li key={c.id} data-testid={`online-check-${c.id}`}>
                <div className="flex items-baseline gap-3 text-small">
                  <span className={worse ? 'font-semibold text-[var(--color-status-error)]' : ''}>{c[lang]}</span>
                  <span className="ml-auto tabular-nums">{pct(check.rate)}</span>
                  <span className="text-meta text-[var(--color-text-muted)] tabular-nums w-28 text-right">
                    {check.checked ? `${check.failed ?? 0}/${check.checked}` : (zh ? '无数据' : 'no data')}
                    {before != null && ` · ${zh ? '上周' : 'last'} ${pct(before)}`}
                  </span>
                </div>
                {online?.judge_error && (c.id === 'O4_GOAL' || c.id === 'O5_TOOLS') && (
                  <div className="text-meta text-[var(--color-status-warning)] mt-1 pl-3">
                    {(zh ? '这周 judge 没跑成：' : 'The judge did not run this week: ') + online.judge_error}
                  </div>
                )}
                {(check.examples ?? []).map((e, i) => (
                  <div key={`${e.session_id}-${i}`} className="text-meta text-[var(--color-text-muted)] mt-1 pl-3">
                    <span className="text-[var(--color-text)]">{e.title || e.session_id}</span>
                    {e.snippet && <> — “{e.snippet}”</>}
                    {e.reason && <> — {e.reason}</>}
                  </div>
                ))}
              </li>
            );
          })}
        </ul>
      )}
      {!status && low.length > 0 && (
        <div className="mt-8">
          <h4 className="text-small font-semibold mb-2">{zh ? '低分会话' : 'Low-scoring sessions'}</h4>
          <ul className="space-y-2">
            {low.map((l, i) => (
              <li key={l.session_id || i} className="text-small">
                <span>{l.title || l.session_id || '—'}</span>
                <span className="text-meta text-[var(--color-text-muted)] tabular-nums">
                  {' '}· {zh ? '目标' : 'goal'} {l.goal_score?.toFixed(2) ?? '—'} · {zh ? '工具' : 'tools'} {l.tool_score?.toFixed(2) ?? '—'}
                </span>
                {l.reason && <div className="text-meta text-[var(--color-text-muted)]">{l.reason}</div>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}


// ─── Guide Tab ────────────────────────────────────────────────────────────────
// How the eval works, in words that do not go stale (design eval-as-product §3.6):
// the text describes mechanisms only. When each line last ran and when the next
// full run is due come from /api/eval/health (EvalService.get_lines), the same
// data the Overview shows. No time, weekday or count is written here
// (__tests__/EvalDashboard.guide.test.tsx checks that).

const GUIDE = {
  title: { en: 'How the eval works', zh: 'eval 是怎么工作的' },
  intro: {
    en: 'SwarmAI checks itself on your machine, in three ways. Each answers a different question, and each lists what needs your attention at the top of Overview and of every report.',
    zh: 'SwarmAI 在你的机器上用三种方式检查自己。三者回答的问题不同，需要你处理的事都列在 Overview 和每份报告的最上面。',
  },
  cadence: {
    quick: { en: 'At the first session of each day, and whenever you press Run Canary.', zh: '每天第一次会话时，以及你点 Run Canary 时。' },
    full: { en: 'The job fires weekly and really runs once two weeks have passed since the last full run. It also writes the HTML report.', zh: '任务每周触发一次，距上次全量满两周时才真正运行，同时生成 HTML 报告。' },
    online: { en: 'Weekly, over your sessions from the past week.', zh: '每周运行，看你过去一周的会话。' },
  },
  casesTitle: { en: 'Where cases come from', zh: 'case 从哪里来' },
  cases: {
    en: [
      'Product cases ship with SwarmAI and are the same on every install. They run on a temporary workspace and data folder, never on your own data, and they are read-only here: changing one is a code change to templates/eval/golden_set.yaml.',
      'Your own cases live in your workspace (Eval/golden_set.private.yaml). Add one with + Add Case: it needs a purpose (what it protects and what breaks for you if it fails). A quick-check case also needs a negative command that must fail, so a case that cannot fail is refused.',
      'Retire a case with the archive button; it stays on disk and can be restored. The golden set has a size limit: when it is full, retire a case before adding one.',
      'Nothing creates cases automatically.',
    ],
    zh: [
      '产品 case 随 SwarmAI 发布，每台机器都一样。它们在临时工作区和临时数据目录里跑，不碰你的数据；在这里只读，要改就是改 templates/eval/golden_set.yaml 的代码改动。',
      '你自己的 case 放在工作区的 Eval/golden_set.private.yaml。用 + Add Case 添加：必须写目的（守住什么、坏了对你意味着什么）。快速检查类的 case 还要一条必须失败的反例命令，不可能失败的 case 会被拒绝。',
      '用归档按钮退役 case，文件仍在磁盘上，可以恢复。golden set 有上限：满了就先退役一条再加。',
      '不会自动生成 case。',
    ],
  },
  statusTitle: { en: 'Reading a result', zh: '怎么看结果' },
  status: {
    en: [
      'passed / failed: the check ran and gave its verdict.',
      'timed out: the check did not finish. It is not scored (often the machine was busy), but it keeps the quick check red, and twice in a row it is listed as possibly stuck.',
      'error: the check could not run (for example the judge was unavailable). Not scored.',
      'skipped: this run did not include the case (the quick check skips the offline tests).',
    ],
    zh: [
      'passed / failed：检查跑完并给出了结论。',
      'timed out：检查没跑完。不计分（常见原因是机器忙），但快速检查仍判红；连续两次会被列为可能卡住。',
      'error：检查没能运行（比如 judge 不可用），不计分。',
      'skipped：这次运行不包含这条 case（快速检查不跑离线考题）。',
    ],
  },
};

export function GuideTab() {
  const [t, setT] = useState<'en' | 'zh'>('en');
  const { data: health } = useEvalHealth();
  const lines = health?.lines;
  const next = lines ? { quick: null, full: lines.full.next_run, online: lines.online.next_run } : null;
  return (
    <div className="max-w-3xl mx-auto p-6" data-testid="eval-guide">
      <div className="flex items-center justify-between mb-2">
        <h2 className="text-body font-semibold">{GUIDE.title[t]}</h2>
        <div role="radiogroup" className="flex gap-1">
          {(['en', 'zh'] as const).map((l) => (
            <button key={l} role="radio" aria-checked={t === l} onClick={() => setT(l)}
              className={`px-2.5 py-1 rounded text-meta transition-colors ${t === l ? 'bg-[var(--color-primary)] text-[var(--color-on-accent)]' : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'}`}>
              {l === 'en' ? 'EN' : '中文'}
            </button>
          ))}
        </div>
      </div>
      <p className="text-small text-[var(--color-text-muted)] mb-6">{GUIDE.intro[t]}</p>

      <ul className="space-y-5 mb-8">
        {EVAL_LINES.map((l) => (
          <li key={l.id} data-testid={`guide-line-${l.id}`}>
            <div className="text-small font-semibold">{l.name[t]}</div>
            <div className="text-small">{l.what[t]} {l.broken[t]}</div>
            <div className="text-meta text-[var(--color-text-muted)]">
              {GUIDE.cadence[l.id][t]}
              {next?.[l.id] && ` ${t === 'zh' ? '下次：' : 'Next: '}${next[l.id]!.slice(0, 16).replace('T', ' ')}`}
            </div>
          </li>
        ))}
      </ul>

      <h3 className="text-small font-semibold mb-2">{GUIDE.casesTitle[t]}</h3>
      <ul className="list-disc pl-5 space-y-1.5 text-small mb-8">
        {GUIDE.cases[t].map((x) => <li key={x}>{x}</li>)}
      </ul>

      <h3 className="text-small font-semibold mb-2">{GUIDE.statusTitle[t]}</h3>
      <ul className="list-disc pl-5 space-y-1.5 text-small">
        {GUIDE.status[t].map((x) => <li key={x}>{x}</li>)}
      </ul>
    </div>
  );
}


// ─── Shared Components ────────────────────────────────────────────────────────

function StatusBadge({ status }: { status?: string }) {
  if (!status) return <span className="text-meta text-[var(--color-text-muted)]">—</span>;
  const cls = status === 'passed' ? 'bg-[color-mix(in_srgb,var(--color-status-success)_10%,transparent)] text-[var(--color-status-success)]'
    : status === 'failed' ? 'bg-[color-mix(in_srgb,var(--color-status-error)_10%,transparent)] text-[var(--color-status-error)]'
    : status === 'timeout' || status === 'error' ? 'bg-[color-mix(in_srgb,var(--color-status-warning)_10%,transparent)] text-[var(--color-status-warning)]'
    : 'bg-[var(--color-hover)] text-[var(--color-text-muted)]';
  return <span className={`inline-block px-1.5 py-0.5 rounded text-meta font-semibold uppercase ${cls}`}>{status}</span>;
}

// Distinguishes curated public cases (ship in the repo) from private instance
// cases (gitignored). The single most-requested distinction for the golden set.
function OriginBadge({ origin }: { origin?: string }) {
  if (!origin) return <span className="text-meta text-[var(--color-text-muted)]">—</span>;
  const isProduct = origin === 'product';
  const cls = isProduct ? 'bg-[color-mix(in_srgb,var(--color-status-success)_10%,transparent)] text-[var(--color-status-success)]' : 'bg-[var(--color-hover)] text-[var(--color-text-muted)]';
  return (
    <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-meta font-semibold ${cls}`} title={isProduct ? 'Product — ships with SwarmAI, the same on every install' : 'Private — your own case, kept on this machine'}>
      <span className="material-symbols-outlined icon-xs">{isProduct ? 'verified' : 'lock'}</span>
      {origin}
    </span>
  );
}

function Loading() {
  return (
    <div className="flex items-center justify-center flex-1 min-h-[10rem] text-body text-[var(--color-text-muted)]">
      Loading eval data...
    </div>
  );
}

function ErrorState({ message }: { message: string }) {
  return (
    <div className="flex items-center justify-center flex-1 min-h-[10rem] text-body text-[var(--color-status-error)]">
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
        <div className="flex flex-col justify-between text-meta text-[var(--color-text-muted)] tabular-nums py-0.5 shrink-0" style={{ height }}>
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
        <span className="text-small font-semibold font-mono">{caseId}</span>
        <button onClick={onClose} className="icon-btn hover:bg-[var(--color-hover)]">
          <span className="material-symbols-outlined icon-sm">close</span>
        </button>
      </div>

      {isLoading && <Loading />}
      {detail && (
        <div className="p-4 space-y-4 text-small">
          {/* Title (editable) */}
          <div>
            <div className="text-meta text-[var(--color-text-muted)] uppercase mb-1">Title</div>
            {editing ? (
              <div className="flex gap-1">
                <input
                  value={editTitle}
                  onChange={(e) => setEditTitle(e.target.value)}
                  className="flex-1 px-2 py-1 rounded border border-[var(--color-border)] bg-[var(--color-bg)] text-small"
                  autoFocus
                />
                <button onClick={saveEdit} className="btn-sm btn-primary">Save</button>
                <button onClick={() => setEditing(false)} className="btn-sm btn-secondary">Cancel</button>
              </div>
            ) : (
              <div className="flex items-center gap-2">
                <span className="">{detail.title}</span>
                <button onClick={startEdit} className="text-[var(--color-text-muted)] hover:text-[var(--color-text)]">
                  <span className="material-symbols-outlined icon-sm">edit</span>
                </button>
              </div>
            )}
          </div>

          {/* Metadata Grid — 2×3 */}
          <div className="grid grid-cols-2 gap-2">
            <div>
              <div className="text-meta text-[var(--color-text-muted)] uppercase">Category</div>
              <span className="px-1.5 py-0.5 rounded bg-[var(--color-hover)] text-meta">{detail.category}</span>
            </div>
            <div>
              <div className="text-meta text-[var(--color-text-muted)] uppercase">Dimension</div>
              <span className="text-meta">{detail.dimension?.replace(/_/g, ' ')}</span>
            </div>
            <div>
              <div className="text-meta text-[var(--color-text-muted)] uppercase">Level</div>
              <span className="text-meta">{detail.level || '—'}</span>
            </div>
            <div>
              <div className="text-meta text-[var(--color-text-muted)] uppercase">Tier</div>
              <span className="text-meta">{detail.tier || 'active'}</span>
            </div>
            <div>
              <div className="text-meta text-[var(--color-text-muted)] uppercase">Source</div>
              <span className="text-meta font-mono">{detail.source || '—'}</span>
            </div>
            <div>
              <div className="text-meta text-[var(--color-text-muted)] uppercase">Evaluators</div>
              <div className="flex flex-wrap gap-0.5 mt-0.5">
                {(detail.evaluators || []).map((ev, i) => (
                  <span key={i} className="px-1 py-0.5 rounded bg-[var(--color-hover)] text-meta font-mono">{ev}</span>
                ))}
              </div>
            </div>
          </div>

          {/* Affected By */}
          {detail.affected_by && detail.affected_by.length > 0 && (
            <div>
              <div className="text-meta text-[var(--color-text-muted)] uppercase mb-1">Affected By</div>
              <div className="flex flex-wrap gap-1">
                {detail.affected_by.map((a, i) => (
                  <span key={i} className="px-1.5 py-0.5 rounded bg-[var(--color-hover)] text-meta">{a}</span>
                ))}
              </div>
            </div>
          )}

          {/* Scenario */}
          {detail.scenario?.turns && detail.scenario.turns.length > 0 && (
            <div>
              <div className="text-meta text-[var(--color-text-muted)] uppercase mb-1">Scenario</div>
              <div className="p-2.5 rounded bg-[var(--color-bg)] border border-[var(--color-border)]">
                {detail.scenario.turns.map((t, i) => (
                  <div key={i} className="text-meta font-mono leading-relaxed">
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
                <div className="text-meta font-semibold text-[var(--color-primary)] uppercase">Layer 1: Trajectory</div>
                {detail.trajectory_match && (
                  <span className="text-meta text-[var(--color-text-muted)] font-mono">{detail.trajectory_match}</span>
                )}
              </div>
              <div className="flex flex-wrap gap-1">
                {detail.expected_trajectory.map((t, i) => (
                  <span key={i} className="px-1.5 py-0.5 rounded bg-[var(--color-primary)]/10 text-[var(--color-primary)] text-meta font-mono">{t}</span>
                ))}
              </div>
            </div>
          )}

          {/* Layer 2: Assertions */}
          {detail.assertions && detail.assertions.length > 0 && (
            <div className="p-2.5 rounded border border-[color-mix(in_srgb,var(--color-status-success)_20%,transparent)] bg-[color-mix(in_srgb,var(--color-status-success)_5%,transparent)]">
              <div className="text-meta font-semibold text-[var(--color-status-success)] uppercase mb-1.5">Layer 2: Assertions</div>
              <ul className="space-y-1">
                {detail.assertions.map((a, i) => (
                  <li key={i} className="text-meta text-[var(--color-text-secondary)] leading-relaxed flex gap-1.5">
                    <span className="text-[var(--color-status-success)] shrink-0">•</span>
                    <span>{a}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Layer 3: Expected Response Contains */}
          {detail.expected_response_contains && detail.expected_response_contains.length > 0 && (
            <div className="p-2.5 rounded border border-[color-mix(in_srgb,var(--color-status-warning)_20%,transparent)] bg-[color-mix(in_srgb,var(--color-status-warning)_5%,transparent)]">
              <div className="text-meta font-semibold text-[var(--color-status-warning)] uppercase mb-1.5">Layer 3: Response</div>
              <div className="flex flex-wrap gap-1">
                {detail.expected_response_contains.map((kw, i) => (
                  <span key={i} className="px-1.5 py-0.5 rounded bg-[color-mix(in_srgb,var(--color-status-warning)_10%,transparent)] text-[var(--color-status-warning)] text-meta font-mono">{kw}</span>
                ))}
              </div>
            </div>
          )}

          {/* Tags */}
          {detail.tags && detail.tags.length > 0 && (
            <div>
              <div className="text-meta text-[var(--color-text-muted)] uppercase mb-1">Tags</div>
              <div className="flex flex-wrap gap-1">
                {detail.tags.map((tag, i) => (
                  <span key={i} className="px-1.5 py-0.5 rounded bg-[var(--color-hover)] text-meta text-[var(--color-text-muted)]">{tag}</span>
                ))}
              </div>
            </div>
          )}

          {/* Promoted From */}
          {detail.promoted_from && (
            <div>
              <div className="text-meta text-[var(--color-text-muted)] uppercase mb-1">Promoted From</div>
              <span className="text-meta text-[var(--color-text-muted)] font-mono">{detail.promoted_from}</span>
            </div>
          )}

          {/* Run History */}
          {detail.history && detail.history.length > 0 && (
            <div>
              <div className="text-meta text-[var(--color-text-muted)] uppercase mb-1">Run History</div>
              <div className="space-y-1">
                {detail.history.map((h, i) => (
                  <div key={i} className="flex items-center gap-2 p-1.5 rounded bg-[var(--color-bg)]">
                    <StatusBadge status={h.status} />
                    <span className="tabular-nums text-meta">{h.triggered_at?.slice(0, 10)}</span>
                    {h.notes && <span className="text-meta text-[var(--color-text-muted)] truncate">{h.notes}</span>}
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
    purpose: '',
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
      // One sentence in the language it was written in (en or zh); the backend
      // rejects a new case without it.
      purpose: { [/[\u3400-\u9fff]/.test(form.purpose) ? 'zh' : 'en']: form.purpose.trim() },
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
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-[var(--color-scrim-strong)] p-4" onClick={onClose}>
      <div className="w-full max-w-md bg-[var(--color-card)] rounded-xl border border-[var(--color-border)] shadow-2xl p-6" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-body font-semibold mb-4">Add Golden Set Case</h3>
        <form onSubmit={handleSubmit} className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <label className="block">
              <span className="text-meta text-[var(--color-text-muted)] uppercase">ID</span>
              <input
                value={form.id}
                onChange={(e) => setForm({ ...form, id: e.target.value })}
                placeholder="GS021"
                required
                className="mt-0.5 w-full px-2 py-1.5 rounded border border-[var(--color-border)] bg-[var(--color-bg)] text-small"
              />
            </label>
            <label className="block">
              <span className="text-meta text-[var(--color-text-muted)] uppercase">Category</span>
              <select
                value={form.category}
                onChange={(e) => setForm({ ...form, category: e.target.value })}
                className="mt-0.5 w-full px-2 py-1.5 rounded border border-[var(--color-border)] bg-[var(--color-bg)] text-small"
              >
                {categories.map(c => <option key={c} value={c}>{c}</option>)}
              </select>
            </label>
          </div>
          <label className="block">
            <span className="text-meta text-[var(--color-text-muted)] uppercase">Title</span>
            <input
              value={form.title}
              onChange={(e) => setForm({ ...form, title: e.target.value })}
              placeholder="Brief description of expected behavior"
              required
              className="mt-0.5 w-full px-2 py-1.5 rounded border border-[var(--color-border)] bg-[var(--color-bg)] text-small"
            />
          </label>
          <label className="block">
            <span className="text-meta text-[var(--color-text-muted)] uppercase">Purpose</span>
            <input
              value={form.purpose}
              onChange={(e) => setForm({ ...form, purpose: e.target.value })}
              placeholder="What this protects, and what breaks for you if it fails"
              required
              maxLength={200}
              className="mt-0.5 w-full px-2 py-1.5 rounded border border-[var(--color-border)] bg-[var(--color-bg)] text-small"
            />
          </label>
          <div className="grid grid-cols-2 gap-3">
            <label className="block">
              <span className="text-meta text-[var(--color-text-muted)] uppercase">Dimension</span>
              <input
                value={form.dimension}
                onChange={(e) => setForm({ ...form, dimension: e.target.value })}
                className="mt-0.5 w-full px-2 py-1.5 rounded border border-[var(--color-border)] bg-[var(--color-bg)] text-small"
              />
            </label>
            <label className="block">
              <span className="text-meta text-[var(--color-text-muted)] uppercase">Evaluator</span>
              <select
                value={form.evaluators}
                onChange={(e) => setForm({ ...form, evaluators: e.target.value })}
                className="mt-0.5 w-full px-2 py-1.5 rounded border border-[var(--color-border)] bg-[var(--color-bg)] text-small"
              >
                <option value="file_contains">file_contains</option>
                <option value="canary_pass">canary_pass</option>
                <option value="keyword_match">keyword_match</option>
                <option value="goal_success">goal_success (LLM)</option>
              </select>
            </label>
          </div>
          <label className="block">
            <span className="text-meta text-[var(--color-text-muted)] uppercase">Affected By (comma-separated)</span>
            <input
              value={form.affected_by}
              onChange={(e) => setForm({ ...form, affected_by: e.target.value })}
              placeholder="AGENT.md, STEERING.md"
              className="mt-0.5 w-full px-2 py-1.5 rounded border border-[var(--color-border)] bg-[var(--color-bg)] text-small"
            />
          </label>
          <div className="flex justify-end gap-2 pt-2">
            <button type="button" onClick={onClose} className="btn-md btn-secondary">Cancel</button>
            <button
              type="submit"
              disabled={createCase.isPending}
              className="btn-md btn-primary hover:opacity-90 disabled:opacity-50"
            >
              {createCase.isPending ? 'Creating...' : 'Create Case'}
            </button>
          </div>
          {createCase.isError && (
            <div className="text-meta text-[var(--color-status-error)] mt-1">
              {(createCase.error as Error)?.message || 'Failed to create case'}
            </div>
          )}
        </form>
      </div>
    </div>
  );
}
