/**
 * The decision funnel on a run's big card (design
 * docs/design/2026-11-02-hive-run-decision-record.md §3.1; mock
 * Knowledge/Mockups/2026-11-02-hive-run-decision-funnel.html).
 *
 * The card answers one question: where is this run in the funnel, and is anything mine?
 *  - ``FunnelLine``: one line under the title, the gates passed and the one the run is at
 *    (review → land on this host → judge), each with who and when; the landed commit or
 *    Brain file. Gates not reached are not listed. Amber only for "waiting on you".
 *  - ``AskPersonCard``: the landed result the dispatching agent handed to the person:
 *    the question, where it landed, the agent's judgment, why it could not decide, what
 *    revert does. The agent's own words, never the Hive's.
 *  - ``DecisionsList``: every decision on the run, grouped by gate as they happened
 *    (a re-run starts the review again); one row each, the facts the host saw on click.
 *
 * Everything shown comes from the backend (core/swarm_out_record); nothing is decided
 * here.
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { SwarmOutAskPerson, SwarmOutDecision, SwarmOutGate, SwarmOutRun } from '../../services/swarmOut';
import { TONE_VAR } from './tone';

const muted = 'text-[var(--color-text-muted)]';

const hhmm = (iso: string | null | undefined): string => {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : d.toTimeString().slice(0, 5);
};
const short = (ref: string | null): string | null => {
  if (!ref) return null;
  return /^[0-9a-f]{12,}$/.test(ref) ? ref.slice(0, 7) : ref.split('/').slice(-2).join('/');
};

function Gate({ g, last }: { g: SwarmOutGate; last: boolean }) {
  const { t } = useTranslation();
  const color = g.state === 'person' ? TONE_VAR.attention : g.state === 'now' ? TONE_VAR.busy : TONE_VAR.ok;
  const mark = g.state === 'done' ? '✓' : '●';
  const who = g.by ? t(`swarmOut.funnel.by.${g.by}`) : null;
  const ref = short(g.ref);
  return (
    <>
      <span className="inline-flex items-center gap-1.5 whitespace-nowrap" data-testid="swarm-out-funnel-gate"
        data-gate={g.key} data-state={g.state}>
        <span className="font-semibold" style={{ color }}>{mark} {t(`swarmOut.funnel.gate.${g.key}`)}</span>
        {g.state === 'person'
          ? <span className="font-semibold" style={{ color }}>{t('swarmOut.funnel.yours')}</span>
          : <span className={muted}>{[who, ref, hhmm(g.at)].filter(Boolean).join(' · ')}</span>}
      </span>
      {!last && <span className="text-[var(--color-text-dim)]">→</span>}
    </>
  );
}

/** The gates under the title; nothing while the run is on its Hive. */
export function FunnelLine({ r }: { r: SwarmOutRun }) {
  const { t } = useTranslation();
  const f = r.funnel;
  if (!f || f.gates.length === 0) return null;
  return (
    <div className="mt-1.5 flex flex-wrap items-center gap-x-1.5 gap-y-1 text-[12px] text-[var(--color-text-secondary)]"
      data-testid="swarm-out-funnel">
      {f.gates.map((g, i) => <Gate key={`${g.key}-${i}`} g={g} last={i === f.gates.length - 1} />)}
      {f.reruns > 0 && <span className={muted}>· {t('swarmOut.funnel.reruns', { count: f.reruns })}</span>}
    </div>
  );
}

