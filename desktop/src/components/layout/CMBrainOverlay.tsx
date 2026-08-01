/**
 * CMBrainOverlay — the C&M (Context & Memory) Global Brain overlay.
 *
 * The cognition-zone "Context / C&M" nav card opens this fullscreen overlay. Its
 * purpose (XG): opening it should teach the user WHAT is loaded into the agent's
 * head right now, WHAT it has learned, and HOW the brain works — a teaching +
 * control surface, not a settings panel.
 *
 * Run 1 scope (run_5f7d4fe1): the 3-tab shell (Context / Memory / Guideline) with
 * ONLY the Context tab implemented + a fixed overview rail. Memory + Guideline are
 * labeled placeholders filled by later runs.
 *
 * Data is backend-primary (IMPROVEMENT.md:367 — "when a symptom shows in a UI
 * widget, the fix layer is often the backend that feeds it"): the Context tab +
 * overview rail CONSUME the calibrated token_block from GET /eval/context-health;
 * the frontend invents no numbers. Opens on the existing `swarm:show-context`
 * window event via useExclusiveOverlay (single-overlay mux + back-to-chat).
 *
 * @exports CMBrainOverlay
 */
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import Modal from '../common/Modal';
import { useExclusiveOverlay } from './useExclusiveOverlay';
import api from '../../services/api';

// ── Types (mirror the backend context-health token_block, snake_case as served) ──
interface TokenFileRow {
  name: string;
  tokens: number;
  pct: number;
  owner: 'system' | 'user' | 'agent' | 'auto';
  priority: number;
  locked: boolean;
}
interface TokenBlock {
  total_tokens: number;
  budget: number;
  warning_threshold: number;
  emergency_threshold: number;
  over_budget: boolean;
  per_file: TokenFileRow[];
}
interface ContextHealth {
  pending_proposals?: Array<Record<string, unknown>>;
  token_block?: TokenBlock | null;
}

type TabKey = 'context' | 'memory' | 'guideline';

const OWNER_LABEL: Record<TokenFileRow['owner'], string> = {
  system: 'system',
  user: 'user',
  agent: 'agent',
  auto: 'auto',
};

// Ownership → accent tint (aligns to the workspace ownership color model).
const OWNER_TINT: Record<TokenFileRow['owner'], string> = {
  system: '#7c8194', // slate — system-owned, non-editable
  user: '#4a8fb0', // teal — user-owned
  agent: '#5fc99a', // cognition green — agent-owned (memory/evolution)
  auto: '#b08fd0', // violet — auto-generated
};

