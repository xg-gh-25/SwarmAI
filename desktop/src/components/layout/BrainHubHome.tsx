/**
 * BrainHubHome — the Brain Hub's front page. It answers three questions, in order:
 *   1. how big is the brain? — one line: the number of Brains, the knowledge they hold,
 *      what they learned in 30 days; the one page action, New Brain, beside it;
 *   2. does anything wait on me? — ONE attention band, only when something is open: the
 *      total and its breakdown by severity (brainMapModel.Severity), the queue one click
 *      away (each item opens where it is handled);
 *   3. which Brain do I open? — the built-in Brains (SwarmAI, then General) and the
 *      person's own, alphabetical so a card never moves. A card: the flower, the name,
 *      what the Brain is, and one quiet footer — its size and recency, plus its one
 *      status when there is one. Color is a small dot, warm only for what blocks on the
 *      person or waits for a review; setup (a Project Agent to confirm) stays grey.
 * Adding knowledge is an action of one Brain: it lives on that Brain's page (and the
 * Library, on General's), not here. Design: s_frontend-design's design-judgment.md
 * (one thing stands out, color only for what needs action, separate with whitespace).
 *
 * Data: the gallery summaries and the General Brain already fetched by BrainHub, and
 * the 30-day pipeline analytics (the same cached query the Brain map uses).
 * Exports: BrainHubHome, Flower (the six-petal icon, also in the Brain header)
 */
import { useMemo, useState, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { BrainSummary, GeneralBrain, SectionKey } from '../../services/ddd';
import { pipelinesService, type PipelineAnalytics } from '../../services/pipelines';
import {
  SECTION_ORDER, SEVERITY_ORDER, brainSignal, hubNeeds, hubSummary,
  type BrainSignalTone, type HubNeed, type Severity,
} from './brainMapModel';
import { openOverlayAt } from '../../utils/overlayTarget';

/** "5m" / "3h" / "2d" since an ISO time; '' when unparseable. */
function waited(iso: string, now = Date.now()): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return '';
  const min = Math.max(0, Math.floor((now - t) / 60000));
  if (min < 60) return `${min}m`;
  const h = Math.floor(min / 60);
  return h < 24 ? `${h}h` : `${Math.floor(h / 24)}d`;
}

type OpenTarget = { view?: 'map' | 'review'; panel?: 'need' | 'add' | 'agent' };

const ANG: Record<SectionKey, number> = {
  identity: -120, knowledge: -60, gates: 0, capabilities: 60, refresher: 120, delivery: 180,
};

function hex(x: number, y: number, r: number) {
  const p: string[] = [];
  for (let i = 0; i < 6; i++) {
    const a = (Math.PI / 180) * (60 * i - 90);
    p.push(`${(x + r * Math.cos(a)).toFixed(1)},${(y + r * Math.sin(a)).toFixed(1)}`);
  }
  return p.join(' ');
}

/** The six-petal flower: a petal is filled when that DDD section exists. */
export function Flower({ b, size = 56 }: { b: BrainSummary; size?: number }) {
  const R = 7, D = Math.sqrt(3) * R + 1.2, c = 24;
  return (
    <svg viewBox="0 0 48 48" width={size} height={size} aria-hidden="true" className="shrink-0">
      {SECTION_ORDER.map(({ key }) => {
        const a = (ANG[key] * Math.PI) / 180;
        const on = b.sectionsPresent?.[key];
        return (
          <polygon key={key} points={hex(c + D * Math.cos(a), c + D * Math.sin(a), R)} strokeWidth={1}
            strokeDasharray={on ? undefined : '2 2'}
            className={on ? 'fill-[color-mix(in_srgb,var(--color-primary)_18%,transparent)] stroke-[var(--color-primary)]'
              : 'fill-transparent stroke-[var(--color-border)]'} />
        );
      })}
      <polygon points={hex(c, c, R)} className="fill-[var(--color-primary)]" />
    </svg>
  );
}

/** The one colored mark of a status: warm only for what needs the person. */
function Dot({ tone }: { tone: BrainSignalTone }) {
  const cls: Record<BrainSignalTone, string> = {
    urgent: 'bg-[var(--color-status-error)]',
    review: 'bg-[var(--color-status-warning)]',
    live: 'bg-[var(--color-primary)] shadow-[0_0_0_3px_color-mix(in_srgb,var(--color-primary)_25%,transparent)]',
    setup: 'border-[1.5px] border-[var(--color-text-muted)]',
    notice: 'bg-[var(--color-text-faint)]',
    calm: '',
  };
  return <span aria-hidden="true" className={`inline-block shrink-0 w-[7px] h-[7px] rounded-full ${cls[tone]}`} />;
}

