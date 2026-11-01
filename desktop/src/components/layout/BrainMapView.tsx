/**
 * BrainMapView — the bodies of a Brain's detail tabs that read the Brain map data.
 * The tabs mirror the parts of the map (Overview, Needs you, Brain (DDD), Project
 * Agent, Reflect · Evolution, Eval); clicking a map part opens its tab.
 *   BrainMapView      Overview: the Brain map, and beside it Needs you + the two Eval numbers
 *   BrainNeedsView    Needs you: what waits on the person in this Brain itself
 *   BrainAgentView    Project Agent: what a Hive run reads, ask for a change, and Start a Run
 *   BrainEvalView     Eval: is the knowledge healthy, was it used (two numbers, two sections)
 *   BrainAddKnowledge Brain (DDD): add files / folders / links / repos, distilled in chat
 * The tab bar, header and the Brain (DDD) / Reflect bodies live in BrainHub.
 *
 * A Brain holds knowledge only (design docs/design/2026-11-01-brain-eval-and-runs.md §2):
 * the Runs that use it, their waits, autonomy and cost are in Pipelines and Swarm Out.
 * The map shows the Brain's runs under way as lanes on its loop (§2.4), read as the very
 * rows Pipelines shows (``pipelineRows``, same cached queries); a lane opens its run in
 * Pipelines, the Runs title opens Pipelines on this Brain. No run statistics here. Eval reads GET /ddd/brains/{name}/eval (core/brain_eval):
 * what nobody measured says "not measured", a sample too small says "too few (n)",
 * never 0. The Project Agent definition is real: project-agent.yaml, a skeleton at
 * creation and drafted from the DDD, kept current by the agent. ProjectAgentView shows it
 * read-only; a change goes to the agent in chat. Submitting a request is real: it goes
 * to chat as a pipeline Run request.
 *
 * Exports: BrainMapView, BrainNeedsView, BrainAgentView, BrainEvalView,
 *          BrainAddKnowledge, BrainTab, MapPanel
 */
import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import {
  useBrainDetail, useBrainEval, useBrainKnowledge, type BrainDetail, type BrainEvalReport, type ProjectAgent, type SectionKey,
} from '../../services/ddd';
import { SectionLabel, StatusBadge, Hint } from '../overlay';
import { BrainMap, type BrainMapTarget, type BrainMapProps, type MapTone } from './BrainMap';
import { MaterialPicker, type MaterialRow } from './StarterMaterial';
import { ProjectAgentView } from './ProjectAgentView';
import { buildAddKnowledgePrompt, buildResumeSetupPrompt } from '../../utils/newBrainDispatch';
import { openOverlayAt, openPipelinesFor } from '../../utils/overlayTarget';
import { copyToClipboard } from '../../utils/clipboard';
import { sectionCounts, needsYouItems, needsCount } from './brainMapModel';
import { allRows, brainLanes } from './pipelineRows';
import { overlayQuery } from './overlayQueries';
import { useRuns } from '../swarm-out/live';

/** The detail tabs of a Brain: one per part of the Brain map, plus Overview. */
export type BrainTab = 'overview' | 'needs' | 'brain' | 'agent' | 'reflect' | 'eval';
/** Where the Hub home may land inside a Brain (kept for its callers). */
export type MapPanel = 'need' | 'agent' | 'add';

type Dispatch = (msg: string, opts?: { autoSend?: boolean }) => boolean;

/** Pipelines, opened on one Run (a missed-use row). */
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
  const needs = useMemo(() => needsYouItems(detail?.pendingBySource), [detail]);
  return { detail, needs, learnedWeek: knowledge ? knowledge.counts.week : null };
}

/** The Brain's runs under way, as the rows Pipelines shows (design §2.4). undefined
 *  until this machine's runs are loaded. */
function useBrainLanes(name: string) {
  const { data: analytics } = useQuery(overlayQuery.pipelineAnalytics('30d'));
  const hive = useRuns(false);
  return useMemo(() => {
    if (!analytics) return undefined;
    const out = brainLanes(allRows(analytics.byProject, hive.data?.runs, hive.data?.asks ?? []), name);
    return { lanes: out.lanes.map(({ key, place, title, waiting, progress }) => ({ key, place, title, waiting, progress })),
      more: out.more, total: out.total };
  }, [analytics, hive.data, name]);
}

