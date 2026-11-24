/**
 * CMBrainOverlay — the Context & Memory overlay (design Knowledge/Mockups/
 * 2026-11-24-cm-library-redesign.html, the "Context & Memory" panel). Four tabs, each
 * answering one question:
 *
 * - 上下文 (context): what every turn carries and who takes the room. One number (the
 *   injected tokens) against the budget, a bar split by owner, the files grouped by owner
 *   (Swarm learned / yours / built in / any other), biggest first. Amber cards on top only
 *   when the person must decide: a governance rule waiting for approval, or the context
 *   over its budget. Rows open the file in Canvas.
 * - 记忆 (memory): MEMORY.md and EVOLUTION.md on one page, each with its entry count, its
 *   size against 7 days ago, a type bar whose legend filters the newest-first list. One
 *   search box over both files' entries and their archives.
 * - 知识 (knowledge): when knowledge is needed, is it found: the share of session starts
 *   whose recall brought something back, the three recalls this week, and how much each
 *   store holds (links to Brain Hub, the Library, the 记忆 tab).
 * - 工作原理 (how it works): how one turn's input is assembled, five layers top to
 *   bottom, with the live numbers the backend gives (system prompt size, recalls brought
 *   back, what CDF wrote this week).
 *
 * Data is backend-primary: GET /eval/context-health/lite (token_block, the governance
 * count, the north star's every-turn sizes), /eval/governance/pending (only when the
 * count says there is one), /eval/brain-graph, /eval/archive-list and -search (both
 * sources), /recall/knowledge, /ingestion/landed (the Cognition flow's 7-day query).
 * The frontend invents no numbers.
 *
 * @exports CMBrainContent
 */
import { useEffect, useState, type ReactNode } from 'react';
import { Trans, useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { overlayQuery } from './overlayQueries';
import api, { classifyLoadError } from '../../services/api';
import { ingestionService } from '../../services/ingestion';
import { OverlayError } from './OverlayState';
import { Tabs } from '../overlay';
import { Big, CARD, Compare, DIM, MUTED, ZoneLabel, useGrown } from './needYouParts';
import { placeTotals } from './cdfText';

// ── Types (as served, snake_case) ─────────────────────────────────────────────────────
interface TokenFileRow {
  name: string;
  tokens: number;          // INJECTED: what the file puts in the prompt
  disk_tokens?: number;    // the whole file on disk
  pct: number;
  owner: string;           // system | user | agent | auto | …
  priority: number;
  locked: boolean;
  health?: string;
  health_counts?: { entries: number; duplicate: number } | null;
}
interface TokenBlock {
  total_tokens: number;
  disk_tokens?: number;
  budget: number;
  warning_threshold: number;
  emergency_threshold: number;
  over_budget: boolean;
  per_file: TokenFileRow[];
}
interface NorthStar {
  at: string;
  measures: { cdf?: { every_turn?: Record<string, [number, number]> } | null } & Record<string, unknown>;
}
interface ContextHealthLite {
  token_block?: TokenBlock | null;
  governance_pending_count?: number;
  north_star?: NorthStar | null;
}
interface GraphEntry { title: string; meta: string; type: string; date: string | null; id?: string | null; label?: string }
interface BrainGraph { nodes: Array<{ type: string; count: number }>; entries?: GraphEntry[]; total: number }
type Source = 'memory' | 'evolution';
interface ArchiveListResp { files: Array<{ name: string; entry_count: number }>; total_files: number }
interface ArchiveSearchHit { title: string; snippet: string; source_file: string; shard: string }
interface RecallWindow { runs: number; hits: number; prev_runs: number; prev_hits: number }
interface RecallKnowledge {
  window_days: number;
  contexts: Record<string, RecallWindow>;
  stores: {
    projects: { brains: number; entries: number } | null;
    general: { files: number } | null;
    sessions: number | null;
    archives: { files: number; entries: number } | null;
  };
}

type TabKey = 'context' | 'memory' | 'knowledge' | 'how';
export type CMOverlayTarget = 'library' | 'brain-hub';

const LITE_QUERY_KEY = overlayQuery.contextLite().queryKey;
const GOV_KEY = ['cm-governance-pending'] as const;
const LEGS = ['session_prompt', 'session_followup', 'session_ddd'] as const;

// Owner → colour (system slate, the person's blue, the agent's cognition green).
const OWNER_ORDER_BAR = ['system', 'user', 'agent'];   // the bar and its legend
const OWNER_ORDER_LIST = ['agent', 'user', 'system'];  // the file list: what changes first
const OWNER_TINT: Record<string, string> = {
  system: 'var(--color-text-dim)', user: 'var(--color-primary)', agent: 'var(--color-zone-cognitive)',
};
const ownerTint = (o: string) => OWNER_TINT[o] ?? 'var(--color-text-muted)';
const TYPE_TINT: Record<string, string> = {
  principle: 'var(--color-status-success)', correction: 'var(--color-status-error)',
  decision: 'var(--color-zone-cognitive)', model: 'var(--color-text-secondary)',
  guideline: 'var(--color-primary)', pitfall: 'var(--color-zone-work)', process: 'var(--color-text-dim)',
  competence: 'var(--color-status-success)', class: 'var(--color-zone-work)',
};
const typeTint = (t: string) => TYPE_TINT[t] ?? 'var(--color-text-muted)';
const LIST_SHOWN = 6;
const WEEK_MS = 7 * 24 * 3600 * 1000;

// ── Number helpers ────────────────────────────────────────────────────────────────────
/** A size in K with one decimal (rows, groups): 15269 → "15.3K", 587 → "0.6K". */
const k1 = (n: number) => `${(n / 1000).toFixed(1)}K`;
/** A headline size in whole K: 71896 → "72K". */
const kRound = (n: number) => (n >= 10_000 ? `${Math.round(n / 1000)}K` : k1(n));
const pctOf = (a: number, b: number) => (b > 0 ? Math.round((a / b) * 100) : 0);
const baseName = (f: string) => f.replace(/\.md$/i, '');
const openFile = (path: string) =>
  document.dispatchEvent(new CustomEvent('swarm:open-file', { detail: { path } }));
/** A file is only partly injected (TIMELINE: its summaries) when the file on disk is 10x. */
const partlyInjected = (f: TokenFileRow) => typeof f.disk_tokens === 'number' && f.tokens > 0 && f.disk_tokens >= 10 * f.tokens;

function groupsOf(rows: TokenFileRow[], order: string[]): Array<{ owner: string; files: TokenFileRow[]; total: number }> {
  const owners = [...order.filter((o) => rows.some((r) => r.owner === o)),
    ...[...new Set(rows.map((r) => r.owner))].filter((o) => !order.includes(o))];
  return owners.map((owner) => {
    const files = rows.filter((r) => r.owner === owner).sort((a, b) => b.tokens - a.tokens);
    return { owner, files, total: files.reduce((n, f) => n + f.tokens, 0) };
  });
}

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setV(value), ms);
    return () => clearTimeout(id);
  }, [value, ms]);
  return v;
}