/** What the person decides on a landed result the agent could not judge. */
export function AskPersonCard({ ask }: { ask: SwarmOutAskPerson }) {
  const { t } = useTranslation();
  const where = [...(ask.landed?.commits ?? []).map((c) => c.slice(0, 7)), ...(ask.landed?.files ?? [])].join(', ');
  const rows: [string, string][] = [
    ['where', where || t('swarmOut.funnel.ask.onHost')],
    ['judgment', ask.judgment],
    ['why', ask.why],
    ['revert', t(ask.landed?.commits?.length ? 'swarmOut.funnel.ask.revertCommits' : 'swarmOut.funnel.ask.revertFiles')],
  ];
  return (
    <div className="mx-4 mt-3 rounded-[10px] border px-3.5 py-3" data-testid="swarm-out-ask-card"
      style={{ borderColor: `color-mix(in srgb, ${TONE_VAR.attention} 55%, var(--color-border))`,
        background: `color-mix(in srgb, ${TONE_VAR.attention} 7%, var(--color-card))` }}>
      <div className="text-[12px] font-bold" style={{ color: TONE_VAR.attention }}>{t('swarmOut.funnel.ask.title')}</div>
      <div className="text-[14px] font-semibold mt-1 mb-2.5 whitespace-pre-wrap break-words">{ask.decide}</div>
      <dl className="grid gap-x-3 gap-y-1 text-[12.5px] m-0" style={{ gridTemplateColumns: '96px minmax(0,1fr)' }}>
        {rows.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className={muted}>{t(`swarmOut.funnel.ask.${k}`)}</dt>
            <dd className="m-0 break-words" data-testid={`swarm-out-ask-${k}`}>{v}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

/** Which gate a decision belongs to. */
function gateOf(d: string): 'run' | 'review' | 'land' | 'judge' {
  if (d === 'review') return 'review';
  if (d === 'land' || d === 'place_files') return 'land';
  if (['accept', 'ask_person', 'keep', 'revert', 'reverted', 'take_over', 'discard', 'settle'].includes(d)) return 'judge';
  return 'run';
}

function Row({ e, open, onToggle }: { e: SwarmOutDecision; open: boolean; onToggle: () => void }) {
  const { t } = useTranslation();
  const toPerson = e.decision === 'ask_person';
  const facts = e.observed && Object.keys(e.observed).length > 0 ? e.observed : null;
  const why = e.why || (e.pick ? `→ ${e.pick}` : '');
  return (
    <div className={`grid gap-x-2.5 py-1 text-[12.5px] items-baseline ${facts ? 'cursor-pointer' : ''}`}
      style={{ gridTemplateColumns: '44px 52px 92px minmax(0,1fr)' }}
      onClick={facts ? onToggle : undefined} data-testid="swarm-out-decision" data-decision={e.decision} data-by={e.by}>
      <span className="text-[var(--color-text-dim)] tabular-nums">{hhmm(e.at)}</span>
      <span className="text-[11px]" style={{ color: e.by === 'person' ? TONE_VAR.attention : undefined }}>
        {t(`swarmOut.funnel.by.${e.by}`)}</span>
      <span className="font-semibold" style={toPerson ? { color: TONE_VAR.attention } : undefined}>
        {t(`swarmOut.funnel.decision.${e.decision}`, { defaultValue: e.decision })}</span>
      <span className="text-[var(--color-text-secondary)] break-words">{why}{facts ? (open ? ' ▾' : ' ▸') : ''}</span>
      {open && facts && (
        <span className="font-mono text-[11px] text-[var(--color-text-muted)] whitespace-pre-wrap break-words"
          style={{ gridColumn: 4 }} data-testid="swarm-out-decision-observed">{JSON.stringify(facts, null, 1)}</span>
      )}
    </div>
  );
}

/** Every decision on the run, grouped by gate in the order they happened. */
export function DecisionsList({ decisions }: { decisions: SwarmOutDecision[] }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState<number | null>(null);
  if (decisions.length === 0) return <div className={`text-[12.5px] ${muted}`}>{t('swarmOut.funnel.none')}</div>;
  const groups: { gate: string; rows: { e: SwarmOutDecision; i: number }[] }[] = [];
  decisions.forEach((e, i) => {
    const g = gateOf(e.decision);
    if (!groups.length || groups[groups.length - 1].gate !== g) groups.push({ gate: g, rows: [] });
    groups[groups.length - 1].rows.push({ e, i });
  });
  return (
    <div className="flex flex-col gap-4" data-testid="swarm-out-decisions">
      {groups.map((g, gi) => (
        <div key={gi} data-testid="swarm-out-decision-group" data-gate={g.gate}>
          <div className={`text-[12px] font-semibold mb-1 ${muted}`}>{t(`swarmOut.funnel.group.${g.gate}`)}</div>
          {g.rows.map(({ e, i }) => <Row key={i} e={e} open={open === i} onToggle={() => setOpen(open === i ? null : i)} />)}
        </div>
      ))}
    </div>
  );
}
