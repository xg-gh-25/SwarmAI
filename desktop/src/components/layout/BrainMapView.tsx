/**
 * BrainMapView — the Brain Hub detail "Map" view: the Brain map on the left and,
 * on the right, the six things a person reviews and judges for one Brain:
 * Needs you · Project Agent · Runs (Conductor) · Reflect · Eval.
 *
 * Reads only records that already exist: the brain detail + review queue (cached
 * ddd hooks) and the 30-day pipeline analytics (one cached query, the same payload
 * the Pipelines overlay reads). Nothing here recomputes on the backend.
 * What is not built yet (Local Worker, Hive, Slack and schedule intake, Conductor /
 * Project Agent eval) is shown and labelled `mock`. The Project Agent definition is
 * real: project-agent.yaml, generated from the DDD at creation; this view only shows
 * it and sends revise / confirm requests to chat (the agent edits the file).
 * Submitting a request is real: it goes to chat as a pipeline Run request, the one
 * placement that exists today (P6: the person chose the pipeline).
 *
 * Exports: BrainMapView, MapPanel
 */
import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useBrainDetail, useReview, type ProjectAgent, type SectionKey } from '../../services/ddd';
import { pipelinesService, type PipelineAnalytics, type PipelineRunSummary } from '../../services/pipelines';
import { Stat, SectionLabel, Tabs, StatusBadge, Hint, type Tone } from '../overlay';
import { BrainMap, type BrainMapTarget } from './BrainMap';
import { MaterialPicker, type MaterialRow } from './StarterMaterial';
import { buildAddKnowledgePrompt, buildResumeSetupPrompt } from '../../utils/newBrainDispatch';
import {
  sectionCounts, projectRuns, runTone, needsYouItems, brainEval, type RunTone,
} from './brainMapModel';

export type MapPanel = 'need' | 'agent' | 'runs' | 'reflect' | 'eval' | 'add';

const TONE_OF: Record<RunTone, Tone> = {
  running: 'primary', needs: 'error', landed: 'success', failed: 'warning', stopped: 'neutral',
};
const TONE_LABEL: Record<RunTone, string> = {
  running: 'running', needs: 'needs you', landed: 'landed', failed: 'not landed', stopped: 'stopped',
};

function Mock() {
  return <span className="ml-1.5 text-meta font-semibold text-[var(--color-status-warning)]" data-testid="bm-mock">mock</span>;
}
function openPipelines() { window.dispatchEvent(new CustomEvent('swarm:show-pipeline')); }

