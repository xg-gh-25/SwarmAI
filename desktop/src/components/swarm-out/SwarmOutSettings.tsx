/**
 * SwarmOutSettings — the Swarm Out tab's settings view: the one place for where tasks run
 * (design docs/design/2026-11-20-swarm-out-multi-carrier.md §6.8; mock
 * Knowledge/Mockups/2026-11-23-swarm-out-single-entry.html v3). It replaced the left-nav
 * Hive page and the Hive count row of Settings.
 *
 * It holds only what the person decides; starting, stopping, updating and keeping
 * Elastic Workers on standby happen by themselves (core/hive_idle, the standby loop), so
 * nothing here asks for them. Four zones, in the order of those decisions:
 *  ① the AWS account the tasks run in (one line; changing it is Settings' job);
 *  ② Elastic Workers (AgentCore Runtime): the standby switch (PUT /hive/elastic/standby,
 *     the person's), and in the old multi-account mode the account it uses (chosen while
 *     there is none yet, saved at once on standby; the account it runs in once there is
 *     one); size, cost, version and its state as one line each;
 *  ③ resident Hives (EC2): one row per Hive (version, state, login info, restart, delete;
 *     a broken one shows why and offers retry, which rebuilds it after a confirm),
 *     "+ add one" naming the monthly cost before anything is created (and, when the account
 *     cannot build one, what is missing: the permission template, a default VPC to create),
 *     the instance type for new Hives, the idle stop time (hive_idle_stop_minutes), the region;
 *  ④ what SwarmAI does by itself and what only the person does.
 * Restart is stop then start (the person's routes): the one way out of a Hive that hangs
 * while it holds a run, which the agent's restart refuses.
 */
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { awsAccountService, problemOf, type AwsAccountView, type ElasticStatus } from '../../services/awsAccount';
import { hiveService, hiveAccountView, type HiveAccount, type HiveInstance } from '../../services/hive';
import { settingsService } from '../../services/settings';
import { OPEN_SETTINGS_EVENT } from '../common/settingsEvents';
import { AuthDisplay, DeployHiveDialog, retryPrompt } from '../settings/hiveComponents';
import { CapabilityLine } from '../awsAccount/HiveFleet';
import { hiveStatusLabel } from './live';

const muted = 'text-[var(--color-text-muted)]';
const link = 'text-[var(--color-primary)] hover:underline disabled:opacity-50';
const IDLE_CHOICES = [30, 60, 120, 240, 480, 0];
const DEFAULT_TYPE = 'm7g.xlarge';

function Section({ title, kind, lead, children, testId }: {
  title: string; kind?: string; lead?: string; children: React.ReactNode; testId: string;
}) {
  return (
    <section className="pb-5 mb-5 border-b border-[var(--color-border)] last:border-b-0" data-testid={testId}>
      <h3 className="m-0 text-[14px] font-semibold flex items-baseline gap-2">
        {title}{kind && <span className={`font-mono text-[11.5px] font-normal ${muted}`} data-testid={`${testId}-kind`}>{kind}</span>}
      </h3>
      {lead && <p className="mt-1 mb-3 text-[12.5px] text-[var(--color-text-secondary)]">{lead}</p>}
      {children}
    </section>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[150px_minmax(0,1fr)] items-center gap-3 py-1.5 text-[12.5px]">
      <span className={muted}>{label}</span><span className="min-w-0">{children}</span>
    </div>
  );
}

