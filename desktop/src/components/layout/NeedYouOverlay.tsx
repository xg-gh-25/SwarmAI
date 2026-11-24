/**
 * NeedYouOverlay — the unified "Need You" channel, fullscreen (design
 * Knowledge/Mockups/2026-11-24-needs-you-redesign.html; channel design
 * Knowledge/Designs/2026-08-08-unified-need-you-channel-design.md).
 *
 * Four tabs: Needs you · Cognition flow · Health · How it works. The first one:
 *   - For you: one card per item only the person can answer (`handler` person, decided
 *     by the backend's handler_of). The title is the thing itself, an amber pill says
 *     once what it needs of the person, then how long it has waited; the card holds the
 *     reason and its actions. Nothing for the person → one calm line.
 *   - Swarm handles it: what Swarm can carry to done itself (a failing job, a fresh
 *     result, a stuck CDF). The backend starts each one by itself (core/attention_auto):
 *     a row with a run shows its state (on it / done) and a link to its session; a row
 *     with none yet keeps "Hand to Swarm", and one button hands all of those. A run that
 *     did not work comes back as a For-you card saying what was tried.
 *   - Under them, Swarm's cognition level (CognitionLevel).
 * The tab's count is the person's count, the same number as the left-nav badge.
 *
 * Actions reuse the existing paths (no /act endpoint): a card or row sends the item's
 * prompt to chat through the overlay ctx's `dispatchPrompt` (or fills the input), or
 * opens the place where it is handled (the Swarm Out tab, a settings page:
 * NeedsYouRow.itemOpener). The overlay closes once the prompt landed.
 *
 * Data: `overlayQuery.needsYou()` (TanStack Query, the same cache as the badge and the
 * Welcome card); first-load states via OverlayState.
 */
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import type { AttentionEntry } from '../../services/attention';
import { Tabs } from '../overlay';
import { CognitiveDistillationFlow } from './CognitiveDistillationFlow';
import { CdfGuideline } from './CdfGuideline';
import { CdfHealth } from './CdfHealth';
import { CognitionLevel } from './CognitionLevel';
import { overlayQuery } from './overlayQueries';
import { OverlayLoading, OverlayError } from './OverlayState';
import { itemOpener, needsYouPrompt } from './NeedsYouRow';
import { getOverlayCtxBridge } from './overlayRegistry';
import { chatService } from '../../services/chat';
import { CARD, MUTED, ZoneLabel, agoText } from './needYouParts';
import type { DispatchPrompt } from './overlayRegistry';

interface NeedYouContentProps {
  /** Land the item's prompt in a chat tab (overlay ctx dispatchPrompt); `autoSend`
   *  sends it now. Returns whether it landed. */
  onDispatch: DispatchPrompt;
  /** Close the overlay (after dispatching). */
  close: () => void;
}

type NeedYouTab = 'attention' | 'flow' | 'health' | 'guide';

export function NeedYouContent({ onDispatch: dispatchPrompt, close }: NeedYouContentProps) {
  const { t } = useTranslation();
  const [tab, setTab] = useState<NeedYouTab>('attention');
  // a prompt that could not reach a chat tab says so, instead of a button that did nothing
  const [missed, setMissed] = useState(false);
  const onDispatch: DispatchPrompt = useCallback((prompt, opts) => {
    const landed = dispatchPrompt(prompt, opts);
    setMissed(!landed);
    return landed;
  }, [dispatchPrompt]);
  useEffect(() => {
    if (!missed) return undefined;
    const timer = setTimeout(() => setMissed(false), 6000);
    return () => clearTimeout(timer);
  }, [missed]);
  const props = { onDispatch, close };
  const q = useQuery(overlayQuery.needsYou());
  const mine = q.data ? (q.data.counts.person ?? q.data.items.filter((i) => i.handler !== 'agent').length) : 0;
  return (
    <div className="flex min-h-0 flex-1 flex-col p-5" data-testid="needs-you-overlay">
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Tabs
          label={t('needYou.title')}
          items={[
            { key: 'attention', label: t('needYou.tabAttention'), testid: 'need-you-tab',
              count: mine > 0 ? mine : undefined, tone: 'alert' },
            { key: 'flow', label: t('needYou.tabFlow'), testid: 'cdf-tab' },
            { key: 'health', label: t('needYou.tabHealth'), testid: 'cdf-health-tab' },
            { key: 'guide', label: t('needYou.tabGuide'), testid: 'cdf-guide-tab' },
          ]}
          value={tab}
          onChange={setTab}
        />
        {missed && (
          <span role="status" className="text-small" style={{ color: 'var(--color-status-warning)' }} data-testid="needs-you-missed">
            {t('needYou.notLanded')}
          </span>
        )}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {tab === 'attention' && (
          <>
            <AttentionQueueContent {...props} />
            <CognitionLevel />
          </>
        )}
        {tab === 'flow' && <CognitiveDistillationFlow onDispatch={props.onDispatch} close={props.close} onOpenHealth={() => setTab('health')} />}
        {tab === 'health' && <CdfHealth onDispatch={props.onDispatch} close={props.close} />}
        {tab === 'guide' && <CdfGuideline />}
      </div>
    </div>
  );
}

