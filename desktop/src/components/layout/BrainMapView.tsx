/**
 * BrainMapView — the bodies of a Brain's detail tabs that read the Brain map data.
 * The tabs mirror the parts of the map (Overview, Needs you, Brain (DDD), Project
 * Agent, Conductor · Runs, Reflect · Evolution, Eval); clicking a map part opens its
 * tab.
 *   BrainMapView      Overview: the Brain map, and beside it Needs you + Eval summary
 *   BrainNeedsView    Needs you: Runs, rule proposals, a Project Agent draft
 *   BrainAgentView    Project Agent: review / revise / confirm, and Start a Run
 *   BrainRunsView     Conductor · Runs: the three lanes with this Brain's Runs
 *   BrainEvalView     Eval: the five dimensions, UNKNOWN where nobody measures
 *   BrainAddKnowledge Brain (DDD): add files / folders / links / repos, distilled in chat
 * The tab bar, header and the Brain (DDD) / Reflect bodies live in BrainHub.
 *
 * Reads only records that already exist: the brain detail + review queue (cached
 * ddd hooks) and the 30-day pipeline analytics (one cached query, the same payload
 * the Pipelines overlay reads). Nothing here recomputes on the backend.
 * What is not built yet (Conductor / Project Agent eval) is shown and labelled `mock`.
 * The Project Agent definition is real: project-agent.yaml, a skeleton at creation and
 * drafted from the DDD. Its form (ProjectAgentForm) shows the standard template's eight
 * sections, saves changes and confirms through the backend; bigger changes go to chat.
 * Submitting a request is real: it goes to chat as a pipeline Run request.
 *
 * Exports: BrainMapView, BrainNeedsView, BrainAgentView, BrainRunsView, BrainEvalView,
 *          BrainAddKnowledge, BrainTab, MapPanel
 */
import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useBrainDetail, useReview, type BrainDetail, type ProjectAgent, type SectionKey } from '../../services/ddd';
import { pipelinesService, type PipelineAnalytics, type PipelineRunSummary } from '../../services/pipelines';
import { Stat, SectionLabel, StatusBadge, Hint, type Tone } from '../overlay';
import { BrainMap, type BrainMapTarget, type BrainMapProps, type MapTone } from './BrainMap';
import { MaterialPicker, type MaterialRow } from './StarterMaterial';
import { ProjectAgentForm } from './ProjectAgentForm';
import { buildAddKnowledgePrompt, buildResumeSetupPrompt } from '../../utils/newBrainDispatch';
import { openOverlayAt } from '../../utils/overlayTarget';
import { copyToClipboard } from '../../utils/clipboard';
import {
  sectionCounts, projectRuns, runTone, needsYouItems, brainEval, type RunTone,
} from './brainMapModel';

/** The detail tabs of a Brain: one per part of the Brain map, plus Overview. */
export type BrainTab = 'overview' | 'needs' | 'brain' | 'agent' | 'runs' | 'reflect' | 'eval';
/** Where the Hub home may land inside a Brain (kept for its callers). */
export type MapPanel = 'need' | 'agent' | 'add';

type Dispatch = (msg: string, opts?: { autoSend?: boolean }) => boolean;

const TONE_OF: Record<RunTone, Tone> = {
  running: 'primary', needs: 'error', landed: 'success', failed: 'warning', stopped: 'neutral',
};
const TONE_LABEL: Record<RunTone, string> = {
  running: 'running', needs: 'needs you', landed: 'landed', failed: 'not landed', stopped: 'stopped',
};

function Mock() {
  return <span className="ml-1.5 text-meta font-semibold text-[var(--color-status-warning)]" data-testid="bm-mock">mock</span>;
}
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
  const { data: review = null } = useReview(name);
  const { data: analytics = null } = useQuery<PipelineAnalytics>({
    queryKey: ['pipeline-analytics', '30d'], queryFn: () => pipelinesService.fetchAnalytics('30d'), staleTime: 30_000,
  });
  const runs = useMemo(() => projectRuns(analytics, name), [analytics, name]);
  const needs = useMemo(() => needsYouItems(runs, review, detail?.projectAgent, detail?.pendingBySource), [runs, review, detail]);
  const ev = useMemo(() => brainEval(runs, detail, review), [runs, detail, review]);
  return { detail, review, runs, needs, ev };
}