export function SwarmOutSettings() {
  const { t } = useTranslation();
  const [view, setView] = useState<AwsAccountView | null>(null);
  const [elastic, setElastic] = useState<ElasticStatus | null>(null);
  const [instances, setInstances] = useState<HiveInstance[]>([]);
  const [accounts, setAccounts] = useState<HiveAccount[]>([]);
  const [idle, setIdle] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<{ id: string; what: 'login' | 'delete' | 'retry' } | null>(null);
  const [adding, setAdding] = useState(false);
  const [deployLegacy, setDeployLegacy] = useState(false);
  const [chosen, setChosen] = useState('');

  const load = useCallback(async () => {
    const [v, e, list, accts, cfg] = await Promise.allSettled([
      awsAccountService.get(), awsAccountService.elasticStatus(), hiveService.listInstances(),
      hiveService.listAccounts(), settingsService.getAPIConfiguration()]);
    if (v.status === 'fulfilled') setView(v.value);
    if (e.status === 'fulfilled') setElastic(e.value);
    if (list.status === 'fulfilled') setInstances(list.value);
    if (accts.status === 'fulfilled') setAccounts(accts.value);
    if (cfg.status === 'fulfilled') {
      const m = cfg.value.hiveIdleStopMinutes;
      setIdle(typeof m === 'number' ? m : 120);
    }
  }, []);
  useEffect(() => { void load(); }, [load]);
  // the account chosen before (several accounts): shown, never blank once saved
  useEffect(() => { if (elastic?.chosen_account) setChosen((c) => c || elastic.chosen_account || ''); }, [elastic?.chosen_account]);
  // while something changes (a Hive being built or deleted, Elastic Workers turning on)
  const changing = instances.some((i) => ['pending', 'provisioning', 'installing', 'deleting', 'updating', 'starting', 'stopping'].includes(i.status))
    || (elastic?.hives ?? []).some((h) => ['enabling', 'upgrading', 'disabling'].includes(h.status))
    || (elastic?.standby === true && (elastic?.hives.length ?? 0) === 0);
  useEffect(() => {
    if (!changing) return undefined;
    const timer = setInterval(() => { void load(); }, 10000);
    return () => clearInterval(timer);
  }, [changing, load]);

  const act = async (run: () => Promise<unknown>) => {
    setBusy(true); setError(null);
    try { await run(); setOpen(null); setAdding(false); } catch (e) { setError(problemOf(e).message); }
    finally { setBusy(false); void load(); }
  };

  const fleet = view?.hive;
  const swarmaiMode = !!fleet && fleet.state !== 'unavailable';
  const legacy = hiveAccountView(accounts);
  const severalAccounts = !swarmaiMode && legacy.deployable.length > 1;
  const type = fleet?.instance_type ?? DEFAULT_TYPE;
  const prices = fleet?.prices ?? {};
  const price = prices[type];
  const ec2 = instances.filter((i) => i.status !== 'deleted');
  const e = elastic?.hives[0];
  const size = elastic?.size;

  return (
    <div className="max-w-[760px]" data-testid="swarm-out-settings">
      <Section title={t('swarmOut.settings.account')} testId="so-set-account">
        <Field label={t('swarmOut.settings.runsIn')}>
          {view?.account?.account_id
            ? <>{t('swarmOut.settings.accountLine', { id: view.account.account_id, region: view.account.region ?? '' })}{' '}</>
            : <span className={muted}>{t('swarmOut.settings.noAccount')}{' '}</span>}
          <button type="button" className={link}
            onClick={() => window.dispatchEvent(new CustomEvent(OPEN_SETTINGS_EVENT, { detail: { tab: 'aws-account' } }))}>
            {t('swarmOut.settings.changeInSettings')}
          </button>
        </Field>
      </Section>

      <Section title="Elastic Workers" kind="AgentCore Runtime" lead={t('swarmOut.settings.elasticLead')} testId="so-set-elastic">
        <Field label={t('swarmOut.settings.standby')}>
          <label className="inline-flex items-center gap-2 cursor-pointer">
            <input type="checkbox" role="switch" aria-checked={elastic?.standby === true} checked={elastic?.standby === true}
              disabled={busy || !elastic || (severalAccounts && !chosen && elastic.standby !== true)} data-testid="so-elastic-switch"
              onChange={(ev) => void act(() => awsAccountService.setElasticStandby(ev.target.checked, severalAccounts ? chosen || undefined : undefined))} />
            <span>{elastic?.standby ? t('swarmOut.settings.on') : t('swarmOut.settings.off')}</span>
            <span className={muted}>· {t('swarmOut.settings.standbyNote')}</span>
          </label>
        </Field>
        {severalAccounts && (
          <Field label={t('swarmOut.settings.elasticAccount')}>
            {e ? (
              // it runs in one account: that one is shown, never changed here
              <span data-testid="so-elastic-account-fixed">
                {(() => { const a = accounts.find((x) => x.id === e.account_ref); return a ? (a.label || a.accountId) : e.account_ref; })()}
              </span>
            ) : (
              <select className="border border-[var(--color-border)] rounded-md px-2 py-1 bg-[var(--color-card)]" value={chosen}
                disabled={busy} data-testid="so-elastic-account"
                onChange={(ev) => {
                  const v = ev.target.value;
                  setChosen(v);
                  // on standby the choice is saved at once: the standby round waits for it
                  if (v && elastic?.standby) void act(() => awsAccountService.setElasticStandby(true, v));
                }}>
                <option value="">{t('swarmOut.settings.chooseAccount')}</option>
                {legacy.deployable.map((a) => <option key={a.id} value={a.id}>{a.label || a.accountId}</option>)}
              </select>
            )}
          </Field>
        )}
        {size && <Field label={t('swarmOut.settings.eachMachine')}>{t('swarmOut.settings.size', { vcpu: size.vcpu_peak, mem: size.memory_gib_peak, disk: size.disk_gib })}</Field>}
        <Field label={t('swarmOut.settings.cost')}>{t('swarmOut.settings.elasticCost')}</Field>
        <Field label={t('swarmOut.settings.version')}>
          {(e?.image_version ?? elastic?.host_version ?? '—')}<span className={muted}> · {t('swarmOut.settings.followsVersion')}</span>
        </Field>
        <Field label={t('swarmOut.settings.state')}>
          <span data-testid="so-elastic-state">
            {e ? t(`swarmOut.settings.elasticStatus.${e.status}`, { defaultValue: e.status })
              : elastic?.standby ? t('swarmOut.settings.preparing') : t('swarmOut.settings.off')}
          </span>
          {e?.status === 'failed' && e.error_message && <span className="block text-[var(--color-error)] break-words">{e.error_message}</span>}
          {!e && elastic?.standby && (elastic.reason || elastic.standby_last) && (
            <span className={`block break-words ${muted}`} data-testid="so-elastic-why">{elastic.reason ?? elastic.standby_last}</span>)}
        </Field>
      </Section>

      <Section title={t('swarmOut.settings.resident')} kind="EC2" lead={t('swarmOut.settings.residentLead')} testId="so-set-hives">
        <div className="rounded-lg border border-[var(--color-border)] mb-3" data-testid="so-hive-list">
          {ec2.map((h) => (
            <div key={h.id} className="px-3.5 py-2 border-b border-[var(--color-border)] last:border-b-0 text-[12.5px]" data-testid="so-hive-row">
              <div className="flex items-center gap-3">
                <span className="w-[120px] font-medium truncate">{h.name}</span>
                <span className={`w-[150px] ${muted}`}>{h.version ?? '—'} · {t('swarmOut.settings.autoUpdate')}</span>
                <span className="flex-1 text-[var(--color-text-secondary)]">{hiveStatusLabel(h.status)}</span>
                <button type="button" className={link} disabled={busy} data-testid="so-hive-login"
                  onClick={() => setOpen(open?.id === h.id && open.what === 'login' ? null : { id: h.id, what: 'login' })}>{t('swarmOut.settings.login')}</button>
                <button type="button" className={link} disabled={busy || h.status !== 'running'} data-testid="so-hive-restart"
                  title={t('swarmOut.settings.restartNote')}
                  onClick={() => void act(async () => { await hiveService.stopInstance(h.id); await hiveService.startInstance(h.id); })}>
                  {t('swarmOut.settings.restart')}</button>
                {h.status === 'error' && (
                  <button type="button" className={link} disabled={busy} data-testid="so-hive-retry"
                    onClick={() => setOpen({ id: h.id, what: 'retry' })}>{t('common.button.retry')}</button>
                )}
                <button type="button" className="text-[var(--color-error)] hover:underline disabled:opacity-50" disabled={busy}
                  data-testid="so-hive-delete" onClick={() => setOpen({ id: h.id, what: 'delete' })}>{t('swarmOut.settings.delete')}</button>
              </div>
              {/* why it is broken, so the person can choose retry (rebuild) or delete */}
              {h.status === 'error' && h.errorMessage && (
                <div className="mt-1 text-[12px] text-[var(--color-error)] break-words" data-testid="so-hive-error">{h.errorMessage}</div>
              )}
              {open?.id === h.id && open.what === 'retry' && (
                <div className="mt-2 flex flex-wrap items-center gap-3" data-testid="so-hive-retry-confirm">
                  <span className="text-[var(--color-text-secondary)] whitespace-pre-line">{retryPrompt(h)}</span>
                  <button type="button" className="btn-md btn-primary disabled:opacity-50" disabled={busy} data-testid="so-hive-retry-go"
                    onClick={() => void act(() => hiveService.retryInstance(h.id, !!h.ec2InstanceId))}>{t('common.button.retry')}</button>
                  <button type="button" className={link} onClick={() => setOpen(null)}>{t('awsConnect.cancel')}</button>
                </div>
              )}
              {open?.id === h.id && open.what === 'login' && <div className="mt-2"><AuthDisplay instance={h} /></div>}
              {open?.id === h.id && open.what === 'delete' && (
                <div className="mt-2 flex flex-wrap items-center gap-3" data-testid="so-hive-delete-confirm">
                  <span className="text-[var(--color-text-secondary)]">{t('swarmOut.settings.deleteNote', { name: h.name })}</span>
                  <button type="button" className="btn-md btn-primary disabled:opacity-50" disabled={busy} data-testid="so-hive-delete-go"
                    onClick={() => void act(() => hiveService.deleteInstance(h.id))}>{t('swarmOut.settings.delete')}</button>
                  <button type="button" className={link} onClick={() => setOpen(null)}>{t('awsConnect.cancel')}</button>
                </div>
              )}
            </div>
          ))}
          <div className="px-3.5 py-2 text-[12.5px] flex flex-wrap items-center gap-3">
            {!adding ? (
              <button type="button" className={link} disabled={busy} data-testid="so-hive-add"
                onClick={() => (swarmaiMode ? setAdding(true) : setDeployLegacy(true))}>+ {t('swarmOut.settings.addOne')}</button>
            ) : (
              <>
                <button type="button" className="btn-md btn-primary disabled:opacity-50" disabled={busy} data-testid="so-hive-add-go"
                  onClick={() => void act(() => awsAccountService.setFleet({ desired_count: (fleet?.counted ?? 0) + 1 }))}>
                  {t('swarmOut.settings.addConfirm', { usd: price ?? '?', account: view?.account?.account_id ?? '' })}</button>
                <button type="button" className={link} onClick={() => setAdding(false)}>{t('awsConnect.cancel')}</button>
              </>
            )}
            {!adding && price != null && <span className={muted}>{t('swarmOut.settings.addCost', { usd: price })}</span>}
          </div>
        </div>
        {/* what is missing for building one (a permission, a default VPC to create) */}
        {fleet?.state === 'cannot_build' && fleet.capability && (
          <div className="mb-3" data-testid="so-hive-cannot"><CapabilityLine cap={fleet.capability} checking={false} onView={setView} /></div>
        )}
        {swarmaiMode && (
          <Field label={t('swarmOut.settings.type')}>
            <select className="border border-[var(--color-border)] rounded-md px-2 py-1 bg-[var(--color-card)]" value={type} disabled={busy}
              data-testid="so-hive-type" onChange={(ev) => void act(() => awsAccountService.setFleet({ instance_type: ev.target.value }))}>
              {Object.keys(prices).length ? Object.keys(prices).map((k) => <option key={k} value={k}>{k}</option>) : <option value={type}>{type}</option>}
            </select>
            <span className={muted}>　{t('swarmOut.settings.typeNote')}</span>
          </Field>
        )}
        <Field label={t('swarmOut.settings.idle')}>
          <select className="border border-[var(--color-border)] rounded-md px-2 py-1 bg-[var(--color-card)]" value={idle ?? 120} disabled={busy || idle === null}
            data-testid="so-idle" onChange={(ev) => { const n = Number(ev.target.value); void act(async () => { await settingsService.updateAPIConfiguration({ hiveIdleStopMinutes: n }); setIdle(n); }); }}>
            {IDLE_CHOICES.map((m) => <option key={m} value={m}>{m === 0 ? t('swarmOut.settings.never') : t('swarmOut.settings.minutes', { count: m })}</option>)}
          </select>
          <span className={muted}>　{t('swarmOut.settings.idleNote')}</span>
        </Field>
        {fleet?.region && <Field label={t('swarmOut.settings.region')}>{fleet.region}</Field>}
      </Section>

      <Section title={t('swarmOut.settings.byItself')} testId="so-set-auto">
        <ul className={`m-0 pl-4 text-[12px] ${muted}`}>
          {(['dispatch', 'idle', 'update', 'restart', 'elastic'] as const).map((k) => <li key={k}>{t(`swarmOut.settings.auto.${k}`)}</li>)}
        </ul>
        <h3 className="mt-4 mb-1 text-[14px] font-semibold">{t('swarmOut.settings.onlyYou')}</h3>
        <ul className={`m-0 pl-4 text-[12px] ${muted}`}>
          {(['add', 'login', 'elastic', 'type'] as const).map((k) => <li key={k}>{t(`swarmOut.settings.yours.${k}`)}</li>)}
        </ul>
      </Section>

      {error && <p className="text-[12.5px] text-[var(--color-error)] break-words" data-testid="so-settings-error">{error}</p>}
      {deployLegacy && (
        <DeployHiveDialog accounts={legacy.deployable} onClose={() => setDeployLegacy(false)}
          onDeployed={() => { setDeployLegacy(false); void load(); }} />
      )}
    </div>
  );
}
