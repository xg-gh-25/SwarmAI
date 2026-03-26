/**
 * Core Engine growth metrics dashboard.
 *
 * Displays: engine level progress, memory effectiveness, learning state,
 * DDD health, session volume, and context health findings.
 * Data from GET /api/system/engine-metrics.
 */
import { useState, useEffect, useCallback } from 'react';
import { systemService, type EngineMetrics } from '../../services/system';

// -- Helpers --

function StatusDot({ color }: { color: 'green' | 'yellow' | 'red' | 'blue' | 'gray' }) {
  const colors = {
    green: 'bg-green-400',
    yellow: 'bg-yellow-400',
    red: 'bg-red-400',
    blue: 'bg-blue-400',
    gray: 'bg-gray-400',
  };
  return <span className={`inline-block w-2 h-2 rounded-full ${colors[color]}`} />;
}

function ProgressBar({ value, max, label }: { value: number; max: number; label: string }) {
  const pct = max > 0 ? Math.round((value / max) * 100) : 0;
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs text-[var(--color-text-muted)]">
        <span>{label}</span>
        <span>{value}/{max} ({pct}%)</span>
      </div>
      <div className="h-2 bg-[var(--color-bg)] rounded-full overflow-hidden">
        <div
          className="h-full bg-[var(--color-primary)] rounded-full transition-all"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

function Stat({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="text-center">
      <div className="text-2xl font-bold text-[var(--color-text)]">{value}</div>
      <div className="text-xs text-[var(--color-text-muted)]">{label}</div>
      {sub && <div className="text-xs text-[var(--color-text-muted)] opacity-60">{sub}</div>}
    </div>
  );
}

// -- Main Component --

export default function EngineMetricsTab() {
  const [metrics, setMetrics] = useState<EngineMetrics | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await systemService.getEngineMetrics();
      setMetrics(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load metrics');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  if (loading && !metrics) {
    return <div className="text-[var(--color-text-muted)] text-sm p-6">Loading engine metrics...</div>;
  }

  if (error && !metrics) {
    return <div className="text-red-400 text-sm p-6">{error}</div>;
  }

  if (!metrics) return null;

  // All data passes through deepSnakeToCamel in system.ts — trust camelCase only
  const level = metrics.engineLevel ?? { current: 'unknown', l3Progress: '0/0', l3Features: {}, levels: {} };
  const memory = (metrics.memory ?? { status: 'error' }) as Record<string, unknown>;
  const learning = (metrics.learning ?? {}) as Record<string, unknown>;
  const sessions = (metrics.sessions ?? {}) as Record<string, unknown>;
  const effectiveness = (learning.effectiveness ?? {}) as Record<string, unknown>;
  const workDist = (learning.workTypeDistribution ?? {}) as Record<string, number>;

  return (
    <div className="space-y-6">
      {/* Header with refresh */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-[var(--color-text)]">Core Engine</h2>
          <p className="text-xs text-[var(--color-text-muted)]">
            Self-governing intelligence metrics
          </p>
        </div>
        <button
          onClick={refresh}
          disabled={loading}
          className="px-3 py-1 text-xs bg-[var(--color-bg)] text-[var(--color-text-muted)] rounded hover:bg-[var(--color-primary)] hover:text-white transition-colors disabled:opacity-50"
        >
          {loading ? 'Refreshing...' : 'Refresh'}
        </button>
      </div>

      {/* Engine Level Progress */}
      <section className="bg-[var(--color-card)] rounded-lg p-5">
        <h3 className="text-sm font-semibold text-[var(--color-text)] mb-3">Growth Level</h3>
        <div className="space-y-2">
          {Object.entries(level.levels ?? {}).map(([key, status]) => {
            const label = key.replace(/_/g, ' ').replace(/^l(\d)/, 'L$1:');
            const isActive = status === 'in_progress';
            const isDone = status === 'complete';
            return (
              <div key={key} className="flex items-center gap-2 text-sm">
                <StatusDot color={isDone ? 'green' : isActive ? 'yellow' : 'gray'} />
                <span className={`flex-1 ${isDone ? 'text-[var(--color-text)]' : isActive ? 'text-yellow-300' : 'text-[var(--color-text-muted)]'}`}>
                  {label}
                </span>
                <span className="text-xs text-[var(--color-text-muted)]">
                  {isDone ? 'Done' : isActive ? level.l3Progress : status}
                </span>
              </div>
            );
          })}
        </div>

        {/* L3 feature checklist */}
        {level.l3Features && Object.keys(level.l3Features ?? {}).length > 0 && (
          <div className="mt-4 pt-3 border-t border-[var(--color-border)]">
            <p className="text-xs text-[var(--color-text-muted)] mb-2">L3 Features</p>
            <div className="grid grid-cols-2 gap-1">
              {Object.entries(level.l3Features).map(([feature, done]) => (
                <div key={feature} className="flex items-center gap-1.5 text-xs">
                  <span className={done ? 'text-green-400' : 'text-[var(--color-text-muted)]'}>
                    {done ? '\u2713' : '\u25CB'}
                  </span>
                  <span className={done ? 'text-[var(--color-text)]' : 'text-[var(--color-text-muted)]'}>
                    {feature.replace(/_/g, ' ')}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </section>

      {/* Stats Row */}
      <section className="bg-[var(--color-card)] rounded-lg p-5">
        <div className="grid grid-cols-4 gap-4">
          <Stat
            label="Sessions (7d)"
            value={sessions.last7dSessions as number ?? '-'}
            sub={`${sessions.last7dActiveDays ?? 0} active days`}
          />
          <Stat
            label="Memory Freshness"
            value={`${memory.freshnessScore ?? 0}%`}
            sub={`${memory.totalEntries ?? 0} entries`}
          />
          <Stat
            label="Follow Rate"
            value={`${Math.round((effectiveness.followRate as number ?? 0) * 100)}%`}
            sub={`${effectiveness.totalSuggestions ?? 0} suggestions`}
          />
          <Stat
            label="Observations"
            value={learning.totalObservations as number ?? 0}
            sub={learning.learningSummary as string ?? ''}
          />
        </div>
      </section>

      {/* Memory Effectiveness */}
      {memory.status === 'ok' && (
        <section className="bg-[var(--color-card)] rounded-lg p-5">
          <h3 className="text-sm font-semibold text-[var(--color-text)] mb-3">Memory Health</h3>
          <div className="space-y-3">
            <ProgressBar
              value={memory.recentEntries14d as number ?? 0}
              max={memory.datedEntries as number ?? 1}
              label="Fresh entries (< 14 days)"
            />
            {(memory.staleEntries30d as number ?? 0) > 0 && (
              <div className="text-xs text-yellow-400">
                {String(memory.staleEntries30d ?? 0)} entries older than 30 days — consider pruning
              </div>
            )}
            {Object.entries((memory.sections ?? {}) as Record<string, Record<string, unknown>>).map(([name, sec]) => (
              <div key={name} className="flex items-center justify-between text-xs">
                <span className="text-[var(--color-text-muted)]">{name}</span>
                <span className="text-[var(--color-text)]">
                  {Number(sec?.count ?? 0)} entries
                  {Number(sec?.stale30d ?? 0) > 0 && (
                    <span className="text-yellow-400 ml-1">
                      ({String(sec?.stale30d ?? 0)} stale)
                    </span>
                  )}
                </span>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Work Distribution */}
      {Object.keys(workDist).length > 0 && (
        <section className="bg-[var(--color-card)] rounded-lg p-5">
          <h3 className="text-sm font-semibold text-[var(--color-text)] mb-3">Work Distribution</h3>
          <div className="space-y-2">
            {Object.entries(workDist)
              .sort(([,a], [,b]) => b - a)
              .map(([type, count]) => {
                const total = Object.values(workDist).reduce((a, b) => a + b, 0);
                return (
                  <ProgressBar
                    key={type}
                    value={count}
                    max={total}
                    label={type.charAt(0).toUpperCase() + type.slice(1)}
                  />
                );
              })}
          </div>
        </section>
      )}

      {/* DDD Health */}
      {(metrics.dddHealth?.projects ?? []).length > 0 && (
        <section className="bg-[var(--color-card)] rounded-lg p-5">
          <h3 className="text-sm font-semibold text-[var(--color-text)] mb-3">DDD Health</h3>
          <div className="space-y-3">
            {metrics.dddHealth.projects.map((project) => (
              <div key={project.name as string} className="space-y-1">
                <div className="flex items-center gap-2 text-sm">
                  <StatusDot color={project.overallStale ? 'yellow' : 'green'} />
                  <span className="text-[var(--color-text)] font-medium">{project.name as string}</span>
                </div>
                <div className="ml-4 grid grid-cols-4 gap-2">
                  {Object.entries((project.docs ?? {}) as Record<string, Record<string, unknown>>).map(([doc, info]) => (
                    <div key={doc} className="text-xs">
                      <span className={info?.stale ? 'text-yellow-400' : info?.exists ? 'text-[var(--color-text-muted)]' : 'text-red-400'}>
                        {doc.replace('.md', '')}
                      </span>
                      {Boolean(info?.exists) && (
                        <span className="text-[var(--color-text-muted)] ml-1">
                          {String(info?.ageDays ?? 0)}d
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>

          {/* DDD Change Suggestions */}
          {(metrics.dddSuggestions ?? []).length > 0 && (
            <div className="mt-3 pt-3 border-t border-[var(--color-border)]">
              <p className="text-xs text-[var(--color-text-muted)] mb-2">Suggested Updates</p>
              {metrics.dddSuggestions.map((s, i) => (
                <div key={i} className="text-xs text-yellow-300 mb-1">
                  {s.doc} ({s.section}): {s.reason}
                </div>
              ))}
            </div>
          )}
        </section>
      )}

      {/* Context Health Findings */}
      {(metrics.contextHealth?.findings ?? []).length > 0 && (
        <section className="bg-[var(--color-card)] rounded-lg p-5">
          <h3 className="text-sm font-semibold text-[var(--color-text)] mb-3">Context Health</h3>
          <div className="space-y-1">
            {metrics.contextHealth.findings.map((f, i) => (
              <div key={i} className="flex items-center gap-2 text-xs">
                <StatusDot color={f.level === 'critical' ? 'red' : f.level === 'warning' ? 'yellow' : 'blue'} />
                <span className="text-[var(--color-text-muted)]">{f.message}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Footer */}
      <p className="text-xs text-[var(--color-text-muted)] text-center opacity-50">
        Last collected: {metrics.collectedAt || 'never'}
      </p>
    </div>
  );
}
