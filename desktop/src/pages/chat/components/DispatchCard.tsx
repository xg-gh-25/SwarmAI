/**
 * DispatchCard — a Swarm Out dispatch in the chat (design
 * docs/design/2026-10-30-swarm-out-dispatch-card.md §3): what went to a Hive, or why
 * nothing did. The block is the backend's (core/swarm_out_card), built from this
 * machine's dispatch log row, never from model text; it renders in any chat tab.
 *
 * The ItemBox look of the turn cards (ItemRows): the status in the title line, the
 * title and brief, one row per field (name · value · a grey "why"), the actions in the
 * last line. Three variants:
 * - gone out: the run's status as the Swarm Out panel shows it (its stage, from Swarm
 *   Out's one shared runs poll, components/swarm-out/runsPoll), "View in Canvas" (the
 *   dispatching tab's Canvas Runs tab), "View in Swarm Out" and "Open Hive session";
 * - refused: why, and that nothing was sent; "Open <Brain>" when it names one;
 * - not sure (the Hive may have taken it but named no session): it may be running there.
 *
 * Text a Hive or the backend wrote (title, brief, error, acceptance) is data and is
 * never translated; the labels come from chat.swarmOut.dispatch.* in the locales.
 *
 * @exports DispatchCard, useDispatchedRun, useRunsPoll, reloadRunsPoll, hiveReasonLabel, syncLabel, liveWord
 */