/** Status dots on the map, from the same records as the numbers. */
function mapTones(detail: BrainDetail | null, learnedWeek: number | null): BrainMapProps['tones'] {
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
    reflect: learnedWeek ? 'ok' : undefined,
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
  const { detail, needs, learnedWeek } = useBrainData(name);
  const { data: ev = null } = useBrainEval(name);
  const needN = needsCount(needs);
  const send = useSend(onDispatch, onRequestClose);

  const runs = useBrainLanes(name);
  const open = (target: BrainMapTarget) => {
    if (target === 'runs') { openPipelinesFor(name); return; }
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
          needs={needN}
          reflect={{ week: learnedWeek }}
          agentLine={agentLine(detail?.projectAgent, t)}
          evalLine={ev ? healthLine(ev, t) : undefined}
          tones={mapTones(detail, learnedWeek)}
          runs={runs}
          onOpen={open}
          onOpenRun={openRun}
        />
        <div className="flex flex-wrap items-center justify-center gap-4 pt-1 text-meta text-[var(--color-text-muted)]">
          <StatusBadge tone="success">{t('ui.BrainMapView.legendHealthy')}</StatusBadge>
          <StatusBadge tone="warning">{t('ui.BrainMapView.legendAttention')}</StatusBadge>
          <StatusBadge tone="error">{t('ui.BrainMapView.legendNeedsYou')}</StatusBadge>
          <StatusBadge tone="primary">{t('ui.BrainMapView.legendRunning')}</StatusBadge>
          <span className="text-[var(--color-border)]">|</span>
          <LineKey v="--color-primary">{t('ui.BrainMapView.lineRequest')}</LineKey>
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
          {it.kind === 'other' && <button className="btn-sm btn-secondary shrink-0" onClick={openNeedYou}>{t('ui.BrainMapView.open')}</button>}
        </div>
      ))}
    </div>
  );
}

const pct = (x: number) => `${Math.round(x * 100)}%`;

/** The healthy rate as one line (the map's Eval part). */
function healthLine(ev: BrainEvalReport, t: TFunction): string {
  const h = ev.health;
  if (h.scored == null) return t('ui.BrainMapView.notMeasured');
  if (h.rate == null) return t('ui.BrainMapView.tooFew', { n: h.scored });
  return t('ui.BrainMapView.healthyLine', { pct: pct(h.rate) });
}

/** The two Eval questions' headline numbers: what they are, and what they compare to. */
function headlines(ev: BrainEvalReport | null, t: TFunction) {
  const h = ev?.health, u = ev?.used;
  const health = !h || h.scored == null
    ? { big: '—', vs: t('ui.BrainMapView.notMeasured'), tone: 'muted' as const }
    : h.rate == null
      ? { big: '—', vs: t('ui.BrainMapView.tooFew', { n: h.scored }), tone: 'muted' as const }
      : { big: pct(h.rate), vs: t('ui.BrainMapView.healthyVs', { target: pct(h.target), n: h.scored, since: day(h.since) }),
          tone: h.rate >= h.target ? 'ok' as const : 'warn' as const };
  const used = !u
    ? { big: '—', vs: t('ui.BrainMapView.notMeasured'), tone: 'muted' as const }
    : u.runs === 0
      ? { big: '—', vs: t('ui.BrainMapView.usedNoRuns'), tone: 'muted' as const }
      : { big: `${u.citing} / ${u.runs}`,
          vs: u.rate == null ? t('ui.BrainMapView.usedVsFew', { n: u.runs }) : t('ui.BrainMapView.usedVs', { pct: pct(u.rate) }),
          tone: 'muted' as const };
  return { health, used };
}

const day = (iso: string | null) => (iso ? iso.slice(5, 10) : '—');
const VS_TONE = { ok: 'text-[var(--color-status-success)]', warn: 'text-[var(--color-status-warning)]', muted: 'text-[var(--color-text-muted)]' };

function EvalSummary({ ev, onOpen }: { ev: BrainEvalReport | null; onOpen: () => void }) {
  const { t } = useTranslation();
  const { health, used } = headlines(ev, t);
  const rows: [string, { big: string; vs: string; tone: keyof typeof VS_TONE }][] = [
    [t('ui.BrainMapView.qHealthy'), health], [t('ui.BrainMapView.qUsed'), used],
  ];
  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)]" data-testid="bm-eval-summary">
      <div className="flex items-center justify-between px-3 py-2">
        <span className="text-small font-semibold">{t('ui.BrainMapView.evalTitle')}</span>
        <button className="text-meta text-[var(--color-primary)]" onClick={onOpen} data-testid="bm-eval-open">{t('ui.BrainMapView.evalAll')}</button>
      </div>
      {rows.map(([k, v]) => (
        <div key={k} className="px-3 py-1.5 border-t border-[var(--color-border)]">
          <div className="flex items-baseline justify-between text-small">
            <span className="text-[var(--color-text-muted)]">{k}</span>
            <span className="font-semibold tabular-nums">{v.big}</span>
          </div>
          <div className={`text-meta ${VS_TONE[v.tone]}`}>{v.vs}</div>
        </div>
      ))}
    </div>
  );
}

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
  const { detail } = useBrainData(name);
  const send = useSend(onDispatch, onRequestClose);
  return (
    <div className="p-4 max-w-[1180px]" data-testid="bm-panel-agent">
      <AgentPanel name={name}
        delivery={detail?.sections.find((s) => s.key === 'delivery')?.members.map((m) => m.path) ?? []}
        agent={detail ? detail.projectAgent : undefined} onDispatch={send} />
    </div>
  );
}