/** The 7-days-ago size of a file from the north star's every-turn readings. */
function weekAgoOf(ns: NorthStar | null | undefined, file: string): number | null {
  const v = ns?.measures?.cdf?.every_turn?.[baseName(file)];
  return Array.isArray(v) && typeof v[1] === 'number' ? v[1] : null;
}

// ── Shell ─────────────────────────────────────────────────────────────────────────────
/**
 * CMBrainContent — mounted by the OverlayHost only while the surface is open, so its
 * queries are always enabled. `onOpenOverlay` switches to Brain Hub or the Library (the
 * 知识 tab's links); without it those rows are not links.
 */
export function CMBrainContent({ onOpenOverlay }: { onOpenOverlay?: (id: CMOverlayTarget) => void } = {}) {
  const { t } = useTranslation();
  const [tab, setTab] = useState<TabKey>('context');
  const lite = useQuery({ ...overlayQuery.contextLite<ContextHealthLite>() });
  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="cm-brain-overlay">
      <div className="border-b border-[var(--color-border)] px-6 py-3">
        <Tabs label={t('ui.CMBrainOverlay.tabsLabel')} value={tab} onChange={setTab} items={[
          { key: 'context', label: t('ui.CMBrainOverlay.tabContext'), testid: 'cm-tab-context',
            count: (lite.data?.governance_pending_count ?? 0) > 0 ? lite.data?.governance_pending_count : undefined, tone: 'alert' },
          { key: 'memory', label: t('ui.CMBrainOverlay.tabMemory'), testid: 'cm-tab-memory' },
          { key: 'knowledge', label: t('ui.CMBrainOverlay.tabKnowledge'), testid: 'cm-tab-knowledge' },
          { key: 'how', label: t('ui.CMBrainOverlay.tabHow'), testid: 'cm-tab-how' },
        ]} />
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-7 pb-9 pt-6">
        <div key={tab} className="so-view-in mx-auto max-w-[880px]">
          {tab === 'context' && <ContextTab />}
          {tab === 'memory' && <MemoryTab />}
          {tab === 'knowledge' && <KnowledgeTab onOpenOverlay={onOpenOverlay} onOpenMemory={() => setTab('memory')} />}
          {tab === 'how' && <HowTab />}
        </div>
      </div>
    </div>
  );
}

// ── 上下文 ────────────────────────────────────────────────────────────────────────────
function ContextTab() {
  const { t } = useTranslation();
  const lite = useQuery({ ...overlayQuery.contextLite<ContextHealthLite>() });
  const block = lite.data?.token_block ?? null;
  const pending = lite.data?.governance_pending_count ?? 0;
  const ns = lite.data?.north_star;
  return (
    <div data-testid="cm-panel-context" className="flex flex-col gap-9">
      {lite.isError && !lite.data && (
        <OverlayError testid="cm-needs-error" retryTestid="cm-needs-retry"
          message={classifyLoadError(lite.error, t('ui.CMBrainOverlay.theContext'), t('ui.CMBrainOverlay.loadError'))}
          onRetry={() => { void lite.refetch(); }} />
      )}
      {(pending > 0 || block?.over_budget) && (
        <section className="flex flex-col gap-2.5" data-testid="cm-cards">
          {pending > 0 && <GovernanceCards />}
          {block?.over_budget && <OverBudgetCard block={block} />}
        </section>
      )}
      {block && <LoadVerdict block={block} />}
      {block ? <FileGroups block={block} ns={ns} /> : lite.data && (
        <p className={`text-body ${DIM}`}>{t('ui.CMBrainOverlay.budgetNotAvailable')}</p>
      )}
    </div>
  );
}

function LoadVerdict({ block }: { block: TokenBlock }) {
  const { t } = useTranslation();
  const groups = groupsOf(block.per_file, OWNER_ORDER_BAR);
  const total = block.total_tokens || 1;
  const grown = useGrown(`${block.total_tokens}/${block.budget}`);
  const head = kRound(block.total_tokens);
  return (
    <section data-testid="cm-load">
      <Big testid="cm-load-big" value={head.replace(/K$/, '')} unit={head.endsWith('K') ? 'K' : undefined}
        what={t('ui.CMBrainOverlay.injectedWhat')}
        compare={<Compare testid="cm-load-budget" was={t('ui.CMBrainOverlay.ofBudget', { pct: pctOf(block.total_tokens, block.budget), budget: kRound(block.budget) })} />} />
      <div className="relative mt-4 h-2.5 rounded-[5px] bg-[var(--color-hover)]" data-testid="cm-budget-bar">
        <div className="so-width absolute inset-y-0 left-0 flex gap-0.5 overflow-hidden rounded-[5px]"
          style={{ width: grown ? `${Math.min(100, (block.total_tokens / (block.budget || total)) * 100)}%` : '0%' }}>
          {groups.map((g) => (
            <i key={g.owner} className="block h-full" data-testid={`cm-budget-seg-${g.owner}`}
              style={{ width: `${(g.total / total) * 100}%`, background: ownerTint(g.owner) }} />
          ))}
        </div>
      </div>
      <div className="mt-3 flex flex-wrap gap-x-2 gap-y-1.5" data-testid="cm-load-legend">
        {groups.map((g) => (
          <span key={g.owner} className={`inline-flex items-center gap-1.5 rounded-full border border-[var(--color-border)] px-2.5 py-0.5 text-small ${MUTED}`}>
            <i aria-hidden className="inline-block h-2 w-2 rounded-sm" style={{ background: ownerTint(g.owner) }} />
            {t(`ui.CMBrainOverlay.owner_${g.owner}`, { defaultValue: g.owner })}
            <b className="font-semibold tabular-nums text-[var(--color-text)]">{k1(g.total)}</b>
          </span>
        ))}
      </div>
    </section>
  );
}

