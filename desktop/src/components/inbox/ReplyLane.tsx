/**
 * ReplyLane — 等你回复: the list of items waiting on the person (REPLY, then PROPOSED
 * under its own label) and the detail of the selected one, with the draft to send
 * (design docs/design/2026-11-20-inbox.md §7-§8, mockup "reply lane").
 *
 * Sending: only a click on 发送 (or Cmd/Ctrl+Enter) calls `inboxService.send`, once per
 * press; a press while one is in flight does nothing. A refusal (403, 409
 * target_changed / redacted / not_open) or an error is shown under the draft with the
 * server's reason and nothing is sent again until the person presses again. Every
 * outcome refreshes the Inbox queries, the item's target included, so after
 * target_changed the person sees the new recipients.
 *
 * Keys (when focus is not in a text field): J / K move, E 不用回, H 稍后; Cmd/Ctrl+Enter
 * sends from anywhere in the overlay, the draft included.
 *
 * The parent (InboxContent) owns the selection, the locally hidden ids and the draft
 * edits, so they survive switching items and tabs.
 */
import { useCallback, useEffect, useRef, useState, type MutableRefObject, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { overlayQuery, invalidateOverlay } from '../layout/overlayQueries';
import { OverlayError, OverlayLoading } from '../layout/OverlayState';
import { inboxService, type ItemAction, type ItemOut, type SendOut, type SendRefusal } from '../../services/inbox';
import { modKeyLabel } from '../../utils/platform';
import { isApiError } from '../../services/api';
import { Avatar, ChannelPill, relTime } from './parts';

export interface DraftEdit { text: string; draftId: string | null }

/** The text for a refusal the server sent without a message of its own. */
const REFUSAL_KEY: Record<string, string> = {
  target_changed: 'ui.InboxOverlay.refusal.targetChanged',
  redacted: 'ui.InboxOverlay.refusal.redacted',
  not_open: 'ui.InboxOverlay.refusal.notOpen',
  forbidden: 'ui.InboxOverlay.refusal.forbidden',
};

export interface ReplyLaneProps {
  items: ItemOut[];
  loading: boolean;
  error: boolean;
  onRetry: () => void;
  selectedId: string | null;
  onSelect: (id: string) => void;
  narrow: boolean;
  showDetail: boolean;
  onBack: () => void;
  /** The item is handled (sent / later / dismissed): hide it and select the next. */
  onHandled: (id: string) => void;
  edits: Record<string, DraftEdit>;
  onEdit: (id: string, edit: DraftEdit | null) => void;
  /** Channel labels of the sources that are not healthy (the banner). */
  unhealthy: string[];
  onFixChannels: () => void;
  /** No channel to reply in is set up (only feeds, or none): the empty state offers 接入. */
  noChannels?: boolean;
  awareCount: number;
  onGoAware: () => void;
  onToChat: (item: ItemOut) => void;
  notify: (msg: string) => void;
  /** Root of the overlay: keys count only inside it. */
  rootRef: MutableRefObject<HTMLElement | null>;
}

const isTextField = (el: EventTarget | null) =>
  el instanceof HTMLElement && (el.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName));

