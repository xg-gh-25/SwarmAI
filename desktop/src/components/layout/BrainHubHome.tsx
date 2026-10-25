/**
 * BrainHubHome — the Brain Hub's top level, in three layers:
 *   1. a summary row (Brains / Need you / Running / Being born are filters; Learned
 *      and Sinking only report) with the two entry points, Add knowledge and New Brain;
 *      choosing Need you also opens the one queue of everything waiting on a person;
 *   2. one card per Brain: the six-petal flower, the name, the ONE most urgent signal
 *      (brainSignal) and a one-line description, cards with something to do first;
 *   3. a footer line for the Run Conductor and Eval. Everything else lives one click
 *      down, on the Brain's map.
 *
 * Data: the gallery summaries already fetched by BrainHub plus the 30-day pipeline
 * analytics (the same cached query the Brain map uses). No other call.
 * Exports: BrainHubHome, HubFilter
 */
import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { BrainSummary, SectionKey } from '../../services/ddd';
import { pipelinesService, type PipelineAnalytics } from '../../services/pipelines';
import { SECTION_ORDER, brainSignal, hubNeeds, hubSummary, type BrainSignalTone } from './brainMapModel';

/** "5m" / "3h" / "2d" since an ISO time; '' when unparseable. */
function waited(iso: string, now = Date.now()): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return '';
  const min = Math.max(0, Math.floor((now - t) / 60000));
  if (min < 60) return `${min}m`;
  const h = Math.floor(min / 60);
  return h < 24 ? `${h}h` : `${Math.floor(h / 24)}d`;
}

export type HubFilter = 'all' | 'need' | 'running' | 'born';
type OpenTarget = { view?: 'map' | 'review'; panel?: 'need' | 'add' | 'agent' };