export function BrainMapView({ name, onGoTo, onDispatch, onRequestClose }: {
  name: string;
  onGoTo: (view: 'browse' | 'review') => void;
  onDispatch?: (msg: string, opts?: { autoSend?: boolean }) => boolean;
  /** Close the Brain Hub so the chat tab that received a dispatch is visible. */
  onRequestClose?: () => void;
}) {
  const [panel, setPanel] = useState<MapPanel>('need');
  const { data: detail = null } = useBrainDetail(name);
  const { data: review = null } = useReview(name);
  const { data: analytics = null } = useQuery<PipelineAnalytics>({
    queryKey: ['pipeline-analytics', '30d'], queryFn: () => pipelinesService.fetchAnalytics('30d'), staleTime: 30_000,
  });

  const runs = useMemo(() => projectRuns(analytics, name), [analytics, name]);
  const needs = useMemo(() => needsYouItems(runs, review), [runs, review]);
  const ev = useMemo(() => brainEval(runs, detail, review), [runs, detail, review]);
  const version = review?.head_sha ? `@ ${review.head_sha.slice(0, 7)}` : 'DDD';

  const open = (t: BrainMapTarget, _s?: SectionKey) => {
    if (t === 'browse') onGoTo('browse');
    else setPanel(t === 'reflect' ? 'reflect' : t);
  };
  /** Send a prompt to chat now; on success close the Hub so its tab shows. */
  const sendToChat = (msg: string): boolean => {
    let ok = false;
    try { ok = onDispatch ? onDispatch(msg, { autoSend: true }) : false; } catch { ok = false; }
    if (ok) onRequestClose?.();
    return ok;
  };

  return (
    <div className="flex flex-col gap-3 p-3 min-h-full" data-testid="brain-map-view">
    {detail?.setupState === 'setting-up' && (
      <div className="flex items-center gap-3 rounded-lg border px-3 py-2 border-[color-mix(in_srgb,var(--color-status-warning)_45%,transparent)] bg-[color-mix(in_srgb,var(--color-status-warning)_8%,transparent)]"
        data-testid="bm-setup-banner">
        <span className="material-symbols-outlined icon-sm text-[var(--color-status-warning)]">construction</span>
        <div className="flex-1 min-w-0">
          <div className="text-small font-semibold">Setup is not finished</div>
          <div className="text-meta text-[var(--color-text-muted)]">The agent checks what is missing and finishes only that, then marks it ready.</div>
        </div>
        <button className="btn-sm btn-primary shrink-0" data-testid="bm-setup-resume"
          onClick={() => sendToChat(buildResumeSetupPrompt(name))}>Continue setup in chat</button>
      </div>
    )}
    <div className="grid gap-3" style={{ gridTemplateColumns: 'minmax(380px, 1fr) minmax(420px, 1fr)' }}>
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] p-2 self-start">
        <div className="flex items-center justify-between px-1 text-small">
          <span className="font-semibold">Brain map</span>
          <span className="flex items-center gap-2">
            <span className="text-meta text-[var(--color-text-muted)]">click any part to open it</span>
            <button className="btn-sm btn-secondary" onClick={() => setPanel('add')} data-testid="bm-add-open">
              <span className="material-symbols-outlined icon-xs">add</span> Add knowledge
            </button>
          </span>
        </div>
        <BrainMap
          version={version}
          counts={sectionCounts(detail)}
          runs={runs.map((r) => ({ id: r.id, title: r.requirement || r.id, tone: runTone(r) }))}
          lanes={{ chat: runs.filter((r) => r.status === 'running' || r.needsYou).length, worker: 0, hive: 0 }}
          needs={needs.length}
          reflect={{ auto: ev.brain.autoApplied, pending: ev.brain.pending }}
          onOpen={open}
        />
        <div className="flex flex-wrap gap-3 px-1 pb-1">
          {(['running', 'needs', 'landed', 'failed'] as RunTone[]).map((t) => (
            <StatusBadge key={t} tone={TONE_OF[t]}>{TONE_LABEL[t]}</StatusBadge>
          ))}
        </div>
      </div>

      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] px-3 pb-3 min-w-0">
        <Tabs<MapPanel>
          value={panel}
          onChange={setPanel}
          items={[
            { key: 'need', label: 'Needs you', count: needs.length, testid: 'bm-tab-need' },
            { key: 'agent', label: 'Project Agent', testid: 'bm-tab-agent' },
            { key: 'runs', label: 'Runs', count: runs.length, testid: 'bm-tab-runs' },
            { key: 'reflect', label: 'Reflect', count: ev.brain.pending || undefined, testid: 'bm-tab-reflect' },
            { key: 'eval', label: 'Eval', testid: 'bm-tab-eval' },
            { key: 'add', label: 'Add knowledge', testid: 'bm-tab-add' },
          ]}
        />
        <div className="pt-3" data-testid={`bm-panel-${panel}`}>
          {panel === 'need' && <NeedPanel items={needs} onReview={() => onGoTo('review')} />}
          {panel === 'agent' && <AgentPanel name={name} runs={runs} delivery={detail?.sections.find((s) => s.key === 'delivery')?.members.map((m) => m.path) ?? []} agent={detail ? detail.projectAgent : undefined} onDispatch={sendToChat} />}
          {panel === 'runs' && <RunsPanel runs={runs} />}
          {panel === 'reflect' && <ReflectPanel review={review} onReview={() => onGoTo('review')} />}
          {panel === 'eval' && <EvalPanel ev={ev} />}
          {panel === 'add' && <AddKnowledgePanel name={name} onSend={sendToChat} />}
        </div>
      </div>
    </div>
    </div>
  );
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

function Row({ children, testid }: { children: React.ReactNode; testid?: string }) {
  return <div className="py-2 border-b border-[var(--color-border)] last:border-b-0" data-testid={testid}>{children}</div>;
}