export function ReplyLane(props: ReplyLaneProps) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { items, selectedId, onSelect, onHandled, notify, rootRef } = props;
  const selected = items.find((x) => x.id === selectedId) ?? null;
  const sendRef = useRef<(() => void) | null>(null);
  const [acting, setActing] = useState(false);

  const act = useCallback(async (item: ItemOut, action: ItemAction) => {
    if (acting) return;
    setActing(true);
    try {
      await inboxService.action(item.id, action);
      onHandled(item.id);
      notify(t(action === 'later' ? 'ui.InboxOverlay.reply.snoozed' : 'ui.InboxOverlay.reply.dismissed'));
    } catch {
      notify(t('ui.InboxOverlay.actionError'));
    } finally {
      setActing(false);
      void invalidateOverlay(qc, 'inbox');
    }
  }, [acting, onHandled, notify, qc, t]);

  // J / K / E / H and Cmd/Ctrl+Enter, inside the overlay only.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented) return;
      const root = rootRef.current;
      const inside = e.target === document.body || (root != null && e.target instanceof Node && root.contains(e.target));
      if (!inside) return;
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
        e.preventDefault();
        sendRef.current?.();
        return;
      }
      if (isTextField(e.target) || e.metaKey || e.ctrlKey || e.altKey) return;
      const k = e.key.toLowerCase();
      const i = items.findIndex((x) => x.id === selectedId);
      if (k === 'j' && i < items.length - 1) { e.preventDefault(); onSelect(items[i + 1].id); }
      else if (k === 'k' && i > 0) { e.preventDefault(); onSelect(items[i - 1].id); }
      else if (k === 'e' && selected) { e.preventDefault(); void act(selected, 'dismiss'); }
      else if (k === 'h' && selected) { e.preventDefault(); void act(selected, 'later'); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [items, selectedId, selected, onSelect, act, rootRef]);

  if (props.loading) return <div className="flex-1"><OverlayLoading testid="inbox-reply-loading" /></div>;
  if (props.error) {
    return (
      <div className="flex-1">
        <OverlayError testid="inbox-load-error" retryTestid="inbox-load-retry" message={t('ui.InboxOverlay.loadError')} onRetry={props.onRetry} />
      </div>
    );
  }
  if (!selected && props.noChannels) {
    return (
      <div className="flex flex-1 items-center justify-center text-center" data-testid="inbox-reply-no-channels">
        <div className="max-w-sm">
          <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-full text-[var(--color-primary)]"
            style={{ background: 'color-mix(in srgb, var(--color-primary) 12%, transparent)' }}>
            <span className="material-symbols-outlined icon-lg" aria-hidden>add_link</span>
          </div>
          <h2 className="text-title font-semibold text-[var(--color-text)]">{t('ui.InboxOverlay.reply.noChannelsTitle')}</h2>
          <p className="mt-1 text-body text-[var(--color-text-muted)]">{t('ui.InboxOverlay.reply.noChannelsBody')}</p>
          <button type="button" className="btn-md btn-primary mt-4" onClick={props.onFixChannels} data-testid="inbox-add-channels">
            {t('ui.InboxOverlay.reply.noChannelsAction')}
          </button>
        </div>
      </div>
    );
  }
  if (!selected) {
    return (
      <div className="flex flex-1 items-center justify-center text-center" data-testid="inbox-reply-empty">
        <div>
          <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-full"
            style={{ color: 'var(--color-status-success)', background: 'color-mix(in srgb, var(--color-status-success) 12%, transparent)' }}>
            <span className="material-symbols-outlined icon-lg" aria-hidden>done_all</span>
          </div>
          <h2 className="text-title font-semibold text-[var(--color-text)]">{t('ui.InboxOverlay.reply.emptyTitle')}</h2>
          {props.awareCount > 0 && (
            <p className="mt-1 text-body text-[var(--color-text-muted)]">
              {t('ui.InboxOverlay.reply.emptyAware', { count: props.awareCount })}{' · '}
              <button type="button" className="text-small text-[var(--color-primary)] hover:underline"
                onClick={props.onGoAware} data-testid="inbox-empty-go-aware">
                {t('ui.InboxOverlay.reply.goLook')}
              </button>
            </p>
          )}
        </div>
      </div>
    );
  }

  const reply = items.filter((x) => x.lane !== 'proposed');
  const proposed = items.filter((x) => x.lane === 'proposed');
  const producers = [...new Set(proposed.map((x) => x.producer).filter(Boolean))].join(' · ');
  const listHidden = props.narrow && props.showDetail;
  const detailHidden = props.narrow && !props.showDetail;

  return (
    <div className="flex min-h-0 flex-1">
      {!listHidden && (
        <div data-testid="inbox-list-pane"
          className={`flex min-h-0 flex-col bg-[var(--color-bg)] ${props.narrow ? 'flex-1' : 'w-[320px] shrink-0 border-r border-[var(--color-border)]'}`}>
          {props.unhealthy.length > 0 && (
            <div data-testid="inbox-unhealthy-banner"
              className="mx-3 mt-3 flex items-center gap-2 rounded-lg px-2.5 py-2 text-small text-[var(--color-status-warning)]"
              style={{ background: 'color-mix(in srgb, var(--color-status-warning) 10%, transparent)' }}>
              <span className="material-symbols-outlined icon-sm" aria-hidden>warning</span>
              <span className="min-w-0 flex-1">{t('ui.InboxOverlay.reply.unhealthy', { names: props.unhealthy.join('、') })}</span>
              <button type="button" className="text-small text-[var(--color-primary)] hover:underline"
                onClick={props.onFixChannels} data-testid="inbox-banner-fix">
                {t('ui.InboxOverlay.fix')}
              </button>
            </div>
          )}
          <ul className="flex flex-1 flex-col gap-0.5 overflow-y-auto p-2" aria-label={t('ui.InboxOverlay.tabs.reply')} data-testid="inbox-list">
            {reply.map((it) => <ItemRow key={it.id} item={it} on={it.id === selectedId} onClick={() => onSelect(it.id)} />)}
            {proposed.length > 0 && (
              <li className="px-2.5 pb-1.5 pt-[18px] text-meta text-[var(--color-text-dim)]" data-testid="inbox-proposed-label">
                {producers
                  ? t('ui.InboxOverlay.reply.proposedBy', { producer: producers })
                  : t('ui.InboxOverlay.reply.proposed')}
              </li>
            )}
            {proposed.map((it) => <ItemRow key={it.id} item={it} on={it.id === selectedId} onClick={() => onSelect(it.id)} />)}
          </ul>
          <div className="px-[18px] pb-3.5 pt-2.5 text-meta text-[var(--color-text-dim)]">
            <Kbd>J</Kbd> <Kbd>K</Kbd> {t('ui.InboxOverlay.reply.keysMove')} · <Kbd>{modKeyLabel()}</Kbd><Kbd>↵</Kbd> {t('ui.InboxOverlay.reply.keysSend')} · <Kbd>E</Kbd> {t('ui.InboxOverlay.reply.keysDismiss')}
          </div>
        </div>
      )}
      {!detailHidden && (
        <ReplyDetail key={selected.id} item={selected} narrow={props.narrow} onBack={props.onBack}
          edit={props.edits[selected.id]} onEdit={(e) => props.onEdit(selected.id, e)}
          sendRef={sendRef} acting={acting}
          onLater={() => void act(selected, 'later')} onDismiss={() => void act(selected, 'dismiss')}
          onToChat={() => props.onToChat(selected)}
          onSent={(s) => {
            props.onEdit(selected.id, null);
            onHandled(selected.id);
            // 'reserved' is an earlier send of this item still in flight: not confirmed
            notify(s.status === 'sent'
              ? t('ui.InboxOverlay.reply.sent', { who: selected.author })
              : t('ui.InboxOverlay.reply.sentUnknown', { who: selected.author }));
          }} />
      )}
    </div>
  );
}

