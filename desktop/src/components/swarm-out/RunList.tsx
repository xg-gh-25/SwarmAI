/**
 * RunList — the Swarm Out run view's list: one row per run, what it asks of you, and a
 * click opens its big card (RunOverlay). Design
 * docs/design/2026-10-31-swarm-out-run-review.md §2.1.
 *
 * Three sections: Waiting for you (needs your answer > awaiting acceptance > stopped,
 * the longest waiting first; always on top), Running, Recently done (folded). A row is
 * its status dot, title, Brain · execution · Hive, the one autonomy word, what it asks
 * of you, how long it waited, an always-visible "Open session ↗", and "■ Stop" while it
 * runs. No accept button on a row (accepting without looking at what came back is not
 * accepting), no inline expansion, no search: sections, order, Brain chips (made from
 * the rows, with counts) and the card's J / K reach every run.
 */
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { swarmOutService, type SwarmOutAsk, type SwarmOutRun } from '../../services/swarmOut';
import { Dot } from './LiveDispatches';
import { agentFacts, brainLabel, stateLabel } from './live';
import { useOpenRun } from './openSession';
import { RunOverlay } from './RunOverlay';
import { actionOf, ago, autonomyWord, brainChips, sections, stageOf, taskTag, waitingSince } from './runReview';
import { runTone, TONE_VAR, type Tone } from './tone';

const muted = 'text-[var(--color-text-muted)]';
const RUNNING_SHOWN = 5;

function StopButton({ r, onDone }: { r: SwarmOutRun; onDone: () => void }) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  return (
    <button type="button" disabled={busy} data-testid="swarm-out-row-stop"
      className="btn-sm border disabled:opacity-50"
      style={{ color: TONE_VAR.error, borderColor: `color-mix(in srgb, ${TONE_VAR.error} 45%, var(--color-border))` }}
      title={failed ? t('swarmOut.review.stopFailed') : t('swarmOut.review.cmd.stop.does')}
      onClick={(e) => {
        e.stopPropagation();
        setBusy(true); setFailed(false);
        swarmOutService.stop(r.dispatch_id).then(onDone).catch(() => setFailed(true)).finally(() => setBusy(false));
      }}>■ {t('swarmOut.review.cmd.stop.name')}</button>
  );
}

function OpenButton({ r, compact }: { r: SwarmOutRun; compact: boolean }) {
  const { t } = useTranslation();
  const open = useOpenRun(r);
  if (!open) return null;
  return (
    <button type="button" data-testid="swarm-out-row-open"
      className="btn-sm border border-[var(--color-border)] text-[var(--color-text-secondary,var(--color-text-muted))] hover:bg-[var(--color-hover)]"
      title={t('swarmOut.hiveTab.open', { title: r.title })} aria-label={t('swarmOut.hiveTab.open', { title: r.title })}
      onClick={(e) => { e.stopPropagation(); open(); }}>{compact ? '↗' : `${t('swarmOut.review.cmd.open.name')} ↗`}</button>
  );
}

const TAG_STYLE: Record<'answer' | 'awaiting' | 'stopped', React.CSSProperties> = {
  answer: { background: TONE_VAR.attention, color: '#0b0d11' },
  awaiting: { color: TONE_VAR.attention, background: `color-mix(in srgb, ${TONE_VAR.attention} 13%, transparent)` },
  stopped: { color: TONE_VAR.error, background: `color-mix(in srgb, ${TONE_VAR.error} 13%, transparent)` },
};

