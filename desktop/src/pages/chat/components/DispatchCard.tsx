/**
 * DispatchCard — a Swarm Out dispatch in the chat (design
 * docs/design/2026-10-30-swarm-out-dispatch-card.md §3): what went to a Hive, or why
 * nothing did. The block is the backend's (core/swarm_out_card), built from this
 * machine's dispatch log row, never from model text; it renders in any chat tab.
 *
 * The ItemBox look of the turn cards (ItemRows): the status in the title line, the
 * title and brief, one row per field (name · value · a grey "why"), the actions in the
 * last line. Three variants:
 * - gone out: the live state of the run (Swarm Out's runs poll, shared by every card on
 *   screen), "View in Swarm Out" and "Open Hive session";
 * - refused: why, and that nothing was sent; "Open <Brain>" when it names one;
 * - not sure (the Hive did not name the session in time): it may be running there.
 *
 * Text a Hive or the backend wrote (title, brief, error, acceptance) is data and is
 * never translated; the labels come from chat.swarmOut.dispatch.* in the locales.
 *
 * @exports DispatchCard, useDispatchedRun, hiveReasonLabel, syncLabel, liveWord
 */
import { useSyncExternalStore, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import type { SwarmOutDispatchContent } from '../../../types';
import { swarmOutService, type SwarmOutRun, type SwarmOutRuns } from '../../../services/swarmOut';
import { isRemoteId } from '../../../services/remoteSession';
import { getOverlayCtxBridge } from '../../../components/layout/overlayRegistry';
import { openOverlayAt } from '../../../utils/overlayTarget';
import { dotStyle, runTone, type Tone } from '../../../components/swarm-out/tone';
import { tf } from '../tf';

export const DISPATCH_POLL_MS = 30_000;

// ── one runs poll for every card on screen ──────────────────────────────────────

let latest: SwarmOutRuns | null = null;
let timer: number | null = null;
const listeners = new Set<() => void>();

function load(): void {
  swarmOutService.runs().then((d) => {
    latest = d;
    listeners.forEach((l) => l());
  }).catch(() => { /* keep the last answer: an unreachable daemon is not "nothing" */ });
}

function subscribe(l: () => void): () => void {
  listeners.add(l);
  if (listeners.size === 1) {
    load();
    timer = window.setInterval(load, DISPATCH_POLL_MS);
  }
  return () => {
    listeners.delete(l);
    if (listeners.size === 0 && timer != null) {
      window.clearInterval(timer);
      timer = null;
    }
  };
}

const noop = () => () => {};
const none = () => null;

/** The run a dispatch started, from the shared poll; null until it is listed (or when
 *  there is no dispatch id: nothing is polled then). */
export function useDispatchedRun(dispatchId: string | null): SwarmOutRun | null {
  const runs = useSyncExternalStore(dispatchId ? subscribe : noop, dispatchId ? () => latest : none);
  if (!dispatchId || !runs) return null;
  return runs.runs.find((r) => r.dispatch_id === dispatchId) ?? null;
}

/** Test hook: forget the shared poll's answer. */
export function _resetDispatchPoll(): void {
  latest = null;
}

// ── words ──────────────────────────────────────────────────────────────────────

/** Why this Hive (backend swarm_out.pick_hive's code). */
export function hiveReasonLabel(t: TFunction, code: string | null): string | null {
  if (!code) return null;
  const busy = /^least_busy:(\d+)$/.exec(code);
  if (busy) return tf(t, 'chat.swarmOut.dispatch.reason.least_busy', 'the least busy ({{n}} running)', { n: busy[1] });
  if (code === 'only_free' || code === 'idle' || code === 'named') {
    return tf(t, `chat.swarmOut.dispatch.reason.${code}`, code);
  }
  return code;
}

/** What the Brain sync did (backend dispatch_spec: up to date / synced / restored …). */
export function syncLabel(t: TFunction, s: string | null): string | null {
  if (!s) return null;
  if (s === 'up to date') return tf(t, 'chat.swarmOut.dispatch.sync.upToDate', 'Brain up to date');
  if (s === 'synced') return tf(t, 'chat.swarmOut.dispatch.sync.synced', 'Brain synced');
  if (s.startsWith('restored')) return tf(t, 'chat.swarmOut.dispatch.sync.restored', 'Brain restored on the Hive');
  return s;
}

/** The run's state in the Swarm Out cards' own words (swarmOut.state / swarmOut.ended),
 *  short: the card links to Swarm Out for the rest. Not live.ts's stateLabel: that module
 *  binds the app's i18n instance at import, and a chat message must not. */
export function liveWord(t: TFunction, r: SwarmOutRun): string {
  const state = r.state ?? (r.waiting ? 'waiting' : r.running === true ? 'running' : r.running === false ? 'stopped' : 'unknown');
  if (state === 'stopped' && r.result?.ended) {
    return tf(t, `swarmOut.ended.${r.result.ended}`, r.result.ended)
      + (r.result.provisional && r.result.ended !== 'no_record' ? tf(t, 'swarmOut.ended.provisional', '') : '');
  }
  if (state === 'unknown' && r.session_unknown) return tf(t, 'swarmOut.state.sessionUnknown', 'session not named');
  return tf(t, `swarmOut.state.${state}`, state);
}

function brainName(t: TFunction, b: string | null): string {
  if (!b) return '—';
  return b === 'General' ? tf(t, 'swarmOut.brain.general', 'General') : b;
}

function clock(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

// ── layout ─────────────────────────────────────────────────────────────────────

const BOX = 'w-full rounded-[10px] border border-[var(--color-border)] bg-[var(--color-card)]';
const BAR = 'flex items-baseline gap-2 px-3 py-1.5 border-b border-[var(--color-border)] text-[11px] font-semibold uppercase tracking-[0.06em] text-[var(--color-text-dim)]';
const TONE_TEXT: Record<Tone, string> = {
  ok: 'text-[var(--color-status-success)]', busy: 'text-[var(--color-primary)]',
  attention: 'text-[var(--color-status-warning)]', error: 'text-[var(--color-status-error)]',
  unknown: 'text-[var(--color-text-dim)]',
};

function Row({ k, children, why, testId }: { k: string; children: ReactNode; why?: string | null; testId: string }) {
  return (
    <li data-testid={testId}
      className="grid grid-cols-[128px_minmax(0,1fr)_auto] items-baseline gap-4 px-3.5 py-[7px]">
      <span className="text-[12.5px] text-[var(--color-text-muted)]">{k}</span>
      <span className="min-w-0 text-[13px] text-[var(--color-text)] break-words">{children}</span>
      <span className="whitespace-nowrap text-[12px] text-[var(--color-text-dim)]">{why ?? ''}</span>
    </li>
  );
}

const Chip = ({ children }: { children: ReactNode }) => (
  <span className="mr-1.5 inline-block rounded-[5px] bg-[var(--color-hover)] px-2 text-[12px] text-[var(--color-text-secondary)]">{children}</span>
);
const Mono = ({ children }: { children: ReactNode }) => <span className="font-mono text-[12.5px]">{children}</span>;

export function DispatchCard({ block }: { block: SwarmOutDispatchContent }) {
  const { t } = useTranslation();
  const k = (key: string, fallback: string, vars?: Record<string, string | number>) =>
    tf(t, `chat.swarmOut.dispatch.${key}`, fallback, vars);
  const run = useDispatchedRun(block.ok ? block.dispatchId : null);
  const bridge = getOverlayCtxBridge();
  const unsure = !block.ok && block.maybeSent === true;

  const status = block.ok
    ? <span className={TONE_TEXT.ok}>✓ {k('ok', 'Dispatched to a Hive')}</span>
    : unsure
      ? <span className={TONE_TEXT.attention}>? {k('unsure', 'Not sure it was dispatched')}</span>
      : <span className={TONE_TEXT.error}>✕ {k('failed', 'Not dispatched')}</span>;
  let live: ReactNode = null;
  if (run) {
    const tone = runTone(run);
    const label = liveWord(t, run);
    live = (
      <span data-testid="dispatch-live" data-tone={tone} className={`inline-flex items-center gap-1.5 ${TONE_TEXT[tone]}`}>
        <span aria-hidden="true" className="h-[7px] w-[7px] rounded-full" style={dotStyle(tone)} />{label}
      </span>
    );
  }

  const canOpen = block.ok && isRemoteId(block.sessionId) && !!bridge.openHiveSession;
  const open = () => bridge.openHiveSession?.(block.sessionId as string, `${block.title} · ${block.hive ?? ''}`);
  // the mode of the Project Agent; how this task runs (and its profile) is its own row
  const agent = [block.mode].filter((x): x is string => !!x);

  return (
    <div data-testid="dispatch-card" data-ok={String(block.ok)} data-unsure={String(unsure)} className={BOX}>
      <div data-testid="dispatch-status" className={BAR}>
        <span className="whitespace-nowrap">{status}</span>
        <span className="ml-auto flex items-center gap-3.5 pl-3 whitespace-nowrap normal-case tracking-normal font-normal">
          {live}<span>{clock(block.at)}</span>
        </span>
      </div>
      {!block.ok && (
        <div data-testid="dispatch-error"
          className="border-b border-[var(--color-border)] px-3.5 py-2.5 bg-[color-mix(in_srgb,var(--color-status-error)_7%,transparent)]">
          <div className="text-[13px] text-[var(--color-text)] break-words">{block.error}</div>
          <div className="mt-0.5 text-[12.5px] text-[var(--color-text-secondary)]">
            {unsure ? k('maybeRunning', 'It may already be running on the Hive: look in Swarm Out before sending it again.')
              : k('nothingSent', 'Nothing was sent to a Hive.')}
          </div>
        </div>
      )}
      {(block.title || block.brief) && (
        <div className={`px-3.5 pt-3 ${block.ok ? 'pb-3 border-b border-[var(--color-border)]' : 'pb-1.5'}`}>
          <div data-testid="dispatch-title" className="text-[15px] font-semibold text-[var(--color-text)] break-words">{block.title}</div>
          {block.brief && <div data-testid="dispatch-brief" className="mt-[3px] text-[13.5px] text-[var(--color-text-secondary)] break-words">{block.brief}</div>}
        </div>
      )}
      <ul className="flex flex-col divide-y divide-[var(--color-border)]">
        {block.hive && (
          <Row testId="dispatch-row-hive" k={k('row.hive', 'Hive')} why={hiveReasonLabel(t, block.hiveReason)}>{block.hive}</Row>
        )}
        {block.brain && <Row testId="dispatch-row-brain" k={k('row.brain', 'Brain')}>{brainName(t, block.brain)}</Row>}
        {block.ok && (agent.length > 0 || block.revision) && (
          <Row testId="dispatch-row-agent" k={tf(t, 'swarmOut.card.agentDefault', 'Project Agent')} why={syncLabel(t, block.brainSync)}>
            {agent.map((a) => <Chip key={a}>{a}</Chip>)}
            {block.revision && <span className="text-[var(--color-text-muted)]">{k('revision', 'definition')} <Mono>{block.revision.slice(0, 8)}</Mono></span>}
          </Row>
        )}
        {block.ok && block.execution && (
          <Row testId="dispatch-row-execution" k={k('row.execution', 'Runs')} why={block.executionWhy}>
            {block.execution === 'native' ? k('native', 'native: in the session, no pipeline run')
              : k('pipeline', 'pipeline · {{profile}}', { profile: block.profile ?? '?' })}
          </Row>
        )}
        {block.ok && block.repos.map((r) => (
          <Row key={`${r.name}@${r.ref}`} testId="dispatch-row-repo" k={k('row.repo', 'Repository')} why={k('commit', "this machine's commit")}>
            {r.name} @ {r.ref ?? '?'} {r.sha && <Mono>{r.sha.slice(0, 8)}</Mono>}
            {r.dirty > 0 && (
              <div data-testid="dispatch-dirty" className="mt-0.5 text-[12.5px] text-[var(--color-status-warning)]">
                ⚠ {k('dirty', '{{count}} uncommitted files here were not sent', { count: r.dirty })}
              </div>
            )}
          </Row>
        ))}
        {block.ok && block.acceptance && <Row testId="dispatch-row-acceptance" k={k('row.acceptance', 'Done when')}>{block.acceptance}</Row>}
        {block.ok && block.delivery && (
          <Row testId="dispatch-row-delivery" k={k('row.delivery', 'Delivery')}>
            {block.delivery === 'bundle' || block.delivery === 'branch-pr' ? k(`delivery.${block.delivery}`, block.delivery) : block.delivery}
          </Row>
        )}
      </ul>
      <div className="flex items-center gap-3 border-t border-[var(--color-border)] px-3.5 py-[9px]">
        <span className="text-[12.5px] text-[var(--color-text-muted)]">
          {block.dispatchId ? <>{k('id', 'Dispatch')} <Mono>{block.dispatchId}</Mono></>
            : !block.ok && !unsure ? k('retryHint', 'Once it is fixed, say "send it again"') : null}
        </span>
        <span className="flex-1" />
        {(block.ok || unsure) && bridge.showSwarmOut && (
          <button type="button" data-testid="dispatch-view" className="btn-sm btn-secondary" onClick={() => bridge.showSwarmOut?.()}>
            {k('view', 'View in Swarm Out')}
          </button>
        )}
        {canOpen && (
          <button type="button" data-testid="dispatch-open" className="btn-sm btn-primary" onClick={open}>
            ↗ {k('open', 'Open Hive session')}
          </button>
        )}
        {!block.ok && !unsure && block.brain && (
          <button type="button" data-testid="dispatch-open-brain" className="btn-sm btn-primary"
            onClick={() => openOverlayAt('brain-hub', block.brain as string)}>
            {k('openBrain', 'Open {{brain}}', { brain: brainName(t, block.brain) })}
          </button>
        )}
      </div>
    </div>
  );
}
