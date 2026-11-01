/**
 * BrainMapView — the bodies of a Brain's detail tabs that read the Brain map data.
 * The tabs mirror the parts of the map (Overview, Needs you, Brain (DDD), Project
 * Agent, Conductor · Runs, Reflect · Evolution, Eval); clicking a map part opens its
 * tab.
 *   BrainMapView      Overview: the Brain map, and beside it Needs you + Eval summary
 *   BrainNeedsView    Needs you: Runs and what Need You holds for the Brain
 *   BrainAgentView    Project Agent: what a Hive run reads, ask for a change, and Start a Run
 *   BrainRunsView     Conductor · Runs: the Chat tab and Hive lanes, each Run one card
 *   BrainEvalView     Eval: the five dimensions, UNKNOWN where nobody measures
 *   BrainAddKnowledge Brain (DDD): add files / folders / links / repos, distilled in chat
 * The tab bar, header and the Brain (DDD) / Reflect bodies live in BrainHub.
 *
 * Reads only records that already exist: the brain detail + what it learned this week
 * (cached ddd hooks), the 30-day pipeline analytics (one cached query, the same payload the
 * Pipelines overlay reads) and, on the tabs that show them, the Hive runs Swarm Out
 * reads (GET /api/swarm-out/runs, polled while shown). Nothing here is a sample: what
 * nobody records is UNKNOWN or "—" (design docs/design/2026-10-30-brain-runs-one-card.md).
 * The Project Agent definition is real: project-agent.yaml, a skeleton at creation and
 * drafted from the DDD, kept current by the agent. ProjectAgentView shows it read-only;
 * a change goes to the agent in chat (nothing is confirmed).
 * Submitting a request is real: it goes to chat as a pipeline Run request.
 *
 * Exports: BrainMapView, BrainNeedsView, BrainAgentView, BrainRunsView, BrainEvalView,
 *          BrainAddKnowledge, BrainTab, MapPanel
 */
import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { useBrainDetail, useBrainKnowledge, type BrainDetail, type ProjectAgent, type SectionKey } from '../../services/ddd';
import { pipelinesService, type PipelineAnalytics, type PipelineRunSummary } from '../../services/pipelines';
import type { SwarmOutRun } from '../../services/swarmOut';
import { RunCard } from '../swarm-out/LiveDispatches';
import { runTone as hiveTone } from '../swarm-out/tone';
import { useRuns } from '../swarm-out/live';
import { LocalRunCard } from './LocalRunCard';
import { Stat, SectionLabel, StatusBadge, Hint } from '../overlay';
import { BrainMap, type BrainMapTarget, type BrainMapProps, type MapTone } from './BrainMap';
import { MaterialPicker, type MaterialRow } from './StarterMaterial';
import { ProjectAgentView } from './ProjectAgentView';
import { buildAddKnowledgePrompt, buildResumeSetupPrompt } from '../../utils/newBrainDispatch';
import { openOverlayAt } from '../../utils/overlayTarget';
import { copyToClipboard } from '../../utils/clipboard';
import {
  sectionCounts, projectRuns, runTone, needsYouItems, type RunTone, needsCount, brainEval, brainHiveRuns, hiveLive, humanPerRun,
} from './brainMapModel';

/** The detail tabs of a Brain: one per part of the Brain map, plus Overview. */
export type BrainTab = 'overview' | 'needs' | 'brain' | 'agent' | 'runs' | 'reflect' | 'eval';
/** Where the Hub home may land inside a Brain (kept for its callers). */
export type MapPanel = 'need' | 'agent' | 'add';

type Dispatch = (msg: string, opts?: { autoSend?: boolean }) => boolean;

/** Pipelines, opened on one Run's drawer (its question, its report). */
function openRun(id: string) { openOverlayAt('pipeline', id); }
function openNeedYou() { window.dispatchEvent(new CustomEvent('swarm:show-needs-you')); }