function Row({ r, asks, now, onOpen, onReload, compact }: { r: SwarmOutRun; asks: SwarmOutAsk[]; now: number; onOpen: () => void; onReload: () => void; compact: boolean }) {
  const { t } = useTranslation();
  const stage = stageOf(r);
  const tone: Tone = stage === 'needs_answer' || stage === 'awaiting' ? 'attention' : stage === 'running' ? 'busy'
    : stage === 'accepted' ? 'ok' : stage === 'stopped' ? 'error' : runTone(r);
  const word = autonomyWord(r.result);
  const tag = taskTag(r);
  const facts = agentFacts(r);
  const meta = [facts.brain, facts.none ? null : facts.agent, r.hive, r.why ? t(`swarmOut.review.why.${r.why}`, { defaultValue: r.why }) : null]
    .filter(Boolean).join(' · ');
  const wordStyle: React.CSSProperties = word?.kind === 'full' || word?.state === 'PASS'
    ? { color: TONE_VAR.ok, borderColor: `color-mix(in srgb, ${TONE_VAR.ok} 45%, var(--color-border))` }
    : word ? { color: TONE_VAR.attention, borderColor: `color-mix(in srgb, ${TONE_VAR.attention} 45%, var(--color-border))` } : {};
  return (
    // keys on the row itself only: Enter on its Stop or Open button is that button's
    <div role="button" tabIndex={0} onClick={onOpen} aria-label={r.title}
      onKeyDown={(e) => { if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); onOpen(); } }}
      className="grid items-center gap-2.5 px-2.5 py-[7px] rounded-lg hover:bg-[var(--color-hover)] cursor-pointer min-w-0"
      style={{ gridTemplateColumns: '10px minmax(0,1fr) auto auto auto auto' }}
      data-testid="swarm-out-row" data-stage={stage} data-id={r.dispatch_id}>
      <Dot tone={tone} className={tone === 'busy' ? 'swarm-out-icon--busy' : ''} />
      <div className="min-w-0">
        <div className="truncate" title={r.title}>{r.title}</div>
        <div className={`text-[11.5px] truncate ${muted}`}>{meta}</div>
      </div>
      {word && !compact ? <span className="text-[11px] px-[7px] py-[1px] rounded-[5px] border whitespace-nowrap" style={wordStyle} data-testid="swarm-out-row-autonomy">{word.text}</span> : <span />}
      {tag ? <span className="text-[12px] px-[9px] py-[2px] rounded-md whitespace-nowrap font-semibold" style={TAG_STYLE[tag.key]} data-testid="swarm-out-row-tag">{tag.text}</span>
        : r.cleanup_soon ? <span className="text-[12px] whitespace-nowrap" style={{ color: TONE_VAR.attention }} data-testid="swarm-out-row-cleanup">{t('swarmOut.review.cleanupSoon')}</span>
        : stage === 'running' ? <span className="text-[12px] whitespace-nowrap" style={{ color: TONE_VAR.busy }}>{stateLabel(r)}</span>
          : <span className={`text-[12px] ${muted}`}>{t(`swarmOut.review.stage.${stage}`)}</span>}
      <span className="flex items-center gap-1.5">
        {stage === 'running' && actionOf(r, 'stop') && <StopButton r={r} onDone={onReload} />}
        {actionOf(r, 'open_session') && <OpenButton r={r} compact={compact} />}
      </span>
      <span className={`text-[11.5px] w-[40px] text-right tabular-nums ${muted}`}>{ago(waitingSince(r, asks), now)}</span>
    </div>
  );
}