/** Eval tab: the two numbers on top, then the two questions, stacked full width. */
export function BrainEvalView({ name, onOpenFile }: {
  name: string;
  /** open a workspace file in the Canvas (the host closes the Hub first) */
  onOpenFile?: (workspaceRelPath: string) => void;
}) {
  const { data: ev = null, isError } = useBrainEval(name);
  return (
    <div className="p-4" data-testid="bm-panel-eval">
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] p-4">
        <EvalPanel ev={ev} failed={isError && !ev} onOpenFile={onOpenFile} />
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

function AgentPanel({ name, delivery, agent, onDispatch }: {
  name: string; delivery: string[];
  agent?: ProjectAgent | null; onDispatch?: (msg: string, opts?: { autoSend?: boolean }) => boolean;
}) {
  const { t } = useTranslation();
  const [req, setReq] = useState('');
  const [title, setTitle] = useState('');
  const [sent, setSent] = useState<string | null>(null);

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

/** One fact of a section: a label and its value ("—" when not measured). */
function Fact({ label, value, testId }: { label: string; value: string; testId?: string }) {
  return (
    <span className="text-small text-[var(--color-text-secondary)]" data-testid={testId}>
      {label}<b className="ml-1.5 font-semibold text-[var(--color-text)] tabular-nums">{value}</b>
    </span>
  );
}

/** A list of things to handle under a section (title, why), each row one click. */
function ToDo({ title, children, testId }: { title: string; children: React.ReactNode; testId: string }) {
  return (
    <div className="mt-3" data-testid={testId}>
      <div className="text-meta font-semibold text-[var(--color-text-muted)] mb-1">{title}</div>
      {children}
    </div>
  );
}

const ROW = 'grid w-full text-left gap-4 px-2 py-1.5 rounded-md hover:bg-[var(--color-hover)]';
const ROW_COLS = { gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1.2fr)' };

function EvalPanel({ ev, failed, onOpenFile }: {
  ev: BrainEvalReport | null; failed: boolean; onOpenFile?: (path: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const zh = i18n.language?.startsWith('zh');
  if (failed) return <div className="text-small text-[var(--color-text-muted)]" data-testid="bm-eval-failed">{t('ui.BrainMapView.evalUnreadable')}</div>;
  if (!ev) return <div className="text-small text-[var(--color-text-muted)]">{t('ui.BrainMapView.evalReading')}</div>;
  const { health, used } = headlines(ev, t);
  const h = ev.health, u = ev.used;
  const none = t('ui.BrainMapView.notMeasured');
  const dups = h.duplicates == null ? none : String(h.duplicates.extra);
  const gaps = h.gaps == null ? none : h.gaps.oldest_days == null ? String(h.gaps.open)
    : t('ui.BrainMapView.gapsOldest', { n: h.gaps.open, days: h.gaps.oldest_days });
  const wu = u.written_used;
  const wuText = wu == null ? none
    : wu.mature_written === 0 ? t('ui.BrainMapView.writtenUsedYoung', { days: wu.window_days })
    : wu.rate == null ? t('ui.BrainMapView.tooFew', { n: wu.mature_written })
    : `${pct(wu.rate)} (${wu.mature_used} / ${wu.mature_written})`;
  const tops = [
    { k: t('ui.BrainMapView.qHealthy'), v: health, id: 'bm-eval-top-health' },
    { k: t('ui.BrainMapView.qUsed'), v: used, id: 'bm-eval-top-used' },
  ];
  return (
    <div className="flex flex-col" data-testid="bm-eval">
      <div className="grid grid-cols-2 pb-4 border-b border-[var(--color-border)]">
        {tops.map(({ k, v, id }, i) => (
          <div key={id} className={i ? 'pl-6 border-l border-[var(--color-border)]' : ''} data-testid={id}>
            <div className="text-meta font-semibold text-[var(--color-text-muted)]">{k}</div>
            <div className="text-display font-semibold tabular-nums mt-1">{v.big}</div>
            <div className={`text-small ${VS_TONE[v.tone]}`}>{v.vs}</div>
          </div>
        ))}
      </div>

      <section className="py-4 border-b border-[var(--color-border)]" data-testid="bm-eval-health">
        <h3 className="text-body font-semibold">{t('ui.BrainMapView.qHealthy')}</h3>
        <div className="text-small text-[var(--color-text-muted)] max-w-[900px]">{t('ui.BrainMapView.healthHow', { min: h.min_sample })}</div>
        <div className="flex flex-wrap gap-8 mt-2.5">
          <Fact label={t('ui.BrainMapView.factEntries')} value={h.entries == null ? none : h.entries.toLocaleString()} />
          <Fact label={t('ui.BrainMapView.factDuplicates')} value={dups} testId="bm-eval-dups" />
          <Fact label={t('ui.BrainMapView.factGaps')} value={gaps} testId="bm-eval-gaps" />
        </div>
        {h.flagged.length > 0 && (
          <ToDo title={t('ui.BrainMapView.toHandle', { n: h.flagged_total ?? h.flagged.length })} testId="bm-eval-flagged">
            {h.flagged.map((f) => (
              <button key={f.unit_id} className={ROW} style={ROW_COLS} onClick={() => onOpenFile?.(f.rel)}
                title={`${f.rel}:${f.line}`} data-testid={`bm-eval-flag-${f.unit_id}`}>
                <span className="truncate text-small">
                  <span className={`text-meta px-1.5 mr-1.5 rounded ${f.verdict === 'withdraw'
                    ? 'text-[var(--color-status-error)] bg-[color-mix(in_srgb,var(--color-status-error)_13%,transparent)]'
                    : 'text-[var(--color-status-warning)] bg-[color-mix(in_srgb,var(--color-status-warning)_13%,transparent)]'}`}>{f.verdict}</span>
                  {f.title}
                </span>
                <span className="truncate text-meta text-[var(--color-text-muted)]">
                  {(zh ? f.reason_zh : f.reason_en) || '—'} · {f.rel.split('/').pop()}:{f.line}
                </span>
              </button>
            ))}
          </ToDo>
        )}
        {h.duplicates && h.duplicates.shown.length > 0 && (
          <ToDo title={t('ui.BrainMapView.dupList', { n: h.duplicates.groups })} testId="bm-eval-duplist">
            {h.duplicates.shown.map((d) => (
              <button key={d.head + d.places[0].rel} className={ROW} style={ROW_COLS} onClick={() => onOpenFile?.(d.places[0].rel)}>
                <span className="truncate text-small">{d.head}</span>
                <span className="truncate text-meta text-[var(--color-text-muted)]">
                  {d.places.map((p) => `${p.rel.split('/').pop()}:${p.line}`).join(' · ')}
                </span>
              </button>
            ))}
          </ToDo>
        )}
      </section>

      <section className="pt-4" data-testid="bm-eval-used">
        <h3 className="text-body font-semibold">{t('ui.BrainMapView.qUsed')}</h3>
        <div className="text-small text-[var(--color-text-muted)] max-w-[900px]">{t('ui.BrainMapView.usedHow', { days: ev.window_days })}</div>
        <div className="flex flex-wrap gap-8 mt-2.5">
          <Fact label={t('ui.BrainMapView.factWrittenUsed', { days: wu?.window_days ?? 14 })} value={wuText} testId="bm-eval-written-used" />
          <Fact label={t('ui.BrainMapView.factMissed')} value={u.missed_total == null ? none : String(u.missed_total)} />
        </div>
        {u.missed && u.missed.length > 0 && (
          <ToDo title={t('ui.BrainMapView.missedList', { n: u.missed_total ?? u.missed.length })} testId="bm-eval-missed">
            {u.missed.map((m) => (
              <button key={m.unit_id} className={ROW} style={ROW_COLS} onClick={() => openRun(m.run_id)}>
                <span className="truncate text-small">{m.title}</span>
                <span className="truncate text-meta text-[var(--color-text-muted)]">{m.run_id}</span>
              </button>
            ))}
          </ToDo>
        )}
        {u.shown.length > 0 && (
          <ToDo title={t('ui.BrainMapView.runsCited', { n: u.runs })} testId="bm-eval-runs">
            {u.shown.map((r) => (
              <button key={r.run_id} className={ROW} style={ROW_COLS} onClick={() => openRun(r.run_id)} data-testid={`bm-eval-run-${r.run_id}`}>
                <span className="truncate text-small">{r.requirement || r.run_id}</span>
                <span className="truncate text-meta text-[var(--color-text-muted)]">
                  {r.cited.length ? t('ui.BrainMapView.cited', { n: r.cited.length }) + ' · ' + r.cited.map((c) => c.head).join(' · ')
                    : t('ui.BrainMapView.citedNone')}
                </span>
              </button>
            ))}
          </ToDo>
        )}
      </section>
    </div>
  );
}
