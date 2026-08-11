/**
 * Tabbed TSCC context panel content — faithful to the approved mockup
 * (Reports/tscc-context-redesign.html): summary strip, per-file token bars with
 * ownership colors, stacked composition chart, recall cards with BM25 scores,
 * security grade badge + severity grid.
 *
 * DATA LOADING: the panel FETCHES on open (not only via the post-turn SSE event).
 * Every fetch is owned by the shell (`SystemPromptModule`) so each datum is
 * requested exactly once — the summary strip and the tabs share one result.
 * - Files/Prompt: uses the SSE `metadata` prop if present, else fetches
 *   GET /chat/{id}/system-prompt so a freshly-opened tab is never blank.
 * - Recall: GET /chat/{id}/recall on open — a cheap read of a snapshot already
 *   stashed during the turn; it never re-runs recall.
 * - Security: GET /chat/{id}/security-scan, deferred until the Security tab is
 *   first opened. That scan regexes the whole assembled prompt, so a panel that
 *   is only used for Flow/Files must not pay for it.
 * Nothing here touches the chat send path.
 *
 * Key exports:
 * - ``SystemPromptModule`` — the tabbed panel body (name kept for call-site compat)
 */

import { useState, useCallback, useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import type {
  SystemPromptMetadata,
  RecallSnapshot,
  SecurityScanResult,
} from '../../../types';
import {
  getSystemPromptMetadata,
  getRecallSnapshot,
  getSecurityScan,
} from '../../../services/tscc';

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface SystemPromptModuleProps {
  sessionId: string | null;
  metadata: SystemPromptMetadata | null;
}

type TabKey = 'flow' | 'files' | 'recall' | 'security' | 'prompt';

/** Lifecycle of a shell-owned fetch. `idle` means "not started yet" — which for
 *  the security scan is a real, lasting state: it only starts when the Security
 *  tab is first opened, so a panel that is never taken there costs zero scans. */
type FetchState = 'idle' | 'loading' | 'done' | 'error';

// ---------------------------------------------------------------------------
// Ownership classification (matches backend context_directory_loader priority)
// ---------------------------------------------------------------------------

interface Owner { label: string; color: string; }
const OWNER: Record<string, Owner> = {
  'SWARMAI.md': { label: 'sys', color: '#6ea8fe' },
  'IDENTITY.md': { label: 'sys', color: '#6ea8fe' },
  'SOUL.md': { label: 'sys', color: '#6ea8fe' },
  'AGENT.md': { label: 'sys', color: '#6ea8fe' },
  'SELF.md': { label: 'agent', color: '#a78bfa' },
  'USER.md': { label: 'user', color: '#4ade80' },
  'STEERING.md': { label: 'user', color: '#4ade80' },
  'TOOLS.md': { label: 'user', color: '#4ade80' },
  'MEMORY.md': { label: 'agent', color: '#a78bfa' },
  'EVOLUTION.md': { label: 'agent', color: '#a78bfa' },
  'KNOWLEDGE.md': { label: 'gen', color: '#38d9c4' },
  'PROJECTS.md': { label: 'gen', color: '#38d9c4' },
};
function ownerOf(filename: string): Owner {
  return OWNER[filename] ?? { label: 'gen', color: '#8b93a7' };
}

function fmtK(n: number): string {
  if (n < 1000) return String(n);
  const k = n / 1000;
  // Round budgets read as "100K"/"50K", not "100.0K"; 1900 still reads "1.9K".
  return `${k % 1 === 0 ? k : k.toFixed(1)}K`;
}

// ---------------------------------------------------------------------------
// Tab 0: Flow — the system-prompt assembly pipeline (original requirement #1).
// STATIC annotated diagram: the assembly stages are a fixed, documented pipeline
// (prompt_builder.build_system_prompt -> context_directory_loader.load_all), so a
// numbered vertical flow with fn names + chips IS the deliverable — no fetch.
// ---------------------------------------------------------------------------

type ChipTone = 'b' | 'p' | 'g' | 'a' | '';
interface FlowStage {
  n: number;
  title: string;
  fn: string;
  desc: string;
  chips: { t: string; tone: ChipTone }[];
  accent?: 'accent' | 'recall' | 'gate';
}

const FLOW_STAGES: FlowStage[] = [
  { n: 1, title: 'Compute dynamic budget', fn: 'compute_token_budget()', accent: 'accent',
    desc: "Set the context-file budget ceiling from the model's context window.",
    chips: [{ t: '≥500K → 100K', tone: 'b' }, { t: '≥200K → 50K', tone: '' }, { t: '≥64K → 30K', tone: '' }, { t: '<64K → L0 cache', tone: 'a' }] },
  { n: 2, title: 'L1 cache check', fn: '_load_l1_if_fresh()',
    desc: 'Models ≥64K try the full L1_SYSTEM_PROMPTS.md cache: reused only if git status (15s TTL) + budget header match.',
    chips: [{ t: 'hit → jump to 8', tone: '' }, { t: 'memory_smart/privacy → bypass', tone: 'a' }] },
  { n: 3, title: 'Read 12 context files in order', fn: '_assemble_from_sources()',
    desc: 'P0→P10 sequential read, each _clean_content() (strip comments, redundant H1), filter dormant/archived MEMORY entries.',
    chips: [{ t: 'SWARMAI', tone: 'b' }, { t: 'IDENTITY', tone: 'b' }, { t: 'SOUL', tone: 'b' }, { t: 'SELF', tone: '' }, { t: 'AGENT', tone: '' }, { t: 'USER', tone: 'g' }, { t: 'STEERING', tone: 'g' }, { t: 'TOOLS', tone: 'g' }, { t: 'MEMORY', tone: 'p' }, { t: 'EVOLUTION', tone: 'p' }, { t: 'KNOWLEDGE', tone: '' }, { t: 'PROJECTS', tone: '' }] },
  { n: 4, title: 'Smart memory selection', fn: 'select_memory_sections()', accent: 'recall',
    desc: 'When MEMORY.md > 30K → inject the L0 compact index (~300–500 tok) + BM25-score the top 0–3 sections (not the full file). Excluded sections stay recallable.',
    chips: [{ t: 'L0 index always on', tone: 'p' }, { t: 'L1 section BM25', tone: 'p' }, { t: 'THRESHOLD 0.15', tone: '' }] },
  { n: 5, title: 'Multi-domain recall fusion', fn: 'recall_all() → render', accent: 'recall',
    desc: 'Pure-filesystem keyword search (allow_embed=False, vector infra off). 5 domains serial, injected with [RECALLED] provenance tags.',
    chips: [{ t: 'context_files', tone: 'p' }, { t: 'ddd', tone: 'p' }, { t: 'library', tone: 'p' }, { t: 'session', tone: '' }, { t: 'codeintel', tone: '' }] },
  { n: 6, title: 'Budget check (warn only)', fn: '_enforce_token_budget()', accent: 'gate',
    desc: 'Sum section tokens incl. ## headers. Over budget → WARNING log, inject full, no truncation (design §3.5). CJK 1.1 tok/char, Latin 2.2 tok/word.',
    chips: [{ t: 'WARN 70%', tone: 'a' }, { t: 'CRITICAL 85%', tone: 'a' }] },
  { n: 7, title: 'Ephemeral injections', fn: 'not cached',
    desc: 'Dynamic, session-specific content appended at the tail.',
    chips: [{ t: 'DailyActivity ×2', tone: '' }, { t: 'briefing', tone: '' }, { t: 'UserObserver', tone: '' }, { t: 'sibling digest', tone: '' }, { t: 'resume ctx', tone: '' }, { t: 'UI state', tone: '' }] },
  { n: 8, title: 'Return final System Prompt', fn: 'system_prompt', accent: 'accent',
    desc: 'Join sections with \\n\\n → full prompt; metadata stored for this panel (GET /chat/{id}/system-prompt).',
    chips: [] },
];

const CHIP_TONE: Record<ChipTone, string> = {
  b: 'text-[#6ea8fe] border-[#6ea8fe]/30',
  p: 'text-[#a78bfa] border-[#a78bfa]/30',
  g: 'text-[#4ade80] border-[#4ade80]/30',
  a: 'text-[#fbbf24] border-[#fbbf24]/30',
  '': 'text-[var(--color-text-muted)] border-[var(--color-border)]',
};
const ACCENT_BORDER: Record<string, string> = {
  accent: 'border-[#6ea8fe]', recall: 'border-[#a78bfa]', gate: 'border-[#4ade80]',
};

function FlowTab() {
  return (
    <div>
      <p className="text-[11px] text-[var(--color-text-muted)] mb-3 leading-relaxed">
        How the system prompt is assembled each turn — <span className="font-mono">build_system_prompt() → load_all()</span>.
        The read path only assembles; over-budget warns, never truncates.
      </p>
      <div className="relative">
        {FLOW_STAGES.map((s, i) => (
          <div key={s.n} className="flex gap-2.5">
            {/* rail: numbered node + connector line */}
            <div className="flex flex-col items-center flex-shrink-0">
              <div className="w-6 h-6 rounded-full flex items-center justify-center text-[11px] font-bold font-mono border-2 border-[#6ea8fe] text-[#6ea8fe] bg-[var(--color-card)] z-10">
                {s.n}
              </div>
              {i < FLOW_STAGES.length - 1 && <div className="w-0.5 flex-1 bg-[var(--color-border)] my-0.5" />}
            </div>
            {/* stage card */}
            <div className={`flex-1 mb-2.5 rounded-lg border p-2.5 bg-[var(--color-hover)]/30 ${s.accent ? ACCENT_BORDER[s.accent] : 'border-[var(--color-border)]'}`}>
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-[12.5px] font-semibold text-[var(--color-text)]">{s.title}</span>
                <span className="font-mono text-[10px] px-1.5 py-0.5 rounded bg-[#38d9c4]/10 text-[#38d9c4]">{s.fn}</span>
              </div>
              <div className="text-[11px] text-[var(--color-text-muted)] mt-1 leading-snug">{s.desc}</div>
              {s.chips.length > 0 && (
                <div className="flex flex-wrap gap-1 mt-1.5">
                  {s.chips.map((c, j) => (
                    <span key={j} className={`text-[9.5px] font-mono px-1.5 py-0.5 rounded-full border ${CHIP_TONE[c.tone]}`}>{c.t}</span>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ===========================================================================
// Full Prompt Modal
// ===========================================================================

function FullPromptModal({ fullText, onClose }: { fullText: string; onClose: () => void }) {
  // Escape closes the modal ONLY. Registered in the CAPTURE phase with
  // stopImmediatePropagation so it runs before the popover's own document-level
  // keydown listener (which would otherwise close the whole popover). Without
  // this, Escape would tear down the popover, not just the modal.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopImmediatePropagation();
        e.preventDefault();
        onClose();
      }
    };
    document.addEventListener('keydown', onKey, true); // capture
    return () => document.removeEventListener('keydown', onKey, true);
  }, [onClose]);

  // Rendered via a portal to document.body so `fixed inset-0` resolves against the
  // viewport. The modal is otherwise a DOM descendant of the popover's
  // `.animate-tscc-panel` element, whose forwards-filled animation retains a
  // non-none transform (scale(1)) — a transformed ancestor becomes the containing
  // block for fixed descendants, which trapped the modal inside the ~720px popover
  // box (run_4ddaee2c). Portaling also escapes the popover's overflow-hidden clip.
  return createPortal(
    <div
      // stopPropagation on mousedown: the popover installs a document-level
      // mousedown "click-outside → close" listener (TSCCPopoverButton). Once the
      // modal is portaled to <body> it is OUTSIDE the popover's ref, so a click on
      // the modal would be seen as "outside" and close the whole popover. Stopping
      // mousedown here keeps the underlying panel open so the user backs out of the
      // modal TO the panel, not out of both.
      onMouseDown={(e) => e.stopPropagation()}
      // z above the popover (zIndex 9999): both are fixed siblings under <body>,
      // so paint order is guaranteed, not left to incidental non-overlap geometry.
      className="fixed inset-0 z-[10000] flex items-center justify-center bg-black/50"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="Full system prompt"
    >
      <div
        className="w-[92vw] max-w-5xl max-h-[90vh] flex flex-col bg-[var(--color-card)] border-2 border-[var(--color-primary)]/70 rounded-lg shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--color-border)]">
          <h3 className="text-sm font-semibold text-[var(--color-text)]">System Prompt</h3>
          <button
            onClick={onClose}
            className="p-1 rounded hover:bg-[var(--color-hover)] text-[var(--color-text-muted)]"
            aria-label="Close"
          >
            <span className="material-symbols-outlined text-base">close</span>
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-5">
          <pre className="text-[13px] text-[var(--color-text)] whitespace-pre-wrap font-mono leading-relaxed">
            {fullText || '(empty)'}
          </pre>
        </div>
      </div>
    </div>,
    document.body,
  );
}

// ===========================================================================
// Summary strip (mockup header stats)
// ===========================================================================

function SummaryStrip({
  metadata,
  recall,
  security,
}: {
  metadata: SystemPromptMetadata | null;
  recall: RecallSnapshot | null;
  security: SecurityScanResult | null;
}) {
  const fileCount = metadata?.files.length ?? 0;
  const totalTok = metadata?.totalTokens ?? 0;
  // The model's REAL budget tier, not the 100K one. See PromptTab.
  const budget = metadata?.effectiveTokenBudget ?? 0;
  // REAL hit count from structured hits — not a regex guess on rendered text.
  const recallHits = recall?.ran ? recall.hits.length : 0;
  const grade = security?.grade ?? '—';

  const stats: { k: string; v: string; cls: string }[] = [
    { k: 'Files', v: String(fileCount), cls: 'text-[#6ea8fe]' },
    { k: 'Tokens', v: fmtK(totalTok), cls: 'text-[#fbbf24]' },
    { k: 'Budget', v: budget > 0 ? fmtK(budget) : '—', cls: 'text-[#4ade80]' },
    { k: 'Recall', v: recall?.ran ? String(recallHits) : '0', cls: 'text-[#a78bfa]' },
    { k: 'Security', v: grade, cls: 'text-[#38d9c4]' },
  ];

  return (
    <div className="grid grid-cols-5 gap-1.5 mb-3">
      {stats.map((s) => (
        <div key={s.k} className="bg-[var(--color-hover)]/50 border border-[var(--color-border)] rounded-lg px-2 py-1.5">
          <div className="text-[8px] uppercase tracking-wide text-[var(--color-text-muted)] truncate">{s.k}</div>
          <div className={`text-base font-bold font-mono ${s.cls}`}>{s.v}</div>
        </div>
      ))}
    </div>
  );
}

// ===========================================================================
// Tab 1: Files — per-file token bars + stacked composition
// ===========================================================================

function FilesTab({ metadata }: { metadata: SystemPromptMetadata | null }) {
  const files = metadata?.files ?? [];
  const totalTokens = metadata?.totalTokens ?? 0;
  const maxTok = files.reduce((m, f) => Math.max(m, f.tokens), 1);

  if (files.length === 0) {
    return <p className="text-sm text-[var(--color-text-muted)] italic py-4">No context files loaded</p>;
  }

  return (
    <div>
      {/* Stacked composition bar */}
      <div className="flex h-6 rounded-md overflow-hidden border border-[var(--color-border)] mb-1">
        {files.map((f) => {
          const w = (f.tokens / totalTokens) * 100;
          const o = ownerOf(f.filename);
          return (
            <span
              key={f.filename}
              style={{ width: `${w}%`, background: o.color }}
              title={`${f.filename}: ${f.tokens.toLocaleString()} tok`}
            />
          );
        })}
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[9px] text-[var(--color-text-muted)] mb-3">
        {[['sys', '#6ea8fe'], ['user', '#4ade80'], ['agent', '#a78bfa'], ['gen', '#38d9c4']].map(([l, c]) => (
          <span key={l} className="flex items-center gap-1">
            <span className="inline-block w-2 h-2 rounded-sm" style={{ background: c as string }} />
            {l}
          </span>
        ))}
      </div>

      {/* Per-file rows with token bars */}
      <ul className="text-sm space-y-1.5">
        {files.map((f) => {
          const o = ownerOf(f.filename);
          const pct = Math.round((f.tokens / maxTok) * 100);
          const share = ((f.tokens / totalTokens) * 100).toFixed(1);
          return (
            <li key={f.filename}>
              <div className="flex items-center justify-between gap-2">
                <span className="text-[var(--color-text)] truncate flex items-center gap-1.5 text-[12px]">
                  <span className="w-2 h-2 rounded-sm flex-shrink-0" style={{ background: o.color }} />
                  <span className="font-mono">{f.filename}</span>
                  <span className="text-[9px] px-1 rounded" style={{ background: `${o.color}22`, color: o.color }}>{o.label}</span>
                  {f.truncated && (
                    <span className="text-[9px] px-1 rounded bg-amber-500/20 text-amber-500" title="Smart-selected to fit budget">smart</span>
                  )}
                </span>
                <span className="text-[11px] text-[var(--color-text-muted)] tabular-nums flex-shrink-0">
                  {f.tokens.toLocaleString()} · {share}%
                </span>
              </div>
              <div className="h-1 rounded bg-[var(--color-border)] mt-1 overflow-hidden">
                <div className="h-full rounded" style={{ width: `${pct}%`, background: o.color }} />
              </div>
            </li>
          );
        })}
      </ul>
      <div className="flex items-center justify-between text-xs text-[var(--color-text-muted)] pt-2 mt-2 border-t border-[var(--color-border)]">
        <span>Total ({files.length} files)</span>
        <span className="tabular-nums font-medium">{totalTokens.toLocaleString()} tokens</span>
      </div>
    </div>
  );
}

// ===========================================================================
// Tab 2: Recall — provenance body (mockup: cards; real data: rendered markdown)
// ===========================================================================

/** Presentational: the shell owns the single fetch (see FetchState) so opening
 *  this tab issues no request of its own — the summary strip and this tab read
 *  the SAME snapshot instead of fetching one each. */
function RecallTab({ snap, state }: { snap: RecallSnapshot | null; state: FetchState }) {
  if (state === 'loading' || state === 'idle') return <p className="text-sm text-[var(--color-text-muted)] py-4">Loading…</p>;
  if (state === 'error') return <p className="text-sm text-[var(--color-text-muted)] italic py-4">Failed to load recall snapshot</p>;
  if (!snap || !snap.ran) {
    return (
      <p className="text-sm text-[var(--color-text-muted)] italic py-4">
        No recall ran this session — recall fires once, on the first substantive
        message (skipped for channels / short openers).
      </p>
    );
  }

  // Group the REAL structured hits by domain into the mockup's source cards.
  const DOMAIN_META: Record<string, { icon: string; label: string; sub: string }> = {
    context_files: { icon: '🧩', label: 'Memory', sub: 'MEMORY.md' },
    library: { icon: '📚', label: 'Library', sub: 'Knowledge/' },
    ddd: { icon: '🏗️', label: 'DDD', sub: 'Project' },
    session: { icon: '💬', label: 'Sessions', sub: 'past chats' },
    codeintel: { icon: '🔣', label: 'Code', sub: 'symbols' },
  };
  const byDomain = new Map<string, typeof snap.hits>();
  for (const h of snap.hits) {
    if (!byDomain.has(h.domain)) byDomain.set(h.domain, []);
    byDomain.get(h.domain)!.push(h);
  }

  return (
    <div>
      <div className="flex items-center gap-3 text-xs text-[var(--color-text-muted)] mb-2 pb-2 border-b border-[var(--color-border)]">
        <span className="tabular-nums">~{snap.tokens.toLocaleString()} tok</span>
        <span className="tabular-nums">{Math.round(snap.latencyMs)} ms</span>
        <span className="tabular-nums">{snap.hits.length} hits</span>
        <span className="text-[10px] px-1.5 rounded bg-[#6ea8fe]/15 text-[#6ea8fe] font-mono">keyword / FTS5</span>
      </div>
      {snap.keywords.length > 0 && (
        <div className="flex flex-wrap gap-1 mb-3">
          {snap.keywords.slice(0, 12).map((k, i) => (
            <span key={i} className="text-[10px] px-1.5 py-0.5 rounded-full bg-[var(--color-hover)] text-[var(--color-text-muted)] font-mono">{k}</span>
          ))}
        </div>
      )}

      {/* Source cards — the mockup's per-domain recall cards with real BM25 scores */}
      {snap.hits.length > 0 ? (
        <div className="space-y-3">
          {[...byDomain.entries()].map(([domain, hits]) => {
            const m = DOMAIN_META[domain] ?? { icon: '📄', label: domain, sub: '' };
            const method = hits[0]?.method || 'keyword';
            return (
              <div key={domain} className="border border-[var(--color-border)] rounded-lg overflow-hidden">
                <div className="flex items-center justify-between px-3 py-2 border-b border-[var(--color-border)] bg-[var(--color-hover)]/30">
                  <span className="text-[13px] font-semibold flex items-center gap-1.5">
                    <span>{m.icon}</span>{m.label}
                    <span className="text-[10px] font-normal text-[var(--color-text-muted)]">{m.sub}</span>
                  </span>
                  <span className="text-[9px] px-1.5 rounded font-mono bg-[#38d9c4]/13 text-[#38d9c4]">{method}</span>
                </div>
                <div className="p-1.5 space-y-0.5">
                  {hits.map((h, i) => (
                    <div key={i} className="px-2 py-1.5 rounded hover:bg-[var(--color-hover)]/40">
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-[11px] text-[var(--color-text-muted)] flex-1 truncate">{h.source || '(source)'}</span>
                        {h.hasScore && (
                          <span className="font-mono text-[10.5px] text-[#4ade80] flex-shrink-0">{h.score.toFixed(2)}</span>
                        )}
                      </div>
                      {h.text && (
                        <div className="text-[11.5px] text-[var(--color-text)] mt-0.5 opacity-85 line-clamp-2">{h.text}</div>
                      )}
                    </div>
                  ))}
                </div>
                <div className="px-3 py-1.5 border-t border-[var(--color-border)] text-[10.5px] text-[var(--color-text-muted)] font-mono flex justify-between">
                  <span>{hits.length} hits</span>
                  {hits.some((h) => h.hasScore) && <span>BM25 [0,1]</span>}
                </div>
              </div>
            );
          })}
        </div>
      ) : snap.body ? (
        // Fallback: legacy path with no structured hits — show the rendered body.
        <pre className="text-[12px] text-[var(--color-text)] whitespace-pre-wrap font-mono leading-relaxed opacity-90">{snap.body}</pre>
      ) : (
        // Reachable state, not a placeholder: the keyword leg ran and matched
        // nothing. Distinct from "no recall ran" above — this one means the
        // query's wording missed, which is the load-bearing failure mode now
        // that the vector leg is retired.
        <p className="text-sm text-[var(--color-text-muted)] italic py-2">
          Recall ran but matched nothing — the agent was prompted to grep
          <span className="font-mono not-italic"> Knowledge/ </span>
          with synonyms instead.
        </p>
      )}
    </div>
  );
}

// ===========================================================================
// Tab 3: Security — grade badge + severity grid + findings (mockup pane 5)
// ===========================================================================

const SEV_COLOR: Record<string, string> = {
  critical: '#f87171', high: '#fb923c', medium: '#fbbf24', info: '#6ea8fe',
};
const GRADE_COLOR: Record<string, string> = {
  A: '#4ade80', 'A-': '#4ade80', B: '#fbbf24', C: '#f87171', 'n/a': '#8b93a7',
};

/** Presentational: the shell owns the single scan and only starts it when this
 *  tab is first opened. The scan is NOT a cached read — it walks the whole
 *  assembled prompt (up to ~400KB) through every credential detector — so it
 *  must not run for someone who only glances at the Flow tab, and must not run
 *  twice because the strip and this tab each asked for it. */
function SecurityTab({ scan, state }: { scan: SecurityScanResult | null; state: FetchState }) {
  if (state === 'loading' || state === 'idle') return <p className="text-sm text-[var(--color-text-muted)] py-4">Scanning…</p>;
  if (state === 'error' || !scan) return <p className="text-sm text-[var(--color-text-muted)] italic py-4">Failed to run security scan</p>;
  if (scan.grade === 'n/a') return <p className="text-sm text-[var(--color-text-muted)] italic py-4">No assembled prompt to scan yet</p>;

  const gc = GRADE_COLOR[scan.grade] ?? '#8b93a7';
  const sevCells: [string, number, string][] = [
    ['Critical', scan.critical, SEV_COLOR.critical],
    ['High', scan.high, SEV_COLOR.high],
    ['Medium', scan.medium, SEV_COLOR.medium],
    ['Info', scan.info, SEV_COLOR.info],
  ];

  return (
    <div>
      {/* Verdict */}
      <div className="flex items-center gap-3 mb-3">
        <div className="w-12 h-12 rounded-lg flex items-center justify-center text-xl font-bold font-mono flex-shrink-0"
             style={{ background: `${gc}22`, color: gc }}>
          {scan.grade}
        </div>
        <div className="text-xs text-[var(--color-text-muted)]">
          Static scan of the assembled prompt · secrets masked · reuses egress credential detectors.
        </div>
      </div>
      {/* Severity grid */}
      <div className="grid grid-cols-4 gap-2 mb-3">
        {sevCells.map(([l, c, col]) => (
          <div key={l} className="bg-[var(--color-hover)]/50 border border-[var(--color-border)] rounded-lg py-2 text-center">
            <div className="text-lg font-bold font-mono" style={{ color: c > 0 ? col : 'var(--color-text-muted)' }}>{c}</div>
            <div className="text-[9px] uppercase tracking-wide text-[var(--color-text-muted)]">{l}</div>
          </div>
        ))}
      </div>
      {/* Findings */}
      <ul className="space-y-1.5">
        {scan.findings.map((f, i) => {
          const pass = f.status === 'pass';
          const sc = pass ? '#4ade80' : (SEV_COLOR[f.severity] ?? '#8b93a7');
          return (
            <li key={i} className="flex items-start gap-2 p-2 rounded bg-[var(--color-hover)]/40">
              <span className="material-symbols-outlined text-sm mt-0.5" style={{ color: sc }}>
                {pass ? 'check_circle' : 'warning'}
              </span>
              <div className="flex-1 min-w-0">
                <div className="text-[13px] font-medium text-[var(--color-text)] flex items-center gap-2">
                  {f.detector}
                  <span className="text-[9px] px-1.5 rounded uppercase font-mono" style={{ background: `${sc}22`, color: sc }}>
                    {pass ? 'pass' : f.severity}
                  </span>
                </div>
                <div className="text-[11px] text-[var(--color-text-muted)] mt-0.5 break-words">{f.detail}</div>
              </div>
            </li>
          );
        })}
      </ul>
      <p className="text-[10px] text-[var(--color-text-muted)] italic mt-2 pt-2 border-t border-[var(--color-border)]">
        Design-time scan of assembled context, not a runtime output filter.
      </p>
    </div>
  );
}

// ===========================================================================
// Tab 4: Prompt — token budget gauge + full-text launcher
// ===========================================================================

/** The gauge's x-axis runs to 1.5x the budget, so the budget marker sits at 2/3. */
const GAUGE_HEADROOM = 1.5;
const BUDGET_MARKER_PCT = 100 / GAUGE_HEADROOM;

function PromptTab({
  sessionId,
  metadata,
}: {
  sessionId: string;
  metadata: SystemPromptMetadata | null;
}) {
  const [showModal, setShowModal] = useState(false);
  const [fullText, setFullText] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const handleView = useCallback(async () => {
    if (metadata?.fullText) { setFullText(metadata.fullText); setShowModal(true); return; }
    setIsLoading(true);
    try {
      const result = await getSystemPromptMetadata(sessionId);
      setFullText(result ? result.fullText : '(Session not initialized yet)');
      setShowModal(true);
    } catch {
      setFullText('(Failed to load system prompt)');
      setShowModal(true);
    } finally { setIsLoading(false); }
  }, [sessionId, metadata?.fullText]);

  const total = metadata?.totalTokens ?? 0;
  // The REAL budget for this model, from the backend. It is NOT always 100K —
  // that is only the >=500K-window tier. Hardcoding it made a 45K prompt on a
  // 200K model read "45% · in budget" when it was actually at 90% of its ceiling.
  const budget = metadata?.effectiveTokenBudget ?? 0;
  const hasBudget = budget > 0;
  const pctOfBudget = hasBudget
    ? Math.min(150, Math.round((total / budget) * 100))
    : 0;
  const over = hasBudget && total > budget;
  const barPct = hasBudget
    ? Math.min(100, (total / (budget * GAUGE_HEADROOM)) * 100)
    : 0;

  return (
    <div>
      {/* Budget gauge */}
      <div className="flex items-center gap-3 mb-3">
        <div className="min-w-[70px]">
          <div className="text-lg font-bold font-mono" style={{ color: over ? '#fbbf24' : 'var(--color-text)' }}>{fmtK(total)}</div>
          <div className="text-[10px] text-[var(--color-text-muted)]">assembled</div>
        </div>
        {hasBudget ? (
          <>
            <div className="flex-1">
              <div className="h-2.5 rounded-full bg-[var(--color-border)] overflow-hidden relative">
                <div className="h-full rounded-full" style={{ width: `${barPct}%`, background: over ? 'linear-gradient(90deg,#4ade80,#fbbf24)' : '#4ade80' }} />
                <div className="absolute top-[-2px] w-0.5 h-[14px] bg-[#f87171]" style={{ left: `${BUDGET_MARKER_PCT}%` }} title={`budget ${fmtK(budget)}`} />
              </div>
              <div className="flex justify-between text-[9px] text-[var(--color-text-muted)] mt-1 font-mono">
                <span>0</span>
                <span className="text-[#f87171]">↑ {fmtK(budget)} budget</span>
                <span>{fmtK(budget * GAUGE_HEADROOM)}</span>
              </div>
            </div>
            <div className="text-right min-w-[64px]">
              <div className="text-[12px] font-mono font-semibold" style={{ color: over ? '#f87171' : '#4ade80' }}>{pctOfBudget}%</div>
              <div className="text-[9px] text-[var(--color-text-muted)]">{over ? 'no cut · warn' : 'in budget'}</div>
            </div>
          </>
        ) : (
          // No budget reported. Drawing a gauge would mean inventing a ceiling,
          // which is the bug this replaced — say "unknown" instead.
          <div className="flex-1 text-[10px] text-[var(--color-text-muted)] italic">
            Budget not reported for this session — percentage unavailable.
          </div>
        )}
      </div>

      <button
        onClick={handleView}
        disabled={isLoading}
        className="w-full text-xs py-1.5 px-3 rounded bg-[var(--color-hover)] text-[var(--color-text)] hover:bg-[var(--color-border)] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {isLoading ? 'Loading…' : 'View Full Prompt'}
      </button>
      <p className="text-[10px] text-[var(--color-text-muted)] italic mt-2">
        CJK 1.1 tok/char + Latin 2.2 tok/word (calibrated). Read path never hard-cuts; over-budget warns only.
      </p>
      {showModal && fullText !== null && <FullPromptModal fullText={fullText} onClose={() => setShowModal(false)} />}
    </div>
  );
}

// ===========================================================================
// SystemPromptModule — tabbed shell (fetches on open)
// ===========================================================================

const TABS: { key: TabKey; label: string; icon: string }[] = [
  { key: 'flow', label: 'Flow', icon: 'account_tree' },
  { key: 'files', label: 'Files', icon: 'description' },
  { key: 'recall', label: 'Recall', icon: 'travel_explore' },
  { key: 'security', label: 'Security', icon: 'security' },
  { key: 'prompt', label: 'Prompt', icon: 'article' },
];

export function SystemPromptModule({ sessionId, metadata: metadataProp }: SystemPromptModuleProps) {
  const [tab, setTab] = useState<TabKey>('flow');
  // Fetch-on-open: the SSE `metadata` prop is only present after a turn streams
  // in THIS tab. On a fresh/idle tab it is null → fetch it so the panel is never
  // blank. The prop, when present, wins (it is the freshest, from the live turn).
  const [fetched, setFetched] = useState<SystemPromptMetadata | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!sessionId) return;
    if (metadataProp) { setFetched(null); return; } // prop is fresher; don't shadow it
    let alive = true;
    setLoading(true);
    getSystemPromptMetadata(sessionId)
      .then((m) => alive && setFetched(m))
      .catch(() => alive && setFetched(null))
      .finally(() => alive && setLoading(false));
    return () => { alive = false; };
  }, [sessionId, metadataProp]);

  const metadata = metadataProp ?? fetched;

  // ── Shell-owned fetches: exactly one request per datum ──────────────────
  // Both the summary strip and the corresponding tab read these, so the fetches
  // live here rather than inside the tabs. Previously the shell prefetched both
  // AND each tab fetched its own, so opening Security ran the scan twice.
  const [recall, setRecall] = useState<RecallSnapshot | null>(null);
  const [recallState, setRecallState] = useState<FetchState>('idle');
  const [security, setSecurity] = useState<SecurityScanResult | null>(null);
  const [securityState, setSecurityState] = useState<FetchState>('idle');

  // Recall is an eager fetch: the endpoint returns a snapshot that was already
  // stashed during the turn, so it is a cheap dict read with no recall re-run.
  useEffect(() => {
    if (!sessionId) return;
    let alive = true;
    setRecallState('loading');
    getRecallSnapshot(sessionId)
      .then((r) => { if (alive) { setRecall(r); setRecallState('done'); } })
      .catch(() => { if (alive) setRecallState('error'); });
    return () => { alive = false; };
  }, [sessionId]);

  // Security is a LAZY fetch, started only once the Security tab is opened. The
  // endpoint's contract is "runs when the user opens the security panel, never
  // during message send", and the scan itself is expensive — regexing the whole
  // assembled prompt. Firing it on every panel open would break both.
  //
  // The started-flag is a ref keyed by session, not state, for two reasons: a
  // state flag in this effect's deps would re-run the effect the moment it set
  // `loading` and cancel its own request, and switching tabs mid-scan must not
  // discard the result (the shell stays mounted, so the scan is still wanted).
  // Results are therefore applied unless the component unmounted or the session
  // changed underneath them.
  const scannedForRef = useRef<string | null>(null);
  const mountedRef = useRef(true);
  useEffect(() => () => { mountedRef.current = false; }, []);

  useEffect(() => {
    if (!sessionId) return;
    // A new session invalidates any previous scan and its displayed grade.
    if (scannedForRef.current !== null && scannedForRef.current !== sessionId) {
      scannedForRef.current = null;
      setSecurity(null);
      setSecurityState('idle');
    }
    if (tab !== 'security' || scannedForRef.current === sessionId) return;
    scannedForRef.current = sessionId;
    const sid = sessionId;
    setSecurityState('loading');
    getSecurityScan(sid)
      .then((s) => {
        if (!mountedRef.current || scannedForRef.current !== sid) return;
        setSecurity(s);
        setSecurityState('done');
      })
      .catch(() => {
        if (!mountedRef.current || scannedForRef.current !== sid) return;
        setSecurityState('error');
      });
  }, [sessionId, tab]);

  if (!sessionId) {
    return <p className="text-sm text-[var(--color-text-muted)] italic">No active session</p>;
  }

  return (
    <div className="flex flex-col h-full">
      <h4 className="text-xs font-semibold uppercase tracking-wide text-[var(--color-text-muted)] mb-2">
        System Prompt
      </h4>

      {/* Fail-loud degradation banner. The backend logs an error when a core
          context section is missing, but a log the user never sees is not
          "loud" — this is the consumer that makes the signal visible. */}
      {metadata?.degraded && (
        <div className="flex items-start gap-2 mb-3 px-2.5 py-2 rounded-lg border border-[#f87171]/40 bg-[#f87171]/10">
          <span className="material-symbols-outlined text-[16px] text-[#f87171] flex-shrink-0">warning</span>
          <div className="min-w-0">
            <div className="text-[11px] font-semibold text-[#f87171]">Prompt assembled incomplete</div>
            <div className="text-[10.5px] font-mono text-[var(--color-text-muted)] break-words">{metadata.degraded}</div>
          </div>
        </div>
      )}

      {/* Summary strip */}
      <SummaryStrip metadata={metadata} recall={recall} security={security} />

      {/* Tab bar */}
      <div className="flex gap-0.5 border-b border-[var(--color-border)] mb-3">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`flex-1 flex items-center justify-center gap-1 py-1.5 text-[11px] font-medium border-b-2 -mb-px transition-colors whitespace-nowrap overflow-hidden ${
              tab === t.key
                ? 'text-[var(--color-primary)] border-[var(--color-primary)]'
                : 'text-[var(--color-text-muted)] border-transparent hover:text-[var(--color-text)]'
            }`}
          >
            <span className="material-symbols-outlined text-[14px] flex-shrink-0">{t.icon}</span>
            {/* whitespace-nowrap + overflow-hidden on the button (above) prevents any
                wrap; truncate keeps a long label from pushing siblings — the icon
                always stays visible so the tab is identifiable even if text clips. */}
            <span className="truncate">{t.label}</span>
          </button>
        ))}
      </div>

      {/* Tab body (scrolls within the panel) */}
      <div className="flex-1 overflow-y-auto min-h-0">
        {tab === 'flow' && <FlowTab />}
        {tab === 'files' && (loading && !metadata
          ? <p className="text-sm text-[var(--color-text-muted)] py-4">Loading…</p>
          : <FilesTab metadata={metadata} />)}
        {tab === 'recall' && <RecallTab snap={recall} state={recallState} />}
        {tab === 'security' && <SecurityTab scan={security} state={securityState} />}
        {tab === 'prompt' && <PromptTab sessionId={sessionId} metadata={metadata} />}
      </div>
    </div>
  );
}