/** Status dots on the map, from the same records as the numbers. */
function mapTones(detail: BrainDetail | null, runs: PipelineRunSummary[], ev: ReturnType<typeof brainEval>): BrainMapProps['tones'] {
  const has = (k: SectionKey) => (detail?.sections.find((s) => s.key === k)?.members.length ?? 0) > 0;
  const base = (k: SectionKey): MapTone => (has(k) ? 'ok' : 'attention');
  const pa = detail?.projectAgent;
  return {
    identity: base('identity'),
    knowledge: has('knowledge') ? ((ev.brain.sinking ?? 0) > 0 ? 'attention' : 'ok') : 'attention',
    gates: ev.brain.pending ? 'needs' : base('gates'),
    capabilities: base('capabilities'),
    delivery: base('delivery'),
    refresher: base('refresher'),
    agent: pa === undefined ? undefined : pa === null ? 'attention'
      : (pa.problems.length || pa.status !== 'reviewed') ? 'needs' : 'ok',
    conductor: runs.some((r) => r.needsYou) ? 'needs' : runs.some((r) => r.status === 'running') ? 'busy' : runs.length ? 'ok' : undefined,
    reflect: ev.brain.pending ? 'needs' : ev.brain.autoApplied ? 'ok' : undefined,
  };
}

function agentLine(pa: ProjectAgent | null | undefined): string | undefined {
  if (pa === undefined) return undefined;
  if (pa === null) return 'not generated yet';
  if (pa.problems.length) return 'file has problems';
  return `${pa.status === 'reviewed' ? 'reviewed' : 'draft'} · ${pa.defaultProfile ?? '--'}`;
}