function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="rounded border border-[var(--color-border)] bg-[var(--color-card)] px-1 text-meta text-[var(--color-text-muted)]">{children}</kbd>
  );
}

function ItemRow({ item, on, onClick }: { item: ItemOut; on: boolean; onClick: () => void }) {
  const { t } = useTranslation();
  const bot = item.lane === 'proposed';
  return (
    <li>
      <button type="button" onClick={onClick} aria-selected={on} data-testid="inbox-item" data-item-id={item.id}
        className={`flex w-full items-start gap-3 rounded-[10px] px-2.5 py-3 text-left transition-colors ${on ? 'bg-[var(--color-card)] shadow-sm' : 'hover:bg-[var(--color-hover)]'}`}>
        <Avatar name={item.author} icon={bot ? 'smart_toy' : undefined} />
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2">
            <span className="truncate text-body font-semibold text-[var(--color-text)]">{item.author}</span>
            <ChannelPill kind={item.source_kind} label={item.channel_label} />
            <span data-testid="inbox-item-time" data-late={item.late || undefined}
              className={`ml-auto shrink-0 text-meta ${item.late ? 'text-[var(--color-status-warning)]' : 'text-[var(--color-text-dim)]'}`}>
              {relTime(item.received_at, t)}
            </span>
          </span>
          <span className={`mt-0.5 line-clamp-2 text-small ${on ? 'text-[var(--color-text-secondary)]' : 'text-[var(--color-text-muted)]'}`}>
            {item.ask || item.excerpt}
          </span>
        </span>
      </button>
    </li>
  );
}

