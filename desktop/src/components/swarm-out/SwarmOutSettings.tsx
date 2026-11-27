/**
 * SwarmOutSettings — the Swarm Out tab's settings view: the one place for where tasks run
 * (design docs/design/2026-11-20-swarm-out-multi-carrier.md §6.8; layout from the mockup
 * Projects/SwarmAI/.artifacts/mockups/swarm-out-settings-v1.html). Everything about Hives is
 * here: nothing on this view sends the person to the App's Settings.
 *
 * It holds only what the person decides; starting, stopping, updating and keeping Elastic
 * Workers on standby happen by themselves (core/hive_idle, the standby loop). The page
 * answers, top to bottom:
 *  ① can work run now, and on what: one sentence (Elastic Workers ready, machines ready, off,
 *    or needing the person), with the AWS account under it (its ID, copyable, its region and
 *    "change", which opens ConnectAccount here; what the account cannot do yet, when that
 *    blocks building a Hive);
 *  ② Elastic Workers (AgentCore Runtime), its own block above the machines: it is a pool
 *    that starts a worker per task, not a machine to look after. Its state and the standby
 *    switch (PUT /hive/elastic/standby, the person's) are on the block; opened, the account
 *    it uses (the old multi-account mode: always shown, it decides the switch), size, cost
 *    and version;
 *  ③ the machines, one list: resident Hives (EC2) and SSH machines (SshMachinesSection,
 *    design 2026-11-24-swarm-out-ssh-machine §8). A closed row is its name, its kind and its
 *    state (for EC2 its idle stop in a few words); a broken one opens by itself and offers
 *    retry, an SSH machine whose certificate expired shows its renewal without opening. An
 *    opened row is the three things the person changes: how long it keeps running
 *    (PUT /hive/instances/{id}/idle-stop, only idleStopSettable; restart), who can sign in
 *    (HiveAccess) and its Brains (HiveBrains); the rest (machine, address, the Host key
 *    rotation, delete) is under "More". "+ Add a machine" asks which kind; an EC2 Hive names
 *    its monthly cost before anything is created;
 *  ④ defaults for new machines, one line, opened with Edit: the instance type, the default
 *    idle stop (for new Hives and Hives without their own), the region;
 *  ⑤ Hives in other accounts (desktop only), then what SwarmAI does by itself, folded.
 * Restart is stop then start (the person's routes): the one way out of a Hive that hangs
 * while it holds a run, which the agent's restart refuses.
 */
import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
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
import { HiveBrains } from '../settings/HiveBrains';
import { AccountCard, AddAccountDialog, DeployHiveDialog, HiveAccess, HiveRotateKey, hiveUrl, retryPrompt } from '../settings/hiveComponents';
import { IDLE_MINUTES, idlePolicyLabel } from './idlePolicy';
import { hiveStatusLabel } from './live';
import { TONE_VAR, type Tone } from './tone';
import { groupedId } from './where';

const muted = 'text-[var(--color-text-muted)]';
const dim = 'text-[var(--color-text-dim)]';
const link = 'text-[var(--color-primary)] hover:underline disabled:opacity-50';
const quiet = `${muted} hover:text-[var(--color-text)] hover:underline disabled:opacity-50`;
const danger = 'text-[var(--color-status-error)] hover:underline disabled:opacity-50';
const card = 'so-card rounded-xl border border-[var(--color-border)] bg-[var(--color-card)]';
const select = 'border border-[var(--color-border)] rounded-md px-2 py-1 bg-[var(--color-card)] text-body disabled:opacity-50';
const heading = `m-0 mb-2 text-small font-semibold ${muted}`;
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

/** Whether the panel is narrow (the dashboard's `compact`, below COMPACT_BELOW): a field then
 *  puts its label above its value, and an opened row loses its indent. */
const Compact = createContext(false);

/** How far an opened row's details are indented: under its name, or flush when narrow. */
const detailPad = (compact: boolean) => (compact ? 'pl-[18px]' : 'pl-[38px]');

/** One thing the person changes in an opened row: its label, then its value. */
function Field({ label, children, testId }: { label: string; children: React.ReactNode; testId?: string }) {
  const compact = useContext(Compact);
  return (
    <div className={`flex ${compact ? 'flex-col gap-1' : 'gap-4 items-baseline'} min-w-0`} data-testid={testId} data-compact={compact || undefined}>
      <span className={`${compact ? '' : 'w-[130px] flex-none'} ${muted}`}>{label}</span>
      <div className="flex-1 min-w-0 text-[var(--color-text-secondary)]">{children}</div>
    </div>
  );
}

/** The rare and the dangerous of an opened row, folded. */
function More({ children, testId }: { children: React.ReactNode; testId?: string }) {
  const { t } = useTranslation();
  return (
    <details className="group" data-testid={testId}>
      <summary className={`cursor-pointer select-none list-none ${muted} hover:text-[var(--color-text)]`}>
        <span aria-hidden className="inline-block w-3 transition-transform group-open:rotate-90">›</span>{t('swarmOut.page.more')}
      </summary>
      <div className="mt-3 flex flex-col gap-3">{children}</div>
    </details>
  );
}

export function Kind({ ac, children }: { ac?: boolean; children: React.ReactNode }) {
  return (
    <span className="px-1.5 rounded text-[10.5px] leading-[15px] font-semibold flex-none"
      style={ac ? { color: 'var(--color-zone-work)', background: 'color-mix(in srgb, var(--color-zone-work) 12%, transparent)' }
        : { color: 'var(--color-text-secondary)', background: 'var(--color-bg-chrome)', boxShadow: 'inset 0 0 0 1px var(--color-border)' }}>{children}</span>
  );
}

const Dot = ({ tone }: { tone: Tone }) => (
  <span aria-hidden className="inline-block w-2 h-2 rounded-full flex-none" style={{ background: TONE_VAR[tone] }} />
);

/** A machine's closed row: its state dot, name and kind, its state, one action when it
 *  needs one, and the chevron that opens it (named after the machine for screen readers). */