/** The attention band's breakdown: one chip per kind of open item. */
const CHIP: Record<string, { label: (n: number) => string }> = {
  runs: { label: (n) => `${n} Run${n === 1 ? '' : 's'} waiting` },
  escalations: { label: (n) => `${n} escalation${n === 1 ? '' : 's'} to decide` },
  jobs: { label: (n) => `${n} other item${n === 1 ? '' : 's'}` },
  'agent-fix': { label: (n) => `${n} agent file${n === 1 ? '' : 's'} to fix` },
  agent: { label: (n) => `${n} agent${n === 1 ? '' : 's'} to confirm` },
  repos: { label: (n) => `${n} repositor${n === 1 ? 'y' : 'ies'} newly read` },
};

function chipsOf(needs: HubNeed[]) {
  const by = new Map<string, { severity: Severity; count: number }>();
  for (const n of needs) {
    const key = n.kind === 'run' ? 'runs' : n.kind === 'agent' && n.severity === 'review' ? 'agent-fix' : n.kind;
    const got = by.get(key);
    if (got) got.count += n.count;
    else by.set(key, { severity: n.severity, count: n.count });
  }
  return [...by.entries()]
    .map(([key, v]) => ({ key, ...v, text: CHIP[key]?.label(v.count) ?? `${v.count} ${key}` }))
    .sort((x, y) => SEVERITY_ORDER.indexOf(x.severity) - SEVERITY_ORDER.indexOf(y.severity));
}

/** The General Brain card's one status: can a task that belongs to no Brain go out? */
function generalSignal(g: GeneralBrain): { tone: BrainSignalTone; text: string } {
  if (g.projectAgentStatus === 'draft') return { tone: 'setup', text: 'Runner Agent to confirm' };
  if (g.projectAgentStatus === 'invalid') return { tone: 'review', text: 'Runner Agent has problems' };
  if (g.projectAgentStatus === 'reviewed') return { tone: 'calm', text: '' };
  // no definition: a task that belongs to no Brain cannot go out
  return { tone: 'review', text: 'No Runner Agent yet' };
}

const entriesOf = (b: BrainSummary) => Object.values(b.typeCounts ?? {}).reduce((s, n) => s + n, 0);

/** A card footer's quiet facts: how much the Brain knows, what it learned lately, when
 *  a Run of it last landed. */
function brainFacts(b: BrainSummary): string {
  const parts: string[] = [];
  if (b.typeCounts) parts.push(`${entriesOf(b).toLocaleString()} entries`);
  if (b.recentActivity) parts.push(`+${b.recentActivity} in 30 days`);
  const last = b.health.lastChangeRelative;
  if (last && last !== '--') parts.push(`landed ${last}`);
  return parts.join(' · ');
}

function BrainCard({ id, onClick, label, icon, name, tag, tagId, description, facts, signal }: {
  id: string; onClick: () => void; label: string; icon: ReactNode; name: string;
  tag?: string; tagId?: string; description?: string | null; facts: string;
  signal: { tone: BrainSignalTone; text: string };
}) {
  return (
    <div className="min-w-0" data-testid={`hub-card-${id}`}>
      <button onClick={onClick} aria-label={label} data-testid={`dddcard-${id}`}
        className="w-full h-full min-h-[140px] flex flex-col gap-2.5 text-left rounded-xl px-[18px] py-4
          bg-[var(--color-card)] border border-transparent outline-none transition-colors
          hover:border-[color-mix(in_srgb,var(--color-primary)_45%,transparent)]
          focus-visible:border-[var(--color-primary)]">
        <div className="flex items-center gap-3 min-w-0">
          {icon}
          <span className="truncate text-title font-semibold">{name}</span>
          {tag && (
            <span className="shrink-0 text-meta font-semibold tracking-[0.04em] px-2 rounded-full text-[var(--color-text-muted)] bg-[var(--color-hover)]"
              data-testid={tagId}>{tag}</span>
          )}
        </div>
        <p className={`m-0 text-small line-clamp-2 ${description ? 'text-[var(--color-text-muted)]' : 'text-[var(--color-text-faint)]'}`}
          data-testid={`hub-desc-${id}`}>
          {description || 'No description yet'}
        </p>
        <div className="mt-auto flex flex-wrap items-center gap-x-3 gap-y-1 min-w-0 text-small text-[var(--color-text-muted)]">
          <span data-testid={`hub-facts-${id}`}>{facts}</span>
          {signal.tone !== 'calm' && (
            <span className="ml-auto shrink-0 inline-flex items-center gap-[7px] whitespace-nowrap text-[var(--color-text-secondary)]"
              data-testid={`hub-signal-${id}`} data-tone={signal.tone}>
              <Dot tone={signal.tone} />{signal.text}
            </span>
          )}
        </div>
      </button>
    </div>
  );
}

