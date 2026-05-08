/**
 * Capabilities settings tab.
 *
 * Displays runtime capabilities from ``GET /api/system/capabilities`` as a
 * grouped card grid with status badges (green check / red x). Shows what
 * modules are available in the current binary/environment.
 *
 * Groups: Core Modules, Optional Extensions, Runtime Info.
 *
 * @exports CapabilitiesTab (default)
 */

import { useState, useEffect } from 'react';
import { getApiBaseUrl } from '../../services/tauri';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface CapabilitiesResponse {
  capabilities: Record<string, boolean | string>;
}

interface CapabilityGroup {
  label: string;
  icon: string;
  items: { key: string; label: string; value: boolean | string }[];
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Human-friendly labels for capability keys. */
const LABELS: Record<string, string> = {
  sqlite_vec: 'Vector Search (sqlite-vec)',
  psutil: 'System Monitor (psutil)',
  slack_bolt: 'Slack Bot (slack-bolt)',
  recall_engine: 'Memory Recall Engine',
  manifest_loader: 'Skill Manifest Loader',
  llm_optimizer: 'Evolution Optimizer',
  locked_write: 'Memory Guard (locked_write)',
  distillation_hook: 'Distillation Hook',
  frozen: 'PyInstaller Bundle',
  mode: 'Runtime Mode',
};

/** Group each key into a section. Keys not listed go to "other". */
const GROUPS: Record<string, string[]> = {
  core: ['recall_engine', 'manifest_loader', 'locked_write', 'distillation_hook'],
  optional: ['sqlite_vec', 'psutil', 'slack_bolt', 'llm_optimizer'],
  runtime: ['frozen', 'mode'],
};

function groupCapabilities(caps: Record<string, boolean | string>): CapabilityGroup[] {
  const groups: CapabilityGroup[] = [
    { label: 'Core Modules', icon: 'memory', items: [] },
    { label: 'Optional Extensions', icon: 'extension', items: [] },
    { label: 'Runtime Info', icon: 'info', items: [] },
  ];

  const groupKeys = ['core', 'optional', 'runtime'];
  const assigned = new Set<string>();

  for (let i = 0; i < groupKeys.length; i++) {
    const keys = GROUPS[groupKeys[i]] || [];
    for (const key of keys) {
      if (key in caps) {
        groups[i].items.push({ key, label: LABELS[key] || key, value: caps[key] });
        assigned.add(key);
      }
    }
  }

  // Put unassigned keys into "Optional"
  for (const [key, value] of Object.entries(caps)) {
    if (!assigned.has(key)) {
      groups[1].items.push({ key, label: LABELS[key] || key, value });
    }
  }

  return groups.filter((g) => g.items.length > 0);
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function CapabilitiesTab() {
  const [groups, setGroups] = useState<CapabilityGroup[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const load = async () => {
      try {
        const apiBase = getApiBaseUrl();
        const resp = await fetch(`${apiBase}/api/system/capabilities`, {
          signal: AbortSignal.timeout(5000),
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data: CapabilitiesResponse = await resp.json();
        setGroups(groupCapabilities(data.capabilities));
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load capabilities');
      } finally {
        setLoading(false);
      }
    };
    load();
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <span className="material-symbols-outlined animate-spin text-[var(--color-text-muted)]">progress_activity</span>
        <span className="ml-2 text-sm text-[var(--color-text-muted)]">Loading capabilities...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-lg border border-red-500/30 bg-red-500/5 p-4 text-center">
        <span className="material-symbols-outlined text-red-400">error</span>
        <p className="mt-1 text-sm text-red-400">{error}</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-[var(--color-text)]">Capabilities</h2>
        <p className="text-sm text-[var(--color-text-muted)] mt-1">
          Runtime capabilities detected in the current backend binary.
        </p>
      </div>

      {groups.map((group) => (
        <div key={group.label} className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] overflow-hidden">
          {/* Group header */}
          <div className="flex items-center gap-2 px-4 py-2.5 border-b border-[var(--color-border)]">
            <span className="material-symbols-outlined text-base text-[var(--color-text-muted)]">
              {group.icon}
            </span>
            <span className="text-sm font-medium text-[var(--color-text)]">{group.label}</span>
          </div>

          {/* Capability rows */}
          <div className="divide-y divide-[var(--color-border)]">
            {group.items.map(({ key, label, value }) => (
              <div key={key} className="flex items-center justify-between px-4 py-2.5">
                <span className="text-sm text-[var(--color-text)]">{label}</span>
                {typeof value === 'boolean' ? (
                  <span className={`material-symbols-outlined text-base ${value ? 'text-green-400' : 'text-red-400'}`}>
                    {value ? 'check_circle' : 'cancel'}
                  </span>
                ) : (
                  <span className="text-xs px-2 py-0.5 rounded bg-[var(--color-bg)] text-[var(--color-text-muted)] font-mono">
                    {value}
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