/** Send a prompt to chat now; on success close the Hub so its tab shows. */
function useSend(onDispatch?: Dispatch, onRequestClose?: () => void) {
  return (msg: string): boolean => {
    let ok = false;
    try { ok = onDispatch ? onDispatch(msg, { autoSend: true }) : false; } catch { ok = false; }
    if (ok) onRequestClose?.();
    return ok;
  };
}

/** The one data read every Brain-map tab shares (all cached queries). */
function useBrainData(name: string) {
  const { data: detail = null } = useBrainDetail(name);
  const { data: knowledge = null } = useBrainKnowledge(name, 'week');
  const { data: analytics = null } = useQuery<PipelineAnalytics>({
    queryKey: ['pipeline-analytics', '30d'], queryFn: () => pipelinesService.fetchAnalytics('30d'), staleTime: 30_000,
  });
  const runs = useMemo(() => projectRuns(analytics, name), [analytics, name]);
  const needs = useMemo(() => needsYouItems(runs, detail?.pendingBySource), [runs, detail]);
  const ev = useMemo(() => brainEval(runs, detail, knowledge), [runs, detail, knowledge]);
  return { detail, runs, needs, ev };
}

/** This Brain's Hive runs (Swarm Out), polled while the tab is shown. */
function useBrainHiveRuns(name: string) {
  const { data, failed, reload } = useRuns(false);
  const hive = useMemo(() => brainHiveRuns(data?.runs, name), [data, name]);
  return { hive, loaded: !!data, failed: failed && !data, reload };
}

/** Status dots on the map, from the same records as the numbers. */
/** A Hive run's dot on the map, in the local Runs' tones. */
function hiveDot(r: SwarmOutRun): RunTone {
  const t = hiveTone(r);
  return t === 'busy' ? 'running' : t === 'ok' ? 'landed' : t === 'error' ? 'failed'
    : t === 'attention' && r.state === 'waiting' ? 'needs' : 'stopped';
}

function mapTones(detail: BrainDetail | null, runs: PipelineRunSummary[], hive: SwarmOutRun[], ev: ReturnType<typeof brainEval>): BrainMapProps['tones'] {
  const has = (k: SectionKey) => (detail?.sections.find((s) => s.key === k)?.members.length ?? 0) > 0;
  const base = (k: SectionKey): MapTone => (has(k) ? 'ok' : 'attention');
  const pa = detail?.projectAgent;
  return {
    identity: base('identity'),
    knowledge: base('knowledge'),
    gates: base('gates'),
    capabilities: base('capabilities'),
    delivery: base('delivery'),
    refresher: base('refresher'),
    agent: pa === undefined ? undefined : pa === null ? 'attention' : pa.problems.length ? 'needs' : 'ok',
    conductor: runs.some((r) => r.needsYou) || hive.some((r) => r.state === 'waiting') ? 'needs'
      : runs.some((r) => r.status === 'running') || hive.some(hiveLive) ? 'busy' : runs.length || hive.length ? 'ok' : undefined,
    reflect: ev.brain.learnedWeek ? 'ok' : undefined,
  };
}

function agentLine(pa: ProjectAgent | null | undefined, t: TFunction): string | undefined {
  if (pa === undefined) return undefined;
  if (pa === null) return t('ui.BrainMapView.agentNotGenerated');
  if (pa.problems.length) return t('ui.BrainMapView.agentProblems');
  return t('ui.BrainMapView.agentReady', { mode: pa.defaultMode ?? '--' });
}

