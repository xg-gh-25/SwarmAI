/**
 * SwarmOutSettings — the Swarm Out tab's settings view: the one place for where tasks run
 * (design docs/design/2026-11-20-swarm-out-multi-carrier.md §6.8; visual design mock
 * Knowledge/Mockups/2026-11-23-swarm-out-dashboard-redesign-v6.html, the Settings view).
 * Everything about Hives is here: nothing on this view sends the person to the App's
 * Settings, and the App's Settings no longer show Hives.
 *
 * It holds only what the person decides; starting, stopping, updating and keeping
 * Elastic Workers on standby happen by themselves (core/hive_idle, the standby loop).
 * Cards, in the order of those decisions:
 *  ① the AWS account: its state, Account ID (copyable) and Region, whether it can build
 *    Hives (the gap and the permission template when not) and run Elastic Workers;
 *    connecting or changing it opens the same sign-in flow as the App's Settings
 *    (ConnectAccount), here, with the warning that model calls follow the account;
 *  ② Elastic Workers (AgentCore Runtime): the standby switch (PUT /hive/elastic/standby,
 *    the person's), in the old multi-account mode the account it uses, its state, size,
 *    cost and version;
 *  ③ resident Hives (EC2): one row per Hive (its state, from its record and, for a running
 *    one, whether the fleet poll could reach it); a row opens to why it is broken, its
 *    sign-in (AuthDisplay), its Brains (HiveBrains: which are out of date, one Sync),
 *    restart, retry (a rebuild, after a confirm) and delete; "+ add one" names the monthly
 *    cost before anything is created (when the account cannot build one, what is missing);
 *  ④ new Hives and idle time: the instance type, the idle stop time, the region;
 *  ⑤ Hives in other accounts (desktop only; moved from the App's AWS account page);
 *  then what SwarmAI does by itself, folded.
 * Restart is stop then start (the person's routes): the one way out of a Hive that hangs
 * while it holds a run, which the agent's restart refuses.
 */
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { awsAccountService, problemOf, type AwsAccountView, type ElasticStatus } from '../../services/awsAccount';
import { hiveService, hiveAccountView, type HiveAccount, type HiveInstance } from '../../services/hive';
import { settingsService } from '../../services/settings';
import type { SwarmOutFleet } from '../../services/swarmOut';
import { isDesktop } from '../../services/tauri';
import { copyToClipboard } from '../../utils/clipboard';
import ConnectAccount from '../awsAccount/ConnectAccount';
import { CapabilityLine } from '../awsAccount/HiveFleet';
import { AWS_REGION_OPTIONS } from '../awsAccount/regions';
import { HiveBrains } from '../settings/HiveBrains';
import { AccountCard, AddAccountDialog, AuthDisplay, DeployHiveDialog, retryPrompt } from '../settings/hiveComponents';
import { hiveStatusLabel } from './live';
import { TONE_VAR, type Tone } from './tone';
import { groupedId } from './where';

const muted = 'text-[var(--color-text-muted)]';
const dim = 'text-[var(--color-text-dim)]';
const link = 'text-[var(--color-primary)] hover:underline disabled:opacity-50';
const card = 'so-card rounded-xl border border-[var(--color-border)] bg-[var(--color-card)]';
const select = 'border border-[var(--color-border)] rounded-md px-2 py-1 bg-[var(--color-card)] text-body disabled:opacity-50';
const IDLE_CHOICES = [30, 60, 120, 240, 480, 0];
const DEFAULT_TYPE = 'm7g.xlarge';
const CHANGING = ['pending', 'provisioning', 'installing', 'deleting', 'updating', 'starting', 'stopping'];