function fmtTokens(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}K`;
  return String(n);
}

function useContextHealth(enabled: boolean) {
  return useQuery<ContextHealth>({
    queryKey: ['cm-brain-context-health'],
    queryFn: async () => (await api.get<ContextHealth>('/eval/context-health')).data,
    staleTime: 30_000,
    // Gate on `open` so a closed overlay never fetches (the overlay is always
    // mounted in ThreeColumnLayout; without this the query would fire on app boot).
    enabled,
  });
}

export function CMBrainOverlay() {
  const { open, close } = useExclusiveOverlay('swarm:show-context');
  const [tab, setTab] = useState<TabKey>('context');

  // Fetch ONLY while the overlay is open (see `enabled` in useContextHealth).
  const { data } = useContextHealth(open);
  const block = data?.token_block ?? null;
  const reviewCount = data?.pending_proposals?.length ?? 0;

  if (!open) return null;

  return (
    <Modal isOpen={open} onClose={close} title="C&M · Global Brain" size="fullscreen" mode="BRAIN" fullscreenWidth="xl">
      <div className="flex h-full min-h-0" data-testid="cm-brain-overlay">
        {/* ── Left overview rail (fixed 264px, tab-independent) ── */}
        <aside
          className="w-[264px] shrink-0 flex flex-col gap-4 border-r border-[var(--color-border)] p-4 overflow-y-auto"
          data-testid="cm-overview-rail"
        >
          <div>
            <div className="text-[11px] font-mono uppercase tracking-wider text-[var(--color-text-faint)]">Current load</div>
            <div className="mt-1 flex items-baseline gap-1.5">
              <span className="text-2xl font-semibold text-[var(--color-text)]">
                {block ? fmtTokens(block.total_tokens) : '—'}
              </span>
              <span className="text-xs text-[var(--color-text-muted)]">
                / {block ? fmtTokens(block.budget) : '—'} budget
              </span>
            </div>
            {block?.over_budget && (
              <div className="mt-1 text-[11px] font-medium text-[#d08a4a]">over assembly budget</div>
            )}
          </div>

          <div>
            <div className="text-[11px] font-mono uppercase tracking-wider text-[var(--color-text-faint)]">Needs you</div>
            <div className="mt-2 flex flex-col gap-1.5">
              <NeedsBtn testid="cm-needs-review" label="Review" count={reviewCount} tint="#5fc99a" />
              <NeedsBtn testid="cm-needs-approve" label="Approve" count={0} tint="#d08a4a" />
              <NeedsBtn testid="cm-needs-action" label="Action" count={0} tint="#4a8fb0" />
            </div>
          </div>
        </aside>

        {/* ── Main area: tabs + panel ── */}
        <div className="flex-1 min-w-0 flex flex-col">
          <div className="flex items-center gap-1 border-b border-[var(--color-border)] px-4 pt-3">
            <TabBtn testid="cm-tab-context" label="Context" active={tab === 'context'} onClick={() => setTab('context')} badge={block?.per_file.length} />
            <TabBtn testid="cm-tab-memory" label="Memory" active={tab === 'memory'} onClick={() => setTab('memory')} />
            <TabBtn testid="cm-tab-guideline" label="Guideline" active={tab === 'guideline'} onClick={() => setTab('guideline')} />
          </div>

          <div className="flex-1 min-h-0 overflow-y-auto p-4">
            {tab === 'context' && <ContextTab block={block} />}
            {tab === 'memory' && <Placeholder testid="cm-placeholder-memory" title="Memory" />}
            {tab === 'guideline' && <Placeholder testid="cm-placeholder-guideline" title="Guideline" />}
          </div>
        </div>
      </div>
    </Modal>
  );
}

function ContextTab({ block }: { block: TokenBlock | null }) {
  const rows = block?.per_file ?? [];
  return (
    <div data-testid="cm-panel-context" className="flex flex-col gap-1">
      <div className="mb-2 text-sm text-[var(--color-text-muted)]">
        The always-injected system prompt — {rows.length} files, priority-ordered. P0–P2 never truncated;
        over budget → cut from the bottom up.
      </div>
      {rows.length === 0 && (
        <div className="py-8 text-center text-sm text-[var(--color-text-faint)]">
          Context budget not available yet.
        </div>
      )}
      {rows.map((f) => (
        <div
          key={f.name}
          data-testid={`cm-file-row-${f.name}`}
          data-owner={f.owner}
          className="flex items-center gap-3 rounded-md px-3 py-2 hover:bg-[var(--color-hover)]"
        >
          <span className="w-8 shrink-0 font-mono text-xs text-[var(--color-text-faint)]">P{f.priority}</span>
          <span
            className="w-1.5 h-4 shrink-0 rounded-full"
            style={{ background: OWNER_TINT[f.owner] }}
            aria-hidden
          />
          <span className="flex-1 min-w-0 truncate text-sm font-medium text-[var(--color-text)]">{f.name}</span>
          <span className="shrink-0 text-[11px] text-[var(--color-text-faint)]">{OWNER_LABEL[f.owner]}</span>
          <span className="w-24 shrink-0">
            <span className="block h-1.5 rounded-full bg-[var(--color-border)]">
              <span className="block h-1.5 rounded-full" style={{ width: `${Math.max(2, f.pct)}%`, background: OWNER_TINT[f.owner] }} />
            </span>
          </span>
          <span className="w-14 shrink-0 text-right font-mono text-xs text-[var(--color-text-muted)]">{fmtTokens(f.tokens)}</span>
          <span className="w-10 shrink-0 text-right font-mono text-[11px] text-[var(--color-text-faint)]">{f.pct}%</span>
          {f.locked ? (
            <span data-testid="cm-lock" className="w-6 shrink-0 text-center text-[var(--color-text-faint)]" title="P0–P2 never truncated">🔒</span>
          ) : (
            <button
              className="w-6 shrink-0 text-center text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              title={`Open ${f.name}`}
              onClick={() =>
                document.dispatchEvent(new CustomEvent('swarm:open-file', { detail: { path: `.context/${f.name}` } }))
              }
            >
              ✎
            </button>
          )}
        </div>
      ))}
    </div>
  );
}

function Placeholder({ testid, title }: { testid: string; title: string }) {
  return (
    <div data-testid={testid} className="flex flex-col items-center justify-center gap-2 py-16 text-center">
      <div className="text-lg font-semibold text-[var(--color-text)]">{title}</div>
      <p className="max-w-md text-sm text-[var(--color-text-muted)]">Coming in a later cycle.</p>
      <span className="text-[11px] font-mono uppercase tracking-widest text-[var(--color-text-faint)]">placeholder</span>
    </div>
  );
}

function TabBtn({
  testid, label, active, onClick, badge,
}: { testid: string; label: string; active: boolean; onClick: () => void; badge?: number }) {
  return (
    <button
      data-testid={testid}
      onClick={onClick}
      className={
        'flex items-center gap-1.5 rounded-t-md px-3 py-2 text-sm font-medium transition-colors ' +
        (active
          ? 'text-[var(--color-text)] border-b-2 border-[#5fc99a]'
          : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)] border-b-2 border-transparent')
      }
    >
      {label}
      {badge != null && <span className="rounded-full bg-[var(--color-hover)] px-1.5 text-[10px] text-[var(--color-text-faint)]">{badge}</span>}
    </button>
  );
}

function NeedsBtn({ testid, label, count, tint }: { testid: string; label: string; count: number; tint: string }) {
  return (
    <div
      data-testid={testid}
      className="flex items-center gap-2 rounded-md border border-[var(--color-border)] px-2.5 py-1.5 text-sm"
    >
      <span className="w-1.5 h-1.5 rounded-full" style={{ background: tint }} aria-hidden />
      <span className="flex-1 text-[var(--color-text-muted)]">{label}</span>
      <span className="font-mono font-semibold text-[var(--color-text)]">{count}</span>
    </div>
  );
}