/** Overview tab: the Brain map, Needs you and an Eval summary. Map parts open tabs. */
export function BrainMapView({ name, onGoTo, onDispatch, onRequestClose }: {
  name: string;
  onGoTo: (tab: BrainTab) => void;
  onDispatch?: Dispatch;
  /** Close the Brain Hub so the chat tab that received a dispatch is visible. */
  onRequestClose?: () => void;
}) {
  const { t } = useTranslation();
  const { detail, runs, needs, ev } = useBrainData(name);
  const { hive } = useBrainHiveRuns(name);
  const needN = needsCount(needs);
  // both lanes' Runs, newest first (the map draws the first twelve)
  const dots = useMemo(() => [
    ...runs.map((r) => ({ at: Date.parse(r.updatedAt || r.createdAt), id: r.id, title: r.requirement || r.id, tone: runTone(r) })),
    ...hive.map((r) => ({ at: Date.parse(r.sent_at), id: r.dispatch_id, title: `${r.title} (Hive ${r.hive})`, tone: hiveDot(r) })),
  ].sort((x, y) => (y.at || 0) - (x.at || 0)).map(({ id, title, tone }) => ({ id, title, tone })), [runs, hive]);
  const send = useSend(onDispatch, onRequestClose);

  const open = (target: BrainMapTarget) => {
    onGoTo(target === 'need' ? 'needs' : target === 'browse' ? 'brain' : target);
  };

  return (
    <div className="flex flex-col gap-3 p-4 min-h-full" data-testid="brain-map-view">
      {detail?.setupState === 'setting-up' && (
        <div className="flex items-center gap-3 rounded-lg border px-3 py-2 border-[color-mix(in_srgb,var(--color-status-warning)_45%,transparent)] bg-[color-mix(in_srgb,var(--color-status-warning)_8%,transparent)]"
          data-testid="bm-setup-banner">
          <span className="material-symbols-outlined icon-sm text-[var(--color-status-warning)]">construction</span>
          <div className="flex-1 min-w-0">
            <div className="text-small font-semibold">{t('ui.BrainMapView.setupTitle')}</div>
            <div className="text-meta text-[var(--color-text-muted)]">{t('ui.BrainMapView.setupBody')}</div>
          </div>
          <button className="btn-sm btn-primary shrink-0" data-testid="bm-setup-resume"
            onClick={() => send(buildResumeSetupPrompt(name))}>{t('ui.BrainMapView.setupResume')}</button>
        </div>
      )}
      {/* the map on the left; what needs you and how it is doing on the right */}
      <div className="grid gap-3 items-start" style={{ gridTemplateColumns: 'minmax(0, 1fr) 300px' }}>
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] p-3 min-w-0">
        <div className="flex items-center justify-between text-small">
          <span className="font-semibold">{t('ui.BrainMapView.mapTitle')}</span>
          <span className="text-meta text-[var(--color-text-muted)]">{t('ui.BrainMapView.mapHint')}</span>
        </div>
        <BrainMap
          version="DDD"
          counts={sectionCounts(detail)}
          runs={dots}
          lanes={{ chat: runs.filter((r) => r.status === 'running' || r.needsYou).length, hive: hive.filter(hiveLive).length }}
          sources={{ chat: runs.length, hive: hive.length }}
          needs={needN}
          reflect={{ week: ev.brain.learnedWeek }}
          agentLine={agentLine(detail?.projectAgent, t)}
          evalLine={t('ui.BrainMapView.evalLine', { landed: ev.runs.landed, total: ev.runs.total })}
          tones={mapTones(detail, runs, hive, ev)}
          onOpen={open}
        />
        <div className="flex flex-wrap items-center justify-center gap-4 pt-1 text-meta text-[var(--color-text-muted)]">
          <StatusBadge tone="success">{t('ui.BrainMapView.legendHealthy')}</StatusBadge>
          <StatusBadge tone="warning">{t('ui.BrainMapView.legendAttention')}</StatusBadge>
          <StatusBadge tone="error">{t('ui.BrainMapView.legendNeedsYou')}</StatusBadge>
          <StatusBadge tone="primary">{t('ui.BrainMapView.legendRunning')}</StatusBadge>
          <span className="text-[var(--color-border)]">|</span>
          <LineKey v="--color-primary">{t('ui.BrainMapView.lineRequest')}</LineKey>
          <LineKey v="--color-text-muted">{t('ui.BrainMapView.lineRun')}</LineKey>
          <LineKey v="--color-status-success">{t('ui.BrainMapView.lineKnowledge')}</LineKey>
          <LineKey v="--color-status-warning" dashed>{t('ui.BrainMapView.lineWaits')}</LineKey>
        </div>
      </div>
      <div className="flex flex-col gap-3 min-w-0">
        <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)]" data-testid="bm-panel-need">
          <div className="flex items-center justify-between px-3 py-2">
            <span className="text-small font-semibold">{t('ui.BrainMapView.needsYou')}</span>
            <span className={`text-small font-semibold ${needN ? 'text-[var(--color-status-error)]' : 'text-[var(--color-text-muted)]'}`}
              data-testid="bm-need-count">{needN}</span>
          </div>
          <NeedList items={needs} onGoTo={onGoTo} />
        </div>
        <EvalSummary ev={ev} onOpen={() => onGoTo('eval')} />
      </div>
      </div>
    </div>
  );
}

