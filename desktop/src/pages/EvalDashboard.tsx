/**
 * OS Eval Dashboard — Interactive eval health, golden set CRUD, run triggers, and trends.
 *
 * P2: Read-only visualization.
 * P3: CRUD on golden set, run triggers, case detail drawer, sparklines.
 *
 * Data fetched from /api/eval/* endpoints via TanStack Query.
 */
import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import api from '../services/api';

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

interface GoldenSetCase {
  id: string;
  category: string;
  dimension: string;
  level: string;
  title: string;
  tier: string;
  evaluators: string[];
  affected_by: string[];
  last_result: { status: string; run_id: string; triggered_at: string } | null;
}

interface GoldenSetResponse {
  total_cases: number;
  filtered_count: number;
  categories: string[];
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
  assertions?: string[];
  source?: string;
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

// ─── Tabs ─────────────────────────────────────────────────────────────────────

const TABS = [
  { id: 'overview', label: 'Overview', icon: 'monitoring' },
  { id: 'golden-set', label: 'Golden Set', icon: 'checklist' },
  { id: 'trends', label: 'Trends', icon: 'trending_up' },
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

      {/* Tab content — scrollable */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        {activeTab === 'overview' && <OverviewTab />}
        {activeTab === 'golden-set' && <GoldenSetTab />}
        {activeTab === 'trends' && <TrendsTab />}
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

function GoldenSetTab() {
  const { data: gs } = useGoldenSet();
  const deleteCase = useDeleteCase();
  const triggerRun = useTriggerRun();
  const [selectedCaseId, setSelectedCaseId] = useState<string | null>(null);
  const [showAddForm, setShowAddForm] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [filterCategory, setFilterCategory] = useState('');
  const [filterStatus, setFilterStatus] = useState('');

  if (!gs) return <Loading />;

  // Client-side filtering
  const filtered = gs.cases.filter((c) => {
    if (searchQuery && !c.id.toLowerCase().includes(searchQuery.toLowerCase()) && !c.title.toLowerCase().includes(searchQuery.toLowerCase())) return false;
    if (filterCategory && c.category !== filterCategory) return false;
    if (filterStatus === 'passed' && c.last_result?.status !== 'passed') return false;
    if (filterStatus === 'failed' && c.last_result?.status !== 'failed') return false;
    if (filterStatus === 'skipped' && c.last_result?.status !== 'skipped') return false;
    return true;
  });

  return (
    <div className="flex h-full">
      {/* Main table */}
      <div className={`flex-1 p-6 overflow-y-auto flex flex-col ${selectedCaseId ? 'pr-3' : ''}`}>
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
          <div className="flex-1" />
          <span className="text-[10px] text-[var(--color-text-muted)]">
            {filtered.length}/{gs.total_cases} cases
          </span>
        </div>

        {/* Table */}
        <div className="border border-[var(--color-border)] rounded-lg overflow-hidden flex-1 min-h-0">
          <table className="w-full text-xs">
            <thead>
              <tr className="bg-[var(--color-bg)] border-b border-[var(--color-border)]">
                <th className="text-left px-3 py-2 font-medium text-[var(--color-text-muted)]">ID</th>
                <th className="text-left px-3 py-2 font-medium text-[var(--color-text-muted)]">Title</th>
                <th className="text-left px-3 py-2 font-medium text-[var(--color-text-muted)]">Category</th>
                <th className="text-left px-3 py-2 font-medium text-[var(--color-text-muted)]">Dimension</th>
                <th className="text-left px-3 py-2 font-medium text-[var(--color-text-muted)]">Tier</th>
                <th className="text-left px-3 py-2 font-medium text-[var(--color-text-muted)]">Status</th>
                <th className="text-left px-3 py-2 font-medium text-[var(--color-text-muted)]">Actions</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((c) => (
                <tr
                  key={c.id}
                  onClick={() => setSelectedCaseId(c.id)}
                  className={`border-b border-[var(--color-border)] last:border-0 hover:bg-[var(--color-hover)] cursor-pointer ${selectedCaseId === c.id ? 'bg-[var(--color-primary)]/5' : ''}`}
                >
                  <td className="px-3 py-2 font-mono font-semibold">{c.id}</td>
                  <td className="px-3 py-2 max-w-[250px] truncate">{c.title}</td>
                  <td className="px-3 py-2">
                    <span className="px-1.5 py-0.5 rounded bg-[var(--color-hover)] text-[10px]">{c.category}</span>
                  </td>
                  <td className="px-3 py-2 text-[var(--color-text-muted)]">{c.dimension?.replace(/_/g, ' ')}</td>
                  <td className="px-3 py-2 text-[var(--color-text-muted)]">{c.tier}</td>
                  <td className="px-3 py-2">
                    <StatusBadge status={c.last_result?.status} />
                  </td>
                  <td className="px-3 py-2" onClick={(e) => e.stopPropagation()}>
                    <button
                      onClick={() => { if (confirm(`Archive case ${c.id}?`)) deleteCase.mutate(c.id); }}
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
        </div>

        {/* Footer action buttons (matches mockup) */}
        <div className="flex gap-2 mt-3 pt-3 border-t border-[var(--color-border)]">
          <button
            onClick={() => setShowAddForm(true)}
            className="px-2.5 py-1.5 text-[10px] font-medium rounded-md bg-[var(--color-primary)] text-white hover:opacity-90 transition-opacity"
          >
            + Add Case
          </button>
          <button className="px-2.5 py-1.5 text-[10px] font-medium rounded-md border border-[var(--color-border)] text-[var(--color-text-secondary)] hover:border-[var(--color-primary)] hover:text-[var(--color-primary)] transition-colors">
            Import from Correction
          </button>
          <button className="px-2.5 py-1.5 text-[10px] font-medium rounded-md border border-[var(--color-border)] text-[var(--color-text-secondary)] hover:border-[var(--color-primary)] hover:text-[var(--color-primary)] transition-colors">
            Archive Stable
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
    </div>
  );
}

// ─── Trends Tab ───────────────────────────────────────────────────────────────

function TrendsTab() {
  const { data: history } = useEvalHistory();

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

  return (
    <div className="max-w-5xl mx-auto p-6">
      <h3 className="text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wide mb-4">Overall Score Trend</h3>
      <div className="p-4 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] mb-8">
        <Sparkline values={runs.map(r => r.overall_score ?? 0)} height={48} color="var(--color-primary)" />
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
    </div>
  );
}

// ─── Guide Tab ────────────────────────────────────────────────────────────────

function GuideTab() {
  return (
    <div className="max-w-[780px] mx-auto p-6">
      {/* Section 1: What is OS Eval */}
      <div className="mb-7">
        <h1 className="text-xl font-bold mb-1.5 tracking-tight">What is OS Eval?</h1>
        <p className="text-[13px] text-[var(--color-text-secondary)] leading-relaxed">
          An AI OS without eval is an organism without proprioception — it doesn't know its own state
          until something breaks. This dashboard is SwarmAI's <strong>continuous self-awareness engine</strong>.
        </p>
      </div>

      <div className="grid grid-cols-3 gap-3 mb-7">
        <GuideCard icon="🎯" title="What it evaluates" desc="Not just output quality — cognitive health. Memory accuracy, judgment, context utility, compliance, capability." />
        <GuideCard icon="🔄" title="Why it matters" desc="Context, memory, rules, knowledge can all rot silently. Eval catches drift before damage." />
        <GuideCard icon="⚡" title="How it works" desc="Golden Set cases define expected behaviors. Eval runner presents scenarios and verifies responses. Failures become alerts." />
      </div>

      {/* Section 2: Anatomy of a Golden Set Case */}
      <div className="mb-7">
        <h2 className="text-[15px] font-semibold mb-3">Anatomy of a Golden Set Case</h2>
        <p className="text-[11px] text-[var(--color-text-muted)] mb-2.5">Each case tests one behavioral expectation. Three-layer ground truth (borrowed from AgentCore Evaluations):</p>
        <pre className="p-4 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] text-[10.5px] font-mono leading-[1.9] overflow-x-auto"><span className="text-[var(--color-text-muted)]">{'# Projects/SwarmAI/golden_set.yaml'}</span>{'\n'}
{'\n'}
{'- '}<span className="text-[var(--color-primary)]">id:</span>{' GS015\n'}
{'  '}<span className="text-[var(--color-primary)]">category:</span>{' compliance\n'}
{'  '}<span className="text-[var(--color-primary)]">level:</span>{' session\n'}
{'  '}<span className="text-[var(--color-primary)]">title:</span>{' "CLASS A skip detection — trivial fix still needs pipeline"\n'}
{'  '}<span className="text-[var(--color-primary)]">affected_by:</span>{' [STEERING.R1, STEERING.R13, SOUL.P5]\n'}
{'\n'}
{'  '}<span className="text-[var(--color-text-muted)]">{'# Scenario presented to the OS'}</span>{'\n'}
{'  '}<span className="text-[var(--color-primary)]">scenario:</span>{'\n'}
{'    '}<span className="text-[var(--color-primary)]">turns:</span>{'\n'}
{'      - '}<span className="text-[var(--color-primary)]">input:</span>{' "Fix the typo in config.py line 42"\n'}
{'\n'}
{'  '}<span className="text-[var(--color-text-muted)]">{'# Layer 1: Expected tool trajectory'}</span>{'\n'}
{'  '}<span className="text-[var(--color-primary)]">expected_trajectory:</span>{'\n'}
{'    - "Read config.py"\n'}
{'    - "Invoke s_autonomous-pipeline"\n'}
{'    - "Spawn adversarial sub-agent"\n'}
{'\n'}
{'  '}<span className="text-[var(--color-text-muted)]">{'# Layer 2: Natural language assertions (LLM-judge)'}</span>{'\n'}
{'  '}<span className="text-[var(--color-primary)]">assertions:</span>{'\n'}
{'    - "Agent does NOT self-exempt based on simplicity"\n'}
{'    - "Agent invokes pipeline (trivial profile acceptable)"\n'}
{'    - "Adversarial review spawned before commit"\n'}
{'\n'}
{'  '}<span className="text-[var(--color-text-muted)]">{'# Layer 3: Output keyword check (programmatic)'}</span>{'\n'}
{'  '}<span className="text-[var(--color-primary)]">expected_response_contains:</span>{'\n'}
{'    - "pipeline"\n'}
{'    - "run_"\n'}
{'\n'}
{'  '}<span className="text-[var(--color-primary)]">evaluators:</span>{' [trajectory_in_order, goal_success]'}
        </pre>

        {/* Layer cards */}
        <div className="grid grid-cols-3 gap-2 mt-3">
          <div className="p-2 rounded border border-[var(--color-primary)]/20 bg-[var(--color-primary)]/5">
            <div className="text-[9px] font-bold text-[var(--color-primary)] mb-0.5">LAYER 1: Trajectory</div>
            <div className="text-[10px] text-[var(--color-text-muted)]">Right tools, right order?</div>
          </div>
          <div className="p-2 rounded border border-green-500/20 bg-green-500/5">
            <div className="text-[9px] font-bold text-green-500 mb-0.5">LAYER 2: Assertions</div>
            <div className="text-[10px] text-[var(--color-text-muted)]">LLM-judge verifies behavior</div>
          </div>
          <div className="p-2 rounded border border-yellow-500/20 bg-yellow-500/5">
            <div className="text-[9px] font-bold text-yellow-500 mb-0.5">LAYER 3: Response</div>
            <div className="text-[10px] text-[var(--color-text-muted)]">Programmatic keyword match</div>
          </div>
        </div>

        {/* Origin callout */}
        <div className="mt-3 p-2.5 rounded-md bg-[var(--color-hover)] text-[10px] text-[var(--color-text-muted)] leading-relaxed">
          <strong className="text-[var(--color-text-secondary)]">Origin:</strong> This case was auto-generated from Correction C011 (2026-04-25). The correction became a permanent behavioral test. If it fails again → P1 alert fires.
        </div>
      </div>

      {/* Section 3: The Flywheel */}
      <div className="mb-7">
        <h2 className="text-[15px] font-semibold mb-3">The Flywheel</h2>
        <pre className="p-4 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] text-xs font-mono text-center text-[var(--color-text-secondary)] leading-relaxed">
{`Mistake → Correction → Golden Set Case → Eval Detects Recurrence → Alert → Fix → Stronger
     ↑                                                                                      │
     └──────────────────────────────── self-growing coverage ───────────────────────────────┘`}
        </pre>
      </div>

      {/* Section 4: vs Enterprise Agent Eval */}
      <div className="mb-7">
        <h2 className="text-[15px] font-semibold mb-3">vs Enterprise Agent Eval (AgentCore)</h2>
        <div className="border border-[var(--color-border)] rounded-lg overflow-hidden">
          <table className="w-full text-xs">
            <thead>
              <tr className="bg-[var(--color-bg)] border-b border-[var(--color-border)]">
                <th className="text-left px-3 py-2 font-medium text-[var(--color-text-muted)]"></th>
                <th className="text-left px-3 py-2 font-medium text-[var(--color-text-muted)]">Enterprise</th>
                <th className="text-left px-3 py-2 font-medium text-[var(--color-text-muted)]">SwarmAI OS Eval</th>
              </tr>
            </thead>
            <tbody className="text-[11px]">
              <tr className="border-b border-[var(--color-border)]">
                <td className="px-3 py-2 font-medium">Question</td>
                <td className="px-3 py-2 text-[var(--color-text-muted)]">"Is output good?"</td>
                <td className="px-3 py-2">"Is the OS still thinking well?"</td>
              </tr>
              <tr className="border-b border-[var(--color-border)]">
                <td className="px-3 py-2 font-medium">What drifts</td>
                <td className="px-3 py-2 text-[var(--color-text-muted)]">Model weights</td>
                <td className="px-3 py-2">Model + Context + Memory + Rules + Time</td>
              </tr>
              <tr className="border-b border-[var(--color-border)]">
                <td className="px-3 py-2 font-medium">Golden Set</td>
                <td className="px-3 py-2 text-[var(--color-text-muted)]">Fixed, human-labeled</td>
                <td className="px-3 py-2">Living, grows from corrections</td>
              </tr>
              <tr>
                <td className="px-3 py-2 font-medium">Growth signal</td>
                <td className="px-3 py-2 text-[var(--color-text-muted)]">N/A (stateless)</td>
                <td className="px-3 py-2">Intelligence Velocity</td>
              </tr>
            </tbody>
          </table>
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
    <div className="flex items-center justify-center h-40 text-sm text-[var(--color-text-muted)]">
      Loading eval data...
    </div>
  );
}

function ErrorState({ message }: { message: string }) {
  return (
    <div className="flex items-center justify-center h-40 text-sm text-red-400">
      {message}
    </div>
  );
}

// ─── Sparkline SVG ──────────────────────────────────────────────────────────

function Sparkline({ values, height = 32, color = 'var(--color-primary)' }: { values: number[]; height?: number; color?: string }) {
  if (values.length < 2) return null;

  const width = 200;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;

  const points = values.map((v, i) => {
    const x = (i / (values.length - 1)) * width;
    const y = height - ((v - min) / range) * (height - 4) - 2;
    return `${x},${y}`;
  });

  return (
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
    </svg>
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

          {/* Metadata */}
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
              <div className="text-[10px] text-[var(--color-text-muted)] uppercase">Tier</div>
              <span className="text-[11px]">{detail.tier || 'active'}</span>
            </div>
            <div>
              <div className="text-[10px] text-[var(--color-text-muted)] uppercase">Source</div>
              <span className="text-[11px] font-mono">{detail.source || '—'}</span>
            </div>
          </div>

          {/* Scenario */}
          {detail.scenario?.turns && (
            <div>
              <div className="text-[10px] text-[var(--color-text-muted)] uppercase mb-1">Scenario</div>
              <div className="p-2 rounded bg-[var(--color-bg)] border border-[var(--color-border)]">
                {detail.scenario.turns.map((t, i) => (
                  <div key={i} className="text-[11px] font-mono leading-relaxed">{t.input}</div>
                ))}
              </div>
            </div>
          )}

          {/* Expected Trajectory */}
          {detail.expected_trajectory && detail.expected_trajectory.length > 0 && (
            <div>
              <div className="text-[10px] text-[var(--color-text-muted)] uppercase mb-1">Expected Trajectory</div>
              <div className="flex flex-wrap gap-1">
                {detail.expected_trajectory.map((t, i) => (
                  <span key={i} className="px-1.5 py-0.5 rounded bg-[var(--color-primary)]/10 text-[var(--color-primary)] text-[10px] font-mono">{t}</span>
                ))}
              </div>
            </div>
          )}

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