/** Overview tab: the Brain map, Needs you and an Eval summary. Map parts open tabs. */
export function BrainMapView({ name, onGoTo, onDispatch, onRequestClose }: {
  name: string;
  onGoTo: (tab: BrainTab) => void;
  onDispatch?: Dispatch;
  /** Close the Brain Hub so the chat tab that received a dispatch is visible. */
  onRequestClose?: () => void;
}) {
  const { detail, review, runs, needs, ev } = useBrainData(name);
  const send = useSend(onDispatch, onRequestClose);
  const version = review?.head_sha ? `@ ${review.head_sha.slice(0, 7)}` : 'DDD';

  const open = (t: BrainMapTarget) => {
    onGoTo(t === 'need' ? 'needs' : t === 'browse' ? 'brain' : t);
  };

  return (
    <div className="flex flex-col gap-3 p-4 min-h-full" data-testid="brain-map-view">
      {detail?.setupState === 'setting-up' && (
        <div className="flex items-center gap-3 rounded-lg border px-3 py-2 border-[color-mix(in_srgb,var(--color-status-warning)_45%,transparent)] bg-[color-mix(in_srgb,var(--color-status-warning)_8%,transparent)]"
          data-testid="bm-setup-banner">
          <span className="material-symbols-outlined icon-sm text-[var(--color-status-warning)]">construction</span>
          <div className="flex-1 min-w-0">
            <div className="text-small font-semibold">Setup is not finished</div>
            <div className="text-meta text-[var(--color-text-muted)]">The agent checks what is missing and finishes only that, then marks it ready.</div>
          </div>
          <button className="btn-sm btn-primary shrink-0" data-testid="bm-setup-resume"
            onClick={() => send(buildResumeSetupPrompt(name))}>Continue setup in chat</button>
        </div>
      )}
      {/* the map on the left; what needs you and how it is doing on the right */}
      <div className="grid gap-3 items-start" style={{ gridTemplateColumns: 'minmax(0, 1fr) 300px' }}>
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] p-3 min-w-0">
        <div className="flex items-center justify-between text-small">
          <span className="font-semibold">Brain map</span>
          <span className="text-meta text-[var(--color-text-muted)]">click a part to open its tab</span>
        </div>
        <BrainMap
          version={version}
          counts={sectionCounts(detail)}
          runs={runs.map((r) => ({ id: r.id, title: r.requirement || r.id, tone: runTone(r) }))}
          lanes={{ chat: runs.filter((r) => r.status === 'running' || r.needsYou).length, worker: 0, hive: 0 }}
          needs={needs.length}
          reflect={{ auto: ev.brain.autoApplied, pending: ev.brain.pending }}
          agentLine={agentLine(detail?.projectAgent)}
          evalLine={`${ev.runs.landed} of ${ev.runs.total} Runs landed`}
          tones={mapTones(detail, runs, ev)}
          onOpen={open}
        />
        <div className="flex flex-wrap items-center justify-center gap-4 pt-1 text-meta text-[var(--color-text-muted)]">
          <StatusBadge tone="success">healthy</StatusBadge>
          <StatusBadge tone="warning">needs attention</StatusBadge>
          <StatusBadge tone="error">needs you</StatusBadge>
          <StatusBadge tone="primary">running</StatusBadge>
          <span className="text-[var(--color-border)]">|</span>
          <LineKey v="--color-primary">a request</LineKey>
          <LineKey v="--color-text-muted">the Run</LineKey>
          <LineKey v="--color-status-success">knowledge written</LineKey>
          <LineKey v="--color-status-warning" dashed>waits for your OK</LineKey>
        </div>
      </div>
      <div className="flex flex-col gap-3 min-w-0">
        <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)]" data-testid="bm-panel-need">
          <div className="flex items-center justify-between px-3 py-2">
            <span className="text-small font-semibold">Needs you</span>
            <span className={`text-small font-semibold ${needs.length ? 'text-[var(--color-status-error)]' : 'text-[var(--color-text-muted)]'}`}>{needs.length}</span>
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

function NeedList({ items, onGoTo }: { items: ReturnType<typeof needsYouItems>; onGoTo: (t: BrainTab) => void }) {
  if (items.length === 0) {
    return <div className="px-3 pb-3 text-small text-[var(--color-status-success)]" data-testid="bm-need-empty">Nothing needs you right now.</div>;
  }
  return (
    <div>
      {items.map((it) => (
        <div key={it.kind + it.id} className="flex items-start gap-2 px-3 py-2 border-t border-[var(--color-border)]" data-testid={`bm-need-${it.id}`}>
          <div className="flex-1 min-w-0">
            <div className="text-small line-clamp-2">{it.title}</div>
            <div className="text-meta text-[var(--color-text-muted)] truncate">{it.detail}</div>
          </div>
          {it.kind === 'run' && <button className="btn-sm btn-secondary shrink-0" onClick={() => openRun(it.id)}>Answer</button>}
          {it.kind === 'other' && <button className="btn-sm btn-secondary shrink-0" onClick={openNeedYou}>Open</button>}
          {it.kind === 'proposal' && <button className="btn-sm btn-secondary shrink-0" onClick={() => onGoTo('reflect')}>Review</button>}
          {it.kind === 'agent' && <button className="btn-sm btn-secondary shrink-0" onClick={() => onGoTo('agent')}>Open</button>}
        </div>
      ))}
    </div>
  );
}

const pct = (x: number | null) => (x == null ? 'UNKNOWN' : `${Math.round(x * 100)}%`);
const num = (x: number | null, unit = '') => (x == null ? 'UNKNOWN' : `${x}${unit}`);