export function RunList({ runs, asks, answered = [], now, onReload, compact = false }: {
  runs: SwarmOutRun[]; asks: SwarmOutAsk[];
  /** the waits answered from here (the big card shows each as one line) */
  answered?: SwarmOutAsk[];
  now: number; onReload: () => void;
  /** the dashboard is narrow: rows drop the autonomy word and shorten Open session */
  compact?: boolean;
}) {
  const { t } = useTranslation();
  const [brain, setBrain] = useState<string | null>(null);
  const [showDone, setShowDone] = useState(false);
  const [allRunning, setAllRunning] = useState(false);
  // the card: which run, and the order of "waiting for you" frozen when it opened
  const [open, setOpen] = useState<{ id: string; order: string[] } | null>(null);
  const chips = useMemo(() => brainChips(runs), [runs]);
  const shown = brain ? runs.filter((r) => brainLabel(r) === brain) : runs;
  const s = sections(shown, asks, now);
  const openRun = (id: string) => setOpen({ id, order: s.you.map((r) => r.dispatch_id) });
  const running = allRunning ? s.running : s.running.slice(0, RUNNING_SHOWN);
  const section = (key: string, title: string, n: number, accent?: string) => (
    <div className="flex items-baseline gap-2 mt-4 mb-1.5" data-testid={`swarm-out-section-${key}`}>
      <h3 className="m-0 text-[13px] font-semibold" style={accent ? { color: accent } : undefined}>{title}</h3>
      <span className={`text-[12px] tabular-nums ${muted}`}>{n}</span>
    </div>
  );
  const row = (r: SwarmOutRun) => <Row key={r.dispatch_id} r={r} asks={asks} now={now} onOpen={() => openRun(r.dispatch_id)} onReload={onReload} compact={compact} />;
  return (
    <div data-testid="swarm-out-run-list">
      {chips.length > 1 && (
        <div className="flex flex-wrap gap-1.5 mt-4" data-testid="swarm-out-brain-chips">
          {[{ brain: null as string | null, count: runs.length }, ...chips].map((c) => (
            <button key={c.brain ?? '*'} type="button" aria-pressed={brain === c.brain} onClick={() => setBrain(c.brain)}
              className={`px-2.5 py-[2px] rounded-full border text-[12px] ${brain === c.brain ? 'border-[var(--color-text-muted)] text-[var(--color-text)]' : `border-[var(--color-border)] ${muted}`}`}
              data-testid="swarm-out-brain-chip">
              {c.brain ?? t('swarmOut.review.all')}<span className="ml-1 text-[var(--color-text-dim)]">{c.count}</span>
            </button>
          ))}
        </div>
      )}
      {section('you', t('swarmOut.review.section.you'), s.you.length, s.you.length ? TONE_VAR.attention : undefined)}
      {s.you.length === 0 ? <div className={`px-2.5 text-[12.5px] ${muted}`}>{t('swarmOut.waiting.none')}</div>
        : <div className="flex flex-col">{s.you.map(row)}</div>}
      {section('running', t('swarmOut.review.section.running'), s.running.length)}
      {s.running.length === 0 ? <div className={`px-2.5 text-[12.5px] ${muted}`}>{t('swarmOut.groups.runningEmpty')}</div>
        : <div className="flex flex-col">{running.map(row)}</div>}
      {s.running.length > running.length && (
        <button type="button" className="px-2.5 py-1.5 text-[12px] text-[var(--color-primary)]" onClick={() => setAllRunning(true)}
          data-testid="swarm-out-running-more">{t('swarmOut.review.moreRunning', { count: s.running.length - running.length })} ›</button>
      )}
      <button type="button" className="flex items-baseline gap-2 mt-4 mb-1.5" aria-expanded={showDone} onClick={() => setShowDone((v) => !v)}
        data-testid="swarm-out-section-done">
        <span className="text-[13px] font-semibold">{t('swarmOut.review.section.done')}</span>
        <span className={`text-[12px] tabular-nums ${muted}`}>{s.done.length}</span>
        <span className={`text-[12px] ${muted}`}>{showDone ? t('swarmOut.review.fold') : t('swarmOut.review.unfold')}</span>
      </button>
      {showDone && <div className="flex flex-col">{s.done.map(row)}</div>}
      {open && (
        <RunOverlay runs={runs} asks={asks} answered={answered} order={open.order} id={open.id} now={now}
          onSelect={(id) => setOpen((o) => (o ? { ...o, id } : o))}
          onSettled={(id) => setOpen((o) => (o ? { ...o, order: o.order.filter((x) => x !== id) } : o))}
          onClose={() => setOpen(null)} onReload={onReload} />
      )}
    </div>
  );
}