function NeedPanel({ items, onReview }: { items: ReturnType<typeof needsYouItems>; onReview: () => void }) {
  return (
    <div>
      <Hint>Everything in this Brain that waits for your judgement: Runs that need you and rule proposals from Reflect.</Hint>
      {items.length === 0 && (
        <div className="mt-3 text-body text-[var(--color-status-success)]" data-testid="bm-need-empty">Nothing needs you right now.</div>
      )}
      {items.map((it) => (
        <Row key={it.kind + it.id} testid={`bm-need-${it.id}`}>
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <StatusBadge tone={it.kind === 'run' ? 'error' : 'primary'}>{it.kind === 'run' ? 'Run' : 'Rule proposal'}</StatusBadge>
              <div className="text-body line-clamp-2">{it.title}</div>
              <div className="text-meta text-[var(--color-text-muted)] truncate">{it.detail}</div>
            </div>
            {it.kind === 'run'
              ? <button className="btn-sm btn-secondary shrink-0" onClick={openPipelines}>Open in Pipelines</button>
              : <button className="btn-sm btn-secondary shrink-0" onClick={onReview}>Review</button>}
          </div>
        </Row>
      ))}
    </div>
  );
}

const PLACE_LABEL: Record<string, string> = { 'chat-tab': 'Chat tab', 'local-worker': 'Local Worker', hive: 'Hive' };

/** The generated Project Agent definition, for review. Every change goes back
 *  through chat (the agent edits project-agent.yaml); this view never writes. */