/** A legend swatch: the same stroke the map draws for that kind of line. */
function LineKey({ v, dashed, children }: { v: string; dashed?: boolean; children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <svg width="22" height="6" aria-hidden="true"><line x1="1" y1="3" x2="21" y2="3" strokeWidth="2" strokeLinecap="round"
        strokeDasharray={dashed ? '5 4' : undefined} style={{ stroke: `var(${v})` }} /></svg>
      {children}
    </span>
  );
}

function NeedList({ items }: { items: ReturnType<typeof needsYouItems>; onGoTo?: (t: BrainTab) => void }) {
  const { t } = useTranslation();
  if (items.length === 0) {
    return <div className="px-3 pb-3 text-small text-[var(--color-status-success)]" data-testid="bm-need-empty">{t('ui.BrainMapView.needEmpty')}</div>;
  }
  return (
    <div>
      {items.map((it) => (
        <div key={it.kind + it.id} className="flex items-start gap-2 px-3 py-2 border-t border-[var(--color-border)]" data-testid={`bm-need-${it.id}`}>
          <div className="flex-1 min-w-0">
            <div className="text-small line-clamp-2">{it.title}</div>
            <div className="text-meta text-[var(--color-text-muted)] truncate">{it.detail}</div>
          </div>
          {it.kind === 'run' && <button className="btn-sm btn-secondary shrink-0" onClick={() => openRun(it.id)}>{t('ui.BrainMapView.answer')}</button>}
          {it.kind === 'other' && <button className="btn-sm btn-secondary shrink-0" onClick={openNeedYou}>{t('ui.BrainMapView.open')}</button>}
        </div>
      ))}
    </div>
  );
}

const pct = (x: number | null) => (x == null ? 'UNKNOWN' : `${Math.round(x * 100)}%`);
const num = (x: number | null, unit = '') => (x == null ? 'UNKNOWN' : `${x}${unit}`);

function EvalSummary({ ev, onOpen }: { ev: ReturnType<typeof brainEval>; onOpen: () => void }) {
  const { t } = useTranslation();
  const rows: [string, string, string?][] = [
    [t('ui.BrainMapView.sumRunsLanded'), `${ev.runs.landed} / ${ev.runs.total}`],
    [t('ui.BrainMapView.averageCycle'), num(ev.runs.avgCycleMin, t('ui.BrainMapView.minUnit'))],
    [t('ui.BrainMapView.sumKnowledge'), ev.brain.recentActivity == null ? 'UNKNOWN' : `+${ev.brain.recentActivity}`],
    [t('ui.BrainMapView.tokens'), ev.runs.tokens == null ? 'UNKNOWN' : `${Math.round(ev.runs.tokens / 1000)}k`],
  ];
  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)]" data-testid="bm-eval-summary">
      <div className="flex items-center justify-between px-3 py-2">
        <span className="text-small font-semibold">{t('ui.BrainMapView.evalTitle')}</span>
        <button className="text-meta text-[var(--color-primary)]" onClick={onOpen} data-testid="bm-eval-open">{t('ui.BrainMapView.evalAll')}</button>
      </div>
      {rows.map(([k, v, cls]) => (
        <div key={k} className="flex items-center justify-between px-3 py-1.5 border-t border-[var(--color-border)] text-small">
          <span className="text-[var(--color-text-muted)]">{k}</span>
          <span className={`font-semibold ${v === 'UNKNOWN' ? 'text-[var(--color-text-muted)]' : cls ?? ''}`}>{v}</span>
        </div>
      ))}
    </div>
  );
}