import { useContext, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import type { SwarmOutDispatchContent } from '../../../types';
import type { SwarmOutRun, SwarmOutRuns } from '../../../services/swarmOut';
import { POLL_MS, _resetSharedPolls, reloadRuns, useSharedRuns } from '../../../components/swarm-out/runsPoll';
import { isRemoteId } from '../../../services/remoteSession';
import { getOverlayCtxBridge } from '../../../components/layout/overlayRegistry';
import { openOverlayAt } from '../../../utils/overlayTarget';
import { dotStyle, runTone, type Tone } from '../../../components/swarm-out/tone';
import { STAGE_TONE_KIND, stageOf, stageWord } from '../../../components/swarm-out/stage';
import { tf } from '../tf';
import { openCanvasRuns } from '../../../utils/canvasRuns';
import { ReadOnlyContext } from '../readOnlyContext';

export const DISPATCH_POLL_MS = POLL_MS;

// ── the runs: Swarm Out's one shared poll (components/swarm-out/runsPoll) ────────

/** The run a dispatch started, from the shared poll; null until it is listed (or when
 *  there is no dispatch id: the card does not keep the poll going then). */
export function useDispatchedRun(dispatchId: string | null): SwarmOutRun | null {
  const runs = useSharedRuns(!!dispatchId).data;
  if (!dispatchId || !runs) return null;
  return runs.runs.find((r) => r.dispatch_id === dispatchId) ?? null;
}

/** Every run, from the same shared poll (the Swarm Run card reads its children from it);
 *  null until the first answer. */
export function useRunsPoll(): SwarmOutRuns | null {
  return useSharedRuns(true).data;
}

/** Ask the shared poll again now (after a command). */
export function reloadRunsPoll(): void {
  reloadRuns();
}

/** Test hook: forget the shared poll's answer. */
export function _resetDispatchPoll(): void {
  _resetSharedPolls();
}

// ── words ──────────────────────────────────────────────────────────────────────

/** Why this Hive (backend swarm_out.pick_hive's code). */
export function hiveReasonLabel(t: TFunction, code: string | null): string | null {
  if (!code) return null;
  const busy = /^least_busy:(\d+)$/.exec(code);
  if (busy) return tf(t, 'chat.swarmOut.dispatch.reason.least_busy', 'the least busy ({{n}} running)', { n: busy[1] });
  if (code === 'only_free' || code === 'idle' || code === 'named' || code === 'started') {
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

/** The run's status as the Swarm Out panel shows it: the backend's stage and why
 *  (core/swarm_out_settle derive) through components/swarm-out/stage, the one source the
 *  panel's pill reads too, so a discarded run reads Discarded here as well. A run the
 *  backend gives no stage (a dispatch without a Project Agent) is worded from its Hive
 *  state and its result's ending, as before. Short: the card links to Swarm Out for the
 *  rest. Worded with this card's own ``t``: the panel's modules that bind the app's i18n
 *  instance at import are not loaded by a chat message. */
export function liveWord(t: TFunction, r: SwarmOutRun): string {
  if (r.stage) return stageWord(t, r.stage, r.why);
  const { state } = r;
  if (state === 'stopped' && r.result?.ended) {
    return tf(t, `swarmOut.ended.${r.result.ended}`, r.result.ended)
      + (r.result.provisional && r.result.ended !== 'no_record' ? tf(t, 'swarmOut.ended.provisional', '') : '');
  }
  if (state === 'unknown' && r.session_unknown) return tf(t, 'swarmOut.state.sessionUnknown', 'session not named');
  if (state === 'unknown' && r.state_reason === 'hive_updating') return tf(t, 'swarmOut.state.unknownUpdating', 'the Hive is updating');
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

export function DispatchCard({ block, sessionId }: {
  block: SwarmOutDispatchContent;
  /** the session of the chat the card is in: "View in Canvas" shows only when it
   *  dispatched the run (that tab's Canvas Runs lists it) */
  sessionId?: string;
}) {
  const { t } = useTranslation();
  const k = (key: string, fallback: string, vars?: Record<string, string | number>) =>
    tf(t, `chat.swarmOut.dispatch.${key}`, fallback, vars);
  const run = useDispatchedRun(block.ok ? block.dispatchId : null);
  const readOnly = useContext(ReadOnlyContext);
  const bridge = getOverlayCtxBridge();
  const unsure = !block.ok && block.maybeSent === true;
  // refused at intake (dispatch-center §2.3): nothing was asked of a Hive
  const refused = !block.ok && !unsure && block.refused === true;
  const doHere = () => bridge.dispatchPrompt?.(k('doHerePrompt',
    'Do this on this machine (Swarm Out refused it: {{why}}). {{title}}\n\n{{task}}',
    { why: block.error ?? '', title: block.title, task: block.task ?? '' }));

  const status = block.ok
    ? <span className={TONE_TEXT.ok}>✓ {k('ok', 'Dispatched to a Hive')}</span>
    : unsure
      ? <span className={TONE_TEXT.attention}>? {k('unsure', 'Not sure it was dispatched')}</span>
      : refused
        ? <span className={TONE_TEXT.error}>✕ {k('refused', 'Refused')}</span>
        : <span className={TONE_TEXT.error}>✕ {k('failed', 'Not dispatched')}</span>;
  let live: ReactNode = null;
  if (run) {
    const stage = run.stage ? stageOf(run) : null;
    const tone = stage ? STAGE_TONE_KIND[stage] : runTone(run);
    const label = liveWord(t, run);
    live = (
      <span data-testid="dispatch-live" data-tone={tone} data-stage={stage ?? undefined} className={`inline-flex items-center gap-1.5 ${TONE_TEXT[tone]}`}>
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
              : refused ? k('refusedNothing', 'Nothing was sent: no Hive was contacted and the Brain was not synced.')
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
          <Row testId="dispatch-row-execution" k={k('row.execution', 'Runs')}>
            {block.execution === 'native' ? k('native', 'native: in the session, no pipeline run')
              : k('pipeline', 'pipeline · {{profile}}', { profile: block.profile ?? '?' })}
            {/* the reason is a sentence: it wraps under the value, not in the one-line column */}
            {block.executionWhy && (
              <span data-testid="dispatch-execution-why" className="block text-[12px] text-[var(--color-text-dim)]">
                {block.executionWhy}
              </span>
            )}
            {/* the run did not run the way it was dispatched to (backend execution_check) */}
            {/* (worded here with this card's own t: components/swarm-out/live loads the
                locale module itself, which every test that mocks react-i18next refuses) */}
            {run?.result?.execution_check?.state === 'differs' && run.result.execution_check.why && (
              <span data-testid="dispatch-execution-differs" className={`block text-[12.5px] ${TONE_TEXT.attention}`}>
                ⚠ {t('swarmOut.execution.differs', { why: t(`swarmOut.execution.${run.result.execution_check.why}`) })}
              </span>
            )}
          </Row>
        )}
        {block.ok && block.repos.map((r) => (
          <Row key={`${r.name}@${r.ref}`} testId="dispatch-row-repo" k={k('row.repo', 'Repository')} why={k('commit', "this machine's commit")}>
            {r.name} @ {r.ref ?? '?'} {r.sha && <Mono>{r.sha.slice(0, 8)}</Mono>}
            {r.access === 'read' && (
              <span data-testid="dispatch-repo-readonly" className="ml-1.5 text-[12.5px] text-[var(--color-text-muted)]">
                · {k('readOnly', 'read-only')}
              </span>
            )}
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
            : refused && block.needsPrivate ? (block.task ? k('doHereHint', 'It can be done on this machine')
              : k('doHereAsk', 'It can be done on this machine: say "do it here"'))
              : !block.ok && !unsure ? k('retryHint', 'Once it is fixed, say "send it again"') : null}
        </span>
        <span className="flex-1" />
        {/* the run is listed and this chat's session dispatched it: this tab's Canvas
            Runs tab shows it with the session's others (not in a read-only preview) */}
        {block.ok && !readOnly && !!sessionId && run?.dispatched_from === sessionId && (
          <button type="button" data-testid="dispatch-view-canvas" className="btn-sm btn-secondary" onClick={() => openCanvasRuns({ sessionId })}>
            ⬡ {tf(t, 'sessionRuns.viewInCanvas', 'View in Canvas')}
          </button>
        )}
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
        {refused && block.needsPrivate && block.task && bridge.dispatchPrompt && (
          <button type="button" data-testid="dispatch-do-here" className="btn-sm btn-primary" onClick={doHere}>
            {k('doHere', 'Do it here, in a chat tab')}
          </button>
        )}
        {!block.ok && !unsure && !(refused && block.needsPrivate) && block.brain && (
          <button type="button" data-testid="dispatch-open-brain" className="btn-sm btn-primary"
            onClick={() => openOverlayAt('brain-hub', block.brain as string)}>
            {k('openBrain', 'Open {{brain}}', { brain: brainName(t, block.brain) })}
          </button>
        )}
      </div>
    </div>
  );
}