/** A card's head: a small mark, the title (and its kind), one line under it, an action. */
function Head({ mark, markStyle, title, kind, lead, children }: {
  mark: string; markStyle: React.CSSProperties; title: string; kind?: React.ReactNode; lead?: React.ReactNode; children?: React.ReactNode;
}) {
  return (
    <div className="flex items-center gap-3 px-[18px] py-3.5">
      <span aria-hidden className="w-8 h-8 rounded-[9px] grid place-items-center text-small font-semibold flex-none" style={markStyle}>{mark}</span>
      <div className="flex-1 min-w-0">
        <div className="text-heading font-semibold flex items-center gap-2 flex-wrap">{title}{kind}</div>
        {lead && <div className={`text-small ${muted}`}>{lead}</div>}
      </div>
      {children}
    </div>
  );
}

function Row({ label, children, testId }: { label: string; children: React.ReactNode; testId?: string }) {
  return (
    <div className="flex items-center gap-3 px-[18px] py-2.5 min-h-[42px] border-t border-[var(--color-border)] text-body" data-testid={testId}>
      <span className={`w-[150px] flex-none ${muted}`}>{label}</span>
      <span className="flex-1 min-w-0 text-[var(--color-text-secondary)]">{children}</span>
    </div>
  );
}

export function Kind({ ac, children }: { ac?: boolean; children: React.ReactNode }) {
  return (
    <span className="px-1.5 rounded text-[10.5px] leading-[15px] font-semibold"
      style={ac ? { color: 'var(--color-zone-work)', background: 'color-mix(in srgb, var(--color-zone-work) 12%, transparent)' }
        : { color: 'var(--color-text-secondary)', background: 'var(--color-bg-chrome)', boxShadow: 'inset 0 0 0 1px var(--color-border)' }}>{children}</span>
  );
}

const Dot = ({ tone }: { tone: Tone }) => (
  <span aria-hidden className="inline-block w-2 h-2 rounded-full flex-none" style={{ background: TONE_VAR[tone] }} />
);


/** A region's name for people ("US East (N. Virginia)"), or nothing when unknown. */
const regionName = (id: string | null | undefined) => AWS_REGION_OPTIONS.find((r) => r.id === id)?.name ?? null;