function FileGroups({ block, ns }: { block: TokenBlock; ns: NorthStar | null | undefined }) {
  const { t, i18n } = useTranslation();
  const groups = groupsOf(block.per_file, OWNER_ORDER_LIST);
  return (
    <section data-testid="cm-files">
      {groups.map((g, gi) => {
        const agos = g.files.map((f) => weekAgoOf(ns, f.name));
        const groupAgo = agos.every((v) => v != null) ? agos.reduce<number>((n, v) => n + (v ?? 0), 0) : null;
        return (
          <div key={g.owner} data-testid={`cm-group-${g.owner}`} className={gi ? 'mt-5' : ''}>
            <div className="mb-1 flex items-baseline gap-2.5">
              <i aria-hidden className="inline-block h-2 w-2 self-center rounded-sm" style={{ background: ownerTint(g.owner) }} />
              <span className="text-body font-semibold">{t(`ui.CMBrainOverlay.owner_${g.owner}`, { defaultValue: g.owner })}</span>
              <span className={`ml-auto text-small tabular-nums ${MUTED}`} data-testid={`cm-group-total-${g.owner}`}>
                <b className="font-semibold text-[var(--color-text-secondary)]">{k1(g.total)}</b>
                {groupAgo != null && <> · {t('ui.CMBrainOverlay.weekAgo', { tokens: k1(groupAgo) })}</>}
              </span>
            </div>
            <div className="-mx-2.5 flex flex-col divide-y divide-[var(--color-border)]">
              {g.files.map((f) => {
                const base = baseName(f.name);
                const roleKey = `ui.CMBrainOverlay.role_${base}`;
                const role = i18n.exists(roleKey) ? t(roleKey) : '';
                const entries = f.health_counts?.entries;
                const label = entries != null && role ? `${t('ui.CMBrainOverlay.entries', { count: entries })} · ${role}` : role;
                const ago = weekAgoOf(ns, f.name);
                const delta = ago != null ? Math.round((f.tokens - ago) / 100) / 10 : null;
                return (
                  <button key={f.name} type="button" data-testid={`cm-file-row-${f.name}`} data-owner={f.owner}
                    title={t('ui.CMBrainOverlay.openInCanvas', { name: f.name })}
                    onClick={() => openFile(`.context/${f.name}`)}
                    className="grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 rounded-lg px-2.5 py-2 text-left hover:bg-[var(--color-hover)]">
                    <span className="min-w-0 truncate text-body">
                      {f.name}
                      {label && <span className={`ml-2 text-small ${DIM}`} data-testid="cm-file-role">{label}</span>}
                      {f.health_counts && f.health_counts.duplicate > 0 && (
                        <span className="ml-2 text-small text-[var(--color-status-warning)]" data-testid={`cm-health-counts-${f.name}`}>
                          {t('ui.CMBrainOverlay.dupCount', { count: f.health_counts.duplicate })}
                        </span>
                      )}
                    </span>
                    <span className={`flex items-baseline gap-3.5 whitespace-nowrap text-small tabular-nums ${MUTED}`}>
                      <b className="font-semibold text-[var(--color-text-secondary)]" data-testid="cm-file-tokens">{k1(f.tokens)}</b>
                      {delta != null && delta !== 0 && (
                        <span data-testid="cm-file-delta">{delta > 0 ? '+' : '−'}{Math.abs(delta).toFixed(1)}K</span>
                      )}
                      {partlyInjected(f) && (
                        <span data-testid="cm-file-disk">{t('ui.CMBrainOverlay.onDisk', { tokens: kRound(f.disk_tokens as number) })}</span>
                      )}
                      {f.locked && (
                        <span data-testid="cm-lock" className={`material-symbols-outlined icon-xs ${DIM}`} title={t('ui.CMBrainOverlay.lockTitle')}>lock</span>
                      )}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        );
      })}
    </section>
  );
}

/** An amber card: what the person must decide, its reason, its actions. */
function PersonCard({ title, pill, why, children, testid }: {
  title: string; pill: string; why?: ReactNode; children?: ReactNode; testid?: string;
}) {
  return (
    <article className={`${CARD} so-card--you relative overflow-hidden px-4 py-3.5`} data-testid={testid}
      style={{ borderColor: 'color-mix(in srgb, var(--color-status-warning) 55%, var(--color-border))' }}>
      <div className="flex min-w-0 items-center gap-2.5">
        <span className="min-w-0 flex-1 truncate text-body font-semibold" title={title}>{title}</span>
        <span className="so-pop whitespace-nowrap rounded-full px-2.5 py-0.5 text-meta font-semibold text-[var(--color-bg)]"
          style={{ background: 'var(--color-status-warning)' }}>{pill}</span>
      </div>
      <div className="mt-3 rounded-lg px-3 py-2.5" style={{ background: 'color-mix(in srgb, var(--color-status-warning) 8%, transparent)' }}>
        {why && <div className="text-body text-[var(--color-text-secondary)]">{why}</div>}
        <div className={`flex flex-wrap items-center gap-2 ${why ? 'mt-2.5' : ''}`}>{children}</div>
      </div>
    </article>
  );
}

function GovernanceCards() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  // the list is fetched only when the lite count says there is something to decide
  const gov = useQuery<{ proposals: Array<Record<string, unknown>>; total: number }>({
    queryKey: GOV_KEY,
    queryFn: async () => (await api.get<{ proposals: Array<Record<string, unknown>>; total: number }>('/eval/governance/pending')).data,
    staleTime: 30_000,
  });
  async function decide(id: string, decision: 'accept' | 'reject' | 'defer') {
    await api.post('/eval/governance/decision', { proposal_id: id, decision });
    await Promise.all([qc.invalidateQueries({ queryKey: LITE_QUERY_KEY }), qc.invalidateQueries({ queryKey: GOV_KEY })]);
  }
  if (gov.isError) {
    return <OverlayError compact testid="cm-gov-error" message={classifyLoadError(gov.error, t('ui.CMBrainOverlay.theRules'))}
      onRetry={() => { void gov.refetch(); }} />;
  }
  return (
    <>
      {(gov.data?.proposals ?? []).map((p, i) => (
        <ProposalCard key={String(p.id ?? i)} item={p} onDecide={decide} />
      ))}
    </>
  );
}

const DECIDED_KEY: Record<string, string> = {
  accept: 'ui.CMBrainOverlay.decidedAccept', reject: 'ui.CMBrainOverlay.decidedReject', defer: 'ui.CMBrainOverlay.decidedDefer',
};

function ProposalCard({ item, onDecide }: {
  item: Record<string, unknown>; onDecide: (id: string, decision: 'accept' | 'reject' | 'defer') => Promise<void>;
}) {
  const { t } = useTranslation();
  const id = String(item.id ?? 'unknown');
  const [state, setState] = useState<null | string>(null);
  const [refused, setRefused] = useState<string | null>(null);
  const occ = item.occurrence_count;
  const why = `${String(item.checked ?? '')}${typeof occ === 'number' ? t('ui.CMBrainOverlay.seenTimes', { count: occ }) : ''}`.trim();
  async function act(decision: 'accept' | 'reject' | 'defer') {
    setState(decision);
    setRefused(null);
    try { await onDecide(id, decision); } catch (err) {
      // the backend's reason (e.g. the row quotes a phrase the memory guard blocks) is
      // shown on the card; without it a refused decision looked like a dead button
      const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
      setRefused(typeof detail === 'string' && detail ? detail : t('ui.CMBrainOverlay.decisionNotSaved'));
      setState(null);
    }
  }
  return (
    <PersonCard testid={`cm-proposal-${id}`} pill={t('ui.CMBrainOverlay.needsApproval')}
      title={t('ui.CMBrainOverlay.ruleTitle', { rule: String(item.proposed_rule ?? t('ui.CMBrainOverlay.noRuleText')) })}
      why={(why || refused) && (
        <>
          {why && <p className="line-clamp-3" title={why}>{why}</p>}
          {refused && <p data-testid="cm-card-refused" role="alert" className="mt-1.5 text-small text-[var(--color-status-error)]">{refused}</p>}
        </>
      )}>
      {state ? (
        <span className={`text-small ${MUTED}`}>{t(DECIDED_KEY[state])}</span>
      ) : (
        <>
          <button type="button" data-testid="cm-card-accept" onClick={() => act('accept')} className="btn-sm btn-primary">{t('ui.CMBrainOverlay.approve')}</button>
          <button type="button" data-testid="cm-card-reject" onClick={() => act('reject')} className="btn-sm btn-secondary">{t('ui.CMBrainOverlay.reject')}</button>
          <button type="button" data-testid="cm-card-defer" onClick={() => act('defer')} className="btn-sm btn-ghost">{t('ui.CMBrainOverlay.defer')}</button>
        </>
      )}
    </PersonCard>
  );
}

/** Over the budget: by how much and the files that carry the load, each one click open. */
function OverBudgetCard({ block }: { block: TokenBlock }) {
  const { t } = useTranslation();
  const top = [...block.per_file].filter((f) => f.tokens > 0).sort((a, b) => b.tokens - a.tokens).slice(0, 2);
  return (
    <PersonCard testid="cm-budget-alert" pill={t('ui.CMBrainOverlay.overBudgetPill')}
      title={t('ui.CMBrainOverlay.overBudgetTitle', { amount: k1(block.total_tokens - block.budget) })}
      why={<>{t('ui.CMBrainOverlay.overBudgetWhy')} <span className="tabular-nums">{top.map((f) => `${f.name} ${k1(f.tokens)}`).join(' · ')}</span></>}>
      {top.map((f) => (
        <button key={f.name} type="button" data-testid={`cm-budget-open-${f.name}`} className="btn-sm btn-secondary"
          onClick={() => openFile(`.context/${f.name}`)}>{t('ui.CMBrainOverlay.openFile', { name: f.name })}</button>
      ))}
    </PersonCard>
  );
}

// ── 记忆 ──────────────────────────────────────────────────────────────────────────────
function useGraph(source: Source) {
  return useQuery<BrainGraph>({
    queryKey: ['cm-brain-graph', source],
    queryFn: async () => (await api.get<BrainGraph>(`/eval/brain-graph?source=${source}`)).data,
    staleTime: 30_000,
  });
}
function useArchiveList(source: Source) {
  return useQuery<ArchiveListResp>({
    queryKey: ['cm-archive-list', source],
    queryFn: async () => (await api.get<ArchiveListResp>(`/eval/archive-list?source=${source}`)).data,
    staleTime: 30_000,
  });
}
function useArchiveSearch(source: Source, q: string) {
  return useQuery<{ results: ArchiveSearchHit[] }>({
    queryKey: ['cm-archive-search', source, q],
    queryFn: async () => (await api.get<{ results: ArchiveSearchHit[] }>(`/eval/archive-search?source=${source}&q=${encodeURIComponent(q)}`)).data,
    staleTime: 30_000,
    enabled: q.length > 0,
  });
}

const FILE_OF: Record<Source, string> = { memory: 'MEMORY.md', evolution: 'EVOLUTION.md' };
const entryText = (e: GraphEntry) => (e.id && e.label ? `${e.id} · ${e.label}` : e.label || e.title);
const mmdd = (d: string | null) => (d ? d.slice(5) : '');

function MemoryTab() {
  const { t } = useTranslation();
  const [q, setQ] = useState('');
  const query = q.trim();
  const memory = useGraph('memory');
  const evolution = useGraph('evolution');
  const memList = useArchiveList('memory');
  const evoList = useArchiveList('evolution');
  const lists = [memList.data, evoList.data];
  const archFiles = lists.every(Boolean) ? lists.reduce((n, l) => n + (l?.total_files ?? 0), 0) : null;
  const archEntries = lists.every(Boolean)
    ? lists.reduce((n, l) => n + (l?.files ?? []).reduce((m, f) => m + (f.entry_count || 0), 0), 0) : null;
  return (
    <div data-testid="cm-panel-memory" className="flex flex-col gap-9">
      <label className="so-card flex items-center gap-2.5 rounded-[10px] border border-[var(--color-border)] bg-[var(--color-card)] px-3.5 py-2.5">
        <span aria-hidden className={`material-symbols-outlined icon-sm ${DIM}`}>search</span>
        <input data-testid="cm-memory-search" value={q} onChange={(e) => setQ(e.target.value)}
          placeholder={t('ui.CMBrainOverlay.searchPlaceholder')} aria-label={t('ui.CMBrainOverlay.searchPlaceholder')}
          className="min-w-0 flex-1 bg-transparent text-body text-[var(--color-text)] outline-none placeholder:text-[var(--color-text-dim)]" />
        {query ? (
          <button type="button" data-testid="cm-memory-search-clear" onClick={() => setQ('')}
            className={`text-small ${MUTED} hover:text-[var(--color-text)]`}>{t('ui.CMBrainOverlay.clear')}</button>
        ) : archFiles != null && archFiles > 0 && (
          <span className={`whitespace-nowrap text-meta ${DIM}`} data-testid="cm-memory-search-hint">
            {t('ui.CMBrainOverlay.searchHint', { files: archFiles, entries: archEntries })}
          </span>
        )}
      </label>
      {query ? (
        <SearchResults query={query} graphs={{ memory: memory.data, evolution: evolution.data }} />
      ) : (
        <>
          <EntryZone source="memory" q={memory} label={t('ui.CMBrainOverlay.memoryLabel')} />
          <EntryZone source="evolution" q={evolution} label={t('ui.CMBrainOverlay.evolutionLabel')} />
        </>
      )}
    </div>
  );
}

function EntryZone({ source, q, label }: { source: Source; q: ReturnType<typeof useGraph>; label: string }) {
  const { t } = useTranslation();
  const lite = useQuery({ ...overlayQuery.contextLite<ContextHealthLite>() });
  const [sel, setSel] = useState<string | null>(null);
  const [all, setAll] = useState(false);
  const graph = q.data;
  const nodes = graph?.nodes ?? [];
  const total = graph?.total ?? 0;
  const grown = useGrown(nodes.map((n) => `${n.type}:${n.count}`).join('|'));
  const file = lite.data?.token_block?.per_file.find((f) => f.name === FILE_OF[source]);
  const ago = weekAgoOf(lite.data?.north_star, FILE_OF[source]);
  const entries = graph?.entries ?? [];
  const shown = entries.filter((e) => !sel || e.type === sel);
  const visible = all ? shown : shown.slice(0, LIST_SHOWN);
  // what came in within 7 days (EVOLUTION: the corrections and competences of this week)
  const now = Date.now();
  const fresh = source === 'evolution'
    ? entries.filter((e) => e.date && now - new Date(`${e.date}T00:00:00`).getTime() < WEEK_MS).length : 0;
  const typeLabel = (type: string) => t(source === 'memory' ? `ui.CMBrainOverlay.memType_${type}` : `ui.CMBrainOverlay.evoType_${type.replace(/-/g, '_')}`, { defaultValue: type });
  const pick = (type: string) => { setSel((s) => (s === type ? null : type)); setAll(false); };
  return (
    <section data-testid={`cm-zone-${source}`}>
      <ZoneLabel>{label}</ZoneLabel>
      {q.isError ? (
        <OverlayError compact message={classifyLoadError(q.error, t('ui.CMBrainOverlay.theEntries'))} onRetry={() => { void q.refetch(); }} />
      ) : graph && (
        <>
          <Big testid={`cm-${source}-big`} value={String(total)} unit={t('ui.CMBrainOverlay.entriesUnit')} what=""
            compare={(fresh > 0 || file) ? (
              <span className="flex flex-wrap gap-1.5">
                {fresh > 0 && <Compare testid={`cm-${source}-fresh`} was={t('ui.CMBrainOverlay.thisWeek', { count: fresh })} />}
                {file && <Compare testid={`cm-${source}-size`}
                  was={`${t('ui.CMBrainOverlay.tokensNow', { tokens: k1(file.tokens) })}${ago != null ? ` · ${t('ui.CMBrainOverlay.weekAgo', { tokens: k1(ago) })}` : ''}`} />}
              </span>
            ) : undefined} />
          {total === 0 ? (
            <p className={`mt-3 text-body ${DIM}`}>{t('ui.CMBrainOverlay.noEntries')}</p>
          ) : (
            <>
              <div className="mt-4 flex h-2.5 gap-0.5 overflow-hidden rounded-[5px] bg-[var(--color-hover)]" data-testid={`cm-split-${source}`}>
                {nodes.map((n) => (
                  <button key={n.type} type="button" data-viz aria-label={`${typeLabel(n.type)} ${n.count}`}
                    onClick={() => pick(n.type)} className="so-width block h-full"
                    style={{ width: grown ? `${(n.count / (total || 1)) * 100}%` : '0%', background: typeTint(n.type), opacity: !sel || sel === n.type ? 1 : 0.25 }} />
                ))}
              </div>
              <div className="mt-3 flex flex-wrap gap-x-2 gap-y-1.5">
                {nodes.map((n) => (
                  <button key={n.type} type="button" data-testid={`cm-chip-${source}-${n.type}`} aria-pressed={sel === n.type}
                    onClick={() => pick(n.type)}
                    className={`btn-sm gap-1.5 rounded-full border px-2.5 font-normal transition-opacity ${sel === n.type
                      ? 'border-[var(--color-text-secondary)] bg-[var(--color-card)] text-[var(--color-text)]'
                      : `border-[var(--color-border)] ${MUTED}`} ${sel && sel !== n.type ? 'opacity-45' : ''}`}>
                    <i aria-hidden className="inline-block h-2 w-2 rounded-sm" style={{ background: typeTint(n.type) }} />
                    {typeLabel(n.type)} <b className="font-semibold tabular-nums text-[var(--color-text)]">{n.count}</b>
                  </button>
                ))}
              </div>
              <div className="-mx-2.5 mt-3.5 flex flex-col divide-y divide-[var(--color-border)]" data-testid={`cm-list-${source}`}>
                {visible.map((e, i) => (
                  <button key={`${e.id ?? e.title}-${i}`} type="button" data-testid={`cm-entry-${source}`}
                    onClick={() => openFile(`.context/${FILE_OF[source]}`)} title={e.title}
                    className="grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 rounded-lg px-2.5 py-2 text-left hover:bg-[var(--color-hover)]">
                    <span className="min-w-0 truncate text-body">
                      <span className="mr-2 rounded bg-[var(--color-bg-chrome)] px-1.5 text-meta text-[var(--color-text-secondary)]">{typeLabel(e.type)}</span>
                      {entryText(e)}
                    </span>
                    <span className={`text-small tabular-nums ${MUTED}`}>{mmdd(e.date)}</span>
                  </button>
                ))}
              </div>
              {shown.length > LIST_SHOWN && (
                <button type="button" data-testid={`cm-more-${source}`} onClick={() => setAll((v) => !v)}
                  className="mt-2 text-small text-[var(--color-primary)]">
                  {all ? t('ui.CMBrainOverlay.showLess') : t('ui.CMBrainOverlay.showAll', { count: shown.length })}
                </button>
              )}
            </>
          )}
        </>
      )}
    </section>
  );
}

function SearchResults({ query, graphs }: { query: string; graphs: Record<Source, BrainGraph | undefined> }) {
  const { t } = useTranslation();
  const debounced = useDebounced(query, 250);
  const mem = useArchiveSearch('memory', debounced);
  const evo = useArchiveSearch('evolution', debounced);
  const needle = query.toLowerCase();
  // the main files: their entries are already here (both graphs); the archives: FTS search
  const main = (['memory', 'evolution'] as Source[]).flatMap((source) =>
    (graphs[source]?.entries ?? []).filter((e) => entryText(e).toLowerCase().includes(needle) || e.title.toLowerCase().includes(needle))
      .map((e) => ({ source, e })));
  const archived = [...(mem.data?.results ?? []), ...(evo.data?.results ?? [])];
  const pending = debounced !== query || mem.isFetching || evo.isFetching;
  const failed = mem.isError || evo.isError;
  return (
    <section data-testid="cm-search-results" className="-mx-2.5 flex flex-col divide-y divide-[var(--color-border)]">
      {main.map(({ source, e }, i) => (
        <button key={`m-${source}-${i}`} type="button" data-testid="cm-hit-main" onClick={() => openFile(`.context/${FILE_OF[source]}`)}
          className="grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 rounded-lg px-2.5 py-2 text-left hover:bg-[var(--color-hover)]">
          <span className="min-w-0 truncate text-body">
            <span className="mr-2 rounded bg-[var(--color-bg-chrome)] px-1.5 text-meta text-[var(--color-text-secondary)]">{baseName(FILE_OF[source])}</span>
            {entryText(e)}
          </span>
          <span className={`text-small tabular-nums ${MUTED}`}>{mmdd(e.date)}</span>
        </button>
      ))}
      {archived.map((r, i) => (
        <button key={`a-${r.shard}-${i}`} type="button" data-testid="cm-hit-archive" onClick={() => openFile(`.context/${r.shard}`)}
          className="grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 rounded-lg px-2.5 py-2 text-left hover:bg-[var(--color-hover)]">
          <span className="min-w-0">
            <span className="block truncate text-body">
              <span className={`mr-2 rounded border border-[var(--color-border)] px-1.5 text-meta ${MUTED}`}>{t('ui.CMBrainOverlay.archive')}</span>
              {r.title}
            </span>
            {r.snippet && <span className={`mt-0.5 block truncate text-small ${MUTED}`}>{r.snippet}</span>}
          </span>
          <span className={`font-mono text-meta ${DIM}`}>{baseName(r.shard)}</span>
        </button>
      ))}
      {failed && <OverlayError compact message={classifyLoadError(mem.error ?? evo.error, t('ui.CMBrainOverlay.theSearch'))}
        onRetry={() => { void mem.refetch(); void evo.refetch(); }} />}
      {pending && archived.length === 0 && <p className={`px-2.5 py-2 text-small ${DIM}`}>{t('ui.CMBrainOverlay.searching')}</p>}
      {!pending && !failed && main.length === 0 && archived.length === 0 && (
        <p className={`px-2.5 py-2 text-body ${DIM}`} data-testid="cm-search-empty">{t('ui.CMBrainOverlay.noMatches', { query })}</p>
      )}
    </section>
  );
}

// ── 知识 ──────────────────────────────────────────────────────────────────────────────
function useRecallKnowledge() {
  return useQuery<RecallKnowledge>({
    queryKey: ['cm-recall-knowledge'],
    queryFn: async () => (await api.get<RecallKnowledge>('/recall/knowledge')).data,
    staleTime: 30_000,
  });
}

function KnowledgeTab({ onOpenOverlay, onOpenMemory }: { onOpenOverlay?: (id: CMOverlayTarget) => void; onOpenMemory: () => void }) {
  const { t } = useTranslation();
  const q = useRecallKnowledge();
  const data = q.data;
  const grown = useGrown(data ? LEGS.map((l) => `${data.contexts[l]?.hits}/${data.contexts[l]?.runs}`).join('|') : '');
  if (q.isError) {
    return <div data-testid="cm-panel-knowledge"><OverlayError message={classifyLoadError(q.error, t('ui.CMBrainOverlay.theRecall'))} onRetry={() => { void q.refetch(); }} /></div>;
  }
  if (!data) return <div data-testid="cm-panel-knowledge" />;
  const first = data.contexts.session_prompt;
  const s = data.stores;
  const dash = '—';
  const stores: Array<{ key: string; name: string; value: string; go?: string; onClick?: () => void }> = [
    { key: 'projects', name: t('ui.CMBrainOverlay.storeProjects', { count: s.projects?.brains ?? 0 }),
      value: s.projects ? t('ui.CMBrainOverlay.entries', { count: s.projects.entries }) : dash,
      go: onOpenOverlay && t('ui.CMBrainOverlay.goBrainHub'), onClick: onOpenOverlay && (() => onOpenOverlay('brain-hub')) },
    { key: 'general', name: t('ui.CMBrainOverlay.storeGeneral'),
      value: s.general ? t('ui.CMBrainOverlay.files', { count: s.general.files }) : dash,
      go: onOpenOverlay && t('ui.CMBrainOverlay.goLibrary'), onClick: onOpenOverlay && (() => onOpenOverlay('library')) },
    { key: 'sessions', name: t('ui.CMBrainOverlay.storeSessions'),
      value: s.sessions != null ? t('ui.CMBrainOverlay.sessions', { count: s.sessions }) : dash },
    { key: 'archives', name: t('ui.CMBrainOverlay.storeArchives'),
      value: s.archives ? t('ui.CMBrainOverlay.archivesValue', { files: s.archives.files, entries: s.archives.entries }) : dash,
      go: t('ui.CMBrainOverlay.goMemory'), onClick: onOpenMemory },
  ];
  return (
    <div data-testid="cm-panel-knowledge" className="flex flex-col gap-9">
      <section>
        {first && first.runs > 0 ? (
          <Big testid="cm-recall-big" value={String(pctOf(first.hits, first.runs))} unit="%" what={t('ui.CMBrainOverlay.firstHitWhat')}
            compare={<Compare testid="cm-recall-compare" was={`${t('ui.CMBrainOverlay.firstHitCompare', { hits: first.hits, runs: first.runs })}${first.prev_runs > 0 ? ` · ${t('ui.CMBrainOverlay.lastWeek', { pct: pctOf(first.prev_hits, first.prev_runs) })}` : ''}`} />} />
        ) : (
          <p className={`text-heading ${MUTED}`} data-testid="cm-recall-none">{t('ui.CMBrainOverlay.noRecallYet')}</p>
        )}
      </section>
      <section>
        <ZoneLabel>{t('ui.CMBrainOverlay.legsLabel')}</ZoneLabel>
        <div className="flex flex-col divide-y divide-[var(--color-border)]">
          {LEGS.map((leg) => {
            const w = data.contexts[leg] ?? { runs: 0, hits: 0, prev_runs: 0, prev_hits: 0 };
            return (
              <div key={leg} data-testid={`cm-leg-${leg}`} className="grid grid-cols-1 items-center gap-x-4 gap-y-1.5 py-2.5 md:grid-cols-[minmax(0,1fr)_220px_90px]">
                <div className="min-w-0">
                  <div className="text-body font-semibold">{t(`ui.CMBrainOverlay.leg_${leg}`)}</div>
                  <div className={`text-small ${MUTED}`}>{t(`ui.CMBrainOverlay.legW_${leg}`)}</div>
                </div>
                <div className="h-1.5 overflow-hidden rounded-[3px] bg-[var(--color-hover)]">
                  <i className="so-width block h-full" style={{ width: grown ? `${pctOf(w.hits, w.runs)}%` : '0%', background: 'var(--color-zone-cognitive)' }} />
                </div>
                <div className={`text-right text-small tabular-nums ${MUTED}`} data-testid={`cm-leg-value-${leg}`}>
                  <b className="text-body font-semibold text-[var(--color-text)]">{w.hits}</b> / {w.runs}
                </div>
              </div>
            );
          })}
        </div>
      </section>
      <section>
        <ZoneLabel>{t('ui.CMBrainOverlay.storesLabel')}</ZoneLabel>
        <div className="-mx-2.5 flex flex-col divide-y divide-[var(--color-border)]">
          {stores.map((st) => {
            const cells = (
              <>
                <span className="min-w-0 truncate">{st.name}</span>
                <span className={`text-right text-small tabular-nums ${MUTED}`}>{st.value}</span>
                <span className="text-right text-small text-[var(--color-primary)]">{st.go ?? ''}</span>
              </>
            );
            const cls = 'grid w-full grid-cols-[minmax(0,1fr)_140px_90px] items-center gap-x-3.5 rounded-lg px-2.5 py-2 text-left text-body';
            return st.onClick ? (
              <button key={st.key} type="button" data-testid={`cm-store-${st.key}`} onClick={st.onClick} className={`${cls} hover:bg-[var(--color-hover)]`}>{cells}</button>
            ) : (
              <div key={st.key} data-testid={`cm-store-${st.key}`} className={cls}>{cells}</div>
            );
          })}
        </div>
      </section>
    </div>
  );
}

// ── 工作原理 ──────────────────────────────────────────────────────────────────────────
/** The system prompt's file order, from per_file: same priority joined by " · ". */
function fileOrder(rows: TokenFileRow[]): string {
  const sorted = [...rows].sort((a, b) => a.priority - b.priority);
  const steps: string[][] = [];
  sorted.forEach((f, i) => {
    if (i && sorted[i - 1].priority === f.priority) steps[steps.length - 1].push(baseName(f.name));
    else steps.push([baseName(f.name)]);
  });
  return steps.map((s) => s.join(' · ')).join(' → ');
}

function Layer({ testid, title, when, right, tint, dashed, children }: {
  testid: string; title: string; when: string; right?: ReactNode; tint: string; dashed?: boolean; children?: ReactNode;
}) {
  return (
    <div data-testid={testid} className={`${CARD} rounded-[10px] px-3.5 py-2.5`}
      style={{ borderStyle: dashed ? 'dashed' : 'solid', borderLeftWidth: 3, borderLeftColor: tint }}>
      <div className="flex flex-wrap items-baseline gap-x-2.5">
        <span className="text-small font-semibold tracking-[0.04em]">{title}</span>
        <span className={`text-meta ${DIM}`}>{when}</span>
        {right && <span className={`ml-auto text-small tabular-nums ${MUTED}`}>{right}</span>}
      </div>
      {children}
    </div>
  );
}

function Parts({ items }: { items: string[] }) {
  return (
    <div className="mt-1.5 flex flex-wrap gap-x-2.5 gap-y-1 text-small text-[var(--color-text-secondary)]">
      {items.map((p) => <span key={p}>{p}</span>)}
    </div>
  );
}

function SourceBox({ title, body }: { title: string; body: string }) {
  return (
    <div className="rounded-[9px] border border-[var(--color-border)] bg-[var(--color-card)] px-3 py-2 text-small text-[var(--color-text-secondary)]">
      <b className="block font-semibold text-[var(--color-text)]">{title}</b>{body}
    </div>
  );
}

function HowTab() {
  const { t } = useTranslation();
  const lite = useQuery({ ...overlayQuery.contextLite<ContextHealthLite>() });
  const recall = useRecallKnowledge();
  // the Cognition flow tab's own 7-day query (CdfGuideline reads the same key)
  const landed = useQuery({ queryKey: ['ingestion', 'landed', '7d'], queryFn: () => ingestionService.landed('7d'), staleTime: 60_000 });
  const block = lite.data?.token_block ?? null;
  const rows = block?.per_file ?? [];
  const groups = groupsOf(rows, OWNER_ORDER_BAR);
  const system = rows.filter((f) => f.owner === 'system');
  const others = groupsOf(rows.filter((f) => f.owner !== 'system'), OWNER_ORDER_LIST);
  const partial = rows.filter(partlyInjected).map((f) => baseName(f.name));
  const brought = recall.data ? LEGS.reduce((n, l) => n + (recall.data.contexts[l]?.hits ?? 0), 0) : null;
  const totals = placeTotals(landed.data);
  const total = block?.total_tokens || 1;
  return (
    <div data-testid="cm-panel-how" className="flex flex-col gap-4">
      <ZoneLabel>{t('ui.CMBrainOverlay.howLabel')}</ZoneLabel>
      <div className="grid grid-cols-1 gap-y-3 md:grid-cols-[190px_minmax(0,1fr)_130px]">
        <div className="flex flex-col gap-2.5 md:pr-5" data-testid="cm-how-sources">
          {system.length > 0 && <SourceBox title={t('ui.CMBrainOverlay.srcSystem', { count: system.length })} body={system.map((f) => baseName(f.name)).join(' · ')} />}
          {others.length > 0 && (
            <SourceBox title={others.map((g) => t(`ui.CMBrainOverlay.owner_${g.owner}`, { defaultValue: g.owner })).join(' · ')}
              body={others.flatMap((g) => g.files).sort((a, b) => a.priority - b.priority).map((f) => baseName(f.name)).join(' · ')} />
          )}
          <SourceBox title={t('ui.CMBrainOverlay.srcDaily')} body={t('ui.CMBrainOverlay.srcDailyW')} />
          <SourceBox title={t('ui.CMBrainOverlay.srcRecall')} body={t('ui.CMBrainOverlay.srcRecallW')} />
          <SourceBox title={t('ui.CMBrainOverlay.srcRepo')} body={t('ui.CMBrainOverlay.srcRepoW')} />
        </div>
        <div className="flex flex-col gap-2.5" data-testid="cm-how-layers">
          <Layer testid="cm-layer-1" title={t('ui.CMBrainOverlay.l1')} when={t('ui.CMBrainOverlay.l1When')} tint="var(--color-text-dim)"
            right={block ? <><b className="font-semibold text-[var(--color-text)]">{kRound(block.total_tokens)}</b> / {kRound(block.budget)}</> : undefined}>
            {block && (
              <>
                <div className="mt-2 flex h-2 gap-0.5 overflow-hidden rounded bg-[var(--color-hover)]">
                  {groups.map((g) => <i key={g.owner} className="block h-full" style={{ width: `${(g.total / total) * 100}%`, background: ownerTint(g.owner) }} />)}
                </div>
                <div className="mt-1.5 flex flex-wrap gap-x-2.5 gap-y-1 text-small">
                  <span className="text-[var(--color-text-secondary)]" data-testid="cm-how-order">{t('ui.CMBrainOverlay.l1Order', { files: fileOrder(rows) })}</span>
                  <span className={DIM}>{partial.length ? t('ui.CMBrainOverlay.l1Partial', { files: partial.join(' · ') }) : t('ui.CMBrainOverlay.l1Whole')}</span>
                </div>
              </>
            )}
          </Layer>
          <Layer testid="cm-layer-2" title={t('ui.CMBrainOverlay.l2')} when={t('ui.CMBrainOverlay.l2When')} tint="var(--color-zone-work)">
            <Parts items={[t('ui.CMBrainOverlay.l2Daily'), t('ui.CMBrainOverlay.l2NorthStar'), t('ui.CMBrainOverlay.l2Sessions'), t('ui.CMBrainOverlay.l2Mcp'), t('ui.CMBrainOverlay.l2Time')]} />
          </Layer>
          <Layer testid="cm-layer-3" title={t('ui.CMBrainOverlay.l3')} when={t('ui.CMBrainOverlay.l3When')} tint="var(--color-zone-cognitive)"
            right={brought != null ? <span data-testid="cm-how-recalled"><Trans i18nKey="ui.CMBrainOverlay.l3Hits" values={{ count: brought }}
              components={{ b: <b className="font-semibold text-[var(--color-text)]" /> }} /></span> : undefined}>
            <Parts items={[t('ui.CMBrainOverlay.l3Ddd'), t('ui.CMBrainOverlay.l3Agent'), t('ui.CMBrainOverlay.l3Recalled')]} />
          </Layer>
          <Layer testid="cm-layer-4" title={t('ui.CMBrainOverlay.l4')} when={t('ui.CMBrainOverlay.l4When')} tint="var(--color-primary)" />
          <Layer testid="cm-layer-5" title={t('ui.CMBrainOverlay.l5')} when={t('ui.CMBrainOverlay.l5When')} tint="var(--color-text-dim)" dashed>
            <Parts items={[t('ui.CMBrainOverlay.l5Repo'), t('ui.CMBrainOverlay.l5Checkout'), t('ui.CMBrainOverlay.l5Language')]} />
          </Layer>
        </div>
        <div className="relative flex items-center md:pl-5">
          <span aria-hidden className="so-flow absolute left-0 top-1/2 hidden h-0.5 w-5 md:block"
            style={{ background: 'color-mix(in srgb, var(--color-zone-cognitive) 35%, transparent)', ['--so-c' as string]: 'var(--color-zone-cognitive)' }} />
          <div className="w-full rounded-xl border px-2.5 py-4 text-center text-body font-semibold"
            style={{ background: 'color-mix(in srgb, var(--color-zone-cognitive) 10%, var(--color-card))', borderColor: 'color-mix(in srgb, var(--color-zone-cognitive) 40%, var(--color-border))' }}>
            {t('ui.CMBrainOverlay.model')}
          </div>
        </div>
      </div>
      <div data-testid="cm-how-loop" className="mt-3 flex items-center gap-2.5 rounded-[10px] border border-dashed px-3.5 py-2.5 text-small text-[var(--color-text-secondary)]"
        style={{ borderColor: 'color-mix(in srgb, var(--color-zone-cognitive) 45%, var(--color-border))' }}>
        <span aria-hidden className="material-symbols-outlined icon-sm" style={{ color: 'var(--color-zone-cognitive)' }}>autorenew</span>
        <span>
          {landed.data && totals ? (
            <Trans i18nKey="ui.CMBrainOverlay.loopNumbers"
              values={{ written: landed.data.steps.written.toLocaleString(), files: landed.data.landed.length, everyTurn: totals[0].toLocaleString() }}
              components={{ b: <b className="font-semibold text-[var(--color-text)]" /> }} />
          ) : t('ui.CMBrainOverlay.loopPlain')}
        </span>
      </div>
    </div>
  );
}