/** Delivery tab: the Project Agent and Start a Run on the left, the Runs on the right. */
/** Needs you tab: everything waiting on the person, full width. */
export function BrainNeedsView({ name, onGoTo }: { name: string; onGoTo: (t: BrainTab) => void }) {
  const { t } = useTranslation();
  const { needs } = useBrainData(name);
  return (
    <div className="p-4" data-testid="bm-needs-view">
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)]">
        <div className="px-3 py-2"><Hint>{t('ui.BrainMapView.needsHint')}</Hint></div>
        <NeedList items={needs} onGoTo={onGoTo} />
      </div>
    </div>
  );
}

/** Project Agent tab: the definition (read-only; a change goes to the agent) and Start a Run. */
export function BrainAgentView({ name, onDispatch, onRequestClose }: {
  name: string; onDispatch?: Dispatch; onRequestClose?: () => void;
}) {
  const { detail, runs } = useBrainData(name);
  const send = useSend(onDispatch, onRequestClose);
  return (
    <div className="p-4 max-w-[1180px]" data-testid="bm-panel-agent">
      <AgentPanel name={name} runs={runs}
        delivery={detail?.sections.find((s) => s.key === 'delivery')?.members.map((m) => m.path) ?? []}
        agent={detail ? detail.projectAgent : undefined} onDispatch={send} />
    </div>
  );
}

/** Conductor · Runs tab: the Chat tab lane and the Hive lane, each Run one card. */
export function BrainRunsView({ name, onRequestClose }: {
  name: string;
  /** the host overlay's close: a file opened from a run's big card opens in the Canvas */
  onRequestClose?: () => void;
}) {
  const { runs } = useBrainData(name);
  const hive = useBrainHiveRuns(name);
  return (
    <div className="p-4 max-w-[1240px]" data-testid="bm-panel-runs">
      <RunsPanel name={name} runs={runs} hive={hive} onRequestClose={onRequestClose} />
    </div>
  );
}

/** Eval tab: every dimension, full width. */
export function BrainEvalView({ name }: { name: string }) {
  const { ev, runs } = useBrainData(name);
  const { hive, loaded } = useBrainHiveRuns(name);
  const human = useMemo(() => humanPerRun(runs, hive), [runs, hive]);
  return (
    <div className="p-4" data-testid="bm-panel-eval">
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] p-3">
        <EvalPanel ev={ev} human={human} hiveLoaded={loaded} />
      </div>
    </div>
  );
}

/** Brain (DDD) tab: add material, distilled into this Brain through chat. */
export function BrainAddKnowledge({ name, onDispatch, onRequestClose }: {
  name: string; onDispatch?: Dispatch; onRequestClose?: () => void;
}) {
  const send = useSend(onDispatch, onRequestClose);
  return <AddKnowledgePanel name={name} onSend={send} />;
}

// ── panels ─────────────────────────────────────────────────────────────────────

/** Add material to this Brain: the New Brain picker, then one prompt to chat that
 *  routes each role to the skill that owns the write (buildAddKnowledgePrompt). */