/** The thing an item is about: its summary (which thing), else its title; a summary that
 *  only repeats the detail (a CDF stall) gives way to the title. */
function thingOf(it: AttentionEntry): string {
  return it.summary && it.summary !== it.detail ? it.summary : it.title;
}

/** What the pill says the item needs of the person, by its kind (else its source). */
function askLabel(t: (k: string, o?: Record<string, unknown>) => string, it: AttentionEntry): string {
  const kind = typeof it.dispatch?.context?.kind === 'string' ? it.dispatch.context.kind : it.source;
  return t(`needYou.ask.${kind}`, { defaultValue: t(`needYou.ask.${it.source}`, { defaultValue: t('needYou.ask.default') }) });
}

function AttentionQueueContent({ onDispatch, close }: NeedYouContentProps) {
  const { t } = useTranslation();
  const q = useQuery(overlayQuery.needsYou());
  const items: AttentionEntry[] = q.data?.items ?? [];
  const loading = q.isPending;
  const error = q.isError && !q.data;

  const onFill = useCallback((prompt: string) => { if (onDispatch(prompt, { autoSend: false })) close(); }, [onDispatch, close]);
  const onSend = useCallback((prompt: string) => { if (onDispatch(prompt, { autoSend: true })) close(); }, [onDispatch, close]);
  // an automatic run's session, resumed in a chat tab (the same path as History)
  const onOpenSession = useCallback((sessionId: string) => {
    void (async () => {
      try {
        const session = await chatService.getSession(sessionId);
        if (session && getOverlayCtxBridge().resumeSession?.(session)) close();
      } catch { /* a deleted session: the link does nothing */ }
    })();
  }, [close]);

  // anything not explicitly Swarm's waits on the person (the client's own default)
  const mine = items.filter((i) => i.handler !== 'agent');
  const swarm = items.filter((i) => i.handler === 'agent');
  // what Swarm has not started on yet: only those are handed over by hand
  const waiting = swarm.filter((i) => !i.auto);
  const handAll = () => onSend(t('needYou.handAllPrompt', {
    count: waiting.length,
    items: waiting.map((it, i) => `${i + 1}. ${needsYouPrompt(it)}`).join('\n\n'),
  }));

  return (
    <div className="space-y-8 py-2" data-testid="needs-you-attention">
      {loading && <OverlayLoading />}
      {error && (
        <OverlayError
          testid="needyou-load-error"
          retryTestid="needyou-load-retry"
          message={t('needYou.loadFailed')}
          onRetry={() => { void q.refetch(); }}
        />
      )}

      {!loading && !error && (
        <section data-testid="needs-you-mine">
          {mine.length === 0 ? (
            <div className="flex items-center gap-3.5 rounded-xl px-[18px] py-5"
              style={{ background: 'color-mix(in srgb, var(--color-status-success) 7%, transparent)' }} data-testid="needs-you-calm">
              <span aria-hidden className="so-pop grid h-[30px] w-[30px] place-items-center rounded-full text-body font-semibold text-[var(--color-on-accent)]"
                style={{ background: 'var(--color-status-success)' }}>✓</span>
              <span className="text-heading font-semibold">{t('needYou.empty')}</span>
            </div>
          ) : (
            <>
              <ZoneLabel count={mine.length}>{t('needYou.mine')}</ZoneLabel>
              <div className="flex flex-col gap-2.5">
                {mine.map((it) => <PersonCard key={it.id} it={it} onFill={onFill} onSend={onSend} onOpened={close} onOpenSession={onOpenSession} />)}
              </div>
            </>
          )}
        </section>
      )}

      {swarm.length > 0 && (
        <section data-testid="needs-you-swarm">
          <ZoneLabel count={swarm.length} right={waiting.length > 0 ? (
            <button type="button" className="btn-sm btn-secondary" style={{ color: 'var(--color-zone-cognitive)' }}
              onClick={handAll} data-testid="needs-you-hand-all">{t('needYou.handAll')}</button>
          ) : undefined}>{t('needYou.swarm')}</ZoneLabel>
          <div className="flex flex-col divide-y divide-[var(--color-border)]">
            {swarm.map((it) => <SwarmRow key={it.id} it={it} onSend={onSend} onOpenSession={onOpenSession} />)}
          </div>
        </section>
      )}
    </div>
  );
}

