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
type HealthTag = 'fresh' | 'idle' | 'growing' | 'oversized';
interface TokenFileRow {
  name: string;
  tokens: number;
  pct: number;
  owner: 'system' | 'user' | 'agent' | 'auto';
  priority: number;
  locked: boolean;
  health?: HealthTag;
}

// Health tag → tint (backend decides the tag; UI only colors it). fresh=calm,
// idle=muted, growing=amber-warn, oversized=red-risk.
const HEALTH_TINT: Record<HealthTag, string> = {
  fresh: '#5fc99a',
  idle: '#7c8194',
  growing: '#d08a4a',
  oversized: '#d0524a',
};
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
            {tab === 'memory' && <MemoryTab enabled={open && tab === 'memory'} />}
            {tab === 'guideline' && <GuidelineTab />}
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
      <div className="mb-3 text-[11px] text-[var(--color-text-faint)]">
        🔒 P0–P2 never truncated · over budget → cut from P10 upward · Health: fresh / idle / growing / oversized
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
          // §4 group+cap: cap the row width (max-w-3xl) so the metadata cluster sits
          // adjacent to the filename instead of a screen away on a wide (xl) panel —
          // kills the dead-space void. The name span KEEPS `flex-1 min-w-0 truncate`
          // (Gate-1: that pairing IS the truncation bound — dropping flex-1 would let a
          // long filename overflow instead of truncating); inside the cap the residual
          // it absorbs is small, so no stretch-band. NOTE (out of scope, follow-up): at
          // the 320px min panel width the fixed columns (~416px) already overflow — a
          // CSS-grid rework is the correct long-term fix for that pre-existing edge.
          className="flex items-center gap-3 rounded-md px-3 py-2 max-w-3xl hover:bg-[var(--color-hover)]"
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
          {f.health && (
            <span
              data-testid="cm-health"
              className="w-16 shrink-0 text-center text-[10px] font-medium rounded px-1 py-[1px]"
              style={{ color: HEALTH_TINT[f.health], background: `color-mix(in srgb, ${HEALTH_TINT[f.health]} 12%, transparent)` }}
            >
              {f.health}
            </span>
          )}
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

// ── Memory tab: the sedimented 7-type knowledge (DoD2) ──────────────────────
interface GraphNode { type: string; count: number; active: number; dormant: number; }
interface DrillEntry { title: string; status: string; ref_count: number; meta: string; }
interface BrainGraph { nodes: GraphNode[]; drill: Record<string, DrillEntry[]>; total: number; }
interface TrendPoint { date: string; prompt_tokens: number; memory_bytes: number; }
interface BrainTrend { points: TrendPoint[]; count: number; launch_date: string | null; }

// 7-type tint (aligns to the ontology; stable across renders).
const TYPE_TINT: Record<string, string> = {
  principle: '#5fc99a', correction: '#d0524a', decision: '#4a8fb0', guideline: '#b08fd0',
  pitfall: '#d08a4a', process: '#7c8194', model: '#5f9ec9',
};