function AddKnowledgePanel({ name, onSend }: { name: string; onSend: (msg: string) => boolean }) {
  const { t } = useTranslation();
  const [items, setItems] = useState<MaterialRow[]>([]);
  const [draft, setDraft] = useState('');
  const [failed, setFailed] = useState(false);
  const counts = (r: string) => items.filter((it) => it.role === r).length;
  const send = () => {
    // a typed-but-not-added line is committed by the input's blur before this click
    if (!items.length) return;
    const ok = onSend(buildAddKnowledgePrompt(name, items.map(({ value, kind, role }) => ({ value, kind, role }))));
    setFailed(!ok);
    if (ok) { setItems([]); setDraft(''); }
  };
  return (
    <div className="flex flex-col gap-3 min-h-[360px]" data-testid="bm-add">
      <Hint>{t('ui.BrainMapView.addHint')}</Hint>
      <MaterialPicker items={items} setItems={setItems} draft={draft} setDraft={setDraft} testPrefix="bm-add"
        emptyText={t('ui.BrainMapView.addEmpty')} />
      <div className="flex items-center gap-3">
        <span className="text-meta text-[var(--color-text-muted)] flex-1">
          {items.length ? t('ui.BrainMapView.addCounts', { distill: counts('DISTILL'), shelf: counts('SHELF'), count: counts('GOVERN') }) : t('ui.BrainMapView.addNothing')}
        </span>
        <button className="btn-md btn-primary" disabled={!items.length} onClick={send} data-testid="bm-add-send">
          {t('ui.BrainMapView.addSend')}
        </button>
      </div>
      {failed && <div className="text-meta text-[var(--color-status-error)]" data-testid="bm-add-failed">{t('ui.BrainMapView.addFailed')}</div>}
    </div>
  );
}



/** No definition yet: offer to draft it from the DDD in chat. */
function AgentNone({ onGenerate }: { onGenerate: () => void }) {
  const { t } = useTranslation();
  return (
    <div data-testid="bm-agent-none">
      <SectionLabel>{t('ui.BrainMapView.projectAgent')}</SectionLabel>
      <div className="text-small mt-1">{t('ui.BrainMapView.agentNone')}</div>
      <div className="text-meta text-[var(--color-text-muted)]">{t('ui.BrainMapView.agentNoneHow')}</div>
      <button className="btn-sm btn-primary mt-2" onClick={onGenerate} data-testid="bm-agent-generate">{t('ui.BrainMapView.agentGenerate')}</button>
    </div>
  );
}

