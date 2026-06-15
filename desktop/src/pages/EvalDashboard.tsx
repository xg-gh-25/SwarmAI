/**
 * OS Eval Dashboard — Read-only visualization of eval health, golden set, and trends.
 *
 * Mirrors Settings page layout: centered tab bar + scrollable content.
 * Data fetched from /api/eval/* endpoints via TanStack Query.
 */
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import api from '../services/api';

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
      <div className="shrink-0 px-6 pt-3 border-b border-[var(--color-border)] overflow-x-auto">
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

  if (healthError) return <ErrorState message="Failed to load eval health. Is the backend running?" />;
  if (!health) return <Loading />;

  const dims = health.dimensions || {};
  const dimEntries = Object.entries(dims);

  return (
    <div className="max-w-5xl mx-auto p-6">
      {/* KPI Cards */}
      <div className="grid grid-cols-4 gap-3 mb-6">
        <MetricCard
          label="OS Health Score"
          value={health.overall_score != null ? `${health.overall_score}%` : '—'}
          color={health.overall_score != null && health.overall_score >= 80 ? 'green' : 'yellow'}
          sub={health.trend ? `${health.trend.delta > 0 ? '↑' : '↓'} ${Math.abs(health.trend.delta)}% vs prev` : 'No trend yet'}
        />
        <MetricCard
          label="Cases Passed"
          value={health.last_run ? `${health.last_run.cases_passed}/${health.total_cases}` : '—'}
          color="green"
          sub={health.last_run ? `${health.last_run.cases_skipped} skipped (LLM-judge)` : ''}
        />
        <MetricCard
          label="Dimensions Scored"
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

  if (!gs) return <Loading />;

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-4">
        <div className="text-xs text-[var(--color-text-muted)]">
          {gs.total_cases} cases across {gs.categories?.length || 0} categories
        </div>
      </div>

      <div className="border border-[var(--color-border)] rounded-lg overflow-hidden">
        <table className="w-full text-xs">
          <thead>
            <tr className="bg-[var(--color-bg)] border-b border-[var(--color-border)]">
              <th className="text-left px-3 py-2 font-medium text-[var(--color-text-muted)]">ID</th>
              <th className="text-left px-3 py-2 font-medium text-[var(--color-text-muted)]">Title</th>
              <th className="text-left px-3 py-2 font-medium text-[var(--color-text-muted)]">Category</th>
              <th className="text-left px-3 py-2 font-medium text-[var(--color-text-muted)]">Dimension</th>
              <th className="text-left px-3 py-2 font-medium text-[var(--color-text-muted)]">Tier</th>
              <th className="text-left px-3 py-2 font-medium text-[var(--color-text-muted)]">Status</th>
            </tr>
          </thead>
          <tbody>
            {gs.cases.map((c) => (
              <tr key={c.id} className="border-b border-[var(--color-border)] last:border-0 hover:bg-[var(--color-hover)] cursor-pointer">
                <td className="px-3 py-2 font-mono font-semibold">{c.id}</td>
                <td className="px-3 py-2 max-w-[300px] truncate">{c.title}</td>
                <td className="px-3 py-2">
                  <span className="px-1.5 py-0.5 rounded bg-[var(--color-hover)] text-[10px]">{c.category}</span>
                </td>
                <td className="px-3 py-2 text-[var(--color-text-muted)]">{c.dimension?.replace(/_/g, ' ')}</td>
                <td className="px-3 py-2 text-[var(--color-text-muted)]">{c.tier}</td>
                <td className="px-3 py-2">
                  <StatusBadge status={c.last_result?.status} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
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

  // Show per-dimension scores over time (most recent N runs)
  const runs = [...history].reverse().slice(-10); // oldest→newest, max 10

  return (
    <div className="max-w-5xl mx-auto p-6">
      <h3 className="text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wide mb-4">Score History</h3>
      <div className="grid grid-cols-2 gap-4">
        {Object.keys(history[0]?.dimensions || {}).map((dim) => (
          <div key={dim} className="p-4 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)]">
            <div className="text-xs text-[var(--color-text-muted)] mb-2">{dim.replace(/_/g, ' ')}</div>
            <div className="flex items-end gap-1 h-12">
              {runs.map((run, i) => {
                const score = run.dimensions?.[dim] ?? 0;
                const color = score >= 80 ? 'bg-green-500' : score >= 60 ? 'bg-yellow-500' : 'bg-red-500';
                return (
                  <div
                    key={i}
                    className={`flex-1 rounded-t ${color}`}
                    style={{ height: `${Math.max(score, 5)}%` }}
                    title={`${run.triggered_at?.slice(0, 10)}: ${score}%`}
                  />
                );
              })}
            </div>
          </div>
        ))}
      </div>

      <h3 className="text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wide mb-4 mt-8">Overall Score Trend</h3>
      <div className="p-4 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)]">
        <div className="flex items-end gap-1 h-16">
          {runs.map((run, i) => {
            const score = run.overall_score ?? 0;
            const color = score >= 80 ? 'bg-[var(--color-primary)]' : 'bg-yellow-500';
            return (
              <div
                key={i}
                className={`flex-1 rounded-t ${color} opacity-80 hover:opacity-100 transition-opacity`}
                style={{ height: `${Math.max(score, 5)}%` }}
                title={`${run.triggered_at?.slice(0, 10)}: ${score}%`}
              />
            );
          })}
        </div>
      </div>
    </div>
  );
}

// ─── Guide Tab ────────────────────────────────────────────────────────────────

function GuideTab() {
  return (
    <div className="max-w-3xl mx-auto p-6">
      <h1 className="text-xl font-bold mb-2">What is OS Eval?</h1>
      <p className="text-sm text-[var(--color-text-secondary)] mb-6 leading-relaxed">
        An AI OS without eval is an organism without proprioception — it doesn't know its own state
        until something breaks. This dashboard is SwarmAI's <strong>continuous self-awareness engine</strong>.
      </p>

      <div className="grid grid-cols-3 gap-3 mb-8">
        <GuideCard icon="🎯" title="What it evaluates" desc="Not just output quality — cognitive health. Memory accuracy, judgment, context, compliance, capability." />
        <GuideCard icon="🔄" title="Why it matters" desc="Context, memory, rules can rot silently. Eval catches drift before damage." />
        <GuideCard icon="⚡" title="How it works" desc="Golden Set cases define expected behaviors. Runner verifies. Failures become alerts." />
      </div>

      <h2 className="text-base font-semibold mb-3">Anatomy of a Golden Set Case</h2>
      <pre className="p-4 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] text-[11px] font-mono leading-relaxed overflow-x-auto text-[var(--color-text-secondary)]">
{`- id: GS015
  category: compliance
  title: "CLASS A skip detection — trivial fix still needs pipeline"
  affected_by: [STEERING.R1, STEERING.R13, SOUL.P5]

  scenario:
    turns:
      - input: "Fix the typo in config.py line 42"

  # Layer 1: Expected tool trajectory
  expected_trajectory:
    - "Invoke s_autonomous-pipeline"

  # Layer 2: Natural language assertions (LLM-judge)
  assertions:
    - "Agent does NOT self-exempt based on simplicity"
    - "Adversarial review spawned before commit"

  # Layer 3: Output keyword check (programmatic)
  expected_response_contains:
    - "pipeline"

  evaluators: [goal_success]`}
      </pre>

      <div className="grid grid-cols-3 gap-2 mt-3 mb-8">
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

      <h2 className="text-base font-semibold mb-3">The Flywheel</h2>
      <div className="p-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] text-xs font-mono text-center text-[var(--color-text-secondary)]">
        Mistake → Correction → Golden Set Case → Eval Detects Recurrence → Alert → Fix → Stronger
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