function MachineHead({ open, onToggle, tone, name, kind, state, extra, action, toggleTestId, stateTestId }: {
  open: boolean; onToggle: () => void; tone: Tone; name: string; kind: string; state: string;
  extra?: React.ReactNode; action?: React.ReactNode; toggleTestId?: string; stateTestId: string;
}) {
  const compact = useContext(Compact);
  const loud = tone === 'error' || tone === 'attention';
  // narrow: the state goes under the name, so the name keeps the width it needs
  const stateLine = (
    <span className={`text-small ${compact ? 'block truncate' : 'whitespace-nowrap text-right'}`}
      style={loud ? { color: TONE_VAR[tone] } : { color: 'var(--color-text-secondary)' }}>
      <span data-testid={stateTestId}>{state}</span>{extra}
    </span>
  );
  return (
    <div className="flex items-center gap-3 px-[18px] py-3 cursor-pointer hover:bg-[var(--color-hover)] transition-colors" onClick={onToggle}>
      <Dot tone={tone} />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-body font-semibold truncate">{name}</span><Kind>{kind}</Kind>
        </div>
        {compact && stateLine}
      </div>
      {!compact && stateLine}
      {action}
      <button type="button" aria-expanded={open} aria-label={name} data-testid={toggleTestId}
        className={`w-4 flex-none text-small ${dim} transition-transform ${open ? 'rotate-90' : ''}`}
        onClick={(ev) => { ev.stopPropagation(); onToggle(); }}>›</button>
    </div>
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

/** ssh's own words when it refused this computer's login (key or certificate not accepted). */
const LOGIN_REFUSED = /permission denied|publickey|certificate/i;

/** This computer's own SSH certificate (the one ssh logs in to the machine with) has ended,
 *  the machine does not answer, and ssh says the login was refused: why it disconnected, and
 *  what to renew. Any other outage (the Hive down, a network error) is not blamed on it, and
 *  neither is a machine not probed yet: a computer may hold an unrelated old certificate. */
export const hostCertEnded = (m: SshMachine, now: number) =>
  m.status === 'running' && m.reachable === false && LOGIN_REFUSED.test(m.tunnel_error ?? '')
  && typeof m.host_cert_valid_until === 'number' && m.host_cert_valid_until * 1000 <= now;

/** How long before this computer's certificate ends the row says so. */
const HOST_CERT_WARN_S = 3600;

/** An SSH machine's tone and state word (design 2026-11-24-swarm-out-ssh-machine §8). */

export function sshTone(m: SshMachine, now: number, t: (k: string, o?: Record<string, unknown>) => string): { tone: Tone; word: string } {
  if (m.status === 'error' || m.status === 'failed') return { tone: 'error', word: hiveStatusLabel(m.status) };
  if (m.status !== 'running') return { tone: CHANGING.includes(m.status) ? 'attention' : 'unknown', word: hiveStatusLabel(m.status) };
  if (typeof m.cert_valid_until === 'number' && m.cert_valid_until * 1000 <= now) return { tone: 'error', word: t('swarmOut.ssh.certExpired') };
  // this computer cannot log in: its own certificate ended (only said while the machine does
  // not answer: a computer may hold an unrelated old certificate)
  if (hostCertEnded(m, now)) return { tone: 'error', word: t('swarmOut.ssh.hostCertExpired') };
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
 *  running a Hive in a container that the Host installed. Its rows sit in the one machine
 *  list. A row says whether it takes tasks now and, when not, the one thing to do (renew its
 *  certificate, look at ssh's error). An expired certificate is renewed from here: one button
 *  types `ssh -t <host> <renew command>` into the App's terminal (a shell on the machine when
 *  it has no renew command, which can be set right there; the command copied where there is
 *  no terminal), and the rows are read every few seconds until the new certificate shows
 *  (RENEW_WATCH_MS at most). Opened: the tasks it takes, its own AWS identity, its Brains;
 *  under More its host, machine, folders and remove. Adding one asks only for the SSH host,
 *  and says that tasks there run with the person's identity. `adding` given: the page's
 *  "+ Add a machine" opens the form; not given, the section offers its own add. It starts
 *  from what it read last and tells the page (`onMachines`); a running machine not probed
 *  yet reads as checking and is asked again every few seconds until its probe answers. */
export function SshMachinesSection({ onError, onMachines, adding: addingProp, onAddingChange }: {
  onError: (message: string | null) => void;
  onMachines?: (machines: SshMachine[] | null, failed: boolean) => void;
  adding?: boolean;
  onAddingChange?: (adding: boolean) => void;
}) {
  const { t } = useTranslation();
  const compact = useContext(Compact);
  const [machines, setMachines] = useState<SshMachine[] | null>(last.machines);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [confirm, setConfirm] = useState<string | null>(null);
  const [ownAdding, setOwnAdding] = useState(false);
  const embedded = addingProp !== undefined;
  const adding = embedded ? addingProp : ownAdding;
  const setAdding = (v: boolean) => { if (embedded) onAddingChange?.(v); else setOwnAdding(v); };
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
  useEffect(() => { onMachines?.(machines, failed); }, [machines, failed, onMachines]);
  const changing = (machines ?? []).some((m) => CHANGING.includes(m.status) || (m.status === 'running' && m.reachable === false));
  // a running machine whose probe has not answered yet: its answer comes within seconds
  const unknown = (machines ?? []).some((m) => m.status === 'running' && m.reachable == null);
  const isExpired = (m: SshMachine) => typeof m.cert_valid_until === 'number' && m.cert_valid_until * 1000 <= now;
  // a renewal started here and not seen yet: the machine's next probe (within about half a
  // minute) shows the new certificate, so the rows are read every few seconds for a while
  // the login renewal (this computer's certificate) is kept under its own key: the two
  // renewals never show each other's "started"
  const loginKey = (id: string) => `${id}:login`;
  // a renewal counts as started for RENEW_WATCH_MS: a certificate that ends again later
  // (hours after a renewal that worked) shows its renew button, never an old "started"
  const startedHere = (key: string) => renewing[key] != null && now - renewing[key] < RENEW_WATCH_MS;
  const watching = (machines ?? []).some((m) =>
    (startedHere(m.id) && isExpired(m)) || (startedHere(loginKey(m.id)) && hostCertEnded(m, now)));
  // the command that renews this computer's login certificate, being set for a machine
  const [loginHintFor, setLoginHintFor] = useState<{ id: string; text: string } | null>(null);
  const startLoginRenew = (m: SshMachine) => {
    const command = (m.config.login_renew_hint ?? '').trim();
    if (!command) return;
    if (!isDesktop()) {               // no terminal here: the command to run, copied
      void copyToClipboard(command).then((ok) => { if (ok) { setCopiedId(m.id); window.setTimeout(() => setCopiedId(null), 1500); } });
      return;
    }
    runInTerminal(command, t('swarmOut.ssh.thisComputer'));   // on this computer, never over ssh
    setRenewing((r) => ({ ...r, [loginKey(m.id)]: Date.now() }));
  };
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
    <div data-testid="so-set-ssh">
      {/* in the page's list an empty or loading SSH part says nothing (the list says it once);
          what cannot be read is said here */}
      {list.length === 0 && (failed || !embedded) && (
        <div className={`px-[18px] py-3 border-t border-[var(--color-border)] text-body ${muted}`} data-testid="so-ssh-none">
          {failed ? t('swarmOut.ssh.unreadable') : machines === null ? t('swarmOut.loading') : t('swarmOut.ssh.none')}
        </div>
      )}
      {list.map((m) => {
        const st = sshTone(m, now, t);
        const isOpen = open.has(m.id) || confirm === m.id;
        const expired = isExpired(m);
        return (
          <div key={m.id} className="border-t border-[var(--color-border)]" data-testid="so-ssh-row" data-tone={st.tone}>
            <MachineHead open={isOpen} onToggle={() => toggle(m.id)} tone={st.tone} name={m.name} kind={t('swarmOut.board.ssh')}
              state={st.word} stateTestId="so-ssh-state"
              action={m.status === 'error' ? (
                <button type="button" className="btn-sm border border-[var(--color-border)] hover:bg-[var(--color-hover)] disabled:opacity-50" disabled={busy}
                  data-testid="so-ssh-retry" onClick={(ev) => { ev.stopPropagation(); void act(() => hiveService.retrySsh(m.id)); }}>{t('common.button.retry')}</button>
              ) : undefined} />
            {/* this computer cannot log in (its certificate ended), or will soon: renew it here */}
            {(() => {
              const end = m.host_cert_valid_until;
              const ended = hostCertEnded(m, now);
              const soon = !ended && m.status === 'running' && m.reachable === true && typeof end === 'number'
                && end * 1000 > now && end * 1000 - now < HOST_CERT_WARN_S * 1000;
              if (!ended && !soon) return null;
              const hint = (m.config.login_renew_hint ?? '').trim();
              const started = startedHere(loginKey(m.id));
              const tone = ended ? TONE_VAR.error : TONE_VAR.attention;
              return (
                <div className={`${detailPad(compact)} pr-[18px] pb-3 -mt-1 text-small flex flex-col gap-2`} data-testid="so-ssh-host-cert">
                  <div style={{ color: tone }} data-testid="so-ssh-host-cert-note">
                    {ended ? t('swarmOut.ssh.hostCertEnded', { time: clock(end as number) })
                      : t('swarmOut.ssh.hostCertEnds', { time: clock(end as number) })}
                  </div>
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
                    {hint && (
                      <button type="button" className={started ? 'btn-sm border border-[var(--color-border)] hover:bg-[var(--color-hover)]' : 'btn-sm btn-primary'}
                        data-testid="so-ssh-host-renew" title={hint} onClick={() => startLoginRenew(m)}>
                        {!isDesktop() ? (copiedId === m.id ? t('swarmOut.setup.copied') : t('swarmOut.ssh.renewCopy'))
                          : t('swarmOut.ssh.renewInTerminal')}
                      </button>
                    )}
                    {started && ended && (
                      <button type="button" className="btn-sm btn-primary" data-testid="so-ssh-host-check" onClick={() => void load()}>
                        {t('swarmOut.ssh.checkAgain')}</button>
                    )}
                    {loginHintFor?.id !== m.id && (
                      <button type="button" className={`text-small ${link}`} data-testid="so-ssh-login-hint-set"
                        onClick={() => setLoginHintFor({ id: m.id, text: hint })}>
                        {hint ? t('swarmOut.ssh.loginHintChange') : t('swarmOut.ssh.loginHintSet')}</button>
                    )}
                  </div>
                  {started && ended && <div className={muted} data-testid="so-ssh-host-renew-started">{t('swarmOut.ssh.hostRenewStarted')}</div>}
                  {loginHintFor?.id === m.id && (
                    <div className="flex flex-wrap items-center gap-2 max-w-[520px]" data-testid="so-ssh-login-hint-form">
                      <input className={`${input} flex-1 min-w-[180px] font-mono`} value={loginHintFor.text} maxLength={120} autoFocus
                        placeholder={t('swarmOut.ssh.loginHintPlaceholder')} aria-label={t('swarmOut.ssh.loginHintLabel')} data-testid="so-ssh-login-hint"
                        onChange={(ev) => setLoginHintFor({ id: m.id, text: ev.target.value })} />
                      <button type="button" className="btn-sm btn-primary disabled:opacity-50" disabled={busy || !loginHintFor.text.trim()} data-testid="so-ssh-login-hint-save"
                        onClick={() => void act(async () => { await hiveService.setSshOptions(m.id, { login_renew_hint: loginHintFor.text.trim() }); setLoginHintFor(null); })}>
                        {t('swarmOut.ssh.hintSave')}</button>
                      <button type="button" className={link} onClick={() => setLoginHintFor(null)}>{t('swarmOut.setup.cancel')}</button>
                    </div>
                  )}
                </div>
              );
            })()}
            {/* the one thing to do, without opening the row, and the way to do it from here */}
            {expired && (() => {
              const hint = (m.config.cert_renew_hint ?? '').trim();
              const started = startedHere(m.id);
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
              <div className={`${detailPad(compact)} pr-[18px] pb-4 pt-0.5 text-small flex flex-col gap-3`} data-testid="so-ssh-detail">
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
                <Field label={t('swarmOut.page.tasks')}>
                  <div className="flex flex-col gap-1.5">
                    <label className="inline-flex items-center gap-2">
                      <input type="checkbox" checked={m.config.reaches_private !== false} disabled={busy} data-testid="so-ssh-private"
                        onChange={(ev) => void act(() => hiveService.setSshOptions(m.id, { reaches_private: ev.target.checked }))} />
                      {t('swarmOut.ssh.reachesPrivate')}
                    </label>
                    <label className="inline-flex items-center gap-2">
                      <input type="checkbox" checked={m.config.general_tasks !== false} disabled={busy} data-testid="so-ssh-general"
                        onChange={(ev) => void act(() => hiveService.setSshOptions(m.id, { general_tasks: ev.target.checked }))} />
                      {t('swarmOut.ssh.generalTasks')}
                    </label>
                  </div>
                </Field>
                <Field label={t('swarmOut.page.identity')}>
                  <div className="flex flex-col gap-1">
                    <span data-testid="so-ssh-aws-identity"
                      style={typeof m.config.not_after === 'number' && m.config.not_after * 1000 <= now ? { color: TONE_VAR.error } : undefined}>
                      {m.config.identity !== 'rolesanywhere'
                        ? (m.status === 'running' ? t('swarmOut.ssh.identityMoving') : t('swarmOut.ssh.identityPending'))
                        : typeof m.config.not_after !== 'number' ? t('swarmOut.ssh.identityPending')
                          : m.config.not_after * 1000 <= now ? t('swarmOut.ssh.identityExpired')
                            : t('swarmOut.ssh.identityUntil', { time: clock(m.config.not_after) })}
                    </span>
                    {typeof m.cert_valid_until === 'number' && !expired && (
                      <span className={muted} data-testid="so-ssh-cert">{t('swarmOut.ssh.certUntil', { time: clock(m.cert_valid_until) })}</span>
                    )}
                    {m.config.renew_error && (
                      <span className="break-words" style={{ color: TONE_VAR.attention }} data-testid="so-ssh-renew-error">{t('swarmOut.ssh.renewFailed', { why: m.config.renew_error })}</span>
                    )}
                  </div>
                </Field>
                {m.status === 'running' && (
                  <Field label={t('swarmOut.page.brains')} testId="so-ssh-brains"><HiveBrains instanceId={m.id} bare /></Field>
                )}
                <More testId="so-ssh-more">
                  {m.config.ssh_host && <Field label={t('swarmOut.page.host')}><span className="font-mono">{m.config.ssh_host}</span></Field>}
                  <Field label={t('swarmOut.page.machine')}>{[m.instance_type !== 'ssh' ? m.instance_type : null, m.version].filter(Boolean).join(' · ') || '—'}</Field>
                  <Field label={t('swarmOut.page.folders')}>
                    <span className="break-words" data-testid="so-ssh-mounts">
                      {(m.config.mounts ?? []).length ? t('swarmOut.ssh.mountsUsed', { list: (m.config.mounts ?? []).join('  ') })
                        : m.config.mounts_auto && m.status !== 'running' ? t('swarmOut.ssh.mountsPending') : t('swarmOut.ssh.mountsNone')}
                    </span>
                  </Field>
                  <div className="flex flex-wrap items-center gap-3">
                    <button type="button" className={danger} disabled={busy} data-testid="so-ssh-delete"
                      onClick={() => setConfirm(m.id)}>{t('swarmOut.page.removeSsh')}</button>
                    {confirm === m.id && (
                      <span className="inline-flex flex-wrap items-center gap-3" data-testid="so-ssh-delete-confirm">
                        <span className="text-[var(--color-text-secondary)]">{t('swarmOut.ssh.deleteNote', { name: m.name })}</span>
                        <button type="button" className="btn-sm btn-danger disabled:opacity-50" disabled={busy} data-testid="so-ssh-delete-go"
                          onClick={() => void act(() => hiveService.removeSsh(m.id))}>{t('swarmOut.settings.delete')}</button>
                        <button type="button" className={link} onClick={() => setConfirm(null)}>{t('swarmOut.setup.cancel')}</button>
                      </span>
                    )}
                  </div>
                </More>
              </div>
            )}
          </div>
        );
      })}
      {(adding || !embedded) && (
        <div className="px-[18px] py-3 border-t border-[var(--color-border)] text-body">
          {!adding ? (
            <button type="button" className={`font-semibold ${link}`} disabled={busy} data-testid="so-ssh-add" onClick={() => setAdding(true)}>
              + {t('swarmOut.ssh.add')}</button>
          ) : (
            <div className="flex flex-col gap-2.5 max-w-[520px]" data-testid="so-ssh-form">
              <label className="flex flex-col gap-1"><span className={`text-small ${muted}`}>{t('swarmOut.ssh.host')}</span>
                <input className={input} value={host} placeholder={t('swarmOut.ssh.hostPlaceholder')} data-testid="so-ssh-host" autoFocus
                  onChange={(ev) => setHost(ev.target.value)} />
                <span className={`text-small ${dim}`} data-testid="so-ssh-found-note">{t('swarmOut.ssh.foundNote')}</span></label>
              <p className="m-0 text-small" style={{ color: TONE_VAR.attention }} data-testid="so-ssh-identity">{t('swarmOut.ssh.identityNote')}</p>
              <div className="flex flex-wrap items-center gap-3">
                <button type="button" className="btn-sm btn-primary disabled:opacity-50" data-testid="so-ssh-add-go"
                  disabled={busy || !host.trim()} onClick={() => void add()}>{t('swarmOut.ssh.addConfirm')}</button>
                <button type="button" className={link} onClick={() => setAdding(false)}>{t('swarmOut.setup.cancel')}</button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** How many places can take work now, counted from what the page already read. */
export function machineCounts(ec2: HiveInstance[], ssh: SshMachine[], fleet: SwarmOutFleet | null, now: number,
  t: (k: string, o?: Record<string, unknown>) => string): { ready: number; off: number; broken: number } {
  let ready = 0; let off = 0; let broken = 0;
  for (const h of ec2) {
    const { tone } = residentTone(h, fleet);
    if (tone === 'error') broken += 1;
    else if (h.status === 'running') ready += 1;
    else if (h.status === 'stopped') off += 1;
  }
  for (const m of ssh) {
    const { tone } = sshTone(m, now, t);
    if (tone === 'error') broken += 1;
    else if (tone === 'ok') ready += 1;
    // a stopped SSH machine is not started by a task (unlike an EC2 Hive): it counts nowhere
  }
  return { ready, off, broken };
}

/** ① The answer on top: can work run now, and on what. A stopped EC2 Hive counts as able:
 *  a task starts it. What needs the person is the one coloured part. It never says "nothing"
 *  on a guess: while a part it counts is unread it says checking, and when a read failed and
 *  nothing known can take work it says it could not read, not that nothing can. */
function Verdict({ checking, unreadable, elasticReady, counts }: {
  checking: boolean; unreadable: boolean; elasticReady: boolean; counts: { ready: number; off: number; broken: number };
}) {
  const { t } = useTranslation();
  const can = elasticReady || counts.ready > 0 || counts.off > 0;
  if (checking) return <p className="m-0 text-heading font-semibold" data-testid="so-verdict" data-state="checking">{t('swarmOut.page.checking')}</p>;
  if (unreadable && !can) return <p className="m-0 text-heading font-semibold" data-testid="so-verdict" data-state="unreadable">{t('swarmOut.page.unreadable')}</p>;
  const parts: React.ReactNode[] = [];
  if (elasticReady && counts.ready > 0) parts.push(t('swarmOut.page.elasticAnd', { count: counts.ready }));
  else if (elasticReady) parts.push(t('swarmOut.page.elasticReady'));
  else if (counts.ready > 0) parts.push(t('swarmOut.page.machinesReady', { count: counts.ready }));
  if (counts.off > 0) parts.push(t('swarmOut.page.off', { count: counts.off }));
  if (counts.broken > 0) parts.push(<span key="b" style={{ color: TONE_VAR.error }}>{t('swarmOut.page.broken', { count: counts.broken })}</span>);
  if (!can && counts.broken === 0) parts.push(t('swarmOut.page.noneHint'));
  return (
    <p className="m-0 text-heading font-semibold" data-testid="so-verdict" data-state={can ? 'can' : 'none'}>
      <span style={{ color: can ? TONE_VAR.ok : TONE_VAR.attention }}>{can ? t('swarmOut.page.canRun') : t('swarmOut.page.none')}</span>
      {parts.map((p, i) => <span key={i}> · {p}</span>)}
    </p>
  );
}

/** The AWS account, one line under the answer: its ID (copyable), its region, change. Not
 *  connected (or needing sign-in), its state and Connect. What it cannot do yet, when that
 *  blocks building a Hive. Change opens the sign-in flow here. */
function AccountLine({ view, onView, failed }: {
  view: AwsAccountView | null; onView: (v: AwsAccountView) => void;
  /** the account could not be read (said so; nothing is offered on a guess) */
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
  return (
    <div className="mt-1" data-testid="so-set-account">
      <div className={`flex flex-wrap items-baseline gap-x-2 gap-y-1 text-small ${muted}`}>
        {id && (
          <span className="inline-flex items-baseline gap-2" data-testid="so-account-id">
            <span>{t('swarmOut.page.awsAccount')}</span>
            <span className="font-mono text-[var(--color-text-secondary)] tracking-[0.02em]" data-testid="so-account-id-value">{groupedId(id)}</span>
            <button type="button" className={quiet} data-testid="so-account-id-copy"
              onClick={() => void copyToClipboard(id).then((done) => { if (done) { setCopied(true); window.setTimeout(() => setCopied(false), 1500); } })}>
              {copied ? t('swarmOut.setup.copied') : t('swarmOut.setup.copy')}
            </button>
          </span>
        )}
        {id && region && <span aria-hidden>·</span>}
        {region && <span className="font-mono" data-testid="so-account-region">{region}</span>}
        {(!ok || !acct) && (
          <span className="inline-flex items-center gap-1.5" data-testid="so-account-state"
            style={state && state !== 'unconnected' ? { color: TONE_VAR.attention } : undefined}>
            {(id || region) && <span aria-hidden className={muted}>·</span>}
            {state ? (acct ? t(`swarmOut.setup.state.${state}`, { defaultValue: state }) : t('swarmOut.page.noAccount'))
              : failed ? t('swarmOut.setup.unreadable') : t('swarmOut.loading')}
          </span>
        )}
        {view && view.supported !== false && (
          <>
            {(id || region || state) && <span aria-hidden>·</span>}
            <button type="button" className={ok ? quiet : link} onClick={() => setConnecting((c) => !c)} aria-expanded={connecting}
              data-testid="so-account-change">
              {acct ? t('swarmOut.page.change') : t('swarmOut.setup.connect')}
            </button>
          </>
        )}
      </div>
      {acct && hiveCap && hiveCap.status !== 'ok' && hiveCap.status !== 'not_applicable' && (
        <div className="mt-2"><CapabilityLine cap={hiveCap} checking={false} onView={onView} /></div>
      )}
      <div className="so-expand" data-open={connecting}>
        <div>
          {connecting && (
            <div className={`${card} mt-3 px-[18px] py-3`} data-testid="so-account-connect">
              {acct && <p className="m-0 mb-3 text-small" style={{ color: TONE_VAR.attention }}>{t('swarmOut.setup.changeWarn')}</p>}
              <ConnectAccount mode="settings" start="pick"
                onConnected={(v) => { onView(v); setConnecting(false); }} onCancel={() => setConnecting(false)} />
            </div>
          )}
        </div>
      </div>
    </div>
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
  const [ssh, setSsh] = useState<{ list: SshMachine[] | null; failed: boolean }>({ list: last.machines, failed: false });
  const onMachines = useCallback((list: SshMachine[] | null, failed: boolean) => setSsh({ list, failed }), []);
  // every write of the account view and the idle default goes through these: the copy a
  // reopened tab starts from never falls behind what is shown
  const keepView = (v: AwsAccountView) => { last.view = v; setView(v); };
  const keepIdle = (n: number) => { last.idle = n; setIdle(n); };
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const errorRef = useRef<HTMLDivElement>(null);
  useEffect(() => { if (error) errorRef.current?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' }); }, [error]);
  // rows the person opened or closed (id -> open); others follow their default (a broken one
  // opens by itself). Kept as a choice, not a flip: a poll changing a row's tone never closes
  // the row under the person (and with it a one-time password on screen)
  const [opened, setOpened] = useState<Map<string, boolean>>(new Map());
  const [confirm, setConfirm] = useState<{ id: string; what: 'delete' | 'retry' | 'restart' } | null>(null);
  // "+ Add a machine": which kind is being chosen or added
  const [addMode, setAddMode] = useState<null | 'choose' | 'ec2' | 'ssh'>(null);
  const [elasticOpen, setElasticOpen] = useState(false);
  const [editingDefaults, setEditingDefaults] = useState(false);
  const [addingAccount, setAddingAccount] = useState(false);
  const [deployLegacy, setDeployLegacy] = useState(false);
  const [chosen, setChosen] = useState('');
  const [copiedUrl, setCopiedUrl] = useState<string | null>(null);

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
    try { await run(); setConfirm(null); } catch (e) { setError(problemOf(e).message); }
    finally { setBusy(false); void load(); }
  };
  const toggle = (id: string, now: boolean) => setOpened((s) => new Map(s).set(id, !now));

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
  const sshList = ssh.list ?? [];
  const counts = machineCounts(ec2, sshList, fleet, Date.now(), t);
  // a part the answer counts is not read yet: the SSH list, or a running machine's first probe
  const sshUnread = ssh.list === null && !ssh.failed;
  const verdictChecking = read.hives === undefined || read.elastic === undefined || sshUnread
    || sshList.some((m) => m.status === 'running' && m.reachable == null);
  const elasticTone: Tone = e?.status === 'ready' ? 'ok' : e?.status === 'failed' ? 'error' : e || standbyOn ? 'attention' : 'unknown';
  const elasticWord = e ? t(`swarmOut.settings.elasticStatus.${e.status}`, { defaultValue: e.status })
    : !elastic ? (read.elastic === false ? t('swarmOut.settings.elasticUnreadable') : t('swarmOut.loading'))
      : standbyOn ? t('swarmOut.settings.preparing') : t('swarmOut.settings.off');
  const copyUrl = (id: string, url: string) => void copyToClipboard(url).then((ok) => {
    if (ok) { setCopiedUrl(id); window.setTimeout(() => setCopiedUrl(null), 1500); }
  });

  return (
    <Compact.Provider value={compact}>
    {/* as wide as the panel, like the Runs and History views */}
    <div className={`flex flex-col ${compact ? 'gap-6' : 'gap-7'}`} data-testid="swarm-out-settings" data-compact={compact || undefined}>
      {/* what failed, at the top: every action here is one click away from this line */}
      {error && (
        <div ref={errorRef} className="flex items-start gap-3 px-4 py-2.5 rounded-lg text-body break-words" role="alert" data-testid="so-settings-error"
          style={{ color: TONE_VAR.error, background: `color-mix(in srgb, ${TONE_VAR.error} 8%, transparent)` }}>
          <span className="flex-1 min-w-0">{error}</span>
          <button type="button" className={`text-small ${muted} hover:underline`} onClick={() => setError(null)}>{t('swarmOut.setup.dismiss')}</button>
        </div>
      )}

      {/* ① can work run now, and on what */}
      <div>
        <Verdict checking={verdictChecking} unreadable={read.hives === false || read.elastic === false || ssh.failed}
          elasticReady={e?.status === 'ready'} counts={counts} />
        <AccountLine view={view} onView={connected} failed={read.account === false} />
      </div>

      {/* ② Elastic Workers: a pool with a switch, not a machine to look after */}
      <section data-testid="so-set-elastic">
        <h2 className={`${heading} flex items-center gap-2`}>Elastic Workers<Kind ac>{t('swarmOut.board.elastic')}</Kind></h2>
        <div className={card}>
          <div className="flex items-center gap-3 px-[18px] py-3.5 cursor-pointer hover:bg-[var(--color-hover)] transition-colors rounded-xl"
            onClick={() => setElasticOpen((o) => !o)}>
            <Dot tone={elasticTone} />
            <div className="flex-1 min-w-0">
              <div className="text-body font-semibold first-letter:uppercase" data-testid="so-elastic-state"
                style={elasticTone === 'error' ? { color: TONE_VAR.error } : undefined}>{elasticWord}</div>
              <div className={`text-small ${muted}`}>{t('swarmOut.page.elasticLead')}</div>
              {e?.status === 'failed' && e.error_message && <div className="text-small break-words" style={{ color: TONE_VAR.error }}>{e.error_message}</div>}
              {!e && standbyOn && (elastic?.reason || elastic?.standby_last) && (
                <div className={`text-small break-words ${muted}`} data-testid="so-elastic-why">{elastic.reason ?? elastic.standby_last}</div>)}
            </div>
            <button type="button" role="switch" aria-checked={standbyOn} aria-label={t('swarmOut.settings.standby')}
              className="so-switch" data-testid="so-elastic-switch"
              title={needChoice ? t('swarmOut.setup.chooseFirst') : t('swarmOut.settings.standbyNote')}
              disabled={busy || !elastic || needChoice}
              onClick={(ev) => { ev.stopPropagation(); void act(() => awsAccountService.setElasticStandby(!standbyOn, severalAccounts ? chosen || undefined : undefined)); }} />
            <button type="button" aria-expanded={elasticOpen} aria-label="Elastic Workers" data-testid="so-elastic-toggle"
              className={`w-4 flex-none text-small ${dim} transition-transform ${elasticOpen ? 'rotate-90' : ''}`}
              onClick={(ev) => { ev.stopPropagation(); setElasticOpen((o) => !o); }}>›</button>
          </div>
          {/* several accounts (the old mode): the account decides the switch, so it is always shown */}
          {severalAccounts && (
            <div className={`${detailPad(compact)} pr-[18px] pb-3 text-small`}>
              <Field label={t('swarmOut.settings.elasticAccount')}>
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
              </Field>
            </div>
          )}
          {elasticOpen && (
            <div className={`${detailPad(compact)} pr-[18px] pb-4 text-small flex flex-col gap-3`} data-testid="so-elastic-detail">
              {size && <Field label={t('swarmOut.settings.eachMachine')}>{t('swarmOut.settings.size', { vcpu: size.vcpu_peak, mem: size.memory_gib_peak, disk: size.disk_gib })}</Field>}
              <Field label={t('swarmOut.settings.cost')}>{t('swarmOut.settings.elasticCost')}</Field>
              <Field label={t('swarmOut.settings.version')}>
                {(e?.image_version ?? elastic?.host_version ?? '—')}<span className={muted}> · {t('swarmOut.settings.followsVersion')}</span>
              </Field>
            </div>
          )}
        </div>
      </section>

      {/* ③ the machines, one list: EC2 Hives and SSH machines */}
      <section data-testid="so-set-hives">
        <h2 className={heading}>{t('swarmOut.page.machines')}</h2>
        <div className={`${card} overflow-hidden`} data-testid="so-hive-list">
          {/* every row draws its top line; the first one sits on the card's own edge */}
          <div className="-mt-px">
            {ec2.length === 0 && (read.hives !== true || sshUnread || (!ssh.failed && sshList.length === 0 && addMode !== 'ssh')) && (
              <div className={`px-[18px] py-3 border-t border-[var(--color-border)] text-body ${muted}`} data-testid="so-hives-none">
                {read.hives === undefined || (read.hives && sshUnread) ? t('swarmOut.loading')
                  : read.hives ? t('swarmOut.page.noMachines') : t('swarmOut.setup.hivesUnreadable')}
              </div>
            )}
            {ec2.map((h) => {
              const st = residentTone(h, fleet);
              const word = st.word ?? t('swarmOut.fleet.unreachable');
              const broken = h.status === 'error' || h.status === 'failed';
              // a broken or unreachable Hive opens by itself: why it is broken is what to read
              const byDefault = st.tone === 'error';
              const isOpen = (opened.get(h.id) ?? byDefault) || confirm?.id === h.id;
              const policy = idlePolicyLabel(t, h.idleStopEffective);
              const own = h.idleStopMinutes ?? null;
              // an own value set outside the choices (through the API) is still shown as set
              const choices = own != null && own > 0 && !IDLE_MINUTES.includes(own) ? [...IDLE_MINUTES, own].sort((a, b) => a - b) : IDLE_MINUTES;
              const url = hiveUrl(h);
              return (
                <div key={h.id} className="border-t border-[var(--color-border)]" data-testid="so-hive-row" data-tone={st.tone}>
                  <MachineHead open={isOpen} onToggle={() => toggle(h.id, isOpen)} tone={st.tone} name={h.name} kind={t('swarmOut.board.ec2')}
                    state={word} stateTestId="so-hive-state" toggleTestId="so-hive-toggle"
                    extra={policy ? <span className={`${dim} ml-1`} data-testid="so-hive-idle-policy">· {policy}</span> : undefined}
                    action={broken ? (
                      <button type="button" className="btn-sm border border-[var(--color-border)] hover:bg-[var(--color-hover)]" disabled={busy}
                        data-testid="so-hive-retry" onClick={(ev) => { ev.stopPropagation(); setConfirm({ id: h.id, what: 'retry' }); }}>
                        {t('common.button.retry')}</button>
                    ) : undefined} />
                  <div className="so-expand" data-open={isOpen}>
                    <div>
                      {isOpen && (
                        <div className={`${detailPad(compact)} pr-[18px] pb-4 pt-0.5 text-small flex flex-col gap-3.5`} data-testid="so-hive-detail">
                          {/* why it is broken, so the person can choose retry (rebuild) or delete */}
                          {broken && h.errorMessage && (
                            <div className="px-2.5 py-2 rounded-md break-words"
                              style={{ color: TONE_VAR.error, background: `color-mix(in srgb, ${TONE_VAR.error} 7%, transparent)` }}
                              data-testid="so-hive-error">{h.errorMessage}</div>
                          )}
                          {confirm?.id === h.id && confirm.what === 'retry' && (
                            <div className="flex flex-wrap items-center gap-3" data-testid="so-hive-retry-confirm">
                              <span className="text-[var(--color-text-secondary)] whitespace-pre-line">{retryPrompt(h)}</span>
                              <button type="button" className="btn-sm btn-primary disabled:opacity-50" disabled={busy} data-testid="so-hive-retry-go"
                                onClick={() => void act(() => hiveService.retryInstance(h.id, !!h.ec2InstanceId))}>{t('swarmOut.setup.retryConfirm')}</button>
                              <button type="button" className={link} onClick={() => setConfirm(null)}>{t('swarmOut.setup.cancel')}</button>
                            </div>
                          )}
                          <Field label={t('swarmOut.page.keepRunning')} testId={h.idleStopSettable ? 'so-hive-idle-row' : undefined}>
                            <span className="inline-flex flex-wrap items-center gap-x-3 gap-y-1">
                              {/* its own idle stop: only a machine the Host stops when idle has one */}
                              {h.idleStopSettable ? (
                                <select className={select} value={own == null ? 'default' : String(own)} disabled={busy} data-testid="so-hive-idle"
                                  aria-label={t('swarmOut.settings.idle')}
                                  onChange={(ev) => { const v = ev.target.value; void act(() => hiveService.setIdleStop(h.id, v === 'default' ? null : Number(v))); }}>
                                  <option value="0">{t('swarmOut.settings.alwaysOn')}</option>
                                  {choices.map((m) => <option key={m} value={String(m)}>{idlePolicyLabel(t, m)}</option>)}
                                  <option value="default">{t('swarmOut.settings.idleDefaultChoice', { what: idlePolicyLabel(t, idle ?? 120) })}</option>
                                </select>
                              ) : <span>{policy ?? t('swarmOut.settings.alwaysOn')}</span>}
                              {confirm?.id === h.id && confirm.what === 'restart' ? (
                                <span className="inline-flex flex-wrap items-center gap-2" data-testid="so-hive-restart-confirm">
                                  <span className="text-[var(--color-text-secondary)]">{t('swarmOut.settings.restartNote')}</span>
                                  <button type="button" className="btn-sm btn-primary disabled:opacity-50" disabled={busy} data-testid="so-hive-restart-go"
                                    onClick={() => void act(async () => { await hiveService.stopInstance(h.id); await hiveService.startInstance(h.id); })}>
                                    {t('swarmOut.settings.restart')}</button>
                                  <button type="button" className={link} onClick={() => setConfirm(null)}>{t('swarmOut.setup.cancel')}</button>
                                </span>
                              ) : (
                                <button type="button" className={quiet} disabled={busy || h.status !== 'running'} data-testid="so-hive-restart"
                                  title={t('swarmOut.settings.restartNote')} onClick={() => setConfirm({ id: h.id, what: 'restart' })}>
                                  {t('swarmOut.settings.restart')}</button>
                              )}
                            </span>
                          </Field>
                          <Field label={t('ui.hiveComponents.accessWho')} testId="so-hive-login"><HiveAccess instance={h} extras={false} /></Field>
                          {h.status === 'running' && <Field label={t('swarmOut.page.brains')} testId="so-hive-brains"><HiveBrains instanceId={h.id} bare /></Field>}
                          <More testId="so-hive-more">
                            <Field label={t('swarmOut.page.machine')}>{[h.instanceType, h.region, h.version, t('swarmOut.settings.autoUpdate')].filter(Boolean).join(' · ')}</Field>
                            {url && (
                              <Field label={t('swarmOut.page.address')}>
                                <span className="inline-flex flex-wrap items-baseline gap-x-3 min-w-0">
                                  <code className="text-[var(--color-text)] select-all break-all" data-testid="so-hive-url">{url}</code>
                                  <button type="button" className={quiet} data-testid="so-hive-url-copy" onClick={() => copyUrl(h.id, url)}>
                                    {copiedUrl === h.id ? t('swarmOut.setup.copied') : t('swarmOut.setup.copy')}</button>
                                </span>
                              </Field>
                            )}
                            <Field label={t('swarmOut.page.appKey')}><HiveRotateKey instance={h} /></Field>
                            <div className="flex flex-wrap items-center gap-3">
                              <button type="button" className={danger} disabled={busy} data-testid="so-hive-delete"
                                onClick={() => setConfirm({ id: h.id, what: 'delete' })}>{t('swarmOut.page.deleteHive')}</button>
                              {confirm?.id === h.id && confirm.what === 'delete' && (
                                <span className="inline-flex flex-wrap items-center gap-3" data-testid="so-hive-delete-confirm">
                                  <span className="text-[var(--color-text-secondary)]">{t('swarmOut.settings.deleteNote', { name: h.name })}</span>
                                  <button type="button" className="btn-sm btn-danger disabled:opacity-50" disabled={busy} data-testid="so-hive-delete-go"
                                    onClick={() => void act(() => hiveService.deleteInstance(h.id))}>{t('swarmOut.settings.delete')}</button>
                                  <button type="button" className={link} onClick={() => setConfirm(null)}>{t('swarmOut.setup.cancel')}</button>
                                </span>
                              )}
                            </div>
                          </More>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
            <SshMachinesSection onError={setError} onMachines={onMachines}
              adding={addMode === 'ssh'} onAddingChange={(v) => setAddMode(v ? 'ssh' : null)} />
          </div>
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
          {/* what is missing for building one (a permission, a default VPC to create) */}
          {fleetStatus?.state === 'cannot_build' && fleetStatus.capability && (
            <div className="px-[18px] py-3 border-t border-[var(--color-border)]" data-testid="so-hive-cannot">
              <CapabilityLine cap={fleetStatus.capability} checking={false} onView={keepView} /></div>
          )}
        </div>
        {/* + Add a machine: which kind first; an EC2 Hive names its cost before it is built */}
        {addMode !== 'ssh' && (
          <div className="mt-3 text-body" data-testid="so-machine-add-row">
            {addMode === null && (
              <button type="button" className={`font-semibold ${link}`} disabled={busy} data-testid="so-machine-add"
                onClick={() => setAddMode('choose')}>+ {t('swarmOut.page.addMachine')}</button>
            )}
            {addMode === 'choose' && (
              <div className="flex flex-col gap-2 max-w-[560px]" data-testid="so-machine-choose">
                {short === 0 && (
                  <button type="button" className={`${card} text-left px-4 py-3 hover:bg-[var(--color-hover)] disabled:opacity-50`}
                    disabled={busy || read.account === undefined} data-testid="so-hive-add"
                    onClick={() => (swarmaiMode ? setAddMode('ec2') : setDeployLegacy(true))}>
                    <span className="flex items-center gap-2 font-semibold">{t('swarmOut.page.addEc2')}<Kind>{t('swarmOut.board.ec2')}</Kind></span>
                    <span className={`block text-small ${muted}`}>
                      {t('swarmOut.page.addEc2Note')}{price != null ? ` · ${t('swarmOut.settings.addCost', { usd: price })}` : ''}</span>
                  </button>
                )}
                <button type="button" className={`${card} text-left px-4 py-3 hover:bg-[var(--color-hover)] disabled:opacity-50`}
                  disabled={busy} data-testid="so-ssh-add" onClick={() => setAddMode('ssh')}>
                  <span className="flex items-center gap-2 font-semibold">{t('swarmOut.page.addSsh')}<Kind>{t('swarmOut.board.ssh')}</Kind></span>
                  <span className={`block text-small ${muted}`}>{t('swarmOut.page.addSshNote')}</span>
                </button>
                <div><button type="button" className={link} onClick={() => setAddMode(null)}>{t('swarmOut.setup.cancel')}</button></div>
              </div>
            )}
            {addMode === 'ec2' && (
              <div className="flex flex-wrap items-center gap-3">
                <button type="button" className="btn-sm btn-primary disabled:opacity-50" disabled={busy} data-testid="so-hive-add-go"
                  onClick={() => void act(async () => { await awsAccountService.setFleet({ desired_count: Math.max(desired ?? 0, counted) + 1 }); setAddMode(null); })}>
                  {price != null ? t('swarmOut.settings.addConfirm', { usd: price, account: view?.account?.account_id ?? '' })
                    : t('swarmOut.setup.addConfirmNoCost', { account: view?.account?.account_id ?? '' })}</button>
                <button type="button" className={link} onClick={() => setAddMode(null)}>{t('swarmOut.setup.cancel')}</button>
              </div>
            )}
          </div>
        )}
      </section>

      {/* ④ rarely changed: one line, opened with Edit */}
      <section data-testid="so-set-rules">
        <h2 className={heading}>{t('swarmOut.page.defaults')}</h2>
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-body">
          <span className="text-[var(--color-text-secondary)]" data-testid="so-defaults-line">
            {[swarmaiMode ? `EC2 ${type}` : null,
              idle === null ? null : idle === 0 ? t('swarmOut.page.defaultsNever') : idlePolicyLabel(t, idle),
              region].filter(Boolean).join(' · ')}
          </span>
          <button type="button" className={quiet} aria-expanded={editingDefaults} data-testid="so-defaults-edit"
            onClick={() => setEditingDefaults((v) => !v)}>{editingDefaults ? t('swarmOut.page.done') : t('swarmOut.page.edit')}</button>
        </div>
        {editingDefaults && (
          <div className={`${card} mt-3 px-[18px] py-4 text-small flex flex-col gap-3.5`} data-testid="so-defaults-form">
            {swarmaiMode && (
              <Field label={t('swarmOut.settings.type')}>
                <select className={select} value={type} disabled={busy} data-testid="so-hive-type"
                  onChange={(ev) => void act(() => awsAccountService.setFleet({ instance_type: ev.target.value }))}>
                  {Object.keys(prices).length ? Object.keys(prices).map((k) => <option key={k} value={k}>{k}</option>) : <option value={type}>{type}</option>}
                </select>
              </Field>
            )}
            <Field label={t('swarmOut.settings.idleDefault')}>
              <span className="flex flex-col gap-1">
                <select className={`${select} self-start`} value={idle ?? 120} disabled={busy || idle === null} data-testid="so-idle"
                  aria-label={t('swarmOut.settings.idleDefault')}
                  onChange={(ev) => { const n = Number(ev.target.value); void act(async () => { await settingsService.updateAPIConfiguration({ hiveIdleStopMinutes: n }); keepIdle(n); }); }}>
                  {IDLE_CHOICES.map((m) => <option key={m} value={m}>{minutesWord(m)}</option>)}
                </select>
                <span className={dim}>{t('swarmOut.settings.idleDefaultNote')}</span>
              </span>
            </Field>
            {region && (
              <Field label={t('swarmOut.settings.region')}>
                <span className="font-mono">{region}</span><span className={dim}> · {t('swarmOut.setup.regionSame')}</span>
              </Field>
            )}
          </div>
        )}
      </section>

      {isDesktop() && (others.length > 0 || legacy.canAdd) && (
        <section data-testid="so-set-other-accounts">
          <div className="flex items-baseline gap-3">
            <h2 className={`${heading} flex-1`}>{legacy.swarmai ? t('settings.awsAccount.otherHives') : t('settings.awsAccount.hiveAccounts')}</h2>
            {legacy.canAdd && (
              <button type="button" className={`text-small ${link}`} onClick={() => setAddingAccount(true)}>
                {t('settings.awsAccount.addHiveAccount')}</button>
            )}
          </div>
          <div className="flex flex-col gap-2">
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
        <DeployHiveDialog accounts={legacy.deployable} onClose={() => { setDeployLegacy(false); setAddMode(null); }}
          onDeployed={() => { setDeployLegacy(false); setAddMode(null); void load(); }} />
      )}
      {addingAccount && (
        <AddAccountDialog onClose={() => setAddingAccount(false)} onSaved={() => { setAddingAccount(false); void load(); }} />
      )}
    </div>
    </Compact.Provider>
  );
}