function AgentDefinition({ name, agent, onGenerate, onConfirm, onRevise }: {
  name: string; agent: ProjectAgent | null;
  onGenerate: () => void; onConfirm: () => void; onRevise: (note: string) => void;
}) {
  const [note, setNote] = useState('');
  const openFile = () => document.dispatchEvent(new CustomEvent('swarm:open-file', {
    detail: { path: `Projects/${name}/project-agent.yaml` },
  }));
  if (agent === null) {
    return (
      <div data-testid="bm-agent-none">
        <SectionLabel>Project Agent</SectionLabel>
        <div className="text-small mt-1">This Brain has no Project Agent yet.</div>
        <div className="text-meta text-[var(--color-text-muted)]">It is generated from the DDD: the knowledge docs, bindings.yaml and the repo&apos;s CI, with the pipeline&apos;s profiles.</div>
        <button className="btn-sm btn-primary mt-2" onClick={onGenerate} data-testid="bm-agent-generate">Generate from the DDD</button>
      </div>
    );
  }
  const reviewed = agent.status === 'reviewed';
  return (
    <div data-testid="bm-agent-def">
      <SectionLabel right={<button className="btn-sm btn-ghost" onClick={openFile}>Open file</button>}>
        Project Agent
      </SectionLabel>
      <div className="flex items-center gap-2 mt-1">
        <StatusBadge tone={reviewed ? 'success' : 'warning'} testid="bm-agent-status">
          {reviewed ? `reviewed${agent.reviewedAt ? ' · ' + agent.reviewedAt : ''}` : 'draft · needs your review'}
        </StatusBadge>
        {agent.sources && agent.sources.length > 0 && (
          <span className="text-meta text-[var(--color-text-muted)] truncate">built from {agent.sources.join(', ')}</span>
        )}
      </div>
      {agent.problems.length > 0 && (
        <div className="mt-1 text-meta text-[var(--color-status-error)]" data-testid="bm-agent-problems">
          {agent.problems.join(' · ')}
        </div>
      )}
      {agent.summary && <div className="text-small mt-1">{agent.summary}</div>}

      <table className="w-full mt-2 text-small">
        <tbody>
          <tr className="border-t border-[var(--color-border)]">
            <td className="py-1.5 pr-3 align-top text-[var(--color-text-muted)] w-[110px]">Profile</td>
            <td className="py-1.5">
              <span className="font-semibold">{agent.defaultProfile ?? '--'}</span> by default
              {(agent.profileRules ?? []).map((r, i) => (
                <div key={i} className="text-meta text-[var(--color-text-muted)]"><span className="font-semibold text-[var(--color-text)]">{r.profile}</span> when {r.when}</div>
              ))}
            </td>
          </tr>
          <tr className="border-t border-[var(--color-border)]">
            <td className="py-1.5 pr-3 align-top text-[var(--color-text-muted)]">Review focus</td>
            <td className="py-1.5">
              {(agent.reviewFocus ?? []).length === 0 && <span className="text-[var(--color-text-muted)]">none</span>}
              {(agent.reviewFocus ?? []).map((r, i) => (
                <div key={i}><span className="font-semibold">{r.area}</span>
                  <span className="text-meta text-[var(--color-text-muted)]"> · {r.why}{r.source ? ` (${r.source})` : ''}</span></div>
              ))}
            </td>
          </tr>
          <tr className="border-t border-[var(--color-border)]">
            <td className="py-1.5 pr-3 align-top text-[var(--color-text-muted)]">Validations</td>
            <td className="py-1.5">
              {(agent.validations ?? []).length === 0 && <span className="text-[var(--color-text-muted)]">none</span>}
              {(agent.validations ?? []).map((v, i) => (
                <div key={i}><code className="text-meta">{v.command}</code>
                  <span className="text-meta text-[var(--color-text-muted)]">{v.source ? ` · ${v.source}` : ''}</span></div>
              ))}
            </td>
          </tr>
          <tr className="border-t border-[var(--color-border)]">
            <td className="py-1.5 pr-3 align-top text-[var(--color-text-muted)]">Runs on</td>
            <td className="py-1.5">{(agent.placements ?? []).map((p) => PLACE_LABEL[p] ?? p).join(', ') || '--'}</td>
          </tr>
          <tr className="border-t border-[var(--color-border)]">
            <td className="py-1.5 pr-3 align-top text-[var(--color-text-muted)]">Landing</td>
            <td className="py-1.5">{agent.landing?.approval === 'human' ? 'you approve every landing' : (agent.landing?.approval ?? '--')}
              {agent.landing?.via ? <span className="text-meta text-[var(--color-text-muted)]"> · via {agent.landing.via}</span> : null}</td>
          </tr>
        </tbody>
      </table>

      <div className="flex items-center gap-2 mt-2">
        <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="What to change, e.g. use full for API changes"
          className="flex-1 min-w-0 rounded-md border border-[var(--color-border)] bg-[var(--color-input-bg)] px-2 py-1 text-small" data-testid="bm-agent-note" />
        <button className="btn-sm btn-secondary" disabled={!note.trim()} onClick={() => { onRevise(note.trim()); setNote(''); }} data-testid="bm-agent-revise">Revise in chat</button>
        {!reviewed && <button className="btn-sm btn-primary" onClick={onConfirm} data-testid="bm-agent-confirm">Confirm</button>}
      </div>
    </div>
  );
}

const PLACES = [
  { key: 'chat', label: 'Chat tab (inline)', built: true },
  { key: 'worker', label: 'Local Worker', built: false },
  { key: 'hive', label: 'Hive', built: false },
] as const;