function EvalSummary({ ev, onOpen }: { ev: ReturnType<typeof brainEval>; onOpen: () => void }) {
  const rows: [string, string, string?][] = [
    ['Runs landed', `${ev.runs.landed} / ${ev.runs.total}`],
    ['Average cycle', num(ev.runs.avgCycleMin, ' min')],
    ['Knowledge, 30 days', ev.brain.recentActivity == null ? 'UNKNOWN' : `+${ev.brain.recentActivity}`],
    ['Sinking entries', num(ev.brain.sinking), ev.brain.sinking ? 'text-[var(--color-status-warning)]' : undefined],
    ['Tokens', ev.runs.tokens == null ? 'UNKNOWN' : `${Math.round(ev.runs.tokens / 1000)}k`],
  ];
  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)]" data-testid="bm-eval-summary">
      <div className="flex items-center justify-between px-3 py-2">
        <span className="text-small font-semibold">Eval · 30 days</span>
        <button className="text-meta text-[var(--color-primary)]" onClick={onOpen} data-testid="bm-eval-open">All of Eval ›</button>
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
  const { needs } = useBrainData(name);
  return (
    <div className="p-4" data-testid="bm-needs-view">
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)]">
        <div className="px-3 py-2"><Hint>Everything in this Brain that waits for your judgement: Runs that need you, rule proposals from Reflect, and a Project Agent draft to confirm.</Hint></div>
        <NeedList items={needs} onGoTo={onGoTo} />
      </div>
    </div>
  );
}

/** Project Agent tab: the definition (review / revise / confirm) and Start a Run. */
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

/** Conductor · Runs tab: the three lanes and this Brain's Runs. */
export function BrainRunsView({ name }: { name: string }) {
  const { runs } = useBrainData(name);
  return (
    <div className="p-4 max-w-[900px]" data-testid="bm-panel-runs">
      <RunsPanel runs={runs} />
    </div>
  );
}

/** Eval tab: every dimension, full width. */
export function BrainEvalView({ name }: { name: string }) {
  const { ev } = useBrainData(name);
  return (
    <div className="p-4" data-testid="bm-panel-eval">
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] p-3"><EvalPanel ev={ev} /></div>
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
      <Hint>Files, folders, links or repos. Each is sorted by type: documents are distilled into the knowledge docs, folders are shelved for reference, a repo becomes a new asset. The agent keeps only what is worth keeping and tells you what it skipped.</Hint>
      <MaterialPicker items={items} setItems={setItems} draft={draft} setDraft={setDraft} testPrefix="bm-add"
        emptyText="Add what this Brain should learn from: design docs, runbooks, a wiki page, another repo." />
      <div className="flex items-center gap-3">
        <span className="text-meta text-[var(--color-text-muted)] flex-1">
          {items.length ? `${counts('DISTILL')} to distill · ${counts('SHELF')} to shelve · ${counts('GOVERN')} new asset${counts('GOVERN') === 1 ? '' : 's'}` : 'Nothing added yet.'}
        </span>
        <button className="btn-md btn-primary" disabled={!items.length} onClick={send} data-testid="bm-add-send">
          Distill into the Brain
        </button>
      </div>
      {failed && <div className="text-meta text-[var(--color-status-error)]" data-testid="bm-add-failed">No chat tab could take it right now. Close a busy tab or send the draft that is waiting, then try again.</div>}
    </div>
  );
}



/** No definition yet: offer to draft it from the DDD in chat. */
function AgentNone({ onGenerate }: { onGenerate: () => void }) {
  return (
    <div data-testid="bm-agent-none">
      <SectionLabel>Project Agent</SectionLabel>
      <div className="text-small mt-1">This Brain has no Project Agent yet.</div>
      <div className="text-meta text-[var(--color-text-muted)]">It is drafted from the DDD: the knowledge docs, bindings.yaml and the repo&apos;s CI, with the pipeline&apos;s profiles.</div>
      <button className="btn-sm btn-primary mt-2" onClick={onGenerate} data-testid="bm-agent-generate">Generate from the DDD</button>
    </div>
  );
}