const ANG: Record<SectionKey, number> = {
  identity: -120, knowledge: -60, gates: 0, capabilities: 60, refresher: 120, delivery: 180,
};
const TONE_TEXT: Record<BrainSignalTone, string> = {
  error: 'text-[var(--color-status-error)]',
  primary: 'text-[var(--color-primary)]',
  warning: 'text-[var(--color-status-warning)]',
  muted: 'text-[var(--color-text-muted)]',
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
function Flower({ b }: { b: BrainSummary }) {
  const R = 7, D = Math.sqrt(3) * R + 1.2, c = 24;
  return (
    <svg viewBox="0 0 48 48" width={56} height={56} aria-hidden="true" className="shrink-0">
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

function SumCell({ label, value, sub, tone, filter, active, onPick }: {
  label: string; value: string | number; sub?: string; tone?: string;
  filter?: HubFilter; active?: boolean; onPick?: (f: HubFilter) => void;
}) {
  const body = (
    <>
      <div className="text-meta text-[var(--color-text-muted)]">{label}</div>
      <div className={`text-title font-semibold ${tone ?? ''}`}>{value}</div>
      {sub && <div className="text-meta text-[var(--color-text-muted)]">{sub}</div>}
    </>
  );
  if (!filter) {
    return <div className="px-4 py-2 border-l border-[var(--color-border)]" data-testid={`hub-sum-${label.toLowerCase().split(' ')[0]}`}>{body}</div>;
  }
  return (
    <button role="radio" aria-checked={active} onClick={() => onPick?.(filter)} data-testid={`hub-filter-${filter}`}
      className={`px-4 py-2 text-left min-w-[96px] border-r border-[var(--color-border)] hover:bg-[var(--color-hover)] ${active ? 'shadow-[inset_0_-2px_0_var(--color-primary)]' : ''}`}>
      {body}
    </button>
  );
}

export function BrainHubHome({ brains, selfName, onOpen }: {
  brains: BrainSummary[];
  selfName?: string;
  onOpen: (name: string, target?: OpenTarget) => void;
}) {
  const [filter, setFilter] = useState<HubFilter>('all');
  const [picking, setPicking] = useState(false);
  const { data: analytics = null } = useQuery<PipelineAnalytics>({
    queryKey: ['pipeline-analytics', '30d'], queryFn: () => pipelinesService.fetchAnalytics('30d'), staleTime: 30_000,
  });

  const sum = useMemo(() => hubSummary(brains, analytics), [brains, analytics]);
  const needs = useMemo(() => hubNeeds(brains, analytics), [brains, analytics]);
  const cards = useMemo(() => {
    const withSig = brains.map((b, i) => ({ b, sig: brainSignal(b, analytics), i }));
    const keep = withSig.filter(({ b, sig }) =>
      filter === 'all' || (filter === 'need' && sig.tone === 'error')
      || (filter === 'running' && b.health.liveRun) || (filter === 'born' && b.setupState === 'setting-up'));
    // things to do first; the self Brain leads its rank; otherwise the incoming order
    return keep.sort((x, y) => x.sig.rank - y.sig.rank
      || Number(y.b.name === selfName) - Number(x.b.name === selfName) || x.i - y.i);
  }, [brains, analytics, filter, selfName]);

  const openNewBrain = () => window.dispatchEvent(new CustomEvent('swarm:show-new-brain'));
  const openPipelines = () => window.dispatchEvent(new CustomEvent('swarm:show-pipeline'));

  return (
    <div className="p-4" data-testid="brainhub-gallery">
      <div className="flex items-center gap-3">
        <span className="text-heading font-semibold">Brain Hub</span>
        <span className="text-small text-[var(--color-text-muted)]">What each project knows, how it delivers, what it learned</span>
        <div className="ml-auto flex items-center gap-2 relative">
          <button className="btn-sm btn-secondary" onClick={() => setPicking((v) => !v)} aria-expanded={picking} data-testid="hub-add-knowledge">
            <span className="material-symbols-outlined icon-sm">add</span>Add knowledge
          </button>
          <button className="btn-sm btn-primary" onClick={openNewBrain} data-testid="hub-new-brain">New Brain</button>
          {picking && <div className="fixed inset-0 z-[9]" onClick={() => setPicking(false)} aria-hidden="true" />}
          {picking && (
            <div className="absolute right-0 top-full mt-1 z-10 w-64 max-h-80 overflow-auto rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] shadow-[var(--shadow-overlay)] py-1"
              role="menu" data-testid="hub-add-pick">
              <div className="px-3 py-1 text-meta text-[var(--color-text-muted)]">Add knowledge to which Brain?</div>
              {brains.map((b) => (
                <button key={b.name} role="menuitem" className="w-full text-left px-3 py-1.5 text-body hover:bg-[var(--color-hover)]"
                  data-testid={`hub-add-pick-${b.name}`}
                  onClick={() => { setPicking(false); onOpen(b.name, { view: 'map', panel: 'add' }); }}>
                  {b.name}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="mt-3 flex rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] overflow-hidden" role="radiogroup" aria-label="Show brains">
        <SumCell label="BRAINS" value={sum.brains} filter="all" active={filter === 'all'} onPick={setFilter} />
        <SumCell label="NEED YOU" value={sum.needsYou} tone={sum.needsYou ? 'text-[var(--color-status-error)]' : undefined}
          filter="need" active={filter === 'need'} onPick={setFilter} />
        <SumCell label="RUNNING" value={sum.running} tone={sum.running ? 'text-[var(--color-primary)]' : undefined}
          filter="running" active={filter === 'running'} onPick={setFilter} />
        <SumCell label="BEING BORN" value={sum.born} tone={sum.born ? 'text-[var(--color-status-warning)]' : undefined}
          filter="born" active={filter === 'born'} onPick={setFilter} />
        <div className="ml-auto flex">
          <SumCell label="LEARNED · 30 DAYS" value={sum.learned === null ? 'UNKNOWN' : `+${sum.learned}`}
            sub={sum.learned === null ? undefined : `entries, ${sum.learnedBrains} Brain${sum.learnedBrains === 1 ? '' : 's'}`} />
          <SumCell label="SINKING" value={sum.sinking} sub="dormant or archived entries" />
        </div>
      </div>

      {filter === 'need' && (
        <div className="rounded-b-lg border border-t-0 border-[var(--color-border)] bg-[var(--color-card)]" data-testid="hub-needs">
          {needs.length === 0 && <div className="px-4 py-2 text-small text-[var(--color-text-muted)]">Nothing is waiting on you.</div>}
          {needs.map((n) => (
            <div key={n.id} className="flex items-center gap-3 px-4 py-2 border-t border-[var(--color-border)] first:border-t-0 text-small" data-testid={`hub-need-${n.id}`}>
              <span className="font-semibold shrink-0">{n.brain}</span>
              <span className="flex-1 min-w-0 truncate">{n.text}</span>
              {n.since && <span className="text-meta text-[var(--color-text-muted)] shrink-0">{waited(n.since)}</span>}
              <button className="btn-sm btn-secondary shrink-0" data-testid={`hub-need-go-${n.id}`}
                onClick={() => onOpen(n.brain, n.kind === 'run' ? { view: 'map', panel: 'need' }
                  : n.kind === 'agent' ? { view: 'map', panel: 'agent' } : { view: 'review' })}>
                {n.kind === 'run' ? 'Answer' : n.kind === 'agent' ? 'Open' : 'Review'}
              </button>
            </div>
          ))}
        </div>
      )}

      <div className="mt-4 grid gap-2.5" style={{ gridTemplateColumns: 'repeat(3, minmax(0, 1fr))' }} data-testid="brainhub-card-wall">
        {cards.map(({ b, sig }) => (
          <div key={b.name} className="group relative rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] hover:border-[var(--color-primary)] focus-within:border-[var(--color-primary)]"
            data-testid={`hub-card-${b.name}`}>
            <button className="w-full text-left px-4 py-3.5 outline-none" onClick={() => onOpen(b.name)} aria-label={`Open ${b.name}`}
              data-testid={`dddcard-${b.name}`}>
              <div className="flex items-center gap-3">
                <Flower b={b} />
                <div className="min-w-0">
                  <div className="flex items-center gap-1.5 text-heading font-semibold">
                    <span className="truncate">{b.name}</span>
                    {b.name === selfName && (
                      <span className="text-meta font-semibold px-1.5 rounded-full text-[var(--color-status-success)] bg-[color-mix(in_srgb,var(--color-status-success)_14%,transparent)]"
                        data-testid="dddcard-self-tag">SELF</span>
                    )}
                  </div>
                  <div className={`text-small ${TONE_TEXT[sig.tone]}`} data-testid={`hub-signal-${b.name}`}>{sig.text}</div>
                </div>
              </div>
              <div className={`mt-2.5 text-small line-clamp-2 min-h-[2lh] ${b.description ? 'text-[var(--color-text-muted)]' : 'text-[var(--color-status-warning)]'}`}
                data-testid={`hub-desc-${b.name}`}>
                {b.description || 'No description yet. Add one line in aim.json.'}
              </div>
            </button>
            <button className="absolute top-2.5 right-3 text-meta text-[var(--color-primary)] opacity-0 group-hover:opacity-100 focus:opacity-100"
              data-testid={`hub-card-add-${b.name}`} onClick={() => onOpen(b.name, { view: 'map', panel: 'add' })}>
              Add knowledge
            </button>
          </div>
        ))}
        {cards.length === 0 && <div className="col-span-3 py-6 text-center text-small text-[var(--color-text-muted)]">No Brain matches this filter.</div>}
      </div>

      <div className="mt-4 pt-2.5 flex items-center justify-between border-t border-[var(--color-border)] text-meta text-[var(--color-text-muted)]">
        <button className="hover:text-[var(--color-text)]" onClick={openPipelines} data-testid="hub-conductor">
          Run Conductor · {sum.liveRuns} running in chat tabs · Local Worker and Hive are mocks ›
        </button>
        <span data-testid="hub-eval">
          Eval · last 30 days: {sum.finished ? `${sum.landed} of ${sum.finished} finished Runs landed` : 'no finished Runs'}
        </span>
      </div>
    </div>
  );
}
