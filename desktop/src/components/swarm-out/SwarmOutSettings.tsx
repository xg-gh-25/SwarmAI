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
 *  ②b SSH machines: machines of the person's own reached over SSH, each with a Hive the Host
 *    installed in a container, an expired certificate renewed from the App's terminal
 *    (SshMachinesSection; design 2026-11-24-swarm-out-ssh-machine §8);
 *  ③ resident Hives (EC2): one row per Hive (its state, from its record and, for a running
 *    one, whether the fleet poll could reach it, and its idle stop in a few words); a row
 *    opens to why it is broken, its own idle stop (PUT /hive/instances/{id}/idle-stop:
 *    always on, its minutes, or the default; only for a machine the Host stops when idle,
 *    idleStopSettable), its sign-in (AuthDisplay), its Brains (HiveBrains: which are out of
 *    date, one Sync), restart, retry (a rebuild, after a confirm) and delete; "+ add one"
 *    names the monthly cost before anything is created (when the account cannot build one,
 *    what is missing);
 *  ④ new Hives and idle time: the instance type, the default idle stop time (for new Hives
 *    and Hives without their own), the region;
 *  ⑤ Hives in other accounts (desktop only; moved from the App's AWS account page);
 *  then what SwarmAI does by itself, folded.
 * Restart is stop then start (the person's routes): the one way out of a Hive that hangs
 * while it holds a run, which the agent's restart refuses.
 */
import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { awsAccountService, problemOf, type AwsAccountView, type ElasticStatus } from '../../services/awsAccount';
import { hiveService, hiveAccountView, type HiveAccount, type HiveInstance, type SshMachine } from '../../services/hive';
import { settingsService } from '../../services/settings';
import type { SwarmOutFleet } from '../../services/swarmOut';
import { isDesktop } from '../../services/tauri';
import { copyToClipboard } from '../../utils/clipboard';
import { runInTerminal } from '../common/settingsEvents';
import ConnectAccount from '../awsAccount/ConnectAccount';
import { CapabilityLine } from '../awsAccount/HiveFleet';
import { AWS_REGION_OPTIONS } from '../awsAccount/regions';
import { HiveBrains } from '../settings/HiveBrains';
import { AccountCard, AddAccountDialog, AuthDisplay, DeployHiveDialog, retryPrompt } from '../settings/hiveComponents';
import { IDLE_MINUTES, idlePolicyLabel } from './idlePolicy';
import { hiveStatusLabel } from './live';
import { TONE_VAR, type Tone } from './tone';
import { groupedId } from './where';

const muted = 'text-[var(--color-text-muted)]';
const dim = 'text-[var(--color-text-dim)]';
const link = 'text-[var(--color-primary)] hover:underline disabled:opacity-50';
const card = 'so-card rounded-xl border border-[var(--color-border)] bg-[var(--color-card)]';
const select = 'border border-[var(--color-border)] rounded-md px-2 py-1 bg-[var(--color-card)] text-body disabled:opacity-50';
const IDLE_CHOICES = [...IDLE_MINUTES, 0];
const DEFAULT_TYPE = 'm7g.xlarge';
const CHANGING = ['pending', 'provisioning', 'installing', 'deleting', 'updating', 'starting', 'stopping'];

/** What the tab read last: a reopened tab (the dashboard mounts each view anew) starts from it
 *  and refreshes underneath, and every read replaces its own part. Only this file reads it. */
type Read = { account?: boolean; hives?: boolean; elastic?: boolean };
const last: {
  view: AwsAccountView | null; elastic: ElasticStatus | null; instances: HiveInstance[] | null;
  accounts: HiveAccount[] | null; idle: number | null; read: Read; machines: SshMachine[] | null;
} = { view: null, elastic: null, instances: null, accounts: null, idle: null, read: {}, machines: null };

/** Tests start each case from an empty tab. */
export function forgetSettingsForTests(): void {
  Object.assign(last, { view: null, elastic: null, instances: null, accounts: null, idle: null, read: {}, machines: null });
}

/** Whether the panel is narrow (the dashboard's `compact`, below COMPACT_BELOW): a row then
 *  puts its label above its value, and the opened parts of a row lose their deep indent. */
const Compact = createContext(false);

/** How far an opened machine's details are indented: under its name, or flush when narrow. */
const detailPad = (compact: boolean) => (compact ? 'pl-[18px]' : 'pl-[44px]');

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
  const compact = useContext(Compact);
  return (
    <div className={`flex ${compact ? 'flex-col items-start gap-0.5' : 'items-center gap-3 min-h-[42px]'} px-[18px] py-2.5 border-t border-[var(--color-border)] text-body`}
      data-testid={testId} data-compact={compact || undefined}>
      <span className={`${compact ? 'text-small' : 'w-[150px] flex-none'} ${muted}`}>{label}</span>
      <span className={`${compact ? 'w-full' : 'flex-1'} min-w-0 text-[var(--color-text-secondary)]`}>{children}</span>
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
function AccountCardView({ view, elastic, elasticRead, onView, failed }: {
  view: AwsAccountView | null; elastic: ElasticStatus | null; onView: (v: AwsAccountView) => void;
  /** whether the Elastic Workers status answered (undefined: not yet) */
  elasticRead: boolean | undefined;
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
              {!e && <span className={dim}>· {elastic === null ? (elasticRead === false ? t('swarmOut.settings.elasticUnreadable') : t('swarmOut.loading'))
                : t('swarmOut.setup.elasticOff')}</span>}
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

/** An SSH machine's tone and state word (design 2026-11-24-swarm-out-ssh-machine §8). */
export function sshTone(m: SshMachine, now: number, t: (k: string, o?: Record<string, unknown>) => string): { tone: Tone; word: string } {
  if (m.status === 'error' || m.status === 'failed') return { tone: 'error', word: hiveStatusLabel(m.status) };
  if (m.status !== 'running') return { tone: CHANGING.includes(m.status) ? 'attention' : 'unknown', word: hiveStatusLabel(m.status) };
  if (typeof m.cert_valid_until === 'number' && m.cert_valid_until * 1000 <= now) return { tone: 'error', word: t('swarmOut.ssh.certExpired') };
  if (m.reachable === false) return { tone: 'error', word: t('swarmOut.fleet.unreachable') };
  return { tone: m.reachable ? 'ok' : 'unknown', word: hiveStatusLabel(m.status) };
}

/** "13:33", or "11/25 13:33" when not today, local time. */
const clock = (s: number) => {
  const d = new Date(s * 1000);
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  return d.toDateString() === new Date().toDateString() ? hm : `${d.getMonth() + 1}/${d.getDate()} ${hm}`;
};

/** One shell word: kept as it is when it holds only safe characters, else single-quoted. */
const shellWord = (s: string) => (/^[A-Za-z0-9@%_+=:,./-]+$/.test(s) ? s : `'${s.replace(/'/g, `'\\''`)}'`);

/** The command that renews an SSH machine's certificate from this computer's terminal: ssh to
 *  the machine with a terminal (`-t`: the renewal asks for a PIN or a code) and run the
 *  machine's own renew command (`cert_renew_hint`, the person's setting: `mwinit -o` on an
 *  Amazon Cloud Desktop). With no renew command set, a shell on the machine. It is typed into
 *  the terminal and not run: the person reads it and presses Enter. */
export function renewCommand(host: string, hint: string | null | undefined): string {
  const h = (hint ?? '').trim();
  return h ? `ssh -t ${shellWord(host)} ${shellWord(h)}` : `ssh ${shellWord(host)}`;
}

/** How long the rows are read again every few seconds after a renewal was started here. */
const RENEW_WATCH_MS = 10 * 60_000;

/** ③ SSH machines: machines of the person's own, reached over SSH (a Cloud Desktop), each
 *  running a Hive in a container that the Host installed. A row says whether it takes tasks
 *  now and, when not, the one thing to do (renew its certificate, look at ssh's error). An
 *  expired certificate is renewed from here: one button types `ssh -t <host> <renew command>`
 *  into the App's terminal (a shell on the machine when it has no renew command, which can be
 *  set right there; the command copied where there is no terminal), and the rows are read
 *  every few seconds until the new certificate shows (RENEW_WATCH_MS at most). It
 *  opens to its host, its own AWS identity, Brains, the "tasks that need no private network"
 *  switch, retry and delete. "+ add" asks only for the SSH host, and says that tasks there run
 *  with the person's identity. It starts from what it read last; a running machine not probed
 *  yet reads as checking and is asked again every few seconds until its probe answers. */
export function SshMachinesSection({ onError }: { onError: (message: string | null) => void }) {
  const { t } = useTranslation();
  const compact = useContext(Compact);
  const [machines, setMachines] = useState<SshMachine[] | null>(last.machines);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [confirm, setConfirm] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [host, setHost] = useState('');
  const [now, setNow] = useState(() => Date.now());
  // machines whose renewal was started here (id -> when): read again until it shows
  const [renewing, setRenewing] = useState<Record<string, number>>({});
  const [copiedId, setCopiedId] = useState<string | null>(null);
  // the renew command being set for a machine that has none (id -> the text typed)
  const [hintFor, setHintFor] = useState<{ id: string; text: string } | null>(null);

  const load = useCallback(async () => {
    try { const got = await hiveService.listSsh(); last.machines = got; setMachines(got); setFailed(false); } catch { setFailed(true); }
    setNow(Date.now());
  }, []);
  useEffect(() => { void load(); }, [load]);
  const changing = (machines ?? []).some((m) => CHANGING.includes(m.status) || (m.status === 'running' && m.reachable === false));
  // a running machine whose probe has not answered yet: its answer comes within seconds
  const unknown = (machines ?? []).some((m) => m.status === 'running' && m.reachable == null);
  const isExpired = (m: SshMachine) => typeof m.cert_valid_until === 'number' && m.cert_valid_until * 1000 <= now;
  // a renewal started here and not seen yet: the machine's next probe (within about half a
  // minute) shows the new certificate, so the rows are read every few seconds for a while
  const watching = (machines ?? []).some((m) => renewing[m.id] != null && isExpired(m) && now - renewing[m.id] < RENEW_WATCH_MS);
  useEffect(() => {
    if (!changing && !unknown && !watching) return undefined;
    const timer = setInterval(() => { void load(); }, unknown || watching ? 3000 : 10000);
    return () => clearInterval(timer);
  }, [changing, unknown, watching, load]);
  const startRenew = (m: SshMachine) => {
    if (!m.config.ssh_host) return;
    const command = renewCommand(m.config.ssh_host, m.config.cert_renew_hint);
    if (!isDesktop()) {               // no terminal here: the command to run, copied
      void copyToClipboard(command).then((ok) => { if (ok) { setCopiedId(m.id); window.setTimeout(() => setCopiedId(null), 1500); } });
      return;
    }
    runInTerminal(command, m.name);
    setRenewing((r) => ({ ...r, [m.id]: Date.now() }));
  };

  const act = async (run: () => Promise<unknown>) => {
    setBusy(true); onError(null);
    try { await run(); setConfirm(null); } catch (e) { onError(problemOf(e).message); }
    finally { setBusy(false); void load(); }
  };
  const toggle = (id: string) => setOpen((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const add = () => act(async () => {
    // only the host: the install finds the AWS profile and the directories on the machine
    await hiveService.addSsh({ ssh_host: host.trim() });
    setAdding(false);
    setHost('');
  });
  const input = 'w-full border border-[var(--color-border)] rounded-md px-2 py-1 bg-[var(--color-card)] text-body';
  const list = machines ?? [];

  return (
    <section className={card} data-testid="so-set-ssh">
      <Head mark="SSH" markStyle={{ color: 'var(--color-text-secondary)', background: 'var(--color-bg-chrome)' }}
        title={t('swarmOut.ssh.title')} kind={<Kind>{t('swarmOut.board.ssh')}</Kind>} lead={t('swarmOut.ssh.lead')} />
      {list.length === 0 && (
        <div className={`px-[18px] py-3 border-t border-[var(--color-border)] text-body ${muted}`} data-testid="so-ssh-none">
          {machines === null ? (failed ? t('swarmOut.ssh.unreadable') : t('swarmOut.loading')) : t('swarmOut.ssh.none')}
        </div>
      )}
      {list.map((m) => {
        const st = sshTone(m, now, t);
        const isOpen = open.has(m.id) || confirm === m.id;
        const expired = typeof m.cert_valid_until === 'number' && m.cert_valid_until * 1000 <= now;
        return (
          <div key={m.id} className="border-t border-[var(--color-border)]" data-testid="so-ssh-row" data-tone={st.tone}>
            <div className="flex items-center gap-3 px-[18px] py-3 cursor-pointer hover:bg-[var(--color-hover)] transition-colors" onClick={() => toggle(m.id)}>
              <button type="button" aria-expanded={isOpen} aria-label={m.name} className={`w-3 text-small ${dim} transition-transform ${isOpen ? 'rotate-90' : ''}`}
                onClick={(ev) => { ev.stopPropagation(); toggle(m.id); }}>›</button>
              <Dot tone={st.tone} />
              <div className="flex-1 min-w-0">
                <div className="text-body font-semibold truncate">{m.name}</div>
                <div className={`text-small ${muted} truncate`}>{[m.instance_type !== 'ssh' ? m.instance_type : null, m.version, m.config.ssh_host].filter(Boolean).join(' · ')}</div>
              </div>
              <span className="text-small whitespace-nowrap" style={st.tone === 'error' || st.tone === 'attention' ? { color: TONE_VAR[st.tone] } : undefined}
                data-testid="so-ssh-state">{st.word}</span>
            </div>
            {/* the one thing to do, without opening the row, and the way to do it from here */}
            {expired && (() => {
              const hint = (m.config.cert_renew_hint ?? '').trim();
              const started = renewing[m.id] != null;
              return (
                <div className={`${detailPad(compact)} pr-[18px] pb-3 -mt-1 text-small flex flex-col gap-2`} data-testid="so-ssh-renew-box">
                  <div style={{ color: TONE_VAR.error }} data-testid="so-ssh-renew">
                    {hint ? t('swarmOut.ssh.renewWith', { hint }) : t('swarmOut.ssh.renew')}
                  </div>
                  {m.config.ssh_host && <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
                    <button type="button" className={started ? 'btn-sm border border-[var(--color-border)] hover:bg-[var(--color-hover)]' : 'btn-sm btn-primary'}
                      data-testid="so-ssh-renew-go" title={renewCommand(m.config.ssh_host, hint)} onClick={() => startRenew(m)}>
                      {!isDesktop() ? (copiedId === m.id ? t('swarmOut.setup.copied') : t('swarmOut.ssh.renewCopy'))
                        : hint ? t('swarmOut.ssh.renewInTerminal') : t('swarmOut.ssh.openTerminal')}
                    </button>
                    {started && (
                      <button type="button" className="btn-sm btn-primary" data-testid="so-ssh-renew-check" onClick={() => void load()}>
                        {t('swarmOut.ssh.checkAgain')}</button>
                    )}
                    {!hint && hintFor?.id !== m.id && (
                      <button type="button" className={`text-small ${link}`} data-testid="so-ssh-hint-set"
                        onClick={() => setHintFor({ id: m.id, text: '' })}>{t('swarmOut.ssh.hintSet')}</button>
                    )}
                  </div>}
                  {started && <div className={muted} data-testid="so-ssh-renew-started">{hint ? t('swarmOut.ssh.renewStarted') : t('swarmOut.ssh.renewStartedShell')}</div>}
                  {hintFor?.id === m.id && (
                    <div className="flex flex-wrap items-center gap-2 max-w-[520px]" data-testid="so-ssh-hint-form">
                      <input className={`${input} flex-1 min-w-[180px] font-mono`} value={hintFor.text} maxLength={120} autoFocus
                        placeholder={t('swarmOut.ssh.hintPlaceholder')} aria-label={t('swarmOut.ssh.hintLabel')} data-testid="so-ssh-hint"
                        onChange={(ev) => setHintFor({ id: m.id, text: ev.target.value })} />
                      <button type="button" className="btn-sm btn-primary disabled:opacity-50" disabled={busy || !hintFor.text.trim()} data-testid="so-ssh-hint-save"
                        onClick={() => void act(async () => { await hiveService.setSshOptions(m.id, { cert_renew_hint: hintFor.text.trim() }); setHintFor(null); })}>
                        {t('swarmOut.ssh.hintSave')}</button>
                      <button type="button" className={link} onClick={() => setHintFor(null)}>{t('swarmOut.setup.cancel')}</button>
                    </div>
                  )}
                </div>
              );
            })()}
            {isOpen && (
              <div className={`${detailPad(compact)} pr-[18px] pb-3.5 pt-0.5 text-small flex flex-col gap-2`} data-testid="so-ssh-detail">
                {(m.status === 'error' && m.error_message) && (
                  <div className="px-2.5 py-2 rounded-md break-words" style={{ color: TONE_VAR.error, background: `color-mix(in srgb, ${TONE_VAR.error} 7%, transparent)` }}
                    data-testid="so-ssh-error">{m.error_message}</div>
                )}
                {m.status === 'running' && m.reachable === false && m.tunnel_error && (
                  <div className="break-words" style={{ color: TONE_VAR.error }} data-testid="so-ssh-tunnel">{m.tunnel_error}</div>
                )}
                {m.status === 'running' && m.error_message && (
                  <div className={`break-words ${muted}`}>{m.error_message}</div>
                )}
                {typeof m.cert_valid_until === 'number' && !expired && (
                  <div className={muted} data-testid="so-ssh-cert">{t('swarmOut.ssh.certUntil', { time: clock(m.cert_valid_until) })}</div>
                )}
                <div className={muted} data-testid="so-ssh-aws-identity"
                  style={typeof m.config.not_after === 'number' && m.config.not_after * 1000 <= now ? { color: TONE_VAR.error } : undefined}>
                  {m.config.identity !== 'rolesanywhere'
                    ? (m.status === 'running' ? t('swarmOut.ssh.identityMoving') : t('swarmOut.ssh.identityPending'))
                    : typeof m.config.not_after !== 'number' ? t('swarmOut.ssh.identityPending')
                      : m.config.not_after * 1000 <= now ? t('swarmOut.ssh.identityExpired')
                        : t('swarmOut.ssh.identityUntil', { time: clock(m.config.not_after) })}
                </div>
                {m.config.renew_error && (
                  <div className={`${muted} break-words`} data-testid="so-ssh-renew-error">{t('swarmOut.ssh.renewFailed', { why: m.config.renew_error })}</div>
                )}
                <div className={`${muted} break-words`} data-testid="so-ssh-mounts">
                  {(m.config.mounts ?? []).length ? t('swarmOut.ssh.mountsUsed', { list: (m.config.mounts ?? []).join('  ') })
                    : m.config.mounts_auto && m.status !== 'running' ? t('swarmOut.ssh.mountsPending') : t('swarmOut.ssh.mountsNone')}
                </div>
                <label className="inline-flex items-center gap-2 text-[var(--color-text-secondary)]">
                  <input type="checkbox" checked={m.config.reaches_private !== false} disabled={busy} data-testid="so-ssh-private"
                    onChange={(ev) => void act(() => hiveService.setSshOptions(m.id, { reaches_private: ev.target.checked }))} />
                  {t('swarmOut.ssh.reachesPrivate')}
                </label>
                <label className="inline-flex items-center gap-2 text-[var(--color-text-secondary)]">
                  <input type="checkbox" checked={m.config.general_tasks !== false} disabled={busy} data-testid="so-ssh-general"
                    onChange={(ev) => void act(() => hiveService.setSshOptions(m.id, { general_tasks: ev.target.checked }))} />
                  {t('swarmOut.ssh.generalTasks')}
                </label>
                {m.status === 'running' && <div className="min-w-0" data-testid="so-ssh-brains"><HiveBrains instanceId={m.id} /></div>}
                <div className="mt-1 flex flex-wrap items-center gap-2">
                  {m.status === 'error' && (
                    <button type="button" className="btn-sm border border-[var(--color-border)] hover:bg-[var(--color-hover)] disabled:opacity-50" disabled={busy}
                      data-testid="so-ssh-retry" onClick={() => void act(() => hiveService.retrySsh(m.id))}>{t('common.button.retry')}</button>
                  )}
                  <button type="button" className="btn-sm border disabled:opacity-50" disabled={busy} data-testid="so-ssh-delete"
                    style={{ color: TONE_VAR.error, borderColor: `color-mix(in srgb, ${TONE_VAR.error} 35%, var(--color-border))` }}
                    onClick={() => setConfirm(m.id)}>{t('swarmOut.settings.delete')}</button>
                  {confirm === m.id && (
                    <span className="inline-flex flex-wrap items-center gap-3" data-testid="so-ssh-delete-confirm">
                      <span className="text-[var(--color-text-secondary)]">{t('swarmOut.ssh.deleteNote', { name: m.name })}</span>
                      <button type="button" className="btn-sm btn-primary disabled:opacity-50" disabled={busy} data-testid="so-ssh-delete-go"
                        onClick={() => void act(() => hiveService.removeSsh(m.id))}>{t('swarmOut.settings.delete')}</button>
                      <button type="button" className={link} onClick={() => setConfirm(null)}>{t('swarmOut.setup.cancel')}</button>
                    </span>
                  )}
                </div>
              </div>
            )}
          </div>
        );
      })}
      <div className="px-[18px] py-3 border-t border-[var(--color-border)] text-body">
        {!adding ? (
          <button type="button" className={`font-semibold ${link}`} disabled={busy} data-testid="so-ssh-add" onClick={() => setAdding(true)}>
            + {t('swarmOut.ssh.add')}</button>
        ) : (
          <div className="flex flex-col gap-2.5 max-w-[520px]" data-testid="so-ssh-form">
            <label className="flex flex-col gap-1"><span className={`text-small ${muted}`}>{t('swarmOut.ssh.host')}</span>
              <input className={input} value={host} placeholder={t('swarmOut.ssh.hostPlaceholder')} data-testid="so-ssh-host"
                onChange={(ev) => setHost(ev.target.value)} />
              <span className={`text-small ${dim}`} data-testid="so-ssh-found-note">{t('swarmOut.ssh.foundNote')}</span></label>
            <p className="text-small" style={{ color: TONE_VAR.attention }} data-testid="so-ssh-identity">{t('swarmOut.ssh.identityNote')}</p>
            <div className="flex flex-wrap items-center gap-3">
              <button type="button" className="btn-sm btn-primary disabled:opacity-50" data-testid="so-ssh-add-go"
                disabled={busy || !host.trim()} onClick={() => void add()}>{t('swarmOut.ssh.addConfirm')}</button>
              <button type="button" className={link} onClick={() => setAdding(false)}>{t('swarmOut.setup.cancel')}</button>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}

export function SwarmOutSettings({ fleet = null, onConnected, compact = false }: {
  fleet?: SwarmOutFleet | null;
  /** the panel is narrow (the dashboard's `compact`, as its other views get it) */
  compact?: boolean;
  /** the account was connected or changed here (the panel's run view follows it) */
  onConnected?: (view: AwsAccountView) => void;
}) {
  const { t } = useTranslation();
  const [view, setView] = useState<AwsAccountView | null>(last.view);
  const [elastic, setElastic] = useState<ElasticStatus | null>(last.elastic);
  const [instances, setInstances] = useState<HiveInstance[]>(last.instances ?? []);
  // what each request could read: a part is missing until it answered (no "none" on a guess)
  const [read, setRead] = useState<Read>(last.read);
  const [accounts, setAccounts] = useState<HiveAccount[]>(last.accounts ?? []);
  const [idle, setIdle] = useState<number | null>(last.idle);
  // every write of the account view and the idle default goes through these: the copy a
  // reopened tab starts from never falls behind what is shown
  const keepView = (v: AwsAccountView) => { last.view = v; setView(v); };
  const keepIdle = (n: number) => { last.idle = n; setIdle(n); };
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [confirm, setConfirm] = useState<{ id: string; what: 'delete' | 'retry' } | null>(null);
  const [adding, setAdding] = useState(false);
  const [addingAccount, setAddingAccount] = useState(false);
  const [deployLegacy, setDeployLegacy] = useState(false);
  const [chosen, setChosen] = useState('');

  // each request fills its own part as it answers: a slow one (the Elastic Workers status is
  // read live from AWS) never holds the others
  const load = useCallback(async () => {
    const mark = (part: keyof Read, ok: boolean) => { last.read = { ...last.read, [part]: ok }; setRead(last.read); };
    await Promise.allSettled([
      awsAccountService.get().then((v) => { keepView(v); mark('account', true); },
        () => mark('account', false)),
      awsAccountService.elasticStatus().then((e) => { last.elastic = e; setElastic(e); mark('elastic', true); },
        () => mark('elastic', false)),
      hiveService.listInstances().then((list) => { last.instances = list; setInstances(list); mark('hives', true); },
        () => mark('hives', false)),
      hiveService.listAccounts().then((a) => { last.accounts = a; setAccounts(a); }),
      settingsService.getAPIConfiguration().then((cfg) => {
        const m = cfg.hiveIdleStopMinutes;
        keepIdle(typeof m === 'number' ? m : 120);
      }),
    ]);
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
  const connected = (v: AwsAccountView) => { keepView(v); onConnected?.(v); void load(); };
  const minutesWord = (m: number) => (m === 0 ? t('swarmOut.settings.never') : t('swarmOut.settings.minutes', { count: m }));

  return (
    <Compact.Provider value={compact}>
    {/* as wide as the panel, like the Runs and History views */}
    <div className={`flex flex-col ${compact ? 'gap-6' : 'gap-8'}`} data-testid="swarm-out-settings" data-compact={compact || undefined}>
      {/* what failed, at the top: every action here is one click away from this line */}
      {error && (
        <div className="flex items-start gap-3 px-4 py-2.5 rounded-lg text-body break-words" role="alert" data-testid="so-settings-error"
          style={{ color: TONE_VAR.error, background: `color-mix(in srgb, ${TONE_VAR.error} 8%, transparent)` }}>
          <span className="flex-1 min-w-0">{error}</span>
          <button type="button" className={`text-small ${muted} hover:underline`} onClick={() => setError(null)}>{t('swarmOut.setup.dismiss')}</button>
        </div>
      )}
      <AccountCardView view={view} elastic={elastic} elasticRead={read.elastic} onView={connected} failed={read.account === false} />

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
              : !elastic ? (read.elastic === false ? t('swarmOut.settings.elasticUnreadable') : t('swarmOut.loading'))
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

      <SshMachinesSection onError={setError} />

      <section className={card} data-testid="so-set-hives">
        <Head mark="EC2" markStyle={{ color: 'var(--color-text-secondary)', background: 'var(--color-bg-chrome)' }}
          title={t('swarmOut.settings.resident')} kind={<Kind>{t('swarmOut.board.ec2')}</Kind>} lead={t('swarmOut.settings.residentLead')} />
        <div data-testid="so-hive-list">
          {ec2.length === 0 && (
            <div className={`px-[18px] py-3 border-t border-[var(--color-border)] text-body ${muted}`} data-testid="so-hives-none">
              {read.hives === undefined ? t('swarmOut.loading') : read.hives ? t('swarmOut.setup.noHives') : t('swarmOut.setup.hivesUnreadable')}
            </div>
          )}
          {ec2.map((h) => {
            const st = residentTone(h, fleet);
            const word = st.word ?? t('swarmOut.fleet.unreachable');
            const broken = h.status === 'error' || h.status === 'failed';
            // a broken or unreachable Hive opens by itself: why it is broken is what to read
            const byDefault = st.tone === 'error';
            const isOpen = (expanded.has(h.id) ? !byDefault : byDefault) || confirm?.id === h.id;
            const policy = idlePolicyLabel(t, h.idleStopEffective);
            const own = h.idleStopMinutes ?? null;
            // an own value set outside the choices (through the API) is still shown as set
            const choices = own != null && own > 0 && !IDLE_MINUTES.includes(own) ? [...IDLE_MINUTES, own].sort((a, b) => a - b) : IDLE_MINUTES;
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
                  {policy && <span className={`text-small whitespace-nowrap ${dim}`} data-testid="so-hive-idle-policy">· {policy}</span>}
                  {broken && (
                    <button type="button" className="btn-sm border border-[var(--color-border)] hover:bg-[var(--color-hover)]" disabled={busy}
                      data-testid="so-hive-retry" onClick={(ev) => { ev.stopPropagation(); setConfirm({ id: h.id, what: 'retry' }); }}>
                      {t('common.button.retry')}</button>
                  )}
                </div>
                <div className="so-expand" data-open={isOpen}>
                  <div>
                    {isOpen && (
                      <div className={`${detailPad(compact)} pr-[18px] pb-3.5 pt-0.5 text-small`} data-testid="so-hive-detail">
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
                        {/* its own idle stop: only a machine the Host stops when idle has one */}
                        {h.idleStopSettable && (
                          <label className="mb-2.5 flex flex-wrap items-center gap-x-3 gap-y-1" data-testid="so-hive-idle-row">
                            <span className={muted}>{t('swarmOut.settings.idle')}</span>
                            <select className={select} value={own == null ? 'default' : String(own)} disabled={busy} data-testid="so-hive-idle"
                              onChange={(ev) => { const v = ev.target.value; void act(() => hiveService.setIdleStop(h.id, v === 'default' ? null : Number(v))); }}>
                              <option value="0">{t('swarmOut.settings.alwaysOn')}</option>
                              {choices.map((m) => <option key={m} value={String(m)}>{minutesWord(m)}</option>)}
                              <option value="default">{t('swarmOut.settings.idleDefaultChoice', { what: minutesWord(idle ?? 120) })}</option>
                            </select>
                          </label>
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
              <button type="button" className={`font-semibold ${link}`} disabled={busy || read.account === undefined} data-testid="so-hive-add"
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
            <div className="px-[18px] pb-3" data-testid="so-hive-cannot"><CapabilityLine cap={fleetStatus.capability} checking={false} onView={keepView} /></div>
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
        <Row label={t('swarmOut.settings.idleDefault')}>
          <span className="inline-flex flex-wrap items-center gap-x-3 gap-y-1">
            <select className={select} value={idle ?? 120} disabled={busy || idle === null} data-testid="so-idle"
              onChange={(ev) => { const n = Number(ev.target.value); void act(async () => { await settingsService.updateAPIConfiguration({ hiveIdleStopMinutes: n }); keepIdle(n); }); }}>
              {IDLE_CHOICES.map((m) => <option key={m} value={m}>{minutesWord(m)}</option>)}
            </select>
            <span className={`text-small ${dim}`}>{t('swarmOut.settings.idleDefaultNote')}</span>
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
    </Compact.Provider>
  );
}