function AgentPanel({ name, runs, delivery, agent, onDispatch }: {
  name: string; runs: PipelineRunSummary[]; delivery: string[];
  agent?: ProjectAgent | null; onDispatch?: (msg: string, opts?: { autoSend?: boolean }) => boolean;
}) {
  const [req, setReq] = useState('');
  const [title, setTitle] = useState('');
  const [sent, setSent] = useState<string | null>(null);
  const profiles = runs.reduce<Record<string, number>>((m, r) => { if (r.profile) m[r.profile] = (m[r.profile] ?? 0) + 1; return m; }, {});
  const usual = Object.entries(profiles).sort((a, b) => b[1] - a[1])[0]?.[0];

  const send = async (msg: string, label: string) => {
    if (onDispatch?.(msg)) { setSent(`Sent to chat: ${label}`); return; }
    const copied = await copyToClipboard(msg);
    setSent(copied
      ? `Copied to the clipboard: ${label}. Paste it into a chat tab.`
      : `Could not send or copy: ${label}. Open a chat tab and try again.`);
  };
  const submit = () => {
    const text = req.trim();
    if (!text) return;
    const t = title.trim() || (text.length > 60 ? text.slice(0, 57).replace(/\s+\S*$/, '') + '…' : text);
    send(`Use the pipeline on ${name} for this change. Pick the profile from its Project Agent (project-agent.yaml).\nTitle: ${t}\nRequirement: ${text}`, t);
    setReq(''); setTitle('');
  };

  return (
    <div className="flex flex-col gap-3">
      {agent === undefined ? (
        <div>
          <SectionLabel>Delivery definition</SectionLabel>
          <div className="text-small mt-1">
            Usual profile: <b>{usual ?? '--'}</b>
            <span className="text-[var(--color-text-muted)]"> (from {runs.length} Runs in 30 days)</span>
          </div>
          <div className="text-meta text-[var(--color-text-muted)] mt-1 truncate">
            Delivery files: {delivery.length ? delivery.join(', ') : 'none'}
          </div>
        </div>
      ) : (
        agent === null
          ? <AgentNone onGenerate={() => send(`Draft the Project Agent of the ${name} Brain from its DDD (s_project-manager P3b, artifact_cli project-agent-draft) and tell me when it is on the Brain Hub form.`, 'draft the Project Agent')} />
          : <ProjectAgentForm key={name} brain={name} agent={agent}
              onRevise={(note) => send(`Revise the Project Agent of the ${name} Brain (project-agent.yaml): ${note}\nGive each change its source, keep it a draft, and tell me when it is on the Brain Hub form.`, 'revise the Project Agent')} />
      )}
      <section className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] p-4" data-testid="bm-agent-send">
        <SectionLabel>Send it work</SectionLabel>
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Title (optional, at most 60 characters)" maxLength={60}
          className="mt-2 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-input-bg)] px-2 py-1 text-small" data-testid="bm-agent-title" />
        <textarea value={req} onChange={(e) => setReq(e.target.value)} rows={3} placeholder={`Describe a change for ${name}`}
          className="mt-1 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-input-bg)] px-2 py-1 text-small" data-testid="bm-agent-req" />
        <div className="flex flex-wrap items-center gap-3 mt-1 text-meta text-[var(--color-text-muted)]">
          It runs in a chat tab; the agent picks the mode and profile from the definition above.
          <button className="btn-sm btn-primary ml-auto" disabled={!req.trim()} onClick={submit} data-testid="bm-agent-submit">Submit as a Run</button>
        </div>
        {sent && <div className="text-meta text-[var(--color-status-success)] mt-1" data-testid="bm-agent-sent">{sent}</div>}
      </section>
    </div>
  );
}