/** ① The AWS account the tasks run in. */
function AccountCardView({ view, elastic, onView, failed }: {
  view: AwsAccountView | null; elastic: ElasticStatus | null; onView: (v: AwsAccountView) => void;
  /** the account could not be read (the card says so; nothing is offered on a guess) */
  failed: boolean;
}) {
  const { t } = useTranslation();
  const [connecting, setConnecting] = useState(false);
  const [copied, setCopied] = useState(false);
  const acct = view?.account ?? null;
  const id = acct?.account_id ?? null;
  const region = view?.region ?? acct?.region ?? null;
  const state = view?.state ?? null;
  const ok = state === 'connected';
  const hiveCap = view?.capabilities?.hive ?? null;
  const e = elastic?.hives[0];
  const elasticTone: Tone = e?.status === 'ready' ? 'ok' : e?.status === 'failed' ? 'error' : e ? 'attention' : 'unknown';
  return (
    <section className={card} data-testid="so-set-account">
      <Head mark="AWS" markStyle={{ color: TONE_VAR.attention, background: `color-mix(in srgb, ${TONE_VAR.attention} 14%, transparent)` }}
        title={t('swarmOut.setup.account')}
        lead={<span className="inline-flex items-center gap-1.5" data-testid="so-account-state">
          <Dot tone={ok ? 'ok' : state === 'unconnected' || !state ? 'unknown' : 'attention'} />
          {state ? t(`swarmOut.setup.state.${state}`, { defaultValue: state }) : failed ? t('swarmOut.setup.unreadable') : t('swarmOut.loading')}</span>}>
        {view && view.supported !== false && (
          <button type="button" className={ok ? 'btn-sm border border-[var(--color-border)] hover:bg-[var(--color-hover)]' : 'btn-sm btn-primary'}
            onClick={() => setConnecting((c) => !c)} aria-expanded={connecting} data-testid="so-account-change">
            {acct ? t('swarmOut.setup.change') : t('swarmOut.setup.connect')}
          </button>
        )}
      </Head>
      {id && (
        <Row label={t('swarmOut.setup.accountId')} testId="so-account-id">
          <span className="inline-flex items-baseline gap-3 flex-wrap">
            <span className="font-mono text-body text-[var(--color-text)] tracking-[0.02em]" data-testid="so-account-id-value">{groupedId(id)}</span>
            <button type="button" className={`text-small ${link}`} data-testid="so-account-id-copy"
              onClick={() => void copyToClipboard(id).then((done) => { if (done) { setCopied(true); window.setTimeout(() => setCopied(false), 1500); } })}>
              {copied ? t('swarmOut.setup.copied') : t('swarmOut.setup.copy')}
            </button>
          </span>
        </Row>
      )}
      {region && (
        <Row label={t('swarmOut.setup.region')} testId="so-account-region">
          <span className="inline-flex items-baseline gap-3 flex-wrap">
            <span className="font-mono text-body text-[var(--color-text)]">{region}</span>
            <span className={`text-small ${dim}`}>{[regionName(region), t('swarmOut.setup.regionNote')].filter(Boolean).join(' · ')}</span>
          </span>
        </Row>
      )}
      {acct && (
        <Row label={t('swarmOut.setup.canDo')} testId="so-account-can">
          <span className="flex flex-wrap gap-x-4 gap-y-1 text-small">
            <span className="inline-flex items-center gap-1.5" data-testid="so-cap-hive" data-status={hiveCap?.status ?? 'unknown'}>
              <Dot tone={hiveCap?.status === 'ok' ? 'ok' : hiveCap ? 'attention' : 'unknown'} />{t('swarmOut.setup.capHive')}
              {hiveCap && hiveCap.status !== 'ok' && <span style={{ color: TONE_VAR.attention }}>· {t(`settings.awsAccount.capStatus.${hiveCap.status}`)}</span>}
            </span>
            <span className="inline-flex items-center gap-1.5" data-testid="so-cap-elastic">
              <Dot tone={elasticTone} />{t('swarmOut.setup.capElastic')}
              {!e && <span className={dim}>· {t('swarmOut.setup.elasticOff')}</span>}
            </span>
          </span>
        </Row>
      )}
      {acct && hiveCap && hiveCap.status !== 'ok' && hiveCap.status !== 'not_applicable' && (
        <div className="px-[18px] pb-3"><CapabilityLine cap={hiveCap} checking={false} onView={onView} /></div>
      )}
      <div className="so-expand" data-open={connecting}>
        <div>
          {connecting && (
            <div className="px-[18px] py-3 border-t border-[var(--color-border)]" data-testid="so-account-connect">
              {acct && <p className="m-0 mb-3 text-small" style={{ color: TONE_VAR.attention }}>{t('swarmOut.setup.changeWarn')}</p>}
              <ConnectAccount mode="settings" start="pick"
                onConnected={(v) => { onView(v); setConnecting(false); }} onCancel={() => setConnecting(false)} />
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

/** A resident Hive's tone: its record's state, and for a running one whether the panel's
 *  fleet poll reached it. */
function residentTone(h: HiveInstance, fleet: SwarmOutFleet | null): { tone: Tone; word: string | null } {
  if (h.status === 'error' || h.status === 'failed') return { tone: 'error', word: hiveStatusLabel(h.status) };
  if (h.status !== 'running') return { tone: CHANGING.includes(h.status) ? 'attention' : 'unknown', word: hiveStatusLabel(h.status) };
  const f = fleet?.hives.find((x) => x.name === h.name);
  // up but not answering: its word is "cannot be reached" (null here, said by the caller)
  if (f && f.reachable === false) return { tone: 'error', word: null };
  return { tone: f?.saturated ? 'attention' : 'ok', word: hiveStatusLabel(h.status) };
}

export function SwarmOutSettings({ fleet = null, onConnected }: {
  fleet?: SwarmOutFleet | null;
  /** the account was connected or changed here (the panel's run view follows it) */
  onConnected?: (view: AwsAccountView) => void;
}) {
  const { t } = useTranslation();
  const [view, setView] = useState<AwsAccountView | null>(null);
  const [elastic, setElastic] = useState<ElasticStatus | null>(null);
  const [instances, setInstances] = useState<HiveInstance[]>([]);
  // what the first load could read: null until it answered (no "none" on a guess)
  const [read, setRead] = useState<{ account: boolean; hives: boolean } | null>(null);
  const [accounts, setAccounts] = useState<HiveAccount[]>([]);
  const [idle, setIdle] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [confirm, setConfirm] = useState<{ id: string; what: 'delete' | 'retry' } | null>(null);
  const [adding, setAdding] = useState(false);
  const [addingAccount, setAddingAccount] = useState(false);
  const [deployLegacy, setDeployLegacy] = useState(false);
  const [chosen, setChosen] = useState('');

  const load = useCallback(async () => {
    const [v, e, list, accts, cfg] = await Promise.allSettled([
      awsAccountService.get(), awsAccountService.elasticStatus(), hiveService.listInstances(),
      hiveService.listAccounts(), settingsService.getAPIConfiguration()]);
    if (v.status === 'fulfilled') setView(v.value);
    if (e.status === 'fulfilled') setElastic(e.value);
    if (list.status === 'fulfilled') setInstances(list.value);
    setRead({ account: v.status === 'fulfilled', hives: list.status === 'fulfilled' });
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
  const changing = instances.some((i) => CHANGING.includes(i.status))
    || (elastic?.hives ?? []).some((h) => ['enabling', 'upgrading', 'disabling'].includes(h.status))
    || (elastic?.standby === true && (elastic?.hives.length ?? 0) === 0);
  useEffect(() => {
    if (!changing) return undefined;
    const timer = setInterval(() => { void load(); }, 10000);
    return () => clearInterval(timer);
  }, [changing, load]);

  const act = async (run: () => Promise<unknown>) => {
    setBusy(true); setError(null);
    try { await run(); setConfirm(null); setAdding(false); } catch (e) { setError(problemOf(e).message); }
    finally { setBusy(false); void load(); }
  };
  const toggle = (id: string) => setExpanded((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });

  const fleetStatus = view?.hive;
  const swarmaiMode = !!fleetStatus && fleetStatus.state !== 'unavailable';
  const legacy = hiveAccountView(accounts);
  const severalAccounts = !swarmaiMode && legacy.deployable.length > 1;
  const type = fleetStatus?.instance_type ?? DEFAULT_TYPE;
  const prices = fleetStatus?.prices ?? {};
  const price = prices[type];
  const ec2 = instances.filter((i) => i.status !== 'deleted');
  const e = elastic?.hives[0];
  const size = elastic?.size;
  const region = fleetStatus?.region ?? view?.region ?? null;
  const others = legacy.swarmai ? legacy.shown.filter((a) => a.role !== 'swarmai') : legacy.shown;
  const standbyOn = elastic?.standby === true;
  const needChoice = severalAccounts && !chosen && !standbyOn;
  const desired = fleetStatus?.desired ?? null;
  const counted = fleetStatus?.counted ?? 0;
  const short = fleetStatus?.state === 'short' && (fleetStatus.short ?? 0) > 0 ? fleetStatus.short ?? 0 : 0;
  const connected = (v: AwsAccountView) => { setView(v); onConnected?.(v); void load(); };

  return (
    <div className="max-w-[760px] flex flex-col gap-8" data-testid="swarm-out-settings">
      {/* what failed, at the top: every action here is one click away from this line */}
      {error && (
        <div className="flex items-start gap-3 px-4 py-2.5 rounded-lg text-body break-words" role="alert" data-testid="so-settings-error"
          style={{ color: TONE_VAR.error, background: `color-mix(in srgb, ${TONE_VAR.error} 8%, transparent)` }}>
          <span className="flex-1 min-w-0">{error}</span>
          <button type="button" className={`text-small ${muted} hover:underline`} onClick={() => setError(null)}>{t('swarmOut.setup.dismiss')}</button>
        </div>
      )}
      <AccountCardView view={view} elastic={elastic} onView={connected} failed={read?.account === false} />

      <section className={card} data-testid="so-set-elastic">
        <Head mark="AC" markStyle={{ color: 'var(--color-zone-work)', background: 'color-mix(in srgb, var(--color-zone-work) 14%, transparent)' }}
          title="Elastic Workers" kind={<Kind ac>{t('swarmOut.board.elastic')}</Kind>} lead={t('swarmOut.settings.elasticLead')}>
          <button type="button" role="switch" aria-checked={standbyOn} aria-label={t('swarmOut.settings.standby')}
            className="so-switch" data-testid="so-elastic-switch"
            title={needChoice ? t('swarmOut.setup.chooseFirst') : t('swarmOut.settings.standbyNote')}
            disabled={busy || !elastic || needChoice}
            onClick={() => void act(() => awsAccountService.setElasticStandby(!standbyOn, severalAccounts ? chosen || undefined : undefined))} />
        </Head>
        <Row label={t('swarmOut.settings.state')}>
          <span className="inline-flex items-center gap-1.5" data-testid="so-elastic-state">
            <Dot tone={e?.status === 'ready' ? 'ok' : e?.status === 'failed' ? 'error' : e || standbyOn ? 'attention' : 'unknown'} />
            {e ? t(`swarmOut.settings.elasticStatus.${e.status}`, { defaultValue: e.status })
              : standbyOn ? t('swarmOut.settings.preparing') : t('swarmOut.settings.off')}
          </span>
          {e?.status === 'failed' && e.error_message && <span className="block text-small break-words" style={{ color: TONE_VAR.error }}>{e.error_message}</span>}
          {!e && standbyOn && (elastic?.reason || elastic?.standby_last) && (
            <span className={`block text-small break-words ${muted}`} data-testid="so-elastic-why">{elastic.reason ?? elastic.standby_last}</span>)}
        </Row>
        {severalAccounts && (
          <Row label={t('swarmOut.settings.elasticAccount')}>
            {e ? (
              // it runs in one account: that one is shown, never changed here
              <span data-testid="so-elastic-account-fixed">
                {(() => { const a = accounts.find((x) => x.id === e.account_ref); return a ? (a.label || a.accountId) : e.account_ref; })()}
              </span>
            ) : (
              <select className={select} value={chosen} disabled={busy} data-testid="so-elastic-account"
                onChange={(ev) => {
                  const v = ev.target.value;
                  setChosen(v);
                  // on standby the choice is saved at once: the standby round waits for it
                  if (v && standbyOn) void act(() => awsAccountService.setElasticStandby(true, v));
                }}>
                <option value="">{t('swarmOut.settings.chooseAccount')}</option>
                {legacy.deployable.map((a) => <option key={a.id} value={a.id}>{a.label || a.accountId}</option>)}
              </select>
            )}
          </Row>
        )}
        {size && <Row label={t('swarmOut.settings.eachMachine')}>{t('swarmOut.settings.size', { vcpu: size.vcpu_peak, mem: size.memory_gib_peak, disk: size.disk_gib })}</Row>}
        <Row label={t('swarmOut.settings.cost')}>{t('swarmOut.settings.elasticCost')}</Row>
        <Row label={t('swarmOut.settings.version')}>
          {(e?.image_version ?? elastic?.host_version ?? '—')}<span className={muted}> · {t('swarmOut.settings.followsVersion')}</span>
        </Row>
      </section>

      <section className={card} data-testid="so-set-hives">
        <Head mark="EC2" markStyle={{ color: 'var(--color-text-secondary)', background: 'var(--color-bg-chrome)' }}
          title={t('swarmOut.settings.resident')} kind={<Kind>{t('swarmOut.board.ec2')}</Kind>} lead={t('swarmOut.settings.residentLead')} />
        <div data-testid="so-hive-list">
          {ec2.length === 0 && (
            <div className={`px-[18px] py-3 border-t border-[var(--color-border)] text-body ${muted}`} data-testid="so-hives-none">
              {!read ? t('swarmOut.loading') : read.hives ? t('swarmOut.setup.noHives') : t('swarmOut.setup.hivesUnreadable')}
            </div>
          )}
          {ec2.map((h) => {
            const st = residentTone(h, fleet);
            const word = st.word ?? t('swarmOut.fleet.unreachable');
            const broken = h.status === 'error' || h.status === 'failed';
            // a broken or unreachable Hive opens by itself: why it is broken is what to read
            const byDefault = st.tone === 'error';
            const isOpen = (expanded.has(h.id) ? !byDefault : byDefault) || confirm?.id === h.id;
            return (
              <div key={h.id} className="border-t border-[var(--color-border)]" data-testid="so-hive-row" data-tone={st.tone}>
                <div className="flex items-center gap-3 px-[18px] py-3 cursor-pointer hover:bg-[var(--color-hover)] transition-colors"
                  onClick={() => toggle(h.id)}>
                  <button type="button" aria-expanded={isOpen} aria-label={h.name} data-testid="so-hive-toggle"
                    className={`w-3 text-small ${dim} transition-transform ${isOpen ? 'rotate-90' : ''}`}
                    onClick={(ev) => { ev.stopPropagation(); toggle(h.id); }}>›</button>
                  <Dot tone={st.tone} />
                  <div className="flex-1 min-w-0">
                    <div className="text-body font-semibold truncate">{h.name}</div>
                    <div className={`text-small ${muted} truncate`}>{[h.instanceType, h.region, h.version, t('swarmOut.settings.autoUpdate')].filter(Boolean).join(' · ')}</div>
                  </div>
                  <span className="text-small whitespace-nowrap" style={st.tone === 'error' || st.tone === 'attention' ? { color: TONE_VAR[st.tone] } : undefined}
                    data-testid="so-hive-state">{word}</span>
                  {broken && (
                    <button type="button" className="btn-sm border border-[var(--color-border)] hover:bg-[var(--color-hover)]" disabled={busy}
                      data-testid="so-hive-retry" onClick={(ev) => { ev.stopPropagation(); setConfirm({ id: h.id, what: 'retry' }); }}>
                      {t('common.button.retry')}</button>
                  )}
                </div>
                <div className="so-expand" data-open={isOpen}>
                  <div>
                    {isOpen && (
                      <div className="pl-[44px] pr-[18px] pb-3.5 pt-0.5 text-small" data-testid="so-hive-detail">
                        {/* why it is broken, so the person can choose retry (rebuild) or delete */}
                        {broken && h.errorMessage && (
                          <div className="mb-2.5 px-2.5 py-2 rounded-md break-words"
                            style={{ color: TONE_VAR.error, background: `color-mix(in srgb, ${TONE_VAR.error} 7%, transparent)` }}
                            data-testid="so-hive-error">{h.errorMessage}</div>
                        )}
                        {confirm?.id === h.id && confirm.what === 'retry' && (
                          <div className="mb-2.5 flex flex-wrap items-center gap-3" data-testid="so-hive-retry-confirm">
                            <span className="text-[var(--color-text-secondary)] whitespace-pre-line">{retryPrompt(h)}</span>
                            <button type="button" className="btn-sm btn-primary disabled:opacity-50" disabled={busy} data-testid="so-hive-retry-go"
                              onClick={() => void act(() => hiveService.retryInstance(h.id, !!h.ec2InstanceId))}>{t('swarmOut.setup.retryConfirm')}</button>
                            <button type="button" className={link} onClick={() => setConfirm(null)}>{t('swarmOut.setup.cancel')}</button>
                          </div>
                        )}
                        {/* each part names itself (its sign-in, its Brains) */}
                        <div className="flex flex-col gap-2 min-w-0">
                          <div className="min-w-0" data-testid="so-hive-login"><AuthDisplay instance={h} /></div>
                          {h.status === 'running' && <div className="min-w-0" data-testid="so-hive-brains"><HiveBrains instanceId={h.id} /></div>}
                        </div>
                        <div className="mt-3 flex flex-wrap items-center gap-2">
                          <button type="button" className="btn-sm border border-[var(--color-border)] hover:bg-[var(--color-hover)] disabled:opacity-50"
                            disabled={busy || h.status !== 'running'} data-testid="so-hive-restart" title={t('swarmOut.settings.restartNote')}
                            onClick={() => void act(async () => { await hiveService.stopInstance(h.id); await hiveService.startInstance(h.id); })}>
                            {t('swarmOut.settings.restart')}</button>
                          <button type="button" className="btn-sm border disabled:opacity-50" disabled={busy} data-testid="so-hive-delete"
                            style={{ color: TONE_VAR.error, borderColor: `color-mix(in srgb, ${TONE_VAR.error} 35%, var(--color-border))` }}
                            onClick={() => setConfirm({ id: h.id, what: 'delete' })}>{t('swarmOut.settings.delete')}</button>
                          {confirm?.id === h.id && confirm.what === 'delete' && (
                            <span className="inline-flex flex-wrap items-center gap-3" data-testid="so-hive-delete-confirm">
                              <span className="text-[var(--color-text-secondary)]">{t('swarmOut.settings.deleteNote', { name: h.name })}</span>
                              <button type="button" className="btn-sm btn-primary disabled:opacity-50" disabled={busy} data-testid="so-hive-delete-go"
                                onClick={() => void act(() => hiveService.deleteInstance(h.id))}>{t('swarmOut.settings.delete')}</button>
                              <button type="button" className={link} onClick={() => setConfirm(null)}>{t('swarmOut.setup.cancel')}</button>
                            </span>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
          {short > 0 && desired != null && (
            <div className="flex flex-wrap items-center gap-3 px-[18px] py-3 border-t border-[var(--color-border)] text-body" data-testid="so-hive-short"
              style={{ background: `color-mix(in srgb, ${TONE_VAR.attention} 7%, transparent)` }}>
              <span className="flex-1 min-w-0" style={{ color: TONE_VAR.attention }}>{t('swarmOut.fleet.short', { count: short })}</span>
              <button type="button" className="btn-sm btn-primary disabled:opacity-50" disabled={busy} data-testid="so-hive-short-build"
                onClick={() => void act(() => awsAccountService.setFleet({ desired_count: desired }))}>
                {price != null ? t('swarmOut.setup.buildShortCost', { count: short, usd: price }) : t('swarmOut.setup.buildShort', { count: short })}</button>
              {!fleetStatus?.dismissed && (
                <button type="button" className={link} disabled={busy} data-testid="so-hive-short-later"
                  onClick={() => void act(() => awsAccountService.setFleet({ dismissed: true }))}>{t('swarmOut.setup.later')}</button>
              )}
            </div>
          )}
          <div className="flex flex-wrap items-center gap-3 px-[18px] py-3 border-t border-[var(--color-border)] text-body">
            {short > 0 ? null : !adding ? (
              <button type="button" className={`font-semibold ${link}`} disabled={busy} data-testid="so-hive-add"
                onClick={() => (swarmaiMode ? setAdding(true) : setDeployLegacy(true))}>+ {t('swarmOut.settings.addOne')}</button>
            ) : (
              <>
                <button type="button" className="btn-sm btn-primary disabled:opacity-50" disabled={busy} data-testid="so-hive-add-go"
                  onClick={() => void act(() => awsAccountService.setFleet({ desired_count: Math.max(desired ?? 0, counted) + 1 }))}>
                  {price != null ? t('swarmOut.settings.addConfirm', { usd: price, account: view?.account?.account_id ?? '' })
                    : t('swarmOut.setup.addConfirmNoCost', { account: view?.account?.account_id ?? '' })}</button>
                <button type="button" className={link} onClick={() => setAdding(false)}>{t('swarmOut.setup.cancel')}</button>
              </>
            )}
            {!adding && short === 0 && price != null && <span className={`text-small ${muted}`}>{t('swarmOut.settings.addCost', { usd: price })}</span>}
          </div>
          {/* what is missing for building one (a permission, a default VPC to create) */}
          {fleetStatus?.state === 'cannot_build' && fleetStatus.capability && (
            <div className="px-[18px] pb-3" data-testid="so-hive-cannot"><CapabilityLine cap={fleetStatus.capability} checking={false} onView={setView} /></div>
          )}
        </div>
      </section>

      <section className={card} data-testid="so-set-rules">
        <Head mark="⚙" markStyle={{ color: 'var(--color-primary)', background: 'color-mix(in srgb, var(--color-primary) 12%, transparent)' }}
          title={t('swarmOut.setup.rules')} lead={t('swarmOut.setup.rulesLead')} />
        {swarmaiMode && (
          <Row label={t('swarmOut.settings.type')}>
            <select className={select} value={type} disabled={busy} data-testid="so-hive-type"
              onChange={(ev) => void act(() => awsAccountService.setFleet({ instance_type: ev.target.value }))}>
              {Object.keys(prices).length ? Object.keys(prices).map((k) => <option key={k} value={k}>{k}</option>) : <option value={type}>{type}</option>}
            </select>
          </Row>
        )}
        <Row label={t('swarmOut.settings.idle')}>
          <span className="inline-flex flex-wrap items-center gap-x-3 gap-y-1">
            <select className={select} value={idle ?? 120} disabled={busy || idle === null} data-testid="so-idle"
              onChange={(ev) => { const n = Number(ev.target.value); void act(async () => { await settingsService.updateAPIConfiguration({ hiveIdleStopMinutes: n }); setIdle(n); }); }}>
              {IDLE_CHOICES.map((m) => <option key={m} value={m}>{m === 0 ? t('swarmOut.settings.never') : t('swarmOut.settings.minutes', { count: m })}</option>)}
            </select>
            <span className={`text-small ${dim}`}>{t('swarmOut.settings.idleNote')}</span>
          </span>
        </Row>
        {region && (
          <Row label={t('swarmOut.settings.region')}>
            <span className="font-mono">{region}</span><span className={`text-small ${dim}`}> · {t('swarmOut.setup.regionSame')}</span>
          </Row>
        )}
      </section>

      {isDesktop() && (others.length > 0 || legacy.canAdd) && (
        <section className={card} data-testid="so-set-other-accounts">
          <Head mark="+" markStyle={{ color: 'var(--color-text-secondary)', background: 'var(--color-bg-chrome)' }}
            title={legacy.swarmai ? t('settings.awsAccount.otherHives') : t('settings.awsAccount.hiveAccounts')}>
            {legacy.canAdd && (
              <button type="button" className="btn-sm border border-[var(--color-border)] hover:bg-[var(--color-hover)]" onClick={() => setAddingAccount(true)}>
                {t('settings.awsAccount.addHiveAccount')}</button>
            )}
          </Head>
          <div className="px-[18px] pb-3.5 flex flex-col gap-2">
            {others.length === 0 ? <p className={`m-0 text-body ${muted}`}>{t('settings.awsAccount.noHiveAccounts')}</p>
              : others.map((a) => <AccountCard key={a.id} account={a} onDelete={() => void load()} />)}
          </div>
        </section>
      )}

      <details className={`text-small ${muted}`} data-testid="so-set-auto">
        <summary className="cursor-pointer select-none">{t('swarmOut.settings.byItself')}</summary>
        <ul className="mt-1.5 mb-0 pl-[18px] text-[var(--color-text-secondary)]">
          {(['dispatch', 'idle', 'update', 'restart', 'elastic'] as const).map((k) => <li key={k}>{t(`swarmOut.settings.auto.${k}`)}</li>)}
        </ul>
      </details>

      {deployLegacy && (
        <DeployHiveDialog accounts={legacy.deployable} onClose={() => setDeployLegacy(false)}
          onDeployed={() => { setDeployLegacy(false); void load(); }} />
      )}
      {addingAccount && (
        <AddAccountDialog onClose={() => setAddingAccount(false)} onSaved={() => { setAddingAccount(false); void load(); }} />
      )}
    </div>
  );
}