/** The draft text with the spans redaction would change marked. The router is Python,
 *  so its offsets count code points: index the text by code point, not by UTF-16 unit
 *  (an emoji before a span would otherwise shift the mark). */
function MarkedText({ text, spans }: { text: string; spans: SendRefusal['spans'] }) {
  const chars = [...text];
  const parts: ReactNode[] = [];
  let at = 0;
  const sorted = [...spans].sort((a, b) => a.start - b.start);
  sorted.forEach((s, i) => {
    const start = Math.max(at, Math.min(s.start, chars.length));
    const end = Math.min(s.end, chars.length);
    if (end <= start) return;
    if (start > at) parts.push(chars.slice(at, start).join(''));
    parts.push(
      <mark key={i} data-testid="inbox-redacted-span" className="rounded-sm text-[var(--color-text)]"
        style={{ background: 'color-mix(in srgb, var(--color-status-error) 22%, transparent)' }}>
        {chars.slice(start, end).join('')}
      </mark>,
    );
    at = end;
  });
  if (at < chars.length) parts.push(chars.slice(at).join(''));
  return <p data-testid="inbox-redacted-preview" className="mt-2 whitespace-pre-wrap text-small text-[var(--color-text-secondary)]">{parts}</p>;
}

function ReplyDetail({ item, narrow, onBack, edit, onEdit, sendRef, acting, onLater, onDismiss, onToChat, onSent }: {
  item: ItemOut; narrow: boolean; onBack: () => void;
  edit: DraftEdit | undefined; onEdit: (e: DraftEdit) => void;
  sendRef: MutableRefObject<(() => void) | null>; acting: boolean;
  onLater: () => void; onDismiss: () => void; onToChat: () => void; onSent: (s: SendOut) => void;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const target = useQuery(overlayQuery.inboxTarget(item.id));
  const draft = useQuery(overlayQuery.inboxDraft(item.id));
  const text = edit?.text ?? draft.data?.text ?? '';
  const draftId = edit?.draftId ?? draft.data?.id ?? null;
  const note = draft.data?.note ?? '';
  const [busy, setBusy] = useState(false);
  const [redrafting, setRedrafting] = useState(false);
  const [refusal, setRefusal] = useState<SendRefusal | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inFlight = useRef(false);
  const bot = item.lane === 'proposed';
  // Send only on a target resolved since this item opened and not being re-resolved
  // (after a refusal the target refetches): never on an earlier answer.
  const targetFresh = target.isFetchedAfterMount && !target.isFetching && !target.isError;
  const canSend = targetFresh && !!target.data?.can_send && text.trim().length > 0 && !busy;

  const send = useCallback(async () => {
    // One press, one POST: a second press while the first is in flight does nothing,
    // and no outcome below sends again.
    if (inFlight.current || !targetFresh || !target.data?.can_send || !text.trim()) return;
    inFlight.current = true;
    setBusy(true);
    setRefusal(null);
    setError(null);
    try {
      const r = await inboxService.send(item.id, { draft_id: draftId, text, target_sha: target.data.target_sha });
      if (!r.ok) {
        // target_changed: the invalidate below refetches this item's target too (its
        // key is under ['overlay', 'inbox']), so the new recipients show before a resend
        setRefusal(r.refusal);
        return;
      }
      if (r.send.status === 'failed') {
        setError(r.send.error || t('ui.InboxOverlay.reply.sendFailed'));
        return;
      }
      onSent(r.send);
    } catch (e) {
      // A 4xx the router explains (a 422 on an empty text) was refused before any send.
      // Anything else (no response, a 5xx) may have come after the row was reserved or
      // even sent: say the outcome is unclear and point to 已发, never "not sent".
      setError(isApiError(e) && e.statusCode >= 400 && e.statusCode < 500
        ? t('ui.InboxOverlay.reply.sendRejected', { reason: e.message })
        : t('ui.InboxOverlay.reply.sendUnclear'));
    } finally {
      inFlight.current = false;
      setBusy(false);
      void invalidateOverlay(qc, 'inbox');
    }
  }, [target.data, targetFresh, text, draftId, item.id, qc, onSent, t]);

  useEffect(() => {
    sendRef.current = () => { void send(); };
    return () => { sendRef.current = null; };
  }, [send, sendRef]);

  const redraft = async () => {
    if (redrafting) return;
    setRedrafting(true);
    setError(null);
    try {
      const d = await inboxService.redraft(item.id);
      onEdit({ text: d.text, draftId: d.id });
      qc.setQueryData(overlayQuery.inboxDraft(item.id).queryKey, d);
    } catch {
      setError(t('ui.InboxOverlay.reply.redraftError'));
    } finally {
      setRedrafting(false);
    }
  };

  const refusalText = refusal ? (refusal.message || t(REFUSAL_KEY[refusal.code] ?? 'ui.InboxOverlay.refusal.other')) : null;

  return (
    <section className="min-w-0 flex-1 overflow-y-auto" aria-label={item.author} data-testid="inbox-detail">
      <div className={`mx-auto flex max-w-[680px] flex-col ${narrow ? 'px-6 pb-10 pt-5' : 'px-9 pb-12 pt-7'}`}>
        <div className="flex items-center gap-2.5 text-small text-[var(--color-text-muted)]">
          {narrow && (
            <button type="button" className="icon-btn text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]"
              onClick={onBack} aria-label={t('ui.InboxOverlay.reply.back')} data-testid="inbox-back">
              <span className="material-symbols-outlined icon-sm">arrow_back</span>
            </button>
          )}
          <Avatar name={item.author} size="sm" icon={bot ? 'smart_toy' : undefined} />
          <span className="text-body font-semibold text-[var(--color-text)]">{item.author}</span>
          <ChannelPill kind={item.source_kind} label={item.channel_label} large />
          <span className="truncate">{[item.author_role, relTime(item.received_at, t)].filter(Boolean).join(' · ')}</span>
          <span className="ml-auto flex shrink-0 gap-0.5">
            <IconAction icon="chat" label={t('ui.InboxOverlay.toChat')} onClick={onToChat} testid="inbox-to-chat" />
            <IconAction icon="schedule" label={t('ui.InboxOverlay.reply.later')} onClick={onLater} testid="inbox-later" disabled={acting} />
            <IconAction icon="done" label={t('ui.InboxOverlay.reply.dismiss')} onClick={onDismiss} testid="inbox-dismiss" disabled={acting} />
          </span>
        </div>

        <h2 className="mt-[18px] text-display font-semibold tracking-[-0.01em] text-[var(--color-text)]" data-testid="inbox-ask">{item.ask || item.excerpt}</h2>
        {item.ask_sub && <p className="mt-1.5 text-small text-[var(--color-text-dim)]">{item.ask_sub}</p>}

        <div className="mt-7 flex flex-col gap-5">
          <div className="max-w-[88%] self-start whitespace-pre-line rounded-[4px_16px_16px_16px] px-4 py-3 text-body text-[var(--color-text)]"
            style={{ background: 'color-mix(in srgb, var(--color-text) 5%, transparent)' }} data-testid="inbox-original">
            {item.where && <span className="mb-1 block text-meta text-[var(--color-text-dim)]">{item.where}</span>}
            {item.excerpt}
          </div>

          <div className="w-[92%] self-end">
            {(target.data?.flags ?? []).map((f, i) => (
              <div key={i} role="alert" data-testid="inbox-target-flag"
                className="mb-2 flex items-start gap-2 rounded-[10px] px-3 py-2.5 text-small text-[var(--color-status-error)]"
                style={{ background: 'color-mix(in srgb, var(--color-status-error) 9%, transparent)' }}>
                <span className="material-symbols-outlined icon-sm mt-px" aria-hidden>error</span>
                <span>{f}</span>
              </div>
            ))}
            <div className="mb-1.5 flex items-center justify-end gap-1.5 text-meta text-[var(--color-text-dim)]" data-testid="inbox-target">
              {target.isPending || (target.isFetching && !target.isError)
                ? t('ui.InboxOverlay.reply.targetLoading')
                : target.isError
                  ? <span className="text-[var(--color-status-error)]">{t('ui.InboxOverlay.reply.targetError')}</span>
                  : <>{t('ui.InboxOverlay.reply.replyTo')} <b className="font-semibold text-[var(--color-text-muted)]">{target.data?.label}</b></>}
            </div>
            <div className="rounded-[16px_4px_16px_16px] px-4 pb-2.5 pt-3 focus-within:shadow-[inset_0_0_0_1.5px_var(--color-primary)]"
              style={{ background: 'color-mix(in srgb, var(--color-primary) 8%, var(--color-card))', boxShadow: 'inset 0 0 0 1px color-mix(in srgb, var(--color-primary) 28%, transparent)' }}>
              {draft.isPending && !edit
                ? <OverlayLoading label={t('ui.InboxOverlay.reply.drafting')} />
                : (
                  <textarea data-testid="inbox-draft" aria-label={t('ui.InboxOverlay.reply.draftLabel')}
                    value={text} rows={Math.min(14, Math.max(3, text.split('\n').length + 1))}
                    onChange={(e) => { onEdit({ text: e.target.value, draftId }); setRefusal(null); setError(null); }}
                    className="block w-full resize-none border-0 bg-transparent p-0 text-body text-[var(--color-text)] outline-none" />
                )}
              <div className="mt-2.5 flex items-center gap-2.5">
                <button type="button" onClick={() => void redraft()} disabled={redrafting} data-testid="inbox-redraft"
                  className="btn-sm btn-ghost">
                  <span className="material-symbols-outlined icon-xs" aria-hidden>refresh</span>
                  {redrafting ? t('ui.InboxOverlay.reply.redrafting') : t('ui.InboxOverlay.reply.redraft')}
                </button>
                <span className="truncate text-meta text-[var(--color-text-dim)]">{note}</span>
                <span className="flex-1" />
                <button type="button" onClick={() => void send()} disabled={!canSend} data-testid="inbox-send"
                  className="btn-lg btn-primary font-semibold disabled:opacity-60">
                  {busy ? t('ui.InboxOverlay.reply.sending') : t('ui.InboxOverlay.reply.send')}
                  {!busy && <kbd className="rounded px-1 text-meta" style={{ background: 'color-mix(in srgb, var(--color-on-accent) 22%, transparent)' }}>{modKeyLabel()}↵</kbd>}
                </button>
              </div>
            </div>
            {(refusalText || error) && (
              <div className="mt-2 text-right text-small text-[var(--color-status-error)]" role="alert" data-testid="inbox-send-error"
                data-code={refusal?.code}>
                {refusalText ?? error}
              </div>
            )}
            {refusal?.code === 'redacted' && refusal.spans.length > 0 && <MarkedText text={text} spans={refusal.spans} />}
          </div>
        </div>
      </div>
    </section>
  );
}

function IconAction({ icon, label, onClick, testid, disabled }: {
  icon: string; label: string; onClick: () => void; testid: string; disabled?: boolean;
}) {
  return (
    <button type="button" className="icon-btn text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)] disabled:opacity-40"
      onClick={onClick} title={label} aria-label={label} data-testid={testid} disabled={disabled}>
      <span className="material-symbols-outlined icon-sm">{icon}</span>
    </button>
  );
}