function RunsPanel({ runs }: { runs: PipelineRunSummary[] }) {
  return (
    <div className="flex flex-col gap-2">
      <Hint>One Conductor for the whole machine. This shows the Runs of this Brain from the last 30 days.</Hint>
      <Lane title="Chat tab (inline)" meta={`${runs.length} Runs`}>
        {runs.length === 0 && <div className="text-meta text-[var(--color-text-muted)]">No Runs in the last 30 days.</div>}
        {runs.map((r) => {
          const t = runTone(r);
          return (
            <button key={r.id} type="button" onClick={() => openRun(r.id)} data-testid={`bm-runrow-${r.id}`}
              className="w-full text-left py-1.5 border-b border-[var(--color-border)] last:border-b-0 hover:bg-[var(--color-hover)]">
              <div className="flex items-center justify-between gap-2">
                <span className="text-small truncate">{r.requirement || r.id}</span>
                <StatusBadge tone={TONE_OF[t]}>{TONE_LABEL[t]}</StatusBadge>
              </div>
              <div className="text-meta text-[var(--color-text-muted)]">
                {r.id} · {r.profile || '--'} · stage {r.progress || '--'}{r.cycleTimeMin != null ? ` · ${r.cycleTimeMin} min` : ''}
              </div>
            </button>
          );
        })}
      </Lane>
      <Lane title="Local Worker" meta="0/1 slots" mock><div className="text-meta text-[var(--color-text-muted)]">Background Runs, one at a time. Comes with Conductor V1.</div></Lane>
      <Lane title="Hive" meta="0/2 slots" mock><div className="text-meta text-[var(--color-text-muted)]">Remote Runs on another machine. Later.</div></Lane>
    </div>
  );
}

function Lane({ title, meta, mock, children }: { title: string; meta: string; mock?: boolean; children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-[var(--color-border)] px-3 py-2">
      <div className="flex items-center justify-between">
        <span className="text-small font-semibold">{title}{mock && <Mock />}</span>
        <span className="text-meta text-[var(--color-text-muted)]">{meta}</span>
      </div>
      <div className="mt-1">{children}</div>
    </div>
  );
}

function EvalPanel({ ev }: { ev: ReturnType<typeof brainEval> }) {
  const grid = 'grid grid-cols-2 gap-2 mt-1';
  return (
    <div className="flex flex-col gap-3">
      <Hint>Eval reads records the Runs already wrote. It runs offline and never blocks a Run. A number nobody measures says UNKNOWN.</Hint>
      <div><SectionLabel>Runs · 30 days</SectionLabel><div className={grid}>
        <Stat label="Landed" value={`${ev.runs.landed} / ${ev.runs.total}`} hint={`completion ${pct(ev.runs.completionRate)}`} />
        <Stat label="Need you now" value={ev.runs.needsYou} tone={ev.runs.needsYou ? 'error' : undefined} />
        <Stat label="Average cycle" value={num(ev.runs.avgCycleMin, ' min')} />
        <Stat label="Tokens" value={ev.runs.tokens == null ? 'UNKNOWN' : `${Math.round(ev.runs.tokens / 1000)}k`} hint="only when every Run was measured" />
      </div></div>
      <div><SectionLabel>Conductor<Mock /></SectionLabel><div className={grid}>
        <Stat label="Queue wait" value="UNKNOWN" hint="no queue until Conductor V1" />
        <Stat label="Worker busy" value="UNKNOWN" hint="no Local Worker yet" />
      </div></div>
      <div><SectionLabel>Project Agent<Mock /></SectionLabel><div className={grid}>
        <Stat label="Interventions per Run" value="UNKNOWN" hint="needs human-loop v2 records" />
        <Stat label="Change requests" value="UNKNOWN" hint="needs human-loop v2 records" />
      </div></div>
      <div><SectionLabel>Brain</SectionLabel><div className={grid}>
        <Stat label="Knowledge entries" value={ev.brain.entries} hint={`${num(ev.brain.recentActivity)} changes in 30 days`} />
        <Stat label="Sinking" value={num(ev.brain.sinking)} hint="dormant or archived entries" tone={ev.brain.sinking ? 'warning' : undefined} />
        <Stat label="Reclaimable noise" value={num(ev.brain.reclaimable)} />
        <Stat label="Does it help Runs?" value="UNKNOWN" hint="not measured, not guessed" />
      </div></div>
    </div>
  );
}