function Meta({ it }: { it: AttentionEntry }) {
  const { t } = useTranslation();
  return (
    <span className="flex flex-wrap items-center gap-x-3 gap-y-1 text-small">
      <b className="font-semibold text-[var(--color-text)]">{it.brain ?? t('needYou.system')}</b>
      <span className="rounded px-1.5 text-meta font-semibold text-[var(--color-text-secondary)] ring-1 ring-inset ring-[var(--color-border)]">
        {t(`needYou.source.${it.source}`, { defaultValue: it.source })}
      </span>
    </span>
  );
}

function PersonCard({ it, onFill, onSend, onOpened, onOpenSession }: {
  it: AttentionEntry; onFill: (p: string) => void; onSend: (p: string) => void; onOpened: () => void;
  onOpenSession: (sessionId: string) => void;
}) {
  const { t } = useTranslation();
  const opener = itemOpener(it);
  const prompt = needsYouPrompt(it);
  const age = agoText(t, it.since);
  const thing = thingOf(it);
  // the reason: the source's detail, else the backend's title when the card's title is the
  // summary; never the card's own title again
  const why = it.detail && it.detail !== thing ? it.detail : (it.summary && it.title !== thing ? it.title : '');
  return (
    <article className={`${CARD} so-card--you relative overflow-hidden px-4 py-3.5`} data-testid="needs-you-card"
      style={{ borderColor: 'color-mix(in srgb, var(--color-status-warning) 55%, var(--color-border))' }}>
      <div className="flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-1">
        <span className="min-w-0 flex-1 truncate text-body font-semibold" title={thing}>{thing}</span>
        <span className="so-pop whitespace-nowrap rounded-full px-2.5 py-0.5 text-meta font-semibold text-[var(--color-bg)]"
          style={{ background: 'var(--color-status-warning)' }} data-testid="needs-you-ask">{askLabel(t, it)}</span>
        {age && <span className={`whitespace-nowrap text-small ${MUTED}`} data-testid="needs-you-age">{age}</span>}
      </div>
      <div className="mt-1.5"><Meta it={it} /></div>
      <div className="mt-3 rounded-lg px-3 py-2.5" style={{ background: 'color-mix(in srgb, var(--color-status-warning) 8%, transparent)' }}>
        {why && <p className="line-clamp-3 text-body text-[var(--color-text-secondary)]" title={why}>{why}</p>}
        <div className={`flex flex-wrap items-center gap-2 ${why ? 'mt-2.5' : ''}`}>
          {opener ? (
            <button type="button" className="btn-sm btn-primary" data-testid="needs-you-open"
              onClick={() => { opener.open(); onOpened(); }}>
              {opener.kind === 'settings' ? t('needYou.openSettings') : t('needYou.openSwarmOut')}
            </button>
          ) : (
            <>
              <button type="button" className="btn-sm btn-primary" data-testid="needs-you-send"
                aria-label={t('needYou.sendNowLabel', { title: thing })} onClick={() => onSend(prompt)}>{t('needYou.handle')}</button>
              <button type="button" className="btn-sm btn-ghost" data-testid="needs-you-fill" onClick={() => onFill(prompt)}>{t('needYou.fillHint')}</button>
            </>
          )}
          {/* Swarm already tried it: what it did is in its session */}
          {it.auto?.sessionId && (
            <button type="button" className="btn-sm btn-ghost" data-testid="needs-you-auto-session"
              onClick={() => onOpenSession(it.auto!.sessionId!)}>{t('needYou.auto.session')}</button>
          )}
        </div>
      </div>
    </article>
  );
}