function GroupLabel({ children, id }: { children: string; id: string }) {
  return (
    <div className="mt-8 mb-2.5 text-meta font-semibold uppercase tracking-[0.08em] text-[var(--color-text-muted)]" data-testid={id}>
      {children}
    </div>
  );
}

const GRID = { gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))' };

export function BrainHubHome({ brains, selfName, general = null, onOpenGeneral, onOpen }: {
  brains: BrainSummary[];
  selfName?: string;
  /** the built-in General Brain (null: an older daemon, or it could not be read) */
  general?: GeneralBrain | null;
  onOpenGeneral?: () => void;
  /** kept for the callers; adding knowledge lives on each Brain's page and General's */
  onOpenLibrary?: () => void;
  onOpen: (name: string, target?: OpenTarget) => void;
  /** kept for the callers; the Runner Agent's card now lives on the General page */
  onDispatch?: (msg: string, opts?: { autoSend?: boolean }) => boolean;
}) {
  const [queueOpen, setQueueOpen] = useState(false);
  const { data: analytics = null } = useQuery<PipelineAnalytics>({
    queryKey: ['pipeline-analytics', '30d'], queryFn: () => pipelinesService.fetchAnalytics('30d'), staleTime: 30_000,
  });

  const sum = useMemo(() => hubSummary(brains, analytics, general), [brains, analytics, general]);
  // the queue reads most pressing first (a stable sort keeps hubNeeds' order inside a severity)
  const needs = useMemo(() => [...hubNeeds(brains, analytics, general)]
    .sort((x, y) => SEVERITY_ORDER.indexOf(x.severity) - SEVERITY_ORDER.indexOf(y.severity)),
  [brains, analytics, general]);
  const chips = useMemo(() => chipsOf(needs), [needs]);
  const open = needs.reduce((s, n) => s + n.count, 0);
  const worst = chips[0]?.severity;
  const self = brains.find((b) => b.name === selfName);
  // the person's own Brains, alphabetical: a card keeps its place when its state changes
  const own = useMemo(() => brains.filter((b) => b.name !== selfName)
    .sort((x, y) => x.name.localeCompare(y.name, undefined, { sensitivity: 'base' })), [brains, selfName]);
  const entries = brains.reduce((s, b) => s + entriesOf(b), 0);
  const hasCounts = brains.some((b) => b.typeCounts);

  const openNewBrain = () => window.dispatchEvent(new CustomEvent('swarm:show-new-brain'));
  /** Each open item opens where it is handled: a Run on its Pipelines drawer, a
   *  draft in its Project Agent tab, a newly read
   *  repository on the Brain's map, the rest (escalations, failing jobs, Runs the
   *  analytics did not list) in Need You. */
  const goNeed = (n: HubNeed) => {
    if (n.builtin) onOpenGeneral?.();
    else if (n.kind === 'run') openOverlayAt('pipeline', n.id);
    else if (n.kind === 'agent') onOpen(n.brain, { view: 'map', panel: 'agent' });
    else if (n.kind === 'repos') onOpen(n.brain, { view: 'map' });
    else window.dispatchEvent(new CustomEvent('swarm:show-needs-you'));
  };
  const openPipelines = () => window.dispatchEvent(new CustomEvent('swarm:show-pipeline'));
  /** A Projects/ Brain that carries the built-in General Brain's name (made before the
   *  name was reserved): its card says so, since dispatch treats the name as General. */
  const shadowed = (b: BrainSummary) => !!general?.shadowed && b.name.toLowerCase() === 'general';

  const card = (b: BrainSummary) => (
    <BrainCard key={b.name} id={b.name} onClick={() => onOpen(b.name)} label={`Open ${b.name}`}
      icon={<Flower b={b} size={36} />} name={b.name}
      tag={b.name === selfName ? 'Self' : undefined} tagId="dddcard-self-tag"
      description={b.description} facts={brainFacts(b)}
      signal={shadowed(b) ? { tone: 'review', text: 'Uses the built-in name General: rename it' } : brainSignal(b, analytics)} />
  );
  const generalCard = general && (
    <BrainCard key="builtin-general" id="builtin-general" onClick={() => onOpenGeneral?.()} label="Open General"
      icon={<span className="material-symbols-outlined icon-lg shrink-0 w-9 h-9 flex items-center justify-center text-[var(--color-primary)]"
        aria-hidden="true">local_library</span>}
      name="General" tag="Built-in" tagId="dddcard-builtin-tag"
      description={general.description || 'For tasks that belong to no Brain, with the Markdown in Knowledge/Library.'}
      facts={`${general.library.files.toLocaleString()} Library file${general.library.files === 1 ? '' : 's'}`}
      signal={generalSignal(general)} />
  );
  const builtIn = [self && card(self), generalCard].filter(Boolean);
  const total = brains.length + (general ? 1 : 0);

  return (
    <div className="max-w-[1240px] mx-auto px-7 pt-6 pb-5" data-testid="brainhub-gallery">
      <div className="flex items-end gap-4">
        <h2 className="m-0 text-display font-semibold tracking-[-0.01em]" data-testid="hub-count">
          {total} Brain{total === 1 ? '' : 's'}
        </h2>
        <span className="pb-0.5 text-body text-[var(--color-text-muted)]" data-testid="hub-ambient">
          {hasCounts && <><b className="font-semibold text-[var(--color-text-secondary)]">{entries.toLocaleString()}</b> knowledge entries</>}
          {hasCounts && sum.learned !== null && ' · '}
          {sum.learned !== null && <><b className="font-semibold text-[var(--color-text-secondary)]">+{sum.learned}</b> learned in 30 days</>}
        </span>
        <button className="ml-auto btn-sm btn-primary" onClick={openNewBrain} data-testid="hub-new-brain">New Brain</button>
      </div>

      {open > 0 && (
        <div className={`mt-5 rounded-xl ${worst === 'urgent' ? 'bg-[color-mix(in_srgb,var(--color-status-error)_8%,var(--color-card))]'
          : worst === 'review' ? 'bg-[color-mix(in_srgb,var(--color-status-warning)_7%,var(--color-card))]' : 'bg-[var(--color-card)]'}`}
          data-testid="hub-attention" data-tone={worst}>
          <div className="flex items-center gap-4 px-4 py-3">
            <span className="shrink-0 text-heading font-semibold" data-testid="hub-open-count">
              {open} open item{open === 1 ? '' : 's'}
            </span>
            <span className="flex flex-wrap gap-x-4 gap-y-1 text-small text-[var(--color-text-secondary)]">
              {chips.map((c) => (
                <span key={c.key} className="inline-flex items-center gap-[7px]" data-testid={`hub-chip-${c.key}`} data-tone={c.severity}>
                  <Dot tone={c.severity} />{c.text}
                </span>
              ))}
            </span>
            <button className="ml-auto shrink-0 btn-sm btn-secondary" aria-expanded={queueOpen}
              onClick={() => setQueueOpen((v) => !v)} data-testid="hub-needs-toggle">
              {queueOpen ? 'Hide' : 'Review'}
            </button>
          </div>
          {queueOpen && (
            <div className="pb-1.5" data-testid="hub-needs">
              {needs.map((n) => (
                <div key={n.id} className="flex items-center gap-3 px-4 py-2 border-t border-[var(--color-border)] text-small" data-testid={`hub-need-${n.id}`}>
                  <Dot tone={n.severity} />
                  <span className="font-semibold shrink-0">{n.builtin ? `${n.brain} (built-in)` : n.brain}</span>
                  <span className="flex-1 min-w-0 truncate text-[var(--color-text-secondary)]">{n.text}</span>
                  {n.since && <span className="text-meta text-[var(--color-text-muted)] shrink-0">{waited(n.since)}</span>}
                  <button className="btn-sm btn-secondary shrink-0" data-testid={`hub-need-go-${n.id}`} onClick={() => goNeed(n)}>
                    {n.kind === 'run' ? 'Answer' : 'Open'}
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {builtIn.length > 0 && (
        <>
          <GroupLabel id="hub-group-builtin">Built-in</GroupLabel>
          <div className="grid gap-3.5" style={GRID} data-testid="brainhub-builtin-wall">{builtIn}</div>
          {own.length > 0 && <GroupLabel id="hub-group-own">Your Brains</GroupLabel>}
        </>
      )}
      <div className={`grid gap-3.5 content-start ${builtIn.length ? '' : 'mt-6'}`} style={GRID} data-testid="brainhub-card-wall">
        {own.map(card)}
      </div>
      {total === 0 && (
        <div className="py-10 text-center text-small text-[var(--color-text-muted)]">
          No Brain yet.
        </div>
      )}

      <div className="mt-9 pt-3 flex items-center justify-between border-t border-[var(--color-border)] text-small text-[var(--color-text-muted)]">
        <button className="hover:text-[var(--color-text)]" onClick={openPipelines} data-testid="hub-conductor">
          Run Conductor · {sum.liveRuns} running ›
        </button>
        <span data-testid="hub-eval">
          Eval · last 30 days: {sum.finished ? `${sum.landed} of ${sum.finished} finished Runs landed` : 'no finished Runs'}
        </span>
      </div>
    </div>
  );
}
