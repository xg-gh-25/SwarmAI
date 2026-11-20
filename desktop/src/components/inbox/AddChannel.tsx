/**
 * AddChannel — the "加一个渠道" part of the Inbox channel settings (design
 * docs/design/2026-11-20-inbox.md §4, §7).
 *
 * The one decision here is "connect this channel?":
 *   - what this machine can connect without typing anything (`GET /sources/available`:
 *     a configured MCP server whose tools match a shipped mapping, a logged-in `gh`),
 *     one row each with a single 接入 button; an added one says 已接入;
 *   - by hand, behind disclosure: Slack Web API (a user token) and IMAP/SMTP.
 * Every add is one POST; the backend stores the source only after asking it who the
 * person is, so success shows that identity and a refusal shows the source's own
 * message and fix in place. A typed secret is sent once and cleared from the form.
 */
import { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { invalidateOverlay } from '../layout/overlayQueries';
import { inboxService, type AvailableSourceOut, type SourceCreateRequest, type SourceRefusal } from '../../services/inbox';
import { Avatar, channelIcon, channelVar } from './parts';

/** Under the overlay's ['overlay', 'inbox'] prefix, so a write's invalidation refreshes it. */
export const AVAILABLE_KEY = ['overlay', 'inbox', 'sources-available'] as const;

const INPUT = 'w-full rounded-md border border-[var(--color-border)] bg-transparent px-2 py-1.5 text-small text-[var(--color-text)]';

function useAdd(notify: (msg: string) => void) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [busy, setBusy] = useState<string | null>(null);
  const [refusal, setRefusal] = useState<{ key: string; r: SourceRefusal } | null>(null);
  const add = useCallback(async (key: string, body: SourceCreateRequest): Promise<boolean> => {
    if (busy) return false;
    setBusy(key);
    setRefusal(null);
    try {
      const res = await inboxService.addSource(body);
      if (!res.ok) {
        setRefusal({ key, r: res.refusal });
        return false;
      }
      notify(t('ui.InboxOverlay.channels.connectedAs', {
        name: res.source.channel_label, identity: res.source.identity || res.source.backend,
      }));
      return true;
    } catch {
      setRefusal({ key, r: { status: 0, code: 'error', message: t('ui.InboxOverlay.actionError'), fix: '' } });
      return false;
    } finally {
      setBusy(null);
      void invalidateOverlay(qc, 'inbox');
    }
  }, [busy, notify, qc, t]);
  return { add, busy, refusal };
}

function RefusalLine({ r }: { r: SourceRefusal }) {
  const { t } = useTranslation();
  const message = r.message || r.code;
  return (
    <p role="alert" data-testid="inbox-add-refusal" className="mt-1 text-small text-[var(--color-status-error)]">
      {r.fix ? t('ui.InboxOverlay.channels.refusedFix', { message, fix: r.fix }) : t('ui.InboxOverlay.channels.refused', { message })}
    </p>
  );
}