function SwarmRow({ it, onSend, onOpenSession }: {
  it: AttentionEntry; onSend: (p: string) => void; onOpenSession: (sessionId: string) => void;
}) {
  const { t } = useTranslation();
  const auto = it.auto && (it.auto.state === 'running' || it.auto.state === 'done') ? it.auto : null;
  const thing = thingOf(it);
  // the other of title and summary, then the detail when it says something new
  const second = thing === it.title ? (it.summary !== it.title ? it.summary : '') : it.title;
  const detail = it.detail && it.detail !== thing && it.detail !== second ? it.detail : '';
  return (
    <div className="grid grid-cols-[18px_minmax(0,1fr)_auto] items-center gap-x-3 py-2.5" data-testid="needs-you-swarm-row">
      <span aria-hidden className="grid h-[18px] w-[18px] place-items-center rounded-full text-meta"
        style={{ color: 'var(--color-zone-cognitive)', background: 'color-mix(in srgb, var(--color-zone-cognitive) 13%, transparent)' }}>◆</span>
      <div className="min-w-0">
        <div className="truncate text-body" title={second ? `${thing} · ${second}` : thing}>
          <b className="font-semibold">{thing}</b>
          {second && <span className={MUTED}> · {second}</span>}
        </div>
        <div className="mt-0.5 flex min-w-0 flex-wrap items-center gap-x-3"><Meta it={it} />
          {detail && <span className={`min-w-0 truncate text-small ${MUTED}`}>{detail}</span>}</div>
      </div>
      {auto ? (
        <span className="flex items-center gap-2.5" data-testid="needs-you-auto" data-state={auto.state}>
          <span className="inline-flex items-center whitespace-nowrap rounded-full px-2.5 py-0.5 text-meta font-semibold"
            style={auto.state === 'running'
              ? { color: 'var(--color-zone-cognitive)', background: 'color-mix(in srgb, var(--color-zone-cognitive) 13%, transparent)' }
              : { color: 'var(--color-status-success)', background: 'color-mix(in srgb, var(--color-status-success) 12%, transparent)' }}>
            {auto.state === 'running' && (
              <span aria-hidden className="so-live mr-1.5 inline-block h-1.5 w-1.5 rounded-full" style={{ background: 'var(--color-zone-cognitive)' }} />
            )}
            {auto.state === 'running' ? t('needYou.auto.running') : `✓ ${t('needYou.auto.done')}`}
            {agoText(t, auto.at) && <span className="font-normal"> · {agoText(t, auto.at)}</span>}
          </span>
          {auto.sessionId && (
            <button type="button" className="btn-sm btn-ghost" data-testid="needs-you-auto-session"
              onClick={() => onOpenSession(auto.sessionId!)}>{t('needYou.auto.session')}</button>
          )}
        </span>
      ) : (
        <button type="button" className="btn-sm btn-secondary" style={{ color: 'var(--color-zone-cognitive)' }}
          aria-label={t('needYou.sendNowLabel', { title: thing })}
          onClick={() => onSend(needsYouPrompt(it))} data-testid="needs-you-hand">{t('needYou.hand')}</button>
      )}
    </div>
  );
}