function AgentPanel({ name, runs, delivery, agent, onDispatch }: {
  name: string; runs: PipelineRunSummary[]; delivery: string[];
  agent?: ProjectAgent | null; onDispatch?: (msg: string, opts?: { autoSend?: boolean }) => boolean;
}) {
  const { t } = useTranslation();
  const [req, setReq] = useState('');
  const [title, setTitle] = useState('');
  const [sent, setSent] = useState<string | null>(null);
  const profiles = runs.reduce<Record<string, number>>((m, r) => { if (r.profile) m[r.profile] = (m[r.profile] ?? 0) + 1; return m; }, {});
  const usual = Object.entries(profiles).sort((a, b) => b[1] - a[1])[0]?.[0];

  const send = async (msg: string, label: string) => {
    if (onDispatch?.(msg)) { setSent(t('ui.BrainMapView.sentChat', { label })); return; }
    const copied = await copyToClipboard(msg);
    setSent(copied
      ? t('ui.BrainMapView.sentCopied', { label })
      : t('ui.BrainMapView.sentFailed', { label }));
  };
  const submit = () => {
    const text = req.trim();
    if (!text) return;
    const runTitle = title.trim() || (text.length > 60 ? text.slice(0, 57).replace(/\s+\S*$/, '') + '…' : text);
    send(`Use the pipeline on ${name} for this change; choose its profile from the change itself.\nTitle: ${runTitle}\nRequirement: ${text}`, runTitle);
    setReq(''); setTitle('');
  };

  return (
    <div className="flex flex-col gap-3">
      {agent === undefined ? (
        <div>
          <SectionLabel>{t('ui.BrainMapView.deliveryDef')}</SectionLabel>
          <div className="text-small mt-1">
            {t('ui.BrainMapView.usualProfile')} <b>{usual ?? '--'}</b>
            <span className="text-[var(--color-text-muted)]"> {t('ui.BrainMapView.fromRuns', { count: runs.length })}</span>
          </div>
          <div className="text-meta text-[var(--color-text-muted)] mt-1 truncate">
            {t('ui.BrainMapView.deliveryFiles')} {delivery.length ? delivery.join(', ') : t('ui.BrainMapView.none')}
          </div>
        </div>
      ) : (
        agent === null
          ? <AgentNone onGenerate={() => send(`Draft the Project Agent of the ${name} Brain from its DDD (s_project-manager P3b, artifact_cli project-agent-draft) and tell me what it holds.`, t('ui.BrainMapView.labelDraft'))} />
          : <ProjectAgentView key={name} brain={name} agent={agent}
              onRevise={(note) => send(`Change the Project Agent of the ${name} Brain (project-agent.yaml): ${note}\nGive each change its source, check it with project_agent.validate, and tell me what you changed.`, t('ui.BrainMapView.labelChange'))} />
      )}
      <section className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] p-4" data-testid="bm-agent-send">
        <SectionLabel>{t('ui.BrainMapView.sendWork')}</SectionLabel>
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder={t('ui.BrainMapView.titlePlaceholder')} maxLength={60}
          className="mt-2 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-input-bg)] px-2 py-1 text-small" data-testid="bm-agent-title" />
        <textarea value={req} onChange={(e) => setReq(e.target.value)} rows={3} placeholder={t('ui.BrainMapView.reqPlaceholder', { name })}
          className="mt-1 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-input-bg)] px-2 py-1 text-small" data-testid="bm-agent-req" />
        <div className="flex flex-wrap items-center gap-3 mt-1 text-meta text-[var(--color-text-muted)]">
          {t('ui.BrainMapView.runsIn')}
          <button className="btn-sm btn-primary ml-auto" disabled={!req.trim()} onClick={submit} data-testid="bm-agent-submit">{t('ui.BrainMapView.submit')}</button>
        </div>
        {sent && <div className="text-meta text-[var(--color-status-success)] mt-1" data-testid="bm-agent-sent">{sent}</div>}
      </section>
    </div>
  );
}

const RUN_GRID = { gridTemplateColumns: 'repeat(auto-fill, minmax(min(100%, 300px), 1fr))' };

function RunsPanel({ name, runs, hive, onRequestClose }: {
  name: string; runs: PipelineRunSummary[]; onRequestClose?: () => void;
  hive: { hive: SwarmOutRun[]; loaded: boolean; failed: boolean; reload: () => void };
}) {
  const { t } = useTranslation();
  const live = hive.hive.filter(hiveLive).length;
  const going = runs.filter((r) => r.status === 'running' || r.needsYou).length;
  return (
    <div className="flex flex-col gap-4">
      <Hint>{t('ui.BrainMapView.runsHint')}</Hint>
      <Lane title={t('ui.BrainMapView.laneChat')} meta={t('ui.BrainMapView.runCount', { count: runs.length }) + (going ? ` · ${t('ui.BrainMapView.going', { count: going })}` : '')} testId="bm-lane-chat">
        {runs.length === 0 ? <div className="text-meta text-[var(--color-text-muted)]">{t('ui.BrainMapView.noRuns')}</div> : (
          <div className="grid gap-3" style={RUN_GRID}>
            {runs.map((r) => <LocalRunCard key={r.id} r={r} brain={name} />)}
          </div>
        )}
      </Lane>
      <Lane title="Hive" meta={hive.loaded ? t('ui.BrainMapView.runCount', { count: hive.hive.length }) + (live ? ` · ${t('ui.BrainMapView.going', { count: live })}` : '') : ''} testId="bm-lane-hive">
        {hive.failed ? <div className="text-meta text-[var(--color-text-muted)]" data-testid="bm-hive-unreadable">{t('ui.BrainMapView.hiveUnreadable')}</div>
          : !hive.loaded ? <div className="text-meta text-[var(--color-text-muted)]">{t('ui.BrainMapView.hiveReading')}</div>
          : hive.hive.length === 0 ? <div className="text-meta text-[var(--color-text-muted)]" data-testid="bm-hive-empty">{t('ui.BrainMapView.hiveEmpty')}</div>
          : (
            <div className="grid gap-3" style={RUN_GRID}>
              {hive.hive.map((r) => <RunCard key={r.dispatch_id} r={r} onReload={hive.reload} onLeave={onRequestClose} />)}
            </div>
          )}
      </Lane>
    </div>
  );
}