export function AddChannel({ notify }: { notify: (msg: string) => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: AVAILABLE_KEY, queryFn: () => inboxService.availableSources(), staleTime: 60_000 });
  const [rescanning, setRescanning] = useState(false);
  const { add, busy, refusal } = useAdd(notify);
  const [manual, setManual] = useState<'slack' | 'imap' | null>(null);

  const rescan = async () => {
    setRescanning(true);
    try {
      qc.setQueryData(AVAILABLE_KEY, await inboxService.availableSources(true));
    } catch {
      void q.refetch();
    } finally {
      setRescanning(false);
    }
  };

  const keyOf = (a: AvailableSourceOut) => `${a.kind}|${a.backend}`;
  const rows = q.data ?? [];

  return (
    <section className="mt-9" data-testid="inbox-add-channel">
      <div className="flex items-baseline gap-3">
        <h2 className="text-heading font-semibold text-[var(--color-text)]">{t('ui.InboxOverlay.channels.addTitle')}</h2>
        <button type="button" className="text-small text-[var(--color-primary)] hover:underline disabled:opacity-50"
          disabled={rescanning || q.isFetching} onClick={() => void rescan()} data-testid="inbox-available-rescan">
          {t('ui.InboxOverlay.channels.rescan')}
        </button>
      </div>
      <p className="mt-1 text-small text-[var(--color-text-dim)]">{t('ui.InboxOverlay.channels.addSub')}</p>

      {q.isPending || rescanning ? (
        <p className="mt-4 text-small text-[var(--color-text-muted)]">{t('ui.InboxOverlay.channels.scanning')}</p>
      ) : q.isError ? (
        <p className="mt-4 text-small text-[var(--color-status-error)]">{t('ui.InboxOverlay.channels.availableError')}</p>
      ) : rows.length === 0 ? (
        <p className="mt-4 text-small text-[var(--color-text-muted)]" data-testid="inbox-available-empty">
          {t('ui.InboxOverlay.channels.availableEmpty')}
        </p>
      ) : (
        <ul className="mt-3 flex flex-col gap-1">
          {rows.map((a) => (
            <li key={keyOf(a)} data-testid="inbox-available-row" className="rounded-xl p-3 hover:bg-[var(--color-hover)]">
              <div className="flex items-center gap-3.5">
                <Avatar name={a.channel_label} size="sm" icon={channelIcon(a.kind)} colorVar={channelVar(a.kind)} />
                <span className="min-w-0 flex-1">
                  <span className="block text-body font-semibold text-[var(--color-text)]">{a.channel_label} · {a.label}</span>
                  <span className="mt-0.5 block truncate text-small text-[var(--color-text-dim)]">
                    {[a.identity, a.detail].filter(Boolean).join(' · ')}
                  </span>
                </span>
                {a.added ? (
                  <span className="text-small text-[var(--color-text-muted)]">{t('ui.InboxOverlay.channels.already')}</span>
                ) : (
                  <button type="button" className="btn-md btn-primary" disabled={busy !== null}
                    data-testid="inbox-available-connect"
                    onClick={() => void add(keyOf(a), { kind: a.kind, backend: a.backend, options: a.options })}>
                    {busy === keyOf(a) ? t('ui.InboxOverlay.channels.connecting') : t('ui.InboxOverlay.channels.connect')}
                  </button>
                )}
              </div>
              {refusal?.key === keyOf(a) && <RefusalLine r={refusal.r} />}
            </li>
          ))}
        </ul>
      )}

      <h3 className="mt-6 text-body font-semibold text-[var(--color-text)]">{t('ui.InboxOverlay.channels.manualTitle')}</h3>
      <div className="mt-2 flex flex-wrap gap-2">
        <button type="button" className={`btn-md ${manual === 'slack' ? 'btn-secondary' : 'btn-ghost'}`} aria-expanded={manual === 'slack'}
          onClick={() => setManual((m) => (m === 'slack' ? null : 'slack'))} data-testid="inbox-add-slack-open">
          {t('ui.InboxOverlay.channels.manualSlack')}
        </button>
        <button type="button" className={`btn-md ${manual === 'imap' ? 'btn-secondary' : 'btn-ghost'}`} aria-expanded={manual === 'imap'}
          onClick={() => setManual((m) => (m === 'imap' ? null : 'imap'))} data-testid="inbox-add-imap-open">
          {t('ui.InboxOverlay.channels.manualImap')}
        </button>
      </div>
      {manual === 'slack' && (
        <SlackForm busy={busy !== null} refusal={refusal?.key === 'manual-slack' ? refusal.r : null}
          onSubmit={(token) => add('manual-slack', { kind: 'slack', backend: 'slack-web', options: {}, secrets: { user_token: token } })}
          onDone={() => setManual(null)} />
      )}
      {manual === 'imap' && (
        <ImapForm busy={busy !== null} refusal={refusal?.key === 'manual-imap' ? refusal.r : null}
          onSubmit={(options, password) => add('manual-imap', { kind: 'mail', backend: 'imap', options, secrets: { password } })}
          onDone={() => setManual(null)} />
      )}
    </section>
  );
}

function SlackForm({ busy, refusal, onSubmit, onDone }: {
  busy: boolean; refusal: SourceRefusal | null; onSubmit: (token: string) => Promise<boolean>; onDone: () => void;
}) {
  const { t } = useTranslation();
  const [token, setToken] = useState('');
  const submit = async () => {
    const value = token.trim();
    if (!value) return;
    const ok = await onSubmit(value);
    setToken('');   // a secret never stays in the form, whatever the answer
    if (ok) onDone();
  };
  return (
    <div className="mt-3 max-w-[520px] rounded-xl border border-[var(--color-border)] p-4" data-testid="inbox-add-slack-form">
      <p className="text-small text-[var(--color-text-dim)]">{t('ui.InboxOverlay.channels.manualSlackHint')}</p>
      <label className="mt-3 block text-small text-[var(--color-text-muted)]">
        {t('ui.InboxOverlay.channels.slackToken')} *
        <input type="password" autoComplete="off" spellCheck={false} className={`mt-1 ${INPUT} font-mono`} value={token}
          onChange={(e) => setToken(e.target.value)} data-testid="inbox-add-slack-token" />
      </label>
      {refusal && <RefusalLine r={refusal} />}
      <div className="mt-3 flex justify-end gap-2">
        <button type="button" className="btn-md btn-ghost" onClick={onDone}>{t('ui.InboxOverlay.channels.cancel')}</button>
        <button type="button" className="btn-md btn-primary" disabled={busy || !token.trim()} onClick={() => void submit()}
          data-testid="inbox-add-slack-submit">{t('ui.InboxOverlay.channels.add')}</button>
      </div>
    </div>
  );
}