function MemoryTab({ enabled }: { enabled: boolean }) {
  const { data: graph } = useQuery<BrainGraph>({
    queryKey: ['cm-brain-graph'],
    queryFn: async () => (await api.get<BrainGraph>('/eval/brain-graph')).data,
    staleTime: 30_000, enabled,
  });
  const { data: trend } = useQuery<BrainTrend>({
    queryKey: ['cm-brain-trend'],
    queryFn: async () => (await api.get<BrainTrend>('/eval/brain-trend')).data,
    staleTime: 30_000, enabled,
  });
  const [selType, setSelType] = useState<string | null>(null);

  const nodes = graph?.nodes ?? [];
  const maxCount = Math.max(1, ...nodes.map((n) => n.count));
  const drill = (selType && graph?.drill[selType]) || [];

  return (
    <div data-testid="cm-panel-memory" className="flex flex-col gap-5 max-w-4xl">
      <div className="text-sm text-[var(--color-text-muted)]">
        The judgment I've sedimented across all conversations — a 7-type ontology.
        Value (not age) decides survival: idle entries dim, load-bearing ones persist.
      </div>

      {/* 7-type graph — nodes sized by entry count */}
      <section>
        <div className="mb-2 text-[11px] font-mono uppercase tracking-wider text-[var(--color-text-faint)]">
          Knowledge graph · 7 types as nodes (click to drill in)
        </div>
        <div className="flex flex-wrap gap-3">
          {nodes.map((n) => {
            const tint = TYPE_TINT[n.type] ?? '#7c8194';
            const size = 44 + Math.round((n.count / maxCount) * 40); // 44-84px by count
            const sel = selType === n.type;
            return (
              <button
                key={n.type}
                data-testid={`cm-graph-node-${n.type}`}
                onClick={() => setSelType(sel ? null : n.type)}
                title={`${n.type}: ${n.count} (${n.active} active · ${n.dormant} dim)`}
                className="flex flex-col items-center justify-center rounded-full border-2 transition-transform hover:scale-105 shrink-0"
                style={{
                  width: size, height: size,
                  borderColor: tint,
                  background: `color-mix(in srgb, ${tint} ${n.dormant > n.active ? 8 : 16}%, transparent)`,
                  boxShadow: sel ? `0 0 0 3px color-mix(in srgb, ${tint} 40%, transparent)` : 'none',
                }}
              >
                <span className="font-mono text-sm font-extrabold" style={{ color: tint }}>{n.count}</span>
                <span className="font-mono text-[9px] font-bold" style={{ color: tint }}>{n.type.slice(0, 4)}</span>
              </button>
            );
          })}
        </div>
        <div className="mt-1.5 text-[10px] text-[var(--color-text-faint)]">
          node size = entry count · bright = active · dim = dormant/archived
        </div>
      </section>

      {/* by-type distribution bars (also drill) */}
      <section>
        <div className="mb-2 text-[11px] font-mono uppercase tracking-wider text-[var(--color-text-faint)]">
          By-type distribution
        </div>
        <div className="flex flex-col gap-1">
          {nodes.map((n) => {
            const tint = TYPE_TINT[n.type] ?? '#7c8194';
            return (
              <button
                key={n.type}
                data-testid={`cm-bar-${n.type}`}
                onClick={() => setSelType(n.type)}
                className="flex items-center gap-2 rounded px-1 py-0.5 text-left hover:bg-[var(--color-hover)]"
              >
                <span className="w-16 shrink-0 font-mono text-[11px] text-[var(--color-text-muted)]">{n.type}</span>
                <span className="flex-1 h-2 rounded-full bg-[var(--color-border)] max-w-md">
                  <span className="block h-2 rounded-full" style={{ width: `${Math.max(3, (n.count / maxCount) * 100)}%`, background: tint }} />
                </span>
                <span className="w-8 shrink-0 text-right font-mono text-[11px] text-[var(--color-text-faint)]">{n.count}</span>
              </button>
            );
          })}
        </div>
      </section>

      {/* drill-down list */}
      <section>
        <div data-testid="cm-drill-list" className="rounded-lg border border-[var(--color-border)] p-3">
          {!selType ? (
            <div className="text-[11px] text-[var(--color-text-faint)]">👆 Click a graph node (or a bar) → latest entries of that type</div>
          ) : drill.length === 0 ? (
            <div className="text-[11px] text-[var(--color-text-faint)]">No <b>{selType}</b> entries yet.</div>
          ) : (
            <>
              <div className="mb-1.5 text-[11px] font-semibold text-[var(--color-text)]">Latest {selType} ({drill.length})</div>
              <div className="flex flex-col gap-1">
                {drill.map((e, i) => (
                  <div key={i} className="flex items-center gap-2 text-xs">
                    <span className={'w-1.5 h-1.5 rounded-full shrink-0'} style={{ background: e.status === 'active' ? '#5fc99a' : '#7c8194' }} />
                    <span className="min-w-0 flex-1 truncate text-[var(--color-text)]">{e.title}</span>
                    <span className="shrink-0 font-mono text-[10px] text-[var(--color-text-faint)]">{e.meta}</span>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </section>

      {/* MEMORY.md size trend (from the daily snapshot series) */}
      <section>
        <div className="mb-2 text-[11px] font-mono uppercase tracking-wider text-[var(--color-text-faint)]">
          MEMORY.md size trend
        </div>
        <TrendChart trend={trend} field="memory_bytes" />
      </section>

      <div className="text-[11px] text-[var(--color-text-faint)]">
        How it works: every message recalls relevant entries (FTS5/BM25); reflection sediments new ones (confident-only); idle entries decay while load-bearing judgment survives.
      </div>
    </div>
  );
}

// Trend line chart from the daily size-snapshot series. R30: NEVER fabricates a
// baseline — <2 real points shows an explicit "collecting since launch" state.
function TrendChart({ trend, field }: { trend: BrainTrend | undefined; field: 'memory_bytes' | 'prompt_tokens' }) {
  const pts = trend?.points ?? [];
  if (pts.length < 2) {
    const since = trend?.launch_date;
    return (
      <div data-testid="cm-trend-collecting" className="rounded-lg border border-dashed border-[var(--color-border)] p-4 text-center text-[11px] text-[var(--color-text-faint)]">
        📈 Collecting since {since ?? 'launch'} — the trend appears after 2 daily snapshots.
      </div>
    );
  }
  const vals = pts.map((p) => p[field]);
  const min = Math.min(...vals), max = Math.max(...vals);
  const range = max - min || 1;
  const W = 300, H = 60;
  const path = pts.map((p, i) => {
    const x = (i / (pts.length - 1)) * W;
    const y = H - ((p[field] - min) / range) * H;
    return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  return (
    <svg data-testid="cm-trend-svg" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="w-full h-14">
      <path d={path} fill="none" stroke="#5fc99a" strokeWidth="1.5" />
    </svg>
  );
}

// ── Guideline tab: static teaching content — "how a powerful agent brain works".
// R30: describes MECHANISMS, not counts — NO baked numbers (they'd drift). All
// content is stable architecture fact, safe to hardcode.
const LIFECYCLE: Array<{ key: string; icon: string; title: string; desc: string }> = [
  { key: 'assemble', icon: '📥', title: 'Assemble', desc: 'context files → prompt, by priority' },
  { key: 'recall', icon: '🔍', title: 'Recall', desc: 'FTS5/BM25 pulls relevant memory' },
  { key: 'judge', icon: '🧠', title: 'Judge', desc: 'the model reasons on that context' },
  { key: 'sediment', icon: '💧', title: 'Sediment', desc: 'reflect → new entries (confident-only)' },
  { key: 'decay', icon: '🍂', title: 'Decay', desc: 'idle sinks, value survives' },
];
const AUTO_ITEMS: Array<{ icon: string; name: string; desc: string; tag: string }> = [
  { icon: '🔍', name: 'Recall', desc: 'every message, keyword-matched injection', tag: 'hook' },
  { icon: '💧', name: 'Cultivation', desc: 'grows DDD docs from sessions, quality-gated', tag: 'hook' },
  { icon: '🍂', name: 'Decay & archive', desc: 'dormant then archived by idle age', tag: 'job' },
  { icon: '📋', name: 'Session briefing', desc: 'start-of-session cognition inject', tag: 'hook' },
  { icon: '🧬', name: 'Evolution capture', desc: 'corrections → pattern detection', tag: 'hook' },
];
const MANUAL_ITEMS: Array<{ icon: string; name: string; desc: string; tag: string }> = [
  { icon: '🧭', name: 'STEERING rules', desc: 'your standing directives (highest precedence)', tag: 'file' },
  { icon: '👤', name: 'USER profile', desc: 'who you are, how you like to work', tag: 'file' },
  { icon: '🧩', name: 'Skill allowlist', desc: 'which capabilities this agent may use', tag: 'config' },
  { icon: '🔌', name: 'MCP tiers', desc: 'always-on vs on-demand tool servers', tag: 'config' },
  { icon: '🗂', name: 'Create a DDD', desc: 'a domain brain per project (Brain Hub)', tag: 'chat' },
];
const HOOK_CHIPS = ['context_health', 'memory_edit_guard', 'ddd_cultivation', 'knowledge_backflow', 'correction_capture', 'session_briefing', 'high_signal_capture'];
const SKILL_CHIPS = ['s_persist', 's_memory-distill', 's_self-evolution', 's_project-manager', 's_ddd-*', 's_golden-case'];

function GuidelineTab() {
  return (
    <div data-testid="cm-panel-guideline" className="flex flex-col gap-5 max-w-4xl">
      <div className="text-sm text-[var(--color-text-muted)]">
        How a powerful agent brain works — the lifecycle every message flows through, what runs
        itself vs what you steer, and the machinery underneath.
      </div>

      {/* Lifecycle flow */}
      <section>
        <div className="mb-2 text-[11px] font-mono uppercase tracking-wider text-[var(--color-text-faint)]">
          Lifecycle — every message flows through this
        </div>
        <div data-testid="cm-guideline-lifecycle" className="flex items-stretch gap-2">
          {LIFECYCLE.map((s, i) => (
            <div key={s.key} className="flex items-center gap-2 flex-1 min-w-0">
              <div
                data-testid={`cm-lc-${s.key}`}
                className="flex-1 min-w-0 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] p-2.5 text-center"
              >
                <div className="text-lg leading-none">{s.icon}</div>
                <div className="mt-1 text-xs font-semibold text-[var(--color-text)]">{s.title}</div>
                <div className="mt-0.5 text-[10px] leading-tight text-[var(--color-text-muted)]">{s.desc}</div>
              </div>
              {i < LIFECYCLE.length - 1 && <span className="shrink-0 text-[var(--color-text-faint)]">→</span>}
            </div>
          ))}
        </div>
      </section>

      {/* Automatic vs Manual */}
      <section>
        <div className="mb-2 text-[11px] font-mono uppercase tracking-wider text-[var(--color-text-faint)]">
          Automatic vs Manual — what runs itself, what you steer
        </div>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <AmColumn testid="cm-guideline-automatic" head="🤖 Runs itself" badge="AUTOMATIC" items={AUTO_ITEMS} accent="#5fc99a" />
          <AmColumn testid="cm-guideline-manual" head="🖐 You configure" badge="MANUAL" items={MANUAL_ITEMS} accent="#4a8fb0" />
        </div>
      </section>

      {/* Reference chips */}
      <section>
        <div className="mb-2 text-[11px] font-mono uppercase tracking-wider text-[var(--color-text-faint)]">
          Under the hood — the machinery
        </div>
        <div data-testid="cm-guideline-chips" className="flex flex-col gap-2">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="mr-1 text-[11px] text-[var(--color-text-muted)]">Hooks (fire automatically):</span>
            {HOOK_CHIPS.map((c) => (
              <span key={c} className="rounded-md border border-[color-mix(in_srgb,#5fc99a_35%,var(--color-border))] px-1.5 py-[1px] font-mono text-[10px] text-[var(--color-text-muted)]">{c}</span>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="mr-1 text-[11px] text-[var(--color-text-muted)]">Skills (you invoke):</span>
            {SKILL_CHIPS.map((c) => (
              <span key={c} className="rounded-md border border-[color-mix(in_srgb,#4a8fb0_35%,var(--color-border))] px-1.5 py-[1px] font-mono text-[10px] text-[var(--color-text-muted)]">{c}</span>
            ))}
          </div>
        </div>
      </section>
    </div>
  );
}

function AmColumn({
  testid, head, badge, items, accent,
}: { testid: string; head: string; badge: string; items: Array<{ icon: string; name: string; desc: string; tag: string }>; accent: string }) {
  return (
    <div data-testid={testid} className="rounded-lg border border-[var(--color-border)] p-3">
      <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-[var(--color-text)]">
        {head}
        <span className="rounded-full px-2 py-[1px] font-mono text-[9px] tracking-wider" style={{ background: `color-mix(in srgb, ${accent} 16%, transparent)`, color: accent }}>{badge}</span>
      </div>
      <div className="flex flex-col gap-1.5">
        {items.map((it) => (
          <div key={it.name} className="flex items-center gap-2">
            <span className="shrink-0 text-sm">{it.icon}</span>
            <div className="min-w-0 flex-1">
              <div className="text-xs font-medium text-[var(--color-text)]">{it.name}</div>
              <div className="truncate text-[11px] text-[var(--color-text-muted)]">{it.desc}</div>
            </div>
            <span className="shrink-0 rounded border border-[var(--color-border)] px-1.5 py-[1px] font-mono text-[9px] text-[var(--color-text-faint)]">{it.tag}</span>
          </div>
        ))}
      </div>
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