function Lane({ title, meta, testId, children }: { title: string; meta: string; testId: string; children: React.ReactNode }) {
  return (
    <section data-testid={testId}>
      <div className="flex items-baseline gap-2 mb-2">
        <span className="text-small font-semibold">{title}</span>
        <span className="text-meta text-[var(--color-text-muted)] tabular-nums">{meta}</span>
      </div>
      {children}
    </section>
  );
}

function EvalPanel({ ev, human, hiveLoaded }: {
  ev: ReturnType<typeof brainEval>; human: { avg: number | null; runs: number }; hiveLoaded: boolean;
}) {
  const { t } = useTranslation();
  const grid = 'grid grid-cols-2 gap-2 mt-1';
  return (
    <div className="flex flex-col gap-3">
      <Hint>{t('ui.BrainMapView.evalHint')}</Hint>
      <div><SectionLabel>{t('ui.BrainMapView.runs30')}</SectionLabel><div className={grid}>
        <Stat label={t('ui.BrainMapView.landed')} value={`${ev.runs.landed} / ${ev.runs.total}`} hint={t('ui.BrainMapView.completion', { pct: pct(ev.runs.completionRate) })} />
        <Stat label={t('ui.BrainMapView.needNow')} value={ev.runs.needsYou} tone={ev.runs.needsYou ? 'error' : undefined} />
        <Stat label={t('ui.BrainMapView.averageCycle')} value={num(ev.runs.avgCycleMin, t('ui.BrainMapView.minUnit'))} />
        <Stat label={t('ui.BrainMapView.tokens')} value={ev.runs.tokens == null ? 'UNKNOWN' : `${Math.round(ev.runs.tokens / 1000)}k`} hint={t('ui.BrainMapView.tokensHint')} />
      </div></div>
      <div><SectionLabel>{t('ui.BrainMapView.projectAgent')}</SectionLabel><div className={grid}>
        <Stat label={t('ui.BrainMapView.humanPerRun')} value={human.avg == null ? 'UNKNOWN' : String(Math.round(human.avg * 10) / 10)}
          hint={human.runs ? (hiveLoaded ? t('ui.BrainMapView.humanOverBoth', { count: human.runs }) : t('ui.BrainMapView.humanOverLocal', { count: human.runs })) : t('ui.BrainMapView.humanNone')} />
      </div></div>
      <div><SectionLabel>{t('ui.BrainMapView.brain')}</SectionLabel><div className={grid}>
        <Stat label={t('ui.BrainMapView.knowledgeEntries')} value={ev.brain.entries} hint={t('ui.BrainMapView.changes30', { n: num(ev.brain.recentActivity) })} />
        <Stat label={t('ui.BrainMapView.helpsRuns')} value="UNKNOWN" hint={t('ui.BrainMapView.notMeasured')} />
      </div></div>
    </div>
  );
}