function ImapForm({ busy, refusal, onSubmit, onDone }: {
  busy: boolean; refusal: SourceRefusal | null;
  onSubmit: (options: Record<string, unknown>, password: string) => Promise<boolean>; onDone: () => void;
}) {
  const { t } = useTranslation();
  const [f, setF] = useState({ host: '', port: '993', username: '', smtp_host: '', smtp_port: '465', smtp_security: 'ssl' });
  const [password, setPassword] = useState('');
  const [missing, setMissing] = useState(false);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF((v) => ({ ...v, [k]: e.target.value }));
  const submit = async () => {
    if (!f.host.trim() || !f.username.trim() || !password) { setMissing(true); return; }
    setMissing(false);
    const options: Record<string, unknown> = {
      host: f.host.trim(), port: Number(f.port) || 993, username: f.username.trim(),
    };
    if (f.smtp_host.trim()) {
      options.smtp_host = f.smtp_host.trim();
      options.smtp_port = Number(f.smtp_port) || 465;
      options.smtp_security = f.smtp_security;
    }
    const ok = await onSubmit(options, password);
    setPassword('');
    if (ok) onDone();
  };
  const field = (k: keyof typeof f, label: string, required = false, mono = false) => (
    <label className="block text-small text-[var(--color-text-muted)]">
      {label}{required ? ' *' : ''}
      <input className={`mt-1 ${INPUT} ${mono ? 'font-mono' : ''}`} value={f[k]} onChange={set(k)} spellCheck={false}
        data-testid={`inbox-imap-${k}`} />
    </label>
  );
  return (
    <div className="mt-3 max-w-[520px] rounded-xl border border-[var(--color-border)] p-4" data-testid="inbox-add-imap-form">
      <p className="text-small text-[var(--color-text-dim)]">{t('ui.InboxOverlay.channels.manualImapHint')}</p>
      <div className="mt-3 grid grid-cols-[1fr_96px] gap-3">
        {field('host', t('ui.InboxOverlay.channels.imapHost'), true, true)}
        {field('port', t('ui.InboxOverlay.channels.imapPort'))}
      </div>
      <div className="mt-3 grid grid-cols-2 gap-3">
        {field('username', t('ui.InboxOverlay.channels.imapUser'), true)}
        <label className="block text-small text-[var(--color-text-muted)]">
          {t('ui.InboxOverlay.channels.imapPassword')} *
          <input type="password" autoComplete="off" className={`mt-1 ${INPUT}`} value={password}
            onChange={(e) => setPassword(e.target.value)} data-testid="inbox-imap-password" />
        </label>
      </div>
      <div className="mt-3 grid grid-cols-[1fr_96px_96px] gap-3">
        {field('smtp_host', t('ui.InboxOverlay.channels.smtpHost'), false, true)}
        {field('smtp_port', t('ui.InboxOverlay.channels.smtpPort'))}
        <label className="block text-small text-[var(--color-text-muted)]">
          {t('ui.InboxOverlay.channels.smtpSecurity')}
          <select className={`mt-1 ${INPUT}`} value={f.smtp_security} onChange={set('smtp_security')} data-testid="inbox-imap-smtp_security">
            <option value="ssl">{t('ui.InboxOverlay.channels.smtpSsl')}</option>
            <option value="starttls">{t('ui.InboxOverlay.channels.smtpStarttls')}</option>
          </select>
        </label>
      </div>
      {missing && <p className="mt-2 text-small text-[var(--color-status-error)]">{t('ui.InboxOverlay.channels.required')}</p>}
      {refusal && <RefusalLine r={refusal} />}
      <div className="mt-3 flex justify-end gap-2">
        <button type="button" className="btn-md btn-ghost" onClick={onDone}>{t('ui.InboxOverlay.channels.cancel')}</button>
        <button type="button" className="btn-md btn-primary" disabled={busy} onClick={() => void submit()}
          data-testid="inbox-add-imap-submit">{t('ui.InboxOverlay.channels.add')}</button>
      </div>
    </div>
  );
}