function AgentPanel({ name, runs, delivery, agent, onDispatch }: {
  name: string; runs: PipelineRunSummary[]; delivery: string[];
  agent?: ProjectAgent | null; onDispatch?: (msg: string, opts?: { autoSend?: boolean }) => boolean;
}) {
  const [req, setReq] = useState('');
  const [title, setTitle] = useState('');
  const [sent, setSent] = useState<string | null>(null);
  const profiles = runs.reduce<Record<string, number>>((m, r) => { if (r.profile) m[r.profile] = (m[r.profile] ?? 0) + 1; return m; }, {});
  const usual = Object.entries(profiles).sort((a, b) => b[1] - a[1])[0]?.[0];

  const send = (msg: string, label: string) => {
    const ok = onDispatch ? onDispatch(msg) : false;
    if (!ok) void navigator.clipboard?.writeText(msg);
    setSent(ok ? `Sent to chat: ${label}` : `Copied to the clipboard: ${label}. Paste it into a chat tab.`);
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
        <AgentDefinition name={name} agent={agent}
          onGenerate={() => send(`Generate the Project Agent of the ${name} Brain from its DDD (s_project-manager P3b), using only the pipeline profiles, and show it to me to confirm.`, 'generate the Project Agent')}
          onConfirm={() => send(`I reviewed the Project Agent of the ${name} Brain and confirm it as is. Set status: reviewed and reviewed_at in project-agent.yaml.`, 'confirm the Project Agent')}
          onRevise={(note) => send(`Revise the Project Agent of the ${name} Brain (project-agent.yaml): ${note}\nKeep only the pipeline profiles, give each change its source, then show me the result.`, 'revise the Project Agent')}
        />
      )}
      <Hint>The Project Agent is built from this Brain&apos;s knowledge with the pipeline&apos;s profiles. You review and revise it. It drafts Runs; it never submits one, and it does not define its own Eval.</Hint>

      <div>
        <SectionLabel>Ask for a change</SectionLabel>
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Title (optional, at most 60 characters)" maxLength={60}
          className="mt-1 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-input-bg)] px-2 py-1 text-small" data-testid="bm-agent-title" />
        <textarea value={req} onChange={(e) => setReq(e.target.value)} rows={3} placeholder={`Describe a change for ${name}`}
          className="mt-1 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-input-bg)] px-2 py-1 text-small" data-testid="bm-agent-req" />
        <div className="flex flex-wrap items-center gap-3 mt-1 text-small">
          {PLACES.map((p) => (
            <label key={p.key} className={p.built ? '' : 'text-[var(--color-text-muted)]'}>
              <input type="radio" name="bm-place" defaultChecked={p.built} disabled={!p.built} className="mr-1" />
              {p.label}{!p.built && <Mock />}
            </label>
          ))}
          <button className="btn-sm btn-primary ml-auto" disabled={!req.trim()} onClick={submit} data-testid="bm-agent-submit">Submit as a Run</button>
        </div>
        {sent && <div className="text-meta text-[var(--color-status-success)] mt-1" data-testid="bm-agent-sent">{sent}</div>}
      </div>

      <div>
        <SectionLabel>Other ways in</SectionLabel>
        <Row><span className="text-small">Slack request in the project channel</span><Mock /></Row>
        <Row><span className="text-small">Schedule, e.g. a weekly flaky-test sweep</span><Mock /></Row>
      </div>
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
            <button key={r.id} type="button" onClick={openPipelines} data-testid={`bm-runrow-${r.id}`}
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

function ReflectPanel({ review, onReview }: { review: import('../../services/ddd').ReviewData | null; onReview: () => void }) {
  const auto = (review?.hunks ?? []).filter((h) => h.tag === 'cultivation·auto-applied');
  const pending = review?.proposals ?? [];
  return (
    <div className="flex flex-col gap-3">
      <Hint>A fact goes into Knowledge on its own and can be undone in Review. A change to how the Brain judges (Gates, Delivery) waits for your OK.</Hint>
      <div className="grid grid-cols-2 gap-2">
        <Stat label="Written automatically" value={auto.length} hint="since your last review" tone="success" />
        <Stat label="Waiting for you" value={pending.length} hint="rule proposals" tone={pending.length ? 'primary' : undefined} />
      </div>
      <div>
        <SectionLabel right={<button className="btn-sm btn-secondary" onClick={onReview}>Open Review</button>}>Recent</SectionLabel>
        {pending.map((p) => (
          <Row key={p.id}><StatusBadge tone="primary">waiting for you</StatusBadge>
            <div className="text-small">{p.content}</div>
            <div className="text-meta text-[var(--color-text-muted)]">{p.target_doc} / {p.target_section}{p.source_run_id ? ` · from ${p.source_run_id}` : ''}</div></Row>
        ))}
        {auto.slice(0, 8).map((h) => (
          <Row key={h.signature}><StatusBadge tone="success">written</StatusBadge>
            <div className="text-meta text-[var(--color-text-muted)] truncate">{h.file}</div></Row>
        ))}
        {pending.length + auto.length === 0 && <div className="text-meta text-[var(--color-text-muted)]">Nothing new since your last review.</div>}
      </div>
    </div>
  );
}

const pct = (x: number | null) => (x == null ? 'UNKNOWN' : `${Math.round(x * 100)}%`);
const num = (x: number | null, unit = '') => (x == null ? 'UNKNOWN' : `${x}${unit}`);

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
