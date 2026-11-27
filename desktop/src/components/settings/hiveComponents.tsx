/**
 * hiveComponents — the shared Hive UI building blocks, extracted from HiveTab.tsx
 * (run_b450108e, R4/R27) so every surface consumes ONE source:
 *   • Swarm Out › settings (swarm-out/SwarmOutSettings) — who can sign in, the legacy deploy dialog
 *   • Settings → Swarm AWS Account — the Hives left in other accounts (AccountCard)
 *
 * Extraction is behavior-preserving: every export below is moved verbatim from the
 * former HiveTab.tsx (deploy progress, instance card, account card, dialogs, status maps,
 * error helper). HiveAccess (who can sign in) gives each person their own
 * browser key, shown once, revocable one by one; no shared password is shown.
 *
 * Dialog note: AddAccountDialog + DeployHiveDialog are `fixed inset-0 z-50` centered
 * modals. Per CSS spec a `transform` on an ancestor makes it the containing block for
 * `position:fixed`, so inside a transformed panel (an OverlayHost overlay) the dialog
 * centers over that panel; with no transformed ancestor (the Settings tab, the Swarm Out
 * tab) it pins to the viewport.
 */
import { useState, useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
// the app's i18next instance (initialised in src/i18n, imported by main.tsx)
import i18n from 'i18next';
import { hiveService, HiveAccount, HiveInstance, VerifyResult, type HiveAccess as HiveAccessEntry, type HiveAccessGrant, TRANSITIONAL_STATUSES, getDeploySteps, isNewerVersion, MAX_HIVES } from '../../services/hive';
import { openExternal } from '../../utils/openExternal';
import { copyToClipboard } from '../../utils/clipboard';
import { isApiError } from '../../services/api';
import { useDialogLayer } from '../../hooks/useLayer';
import { HiveBrains } from './HiveBrains';

/** Extract the most useful message from an API error. */
export function friendlyError(e: unknown): string {
  if (isApiError(e)) {
    // Prefer .detail (backend's actual message), fall back to .message
    return e.detail || e.message;
  }
  return e instanceof Error ? e.message : String(e);
}

export const STATUS_COLORS: Record<string, string> = {
  running: 'text-green-400',
  stopped: 'text-yellow-400',
  pending: 'text-blue-400',
  provisioning: 'text-blue-400',
  installing: 'text-blue-400',
  updating: 'text-blue-400',
  error: 'text-red-400',
  deleting: 'text-red-400',
};

export const STATUS_ICONS: Record<string, string> = {
  running: '\u{1F7E2}',
  stopped: '\u{1F534}',
  pending: '\u{1F535}',
  provisioning: '\u{1F535}',
  installing: '\u{1F535}',
  updating: '\u{1F535}',
  error: '❌',
  deleting: '\u{1F534}',
};

export const INSTANCE_SIZES = [
  { id: 'm7g.large', labelKey: 'ui.hiveComponents.sizeLarge', cost: '~$60/mo' },
  { id: 'm7g.xlarge', labelKey: 'ui.hiveComponents.sizeXlarge', cost: '~$119/mo' },
  { id: 'm7g.2xlarge', labelKey: 'ui.hiveComponents.size2xlarge', cost: '~$238/mo' },
];

// ── Hive Section ──────────────────────────────────────────────────

export function HiveSection({
  title, instances, onAction, accounts, onDeploy, showEmpty, limitReached,
}: {
  title: string;
  instances: HiveInstance[];
  onAction: () => void;
  accounts?: HiveAccount[];
  onDeploy?: () => void;
  showEmpty?: boolean;
  /** MAX_HIVES already counted: deploying is disabled */
  limitReached?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <section className="bg-[var(--color-card)] rounded-lg p-6">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-semibold text-[var(--color-text)]">{title}</h3>
        {onDeploy && (
          <button
            onClick={onDeploy}
            disabled={!accounts || accounts.length === 0 || limitReached}
            title={limitReached ? t('ui.hiveComponents.atMost', { max: MAX_HIVES }) : undefined}
            className="btn-md btn-primary disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <span className="material-symbols-outlined icon-sm">add</span>
            {t('ui.hiveComponents.deployNew')}
          </button>
        )}
      </div>

      {instances.length === 0 && showEmpty ? (
        <div className="text-center py-8">
          <span className="text-4xl">{'\u{1F41D}'}</span>
          <p className="text-sm text-[var(--color-text-muted)] mt-2">
            {!accounts || accounts.length === 0
              ? t('ui.hiveComponents.emptyNoAccount')
              : t('ui.hiveComponents.emptyNoHive')}
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {instances.map((inst) => (
            <InstanceCard key={inst.id} instance={inst} onAction={onAction} />
          ))}
        </div>
      )}
    </section>
  );
}

// ── Latest release (one lookup for every card, refreshed at most every 10 minutes) ──

const LATEST_TTL_MS = 10 * 60_000;
let latestCache: { at: number; value: Promise<string | null> } | null = null;

/** Test hook: forget the cached release. */
export function resetLatestHiveVersion(): void { latestCache = null; }

export function useLatestHiveVersion(): string | null {
  const [latest, setLatest] = useState<string | null>(null);
  useEffect(() => {
    if (!latestCache || Date.now() - latestCache.at > LATEST_TTL_MS) {
      const entry = {
        at: Date.now(),
        // a failed lookup is not cached: the next card mount asks again
        value: hiveService.latestVersion().catch(() => {
          if (latestCache === entry) latestCache = null;
          return null;
        }),
      };
      latestCache = entry;
    }
    let alive = true;
    void latestCache.value.then((v) => { if (alive) setLatest(v); });
    return () => { alive = false; };
  }, []);
  return latest;
}

/** What Retry will do, asked before it runs. A Hive whose instance still exists holds the
 *  person's data: Retry deletes it (after saving its disks as snapshots), so say so and
 *  point to Start. Without an instance it only cleans up a failed deploy. */
export function retryPrompt(inst: HiveInstance): string {
  if (inst.ec2InstanceId) {
    return i18n.t('ui.hiveComponents.retryDeletes', { name: inst.name });
  }
  return i18n.t('ui.hiveComponents.retryCleans', { name: inst.name });
}

// ── Instance Card ─────────────────────────────────────────────────

export function InstanceCard({ instance: inst, onAction }: { instance: HiveInstance; onAction: () => void }) {
  const { t } = useTranslation();
  const latest = useLatestHiveVersion();
  // set once the update request is accepted, until the list shows the Hive 'updating'
  const [updateSent, setUpdateSent] = useState(false);
  useEffect(() => { if (inst.status !== 'running') setUpdateSent(false); }, [inst.status]);
  const canUpdate = inst.status === 'running' && !updateSent && isNewerVersion(latest, inst.version);
  const [acting, setActing] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const isTransitional = TRANSITIONAL_STATUSES.includes(inst.status);

  const doAction = async (action: () => Promise<void>) => {
    setActing(true);
    setActionError(null);
    try { await action(); onAction(); }
    catch (e) { setActionError(friendlyError(e)); }
    finally { setActing(false); }
  };

  const url = inst.cloudfrontDomain
    ? `https://${inst.cloudfrontDomain}`
    : inst.ec2PublicIp ? `http://${inst.ec2PublicIp}` : null;

  return (
    <div className="p-4 bg-[var(--color-bg)] rounded-lg">
      {/* Header row */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span>{STATUS_ICONS[inst.status] || '⚪'}</span>
          <span className="text-sm font-medium text-[var(--color-text)]">{inst.name}</span>
          <span className={`text-xs ${STATUS_COLORS[inst.status] || ''}`}>{inst.status}</span>
          {isTransitional && (
            <span className="inline-block w-3 h-3 border-2 border-[var(--color-primary)] border-t-transparent rounded-full animate-spin" />
          )}
        </div>
        <div className="flex items-center gap-1.5">
          <button
            onClick={() => url && openExternal(url)}
            disabled={!url}
            title={url || t('ui.hiveComponents.noUrl')}
            className="btn-sm btn-soft disabled:opacity-30 disabled:cursor-not-allowed"
          >
            {t('ui.hiveComponents.open')} {'↗'}
          </button>
          {canUpdate && latest && (
            <button
              onClick={() => {
                if (confirm(t('ui.hiveComponents.confirmUpdate', { name: inst.name, from: inst.version, to: latest }))) {
                  doAction(async () => { await hiveService.updateInstance(inst.id, latest); setUpdateSent(true); });
                }
              }}
              disabled={acting}
              title={t('ui.hiveComponents.latestRelease', { version: latest })}
              className="btn-sm btn-primary disabled:opacity-50"
              data-testid="hive-update"
            >
              {t('ui.hiveComponents.updateTo', { version: latest })}
            </button>
          )}
          {inst.status === 'running' && (
            <button
              onClick={() => doAction(() => hiveService.stopInstance(inst.id))}
              disabled={acting}
              className="btn-sm btn-warning disabled:opacity-50"
            >
              {t('ui.hiveComponents.stop')}
            </button>
          )}
          {(inst.status === 'stopped' || (inst.status === 'error' && !!inst.ec2InstanceId)) && (
            <button
              onClick={() => doAction(() => hiveService.startInstance(inst.id))}
              disabled={acting}
              className="btn-sm btn-success disabled:opacity-50"
            >
              {t('ui.hiveComponents.start')}
            </button>
          )}
          {inst.status === 'error' && (
            <button
              onClick={() => {
                if (!confirm(retryPrompt(inst))) return;
                doAction(() => hiveService.retryInstance(inst.id, !!inst.ec2InstanceId));
              }}
              disabled={acting}
              className="btn-sm btn-secondary disabled:opacity-50"
            >
              {t('common.button.retry')}
            </button>
          )}
          {!isTransitional && (
            <button
              onClick={() => {
                if (confirm(t('ui.hiveComponents.confirmDelete', { name: inst.name }))) {
                  doAction(() => hiveService.deleteInstance(inst.id));
                }
              }}
              disabled={acting}
              className="btn-sm btn-danger disabled:opacity-50"
            >
              {t('common.button.delete')}
            </button>
          )}
        </div>
      </div>

      {/* Owner name for shared Hives */}
      {inst.ownerName && (
        <div className="text-xs text-[var(--color-text-muted)] mb-2">
          {t('ui.hiveComponents.owner', { name: inst.ownerName })}
        </div>
      )}

      {/* Meta row */}
      <div className="flex items-center gap-4 text-xs text-[var(--color-text-muted)] mb-2">
        {url && <code className="bg-[var(--color-card)] px-1.5 py-0.5 rounded select-all text-[11px]">{url}</code>}
        <span>{inst.instanceType}</span>
        <span>{inst.region}</span>
        {inst.version && <span>v{inst.version}</span>}
      </div>

      {/* Deploy progress for transitional states (an update has its own line) */}
      {inst.status === 'updating' ? (
        <p className="text-xs text-[var(--color-text-muted)] mt-2" data-testid="hive-updating">
          {t('ui.hiveComponents.updating')}
        </p>
      ) : isTransitional && <DeployProgress instance={inst} />}

      {/* Who can sign in (each person their own browser key) for running instances */}
      {inst.status === 'running' && <HiveAccess instance={inst} />}

      {/* Which Brains this Hive has and which can be sent here (project-agent-flow §6) */}
      {inst.status === 'running' && <HiveBrains instanceId={inst.id} />}

      {/* Action error */}
      {actionError && (
        <p className="text-xs text-red-400 mt-2 bg-red-500/10 px-2 py-1 rounded flex items-center justify-between">
          <span>{actionError}</span>
          <button onClick={() => setActionError(null)} className="text-red-400/60 hover:text-red-400 ml-2">✕</button>
        </p>
      )}

      {/* Deploy error message */}
      {inst.errorMessage && (
        <p className="text-xs text-red-400 mt-2 bg-red-500/10 px-2 py-1 rounded">{inst.errorMessage}</p>
      )}
    </div>
  );
}

// ── Deploy Progress ───────────────────────────────────────────────

export function DeployProgress({ instance }: { instance: HiveInstance }) {
  const { t } = useTranslation();
  const steps = getDeploySteps(instance);
  const currentIdx = steps.findIndex(s => !s.done);

  return (
    <div className="mt-2 space-y-1">
      {steps.map((step, i) => (
        <div key={step.labelKey} className="flex items-center gap-2 text-xs">
          {step.done ? (
            <span className="text-green-400 w-4 text-center">{'✓'}</span>
          ) : i === currentIdx ? (
            <span className="inline-block w-3 h-3 border-2 border-[var(--color-primary)] border-t-transparent rounded-full animate-spin ml-0.5" />
          ) : (
            <span className="w-4 text-center text-[var(--color-text-muted)]">{'○'}</span>
          )}
          <span className={step.done ? 'text-[var(--color-text-muted)]' : i === currentIdx ? 'text-[var(--color-text)]' : 'text-[var(--color-text-muted)]/50'}>
            {t(step.labelKey)}
          </span>
        </div>
      ))}
    </div>
  );
}

// ── Who can sign in (Hive access) ─────────────────────────────────

/** The address a person opens to reach a Hive in a browser, or '' when it has none yet. */
export function hiveUrl(inst: HiveInstance): string {
  return inst.cloudfrontDomain ? `https://${inst.cloudfrontDomain}` : inst.ec2PublicIp ? `http://${inst.ec2PublicIp}` : '';
}

/** The one-time result of adding a person: a dialog over everything, so the only password
 *  ever on screen is never lost inside a list. Only Done or Escape discards it: a click
 *  outside (or a text selection released outside) does not, and focus starts on the dialog
 *  itself, not on Done, so a repeated Enter from the name field cannot discard it either. */
function AccessGrantDialog({ grant, url, onClose }: { grant: HiveAccessGrant; url: string; onClose: () => void }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  useDialogLayer(true, dialogRef, onClose);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  const address = grant.url || url;
  const copyAll = async () => {
    if (await copyToClipboard(t('ui.hiveComponents.accessShareText', { url: address, user: grant.user, password: grant.password }))) {
      setCopied(true);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), 2000);
    }
  };
  // pt-11 + max-h-full: below the macOS title bar, like the other dialogs here
  return createPortal(
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 px-4 pb-4 pt-11" data-testid="hive-access-grant-scrim">
      <div ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="hive-access-grant-title" tabIndex={-1}
        className="bg-[var(--color-card)] border border-[var(--color-border)] rounded-xl p-5 w-[420px] max-w-full max-h-full overflow-y-auto outline-none"
        data-testid="hive-access-grant">
        <h3 id="hive-access-grant-title" className="m-0 text-heading font-semibold text-[var(--color-text)]">
          {t('ui.hiveComponents.accessGrantTitle', { name: grant.user })}</h3>
        <p className="mt-1 mb-4 text-small text-[var(--color-text-muted)]">{t('ui.hiveComponents.accessGrantLead', { name: grant.user })}</p>
        <div className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 px-3.5 py-3 rounded-lg bg-[var(--color-bg)] text-small min-w-0">
          <span className="text-[var(--color-text-muted)]">{t('ui.hiveComponents.accessUrl')}</span>
          <code className="text-[var(--color-text)] select-all break-all">{address}</code>
          <span className="text-[var(--color-text-muted)]">{t('ui.hiveComponents.accessUser')}</span>
          <code className="text-[var(--color-text)] select-all">{grant.user}</code>
          <span className="text-[var(--color-text-muted)]">{t('ui.hiveComponents.accessPassword')}</span>
          <code className="text-[var(--color-text)] select-all break-all" data-testid="hive-access-password">{grant.password}</code>
        </div>
        <p className="mt-3 mb-4 text-small text-[var(--color-status-warning)]">{t('ui.hiveComponents.accessShownOnce')}</p>
        <div className="flex justify-end gap-2">
          <button onClick={onClose} className="btn-sm btn-secondary" data-testid="hive-access-done">{t('ui.hiveComponents.accessDone')}</button>
          <button onClick={() => void copyAll()} className="btn-sm btn-primary" data-testid="hive-access-copy-all">
            {copied ? t('common.copy.copied') : t('ui.hiveComponents.accessCopyAll')}</button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

/** Rotate the Host's own machine key: rare (only when it may have leaked), so a text link
 *  that asks once. Everyone's browser keys keep working. */
export function HiveRotateKey({ instance: inst, long = false }: { instance: HiveInstance; long?: boolean }) {
  const { t } = useTranslation();
  const [state, setState] = useState<'idle' | 'confirm' | 'busy' | 'done'>('idle');
  const [error, setError] = useState<string | null>(null);
  const linkBtn = 'text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:underline disabled:opacity-50';
  if (inst.status !== 'running') return <span className="text-[var(--color-text-muted)]">{t('ui.hiveComponents.accessWhenRunning')}</span>;
  const go = async () => {
    setState('busy'); setError(null);
    try { await hiveService.rotateHostKey(inst.id); setState('done'); }
    catch (e) { setError(friendlyError(e)); setState('confirm'); }
  };
  return (
    <span className="inline-flex flex-wrap items-center gap-x-3 gap-y-1">
      {state === 'confirm' || state === 'busy' ? (
        <span className="inline-flex flex-wrap items-center gap-2" data-testid="hive-access-rotate-confirm">
          <span className="text-[var(--color-text-secondary)]">{t('ui.hiveComponents.rotateHostKeyNote')}</span>
          <button onClick={() => void go()} disabled={state === 'busy'} className="btn-sm btn-secondary disabled:opacity-50" data-testid="hive-access-rotate-go">
            {t('ui.hiveComponents.rotateHostKeyGo')}</button>
          <button onClick={() => setState('idle')} disabled={state === 'busy'} className={linkBtn}>{t('common.button.cancel')}</button>
        </span>
      ) : (
        <>
          <button onClick={() => { setState('confirm'); setError(null); }} className={linkBtn} data-testid="hive-access-rotate">
            {long ? t('ui.hiveComponents.rotateHostKey') : t('ui.hiveComponents.rotateHostKeyGo')}</button>
          <span className="text-[var(--color-text-muted)]">· {t('ui.hiveComponents.rotateHostKeyWhen')}</span>
          {state === 'done' && <span className="text-[var(--color-status-success)]">{t('ui.hiveComponents.rotateHostKeyDone')}</span>}
        </>
      )}
      {error && <span className="text-[var(--color-status-error)]" data-testid="hive-access-rotate-error">{error}</span>}
    </span>
  );
}

/** The person's decisions about who can sign in to one Hive: give someone access (each
 *  person their own browser key, its password shown ONCE, in a dialog), and take it away.
 *  The old shared password, while it still works, is the one line that asks for action.
 *  No stored password is ever shown here: the only password on screen is the one just
 *  created, and Done (or leaving) discards it. `extras` (the legacy Hive card) also shows
 *  the address and the Host key rotation, which the Swarm Out settings show under More. */
export function HiveAccess({ instance: inst, extras = true }: { instance: HiveInstance; extras?: boolean }) {
  const { t } = useTranslation();
  const running = inst.status === 'running';
  const [accesses, setAccesses] = useState<HiveAccessEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState('');
  const [grant, setGrant] = useState<HiveAccessGrant | null>(null);
  const [revoking, setRevoking] = useState<string | null>(null);
  // the list itself could not be read (not an action that failed): it says so, with Retry
  const [listFailed, setListFailed] = useState(false);
  const copyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const url = hiveUrl(inst);

  const reload = async () => {
    try { setAccesses(await hiveService.listAccess(inst.id)); setListFailed(false); }
    catch (e) { setError(friendlyError(e)); setListFailed(true); }
  };

  useEffect(() => {
    if (!running) return;
    let alive = true;
    setAccesses(null);
    hiveService.listAccess(inst.id)
      .then((rows) => { if (alive) { setAccesses(rows); setListFailed(false); } })
      .catch((e) => { if (alive) { setError(friendlyError(e)); setListFailed(true); } });
    return () => { alive = false; };
  }, [inst.id, running]);

  useEffect(() => () => { if (copyTimer.current) clearTimeout(copyTimer.current); }, []);

  const copyUrl = async () => {
    if (await copyToClipboard(url)) {
      setCopied(true);
      if (copyTimer.current) clearTimeout(copyTimer.current);
      copyTimer.current = setTimeout(() => setCopied(false), 2000);
    }
  };

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try { await action(); }
    catch (e) { setError(friendlyError(e)); }
    finally { setBusy(false); }
  };

  const submitAdd = () => {
    const n = name.trim();
    if (!/^[a-z0-9-]{1,32}$/.test(n)) { setError(t('ui.hiveComponents.accessNameInvalid')); return; }
    void run(async () => {
      const g = await hiveService.addAccess(inst.id, n);
      setGrant(g);
      setAdding(false);
      setName('');
      await reload();
    });
  };

  const created = (iso: string | null) => {
    if (!iso) return '';
    const d = new Date(iso);
    return Number.isNaN(d.getTime())
      ? ''
      : d.toLocaleDateString(i18n.language || undefined, { year: 'numeric', month: 'short', day: 'numeric' });
  };

  if (!running) {
    return (
      <div className="text-small text-[var(--color-text-muted)]" data-testid="hive-access">
        {t('ui.hiveComponents.accessWhenRunning')}
      </div>
    );
  }

  const linkBtn = 'text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-50';
  const legacy = (accesses ?? []).filter((a) => a.legacy);
  const people = (accesses ?? []).filter((a) => !a.legacy);
  const revokeConfirm = (user: string, label: string) => (
    <>
      <span className="text-[var(--color-text)]">{t('ui.hiveComponents.accessRevokeConfirm', { name: label })}</span>
      <button onClick={() => void run(async () => { await hiveService.revokeAccess(inst.id, user); setRevoking(null); await reload(); })}
        disabled={busy} className="btn-sm btn-danger disabled:opacity-50" data-testid="hive-access-revoke-go">
        {t('ui.hiveComponents.accessRevoke')}
      </button>
      <button onClick={() => setRevoking(null)} disabled={busy} className={linkBtn}>{t('common.button.cancel')}</button>
    </>
  );

  return (
    <div className="text-small flex flex-col gap-2 min-w-0" data-testid="hive-access">
      {extras && url && (
        <div className="flex items-center gap-2 min-w-0">
          <code className="text-[var(--color-text)] select-all truncate min-w-0">{url}</code>
          <button onClick={() => void copyUrl()} className="btn-sm btn-secondary shrink-0" data-testid="hive-access-copy-url">
            {copied ? t('common.copy.copied') : t('common.copy.copy')}
          </button>
        </div>
      )}

      {accesses === null ? (
        listFailed ? (
          <div className="text-[var(--color-text-muted)]" data-testid="hive-access-unreadable">
            {t('ui.hiveComponents.accessUnreadable')}{' '}
            <button onClick={() => { setListFailed(false); setError(null); void reload(); }} className="text-[var(--color-primary)] hover:underline"
              data-testid="hive-access-retry">{t('common.button.retry')}</button>
          </div>
        ) : <div className="text-[var(--color-text-muted)]">{t('ui.hiveComponents.accessReading')}</div>
      ) : (
        <ul className="m-0 p-0 list-none flex flex-col gap-2" data-testid="hive-access-list">
          {/* the old shared password: the one thing here that asks for action */}
          {legacy.map((a) => (
            <li key={a.user} className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-2.5 py-2 rounded-lg min-w-0"
              style={{ color: 'var(--color-status-warning)', background: 'color-mix(in srgb, var(--color-status-warning) 10%, transparent)' }}
              data-testid={`hive-access-row-${a.user}`}>
              {revoking === a.user ? revokeConfirm(a.user, t('ui.hiveComponents.accessLegacy')) : (
                <>
                  <span className="flex-1 min-w-0">
                    <span className="font-semibold">{t('ui.hiveComponents.accessLegacy')}</span>
                    <span> · {t('ui.hiveComponents.accessLegacyHint')}</span>
                  </span>
                  <button onClick={() => setRevoking(a.user)} disabled={busy}
                    className="btn-sm border disabled:opacity-50"
                    style={{ borderColor: 'color-mix(in srgb, var(--color-status-warning) 45%, transparent)', color: 'var(--color-status-warning)' }}
                    data-testid={`hive-access-revoke-${a.user}`}>
                    {t('ui.hiveComponents.accessLegacyRevoke')}
                  </button>
                </>
              )}
            </li>
          ))}
          {people.length === 0 && legacy.length === 0 && (
            <li className="text-[var(--color-text-muted)]" data-testid="hive-access-empty">{t('ui.hiveComponents.accessNone')}</li>
          )}
          {people.map((a) => {
            const label = a.label || a.user;
            return (
              <li key={a.user} className="flex flex-wrap items-center gap-x-3 gap-y-1 min-w-0" data-testid={`hive-access-row-${a.user}`}>
                {revoking === a.user ? revokeConfirm(a.user, label) : (
                  <>
                    <span className="text-[var(--color-text)] truncate">{label}</span>
                    {created(a.createdAt) && <span className="text-[var(--color-text-muted)]">{t('ui.hiveComponents.accessAdded', { date: created(a.createdAt) })}</span>}
                    <button onClick={() => setRevoking(a.user)} disabled={busy}
                      className="ml-auto text-[var(--color-status-error)] hover:underline disabled:opacity-50" data-testid={`hive-access-revoke-${a.user}`}>
                      {t('ui.hiveComponents.accessRevoke')}
                    </button>
                  </>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {adding ? (
        <div className="flex flex-col gap-1 max-w-[420px]">
          <div className="flex items-center gap-2">
            <input autoFocus value={name} onChange={(e) => setName(e.target.value.toLowerCase())} maxLength={32}
              onKeyDown={(e) => { if (e.key === 'Enter') submitAdd(); if (e.key === 'Escape') { setAdding(false); setName(''); } }}
              placeholder={t('ui.hiveComponents.accessNamePlaceholder')} aria-label={t('ui.hiveComponents.accessNamePlaceholder')}
              className="flex-1 min-w-0 px-2 py-1 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-md text-small text-[var(--color-text)] placeholder-[var(--color-text-muted)]/40 focus:outline-none focus:border-[var(--color-primary)]"
              data-testid="hive-access-name" />
            <button onClick={submitAdd} disabled={busy || !name.trim()} className="btn-sm btn-primary disabled:opacity-50" data-testid="hive-access-create">
              {busy ? '...' : t('ui.hiveComponents.accessCreate')}
            </button>
            <button onClick={() => { setAdding(false); setName(''); setError(null); }} disabled={busy} className={linkBtn}>{t('common.button.cancel')}</button>
          </div>
          <div className="text-[var(--color-text-muted)]">{t('ui.hiveComponents.accessNameHint')}</div>
        </div>
      ) : (
        <div>
          <button onClick={() => { setAdding(true); setError(null); }} disabled={busy || accesses === null}
            className="btn-sm border border-[var(--color-border)] hover:bg-[var(--color-hover)] disabled:opacity-50" data-testid="hive-access-add">
            + {t('ui.hiveComponents.accessAdd')}
          </button>
        </div>
      )}

      {error && (
        <p className="m-0 text-[var(--color-status-error)] flex items-center justify-between gap-2" data-testid="hive-access-error">
          <span className="min-w-0 break-words">{error}</span>
          <button onClick={() => setError(null)} className="opacity-60 hover:opacity-100" aria-label={t('swarmOut.setup.dismiss')}>✕</button>
        </p>
      )}

      {extras && <div className="text-[11px]"><HiveRotateKey instance={inst} long /></div>}

      {grant && <AccessGrantDialog grant={grant} url={url} onClose={() => setGrant(null)} />}
    </div>
  );
}

// ── Account Card ───────────────────────────────────────────────────

export function AccountCard({ account: acc, onDelete }: { account: HiveAccount; onDelete: () => void }) {
  const { t } = useTranslation();
  const [verifying, setVerifying] = useState(false);
  const [verifyResult, setVerifyResult] = useState<VerifyResult | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleVerify = async () => {
    setVerifying(true);
    setVerifyResult(null);
    try {
      const result = await hiveService.verifyAccount(acc.id);
      setVerifyResult(result);
    } catch (e) {
      setVerifyResult({ success: false, accountId: acc.accountId, checks: {}, error: String(e) });
    } finally {
      setVerifying(false);
    }
  };

  const handleDelete = async () => {
    if (!confirm(t('ui.hiveComponents.confirmDeleteAccount'))) return;
    setDeleting(true);
    setError(null);
    try {
      await hiveService.deleteAccount(acc.id);
      onDelete();
    } catch (e) {
      setError(friendlyError(e));
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="p-3 bg-[var(--color-bg)] rounded-lg">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className={`w-1.5 h-1.5 rounded-full ${acc.verifiedAt ? 'bg-green-400' : 'bg-[var(--color-text-muted)]'}`} />
          <div>
            <div className="text-sm text-[var(--color-text)]">
              <code>{acc.accountId}</code>
              {acc.label && acc.label !== acc.accountId && (
                <span className="text-[var(--color-text-muted)] ml-2">({acc.label})</span>
              )}
            </div>
            <div className="text-xs text-[var(--color-text-muted)]" data-testid="hive-account-detail">
              {acc.role === 'swarmai'
                ? <>{t('ui.hiveComponents.roleSwarmai')}</>
                : acc.role === 'disconnected'
                  ? <>{t('ui.hiveComponents.roleDisconnected')}</>
                : acc.role === 'other'
                  ? <>{t('ui.hiveComponents.roleOther', { count: acc.hiveCount })}</>
                  : <>{acc.authMethod} {'·'} {acc.defaultRegion}</>}
              {acc.verifiedAt && <span className="text-green-400 ml-2">{'✓'} {t('ui.hiveComponents.verified')}</span>}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleVerify}
            disabled={verifying}
            className="btn-sm btn-secondary disabled:opacity-50"
          >
            {verifying ? '...' : t('ui.hiveComponents.verify')}
          </button>
          {acc.role !== 'swarmai' && acc.role !== 'disconnected' && (
            <button
              onClick={handleDelete}
              disabled={deleting}
              aria-label={t('ui.hiveComponents.deleteAccount')}
              className="px-2 py-1 text-xs text-red-400 hover:bg-red-500/20 rounded disabled:opacity-50"
            >
              {deleting ? '...' : '✕'}
            </button>
          )}
        </div>
      </div>
      {verifyResult && (
        <div className={`mt-2 text-xs ${verifyResult.success ? 'text-green-400' : 'text-red-400'}`}>
          {verifyResult.success
            ? (verifyResult.checks?.sts as { merged?: boolean } | undefined)?.merged
              ? t('ui.hiveComponents.merged')
              : t('ui.hiveComponents.allPassed')
            : `✗ ${verifyResult.error || t('ui.hiveComponents.failed')}`}
        </div>
      )}
      {error && (
        <p className="text-xs text-red-400 mt-2 bg-red-500/10 px-2 py-1 rounded flex items-center justify-between">
          <span>{error}</span>
          <button onClick={() => setError(null)} className="text-red-400/60 hover:text-red-400 ml-2">✕</button>
        </p>
      )}
    </div>
  );
}

// ── Add Account Dialog ─────────────────────────────────────────────

export function AddAccountDialog({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const { t } = useTranslation();
  const [accountId, setAccountId] = useState('');
  const [label, setLabel] = useState('');
  const [region, setRegion] = useState('us-east-1');
  const [authMethod, setAuthMethod] = useState<'access_keys' | 'sso'>('access_keys');
  const [accessKeyId, setAccessKeyId] = useState('');
  const [secretKey, setSecretKey] = useState('');
  const [ssoProfile, setSsoProfile] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  // A dialog layer: Escape closes it (not the Hive overlay behind it), except while
  // saving; focus moves to the first field and back on close.
  const dialogRef = useRef<HTMLDivElement>(null);
  useDialogLayer(true, dialogRef, () => { if (!saving) onClose(); }, { first: true });

  const handleSave = async () => {
    if (!accountId.match(/^\d{12}$/)) { setError(t('ui.hiveComponents.accountIdInvalid')); return; }
    setSaving(true);
    setError('');
    try {
      const authConfig: Record<string, string> = authMethod === 'access_keys'
        ? { access_key_id: accessKeyId, secret_access_key: secretKey }
        : { profile: ssoProfile };
      await hiveService.createAccount({ accountId, label, authMethod, authConfig, defaultRegion: region });
      onSaved();
    } catch (e) {
      setError(friendlyError(e));
    } finally {
      setSecretKey('');
      setAccessKeyId('');
      setSaving(false);
    }
  };

  // pt-11 + max-h-full: kept below the macOS title bar (top 28px, TitleBarStyle::Overlay),
  // its height capped at the scrim's own box rather than a share of 100vh (same as Modal).
  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 px-4 pb-4 pt-11" onClick={saving ? undefined : onClose}>
      <div ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="hive-add-account-title" tabIndex={-1}
        className="bg-[var(--color-card)] rounded-xl p-6 w-[440px] max-w-full max-h-full overflow-y-auto outline-none" onClick={e => e.stopPropagation()}>
        <h3 id="hive-add-account-title" className="text-lg font-semibold text-[var(--color-text)] mb-4">{t('ui.hiveComponents.addAccountTitle')}</h3>

        <div className="space-y-3">
          <div>
            <label className="block text-xs text-[var(--color-text-muted)] mb-1">{t('ui.hiveComponents.accountId')}</label>
            <input value={accountId} onChange={(e) => setAccountId(e.target.value)} placeholder={t('ui.hiveComponents.accountIdPlaceholder')}
              className="w-full px-3 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)]/40 focus:outline-none focus:border-[var(--color-primary)]" />
          </div>
          <div>
            <label className="block text-xs text-[var(--color-text-muted)] mb-1">{t('ui.hiveComponents.labelOptional')}</label>
            <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder={t('ui.hiveComponents.labelPlaceholder')}
              className="w-full px-3 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)]/40 focus:outline-none focus:border-[var(--color-primary)]" />
          </div>

          <div className="grid grid-cols-2 gap-2">
            {(['access_keys', 'sso'] as const).map((m) => (
              <button key={m} onClick={() => setAuthMethod(m)}
                className={`p-2 rounded-lg text-left text-xs ${authMethod === m ? 'bg-[var(--color-primary)]/20 border-2 border-[var(--color-primary)]' : 'bg-[var(--color-bg)] border border-[var(--color-border)]'}`}>
                <div className="font-medium text-[var(--color-text)]">{m === 'access_keys' ? t('ui.hiveComponents.accessKeys') : t('ui.hiveComponents.ssoProfile')}</div>
              </button>
            ))}
          </div>

          {authMethod === 'access_keys' && (
            <>
              <input value={accessKeyId} onChange={(e) => setAccessKeyId(e.target.value)} placeholder={t('ui.hiveComponents.accessKeyId')}
                className="w-full px-3 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)]/40 focus:outline-none focus:border-[var(--color-primary)]" />
              <input type="password" value={secretKey} onChange={(e) => setSecretKey(e.target.value)} placeholder={t('ui.hiveComponents.secretKey')}
                className="w-full px-3 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)]/40 focus:outline-none focus:border-[var(--color-primary)]" />
            </>
          )}
          {authMethod === 'sso' && (
            <input value={ssoProfile} onChange={(e) => setSsoProfile(e.target.value)} placeholder={t('ui.hiveComponents.ssoProfileName')}
              className="w-full px-3 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)]/40 focus:outline-none focus:border-[var(--color-primary)]" />
          )}

          <div>
            <label className="block text-xs text-[var(--color-text-muted)] mb-1">{t('ui.hiveComponents.region')}</label>
            <select value={region} onChange={(e) => setRegion(e.target.value)}
              className="w-full px-3 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-sm text-[var(--color-text)]">
              <option value="us-east-1">{t('ui.hiveComponents.regionUsEast1')}</option>
              <option value="us-west-2">{t('ui.hiveComponents.regionUsWest2')}</option>
              <option value="eu-west-1">{t('ui.hiveComponents.regionEuWest1')}</option>
              <option value="ap-northeast-1">{t('ui.hiveComponents.regionApNortheast1')}</option>
            </select>
          </div>

          {error && <p className="text-xs text-red-400">{error}</p>}
        </div>

        <div className="flex justify-end gap-2 mt-6">
          <button onClick={onClose} disabled={saving} className="px-4 py-2 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-50">{t('common.button.cancel')}</button>
          <button onClick={handleSave} disabled={saving || !accountId}
            className="btn-lg btn-primary disabled:opacity-50">
            {saving ? t('common.button.saving') : t('ui.hiveComponents.addAccount')}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Deploy Hive Dialog ─────────────────────────────────────────────

export function DeployHiveDialog({ accounts, onClose, onDeployed }: { accounts: HiveAccount[]; onClose: () => void; onDeployed: () => void }) {
  const { t } = useTranslation();
  const [name, setName] = useState('');
  const [ownerName, setOwnerName] = useState('');
  const [accountRef, setAccountRef] = useState(accounts[0]?.id ?? '');
  const [instanceType, setInstanceType] = useState('m7g.xlarge');
  const [deploying, setDeploying] = useState(false);
  const [error, setError] = useState('');

  // A dialog layer, like AddAccountDialog: Escape closes it except while deploying.
  const dialogRef = useRef<HTMLDivElement>(null);
  useDialogLayer(true, dialogRef, () => { if (!deploying) onClose(); }, { first: true });

  const handleDeploy = async () => {
    if (!name.match(/^[a-z][a-z0-9]([a-z0-9-]{0,60}[a-z0-9])?$/)) {
      setError(t('ui.hiveComponents.nameInvalid'));
      return;
    }
    setDeploying(true);
    setError('');
    try {
      const selectedAccount = accounts.find(a => a.id === accountRef);
      await hiveService.createInstance({
        name,
        accountRef,
        region: selectedAccount?.defaultRegion ?? 'us-east-1',
        instanceType,
        ownerName: ownerName || undefined,
        hiveType: ownerName ? 'shared' : 'my',
      });
      onDeployed();
    } catch (e) {
      setError(friendlyError(e));
    } finally {
      setDeploying(false);
    }
  };

  // pt-11 + max-h-full: kept below the macOS title bar (top 28px), same as AddAccountDialog.
  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 px-4 pb-4 pt-11" onClick={deploying ? undefined : onClose}>
      <div ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="hive-deploy-title" tabIndex={-1}
        className="bg-[var(--color-card)] rounded-xl p-6 w-[440px] max-w-full max-h-full overflow-y-auto outline-none" onClick={e => e.stopPropagation()}>
        <h3 id="hive-deploy-title" className="text-lg font-semibold text-[var(--color-text)] mb-4">{t('ui.hiveComponents.deployNew')}</h3>

        <div className="space-y-3">
          <div>
            <label className="block text-xs text-[var(--color-text-muted)] mb-1">{t('ui.hiveComponents.hiveName')}</label>
            <input value={name} onChange={(e) => setName(e.target.value.toLowerCase())} placeholder={t('ui.hiveComponents.hiveNamePlaceholder')}
              className="w-full px-3 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)]/40 focus:outline-none focus:border-[var(--color-primary)]" />
          </div>

          {/* Owner name field */}
          <div>
            <label className="block text-xs text-[var(--color-text-muted)] mb-1">{t('ui.hiveComponents.ownerOptional')}</label>
            <input value={ownerName} onChange={(e) => setOwnerName(e.target.value)} placeholder={t('ui.hiveComponents.ownerPlaceholder')}
              className="w-full px-3 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)]/40 focus:outline-none focus:border-[var(--color-primary)]" />
          </div>

          <div>
            <label className="block text-xs text-[var(--color-text-muted)] mb-1">{t('ui.hiveComponents.awsAccount')}</label>
            {accounts.length === 1 && accounts[0].role === 'swarmai' ? (
              <div className="px-3 py-2 text-sm text-[var(--color-text)]" data-testid="hive-deploy-account">
                <code>{accounts[0].accountId}</code>
                <span className="text-[var(--color-text-muted)] ml-2">{t('ui.hiveComponents.swarmaiAccount')}</span>
              </div>
            ) : (
              <select value={accountRef} onChange={(e) => setAccountRef(e.target.value)}
                className="w-full px-3 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-sm text-[var(--color-text)]">
                {accounts.map((a) => (
                  <option key={a.id} value={a.id}>{a.accountId} {a.label ? `(${a.label})` : ''}</option>
                ))}
              </select>
            )}
          </div>

          <div>
            <label className="block text-xs text-[var(--color-text-muted)] mb-1">{t('ui.hiveComponents.instanceSize')}</label>
            <div className="space-y-1.5">
              {INSTANCE_SIZES.map((s) => (
                <button key={s.id} onClick={() => setInstanceType(s.id)}
                  className={`w-full p-2.5 rounded-lg text-left text-xs flex justify-between ${instanceType === s.id ? 'bg-[var(--color-primary)]/20 border-2 border-[var(--color-primary)]' : 'bg-[var(--color-bg)] border border-[var(--color-border)]'}`}>
                  <span className="text-[var(--color-text)]">{t(s.labelKey)}</span>
                  <span className="text-[var(--color-text-muted)]">{s.cost}</span>
                </button>
              ))}
            </div>
          </div>

          {error && <p className="text-xs text-red-400">{error}</p>}
        </div>

        <div className="flex justify-end gap-2 mt-6">
          <button onClick={onClose} disabled={deploying} className="px-4 py-2 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-50">{t('common.button.cancel')}</button>
          <button onClick={handleDeploy} disabled={deploying || !name || !accountRef}
            className="btn-lg btn-primary disabled:opacity-50">
            {deploying ? t('ui.hiveComponents.deploying') : t('ui.hiveComponents.deploy')}
          </button>
        </div>
      </div>
    </div>
  );
}
